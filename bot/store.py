"""Persistencia del bot: conversaciones + mensajes en la DB.

Reemplaza el estado en memoria → habilita el handoff (panel de operadores),
el historial, multi-línea y que sobreviva reinicios.
"""
import database


def get_conversacion(canal, canal_user_id, nombre=None, linea=None):
    """Devuelve el estado de la conversación (la crea si no existe).
    Dict plano: {id, estado_atencion, nodo, esperando}."""
    cid = str(canal_user_id)
    with database.get_db() as s:
        conv = (s.query(database.BotConversacion)
                .filter_by(canal=canal, canal_user_id=cid).first())
        if not conv:
            conv = database.BotConversacion(
                canal=canal, canal_user_id=cid, nombre_cliente=nombre,
                linea=linea, estado_atencion='bot', nodo='inicio')
            # En WhatsApp el canal_user_id ES el teléfono → autovinculamos la ficha.
            if canal == 'whatsapp':
                conv.cliente_observer_id = match_cliente_por_telefono(cid)
            s.add(conv)
            s.commit()
        elif nombre and not conv.nombre_cliente:
            conv.nombre_cliente = nombre
            s.commit()
        return {'id': conv.id, 'estado_atencion': conv.estado_atencion,
                'nodo': conv.nodo or 'inicio', 'esperando': conv.esperando}


def set_estado_flujo(conv_id, nodo, esperando):
    with database.get_db() as s:
        conv = s.get(database.BotConversacion, conv_id)
        if conv:
            conv.nodo, conv.esperando = nodo, esperando
            conv.ultimo_en = database.now_ar()
            s.commit()


def set_atencion(conv_id, estado_atencion, operador_user_id=None):
    """bot | cola (derivada) | humano (tomada por un operador)."""
    with database.get_db() as s:
        conv = s.get(database.BotConversacion, conv_id)
        if conv:
            conv.estado_atencion = estado_atencion
            conv.operador_user_id = operador_user_id
            conv.ultimo_en = database.now_ar()
            s.commit()


def guardar_mensaje(conv_id, origen, texto, tiene_imagen=False):
    """origen: cliente | bot | operador."""
    with database.get_db() as s:
        s.add(database.BotMensaje(conversacion_id=conv_id, origen=origen,
                                  texto=texto, tiene_imagen=tiene_imagen))
        conv = s.get(database.BotConversacion, conv_id)
        if conv:
            conv.ultimo_en = database.now_ar()
        s.commit()


# ── Lecturas para el panel de operadores ─────────────────────────────────────

def _conv_dict(c, nombres=None):
    nombres = nombres or {}
    return {'id': c.id, 'canal': c.canal, 'linea': c.linea or c.canal,
            'canal_user_id': c.canal_user_id,
            'nombre': c.nombre_cliente or c.canal_user_id,
            'estado': c.estado_atencion, 'operador_id': c.operador_user_id,
            'operador_nombre': nombres.get(c.operador_user_id),
            'cliente_observer_id': c.cliente_observer_id,
            'ultimo_en': c.ultimo_en.strftime('%d/%m %H:%M') if c.ultimo_en else ''}


def _mapa_nombres(session, ids):
    """uid -> nombre, en una sola query (evita N+1 al armar la bandeja)."""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    us = session.query(database.Usuario).filter(database.Usuario.id.in_(ids)).all()
    return {u.id: (u.nombre_completo or u.username) for u in us}


def listar_conversaciones(linea=None):
    """Para la bandeja: cola (derivadas) + atendidas por humanos, ordenadas por
    actividad. Las que están solo con el bot NO ensucian la bandeja."""
    with database.get_db() as s:
        q = (s.query(database.BotConversacion)
             .filter(database.BotConversacion.estado_atencion.in_(['cola', 'humano'])))
        if linea:
            q = q.filter(database.BotConversacion.linea == linea)
        convs = q.order_by(database.BotConversacion.ultimo_en.desc()).limit(100).all()
        nombres = _mapa_nombres(s, [c.operador_user_id for c in convs])
        return [_conv_dict(c, nombres) for c in convs]


def get_conversacion_full(conv_id):
    with database.get_db() as s:
        c = s.get(database.BotConversacion, conv_id)
        if not c:
            return None
        return _conv_dict(c, _mapa_nombres(s, [c.operador_user_id]))


def listar_operadores():
    """Usuarios que pueden atender (para el dropdown de transferencia)."""
    with database.get_db() as s:
        us = (s.query(database.Usuario)
              .filter(database.Usuario.activo.is_(True),
                      database.Usuario.rol.in_(['operador', 'admin', 'farmacia', 'dev']))
              .order_by(database.Usuario.username).all())
        return [{'id': u.id, 'nombre': u.nombre_completo or u.username, 'rol': u.rol}
                for u in us]


# ── Vinculación con la ficha del cliente (ObServer) ──────────────────────────

def _ult_digitos(s, n=8):
    d = ''.join(ch for ch in (s or '') if ch.isdigit())
    return d[-n:] if len(d) >= n else d


def _unico_por_sufijo(session, tabla, col, suf):
    """observer_id si EXACTAMENTE un cliente tiene el teléfono terminado en `suf`
    (ignora formato). None si no hay o si es ambiguo (≥2) — preferimos no
    autovincular antes que vincular al cliente equivocado."""
    rows = session.execute(database.text(
        f"SELECT observer_id FROM {tabla} "
        f"WHERE regexp_replace(coalesce({col},''), '\\D', '', 'g') LIKE :p "
        f"LIMIT 2"), {'p': f'%{suf}'}).fetchall()
    return rows[0][0] if len(rows) == 1 else None


def match_cliente_por_telefono(telefono):
    """Devuelve el observer_id del cliente con ese teléfono, o None.

    Maneja el lío de los celulares AR: el guardado suele venir con prefijo '15'
    (0341 15 5998877) y el wa_id de WhatsApp con '9' y sin '15' (549 341 5998877),
    así que los últimos 8 no siempre coinciden. Probamos 8 y luego 7 dígitos, y
    solo vinculamos si el match es ÚNICO (inequívoco)."""
    digd = ''.join(ch for ch in (telefono or '') if ch.isdigit())
    if len(digd) < 8:
        return None
    try:
        with database.get_db() as s:
            for n in (8, 7):
                suf = digd[-n:]
                for tabla, col in (('obs_clientes', 'telefono'), ('clientes', 'whatsapp')):
                    oid = _unico_por_sufijo(s, tabla, col, suf)
                    if oid:
                        return oid
    except Exception:  # noqa: BLE001 (regexp_replace no existe en SQLite / sin ObServer)
        return None
    return None


def buscar_clientes(query, limite=10):
    """Búsqueda manual de clientes por nombre o documento (para vincular a mano)."""
    q = (query or '').strip()
    if len(q) < 2:
        return []
    with database.get_db() as s:
        base = s.query(database.ObsCliente)
        if q.isdigit():
            base = base.filter(database.ObsCliente.documento_numero == int(q))
        else:
            base = base.filter(database.ObsCliente.apellido_nombre.ilike(f'%{q}%'))
        rows = base.order_by(database.ObsCliente.apellido_nombre).limit(limite).all()
        return [{'observer_id': r.observer_id, 'nombre': r.apellido_nombre,
                 'documento': r.documento_numero, 'telefono': r.telefono} for r in rows]


def get_ficha_cliente(observer_id):
    """Ficha completa del cliente: datos de ObServer + capa local editable."""
    if not observer_id:
        return None
    with database.get_db() as s:
        oc = s.get(database.ObsCliente, observer_id)
        if not oc:
            return None
        cl = s.query(database.Cliente).filter_by(observer_id=observer_id).first()
        return {
            'observer_id': oc.observer_id,
            'nombre': oc.apellido_nombre,
            'documento': f'{oc.documento_tipo or ""} {oc.documento_numero or ""}'.strip(),
            'telefono': oc.telefono,
            'domicilio': ', '.join(x for x in [oc.domicilio_direccion, oc.localidad] if x),
            'notas': cl.notas if cl else '',
            'tags': cl.tags if cl else '',
            'whatsapp': cl.whatsapp if cl else '',
            'email': cl.email if cl else '',
        }


def vincular_cliente(conv_id, observer_id):
    with database.get_db() as s:
        c = s.get(database.BotConversacion, conv_id)
        if not c:
            return {'ok': False, 'error': 'no existe'}
        c.cliente_observer_id = observer_id
        s.commit()
        return {'ok': True}


def desvincular_cliente(conv_id):
    return vincular_cliente(conv_id, None)


def guardar_ficha_local(observer_id, notas=None, tags=None):
    """Edita la capa local (Cliente) del cliente: notas/tags. La crea si no existe."""
    if not observer_id:
        return {'ok': False}
    with database.get_db() as s:
        cl = s.query(database.Cliente).filter_by(observer_id=observer_id).first()
        if not cl:
            cl = database.Cliente(observer_id=observer_id)
            s.add(cl)
        if notas is not None:
            cl.notas = notas
        if tags is not None:
            cl.tags = tags
        s.commit()
        return {'ok': True}


def _nota_sistema(session, conv_id, texto):
    session.add(database.BotMensaje(conversacion_id=conv_id, origen='sistema', texto=texto))


def tomar(conv_id, operador_id, operador_nombre):
    """Pull: el operador agarra una conversación. Anti-colisión: si ya la tomó
    OTRO operador, no la pisa y devuelve quién la tiene."""
    with database.get_db() as s:
        c = s.get(database.BotConversacion, conv_id)
        if not c:
            return {'ok': False, 'error': 'no existe'}
        if (c.estado_atencion == 'humano' and c.operador_user_id
                and c.operador_user_id != operador_id):
            otro = s.get(database.Usuario, c.operador_user_id)
            return {'ok': False, 'conflicto': (otro.nombre_completo or otro.username)
                    if otro else 'otro operador'}
        c.estado_atencion = 'humano'
        c.operador_user_id = operador_id
        c.ultimo_en = database.now_ar()
        _nota_sistema(s, conv_id, f'🙋 Tomada por {operador_nombre}')
        s.commit()
        return {'ok': True}


def transferir(conv_id, nuevo_operador_id, nuevo_nombre, de_nombre, nota=''):
    """Pasa la conversación a otro operador (sigue en 'humano')."""
    with database.get_db() as s:
        c = s.get(database.BotConversacion, conv_id)
        if not c:
            return {'ok': False, 'error': 'no existe'}
        c.estado_atencion = 'humano'
        c.operador_user_id = nuevo_operador_id
        c.ultimo_en = database.now_ar()
        txt = f'🔄 Transferida de {de_nombre} a {nuevo_nombre}'
        if nota:
            txt += f' — “{nota}”'
        _nota_sistema(s, conv_id, txt)
        s.commit()
        return {'ok': True}


def devolver_a_cola(conv_id, de_nombre):
    """Libera la conversación: vuelve a la cola para que la tome cualquiera."""
    with database.get_db() as s:
        c = s.get(database.BotConversacion, conv_id)
        if not c:
            return {'ok': False, 'error': 'no existe'}
        c.estado_atencion = 'cola'
        c.operador_user_id = None
        c.ultimo_en = database.now_ar()
        _nota_sistema(s, conv_id, f'↩️ {de_nombre} la devolvió a la cola')
        s.commit()
        return {'ok': True}


def get_mensajes(conv_id, desde_id=0):
    """Mensajes de una conversación (para el chat del panel). desde_id permite
    traer solo los nuevos (polling)."""
    with database.get_db() as s:
        msgs = (s.query(database.BotMensaje)
                .filter(database.BotMensaje.conversacion_id == conv_id,
                        database.BotMensaje.id > desde_id)
                .order_by(database.BotMensaje.id).all())
        return [{'id': m.id, 'origen': m.origen, 'texto': m.texto or '',
                 'tiene_imagen': m.tiene_imagen,
                 'hora': m.creado_en.strftime('%H:%M') if m.creado_en else ''}
                for m in msgs]


def lineas_distintas():
    """Las líneas/números de entrada que aparecieron (para el filtro del panel)."""
    with database.get_db() as s:
        rows = (s.query(database.BotConversacion.linea)
                .filter(database.BotConversacion.linea.isnot(None))
                .distinct().all())
        return sorted({r[0] for r in rows if r[0]})
