"""Cruce del stock del robot Rowa con datos reales de ObServer.

Enriquece cada `ArticuloAnalizado` (de rowa_analisis) con lo que el robot NO
sabe y sí vive en ObServer para esta farmacia:

- ventas reales de los últimos 12 meses (`obs_ventas_mensuales`),
- rotación recalculada por ventas (reemplaza el proxy por antigüedad),
- heladera (`obs_productos.requiere_cadena_frio` — el robot no lo expone),
- descripción del catálogo y tipo de venta (L/R/A).

El cruce es por EAN (el ScanCode del pack, ya limpio). Validado contra la DB de
Badia el 2026-08-19: 98.8% de los EANs del robot cruzan, 96% con ventas, 99.4%
mapean a un solo producto. El 0.6% que mapea a >1 producto se desempata eligiendo
el de más ventas.

appfarmweb es multi-farmacia pero cada instancia corre para una sola; las ventas
se filtran por `farmacia_operativa()`. Solo Badia tiene robot.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime

from services.rowa_analisis import _recomendar

logger = logging.getLogger(__name__)

# Umbrales de rotación por ventas (u/mes). Defaults = los de Config del sistema
# de compras, para que "Alta/Media/Baja" signifique lo mismo en los dos lados.
ROT_ALTA_DEFAULT = 20.0
ROT_MEDIA_DEFAULT = 5.0
MESES_VENTA_DEFAULT = 3  # ventana para estimar u/mes (meses recientes)


def _clasificar_por_ventas(unid_mes: float, rot_alta: float, rot_media: float) -> str:
    if unid_mes >= rot_alta:
        return "Alta"
    if unid_mes >= rot_media:
        return "Media"
    if unid_mes > 0:
        return "Baja"
    return "Durmiente"


def cargar_umbrales(session) -> tuple[float, float]:
    """Lee rot_alta_min / rot_media_min de la Config; si no, usa los defaults."""
    try:
        import database
        cfg = session.get(database.Config, 1)
        alta = float(getattr(cfg, "rot_alta_min", None) or ROT_ALTA_DEFAULT)
        media = float(getattr(cfg, "rot_media_min", None) or ROT_MEDIA_DEFAULT)
        return alta, media
    except Exception:  # noqa: BLE001
        return ROT_ALTA_DEFAULT, ROT_MEDIA_DEFAULT


def cruzar_con_observer(session, filas, hoy: date | None = None,
                        rot_alta: float | None = None, rot_media: float | None = None,
                        meses_venta: int = MESES_VENTA_DEFAULT) -> dict:
    """Enriquece `filas` (lista de ArticuloAnalizado) in place con datos de
    ObServer. Devuelve un resumen del cruce.

    Recalcula, para cada artículo que cruza:
      - unid_mes_est: promedio de ventas de los últimos `meses_venta` meses
      - rotacion: por ventas reales (marca rotacion_por_ventas=True)
      - recomendacion / sug_en_robot / al_deposito: con la rotación nueva
      - requiere_frio, nombre_obs, tipo_venta, producto_observer, ventas_arr
    """
    hoy = hoy or datetime.now().date()
    if rot_alta is None or rot_media is None:
        _a, _m = cargar_umbrales(session)
        rot_alta = rot_alta if rot_alta is not None else _a
        rot_media = rot_media if rot_media is not None else _m

    # Import local para no acoplar el import del módulo a la DB.
    from database import ObsCodigoBarras, ObsProducto
    from services.farmacia import farmacia_operativa
    from services.pedido_estacional import obtener_ventas_arr_bulk

    eans = sorted({f.ean for f in filas if f.ean})
    if not eans:
        return {"total": len(filas), "eans": 0, "matcheados": 0, "sin_match": len(filas)}

    # EAN → set de producto_observer (puede haber >1; deduplicamos con el set).
    ean_prods: dict[str, set[int]] = defaultdict(set)
    for cb_ean, cb_prod in (
        session.query(ObsCodigoBarras.codigo_barras, ObsCodigoBarras.producto_observer)
        .filter(ObsCodigoBarras.codigo_barras.in_(eans)).all()
    ):
        ean_prods[cb_ean].add(cb_prod)

    todos_prods = {p for s in ean_prods.values() for p in s}
    id_farmacia = farmacia_operativa()
    ventas = obtener_ventas_arr_bulk(session, todos_prods, id_farmacia, hoy)

    # Desempate del EAN ambiguo: el producto con más ventas (el "vivo").
    def _elegir(prods: set[int]) -> int:
        return max(prods, key=lambda pid: sum(ventas.get(pid, [])))

    ean_prod: dict[str, int] = {e: _elegir(prods) for e, prods in ean_prods.items() if prods}
    ambiguos = sum(1 for prods in ean_prods.values() if len(prods) > 1)

    info = {
        p.observer_id: p
        for p in session.query(ObsProducto).filter(
            ObsProducto.observer_id.in_(set(ean_prod.values())))
    }

    matcheados = 0
    for f in filas:
        pid = ean_prod.get(f.ean)
        if pid is None:
            continue
        matcheados += 1
        arr = ventas.get(pid, [0.0] * 12)
        recientes = arr[-meses_venta:] or [0.0]
        unid_mes = round(sum(recientes) / len(recientes), 1)

        f.producto_observer = pid
        f.ventas_arr = arr
        f.unid_mes_est = unid_mes
        p = info.get(pid)
        if p is not None:
            f.requiere_frio = bool(p.requiere_cadena_frio)
            f.nombre_obs = (p.descripcion_custom or p.descripcion or "").strip() or None
            f.tipo_venta = p.id_tipo_venta_control

        # Rotación por ventas reales + recomendación recalculada.
        f.rotacion = _clasificar_por_ventas(unid_mes, rot_alta, rot_media)
        f.rotacion_por_ventas = True
        reco, sug, depo = _recomendar(f.cantidad, f.rotacion, f.antig_max_d, unid_mes)
        f.recomendacion, f.sug_en_robot, f.al_deposito = reco, sug, depo

    resumen = {
        "total": len(filas),
        "eans": len(eans),
        "matcheados": matcheados,
        "sin_match": len(filas) - matcheados,
        "eans_ambiguos": ambiguos,
        "id_farmacia": id_farmacia,
        "rot_alta": rot_alta,
        "rot_media": rot_media,
    }
    logger.info("Rowa×ObServer: %s/%s artículos cruzados (%s ambiguos)",
                matcheados, len(filas), ambiguos)
    return resumen
