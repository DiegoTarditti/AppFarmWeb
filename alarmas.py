"""Motor de alarmas del sistema.

Cada función `check_X(session) -> Alarma | None` evalúa un chequeo concreto
de salud (cron parado, tabla creciendo sin control, sync desfasada, etc).

Si la función devuelve `None`, el chequeo está OK. Si devuelve `Alarma`,
significa que el threshold se superó y la pantalla `/admin/alarmas` la muestra.

Diseño:
- Funciones puras: cada check toma `session` y devuelve `Alarma | None`.
- Sin red: solo SQL contra DB local. Las alarmas externas (GitHub API,
  Render API) van en `alarmas_externas.py` (futuro) para no bloquear el
  endpoint si la red falla.
- Cero state global: cada call a `evaluar_todas()` es independiente.

Spec: ver `c:/AppSeguimiento/mantenimiento-y-alarmas.md`.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

SEV_CRITICA = 'critica'
SEV_ALTA = 'alta'
SEV_MEDIA = 'media'
SEV_BAJA = 'baja'

# Orden de severidad para sortear (críticas primero).
SEV_ORDER = {
    SEV_CRITICA: 0,
    SEV_ALTA: 1,
    SEV_MEDIA: 2,
    SEV_BAJA: 3,
}


@dataclass
class Alarma:
    nombre: str
    severidad: str
    valor_actual: str
    threshold: str
    accion: str
    link: Optional[str] = None

    def to_dict(self):
        return {
            'nombre': self.nombre,
            'severidad': self.severidad,
            'valor_actual': self.valor_actual,
            'threshold': self.threshold,
            'accion': self.accion,
            'link': self.link,
        }


# ─── Checks críticos (acción inmediata) ──────────────────────────────────────


def check_cron_errors_24h(session) -> Optional[Alarma]:
    """Filas con estado=error en cron_log últimas 24h."""
    from database import CronLog
    corte = datetime.now() - timedelta(hours=24)
    n = (session.query(CronLog)
         .filter(CronLog.estado == 'error', CronLog.inicio >= corte)
         .count())
    if n >= 1:
        return Alarma(
            nombre='Cron con errores (24h)',
            severidad=SEV_CRITICA,
            valor_actual=f'{n} {"error" if n == 1 else "errores"}',
            threshold='≥1 error últimas 24h',
            accion='Revisar /admin/cron-log con filtro estado=error',
            link='/admin/cron-log?estado=error',
        )
    return None


def check_sync_observer_parado(session) -> Optional[Alarma]:
    """Si el último sync de ObServer fue hace >48h, la sync está rota silenciosa."""
    from database import CronLog
    ultimo = (session.query(CronLog.inicio)
              .filter(CronLog.proceso.like('sync_%'))
              .order_by(CronLog.inicio.desc())
              .first())
    if ultimo is None:
        return Alarma(
            nombre='Sync ObServer nunca corrió',
            severidad=SEV_CRITICA,
            valor_actual='nunca',
            threshold='≤48h',
            accion='Verificar DockerPanel en la farmacia + conectividad a 192.168.1.137',
            link='/admin/cron-log?proceso=sync_',
        )
    horas = (datetime.now() - ultimo[0]).total_seconds() / 3600
    if horas > 48:
        return Alarma(
            nombre='Sync ObServer parado',
            severidad=SEV_CRITICA,
            valor_actual=f'{int(horas)}h sin sync',
            threshold='≤48h',
            accion='Verificar DockerPanel en la farmacia + conectividad a 192.168.1.137',
            link='/admin/cron-log?proceso=sync_',
        )
    return None


def check_recalculo_os_atrasado(session) -> Optional[Alarma]:
    """Cron `recalcular_os_clientes` no corrió en los últimos 7 días."""
    try:
        from database import ClienteOsInferida
        ultimo = (session.query(ClienteOsInferida.calculado_en)
                  .order_by(ClienteOsInferida.calculado_en.desc())
                  .first())
    except Exception:
        return None
    if ultimo is None:
        return Alarma(
            nombre='cliente_os_inferida vacía',
            severidad=SEV_ALTA,
            valor_actual='nunca calculada',
            threshold='≤7 días',
            accion='Disparar manual desde /admin → "Recalcular OS clientes"',
            link='/admin',
        )
    dias = (datetime.now() - ultimo[0]).total_seconds() / 86400
    if dias > 7:
        return Alarma(
            nombre='Recálculo OS clientes atrasado',
            severidad=SEV_ALTA,
            valor_actual=f'{int(dias)} días sin recalcular',
            threshold='≤7 días (cron diario)',
            accion='Verificar GitHub Actions cron-os-recalcular + secret CRON_SECRET',
            link='/admin/cron-log?proceso=recalcular_os_clientes',
        )
    return None


# ─── Checks altos (atender esta semana) ──────────────────────────────────────


def check_cron_log_grande(session) -> Optional[Alarma]:
    """Tabla `cron_log` >10k filas — la purga >7d no está corriendo."""
    from database import CronLog
    n = session.query(CronLog).count()
    if n > 10000:
        return Alarma(
            nombre='cron_log creció sin purgar',
            severidad=SEV_ALTA,
            valor_actual=f'{n} filas',
            threshold='≤10.000 filas',
            accion='Disparar manual: POST /api/cron-log/purgar?dias=7. O verificar cron en DockerPanel.',
            link='/admin/cron-log',
        )
    return None


def check_obs_codigos_barras_desfasada(session) -> Optional[Alarma]:
    """obs_codigos_barras es la tabla más grande de Observer; si tiene >14d
    sin sync_en, algo está mal en el push desde DockerPanel."""
    try:
        from sqlalchemy import func

        from database import ObsCodigoBarras
        ultimo = (session.query(func.max(ObsCodigoBarras.sync_en)).scalar())
    except Exception:
        return None
    if ultimo is None:
        return None
    dias = (datetime.now() - ultimo).total_seconds() / 86400
    if dias > 14:
        return Alarma(
            nombre='obs_codigos_barras desfasada',
            severidad=SEV_MEDIA,
            valor_actual=f'{int(dias)} días sin sync',
            threshold='≤14 días',
            accion='En DockerPanel: "Importar códigos de barras" + push a Render',
            link='/admin/cron-log?proceso=sync_codigos_barras',
        )
    return None


def check_pedidos_pendientes_viejos(session) -> Optional[Alarma]:
    """Más de 50 pedidos en estado='PENDIENTE' con >30 días = se acumulan
    sin cerrar (limpiar o investigar workflow)."""
    try:
        from database import Pedido
    except Exception:
        return None
    corte = datetime.now() - timedelta(days=30)
    try:
        n = (session.query(Pedido)
             .filter(Pedido.estado == 'PENDIENTE',
                     Pedido.creado_en < corte).count())
    except Exception:
        return None
    if n > 50:
        return Alarma(
            nombre='Pedidos pendientes viejos',
            severidad=SEV_MEDIA,
            valor_actual=f'{n} pedidos PENDIENTE >30 días',
            threshold='≤50',
            accion='Revisar /orders con filtro pendientes y cerrar/eliminar los que ya no aplican',
            link='/orders?estado=PENDIENTE',
        )
    return None


def check_matview_sin_refresh(session) -> Optional[Alarma]:
    """Las matviews deberían refrescarse al menos cada 2 días."""
    try:
        from sqlalchemy import func

        from database import MvRefreshLog
        ultimo = session.query(func.max(MvRefreshLog.inicio)).scalar()
    except Exception:
        return None
    if ultimo is None:
        return None
    dias = (datetime.now() - ultimo).total_seconds() / 86400
    if dias > 2:
        return Alarma(
            nombre='Matviews sin refresh',
            severidad=SEV_MEDIA,
            valor_actual=f'{int(dias * 24)}h sin refresh',
            threshold='≤48h',
            accion='Disparar manual desde /admin o cron del DockerPanel',
            link='/admin/cron-log?proceso=mv_refresh',
        )
    return None


# ─── Registro y evaluación masiva ────────────────────────────────────────────

# Lista de chequeos a evaluar. Para sumar uno nuevo: definir la función arriba
# y agregarla acá.
CHECKS = [
    check_cron_errors_24h,
    check_sync_observer_parado,
    check_recalculo_os_atrasado,
    check_cron_log_grande,
    check_obs_codigos_barras_desfasada,
    check_pedidos_pendientes_viejos,
    check_matview_sin_refresh,
]


def evaluar_todas(session) -> list[Alarma]:
    """Corre todos los checks y devuelve solo los que dispararon, ordenados
    por severidad (críticos primero)."""
    alarmas = []
    for check in CHECKS:
        try:
            alarma = check(session)
            if alarma is not None:
                alarmas.append(alarma)
        except Exception as e:
            # Un check roto NO debe bloquear los demás. Registrar en log
            # pero seguir.
            try:
                from flask import current_app
                current_app.logger.warning(
                    'Alarma %s falló al evaluar: %s', check.__name__, e,
                )
            except Exception:
                pass
    alarmas.sort(key=lambda a: (SEV_ORDER.get(a.severidad, 99), a.nombre))
    return alarmas


def contar_por_severidad(alarmas: list[Alarma]) -> dict:
    """Devuelve dict tipo {'critica': 1, 'alta': 2, 'media': 0, 'baja': 0}."""
    out = {SEV_CRITICA: 0, SEV_ALTA: 0, SEV_MEDIA: 0, SEV_BAJA: 0}
    for a in alarmas:
        if a.severidad in out:
            out[a.severidad] += 1
    return out
