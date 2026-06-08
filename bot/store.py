"""Persistencia del bot: conversaciones + mensajes en la DB.

Reemplaza el estado en memoria → habilita el handoff (panel de operadores),
el historial, multi-línea y que sobreviva reinicios.
"""
from datetime import date, datetime, timedelta

from sqlalchemy import and_, func, or_

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
                oid = match_cliente_por_telefono(cid)
                if oid:
                    conv.cliente_id = database.get_or_create_cliente(s, observer_id=oid)
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


def estado_atencion_de(canal, canal_user_id):
    """Estado de atención de una conversación SIN crearla (bot si no existe)."""
    with database.get_db() as s:
        conv = (s.query(database.BotConversacion)
                .filter_by(canal=canal, canal_user_id=str(canal_user_id)).first())
        return conv.estado_atencion if conv else 'bot'


def conversaciones_para_reenganche(minutos, max_minutos=120):
    """Conversaciones que el bot atiende, que quedaron a mitad de un flujo
    (esperando input del cliente) y sin actividad entre `minutos` y `max_minutos`.
    El techo de 120 min (2hs) garantiza que el mensaje llega dentro de la ventana
    libre de WhatsApp (24hs), evitando la necesidad de templates aprobados.
    `esperando IS NOT NULL` se limpia al re-enganchar → no vuelve a disparar."""
    ahora = database.now_ar()
    desde = ahora - timedelta(minutes=max_minutos)
    hasta = ahora - timedelta(minutes=minutos)
    with database.get_db() as s:
        convs = (s.query(database.BotConversacion)
                 .filter(database.BotConversacion.estado_atencion == 'bot',
                         database.BotConversacion.esperando.isnot(None),
                         database.BotConversacion.ultimo_en >= desde,
                         database.BotConversacion.ultimo_en < hasta)
                 .limit(50).all())
        return [{'id': c.id, 'canal': c.canal, 'canal_user_id': c.canal_user_id}
                for c in convs]


def revisar_inactividad(conv_id, minutos):
    """Auto-retorno al bot: si la conversación está derivada/atendida pero sin
    actividad por más de `minutos`, la devuelve al bot. True si la devolvió."""
    with database.get_db() as s:
        c = s.get(database.BotConversacion, conv_id)
        if not c or c.estado_atencion not in ('cola', 'humano'):
            return False
        if not c.ultimo_en or (database.now_ar() - c.ultimo_en) <= timedelta(minutes=minutos):
            return False
        c.estado_atencion = 'bot'
        c.operador_user_id = None
        _nota_sistema(s, conv_id, '↩️ Volvió al bot por inactividad')
        s.commit()
        return True


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

def _conv_dict(c, nombres=None, supervisado=False):
    nombres = nombres or {}
    return {'id': c.id, 'canal': c.canal, 'linea': c.linea or c.canal,
            'canal_user_id': c.canal_user_id,
            'nombre': c.nombre_cliente or c.canal_user_id,
            'estado': c.estado_atencion, 'operador_id': c.operador_user_id,
            'operador_nombre': nombres.get(c.operador_user_id),
            'cliente_id': c.cliente_id,
            'cliente_observer_id': c.cliente.observer_id if c.cliente else None,
            'tiene_encargo': bool(c.tiene_encargo),
            'supervisado': supervisado,
            'ultimo_en': c.ultimo_en.strftime('%d/%m %H:%M') if c.ultimo_en else ''}


def _mapa_nombres(session, ids):
    """uid -> nombre, en una sola query (evita N+1 al armar la bandeja)."""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    us = session.query(database.Usuario).filter(database.Usuario.id.in_(ids)).all()
    return {u.id: (u.nombre_completo or u.username) for u in us}


def listar_conversaciones(linea=None):
    """Para la bandeja: TODAS las conversaciones recientes (cola, humano y también
    las que maneja solo el bot), ordenadas por actividad. El panel filtra por
    pestaña (Sin asignar / Mías / Bot / Todas). Así el equipo puede supervisar
    y meterse en cualquier charla, no solo en las derivadas."""
    with database.get_db() as s:
        q = s.query(database.BotConversacion)
        if linea:
            q = q.filter(database.BotConversacion.linea == linea)
        convs = q.order_by(database.BotConversacion.ultimo_en.desc()).limit(100).all()
        nombres = _mapa_nombres(s, [c.operador_user_id for c in convs])
        # IDs de convs notificadas al supervisor
        supervisadas = set()
        if convs:
            from sqlalchemy import text as _text
            ids = [c.id for c in convs]
            placeholders = ','.join(str(i) for i in ids)
            rows = s.execute(_text(
                f'SELECT DISTINCT conversacion_id FROM informe_enviado WHERE conversacion_id IN ({placeholders})'
            )).fetchall()
            supervisadas = {r[0] for r in rows}
        return [_conv_dict(c, nombres, supervisado=c.id in supervisadas) for c in convs]


def get_conversacion_full(conv_id):
    with database.get_db() as s:
        c = s.get(database.BotConversacion, conv_id)
        if not c:
            return None
        return _conv_dict(c, _mapa_nombres(s, [c.operador_user_id]))


def _iniciales(nombre):
    parts = [p for p in (nombre or '').replace(',', ' ').split() if p]
    return (''.join(p[0] for p in parts[:2]) or '?').upper()


def listar_operadores():
    """Usuarios que pueden atender, con su presencia (estado + conectado).
    Conectado = mandó heartbeat en los últimos 70 s (panel abierto)."""
    ahora = database.now_ar()
    with database.get_db() as s:
        us = (s.query(database.Usuario)
              .filter(database.Usuario.activo.is_(True),
                      database.Usuario.rol.in_(['operador', 'admin', 'farmacia', 'dev']))
              .order_by(database.Usuario.username).all())
        return [{'id': u.id, 'nombre': u.nombre_completo or u.username, 'rol': u.rol,
                 'username': u.username,
                 'iniciales': _iniciales(u.nombre_completo or u.username),
                 'estado': u.estado_presencia or 'online',
                 'conectado': bool(u.ultima_actividad
                                   and (ahora - u.ultima_actividad) < timedelta(seconds=70))}
                for u in us]


def heartbeat(user_id):
    """Marca actividad del operador (panel abierto)."""
    with database.get_db() as s:
        u = s.get(database.Usuario, user_id)
        if u:
            u.ultima_actividad = database.now_ar()
            s.commit()


def set_presencia(user_id, estado):
    """Cambia el estado manual del operador (online/ocupado/ausente)."""
    if estado not in ('online', 'ocupado', 'ausente'):
        return {'ok': False, 'error': 'estado inválido'}
    with database.get_db() as s:
        u = s.get(database.Usuario, user_id)
        if u:
            u.estado_presencia = estado
            u.ultima_actividad = database.now_ar()
            s.commit()
    return {'ok': True, 'estado': estado}


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
    """Búsqueda de clientes por nombre (multi-token AND) o documento exacto."""
    q = (query or '').strip()
    if len(q) < 2:
        return []
    with database.get_db() as s:
        base = s.query(database.ObsCliente)
        if q.isdigit():
            base = base.filter(database.ObsCliente.documento_numero == int(q))
        else:
            tokens = [t for t in q.split() if len(t) >= 2]
            filtros = [database.ObsCliente.apellido_nombre.ilike(f'%{t}%') for t in tokens]
            base = base.filter(and_(*filtros))
        rows = base.order_by(database.ObsCliente.apellido_nombre).limit(limite).all()
        return [{'observer_id': r.observer_id, 'nombre': r.apellido_nombre,
                 'documento': r.documento_numero, 'telefono': r.telefono} for r in rows]


def buscar_clientes_unificado(query, limite=12):
    """Búsqueda multitoken unificada: obs_clientes + clientes locales.
    Devuelve lista deduplicada. Un Cliente local con observer_id NO duplica
    la fila de ObServer (se prefiere la local)."""
    q = (query or '').strip()
    if len(q) < 2:
        return []
    with database.get_db() as s:
        # ── Buscar en obs_clientes (como buscar_clientes original) ──
        tokens = [t for t in q.split() if len(t) >= 2]
        obs_results = []
        if q.isdigit():
            rows = (s.query(database.ObsCliente)
                    .filter(database.ObsCliente.documento_numero == int(q))
                    .order_by(database.ObsCliente.apellido_nombre)
                    .limit(limite).all())
        elif tokens:
            filtros = [database.ObsCliente.apellido_nombre.ilike(f'%{t}%')
                       for t in tokens]
            rows = (s.query(database.ObsCliente)
                    .filter(and_(*filtros))
                    .order_by(database.ObsCliente.apellido_nombre)
                    .limit(limite).all())
        else:
            rows = []
        for r in rows:
            obs_results.append({
                'observer_id': r.observer_id, 'cliente_id': None,
                'nombre': r.apellido_nombre,
                'documento': r.documento_numero or '',
                'telefono': r.telefono or '',
                'ciudad': r.localidad or '',
            })

        # ── Buscar en clientes locales ──
        loc_results = []
        if q.isdigit():
            rows = (s.query(database.Cliente)
                    .filter(database.Cliente.dni == q)
                    .order_by(database.Cliente.apellido, database.Cliente.nombre)
                    .limit(limite).all())
        elif tokens:
            # AND multitoken sobre nombre + apellido + dni + telefono
            concat = func.lower(
                func.coalesce(database.Cliente.nombre, '') + ' ' +
                func.coalesce(database.Cliente.apellido, ''))
            filtros_loc = []
            for t in tokens:
                tl = t.lower()
                filtros_loc.append(or_(
                    concat.ilike(f'%{tl}%'),
                    database.Cliente.dni.ilike(f'%{tl}%'),
                    database.Cliente.telefono.ilike(f'%{tl}%'),
                ))
            rows = (s.query(database.Cliente)
                    .filter(and_(*filtros_loc))
                    .order_by(database.Cliente.apellido, database.Cliente.nombre)
                    .limit(limite).all())
        else:
            rows = []
        for r in rows:
            nombre = ', '.join(x for x in [r.apellido, r.nombre] if x) or '(sin nombre)'
            loc_results.append({
                'observer_id': r.observer_id, 'cliente_id': r.id,
                'nombre': nombre,
                'documento': r.dni or '',
                'telefono': r.telefono or '',
                'ciudad': r.ciudad or '',
            })

        # ── Dedup: si un local tiene observer_id, quitar la fila obs equivalente ──
        local_oids = {r['observer_id'] for r in loc_results if r.get('observer_id')}
        obs_results = [r for r in obs_results
                       if r['observer_id'] not in local_oids]

        return (obs_results + loc_results)[:limite]


def _ficha_de_cliente(s, cliente_id):
    """Ficha uniforme desde la tabla única `clientes`. Si la fila tiene
    observer_id, completa los datos maestros desde `obs_clientes`. Devuelve dict
    con `fuente` ('observer' | 'local') o None."""
    if not cliente_id:
        return None
    c = s.get(database.Cliente, cliente_id)
    if not c:
        return None
    oc = s.get(database.ObsCliente, c.observer_id) if c.observer_id else None
    if oc:
        nombre = oc.apellido_nombre
        documento = f'{oc.documento_tipo or ""} {oc.documento_numero or ""}'.strip()
        telefono = oc.telefono or c.telefono or ''
        domicilio = ', '.join(x for x in [oc.domicilio_direccion, oc.localidad] if x)
        fuente = 'observer'
    else:
        nombre = ', '.join(x for x in [c.apellido, c.nombre] if x) or '(sin nombre)'
        documento = c.dni or ''
        telefono = c.telefono or ''
        domicilio = ', '.join(x for x in [c.domicilio, c.ciudad] if x)
        fuente = 'local'
    return {
        'cliente_id': c.id, 'observer_id': c.observer_id, 'fuente': fuente,
        'nombre': nombre, 'documento': documento, 'telefono': telefono,
        'domicilio': domicilio,
        'notas': c.notas or '', 'tags': c.tags or '',
        'whatsapp': c.whatsapp or '', 'email': c.email or '',
    }


def get_ficha_cliente(observer_id):
    """Ficha de un cliente de ObServer (datos maestros + capa editable si existe).
    Read-only: NO crea fila en `clientes`."""
    if not observer_id:
        return None
    with database.get_db() as s:
        oc = s.get(database.ObsCliente, observer_id)
        if not oc:
            return None
        cl = s.query(database.Cliente).filter_by(observer_id=observer_id).first()
        return {
            'observer_id': oc.observer_id, 'cliente_id': cl.id if cl else None,
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
    """Vincula la conversación a un cliente de ObServer (get-or-create en la tabla
    única de clientes)."""
    with database.get_db() as s:
        c = s.get(database.BotConversacion, conv_id)
        if not c:
            return {'ok': False, 'error': 'no existe'}
        c.cliente_id = (database.get_or_create_cliente(s, observer_id=observer_id)
                        if observer_id else None)
        s.commit()
        return {'ok': True}


def desvincular_cliente(conv_id):
    """Desvincula la ficha de la conversación."""
    with database.get_db() as s:
        c = s.get(database.BotConversacion, conv_id)
        if not c:
            return {'ok': False}
        c.cliente_id = None
        s.commit()
        return {'ok': True}


def reset_conversacion_testing(conv_id):
    """Para testing: borra mensajes, domicilios y vinculación de cliente.
    Deja la conversación como si el usuario escribiera por primera vez."""
    with database.get_db() as s:
        c = s.get(database.BotConversacion, conv_id)
        if not c:
            return {'ok': False, 'error': 'no existe'}
        # 1. Borrar mensajes del historial
        s.query(database.BotMensaje).filter(
            database.BotMensaje.conversacion_id == conv_id
        ).delete(synchronize_session=False)
        # 2. Borrar domicilios ligados a esta conversación
        s.query(database.DomicilioCliente).filter(
            database.DomicilioCliente.conversacion_id == conv_id
        ).delete(synchronize_session=False)
        # 3. Resetear estado del flujo y desvincular cliente
        c.nodo = 'inicio'
        c.esperando = None
        c.estado_atencion = 'bot'
        c.operador_user_id = None
        c.cliente_id = None
        c.cliente_observer_id = None
        c.cliente_local_id = None
        s.commit()
        return {'ok': True}


def crear_cliente_local(conv_id, datos, creado_por=None):
    """Alta de un cliente nuevo (lead) desde el panel + lo vincula al chat.
    En WhatsApp guarda el teléfono del chat automáticamente."""
    with database.get_db() as s:
        c = s.get(database.BotConversacion, conv_id)
        if not c:
            return {'ok': False, 'error': 'no existe'}
        lead = dict(datos or {})
        if c.canal == 'whatsapp' and not lead.get('telefono'):
            lead['telefono'] = c.canal_user_id
        cid = database.get_or_create_cliente(s, lead=lead, creado_por=creado_por)
        if not cid:
            return {'ok': False, 'error': 'datos vacíos'}
        c.cliente_id = cid
        s.commit()
        return {'ok': True}


# ── Catálogo de ciudades ─────────────────────────────────────────────────────

def listar_ciudades():
    with database.get_db() as s:
        cs = (s.query(database.Ciudad).filter(database.Ciudad.activa.is_(True))
              .order_by(database.Ciudad.nombre).all())
        return [{'id': c.id, 'nombre': c.nombre, 'provincia': c.provincia or ''} for c in cs]


def crear_ciudad(nombre, provincia=None):
    nombre = (nombre or '').strip()
    if not nombre:
        return {'ok': False, 'error': 'nombre vacío'}
    with database.get_db() as s:
        existe = s.query(database.Ciudad).filter(
            database.Ciudad.nombre.ilike(nombre)).first()
        if existe:
            if not existe.activa:
                existe.activa = True
                s.commit()
            return {'ok': True, 'id': existe.id}
        c = database.Ciudad(nombre=nombre, provincia=(provincia or '').strip() or None)
        s.add(c)
        s.commit()
        return {'ok': True, 'id': c.id}


def eliminar_ciudad(ciudad_id):
    with database.get_db() as s:
        c = s.get(database.Ciudad, ciudad_id)
        if c:
            s.delete(c)
            s.commit()
        return {'ok': True}


def get_ficha_de_conversacion(conv_id):
    """Ficha del cliente vinculado a la conversación (tabla única).
    Devuelve un dict uniforme con `fuente` ('observer' | 'local') o None."""
    with database.get_db() as s:
        c = s.get(database.BotConversacion, conv_id)
        if not c:
            return None
        return _ficha_de_cliente(s, c.cliente_id)


def guardar_notas_conversacion(conv_id, notas):
    """Guarda las notas en la ficha (tabla única) del cliente vinculado al chat."""
    with database.get_db() as s:
        c = s.get(database.BotConversacion, conv_id)
        if not c or not c.cliente_id:
            return {'ok': False}
        cli = s.get(database.Cliente, c.cliente_id)
        if not cli:
            return {'ok': False}
        cli.notas = notas
        s.commit()
        return {'ok': True}


def guardar_ficha_local(observer_id, notas=None, tags=None):
    """Edita la capa editable (notas/tags) de un cliente de ObServer en la tabla
    única. La fila se crea si no existe (get-or-create por observer_id)."""
    if not observer_id:
        return {'ok': False}
    with database.get_db() as s:
        cid = database.get_or_create_cliente(s, observer_id=observer_id)
        cl = s.get(database.Cliente, cid)
        if notas is not None:
            cl.notas = notas
        if tags is not None:
            cl.tags = tags
        s.commit()
        return {'ok': True}


# ── Libreta de domicilios del cliente ────────────────────────────────────────

def _identidad_domicilio(s, conv_id):
    """(campo, valor) para colgar/leer domicilios: cliente vinculado si lo hay,
    si no la conversación."""
    c = s.get(database.BotConversacion, conv_id)
    if c and c.cliente_id:
        return ('cliente_id', c.cliente_id)
    return ('conversacion_id', conv_id)


def _dom_dict(d):
    return {'id': d.id, 'etiqueta': d.etiqueta or 'Otro',
            'direccion': d.direccion or '', 'localidad': d.localidad or '',
            'lat': d.lat, 'lng': d.lng, 'origen': d.origen}


def listar_domicilios_de_cliente(cliente_id=None, observer_id=None):
    """Domicilios de un cliente (para el alta de pedidos de reparto). Se puede
    pasar `cliente_id` directo o `observer_id` (se resuelve a la fila única)."""
    D = database.DomicilioCliente
    with database.get_db() as s:
        if not cliente_id and observer_id:
            c = s.query(database.Cliente).filter_by(observer_id=observer_id).first()
            cliente_id = c.id if c else None
        if not cliente_id:
            return []
        q = s.query(D).filter(D.cliente_id == cliente_id)
        return [_dom_dict(d) for d in q.order_by(D.id.desc()).all()]


def listar_domicilios(conv_id):
    """Domicilios del cliente vinculado Y los de la conversación (para no perder
    los que se guardaron antes de vincular el cliente)."""
    D = database.DomicilioCliente
    with database.get_db() as s:
        c = s.get(database.BotConversacion, conv_id)
        conds = [D.conversacion_id == conv_id]
        if c and c.cliente_id:
            conds.append(D.cliente_id == c.cliente_id)
        ds = (s.query(D).filter(or_(*conds))
              .order_by(func.coalesce(D.ultimo_uso_en, D.creado_en).desc(), D.id.desc())
              .all())
        return [_dom_dict(d) for d in ds]


def guardar_domicilio(conv_id, etiqueta=None, direccion=None, localidad=None,
                      lat=None, lng=None, origen=None):
    with database.get_db() as s:
        campo, valor = _identidad_domicilio(s, conv_id)
        d = database.DomicilioCliente(
            etiqueta=((etiqueta or '').strip()[:40] or None),
            direccion=((direccion or '').strip()[:200] or None),
            localidad=((localidad or '').strip()[:120] or None),
            lat=lat, lng=lng, origen=origen)
        setattr(d, campo, valor)
        s.add(d)
        s.commit()
        return {'ok': True, 'id': d.id}


def set_etiqueta_domicilio(dom_id, etiqueta):
    with database.get_db() as s:
        d = s.get(database.DomicilioCliente, dom_id)
        if not d:
            return {'ok': False}
        nueva_et = (etiqueta or '').strip()[:40] or 'Otro'
        D = database.DomicilioCliente
        # Eliminar duplicados previos con la misma etiqueta para este cliente/conv.
        cond = (D.cliente_id == d.cliente_id) if d.cliente_id else (D.conversacion_id == d.conversacion_id)
        dupes = (s.query(D)
                 .filter(cond, D.etiqueta == nueva_et, D.id != dom_id)
                 .all())
        for dup in dupes:
            s.delete(dup)
        d.etiqueta = nueva_et
        s.commit()
        return {'ok': True}


def marcar_uso_domicilio(dom_id):
    with database.get_db() as s:
        d = s.get(database.DomicilioCliente, dom_id)
        if d:
            d.ultimo_uso_en = database.now_ar()
            s.commit()


def eliminar_domicilio(dom_id):
    with database.get_db() as s:
        d = s.get(database.DomicilioCliente, dom_id)
        if d:
            s.delete(d)
            s.commit()
        return {'ok': True}


def get_domicilio(dom_id):
    with database.get_db() as s:
        d = s.get(database.DomicilioCliente, dom_id)
        return _dom_dict(d) if d else None


# ── Buscador de productos (panel de atención) ────────────────────────────────

def _fmt_presentacion(r):
    partes = []
    if r.concentracion_mg:
        c = r.concentracion_mg
        c = int(c) if c == int(c) else c
        partes.append(f"{c} {r.concentracion_unidad or 'mg'}")
    if r.cantidad_envase:
        n = r.cantidad_envase
        n = int(n) if n == int(n) else n
        partes.append(f"{n} {r.forma_farma or 'u.'}")
    elif r.forma_farma:
        partes.append(r.forma_farma)
    return ' · '.join(partes)


def buscar_productos_detalle(query, limite=12):
    """Búsqueda rica para el popup del panel: nombre, droga, presentación,
    precio, stock (con nivel ok/low/none) y flag de receta.

    Fuente primaria: obs_productos + obs_stock (66k filas, igual que el bot).
    Busca por descripción de marca Y por monodroga (obs_nombres_drogas).
    precio_pvp viene de product_analytics cuando existe.
    obs_productos (tipo venta → receta) + producto_atributos (concentración/forma)."""
    import os
    id_farmacia = int(os.environ.get('OBSERVER_ID_FARMACIA', '10525'))
    palabras = [p for p in (query or '').split() if p][:6]
    if not palabras:
        return []
    cond_prod  = ' AND '.join(f"op.descripcion ILIKE :p{i}" for i in range(len(palabras)))
    cond_droga = ' AND '.join(f"nd.descripcion ILIKE :p{i}" for i in range(len(palabras)))
    params = {f'p{i}': f'%{w}%' for i, w in enumerate(palabras)}
    params['lim'] = limite
    params['fid'] = id_farmacia
    sql = database.text(f"""
        SELECT op.descripcion,
               COALESCE(os.stock_actual, 0)             AS stock,
               COALESCE(pa.precio_pvp, pr.precio_pvp)   AS precio_pvp,
               nd.descripcion               AS droga,
               atr.concentracion_mg, atr.concentracion_unidad, atr.forma_farma, atr.cantidad_envase,
               op.id_tipo_venta_control     AS tvc,
               op.observer_id
          FROM obs_productos op
          JOIN obs_stock os
            ON os.producto_observer = op.observer_id
           AND os.id_farmacia = :fid
          LEFT JOIN obs_nombres_drogas nd
            ON nd.observer_id = op.nombre_droga_observer
          LEFT JOIN obs_codigos_barras cb
            ON cb.producto_observer = op.observer_id AND cb.fecha_baja IS NULL
          LEFT JOIN product_analytics pa
            ON pa.codigo_barra = cb.codigo_barras
          LEFT JOIN productos pr
            ON pr.observer_id = op.observer_id
          LEFT JOIN producto_atributos atr
            ON atr.producto_id = pr.id
         WHERE op.fecha_baja IS NULL
           AND ({cond_prod} OR {cond_droga})
         ORDER BY (os.stock_actual > 0) DESC, os.stock_actual DESC, op.descripcion
         LIMIT :lim
    """)
    try:
        with database.get_db() as s:
            rows = s.execute(sql, params).fetchall()
    except Exception:  # noqa: BLE001 (SQLite en tests no tiene estas tablas/funciones)
        return []
    out = []
    for r in rows:
        stock = int(r.stock or 0)
        nivel = 'none' if stock <= 0 else ('low' if stock <= 5 else 'ok')
        tvc = (r.tvc or '').strip()
        out.append({
            'nombre': r.descripcion, 'droga': r.droga or '',
            'presentacion': _fmt_presentacion(r),
            'precio': float(r.precio_pvp) if r.precio_pvp is not None else None,
            'stock': stock, 'nivel': nivel,
            'receta': bool(tvc and tvc != 'L'),
            'observer_id': r.observer_id,
        })
    return out


def _nota_sistema(session, conv_id, texto):
    session.add(database.BotMensaje(conversacion_id=conv_id, origen='sistema', texto=texto))


def nota_sistema(conv_id, texto):
    """Wrapper público de _nota_sistema (abre su propia sesión)."""
    with database.get_db() as s:
        _nota_sistema(s, conv_id, texto)


def get_ofertas_para_productos(observer_ids):
    """Devuelve ofertas activas para una lista de observer_ids de productos."""
    if not observer_ids:
        return []
    with database.get_db() as s:
        ofertas = (s.query(database.OfertaBot)
                   .filter(database.OfertaBot.observer_id.in_(observer_ids),
                           database.OfertaBot.activo.is_(True))
                   .all())
        return [{'id': o.id, 'observer_id': o.observer_id, 'descripcion': o.descripcion,
                 'tipo': o.tipo, 'valor': float(o.valor) if o.valor else None}
                for o in ofertas]


def registrar_oferta(conv_id, oferta_bot_id, usuario_id):
    """Registra que un operador ofreció una oferta al cliente."""
    with database.get_db() as s:
        s.add(database.OfertaRegistro(
            conversacion_id=conv_id, oferta_bot_id=oferta_bot_id,
            enviado_por=usuario_id))
        s.commit()


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


def get_historial_ia(conv_id, limite=10):
    """Últimos mensajes cliente/bot en formato Anthropic (lista de
    {role, content}), para darle MEMORIA de conversación al nodo de IA.

    Incluye el mensaje entrante (ya guardado por `procesar`). Colapsa turnos
    consecutivos del mismo rol (evita el 400 de la API por roles no alternados,
    p.ej. tras un handoff donde quedaron varios mensajes del cliente seguidos) y
    arranca siempre en 'user'."""
    with database.get_db() as s:
        msgs = (s.query(database.BotMensaje)
                .filter(database.BotMensaje.conversacion_id == conv_id,
                        database.BotMensaje.origen.in_(['cliente', 'bot']))
                .order_by(database.BotMensaje.id.desc())
                .limit(limite).all())
    historial = []
    for m in reversed(msgs):  # cronológico
        texto = (m.texto or '').strip()
        if not texto:
            continue
        rol = 'user' if m.origen == 'cliente' else 'assistant'
        if historial and historial[-1]['role'] == rol:
            historial[-1]['content'] += '\n' + texto
        else:
            historial.append({'role': rol, 'content': texto})
    while historial and historial[0]['role'] != 'user':
        historial.pop(0)
    return historial


def lineas_distintas():
    """Las líneas/números de entrada que aparecieron (para el filtro del panel)."""
    with database.get_db() as s:
        rows = (s.query(database.BotConversacion.linea)
                .filter(database.BotConversacion.linea.isnot(None))
                .distinct().all())
        return sorted({r[0] for r in rows if r[0]})


# ── Analítica: memoria de no-resueltos + caminos ─────────────────────────────

def registrar_interaccion(conv_id, canal=None, linea=None, texto=None,
                          camino='otro', resuelto=True, motivo=None,
                          tema=None, producto=None):
    """Guarda una fila de analítica por interacción procesada por el bot.
    Defensivo: si la analítica falla, NO rompe la respuesta al cliente."""
    try:
        with database.get_db() as s:
            s.add(database.BotInteraccion(
                conversacion_id=conv_id, canal=canal, linea=linea,
                texto=(texto or '')[:2000], camino=(camino or 'otro')[:30],
                resuelto=bool(resuelto), motivo=(motivo or None) and motivo[:30],
                tema=(tema or None) and tema[:80],
                producto=(producto or None) and producto[:160]))
            s.commit()
    except Exception as e:  # noqa: BLE001
        print('registrar_interaccion error:', e)


def _rango_dia(fecha):
    """('YYYY-MM-DD' | date | datetime | None) → (inicio, fin) datetime del día.
    None = hoy (hora AR)."""
    if fecha is None:
        d = database.now_ar().date()
    elif isinstance(fecha, datetime):
        d = fecha.date()
    elif isinstance(fecha, date):
        d = fecha
    else:
        d = datetime.strptime(str(fecha)[:10], '%Y-%m-%d').date()
    ini = datetime(d.year, d.month, d.day)
    return ini, ini + timedelta(days=1)


def listar_interacciones(desde=None, hasta=None, linea=None, motivo=None,
                         solo_no_resueltas=False, limite=500):
    """Listado filtrable para el panel. `desde`/`hasta` por día (inclusive)."""
    I = database.BotInteraccion
    with database.get_db() as s:
        q = s.query(I)
        if desde:
            q = q.filter(I.creado_en >= _rango_dia(desde)[0])
        if hasta:
            q = q.filter(I.creado_en < _rango_dia(hasta)[1])
        if linea:
            q = q.filter(I.linea == linea)
        if motivo:
            q = q.filter(I.motivo == motivo)
        if solo_no_resueltas:
            q = q.filter(I.resuelto.is_(False))
        rows = q.order_by(I.creado_en.desc()).limit(limite).all()
        return [{'id': r.id, 'conversacion_id': r.conversacion_id,
                 'canal': r.canal, 'linea': r.linea, 'texto': r.texto or '',
                 'camino': r.camino, 'resuelto': r.resuelto, 'motivo': r.motivo,
                 'tema': r.tema, 'producto': r.producto,
                 'fecha': r.creado_en.strftime('%d/%m %H:%M') if r.creado_en else ''}
                for r in rows]


def resumen_del_dia(fecha=None, linea=None):
    """Agregados de un día: totales, por camino, por motivo, top productos sin
    stock (demanda perdida) y top temas (tanda 2). Todo por SQL, sin IA."""
    I = database.BotInteraccion
    ini, fin = _rango_dia(fecha)
    conds = [I.creado_en >= ini, I.creado_en < fin]
    if linea:
        conds.append(I.linea == linea)
    with database.get_db() as s:
        total = s.query(func.count(I.id)).filter(*conds).scalar() or 0
        por_camino = dict(s.query(I.camino, func.count(I.id)).filter(*conds)
                          .group_by(I.camino).all())
        por_motivo = dict(s.query(I.motivo, func.count(I.id))
                          .filter(*conds, I.resuelto.is_(False))
                          .group_by(I.motivo).all())
        prod_rows = (s.query(func.lower(func.trim(I.producto)), func.count(I.id))
                     .filter(*conds, I.motivo == 'sin_stock', I.producto.isnot(None))
                     .group_by(func.lower(func.trim(I.producto)))
                     .order_by(func.count(I.id).desc()).limit(15).all())
        tema_rows = (s.query(I.tema, func.count(I.id))
                     .filter(*conds, I.tema.isnot(None))
                     .group_by(I.tema).order_by(func.count(I.id).desc()).limit(15).all())
    no_resueltas = sum(por_motivo.values())
    return {
        'fecha': ini.strftime('%Y-%m-%d'),
        'total': total,
        'resueltas': total - no_resueltas,
        'no_resueltas': no_resueltas,
        'por_camino': {k: v for k, v in por_camino.items()},
        'por_motivo': {k: v for k, v in por_motivo.items() if k},
        'productos_sin_stock': [{'producto': p, 'veces': n} for p, n in prod_rows],
        'temas': [{'tema': t, 'veces': n} for t, n in tema_rows],
    }


def lineas_interacciones():
    """Líneas distintas vistas en la analítica (para el filtro del panel). Propia
    de bot_interacciones: no depende de que la conversación siga viva."""
    I = database.BotInteraccion
    with database.get_db() as s:
        rows = s.query(I.linea).filter(I.linea.isnot(None)).distinct().all()
        return sorted({r[0] for r in rows if r[0]})
