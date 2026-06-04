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

def _conv_dict(c):
    return {'id': c.id, 'canal': c.canal, 'linea': c.linea or c.canal,
            'canal_user_id': c.canal_user_id,
            'nombre': c.nombre_cliente or c.canal_user_id,
            'estado': c.estado_atencion, 'operador_id': c.operador_user_id,
            'ultimo_en': c.ultimo_en.strftime('%d/%m %H:%M') if c.ultimo_en else ''}


def listar_conversaciones(linea=None):
    """Para la bandeja: cola (derivadas) + atendidas por humanos, ordenadas por
    actividad. Las que están solo con el bot NO ensucian la bandeja."""
    with database.get_db() as s:
        q = (s.query(database.BotConversacion)
             .filter(database.BotConversacion.estado_atencion.in_(['cola', 'humano'])))
        if linea:
            q = q.filter(database.BotConversacion.linea == linea)
        convs = q.order_by(database.BotConversacion.ultimo_en.desc()).limit(100).all()
        return [_conv_dict(c) for c in convs]


def get_conversacion_full(conv_id):
    with database.get_db() as s:
        c = s.get(database.BotConversacion, conv_id)
        return _conv_dict(c) if c else None


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
