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

from database import RowaCarga, RowaNuevo, RowaSnapshot, get_db
from services.rowa_analisis import (
    analizar_alturas,
    analizar_stock,
    clasificar_eventos_stock,
    clean_ean,
    diagnosticar,
    peor_tipo_evento,
)
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
        # es_nuevo ya fue refinado por cruzar_con_observer usando ventas_arr:
        # si el producto vendía hace >6 meses, es_nuevo=False (falso positivo del proxy).
        nuevos_map = {}
        try:
            filas_map = {f.article_id: f for f in filas}
            ahora = datetime.now()
            hoy = ahora.date()
            with get_db() as session:
                registros = session.query(RowaNuevo).all()
                conocidos = {r.article_id for r in registros}

                # Actualizar registros existentes sin verificación ObServer
                for r in registros:
                    f = filas_map.get(r.article_id)
                    if f and f.ventas_arr and r.obs_verificado_en is None:
                        fa = hoy - timedelta(days=f.antig_max_d) if f.antig_max_d > 0 else hoy
                        r.confirmado = f.es_nuevo
                        r.fecha_alta = fa if f.es_nuevo else None
                        r.obs_verificado_en = ahora

                # Insertar artículos nuevos detectados por primera vez
                for f in filas:
                    if f.es_nuevo and f.article_id not in conocidos:
                        fa = hoy - timedelta(days=f.antig_max_d) if f.antig_max_d > 0 else hoy
                        session.add(RowaNuevo(
                            article_id=f.article_id,
                            ean=f.ean,
                            nombre=f.nombre_obs or f.nombre,
                            confirmado=True,
                            fecha_alta=fa,
                            obs_verificado_en=ahora if f.ventas_arr else None,
                        ))

                session.commit()
                nuevos_map = {
                    r.article_id: {
                        "confirmado": r.confirmado,
                        "fecha_alta": r.fecha_alta,
                        "detectado_en": r.detectado_en,
                        "obs_verificado_en": r.obs_verificado_en,
                        "ean": r.ean,
                        "nombre": r.nombre,
                    }
                    for r in session.query(RowaNuevo).order_by(RowaNuevo.detectado_en.desc()).all()
                }
        except Exception:
            pass

        # Orden para operar: por laboratorio y, dentro de cada uno, alfabético.
        accion = sorted(
            [f for f in filas if f.al_deposito > 0],
            key=lambda f: (f.laboratorio or "~", (f.nombre_obs or f.nombre or "").lower()))
        vencimientos = sorted(
            [f for f in filas if f.dias_prox_venc is not None and f.packs_venc_alerta],
            key=lambda f: f.dias_prox_venc)
        n_nuevos = sum(1 for v in nuevos_map.values() if v["confirmado"])

        # La tabla mostraba SOLO `accion` (al_deposito > 0). Con el robot de
        # Badia eso es ~450 de ~3500 articulos: el 87% del stock se analizaba y
        # no se veia nunca, y desde la pantalla parecia que faltaban productos.
        #
        # `?todo=1` muestra el inventario completo. No se renderiza siempre
        # porque 3500 filas son unos 4 MB de HTML; el default sigue liviano y la
        # vista completa se pide cuando hace falta.
        ver_todo = request.args.get("todo") == "1"
        filas_orden = sorted(filas, key=lambda f: (f.nombre_obs or f.nombre or "").lower())
        tabla = filas_orden if ver_todo else accion

        # Los filtros son client-side sobre lo renderizado, asi que el combo de
        # laboratorios tiene que salir de la tabla que se muestra: si saliera de
        # `accion`, en la vista completa faltarian labs.
        labs_disponibles = sorted({f.laboratorio for f in tabla if f.laboratorio})
        return render_template(
            "rowa.html",
            sin_robot=False,
            robot=data["robot"], diag=data["diag"], alturas=data["alturas"],
            cruce=data["cruce"], generado=data["generado"],
            filas=filas_orden,
            accion=accion, vencimientos=vencimientos,
            n_accion=len(accion), n_venc=len(vencimientos),
            nuevos_map=nuevos_map, n_nuevos=n_nuevos,
            labs_disponibles=labs_disponibles,
            tabla=tabla, ver_todo=ver_todo,
            n_total=len(filas_orden),
            unid_total=sum(f.cantidad for f in filas_orden),
            unid_accion=sum(f.cantidad for f in accion),
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

    # ---- Snapshots y carga -----------------------------------------------

    def _tomar_snapshot(session, filas) -> datetime:
        """Guarda una foto del stock actual en rowa_snapshots. Devuelve el ts."""
        ahora = datetime.now()
        for f in filas:
            session.add(RowaSnapshot(
                tomado_en=ahora,
                article_id=f.article_id,
                cantidad=f.cantidad,
            ))
        session.commit()
        return ahora

    def _calcular_salidas_diarias(session, filas) -> dict[str, float]:
        """Devuelve {article_id: salidas_por_dia} usando snapshots + cargas.

        Toma los últimos 14 días de snapshots. El movimiento neto de un artículo
        entre dos snapshots es: (cant_anterior - cant_posterior) + cargas_entre_medio.
        Si no hay suficientes snapshots, usa unid_mes_est / 30 como fallback.
        """
        desde = datetime.now() - timedelta(days=14)

        # Snapshots ordenados por tiempo
        snaps_raw = (
            session.query(RowaSnapshot)
            .filter(RowaSnapshot.tomado_en >= desde)
            .order_by(RowaSnapshot.tomado_en)
            .all()
        )

        # Cargas en el mismo período (suman al stock → no son salidas)
        cargas_raw = (
            session.query(RowaCarga)
            .filter(RowaCarga.cargado_en >= desde)
            .all()
        )

        if not snaps_raw:
            # Sin snapshots: fallback a unid_mes_est / 30
            return {f.article_id: round((f.unid_mes_est or 0) / 30, 3) for f in filas}

        # Agrupar por ts → {ts: {article_id: cantidad}}
        from collections import defaultdict as _dd
        por_ts: dict = _dd(dict)
        for s in snaps_raw:
            por_ts[s.tomado_en][s.article_id] = s.cantidad

        ts_list = sorted(por_ts)
        if len(ts_list) < 2:
            return {f.article_id: round((f.unid_mes_est or 0) / 30, 3) for f in filas}

        # Cargas entre snapshots → {(article_id, ts_ini, ts_fin): total_cargado}
        cargas_por_aid: dict = _dd(float)
        for c in cargas_raw:
            cargas_por_aid[c.article_id] += c.cantidad

        # Salidas acumuladas por artículo en el período
        salidas_total: dict = _dd(float)
        primer_ts = ts_list[0]
        ultimo_ts = ts_list[-1]

        for aid in {s.article_id for s in snaps_raw}:
            q_ini = por_ts[primer_ts].get(aid)
            q_fin = por_ts[ultimo_ts].get(aid)
            if q_ini is None or q_fin is None:
                continue
            cargado = cargas_por_aid.get(aid, 0)
            salidas = (q_ini - q_fin) + cargado
            salidas_total[aid] = max(salidas, 0)

        dias = max((ultimo_ts - primer_ts).total_seconds() / 86400, 1)
        result: dict[str, float] = {}
        for f in filas:
            if f.article_id in salidas_total:
                result[f.article_id] = round(salidas_total[f.article_id] / dias, 3)
            else:
                result[f.article_id] = round((f.unid_mes_est or 0) / 30, 3)
        return result

    @app.route("/rowa/carga")
    @login_required
    def rowa_carga():
        try:
            data = _cargar(refresh=bool(request.args.get("refresh")))
        except (RowaError, OSError) as e:
            return render_template("rowa_carga.html", sin_robot=True, error=str(e))

        filas = data["filas"]

        with get_db() as session:
            # Auto-snapshot si el último tiene más de 1 hora
            ultimo_snap = (
                session.query(RowaSnapshot.tomado_en)
                .order_by(RowaSnapshot.tomado_en.desc())
                .first()
            )
            snap_ts = None
            if not ultimo_snap or (datetime.now() - ultimo_snap[0]).total_seconds() > 3600:
                snap_ts = _tomar_snapshot(session, filas)
            else:
                snap_ts = ultimo_snap[0]

            salidas_dia = _calcular_salidas_diarias(session, filas)

            # Eventos de aumento de stock por artículo (carga/parcial/ingreso sin
            # registrar), para el filtro "solo con aumentos" — una sola pasada
            # sobre todos los snapshots/cargas en vez de N queries por artículo.
            snaps_por_art: dict[str, list[tuple[datetime, int]]] = defaultdict(list)
            for aid, ts, cant in (
                session.query(RowaSnapshot.article_id, RowaSnapshot.tomado_en, RowaSnapshot.cantidad)
                .order_by(RowaSnapshot.tomado_en)
                .all()
            ):
                snaps_por_art[aid].append((ts, cant))
            cargas_por_art: dict[str, list[tuple[datetime, int]]] = defaultdict(list)
            for aid, ts, cant in (
                session.query(RowaCarga.article_id, RowaCarga.cargado_en, RowaCarga.cantidad)
                .order_by(RowaCarga.cargado_en)
                .all()
            ):
                cargas_por_art[aid].append((ts, cant))
            tipo_aumento_por_art = {
                aid: peor_tipo_evento(clasificar_eventos_stock(snaps, cargas_por_art.get(aid, [])))
                for aid, snaps in snaps_por_art.items()
                if len(snaps) >= 2
            }

            # Última sesión de carga
            ultima_carga = (
                session.query(RowaCarga.cargado_en)
                .order_by(RowaCarga.cargado_en.desc())
                .first()
            )

            # Historial de snapshots (últimos 10, agrupados por timestamp)
            from sqlalchemy import func as _func
            snap_historial = (
                session.query(
                    RowaSnapshot.tomado_en,
                    _func.count(RowaSnapshot.article_id).label("n_art"),
                )
                .group_by(RowaSnapshot.tomado_en)
                .order_by(RowaSnapshot.tomado_en.desc())
                .limit(10)
                .all()
            )

        # Construir lista de carga con cobertura
        items = []
        for f in filas:
            sal = salidas_dia.get(f.article_id, 0)
            if sal > 0:
                cobertura = round(f.cantidad / sal)
            elif f.cantidad == 0:
                cobertura = 0
            else:
                cobertura = 999  # sin salidas conocidas

            urgencia = 0 if f.cantidad == 0 else (1 if cobertura <= 3 else (2 if cobertura <= 7 else 3))
            items.append({
                "article_id": f.article_id,
                "ean": f.ean or "",
                "nombre": f.nombre_obs or f.nombre or "",
                "laboratorio": f.laboratorio or "",
                "cantidad": f.cantidad,
                "stock_deposito": f.stock_deposito,
                "stock_total": f.stock_total,
                "salidas_dia": sal,
                "cobertura": cobertura,
                "urgencia": urgencia,
                "sug_cargar": f.sug_en_robot - f.cantidad if f.sug_en_robot > f.cantidad else 0,
                "tipo_aumento": tipo_aumento_por_art.get(f.article_id),
            })

        items.sort(key=lambda x: (x["urgencia"], x["cobertura"]))
        labs_disponibles = sorted({i["laboratorio"] for i in items if i["laboratorio"]})

        return render_template(
            "rowa_carga.html",
            sin_robot=False,
            items=items,
            snap_ts=snap_ts,
            ultima_carga=ultima_carga[0] if ultima_carga else None,
            n_criticos=sum(1 for i in items if i["urgencia"] < 2),
            generado=data["generado"],
            snap_historial=snap_historial,
            labs_disponibles=labs_disponibles,
        )

    @app.route("/rowa/carga/registrar", methods=["POST"])
    @login_required
    def rowa_carga_registrar():
        payload = request.get_json(silent=True) or {}
        items = payload.get("items") or []
        if not items:
            return jsonify({"ok": False, "error": "Sin artículos"}), 400

        sid = str(uuid.uuid4())
        usuario = getattr(request, "current_user", None)
        usuario_str = getattr(usuario, "username", None) if usuario else None

        with get_db() as session:
            for it in items:
                try:
                    cant = int(it.get("cantidad") or 0)
                except (TypeError, ValueError):
                    cant = 0
                if cant <= 0:
                    continue
                session.add(RowaCarga(
                    sesion_id=sid,
                    article_id=str(it.get("article_id") or ""),
                    ean=it.get("ean") or None,
                    nombre=it.get("nombre") or None,
                    cantidad=cant,
                    usuario=usuario_str,
                ))
            session.commit()

        _CACHE["payload"] = None  # forzar recarga del robot en el próximo acceso
        return jsonify({"ok": True, "sesion_id": sid})

    @app.route("/rowa/api/producto/<article_id>/historial-stock")
    @login_required
    def rowa_historial_stock(article_id):
        """Serie de stock del robot por snapshot + eventos de aumento detectados,
        clasificados según si coinciden con una carga registrada en `/carga`
        (RowaCarga) o no (ingreso sin registrar — el gap que rompe salidas/día)."""
        with get_db() as session:
            snaps = (
                session.query(RowaSnapshot)
                .filter(RowaSnapshot.article_id == article_id)
                .order_by(RowaSnapshot.tomado_en)
                .all()
            )
            cargas = (
                session.query(RowaCarga)
                .filter(RowaCarga.article_id == article_id)
                .order_by(RowaCarga.cargado_en)
                .all()
            )

        if not snaps:
            return jsonify({"ok": False, "error": "Sin snapshots para este artículo todavía."})

        puntos = [{"ts": s.tomado_en.isoformat(), "stock": s.cantidad} for s in snaps]
        eventos = clasificar_eventos_stock(
            [(s.tomado_en, s.cantidad) for s in snaps],
            [(c.cargado_en, c.cantidad) for c in cargas],
        )
        for e in eventos:
            e["ts"] = e["ts"].isoformat()

        return jsonify({"ok": True, "article_id": article_id, "puntos": puntos, "eventos": eventos})

    @app.route("/rowa/analisis")
    @login_required
    def rowa_analisis():
        try:
            data = _cargar(refresh=bool(request.args.get("refresh")))
        except (RowaError, OSError) as e:
            return render_template("rowa_analisis.html", sin_robot=True, error=str(e))

        filas = data["filas"]
        try:
            with get_db() as session:
                salidas_dia = _calcular_salidas_diarias(session, filas)
        except Exception:
            salidas_dia = {f.article_id: round((f.unid_mes_est or 0) / 30, 3) for f in filas}

        COBERTURA_UMBRAL = 7

        donut_vols: dict[str, float] = {"Alta": 0.0, "Media": 0.0, "Baja": 0.0, "Durmiente": 0.0}
        q_items: dict[str, list] = {"sobrestock": [], "bien_cargado": [], "ajustado": [], "riesgo": []}

        for f in filas:
            donut_vols[f.rotacion] = donut_vols.get(f.rotacion, 0.0) + f.vol_total_cm3
            sal = salidas_dia.get(f.article_id, 0)
            cob = f.cantidad / sal if sal > 0 else (0.0 if f.cantidad == 0 else 999.0)
            alta_venta = f.rotacion in ("Alta", "Media")
            if alta_venta:
                if f.cantidad == 0 or cob <= COBERTURA_UMBRAL:
                    q_items["riesgo"].append((f, cob))
                else:
                    q_items["bien_cargado"].append((f, cob))
            else:
                if f.al_deposito > 0:
                    q_items["sobrestock"].append((f, cob))
                else:
                    q_items["ajustado"].append((f, cob))

        donut_data = [
            {"label": "Alta",      "vol_l": round(donut_vols["Alta"] / 1000, 1),      "color": "#10b981"},
            {"label": "Media",     "vol_l": round(donut_vols["Media"] / 1000, 1),     "color": "#38bdf8"},
            {"label": "Baja",      "vol_l": round(donut_vols["Baja"] / 1000, 1),      "color": "#fbbf24"},
            {"label": "Durmiente", "vol_l": round(donut_vols["Durmiente"] / 1000, 1), "color": "#9ca3af"},
        ]

        def _pts(pairs):
            return [
                {
                    "x": round(f.unid_mes_est or 0, 1),
                    "y": round(min(cob, 60), 1),
                    "nombre": (f.nombre_obs or f.nombre or "")[:45],
                    "rotacion": f.rotacion,
                    "vol": round(f.vol_total_cm3),
                    "aid": f.article_id,
                    "cob": round(cob, 1) if cob < 900 else None,
                    "nuevo": f.es_nuevo,
                    "laboratorio": f.laboratorio or "",
                    "stock_deposito": f.stock_deposito,
                    "stock_total": f.stock_total,
                }
                for f, cob in pairs
            ]

        scatter_all = {q: _pts(pairs) for q, pairs in q_items.items()}
        q_stats = {
            q: {
                "n": len(pairs),
                "vol_l": round(sum(f.vol_total_cm3 for f, _ in pairs) / 1000, 1),
            }
            for q, pairs in q_items.items()
        }
        labs_disponibles = sorted({f.laboratorio for f in filas if f.laboratorio})

        return render_template(
            "rowa_analisis.html",
            sin_robot=False,
            diag=data["diag"],
            generado=data["generado"],
            donut_data=donut_data,
            q_stats=q_stats,
            scatter_all=scatter_all,
            cobertura_umbral=COBERTURA_UMBRAL,
            labs_disponibles=labs_disponibles,
        )

    @app.route("/rowa/snapshot/auto")
    def rowa_snapshot_auto():
        """Endpoint para cron externo: toma un snapshot del robot si el último
        tiene más de 50 minutos. Sin login_required — acceso solo desde LAN."""
        import os
        token = os.environ.get("ROWA_SNAPSHOT_TOKEN", "")
        if token and request.args.get("token") != token:
            return jsonify({"ok": False, "error": "token inválido"}), 403
        try:
            data = _cargar()
        except (RowaError, OSError) as e:
            return jsonify({"ok": False, "error": f"robot no disponible: {e}"}), 502

        with get_db() as session:
            ultimo = (
                session.query(RowaSnapshot.tomado_en)
                .order_by(RowaSnapshot.tomado_en.desc())
                .first()
            )
            ahora = datetime.now()
            if ultimo and (ahora - ultimo[0]).total_seconds() < 14400:  # 4 hs
                return jsonify({"ok": True, "skipped": True,
                                "ultimo": ultimo[0].isoformat()})
            ts = _tomar_snapshot(session, data["filas"])

        return jsonify({"ok": True, "skipped": False, "ts": ts.isoformat(),
                        "articulos": len(data["filas"])})
