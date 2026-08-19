"""Análisis de stock del robot Rowa: rotación, vencimientos y optimización.

Toma los artículos que devuelve `rowa_client.RowaClient.stock_info()` y produce,
por artículo, las métricas y recomendaciones que Lisandro armó a mano en las
planillas (Stock / Optimización / Capacidad-Alturas), pero desde datos en vivo.

Es lógica pura (sin robot ni DB), así que se testea sola. La rotación acá es un
**proxy por antigüedad de los packs** (`StockInDate`); cuando se cruce con las
ventas reales de ObServer, `UnidMesEst` y la clasificación se recalculan con
datos de venta y esto queda de fallback.

IMPORTANTE: el robot NO expone heladera (IsInFridge viene vacío) ni ubicación
física de canales. La heladera se completa después desde
`obs_productos.requiere_cadena_frio`.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime

# -- Umbrales de rotación (días de antigüedad promedio de los packs) -----
ROT_ALTA_MAX = 21      # <=21 d  -> Alta
ROT_MEDIA_MAX = 60     # 22-60 d -> Media
ROT_BAJA_MAX = 120     # 61-120 d -> Baja; >120 -> Durmiente
PARADA_REVISAR_D = 180  # 1 sola unidad parada más de esto -> revisar
VENC_ALERTA_D = 120    # vencimiento próximo a vigilar


def clean_ean(raw: str | None) -> str | None:
    """Devuelve un EAN-13 limpio.

    El robot a veces entrega el DataMatrix GS1 completo escaneado
    (AI 01 = GTIN-14, luego 21=serie, 17=venc, 10=lote, con separadores 0x1D).
    Extrae el GTIN y lo baja a EAN-13. Si ya es un EAN limpio, lo deja igual.
    """
    if not raw:
        return None
    s = str(raw).strip()
    # DataMatrix GS1: empieza con "01" + GTIN-14
    if len(s) >= 16 and s.startswith("01") and s[2:16].isdigit():
        gtin = s[2:16]
        return gtin[1:] if gtin[0] == "0" else gtin
    # EAN/GTIN plano
    digits = re.sub(r"\D", "", s)
    if 8 <= len(digits) <= 14:
        if len(digits) == 14 and digits[0] == "0":
            return digits[1:]
        return digits
    return s or None


def _parse_date(v: str | None) -> date | None:
    if not v:
        return None
    try:
        return datetime.strptime(v[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


@dataclass
class ArticuloAnalizado:
    article_id: str
    ean: str | None
    nombre: str | None
    cantidad: int                    # packs en el robot
    rotacion: str                    # Alta / Media / Baja / Durmiente
    unid_mes_est: float | None       # proxy de unidades/mes (provisional)
    antig_prom_d: int                # antigüedad promedio de los packs
    antig_max_d: int                 # antigüedad del pack más viejo
    vol_unit_cm3: float              # volumen de un pack (promedio)
    vol_total_cm3: float             # volumen ocupado por el artículo
    prox_venc: date | None           # vencimiento más próximo
    dias_prox_venc: int | None
    recomendacion: str
    sug_en_robot: int                # cuántos packs conviene dejar
    al_deposito: int                 # cuántos mandar al depósito
    requiere_frio: bool = False      # se completa desde ObServer
    packs_venc_alerta: int = 0       # packs que vencen dentro de VENC_ALERTA_D
    # -- se completan al cruzar con ObServer (services/rowa_observer.py) --
    producto_observer: int | None = None
    nombre_obs: str | None = None    # descripción del catálogo ObServer
    tipo_venta: str | None = None    # L=libre, R=receta, A=archivada, ...
    ventas_arr: list = field(default_factory=list)  # 12 meses de unidades
    rotacion_por_ventas: bool = False  # True si la rotación salió de ventas reales


def clasificar_rotacion(antig_prom_d: int) -> str:
    if antig_prom_d <= ROT_ALTA_MAX:
        return "Alta"
    if antig_prom_d <= ROT_MEDIA_MAX:
        return "Media"
    if antig_prom_d <= ROT_BAJA_MAX:
        return "Baja"
    return "Durmiente"


def _recomendar(cantidad: int, rotacion: str, antig_max_d: int,
                unid_mes_est: float | None) -> tuple[str, int, int]:
    """Devuelve (recomendación, sugerido_en_robot, al_deposito)."""
    sin_venta = not unid_mes_est
    # 1 sola unidad parada hace mucho: revisar, no tocar el reparto todavía
    if cantidad <= 1:
        if antig_max_d > PARADA_REVISAR_D:
            return "Revisar: 1u parada >180d", 1, 0
        return "OK mantener", cantidad, 0
    if rotacion == "Durmiente":
        if sin_venta:
            return "Candidato a sacar del robot", 1, cantidad - 1
        return "Reducir en robot (lento, ocupa lugar)", 1, cantidad - 1
    if rotacion == "Baja":
        dejar = max(1, math.ceil(cantidad / 2))
        return "Bajar cantidad interna", dejar, cantidad - dejar
    return "OK mantener", cantidad, 0


def analizar_articulo(art, hoy: date | None = None) -> ArticuloAnalizado | None:
    """Convierte un `rowa_client.Article` en su fila de análisis."""
    hoy = hoy or datetime.now().date()
    packs = [p for p in art.packs if (p.state or "Available") != "NotAvailable"]
    cantidad = len(packs)
    if cantidad == 0:
        return None

    antigs = []
    vols = []
    vencs = []
    for p in packs:
        sd = _parse_date(p.stock_in_date)
        if sd:
            antigs.append((hoy - sd).days)
        if p.volume_cm3:
            vols.append(p.volume_cm3)
        ed = _parse_date(p.expiry_date)
        if ed:
            vencs.append(ed)

    # EAN del artículo = ScanCode más frecuente entre sus packs (limpiado).
    codigos = [clean_ean(p.scan_code) for p in packs if p.scan_code]
    codigos = [c for c in codigos if c]
    if not codigos and art.product_codes:
        codigos = [clean_ean(art.product_codes[0])]
    ean = Counter(codigos).most_common(1)[0][0] if codigos else None

    antig_prom = round(sum(antigs) / len(antigs)) if antigs else 0
    antig_max = max(antigs) if antigs else 0
    vol_unit = round(sum(vols) / len(vols), 1) if vols else 0.0
    vol_total = round(sum(vols), 1) if vols else 0.0
    prox_venc = min(vencs) if vencs else None
    dias_prox = (prox_venc - hoy).days if prox_venc else None
    packs_venc_alerta = sum(1 for e in vencs if (e - hoy).days <= VENC_ALERTA_D)

    rotacion = clasificar_rotacion(antig_prom)
    # Proxy de unidades/mes: reposición implícita por antigüedad (provisional,
    # se reemplaza por ventas reales de ObServer). Los durmientes quedan sin dato.
    unid_mes = None
    if antig_prom > 0 and rotacion != "Durmiente":
        unid_mes = round(cantidad / (antig_prom / 30.0), 1)

    reco, sug, depo = _recomendar(cantidad, rotacion, antig_max, unid_mes)

    return ArticuloAnalizado(
        article_id=art.article_id,
        ean=ean,
        nombre=art.name,
        cantidad=cantidad,
        rotacion=rotacion,
        unid_mes_est=unid_mes,
        antig_prom_d=antig_prom,
        antig_max_d=antig_max,
        vol_unit_cm3=vol_unit,
        vol_total_cm3=vol_total,
        prox_venc=prox_venc,
        dias_prox_venc=dias_prox,
        recomendacion=reco,
        sug_en_robot=sug,
        al_deposito=depo,
        packs_venc_alerta=packs_venc_alerta,
    )


@dataclass
class Diagnostico:
    generado: date
    articulos: int
    packs: int
    vol_ocupado_l: float
    por_rotacion: dict = field(default_factory=dict)
    con_accion: int = 0
    espacio_recuperable_l: float = 0.0


def analizar_stock(articulos, hoy: date | None = None
                   ) -> tuple[list[ArticuloAnalizado], Diagnostico]:
    """Analiza toda la lista de artículos del robot. Devuelve (filas, diagnóstico)."""
    hoy = hoy or datetime.now().date()
    filas = [f for f in (analizar_articulo(a, hoy) for a in articulos) if f]

    por_rot: dict[str, int] = {}
    for f in filas:
        por_rot[f.rotacion] = por_rot.get(f.rotacion, 0) + 1

    # "Revisar 1u" no libera espacio (no se cuenta como acción, igual que Lisandro).
    _sin_accion = {"OK mantener", "Revisar: 1u parada >180d"}
    con_accion = [f for f in filas if f.recomendacion not in _sin_accion]
    recup_cm3 = sum(f.al_deposito * f.vol_unit_cm3 for f in filas)

    diag = Diagnostico(
        generado=hoy,
        articulos=len(filas),
        packs=sum(f.cantidad for f in filas),
        vol_ocupado_l=round(sum(f.vol_total_cm3 for f in filas) / 1000, 1),
        por_rotacion=por_rot,
        con_accion=len(con_accion),
        espacio_recuperable_l=round(recup_cm3 / 1000, 1),
    )
    return filas, diag


# -- Análisis de alturas (para densificar estantes) ----------------------
RANGOS_ALTURA = [(0, 20), (20, 30), (30, 40), (40, 50), (50, 60), (60, 80), (80, 145)]
# Cobertura de stock si el canal se setea a cada altura, y filas/metro relativas.
SEPARACIONES = [145, 104, 80, 60, 50, 40]


def analizar_alturas(articulos, margen_mm: int = 15) -> dict:
    """Distribución de alturas de packs para decidir separación de estantes."""
    alturas = [p.height_mm for a in articulos for p in a.packs if p.height_mm]
    n = len(alturas)
    if not n:
        return {}
    alturas.sort()
    dist = []
    for lo, hi in RANGOS_ALTURA:
        c = sum(1 for h in alturas if lo <= h < hi) if hi < 145 else sum(1 for h in alturas if lo <= h <= hi)
        dist.append({"rango": f"{lo}-{hi} mm", "packs": c, "pct": round(100 * c / n, 1)})

    rendimiento = []
    base_filas = None
    for sep in SEPARACIONES:
        # "cubre" = cajas cuya altura entra en un canal de esta separación.
        cubre = sum(1 for h in alturas if h <= sep) / n
        # filas por metro: el margen es el grosor/holgura del estante, se suma a
        # la separación (reproduce las filas/metro que calculó Lisandro).
        filas_m = int(1000 // (sep + margen_mm))
        if sep == 80:
            base_filas = filas_m
        rendimiento.append({
            "canal_mm": sep,
            "cubre_pct": round(100 * cubre, 1),
            "filas_x_metro": filas_m,
        })
    if base_filas:
        for r in rendimiento:
            r["vs_80mm"] = round((r["filas_x_metro"] - base_filas) / base_filas, 1)

    return {
        "packs": n,
        "altura_prom": round(sum(alturas) / n),
        "altura_mediana": alturas[n // 2],
        "altura_max": max(alturas),
        "margen_mm": margen_mm,
        "distribucion": dist,
        "rendimiento": rendimiento,
    }
