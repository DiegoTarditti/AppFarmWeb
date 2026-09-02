"""Ciclo de compra Kellerhoff: scraping del portal → InvoiceItems → liga PedidoEmitido."""
from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timedelta

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import text as _text
from werkzeug.utils import secure_filename

import database
from database import (
    FacturaFaltante,
    Invoice,
    InvoiceItem,
    PagoAjusteCC,
    PedidoEmitido,
    ResumenProveedor,
    ResumenProveedorItem,
    get_db,
)
from services.cuenta_corriente import (
    corte_resumenes,
    movimientos_proveedor,
    normalizar_cuit,
)
from services.kellerhoff_analizador import resolver_anunciante

KELLERHOFF_PROVIDER_ID = int(os.environ.get('KELLERHOFF_PROVIDER_ID', '1'))
KELLERHOFF_CUIT = '30539756490'

# ── Lock del sync en DB (id=2 en sync_lock) ──────────────────────────────────
# Antes: threading.Lock + dict en memoria. Con gunicorn --workers 2 eso es una
# copia POR worker: el polling caía en el worker que no corría el sync y no veía
# el log, y un segundo click en otro worker arrancaba un SEGUNDO scraping. El
# lock en DB lo comparten los dos workers (mismo patrón que ObServer, id=1).
_KH_LOCK_ID = 2
_KH_LOCK_TIMEOUT_MIN = 60   # lock abandonado (worker muerto mid-sync) → se puede tomar

# Buffer del log SOLO en el worker que corre el sync (el que tiene el lock). Se
# vuelca entero a sync_lock.log en cada línea, así el otro worker lo lee de DB.
_kh_log_buffer: list[str] = []


def _kh_lock_acquire() -> bool:
    """Toma el lock por UPDATE atómico. True si lo tomó; False si ya está tomado
    por otro worker dentro del timeout."""
    with get_db() as session:
        if session.query(database.SyncLock).filter_by(id=_KH_LOCK_ID).first() is None:
            session.add(database.SyncLock(id=_KH_LOCK_ID, en_curso=False))
            session.commit()
        umbral = datetime.now() - timedelta(minutes=_KH_LOCK_TIMEOUT_MIN)
        result = session.execute(_text(
            "UPDATE sync_lock SET en_curso = :on, iniciado_en = :now, "
            "finalizado_en = NULL, ultimo_resultado = NULL, log = NULL "
            "WHERE id = :lid AND (en_curso = :off OR iniciado_en IS NULL OR iniciado_en < :umbral)"
        ), {'on': True, 'off': False, 'now': datetime.now(), 'umbral': umbral,
            'lid': _KH_LOCK_ID})
        session.commit()
        return result.rowcount == 1


def _kh_lock_release(resultado=None) -> None:
    with get_db() as session:
        session.execute(_text(
            "UPDATE sync_lock SET en_curso = :off, finalizado_en = :now, "
            "ultimo_resultado = :res WHERE id = :lid"
        ), {'off': False, 'now': datetime.now(), 'lid': _KH_LOCK_ID,
            'res': json.dumps(resultado, default=str) if resultado else None})
        session.commit()


def _kh_lock_estado() -> dict:
    """Estado del sync leído de DB — lo mismo para los dos workers."""
    with get_db() as session:
        row = session.query(database.SyncLock).filter_by(id=_KH_LOCK_ID).first()
    if row is None:
        return {'corriendo': False, 'msg': '', 'log': [], 'resultado': None, 'ultimo': None}
    try:
        resultado = json.loads(row.ultimo_resultado) if row.ultimo_resultado else None
    except (ValueError, TypeError):
        resultado = None
    return {
        'corriendo': bool(row.en_curso),
        'msg': row.paso_actual or '',
        'log': row.log.split('\n') if row.log else [],
        'resultado': resultado,
        'ultimo': row.finalizado_en.strftime('%d/%m/%Y %H:%M') if row.finalizado_en else None,
    }


def _kh_provider(session):
    """El Provider de Kellerhoff, para saldo/extracto. Por CUIT normalizado
    (la vía robusta, ver services/cuenta_corriente); fallback al id de env."""
    cuit_d = normalizar_cuit(KELLERHOFF_CUIT)
    for p in session.query(database.Provider).all():
        if normalizar_cuit(p.cuit) == cuit_d:
            return p
    return session.get(database.Provider, KELLERHOFF_PROVIDER_ID)


def init_app(app):

    @app.route('/kellerhoff/sync')
    @login_required
    def kellerhoff_sync():
        credenciales_ok = bool(os.environ.get('KELLERHOFF_USER') and
                               os.environ.get('KELLERHOFF_PASS'))
        with get_db() as session:
            pendientes = (
                session.query(PedidoEmitido)
                .filter(
                    PedidoEmitido.drogueria_id == KELLERHOFF_PROVIDER_ID,
                    PedidoEmitido.estado == 'ABIERTO',
                    PedidoEmitido.factura_id.is_(None),
                )
                .order_by(PedidoEmitido.fecha.desc())
                .limit(10)
                .all()
            )
            pendientes_data = [
                # 'articulos' y NO 'items': en Jinja `p.items` resuelve al método
                # items() del dict, no a la key (gotcha documentado en CLAUDE.md).
                {'id': p.id, 'fecha': p.fecha.strftime('%d/%m/%Y'),
                 'articulos': p.total_items, 'unidades': p.total_unidades}
                for p in pendientes
            ]
            facturas_kh = (
                session.query(Invoice)
                .filter(Invoice.proveedor_cuit.like(f'%{KELLERHOFF_CUIT[-8:]}%'))
                .order_by(Invoice.fecha.desc())
                .limit(200)
                .all()
            )
            from helpers import detalle_facturas
            detalle = detalle_facturas(session, facturas_kh)
            facturas_data = [
                {
                    'id': f.id,
                    'detalle': detalle.get(f.id),
                    'numero': f.numero_factura,
                    'fecha': f.fecha.strftime('%d/%m/%Y') if f.fecha else '',
                    'tipo': f.tipo_comprobante or 'FAC',
                    'total': f.total,
                    'articulos': f.total_articulos or 0,
                    'origen': f.origen or 'manual',
                }
                for f in facturas_kh
            ]
        return render_template(
            'kellerhoff_sync.html',
            credenciales_ok=credenciales_ok,
            sync_estado=_kh_lock_estado(),
            pendientes=pendientes_data,
            facturas=facturas_data,
        )

    @app.route('/kellerhoff/sync/ejecutar', methods=['POST'])
    def kellerhoff_sync_ejecutar():
        # Auth: mismo patrón que /api/auto-sync — token de máquina (header
        # X-Auto-Sync-Token) O usuario logueado. Sin AUTO_SYNC_TOKEN seteado
        # (caso actual en prod) queda abierto, para que el cron de
        # /etc/cron.d/appfarmweb lo pueda llamar con curl sin sesión.
        expected = os.environ.get('AUTO_SYNC_TOKEN', '').strip()
        if expected:
            from flask_login import current_user
            sent = request.headers.get('X-Auto-Sync-Token', '').strip()
            if sent != expected and not current_user.is_authenticated:
                return jsonify({'ok': False, 'error': 'token invalido'}), 401

        # Lock en DB: si otro worker ya está sincronizando, rowcount==0 → 409.
        # Esto es lo que evita el segundo scraping en paralelo.
        if not _kh_lock_acquire():
            return jsonify({'ok': False, 'error': 'Ya hay un sync en curso'}), 409

        dias = min(int(request.form.get('dias', 7)), 60)
        hasta = date.today()
        desde = hasta - timedelta(days=dias)

        def _run():
            global _kh_log_buffer
            _kh_log_buffer = []   # log limpio para esta corrida (este worker)
            resultado = None
            try:
                resultado = _sincronizar(desde, hasta)
            except Exception as e:
                resultado = {'ok': False, 'error': str(e)}
            finally:
                _kh_lock_release(resultado)

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({'ok': True, 'mensaje': f'Sync iniciado ({desde} → {hasta})'})

    @app.route('/kellerhoff/sync/estado')
    @login_required
    def kellerhoff_sync_estado():
        return jsonify(_kh_lock_estado())

    # ── Landing del módulo "Control Kellerhoff" (panel del día) ───────────────

    @app.route('/kellerhoff')
    @login_required
    def kellerhoff_index():
        with get_db() as session:
            prov = _kh_provider(session)
            pedidos_abiertos = (
                session.query(PedidoEmitido)
                .filter(PedidoEmitido.drogueria_id == KELLERHOFF_PROVIDER_ID,
                        PedidoEmitido.estado == 'ABIERTO',
                        PedidoEmitido.factura_id.is_(None))
                .count()
            )
            facturas_kh = (
                session.query(Invoice)
                .filter(Invoice.proveedor_cuit.like(f'%{KELLERHOFF_CUIT[-8:]}%')).all()
            )
            from helpers import detalle_facturas
            det = detalle_facturas(session, facturas_kh)
            # "sin detalle" = factura sin renglones (lo que impide cruzar).
            facturas_sin_detalle = sum(
                1 for f in facturas_kh
                if not (det.get(f.id) or {}).get('renglones'))
            faltantes = (session.query(FacturaFaltante)
                         .join(Invoice, Invoice.id == FacturaFaltante.factura_id)
                         .filter(Invoice.proveedor_cuit.like(f'%{KELLERHOFF_CUIT[-8:]}%'))
                         .count())
            saldo = 0.0
            resumen_ultimo = None
            if prov is not None:
                _movs, resumen = movimientos_proveedor(session, prov)
                saldo = resumen['saldo']
                corte = corte_resumenes(session, prov)
                resumen_ultimo = corte.strftime('%d/%m/%Y') if corte else None
            panel = {
                'pedidos_abiertos': pedidos_abiertos,
                'facturas_sin_detalle': facturas_sin_detalle,
                'faltantes': faltantes,
                'saldo': saldo,
                'resumen_ultimo': resumen_ultimo,
                'ultimo_sync': _kh_lock_estado().get('ultimo'),
            }
        return render_template('kellerhoff_index.html', panel=panel)

    @app.route('/kellerhoff/cuenta-corriente')
    @login_required
    def kellerhoff_cuenta_corriente():
        """Extracto de Kellerhoff DENTRO del módulo. Reusa el motor único
        (movimientos_proveedor) — misma data que /cuentas-corrientes, vista
        filtrada a Kellerhoff y enmarcada en el módulo (no un silo aparte)."""
        # Default: últimos 30 días (solo si no vino ningún parámetro de fecha;
        # campos vacíos a propósito = "Ver todo", se respeta).
        _draw, _hraw = request.args.get('desde'), request.args.get('hasta')
        if _draw is None and _hraw is None:
            _hoy = date.today()
            desde = (_hoy - timedelta(days=30)).isoformat()
            hasta = _hoy.isoformat()
        else:
            desde = (_draw or '').strip() or None
            hasta = (_hraw or '').strip() or None
        with get_db() as session:
            prov = _kh_provider(session)
            movimientos, resumen = ([], {'saldo': 0.0, 'total_prefac': 0.0})
            corte = None
            if prov is not None:
                movimientos, resumen = movimientos_proveedor(session, prov)
                # Hasta dónde llegan los resúmenes importados: sin esto no se
                # distingue "todavía no lo cobraron" de "falta".
                corte = corte_resumenes(session, prov)
                from services.cuenta_corriente import filtrar_por_fecha
                movimientos = filtrar_por_fecha(movimientos, desde, hasta)
            prov_data = {'razon_social': prov.razon_social if prov else 'Kellerhoff',
                         'cuit': (prov.cuit if prov else '') or ''}
        return render_template('kellerhoff_cuenta_corriente.html',
                               provider=prov_data, movimientos=movimientos,
                               saldo_total=resumen['saldo'],
                               total_prefac=resumen.get('total_prefac', 0),
                               corte_resumenes=corte,
                               desde=desde, hasta=hasta, hoy=date.today())

    # ── Resúmenes de cuenta (el cierre semanal que emite Kellerhoff) ──────────

    @app.route('/kellerhoff/resumenes')
    @login_required
    def kellerhoff_resumenes():
        with get_db() as session:
            resumenes = []
            for r in (session.query(ResumenProveedor)
                      .filter_by(proveedor_id=KELLERHOFF_PROVIDER_ID)
                      .order_by(ResumenProveedor.periodo_desde.desc()).all()):
                n_items, n_ligados, cerrado = _contar_items(session, r.id)
                resumenes.append({
                    'id': r.id, 'numero': r.numero,
                    'periodo': _fmt_periodo(r),
                    'total': float(r.total or 0),
                    'cuadra': r.cuadra,
                    'diferencia': r.diferencia,
                    'primer_vencimiento': (r.primer_vencimiento.strftime('%d/%m/%Y')
                                           if r.primer_vencimiento else ''),
                    'n_items': n_items, 'n_ligados': n_ligados,
                    'cerrado': cerrado, 'pendientes': n_items - n_ligados,
                    'importado_en': (r.importado_en.strftime('%d/%m/%Y %H:%M')
                                     if r.importado_en else ''),
                })
        return render_template('kellerhoff_resumenes.html', resumenes=resumenes)

    @app.route('/kellerhoff/resumenes/importar', methods=['POST'])
    @login_required
    def kellerhoff_resumen_importar():
        from services.kellerhoff_resumen import importar_resumen

        archivo = request.files.get('pdf')
        if not archivo or not archivo.filename:
            flash('Elegí el PDF del resumen', 'error')
            return redirect(url_for('kellerhoff_resumenes'))

        nombre = secure_filename(archivo.filename)
        destino = os.path.join(app.config['UPLOAD_FOLDER'], nombre)
        archivo.save(destino)
        try:
            with get_db() as session:
                res = importar_resumen(session, destino, KELLERHOFF_PROVIDER_ID,
                                       pdf_filename=nombre)
        except Exception as e:
            flash(f'No se pudo importar: {e}', 'error')
            return redirect(url_for('kellerhoff_resumenes'))

        msg = (f"Resumen {res['numero']}: {len(res['items'])} comprobantes, "
               f"{res['ligados']} ligados a facturas nuestras.")
        # El aviso también queda guardado en la fila (ResumenProveedor.cuadra),
        # así que cerrar el flash no borra el rastro.
        if res['cuadra'] is False:
            flash(f"{msg} ⚠ El total impreso (${res['total']:,.2f}) no coincide con "
                  f"la suma de los renglones (${res['total_calculado']:,.2f}): faltan "
                  f"${res['diferencia']:,.2f}. Se leyó mal alguna línea del PDF.",
                  'error')
        elif res['cuadra'] is None:
            flash(f"{msg} ⚠ No se pudo leer el TOTAL RESUMEN del PDF, así que no hay "
                  f"con qué verificar que los renglones estén completos.", 'error')
        else:
            flash(msg, 'success')
        return redirect(url_for('kellerhoff_resumen_detalle', resumen_id=res['resumen_id']))

    @app.route('/kellerhoff/resumen/<int:resumen_id>')
    @login_required
    def kellerhoff_resumen_detalle(resumen_id):
        import json as _json

        with get_db() as session:
            r = session.get(ResumenProveedor, resumen_id)
            if r is None:
                flash('Resumen no encontrado', 'error')
                return redirect(url_for('kellerhoff_resumenes'))

            from services.kellerhoff_resumen import cruce_erp_map, estado_item, item_tildado

            items_orm = (session.query(ResumenProveedorItem)
                        .filter_by(resumen_id=r.id)
                        .order_by(ResumenProveedorItem.fecha,
                                  ResumenProveedorItem.numero).all())
            cruce_erp = cruce_erp_map(session, items_orm)
            items = []
            for it in items_orm:
                items.append({
                    'fecha': it.fecha.strftime('%d/%m/%Y') if it.fecha else '',
                    'tipo': it.tipo, 'numero': it.numero,
                    'numero_remito': it.numero_remito or '',
                    'total': float(it.total or 0),
                    'factura_id': it.factura_id,
                    'pago_ajuste_id': it.pago_ajuste_id,
                    'tildado': item_tildado(it, cruce_erp),
                    'checks': estado_item(it, cruce_erp),
                })
            n_items, n_tildados, cerrado = _contar_items(session, r.id)
            cab = {
                'id': r.id, 'numero': r.numero, 'periodo': _fmt_periodo(r),
                'cierre': r.cierre or '',
                'total': float(r.total or 0),
                'total_calculado': float(r.total_calculado or 0),
                'cuadra': r.cuadra,
                'diferencia': r.diferencia,
                'primer_vencimiento': (r.primer_vencimiento.strftime('%d/%m/%Y')
                                       if r.primer_vencimiento else ''),
                'generado_en': (r.generado_en.strftime('%d/%m/%Y %H:%M')
                                if r.generado_en else ''),
                # Se guardan en ISO, pero la pantalla usa dd/mm/aaaa en todos
                # lados: sin esto los vencimientos salían '2026-09-22' al lado
                # de fechas '22/08/2026'.
                'vencimientos': [{'fecha': _fmt_iso(v.get('fecha')),
                                  'importe': v.get('importe')}
                                 for v in _json.loads(r.vencimientos_json or '[]')],
                'n_items': n_items, 'n_tildados': n_tildados,
                'pendientes': n_items - n_tildados, 'cerrado': cerrado,
            }

        from flask_login import current_user

        import observer_source
        observer_disponible = bool(
            current_user.is_authenticated
            and current_user.rol in ('farmacia', 'dev', 'admin')
            and observer_source.observer_disponible()
        )
        return render_template('kellerhoff_resumen_detalle.html',
                               resumen=cab, items=items,
                               observer_disponible=observer_disponible)

    @app.route('/kellerhoff/resumen/<int:resumen_id>/verificar-ingresos', methods=['POST'])
    @login_required
    def kellerhoff_resumen_verificar_ingresos(resumen_id):
        """Corre el cruce contra ObServer (DW.Recepciones) para todos los
        ítems del resumen — no corta ante el primero que falle o no aparezca,
        junta todo y avisa el resultado al final."""
        from flask_login import current_user

        import observer_source
        if not (current_user.rol in ('farmacia', 'dev', 'admin')
                and observer_source.observer_disponible()):
            flash('ObServer no está disponible en este momento.', 'error')
            return redirect(url_for('kellerhoff_resumen_detalle', resumen_id=resumen_id))

        from services.kellerhoff_resumen import verificar_ingresos_resumen
        with get_db() as session:
            r = session.get(ResumenProveedor, resumen_id)
            if r is None:
                flash('Resumen no encontrado', 'error')
                return redirect(url_for('kellerhoff_resumenes'))
            try:
                res = verificar_ingresos_resumen(session, resumen_id)
            except RuntimeError as e:
                flash(f'No se pudo consultar ObServer: {e}', 'error')
                return redirect(url_for('kellerhoff_resumen_detalle', resumen_id=resumen_id))

        msg = (f"Ingresos verificados: {res['encontrados']} encontrados, "
               f"{res['no_encontrados']} sin recepción en ObServer")
        if res.get('no_aplica'):
            msg += f", {res['no_aplica']} no aplica (NC)"
        if res['errores']:
            msg += f", {res['errores']} con error (reintentá)"
        flash(msg, 'success' if not res['errores'] else 'warning')
        return redirect(url_for('kellerhoff_resumen_detalle', resumen_id=resumen_id))

    @app.route('/kellerhoff/sync/ligar', methods=['POST'])
    @login_required
    def kellerhoff_sync_ligar():
        """Liga manualmente una factura a un PedidoEmitido."""
        pedido_id = request.form.get('pedido_id', type=int)
        factura_id = request.form.get('factura_id', type=int)
        if not pedido_id or not factura_id:
            return jsonify({'ok': False, 'error': 'Faltan parámetros'}), 400
        with get_db() as session:
            pedido = session.get(PedidoEmitido, pedido_id)
            factura = session.get(Invoice, factura_id)
            if not pedido or not factura:
                return jsonify({'ok': False, 'error': 'No encontrado'}), 404
            pedido.factura_id = factura_id
            pedido.estado = 'FACTURADA'
            session.commit()
        return jsonify({'ok': True})


# ── Lógica de sincronización ───────────────────────────────────────────────────

def _msg(texto: str) -> None:
    """Publica una línea de progreso: paso_actual + log completo en sync_lock,
    para que el worker que atiende el polling (que puede NO ser este) la vea.

    Corre en el thread del sync, que es el único que tiene el lock, así que el
    buffer de módulo es de este worker y no pisa a nadie. Cada línea = un UPDATE
    chico; en un sync son ~150, trivial para la DB.
    """
    import logging
    _kh_log_buffer.append(texto)
    if len(_kh_log_buffer) > 800:   # cap defensivo para un tramo largo
        del _kh_log_buffer[:len(_kh_log_buffer) - 800]
    try:
        with get_db() as session:
            session.execute(_text(
                "UPDATE sync_lock SET paso_actual = :paso, log = :log WHERE id = :lid"
            ), {'paso': texto[:80], 'log': '\n'.join(_kh_log_buffer), 'lid': _KH_LOCK_ID})
            session.commit()
    except Exception:
        # El log es informativo: si falla el UPDATE, el sync sigue igual.
        pass
    logging.getLogger(__name__).warning('[KH-SYNC] %s', texto)


def nros_ya_completos(session) -> set:
    """Comprobantes que NO hace falta volver a bajar del portal.

    Son los que ya tienen todo lo que el detalle aporta: facturas/NCR de
    mercadería **con renglones**, y las NC financieras ya registradas como
    ajuste de cuenta corriente (esas no tienen detalle que bajar).

    ⚠ El filtro `Invoice.items.any()` es el punto del asunto y no se puede
    sacar. Antes se salteaba TODA factura que existiera en la DB, tuviera
    renglones o no, y `scrape_comprobantes` a las salteadas les pone `items=[]`
    sin navegar el detalle. Resultado: una factura guardada con encabezado y sin
    renglones **no podía recuperarlos nunca** — cada sync la salteaba por "ya
    existe". Le pasó a 0046-00255798: encabezado y desglose fiscal perfectos,
    cero ítems, y volver a sincronizar no la arreglaba.

    El síntoma de que era un bug estaba a la vista: el contador `enriquecidos`
    ("factura que ya existía y ahora ganó sus ítems") era inalcanzable, porque
    exige a la vez estar salteada y traer ítems.
    """
    rows = (
        session.query(Invoice.numero_factura)
        .filter(Invoice.proveedor_cuit.like(f'%{KELLERHOFF_CUIT[-8:]}%'),
                Invoice.items.any())
        .all()
    )
    nros = {r[0] for r in rows}
    rows_nc = (
        session.query(PagoAjusteCC.numero_comprobante)
        .filter(PagoAjusteCC.anunciante_id.isnot(None))
        .all()
    )
    nros |= {r[0] for r in rows_nc if r[0]}
    return nros


def _sincronizar(desde: date, hasta: date) -> dict:
    from services.kellerhoff_scraper import scrape_comprobantes

    with get_db() as session:
        _nros_db = nros_ya_completos(session)

    _msg(f'Iniciando Chromium… (rango {desde} → {hasta})')
    comprobantes = scrape_comprobantes(desde, hasta, _msg, skip_nros=_nros_db)
    _msg(f'Scraping completo: {len(comprobantes)} comprobante(s) encontrados')

    creados = 0
    enriquecidos = 0
    ligados = 0
    nc_financieras = 0
    faltantes_registrados = 0
    errores = []

    with get_db() as session:
        for i, comp in enumerate(comprobantes, 1):
            nro = comp.get('nro_comp_kh', '?')
            _msg(f'Procesando {i}/{len(comprobantes)}: {nro}')
            try:
                analisis = comp.get('analisis') or {'categoria': 'factura'}

                if analisis.get('categoria') == 'nc_financiera':
                    if _crear_pago_ajuste_nc(session, comp, analisis):
                        nc_financieras += 1
                    session.commit()
                    continue

                inv = _get_or_create_invoice(session, comp)
                if inv is None:
                    continue

                # Vencimiento de pago + TRF detectados del detalle. Solo si están
                # vacíos: no pisar una edición manual ni reescribir en re-syncs.
                _vt = comp.get('analisis') or {}
                if inv.vencimiento is None and _vt.get('vencimiento'):
                    inv.vencimiento = _vt['vencimiento']
                if not inv.condicion_pago and _vt.get('condicion_pago'):
                    inv.condicion_pago = _vt['condicion_pago']
                if not inv.trf and _vt.get('trf'):
                    inv.trf = _vt['trf']

                es_nuevo = not comp.get('_estaba_en_db')
                if not inv.items:
                    _crear_items(session, inv, comp['items'])
                    if comp['items']:
                        if es_nuevo:
                            creados += 1
                        else:
                            enriquecidos += 1
                    faltantes_registrados += _crear_faltantes(
                        session, inv, analisis.get('faltantes') or [])

                pedido = _match_pedido(session, inv, comp)
                if pedido and not pedido.factura_id:
                    pedido.factura_id = inv.id
                    pedido.nro_remito_kh = comp.get('nro_remito', '') or ''
                    pedido.estado = 'FACTURADA'
                    ligados += 1

                session.commit()
            except Exception as e:
                session.rollback()
                errores.append(f"{nro}: {e}")

    return {
        'ok': True,
        'comprobantes': len(comprobantes),
        'creados': creados,
        'enriquecidos': enriquecidos,
        'ligados': ligados,
        'nc_financieras': nc_financieras,
        'faltantes_registrados': faltantes_registrados,
        'errores': errores,
    }


def _crear_pago_ajuste_nc(session, comp: dict, analisis: dict) -> bool:
    """NC financiera (recupero de publicidad/descuento de un anunciante) →
    PagoAjusteCC vinculado a Anunciante, NUNCA a Invoice/InvoiceItem (no hay
    mercadería, es un ajuste monetario). Devuelve False si ya estaba cargada.
    """
    nro = comp.get('nro_comp_arca') or _normalizar_nro(comp.get('nro_comp_kh', ''))
    if not nro:
        return False
    ya_existe = (
        session.query(PagoAjusteCC)
        .filter(
            PagoAjusteCC.numero_comprobante == nro,
            PagoAjusteCC.anunciante_id.isnot(None),
        )
        .first()
    )
    if ya_existe:
        return False

    anunciante = resolver_anunciante(session, analisis.get('anunciante_nombre', ''))
    if anunciante is None:
        return False

    session.add(PagoAjusteCC(
        anunciante_id=anunciante.id,
        # Convención por defecto (sin validar con el usuario): un recupero
        # reduce lo que el anunciante "debe" en su cta cte con la farmacia.
        # Revisar si en la práctica el signo esperado es el opuesto.
        tipo='AJUSTE_NEG',
        fecha=comp['fecha'],
        monto=abs(comp.get('total', 0)),
        numero_comprobante=nro,
        observaciones=analisis.get('concepto', ''),
    ))
    return True


def _crear_faltantes(session, inv: Invoice, faltantes: list[dict]) -> int:
    """Ítems de '*** PRODUCTOS EN FALTA MOMENTANEA ***' → FacturaFaltante.
    Nunca suman a factura_items ni al total (ver database.FacturaFaltante)."""
    n = 0
    for f in faltantes:
        desc = (f.get('descripcion') or '').strip()
        if not desc:
            continue
        session.add(FacturaFaltante(
            factura_id=inv.id,
            codigo_barra=(f.get('codigo_barra') or '')[:20] or None,
            cantidad=f.get('cantidad') or 0,
            descripcion=desc[:150],
        ))
        n += 1
    return n


def _get_or_create_invoice(session, comp: dict) -> Invoice | None:
    """Busca la factura ARCA existente o crea una provisional con datos del portal."""
    nro = comp.get('nro_comp_arca') or _normalizar_nro(comp.get('nro_comp_kh', ''))
    if not nro:
        return None

    # Buscar por número normalizado y CUIT Kellerhoff
    existente = (
        session.query(Invoice)
        .filter(
            Invoice.proveedor_cuit.like(f'%{KELLERHOFF_CUIT[-8:]}%'),
            Invoice.numero_factura == nro,
        )
        .first()
    )
    if existente:
        # Marca para el contador: la factura ya estaba en la DB. NO se puede usar
        # `_ya_existe` (que pone el scraper) porque ése sólo marca las que se
        # saltearon del portal, y desde que las facturas sin renglones dejaron de
        # saltearse, una preexistente que gana su detalle no lleva esa marca.
        comp['_estaba_en_db'] = True
        return existente

    # Crear provisional (puede enriquecerse después con el import ARCA)
    es_nc = comp.get('clase_doc', '').upper() in ('NCR', 'NC', 'NCA', 'NCB', 'NCC')
    signo = -1 if es_nc else 1
    inv = Invoice(
        numero_factura=nro,
        fecha=comp['fecha'],
        proveedor_razon='DROGUERIA KELLERHOFF S.A.',
        proveedor_cuit=KELLERHOFF_CUIT,
        tipo_comprobante='NCR' if es_nc else 'FAC',
        total=signo * abs(comp.get('total', 0)),
        monto_exento=signo * abs(comp.get('monto_exento', 0)) or None,
        monto_gravado=signo * abs(comp.get('monto_gravado', 0)) or None,
        iva_21=signo * abs(comp.get('iva', 0)) or None,
        percepciones=signo * abs(
            comp.get('percepcion_dgr', 0) +
            comp.get('percepcion_mun', 0) +
            comp.get('percepcion_iva', 0)
        ) or None,
        total_articulos=len(comp.get('items', [])),
        total_unidades=sum(it.get('cantidad', 0) for it in comp.get('items', [])),
        origen='kh_portal',
    )
    session.add(inv)
    session.flush()
    return inv


def _crear_items(session, inv: Invoice, items: list[dict]) -> None:
    for it in items:
        session.add(InvoiceItem(
            factura_id=inv.id,
            codigo_barra=it.get('barcode', '') or '',
            descripcion=(it.get('descripcion', '') or '')[:150],
            cantidad=it.get('cantidad', 0),
            precio_unitario=it.get('precio_unitario') or it.get('precio_pub'),
            dto=it.get('dto_pct') or None,
            importe=it.get('importe', 0),
        ))
    inv.total_articulos = len(items)
    inv.total_unidades = sum(it.get('cantidad', 0) for it in items)


def _match_pedido(session, inv: Invoice, comp: dict) -> PedidoEmitido | None:
    """PedidoEmitido de Kellerhoff sin factura y más cercano en fecha (±5 días)."""
    candidatos = (
        session.query(PedidoEmitido)
        .filter(
            PedidoEmitido.drogueria_id == KELLERHOFF_PROVIDER_ID,
            PedidoEmitido.estado == 'ABIERTO',
            PedidoEmitido.factura_id.is_(None),
        )
        .all()
    )
    if not candidatos:
        return None

    mejor, mejor_delta = None, None
    for p in candidatos:
        delta = abs((p.fecha.date() if hasattr(p.fecha, 'date') else p.fecha) - inv.fecha)
        if isinstance(delta, timedelta):
            delta = delta.days
        if delta <= 5 and (mejor_delta is None or delta < mejor_delta):
            mejor, mejor_delta = p, delta
    return mejor


def _fmt_iso(s):
    """'2026-09-22' → '22/09/2026'. Devuelve lo que entró si no es una fecha ISO."""
    from datetime import datetime as _dt
    try:
        return _dt.strptime(s, '%Y-%m-%d').strftime('%d/%m/%Y')
    except (TypeError, ValueError):
        return s or ''


def _fmt_periodo(r) -> str:
    if not r.periodo_desde:
        return ''
    hasta = r.periodo_hasta.strftime('%d/%m/%Y') if r.periodo_hasta else ''
    return f"{r.periodo_desde.strftime('%d/%m')} al {hasta}"


def _contar_items(session, resumen_id):
    """(total de renglones, cuántos están tildados, si la semana está cerrada)."""
    from services.kellerhoff_resumen import estado_resumen
    return estado_resumen(session, resumen_id)


def _normalizar_nro(s: str) -> str:
    """'00001-00012345' o '1-12345' → '00001-00012345'."""
    s = s.strip()
    if '-' in s:
        partes = s.split('-', 1)
        return f"{partes[0].zfill(5)}-{partes[1].zfill(8)}"
    return s


def _tipo_comp(tipo: str) -> str:
    t = tipo.upper()
    if 'NC' in t or 'NOTA' in t or 'CR' in t:
        return 'NCR'
    return 'FAC'
