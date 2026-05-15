"""Helper para horarios de reparto por droguería.

Calcula el próximo cierre/reparto a partir de la matriz semanal almacenada en
`proveedor_horarios_reparto`. Lo usa el dashboard de "Compra del día" para el
countdown live por droguería.
"""
from datetime import datetime, timedelta

from database import ProveedorHorarioReparto


def _parse_hhmm(s):
    """'HH:MM' → (hh, mm). Retorna None si inválido."""
    if not s or ':' not in s:
        return None
    try:
        hh, mm = s.split(':', 1)
        h, m = int(hh), int(mm)
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except (ValueError, TypeError):
        pass
    return None


def proximo_cierre(session, proveedor_id, ahora=None):
    """Devuelve un dict {fecha, falta_segundos, hora_str} con el próximo cierre
    futuro de la droguería, o None si no hay slots configurados.

    Recorre los próximos 7 días arrancando desde `ahora` (default: now()) y
    encuentra el primer slot futuro entre los activos.
    """
    if ahora is None:
        ahora = datetime.now()
    slots = (session.query(ProveedorHorarioReparto)
             .filter(ProveedorHorarioReparto.proveedor_id == proveedor_id,
                     ProveedorHorarioReparto.activo.is_(True))
             .all())
    if not slots:
        return None
    # Indexar por dia_semana → list de (h, m)
    por_dia = {}
    for s in slots:
        hm = _parse_hhmm(s.hora)
        if hm:
            por_dia.setdefault(s.dia_semana, []).append(hm)
    if not por_dia:
        return None

    # Buscar en los próximos 7 días.
    for d in range(7):
        cand_date = ahora.date() + timedelta(days=d)
        # Python weekday(): 0=Lunes..6=Domingo (coincide con nuestra convención).
        dia_sem = cand_date.weekday()
        slots_del_dia = sorted(por_dia.get(dia_sem, []))
        for h, m in slots_del_dia:
            cand = datetime.combine(cand_date, datetime.min.time()).replace(hour=h, minute=m)
            if cand > ahora:
                return {
                    'fecha':           cand,
                    'falta_segundos':  int((cand - ahora).total_seconds()),
                    'hora_str':        f'{h:02d}:{m:02d}',
                }
    return None


def urgencia_cierre(falta_segundos):
    """Clasifica la urgencia hasta el próximo cierre en 3 niveles.

    Usado para mostrar un badge en pedido/día y para ponderar la cantidad a
    pedir (más horas → más unidades, ver _ponderar_target_dias en compras_dia).

    - corto: < 8h    (mucha urgencia, factor < 0.33)
    - medio: 8–24h   (cobertura normal de 1 día)
    - largo: ≥ 24h   (fin de semana o feriado, hay que cargar más)

    Devuelve dict {nivel, label, horas} o None si falta_segundos es None.
    """
    if falta_segundos is None:
        return None
    horas = falta_segundos / 3600
    if horas < 8:
        nivel = 'corto'
    elif horas < 24:
        nivel = 'medio'
    else:
        nivel = 'largo'
    return {
        'nivel': nivel,
        'horas': round(horas, 1),
        'label': nivel.capitalize(),
    }


def horarios_por_dia(session, proveedor_id):
    """Devuelve la matriz {dia_semana: [hora_str, ...]} ordenada por hora.

    Para renderizar la UI estilo grilla "Lunes: 07:10, 10:20, 15:00, 19:00".
    """
    slots = (session.query(ProveedorHorarioReparto)
             .filter(ProveedorHorarioReparto.proveedor_id == proveedor_id,
                     ProveedorHorarioReparto.activo.is_(True))
             .all())
    out = {d: [] for d in range(7)}
    for s in slots:
        if _parse_hhmm(s.hora):
            out[s.dia_semana].append(s.hora)
    for d in out:
        out[d].sort()
    return out
