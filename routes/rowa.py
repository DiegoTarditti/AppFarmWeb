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
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta

from flask import Response, abort, jsonify, render_template, request
from flask_login import login_required

from database import RowaNuevo, get_db
from services.rowa_analisis import analizar_alturas, analizar_stock, clean_ean, diagnosticar
from services.rowa_client import RowaClient, RowaError
from services.rowa_observer import cruzar_con_observer

# Caché en memoria (un solo robot, un solo proceso relevante). TTL corto.
_CACHE: dict = {"ts": None, "payload": None}
_TTL_SEG = 600  # 10 min

# Boca de salida del robot para egreso a depósito (las de venta son otras).
BOCA_EGRESO = 1
# Egresos recientes en memoria, para la pantalla imprimible (no necesita DB:
# el doc se imprime al momento y se carga en ObServer).
_EGRESOS: dict = {}


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

        # Upsert artículos nuevos y cargar el mapa completo desde DB.
        nuevos_map = {}
        try:
            nuevos_actuales = [f for f in filas if f.es_nuevo]
            with get_db() as session:
                hoy = date.today()
                for f in nuevos_actuales:
                    if not session.query(RowaNuevo).filter_by(article_id=f.article_id).first():
                        fa = hoy - timedelta(days=f.antig_max_d) if f.antig_max_d > 0 else hoy
                        session.add(RowaNuevo(
                            article_id=f.article_id,
                            ean=f.ean,
                            nombre=f.nombre_obs or f.nombre,
                            confirmado=True,
                            fecha_alta=fa,
                        ))
                session.commit()
                nuevos_map = {
                    r.article_id: {
                        "confirmado": r.confirmado,
                        "fecha_alta": r.fecha_alta,
                        "detectado_en": r.detectado_en,
                        "ean": r.ean,
                        "nombre": r.nombre,
                    }
                    for r in session.query(RowaNuevo).order_by(RowaNuevo.detectado_en.desc()).all()
                }
        except Exception:
            pass

        # Orden para operar: primero lo accionable (mayor espacio a liberar).
        accion = sorted(
            [f for f in filas if f.al_deposito > 0],
            key=lambda f: f.al_deposito * f.vol_unit_cm3, reverse=True)
        vencimientos = sorted(
            [f for f in filas if f.dias_prox_venc is not None and f.packs_venc_alerta],
            key=lambda f: f.dias_prox_venc)
        n_nuevos = sum(1 for v in nuevos_map.values() if v["confirmado"])
        return render_template(
            "rowa.html",
            sin_robot=False,
            robot=data["robot"], diag=data["diag"], alturas=data["alturas"],
            cruce=data["cruce"], generado=data["generado"],
            filas=sorted(filas, key=lambda f: (f.nombre_obs or f.nombre or "").lower()),
            accion=accion, vencimientos=vencimientos,
            n_accion=len(accion), n_venc=len(vencimientos),
            nuevos_map=nuevos_map, n_nuevos=n_nuevos,
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

    # ---- Vistas de limpieza (solo lectura: muestran candidatos a sacar) ----
    _TIPOS = {
        "vencimiento": {
            "titulo": "Limpieza por vencimiento",
            "sub": "Productos con vencimiento real próximo — sacarlos antes de que venzan.",
            "icono": "⏰",
        },
        "rotacion": {
            "titulo": "Limpieza por baja rotación",
            "sub": "Durmientes que ocupan lugar y casi no se venden — liberar el robot.",
            "icono": "📦",
        },
    }

    @app.route("/rowa/limpieza/<tipo>")
    @login_required
    def rowa_limpieza(tipo):
        if tipo not in _TIPOS:
            abort(404)
        try:
            data = _cargar(refresh=bool(request.args.get("refresh")))
        except (RowaError, OSError) as e:
            return render_template("rowa_limpieza.html", sin_robot=True, error=str(e),
                                   tipo=tipo, meta=_TIPOS[tipo])
        filas = data["filas"]
        if tipo == "vencimiento":
            cand = sorted(
                [f for f in filas if f.dias_prox_venc is not None and f.packs_venc_alerta],
                key=lambda f: f.dias_prox_venc)
        else:  # rotacion
            cand = sorted(
                [f for f in filas if f.al_deposito > 0],
                key=lambda f: f.al_deposito * f.vol_unit_cm3, reverse=True)
        return render_template(
            "rowa_limpieza.html", sin_robot=False, tipo=tipo, meta=_TIPOS[tipo],
            robot=data["robot"], generado=data["generado"],
            candidatos=cand, n=len(cand), boca=BOCA_EGRESO)

    @app.route("/rowa/extraer", methods=["POST"])
    @login_required
    def rowa_extraer():
        """Saca en lote los productos marcados por la boca de egreso y guarda el
        movimiento para el reporte imprimible que se carga en ObServer."""
        payload = request.get_json(silent=True) or {}
        crudos = payload.get("items") or []
        seleccion = {}
        for it in crudos:
            aid = str(it.get("article_id") or "").strip()
            try:
                cant = int(it.get("cantidad") or 0)
            except (TypeError, ValueError):
                cant = 0
            if aid and cant > 0:
                seleccion[aid] = {"cantidad": cant, "nombre": it.get("nombre") or "",
                                  "ean": it.get("ean") or ""}
        if not seleccion:
            return jsonify({"ok": False, "error": "No marcaste ningún producto."}), 400

        pares = [(aid, v["cantidad"]) for aid, v in seleccion.items()]
        try:
            with RowaClient() as robot:
                res = robot.output_batch(pares, BOCA_EGRESO)
        except (RowaError, OSError) as e:
            return jsonify({"ok": False, "error": f"No se pudo completar con el robot: {e}"}), 502

        _CACHE["payload"] = None  # el stock del robot cambió

        confirmados = defaultdict(list)
        for pk in res["dispensados"]:
            confirmados[pk["article_id"]].append(pk)
        lineas = []
        for aid, sel in seleccion.items():
            packs = confirmados.get(aid, [])
            lineas.append({
                "article_id": aid, "nombre": sel["nombre"],
                "ean": sel["ean"] or (clean_ean(packs[0]["scan_code"]) if packs else ""),
                "pedido": sel["cantidad"], "salieron": len(packs), "packs": packs,
            })
        lineas.sort(key=lambda x: (x["nombre"] or "").lower())

        eid = uuid.uuid4().hex[:8]
        _EGRESOS[eid] = {
            "id": eid, "tipo": payload.get("tipo") or "rotacion",
            "generado": datetime.now(), "boca": res["boca"], "estado": res["estado"],
            "lineas": lineas,
            "total_pedido": sum(x["pedido"] for x in lineas),
            "total_salieron": sum(x["salieron"] for x in lineas),
        }
        return jsonify({"ok": True, "egreso_id": eid,
                        "salieron": _EGRESOS[eid]["total_salieron"],
                        "pedido": _EGRESOS[eid]["total_pedido"], "estado": res["estado"]})

    @app.route("/rowa/egreso/<eid>")
    @login_required
    def rowa_egreso(eid):
        egreso = _EGRESOS.get(eid)
        if not egreso:
            abort(404)
        return render_template("rowa_egreso.html", e=egreso, meta=_TIPOS.get(egreso["tipo"]))

    @app.route("/rowa/nuevo/<article_id>/toggle", methods=["POST"])
    @login_required
    def rowa_nuevo_toggle(article_id):
        with get_db() as session:
            r = session.query(RowaNuevo).filter_by(article_id=article_id).first()
            if not r:
                return jsonify({"ok": False, "error": "No encontrado"}), 404
            r.confirmado = not r.confirmado
            r.fecha_alta = date.today() if r.confirmado else None
            session.commit()
            return jsonify({
                "ok": True,
                "confirmado": r.confirmado,
                "fecha_alta": r.fecha_alta.isoformat() if r.fecha_alta else None,
            })
