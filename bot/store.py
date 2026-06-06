"""Persistencia del bot: conversaciones + mensajes en la DB.

Reemplaza el estado en memoria → habilita el handoff (panel de operadores),
el historial, multi-línea y que sobreviva reinicios.
"""
from datetime import timedelta

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


def estado_atencion_de(canal, canal_user_id):
    """Estado de atención de una conversación SIN crearla (bot si no existe)."""
    with database.get_db() as s:
        conv = (s.query(database.BotConversacion)
                .filter_by(canal=canal, canal_user_id=str(canal_user_id)).first())
        return conv.estado_atencion if conv else 'bot'


def conversaciones_para_reenganche(minutos):
    """Conversaciones que el bot atiende, que quedaron a mitad de un flujo
    (esperando input del cliente) y sin actividad por más de `minutos`.
    `esperando IS NOT NULL` se limpia al re-enganchar → no vuelve a disparar."""
    limite = database.now_ar() - timedelta(minutes=minutos)
    with database.get_db() as s:
        convs = (s.query(database.BotConversacion)
                 .filter(database.BotConversacion.estado_atencion == 'bot',
                         database.BotConversacion.esperando.isnot(None),
                         database.BotConversacion.ultimo_en < limite)
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

def _conv_dict(c, nombres=None):
    nombres = nombres or {}
    return {'id': c.id, 'canal': c.canal, 'linea': c.linea or c.canal,
            'canal_user_id': c.canal_user_id,
            'nombre': c.nombre_cliente or c.canal_user_id,
            'estado': c.estado_atencion, 'operador_id': c.operador_user_id,
            'operador_nombre': nombres.get(c.operador_user_id),
            'cliente_observer_id': c.cliente_observer_id,
            'cliente_local_id': c.cliente_local_id,
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
        return [_conv_dict(c, nombres) for c in convs]


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
    """Vincula a un cliente de ObServer (limpia el lead local si había)."""
    with database.get_db() as s:
        c = s.get(database.BotConversacion, conv_id)
        if not c:
            return {'ok': False, 'error': 'no existe'}
        c.cliente_observer_id = observer_id
        if observer_id:
            c.cliente_local_id = None
        s.commit()
        return {'ok': True}


def desvincular_cliente(conv_id):
    """Desvincula cualquier ficha (ObServer o local)."""
    with database.get_db() as s:
        c = s.get(database.BotConversacion, conv_id)
        if not c:
            return {'ok': False}
        c.cliente_observer_id = None
        c.cliente_local_id = None
        s.commit()
        return {'ok': True}


def crear_cliente_local(conv_id, datos, creado_por=None):
    """Alta de un cliente nuevo (lead local) desde el panel + lo vincula al chat.
    En WhatsApp guarda el teléfono del chat automáticamente."""
    with database.get_db() as s:
        c = s.get(database.BotConversacion, conv_id)
        if not c:
            return {'ok': False, 'error': 'no existe'}
        cl = database.ClienteLocal(
            nombre=(datos.get('nombre') or '').strip() or None,
            apellido=(datos.get('apellido') or '').strip() or None,
            dni=(datos.get('dni') or '').strip() or None,
            domicilio=(datos.get('domicilio') or '').strip() or None,
            ciudad=(datos.get('ciudad') or '').strip() or None,
            telefono=c.canal_user_id if c.canal == 'whatsapp' else None,
            creado_por=creado_por)
        s.add(cl)
        s.flush()
        c.cliente_local_id = cl.id
        c.cliente_observer_id = None
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
    """Ficha del cliente vinculado a la conversación, sea de ObServer o local.
    Devuelve un dict uniforme con `fuente` ('observer' | 'local') o None."""
    with database.get_db() as s:
        c = s.get(database.BotConversacion, conv_id)
        if not c:
            return None
        if c.cliente_observer_id:
            f = get_ficha_cliente(c.cliente_observer_id)
            if f:
                f['fuente'] = 'observer'
            return f
        if c.cliente_local_id:
            cl = s.get(database.ClienteLocal, c.cliente_local_id)
            if cl:
                nombre = ', '.join(x for x in [cl.apellido, cl.nombre] if x) or '(sin nombre)'
                domic = ', '.join(x for x in [cl.domicilio, cl.ciudad] if x)
                return {'fuente': 'local', 'observer_id': None,
                        'nombre': nombre, 'documento': cl.dni or '',
                        'telefono': cl.telefono or '', 'domicilio': domic,
                        'notas': cl.notas or '', 'tags': ''}
        return None


def guardar_notas_conversacion(conv_id, notas):
    """Guarda las notas en la ficha vinculada (ObServer → Cliente; local → ClienteLocal)."""
    with database.get_db() as s:
        c = s.get(database.BotConversacion, conv_id)
        if not c:
            return {'ok': False}
        if c.cliente_observer_id:
            return guardar_ficha_local(c.cliente_observer_id, notas=notas)
        if c.cliente_local_id:
            cl = s.get(database.ClienteLocal, c.cliente_local_id)
            if cl:
                cl.notas = notas
                s.commit()
                return {'ok': True}
        return {'ok': False}


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

    Junta product_analytics (stock/precio) + productos (monodroga) +
    producto_atributos (concentración/forma) + obs_productos (tipo venta → receta)."""
    palabras = [p for p in (query or '').split() if p][:6]
    if not palabras:
        return []
    conds = ' AND '.join(f"pa.descripcion ILIKE :p{i}" for i in range(len(palabras)))
    params = {f'p{i}': f'%{w}%' for i, w in enumerate(palabras)}
    params['lim'] = limite
    sql = database.text(f"""
        SELECT pa.descripcion, pa.stock, pa.precio_pvp,
               p.monodroga AS droga,
               atr.concentracion_mg, atr.concentracion_unidad, atr.forma_farma, atr.cantidad_envase,
               op.id_tipo_venta_control AS tvc
          FROM product_analytics pa
          LEFT JOIN productos p          ON p.codigo_barra = pa.codigo_barra
          LEFT JOIN obs_productos op     ON op.observer_id = p.observer_id
          LEFT JOIN producto_atributos atr ON atr.producto_id = p.id
         WHERE {conds}
         ORDER BY (pa.stock > 0) DESC, pa.stock DESC, pa.descripcion
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
        })
    return out


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
