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
    clasificar_carga,
    clasificar_eventos_stock,
    clean_ean,
    diagnosticar,
    peor_tipo_evento,
)
from services.rowa_client import RowaClient, RowaError
from services.rowa_observer import cruzar_con_observer

# Días de venta que se busca cubrir al cargar el robot, desde /rowa/carga.
# Es 3 y no los 7 de la planilla a propósito: la recarga es diaria, y apuntar
# más lejos llena la máquina de producto que no va a salir y le saca lugar al
# que sí. El operador lo ajusta desde el encabezado.
DIAS_CARGA_DEFAULT = 3

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
        with get_db() as session:
            syncs = _frescura_syncs(session)

        return render_template(
            "rowa.html",
            syncs=syncs,
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

    # Cuanto puede envejecer cada sync antes de que el numero deje de servir.
    # El stock es el critico: el deposito se calcula como stock_total - robot, asi
    # que con datos viejos la planilla manda a buscar mercaderia que ya se vendio.
    # La capacidad, en cambio, cambia cada muchos meses.
    FRESCURA_MIN = {"stock": 60, "rowa_productos": 24 * 60, "ventas_mensuales": 24 * 60}

    def _frescura_syncs(session):
        """Edad de cada sync que alimenta el modulo. Devuelve lista de dicts."""
        from database import ObsSyncLog, now_ar
        out = []
        for entidad, tope in FRESCURA_MIN.items():
            ult = (session.query(ObsSyncLog)
                   .filter(ObsSyncLog.entidad == entidad, ObsSyncLog.error.is_(None))
                   .order_by(ObsSyncLog.ejecutado_en.desc()).first())
            minutos = (int((now_ar() - ult.ejecutado_en).total_seconds() // 60)
                       if ult and ult.ejecutado_en else None)
            out.append({
                "entidad": entidad,
                "minutos": minutos,
                "nunca": ult is None,
                # None (nunca corrido) cuenta como viejo: sin ese dato no hay
                # planilla posible, y callarlo seria peor que avisarlo.
                "viejo": minutos is None or minutos > tope,
                "tope_min": tope,
            })
        return out

    def _salidas_para_planilla(session, filas):
        """Salidas diarias por producto_observer, de las dos fuentes.

        Devuelve (mapa, dias_ventana). El mapa trae (snapshot, observer) sin
        mezclar: la mezcla y su ponderación viven en `services/rowa_planilla`,
        que es lógica pura y se testea sola.
        """
        desde = datetime.now() - timedelta(days=14)
        snaps = (session.query(RowaSnapshot)
                 .filter(RowaSnapshot.tomado_en >= desde)
                 .order_by(RowaSnapshot.tomado_en).all())
        serie = defaultdict(list)
        for x in snaps:
            serie[x.article_id].append((x.tomado_en, x.cantidad))
        ts = sorted({x.tomado_en for x in snaps})
        dias_ventana = (max((ts[-1] - ts[0]).total_seconds() / 86400, 1)
                        if len(ts) >= 2 else 0.0)

        out = {}
        for f in filas:
            if not f.producto_observer:
                continue
            sr = serie.get(f.article_id, [])
            # Bajas entre snapshots consecutivos, igual que
            # `_calcular_salidas_diarias`: comparar extremos subestima 3,4x.
            snap = (sum(max(p - q, 0) for (_, p), (_, q) in zip(sr, sr[1:])) / dias_ventana
                    if len(sr) >= 2 and dias_ventana else None)
            v = f.ventas_arr or []
            # u3m/día: los 3 meses completos recientes, sin el mes en curso que
            # está a medio andar.
            obs = (sum(v[-4:-1]) / (3 * 30.42)) if len(v) >= 12 else None
            out[f.producto_observer] = (snap, obs, f.cantidad, f.nombre_obs or f.nombre)
        return out, dias_ventana

    @app.route("/rowa/planilla/export.<fmt>")
    @login_required
    def rowa_planilla_export(fmt):
        """Baja la planilla en PDF (para caminar el depósito) o XLSX.

        Acepta los mismos parámetros que la pantalla —`dias` y los filtros— para
        que el papel coincida con lo que el operador está mirando. Los filtros de
        la pantalla son client-side, así que el botón los reenvía.
        """
        if fmt not in ("pdf", "xlsx"):
            abort(404)
        try:
            planilla, _labs, generado, _syncs, sin_sync = _armar_planilla(
                request.args.get("dias", type=int))
        except (RowaError, OSError) as e:
            abort(503, description=str(e))
        if sin_sync:
            abort(409, description="falta correr el sync rowa_productos")

        filas = planilla["filas"]
        q = (request.args.get("q") or "").strip().lower()
        lab = (request.args.get("lab") or "").strip()
        aviso = (request.args.get("aviso") or "").strip()
        if q:
            filas = [f for f in filas if q in (f["nombre"] or "").lower()]
        if lab:
            filas = [f for f in filas if f["laboratorio"] == lab]
        if aviso == "vacio":
            filas = [f for f in filas if f["vacio"]]
        elif aviso == "parcial":
            filas = [f for f in filas if f["parcial"]]
        elif aviso == "corregir":
            filas = [f for f in filas if f["corregir_a"]]
        elif aviso == "diferencia":
            filas = [f for f in filas if f["diferencia"]]

        # Los totales se recalculan sobre lo filtrado: si el papel muestra un
        # laboratorio, el encabezado tiene que decir los packs de ESE laboratorio.
        totales = dict(planilla["totales"])
        totales["articulos"] = len(filas)
        totales["packs_sug"] = sum(f["a_mover_sug"] for f in filas)
        totales["packs_max"] = sum(f["a_mover_max"] for f in filas)

        from services.rowa_planilla_export import construir_pdf, construir_xlsx
        if fmt == "pdf":
            contenido = construir_pdf(filas, totales, generado)
            mime = "application/pdf"
        else:
            contenido = construir_xlsx(filas, totales, generado)
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        nombre = "Planilla-carga-%s.%s" % (generado.strftime("%Y-%m-%d"), fmt)
        return Response(contenido, mimetype=mime,
                        headers={"Content-Disposition": 'attachment; filename="%s"' % nombre})

    def _armar_planilla(dias_pedidos=None, refresh=False):
        """Junta la entrada y arma la planilla. La usan la pantalla y las descargas.

        Se arma desde ObServer y no desde el robot: `stock_info()` sólo devuelve
        artículos que tienen packs adentro, así que partir de ahí pierde el caso
        más fuerte — robot en CERO con mercadería esperando en el depósito.

        La lógica vive en `services/rowa_planilla`; acá sólo se junta la entrada.
        Devuelve (planilla, labs_disponibles, generado, syncs, sin_sync) y deja
        que `RowaError` / `OSError` suban: cada llamador decide qué mostrar.
        """
        from sqlalchemy import func as _f

        from database import (
            ObsLaboratorio,
            ObsProducto,
            ObsRowaProducto,
            ObsStock,
        )
        from services.rowa_planilla import DIAS_AUTONOMIA, construir_planilla

        dias = dias_pedidos or DIAS_AUTONOMIA
        dias = max(1, min(dias, 60))
        data = _cargar(refresh=refresh)
        filas = data["filas"]
        with get_db() as session:
            salidas, dias_ventana = _salidas_para_planilla(session, filas)

            maximos = dict(session.query(ObsRowaProducto.producto_observer,
                                          ObsRowaProducto.cantidad_maxima).all())
            stock_obs = {r.pid: int(r.stock or 0) for r in
                         session.query(ObsStock.producto_observer.label("pid"),
                                        _f.sum(ObsStock.stock_actual).label("stock"))
                         .group_by(ObsStock.producto_observer).all()}
            prods = {p.observer_id: (p.descripcion, p.laboratorio_observer)
                     for p in session.query(ObsProducto)
                     .filter(ObsProducto.fecha_baja.is_(None)).all()}
            labs = dict(session.query(ObsLaboratorio.observer_id,
                                       ObsLaboratorio.descripcion).all())

        articulos = []
        for pid, cmax in maximos.items():
            if pid not in prods:
                continue
            snap, obs, en_robot, nombre = salidas.get(pid, (None, None, 0, None))
            desc, lab_id = prods[pid]
            articulos.append({
                "producto_observer": pid,
                "nombre": nombre or desc,
                "laboratorio": labs.get(lab_id) or "",
                "ean": "",
                "en_robot": en_robot,
                "maximo": cmax,
                "stock_total": stock_obs.get(pid),
                "salidas_snapshot": snap,
                "salidas_observer": obs,
            })

        planilla = construir_planilla(articulos, dias_ventana, dias)
        labs_disponibles = sorted({f["laboratorio"] for f in planilla["filas"]
                                   if f["laboratorio"]})
        with get_db() as session:
            syncs = _frescura_syncs(session)

        # Sin el sync corrido no hay maximos y la planilla sale vacia: se avisa
        # en vez de mostrar una pantalla en blanco sin explicacion.
        return planilla, labs_disponibles, data["generado"], syncs, not maximos

    @app.route("/rowa/planilla")
    @login_required
    def rowa_planilla():
        """Pantalla de la planilla de carga."""
        try:
            planilla, labs, generado, syncs, sin_sync = _armar_planilla(
                request.args.get("dias", type=int),
                refresh=bool(request.args.get("refresh")))
        except (RowaError, OSError) as e:
            return render_template("rowa_planilla.html", sin_robot=True, error=str(e))
        return render_template(
            "rowa_planilla.html",
            syncs=syncs, sin_robot=False, generado=generado,
            filas=planilla["filas"], totales=planilla["totales"],
            labs_disponibles=labs, sin_sync=sin_sync,
        )

    @app.route("/rowa/diferencias")
    @login_required
    def rowa_diferencias():
        """Articulos donde el robot tiene MAS packs que el stock total de ObServer.

        No deberia pasar nunca: lo que esta en el robot es parte del stock total,
        asi que robot <= total siempre. Cuando no se cumple, o se cargo algo al
        robot sin registrarlo en ObServer, o se descargo de ObServer sin sacarlo
        de la maquina.

        Medido el 24/8/2026: 24 articulos y 34 packs sobre 3.456 comparables
        (0,7%). La mayoria son diferencias de 1 pack, que es el desfasaje normal
        entre la venta y la extraccion fisica. Los que importan son dos grupos:

          - ObServer en CERO con stock en el robot: para ObServer no existen, asi
            que no se reponen ni se ofrecen en el mostrador, pero ocupan lugar.
          - Diferencias grandes: no son desfasaje, son error de carga.
        """
        try:
            data = _cargar(refresh=bool(request.args.get("refresh")))
        except (RowaError, OSError) as e:
            return render_template("rowa_diferencias.html", sin_robot=True, error=str(e))

        filas = data["filas"]
        comparables = [f for f in filas
                       if f.producto_observer and f.stock_total is not None]
        difs = []
        for f in comparables:
            exceso = f.cantidad - f.stock_total
            if exceso > 0:
                difs.append({
                    "article_id": f.article_id,
                    "producto_observer": f.producto_observer,
                    "nombre": f.nombre_obs or f.nombre or "?",
                    "laboratorio": f.laboratorio or "",
                    "ean": f.ean or "",
                    "en_robot": f.cantidad,
                    "en_observer": f.stock_total,
                    "exceso": exceso,
                    "observer_en_cero": f.stock_total == 0,
                })
        # Primero los que ObServer da en cero (los que se pierden del circuito),
        # despues por tamano de la diferencia.
        difs.sort(key=lambda d: (not d["observer_en_cero"], -d["exceso"]))

        return render_template(
            "rowa_diferencias.html",
            sin_robot=False,
            generado=data["generado"],
            difs=difs,
            n_comparables=len(comparables),
            n_articulos=len(filas),
            packs_dif=sum(d["exceso"] for d in difs),
            n_cero=sum(1 for d in difs if d["observer_en_cero"]),
        )

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
        """Devuelve {article_id: salidas_por_dia} a partir de los snapshots.

        Suma las BAJAS entre snapshots consecutivos. Antes comparaba solo el
        primero contra el ultimo y le sumaba las cargas registradas, y eso
        subestimaba fuerte: un articulo que bajo de 10 a 2 y se repuso a 10
        daba cero salidas. Medido contra el robot de Badia (24/8/2026, 26
        snapshots en ~4 dias): por extremos 427 unidades, sumando bajas 1.469.

        Ademas ya no depende de que alguien registre las cargas. En esos mismos
        14 dias habia **cero** `RowaCarga` cargadas, asi que el termino que las
        compensaba nunca sumaba nada y toda reposicion quedaba invisible. Con
        este metodo los aumentos simplemente no se cuentan como salida, que es
        lo correcto los registre alguien o no.

        Sin al menos dos snapshots del articulo, cae al proxy unid_mes_est / 30.
        """
        desde = datetime.now() - timedelta(days=14)

        snaps_raw = (
            session.query(RowaSnapshot)
            .filter(RowaSnapshot.tomado_en >= desde)
            .order_by(RowaSnapshot.tomado_en)
            .all()
        )
        if not snaps_raw:
            return {f.article_id: round((f.unid_mes_est or 0) / 30, 3) for f in filas}

        # {article_id: [(ts, cantidad), ...]} ya ordenado por la query.
        serie_por_art: dict[str, list[tuple[datetime, int]]] = defaultdict(list)
        for s in snaps_raw:
            serie_por_art[s.article_id].append((s.tomado_en, s.cantidad))

        ts_todos = sorted({s.tomado_en for s in snaps_raw})
        if len(ts_todos) < 2:
            return {f.article_id: round((f.unid_mes_est or 0) / 30, 3) for f in filas}
        dias = max((ts_todos[-1] - ts_todos[0]).total_seconds() / 86400, 1)

        result: dict[str, float] = {}
        for f in filas:
            serie = serie_por_art.get(f.article_id, [])
            if len(serie) < 2:
                result[f.article_id] = round((f.unid_mes_est or 0) / 30, 3)
                continue
            bajas = sum(max(prev - cur, 0)
                        for (_, prev), (_, cur) in zip(serie, serie[1:]))
            result[f.article_id] = round(bajas / dias, 3)
        return result

    def _sugerir_carga(en_robot, deposito, maximo, salidas, dias=None):
        """Cuántos packs conviene mover del depósito al robot.

        Misma fórmula que `/rowa/planilla` (`services/rowa_planilla.calcular_fila`):
        objetivo por demanda acotado por el cupo de ObServer y por lo que hay en
        el depósito. Antes esta pantalla usaba `sug_en_robot - cantidad`, que era
        **siempre 0**: `_recomendar()` sólo sabe reducir y nunca fija un objetivo
        mayor al stock actual, así que la columna que existe para decidir qué
        reponer no podía responder su propia pregunta.

        Sin cupo cargado no se opina: no hay objetivo contra el cual comparar.
        Sin señal de ventas tampoco se inventa demanda — se respeta el máximo,
        que es una decisión humana.
        """
        import math

        from services.rowa_planilla import DIAS_AUTONOMIA

        dias = dias or DIAS_AUTONOMIA
        disponible = max(deposito or 0, 0)
        if not maximo or maximo <= 0 or disponible <= 0:
            return 0
        hueco = max(0, maximo - (en_robot or 0))
        if hueco <= 0:
            return 0
        if salidas and salidas > 0:
            objetivo = max(1, min(math.ceil(salidas * dias), maximo))
            return min(max(0, objetivo - (en_robot or 0)), disponible)
        return min(hueco, disponible)

    def _construir_items(session, filas, dias_autonomia=None):
        """Arma la lista de carga (cobertura, urgencia, sugerido) para /rowa/carga.

        Vive aparte porque lo usan la pantalla y las exportaciones, y porque la
        exportacion NO debe tomar snapshot: eso es efecto de abrir la pantalla, no
        de bajarse un PDF.

        Orden: por laboratorio y, dentro de cada uno, alfabetico por producto. Antes
        ordenaba por urgencia y cobertura, pero para ir a buscar la mercaderia al
        deposito conviene recorrer un laboratorio a la vez. La urgencia sigue estando
        en cada fila (y pintada en la pantalla), asi que no se pierde.
        """
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

        # Cupo por artículo (Varios.Rowa_Productos). Es la única fuente de
        # "cuántos packs entran": el robot no lo expone (ver ObsRowaProducto).
        from database import ObsRowaProducto

        maximos = dict(session.query(ObsRowaProducto.producto_observer,
                                     ObsRowaProducto.cantidad_maxima).all())

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
                "maximo": maximos.get(f.producto_observer),
                "sug_cargar": _sugerir_carga(
                    en_robot=f.cantidad,
                    deposito=f.stock_deposito,
                    maximo=maximos.get(f.producto_observer),
                    salidas=sal,
                    dias=dias_autonomia),
                "tipo_aumento": tipo_aumento_por_art.get(f.article_id),
            })

        # "~" para que los sin laboratorio queden al final y no encabecen la lista.
        items.sort(key=lambda x: ((x["laboratorio"] or "~").lower(), x["nombre"].lower()))
        return items

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

            # Para cuántos días de venta se quiere llenar el robot. Default 3:
            # la recarga es diaria, así que apuntar muy lejos llena el robot de
            # producto que no va a salir y le saca lugar al que sí. Se ajusta
            # desde el encabezado.
            dias = request.args.get("dias", type=int) or DIAS_CARGA_DEFAULT
            dias = max(1, min(dias, 60))
            items = _construir_items(session, filas, dias_autonomia=dias)

            # Última sesión de carga
            ultima_carga = (
                session.query(RowaCarga.cargado_en)
                .order_by(RowaCarga.cargado_en.desc())
                .first()
            )

            # Estado de las cargas recientes contra el robot. Lo que importa es
            # `no_detectada`: mercadería que la app da por cargada y el robot
            # nunca tomó (pasó el 25/8/2026 con 23 packs).
            _desde = datetime.now() - timedelta(hours=48)
            cargas_recientes = (session.query(RowaCarga)
                                .filter(RowaCarga.cargado_en >= _desde)
                                .order_by(RowaCarga.cargado_en.desc()).all())
            verif = {'confirmada': 0, 'parcial': 0, 'no_detectada': 0, 'pendiente': 0}
            for _c in cargas_recientes:
                verif[_c.estado or 'pendiente'] = verif.get(_c.estado or 'pendiente', 0) + 1
            verif['total'] = len(cargas_recientes)
            verif['packs'] = sum(_c.cantidad or 0 for _c in cargas_recientes)
            verif['no_detectada_packs'] = sum(
                _c.cantidad or 0 for _c in cargas_recientes
                if (_c.estado or 'pendiente') == 'no_detectada')

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

        # Se renderizaban los ~3.500 items siempre, con el checkbox "Solo
        # criticos" tildado escondiendo el 96% del lado del navegador: 4 MB de
        # HTML por visita para mostrar 222 renglones. Ahora el corte lo hace el
        # servidor y `?todo=1` trae el resto.
        ver_todo = request.args.get("todo") == "1"
        # Sin sugerencia no hay nada que hacer con esa fila: mostrarla es pedirle
        # al operador que descarte a mano lo que no puede cargar. El filtro
        # subsume al de "sin stock en depósito" (sin depósito la sugerencia ya da
        # cero) y además saca los que no tienen cupo cargado en ObServer.
        # `?sugeridos=0` las trae igual, para diagnosticar por qué un crítico no
        # aparece.
        solo_sugeridos = request.args.get("sugeridos", "1") != "0"
        base = [i for i in items if i["sug_cargar"] > 0] if solo_sugeridos else items
        tabla = base if ver_todo else [i for i in base if i["urgencia"] < 3]

        # Desglose de por qué se ocultó cada una: "sin cupo" es accionable
        # (hay que cargar la Cantidad Máxima en ObServer), "sin depósito" no.
        ocultos = [i for i in items if i["sug_cargar"] <= 0] if solo_sugeridos else []
        n_sin_deposito = sum(1 for i in ocultos if (i["stock_deposito"] or 0) <= 0)
        n_sin_cupo = sum(1 for i in ocultos
                         if (i["stock_deposito"] or 0) > 0 and not i.get("maximo"))

        # Del conjunto renderizado: los filtros client-side no pueden ofrecer un
        # laboratorio que no esta en la tabla.
        labs_disponibles = sorted({i["laboratorio"] for i in tabla if i["laboratorio"]})

        return render_template(
            "rowa_carga.html",
            sin_robot=False,
            items=tabla,
            ver_todo=ver_todo,
            n_total=len(items),
            n_tabla=len(tabla),
            n_ocultos=len(ocultos),
            n_sin_deposito=n_sin_deposito,
            n_sin_cupo=n_sin_cupo,
            solo_sugeridos=solo_sugeridos,
            dias_autonomia=dias,
            snap_ts=snap_ts,
            ultima_carga=ultima_carga[0] if ultima_carga else None,
            verif=verif,
            n_criticos=sum(1 for i in items if i["urgencia"] < 2),
            generado=data["generado"],
            snap_historial=snap_historial,
            labs_disponibles=labs_disponibles,
        )

    @app.route("/rowa/carga/export.<fmt>")
    @login_required
    def rowa_carga_export(fmt):
        """Baja la lista de carga en XLSX o PDF, agrupada por laboratorio.

        Aplica los mismos filtros que la pantalla (buscador, laboratorio, solo
        criticos), que viajan por querystring: los de la pantalla son
        client-side, asi que el boton los reenvia para que el archivo coincida
        con lo que el operador esta viendo.

        No toma snapshot a proposito: eso es efecto de abrir /rowa/carga, no de
        bajarse un archivo.
        """
        if fmt not in ("xlsx", "pdf"):
            abort(404)

        try:
            data = _cargar()
        except (RowaError, OSError) as e:
            abort(503, description=str(e))

        dias = request.args.get("dias", type=int) or DIAS_CARGA_DEFAULT
        dias = max(1, min(dias, 60))
        with get_db() as session:
            items = _construir_items(session, data["filas"], dias_autonomia=dias)

        # Mismo criterio que la pantalla: sin sugerencia no va al archivo.
        if request.args.get("sugeridos", "1") != "0":
            items = [i for i in items if i["sug_cargar"] > 0]

        q = (request.args.get("q") or "").strip().lower()
        lab = (request.args.get("lab") or "").strip()
        if q:
            items = [i for i in items if q in i["nombre"].lower()]
        if lab:
            items = [i for i in items if i["laboratorio"] == lab]
        if request.args.get("criticos") == "1":
            # < 3, igual que el filtro de la pantalla (cargaFiltrar). El KPI
            # `n_criticos` de la ruta usa < 2, que es otra cosa: acá lo que
            # importa es que el archivo coincida con lo que el operador ve.
            items = [i for i in items if i["urgencia"] < 3]

        from services.rowa_carga_export import construir_pdf, construir_xlsx
        generado = data["generado"]
        sello = f"{generado:%Y-%m-%d}"

        if fmt == "xlsx":
            contenido = construir_xlsx(items, generado)
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            contenido = construir_pdf(items, generado)
            mime = "application/pdf"

        nombre = f"Carga-robot-{sello}.{fmt}"
        return Response(contenido, mimetype=mime,
                        headers={"Content-Disposition": f'attachment; filename="{nombre}"'})

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

        # Foto del stock ANTES de la carga, para poder verificarla después. Se
        # lee del robot y no del último snapshot: entre snapshot y registro
        # pueden pasar horas, y ahí el "antes" ya no es el antes.
        stock_antes = {}
        try:
            for f in _cargar()["filas"]:
                stock_antes[str(f.article_id)] = f.cantidad
        except (RowaError, OSError):
            pass   # sin robot se registra igual; queda 'pendiente' de verificar

        with get_db() as session:
            for it in items:
                try:
                    cant = int(it.get("cantidad") or 0)
                except (TypeError, ValueError):
                    cant = 0
                if cant <= 0:
                    continue
                aid = str(it.get("article_id") or "")
                session.add(RowaCarga(
                    sesion_id=sid,
                    article_id=aid,
                    ean=it.get("ean") or None,
                    nombre=it.get("nombre") or None,
                    cantidad=cant,
                    usuario=usuario_str,
                    stock_antes=stock_antes.get(aid),
                    estado="pendiente",
                ))
            session.commit()

        _CACHE["payload"] = None  # forzar recarga del robot en el próximo acceso
        return jsonify({"ok": True, "sesion_id": sid})

    def _stock_antes_de(session, article_id, momento):
        """Stock del artículo en el último snapshot PREVIO a `momento`.

        Recupera el "antes" de las cargas que se registraron cuando el campo no
        existía todavía. Devuelve None si no hay ningún snapshot anterior, y ahí
        la carga queda 'pendiente' de verdad: no hay contra qué comparar.
        """
        fila = (session.query(RowaSnapshot.cantidad)
                .filter(RowaSnapshot.article_id == article_id,
                        RowaSnapshot.tomado_en < momento)
                .order_by(RowaSnapshot.tomado_en.desc())
                .first())
        return fila[0] if fila else None

    @app.route("/rowa/carga/verificar", methods=["POST"])
    @login_required
    def rowa_carga_verificar():
        """Compara las cargas registradas contra el stock real del robot.

        Relee el robot (no usa el último snapshot: puede tener horas) y marca
        cada carga como confirmada / parcial / no_detectada. Deja el snapshot
        tomado, así el gráfico también refleja el momento de la verificación.

        Sirve para el caso que lo motivó: el 25/8/2026 se registraron 23 packs
        que el robot nunca tomó, y la app los daba por cargados.
        """
        horas = min(request.args.get("horas", type=int) or 24, 24 * 7)
        try:
            data = _cargar(refresh=True)
        except (RowaError, OSError) as e:
            return jsonify({"ok": False, "error": f"robot no disponible: {e}"}), 502

        filas = data["filas"]
        stock_ahora = {str(f.article_id): f.cantidad for f in filas}
        desde = datetime.now() - timedelta(hours=horas)

        resumen = {"confirmada": 0, "parcial": 0, "no_detectada": 0, "pendiente": 0}
        detalle = []
        with get_db() as session:
            _tomar_snapshot(session, filas)
            cargas = (session.query(RowaCarga)
                      .filter(RowaCarga.cargado_en >= desde)
                      .order_by(RowaCarga.cargado_en.desc()).all())
            for c in cargas:
                despues = stock_ahora.get(c.article_id)
                antes = c.stock_antes
                if antes is None:
                    # Carga registrada antes de que existiera `stock_antes`, o
                    # con el robot caído. El "antes" igual existe: es el último
                    # snapshot PREVIO a la carga. Sin esto, esas cargas quedaban
                    # 'pendiente' para siempre y el botón no las resolvía nunca.
                    antes = _stock_antes_de(session, c.article_id, c.cargado_en)
                    if antes is not None:
                        c.stock_antes = antes
                estado = clasificar_carga(c.cantidad, antes, despues)
                c.stock_despues = despues
                c.verificado_en = datetime.now()
                c.estado = estado
                resumen[estado] = resumen.get(estado, 0) + 1
                detalle.append({
                    "article_id": c.article_id,
                    "nombre": c.nombre or "",
                    "cantidad": c.cantidad,
                    "stock_antes": c.stock_antes,
                    "stock_despues": despues,
                    "estado": estado,
                })
            session.commit()

        return jsonify({"ok": True, "resumen": resumen, "detalle": detalle,
                        "cargas": len(detalle)})

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
                    # Las tres se muestran juntas en la tabla, igual que en la
                    # planilla de carga: robot + depósito = total. `cantidad` es
                    # lo que el robot reporta adentro; `stock_total` es el stock
                    # de ObServer (robot + depósito) y la resta da el depósito.
                    "robot": f.cantidad,
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
