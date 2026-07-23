"""Refresco del snapshot product_analytics para el dashboard.

La fuente vieja (flujo /purchase con Excel del ERP) quedó en desuso → la tabla
queda vacía y el dashboard mostraba todo en cero. Esto la rellena con los datos
VIVOS de Observer (obs_stock + obs_ventas_mensuales + obs_productos), calculando
las mismas métricas que usa el armado (rotación, promedio, PVP reciente, etc.).

El dashboard sigue leyendo product_analytics (rápido); esto se dispara on-demand
desde un botón "Recalcular". Idempotente: reemplaza todo el snapshot.
"""
import json
from collections import defaultdict
from datetime import date

from sqlalchemy import func

from purchase_engine import rotation_index
from purchase_helpers import pvp_reciente


def refrescar_product_analytics(session):
    """Recalcula product_analytics desde Observer. Devuelve dict con stats."""
    from database import (
        Config,
        ObsCodigoBarras,
        ObsLaboratorio,
        ObsProducto,
        ObsRubro,
        ObsStock,
        ObsSubrubro,
        ObsVentaMensual,
        ProductAnalytics,
        Producto,
        now_ar,
    )

    cfg = session.get(Config, 1)
    rot_alta = float(getattr(cfg, 'rot_alta_min', 20.0) or 20.0) if cfg else 20.0
    rot_media = float(getattr(cfg, 'rot_media_min', 5.0) or 5.0) if cfg else 5.0

    hoy = date.today()

    def _slot(anio, mes):
        """Slot 0..11 (11 = mes actual). None si cae fuera de la ventana 12m."""
        diff = (hoy.year - anio) * 12 + (hoy.month - mes)
        return 11 - diff if 0 <= diff <= 11 else None

    # 1) Stock por producto (sumado entre farmacias).
    stock_por = {pid: int(st or 0) for pid, st in
                 session.query(ObsStock.producto_observer,
                               func.sum(ObsStock.stock_actual))
                 .group_by(ObsStock.producto_observer)}

    # 2) Ventas 12m por producto → arrays de unidades y montos (slot 11 = actual).
    u_arr = defaultdict(lambda: [0] * 12)
    m_arr = defaultdict(lambda: [0.0] * 12)
    for pid, anio, mes, uds, mto in session.query(
            ObsVentaMensual.producto_observer, ObsVentaMensual.anio,
            ObsVentaMensual.mes, func.sum(ObsVentaMensual.unidades),
            func.sum(ObsVentaMensual.monto)).group_by(
            ObsVentaMensual.producto_observer, ObsVentaMensual.anio,
            ObsVentaMensual.mes):
        s = _slot(anio, mes)
        if s is not None:
            u_arr[pid][s] += int(uds or 0)
            m_arr[pid][s] += float(mto or 0)

    # Universo: productos con stock > 0 o con ventas en la ventana.
    pids = {pid for pid, st in stock_por.items() if st > 0} | set(u_arr.keys())
    if not pids:
        return {'filas': 0}

    # 3) Catálogo: descripción, lab, subrubro→rubro, EAN principal.
    desc_por, labobs_por, subr_por = {}, {}, {}
    for oid, desc, labobs, subr in session.query(
            ObsProducto.observer_id, ObsProducto.descripcion,
            ObsProducto.laboratorio_observer, ObsProducto.subrubro_observer):
        desc_por[oid] = desc
        labobs_por[oid] = labobs
        subr_por[oid] = subr
    lab_nombre = {lid: nom for lid, nom in
                  session.query(ObsLaboratorio.observer_id, ObsLaboratorio.descripcion)}
    # subrubro → rubro_observer → nombre del rubro (para clasificar y filtrar stats).
    subr_a_rubro = {sid: rid for sid, rid in
                    session.query(ObsSubrubro.observer_id, ObsSubrubro.rubro_observer)}
    rubro_nombre = {rid: nom for rid, nom in
                    session.query(ObsRubro.observer_id, ObsRubro.descripcion)}
    ean_por = {}
    for pid, ean, orden in session.query(
            ObsCodigoBarras.producto_observer, ObsCodigoBarras.codigo_barras,
            ObsCodigoBarras.orden).filter(ObsCodigoBarras.fecha_baja.is_(None)):
        cur = ean_por.get(pid)
        if cur is None or (orden or 999) < cur[0]:
            ean_por[pid] = (orden or 999, ean)

    # 4) Construir las filas del snapshot.
    rows = []
    ahora = now_ar()
    for pid in pids:
        u = u_arr.get(pid, [0] * 12)
        m = m_arr.get(pid, [0.0] * 12)
        avg_monthly = round(sum(u) / 12.0, 2)
        # sin movimiento ~60d: sin ventas en los últimos 2 meses (slots 10 y 11).
        sin_mov = 1 if (u[11] == 0 and u[10] == 0) else 0
        pvp = pvp_reciente(u, m)
        codigo = (ean_por[pid][1] if pid in ean_por else f'OBS-{pid}')
        rubro_nom = rubro_nombre.get(subr_a_rubro.get(subr_por.get(pid)))
        rows.append({
            'codigo_barra': codigo[:20],
            'descripcion': (desc_por.get(pid) or '')[:200],
            'laboratorio': lab_nombre.get(labobs_por.get(pid)),
            'rubro': (rubro_nom or '')[:150] or None,
            'stock': stock_por.get(pid, 0),
            'avg_monthly': avg_monthly,
            'rotacion': rotation_index(avg_monthly, rot_alta, rot_media),
            'sin_mov_60d': sin_mov,
            'precio_pvp': pvp or None,
            'ventas_json': json.dumps(u),
            'actualizado_en': ahora,
        })

    # 5) Reemplazo total del snapshot (dedup por codigo_barra, último gana).
    dedup = {}
    for r in rows:
        dedup[r['codigo_barra']] = r
    session.query(ProductAnalytics).delete()
    session.flush()
    session.bulk_insert_mappings(ProductAnalytics, list(dedup.values()))
    session.commit()
    return {'filas': len(dedup), 'con_stock': sum(1 for r in dedup.values() if r['stock'] > 0),
            'sin_mov': sum(1 for r in dedup.values() if r['sin_mov_60d'])}
