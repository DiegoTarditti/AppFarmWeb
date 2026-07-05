"""Helper para registrar eventos SLA (Diego 2026-06-22).

Una sola función `registrar()` que inserta en la tabla `eventos_sla` con
deduplicación: si ya hay un evento del mismo `tipo` + entidad (conv_id o
pedido_id) **sin resolver**, NO inserta otro. Esto permite llamar desde
crons que tickan cada 60s sin generar un registro por tick.

Tipos soportados (extendibles, son strings libres):
  - 'reaviso_publicacion'      (entidad: pedido)
  - 'retiro_excedido'          (entidad: pedido, cadete)
  - 'entrega_excedida'         (entidad: pedido, cadete)
  - 'drogueria_excedida'       (entidad: pedido)
  - 'sin_respuesta_cadete'     (entidad: conv, cadete)
  - 'sin_respuesta_cliente'    (entidad: conv)

Severidad:
  - 'aviso'    → warning, se puede resolver naturalmente.
  - 'critico'  → ya disparó una acción correctiva (ej. desasignar cadete).
"""
import logging

import database

log = logging.getLogger(__name__)


def registrar(tipo, *, severidad='aviso', conv_id=None, pedido_id=None,
              cadete_id=None, operador_id=None, minutos=None, detalle=None,
              dedup=True):
    """Inserta un evento SLA. Devuelve el id (nuevo o existente con dedup)."""
    with database.get_db() as s:
        E = database.EventoSLA
        if dedup:
            q = s.query(E).filter(E.tipo == tipo, E.resuelto_en.is_(None))
            if conv_id is not None:
                q = q.filter(E.conv_id == conv_id)
            elif pedido_id is not None:
                q = q.filter(E.pedido_id == pedido_id)
            existing = q.order_by(E.id.desc()).first()
            if existing:
                return existing.id
        e = E(tipo=tipo, severidad=severidad,
              conv_id=conv_id, pedido_id=pedido_id,
              cadete_id=cadete_id, operador_id=operador_id,
              minutos=minutos, detalle=detalle)
        s.add(e)
        s.commit()
        log.warning('[evento_sla] %s · sev=%s · conv=%s pedido=%s min=%s · %s',
                    tipo, severidad, conv_id, pedido_id, minutos, detalle or '')
        return e.id


def resolver(evento_id, resuelto_por_user_id=None):
    """Marca un evento como resuelto (cuando el operador lo atendió)."""
    with database.get_db() as s:
        e = s.get(database.EventoSLA, evento_id)
        if not e or e.resuelto_en:
            return False
        e.resuelto_en = database.now_ar()
        e.resuelto_por = resuelto_por_user_id
        s.commit()
        return True
