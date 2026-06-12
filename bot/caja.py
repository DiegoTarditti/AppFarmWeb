"""Caja: tickets confirmados por el operador, que el cajero cobra y entrega.

NO procesa pagos online (Meta lo prohíbe para farmacia). Solo registra el medio
de pago al cobrar, como un POS interno. El pago/entrega se hacen presencialmente.
"""
import database

DEFAULT_FORMAS = ['Efectivo', 'Transferencia', 'Débito', 'Crédito', 'QR / MercadoPago']
_ACTIVOS = ('confirmado', 'cobrado')   # lo que ve la caja como pendiente


# ── Formas de pago (catálogo) ────────────────────────────────────────────────

def listar_formas_pago():
    with database.get_db() as s:
        q = (s.query(database.FormaPago).filter(database.FormaPago.activa.is_(True))
             .order_by(database.FormaPago.orden, database.FormaPago.nombre))
        fs = q.all()
        if not fs:   # seed la primera vez
            for i, n in enumerate(DEFAULT_FORMAS):
                s.add(database.FormaPago(nombre=n, orden=i))
            s.commit()
            fs = q.all()
        return [{'id': f.id, 'nombre': f.nombre} for f in fs]


def crear_forma_pago(nombre):
    nombre = (nombre or '').strip()
    if not nombre:
        return {'ok': False, 'error': 'nombre vacío'}
    with database.get_db() as s:
        existe = s.query(database.FormaPago).filter(database.FormaPago.nombre.ilike(nombre)).first()
        if existe:
            existe.activa = True
            s.commit()
            return {'ok': True, 'id': existe.id}
        f = database.FormaPago(nombre=nombre, orden=99)
        s.add(f)
        s.commit()
        return {'ok': True, 'id': f.id}


def eliminar_forma_pago(forma_id):
    with database.get_db() as s:
        f = s.get(database.FormaPago, forma_id)
        if f:
            s.delete(f)
            s.commit()
        return {'ok': True}


# ── Tickets ──────────────────────────────────────────────────────────────────

def _nombre_usuario(s, uid):
    if not uid:
        return None
    u = s.get(database.Usuario, uid)
    return (u.nombre_completo or u.username) if u else None


def _ticket_dict(s, t, con_items=True):
    d = {
        'id': t.id, 'conversacion_id': t.conversacion_id,
        'cliente': t.cliente_nombre or 's/cliente',
        'total': float(t.total or 0), 'estado': t.estado,
        'forma_pago': t.forma_pago, 'nota': t.nota,
        'operador': _nombre_usuario(s, t.operador_id),
        'cajero': _nombre_usuario(s, t.cajero_id),
        'creado_en': t.creado_en.strftime('%d/%m %H:%M') if t.creado_en else '',
    }
    if con_items:
        its = (s.query(database.TicketItem).filter_by(ticket_id=t.id)
               .order_by(database.TicketItem.id).all())
        d['items'] = [{'nombre': i.nombre, 'detalle': i.detalle, 'precio': float(i.precio or 0),
                       'cantidad': i.cantidad, 'subtotal': float(i.subtotal or 0)} for i in its]
    return d


def crear_ticket(conv_id, items, operador_id, cliente_nombre=None, cliente_id=None):
    """items: [{nombre, detalle, precio, cantidad}]. Devuelve {ok, id, total}.
    Si no se pasa cliente_id, se hereda del cliente vinculado a la conversación."""
    items = [it for it in (items or []) if (it.get('nombre') or '').strip()]
    if not items:
        return {'ok': False, 'error': 'pedido vacío'}
    total = 0
    with database.get_db() as s:
        if cliente_id is None and conv_id:
            conv = s.get(database.BotConversacion, conv_id)
            cliente_id = conv.cliente_id if conv else None
        t = database.TicketCaja(
            conversacion_id=conv_id, cliente_nombre=cliente_nombre,
            cliente_id=cliente_id,
            estado='confirmado', operador_id=operador_id)
        s.add(t)
        s.flush()
        for it in items:
            precio = float(it.get('precio') or 0)
            cant = int(it.get('cantidad') or 1)
            sub = precio * cant
            total += sub
            s.add(database.TicketItem(
                ticket_id=t.id, nombre=it['nombre'].strip(),
                detalle=(it.get('detalle') or '').strip() or None,
                precio=precio, cantidad=cant, subtotal=sub))
        t.total = total
        s.commit()
        return {'ok': True, 'id': t.id, 'total': total}


def listar_tickets(incluir_cerrados=False):
    """Cola de caja: confirmados + cobrados (pendientes de entrega). Con
    incluir_cerrados=True trae también entregados/anulados del día."""
    with database.get_db() as s:
        q = s.query(database.TicketCaja)
        if not incluir_cerrados:
            q = q.filter(database.TicketCaja.estado.in_(_ACTIVOS))
        ts = q.order_by(database.TicketCaja.creado_en.desc()).limit(100).all()
        return [_ticket_dict(s, t) for t in ts]


# ── Bandejas de la nueva caja (sobre PedidoReparto / flujo Cerrar transacción) ──
# 3 vistas para el cajero según en qué etapa está el pedido:
#   - por_cobrar : llegan de /atencion al cerrar la TX (estado=en_caja).
#   - cadetes    : ya cobrados, esperando publicación al grupo o cadete que retire.
#   - drogueria  : sin stock, esperando que llegue la factura de la droguería.

def _pedido_dict(p):
    """Resumen del pedido para las bandejas de caja (cero datos sensibles
    como vuelto/notas internas, el cajero no los necesita acá)."""
    return {
        'id': p.id,
        'cliente': p.cliente_nombre,
        'direccion': p.direccion,
        'piso': p.piso, 'depto': p.depto, 'referencia': p.referencia,
        'total': float(p.total_paciente) if p.total_paciente is not None else None,
        'forma_pago': p.forma_pago,
        'dato_pago_mp': p.dato_pago_mp,    # para que el cajero pegue el TXT en ObServer
        'destino': p.destino,
        'stock': p.stock_status,
        'drogueria_id': p.drogueria_id,
        'obra_social': p.obra_social,
        'receta': p.receta_estado,
        'firma': bool(p.requiere_firma),
        'envio': float(p.envio_costo) if p.envio_costo is not None else None,
        'estado': p.estado,
        'canal': p.canal,
        'prioridad': p.prioridad,
        'tomo': p.tomo,
        'creado_en': p.creado_en.isoformat() if p.creado_en else None,
    }


def listar_bandeja(name, limit=100):
    """Lista pedidos para una de las 3 bandejas de caja.

    `name`: 'por_cobrar' | 'cadetes' | 'drogueria'.
    Devuelve [] si name desconocido (el frontend muestra empty state).
    """
    P = database.PedidoReparto
    with database.get_db() as s:
        q = s.query(P)
        if name == 'por_cobrar':
            q = q.filter(P.estado == 'en_caja')
        elif name == 'cadetes':
            # Cobrado y esperando ser publicado al grupo, o ya publicado.
            q = q.filter(P.estado.in_(('en_planilla', 'publicado')))
        elif name == 'drogueria':
            # Esperando ingreso de droguería (cualquier estado activo).
            q = q.filter(P.stock_status == 'esperar',
                         P.estado.notin_(('entregado', 'anulado')))
        else:
            return []
        rows = q.order_by(P.creado_en.desc()).limit(limit).all()
        return [_pedido_dict(p) for p in rows]


def cobrar_ticket(ticket_id, forma_pago, cajero_id):
    with database.get_db() as s:
        t = s.get(database.TicketCaja, ticket_id)
        if not t:
            return {'ok': False, 'error': 'no existe'}
        t.estado = 'cobrado'
        t.forma_pago = (forma_pago or '').strip() or None
        t.cajero_id = cajero_id
        t.cobrado_en = database.now_ar()
        s.commit()
        return {'ok': True}


def entregar_ticket(ticket_id):
    with database.get_db() as s:
        t = s.get(database.TicketCaja, ticket_id)
        if t:
            t.estado = 'entregado'
            s.commit()
        return {'ok': True}


def anular_ticket(ticket_id):
    with database.get_db() as s:
        t = s.get(database.TicketCaja, ticket_id)
        if t:
            t.estado = 'anulado'
            s.commit()
        return {'ok': True}


def enviar_a_reparto(ticket_id):
    """Crea un PedidoReparto a partir de un TicketCaja.
    Toma cliente, ítems y total del ticket; domicilio de la conversación si existe."""
    from services import reparto as svc
    with database.get_db() as s:
        t = s.get(database.TicketCaja, ticket_id)
        if not t:
            return {'ok': False, 'error': 'ticket no existe'}
        if t.estado == 'anulado':
            return {'ok': False, 'error': 'ticket anulado'}

        its = (s.query(database.TicketItem).filter_by(ticket_id=ticket_id)
               .order_by(database.TicketItem.id).all())
        producto_str = ', '.join(f'{i.cantidad}x {i.nombre}' for i in its)[:200] or None

        # Domicilio: último domicilio guardado en la conversación
        direccion = localidad = None
        if t.conversacion_id:
            dom = (s.query(database.DomicilioCliente)
                   .filter_by(conversacion_id=t.conversacion_id)
                   .order_by(database.DomicilioCliente.id.desc())
                   .first())
            if dom:
                direccion = dom.direccion
                localidad = dom.localidad

        coords = svc.coords_de_pedido(None, direccion, localidad)
        lat, lng = coords if coords else (None, None)
        ruta = svc.ruta_para_punto(s, lat, lng)

        p = database.PedidoReparto(
            fecha=database.now_ar().date(),
            cliente_id=t.cliente_id,
            cliente_nombre=t.cliente_nombre,
            direccion=direccion,
            lat=lat, lng=lng,
            cuadrante=svc.cuadrante_de(lat, lng),
            ruta_id=(ruta.id if ruta else None),
            prioridad='normal',
            estado='pendiente',
            canal='bot',
            importe=float(t.total) if t.total else None,
            forma_pago=t.forma_pago,
            producto=producto_str,
        )
        s.add(p)
        s.commit()
        return {'ok': True, 'pedido_id': p.id, 'asignado': bool(ruta)}
