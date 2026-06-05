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


def crear_ticket(conv_id, items, operador_id, cliente_nombre=None,
                 cliente_observer_id=None, cliente_local_id=None):
    """items: [{nombre, detalle, precio, cantidad}]. Devuelve {ok, id, total}."""
    items = [it for it in (items or []) if (it.get('nombre') or '').strip()]
    if not items:
        return {'ok': False, 'error': 'pedido vacío'}
    total = 0
    with database.get_db() as s:
        t = database.TicketCaja(
            conversacion_id=conv_id, cliente_nombre=cliente_nombre,
            cliente_observer_id=cliente_observer_id, cliente_local_id=cliente_local_id,
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
