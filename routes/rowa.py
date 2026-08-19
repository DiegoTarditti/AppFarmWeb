"""Módulo del robot BD Rowa: optimización de stock y compras.

Pantalla que lee el stock en vivo del robot (WWKS2), lo cruza con ventas reales
de ObServer y muestra recomendaciones: qué sacar/reducir del robot, vencimientos
próximos y capacidad por altura.

Solo Badia tiene robot, así que el módulo se **gatea por robot**: si no hay robot
accesible (ROWA_HOST), muestra un aviso en vez de romperse — así el código no
molesta en otras instancias de la app multi-farmacia.

La carga (robot + cruce) es pesada (~10 s), así que se cachea en memoria con TTL;
`?refresh=1` fuerza recarga.

Endpoints:
  GET /rowa            → dashboard (KPIs + recomendaciones + alturas)
  GET /rowa?refresh=1  → fuerza reconsulta del robot
  GET /rowa/export     → descarga el .xlsx en vivo
"""
from __future__ import annotations

import io
from datetime import datetime

from flask import Response, render_template, request
from flask_login import login_required

from database import get_db
from services.rowa_analisis import analizar_alturas, analizar_stock, diagnosticar
from services.rowa_client import RowaClient, RowaError
from services.rowa_observer import cruzar_con_observer

# Caché en memoria (un solo robot, un solo proceso relevante). TTL corto.
_CACHE: dict = {"ts": None, "payload": None}
_TTL_SEG = 600  # 10 min


def _cargar(refresh: bool = False) -> dict:
    """Devuelve el análisis (cacheado). Lanza RowaError si el robot no responde."""
    ahora = datetime.now()
    if (not refresh and _CACHE["payload"] is not None and _CACHE["ts"]
            and (ahora - _CACHE["ts"]).total_seconds() < _TTL_SEG):
        return _CACHE["payload"]

    with RowaClient() as robot:
        info = robot.robot_info
        arts = robot.stock_info(include_packs=True)

    filas, diag = analizar_stock(arts)
    alturas = analizar_alturas(arts)
    try:
        with get_db() as session:
            cruce = cruzar_con_observer(session, filas)
        # El cruce cambió rotación y recomendaciones con ventas reales → los KPIs
        # se recalculan, si no quedan mostrando los valores del proxy.
        diag = diagnosticar(filas)
    except Exception:  # noqa: BLE001 — sin DB/ObServer, se muestra el proxy
        cruce = {"matcheados": 0, "sin_match": len(filas), "error": "ObServer no disponible"}

    payload = {
        "robot": info, "filas": filas, "diag": diag, "alturas": alturas,
        "cruce": cruce, "generado": ahora,
    }
    _CACHE["ts"] = ahora
    _CACHE["payload"] = payload
    return payload


def init_app(app):

    @app.route("/rowa")
    @login_required
    def rowa_dashboard():
        try:
            data = _cargar(refresh=bool(request.args.get("refresh")))
        except (RowaError, OSError) as e:
            return render_template("rowa.html", sin_robot=True, error=str(e))

        filas = data["filas"]
        # Orden para operar: primero lo accionable (mayor espacio a liberar).
        accion = sorted(
            [f for f in filas if f.al_deposito > 0],
            key=lambda f: f.al_deposito * f.vol_unit_cm3, reverse=True)
        vencimientos = sorted(
            [f for f in filas if f.dias_prox_venc is not None and f.packs_venc_alerta],
            key=lambda f: f.dias_prox_venc)
        return render_template(
            "rowa.html",
            sin_robot=False,
            robot=data["robot"], diag=data["diag"], alturas=data["alturas"],
            cruce=data["cruce"], generado=data["generado"],
            filas=sorted(filas, key=lambda f: (f.nombre_obs or f.nombre or "").lower()),
            accion=accion, vencimientos=vencimientos,
            n_accion=len(accion), n_venc=len(vencimientos),
        )

    @app.route("/rowa/export")
    @login_required
    def rowa_export_xlsx():
        from services.rowa_export import construir_workbook
        data = _cargar()
        wb = construir_workbook(data["filas"], data["diag"], data["alturas"])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        nombre = f"Rowa-{datetime.now():%Y-%m-%d}.xlsx"
        return Response(
            buf.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{nombre}"'})
