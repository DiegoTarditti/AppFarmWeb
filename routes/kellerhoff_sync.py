"""Ciclo de compra Kellerhoff: scraping del portal → InvoiceItems → liga PedidoEmitido."""
from __future__ import annotations

import os
import threading
from datetime import date, datetime, timedelta

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required
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
from services.kellerhoff_analizador import resolver_anunciante

KELLERHOFF_PROVIDER_ID = int(os.environ.get('KELLERHOFF_PROVIDER_ID', '1'))
KELLERHOFF_CUIT = '30539756490'

# Estado de ejecución del scraper (in-memory, un thread a la vez)
_sync_lock = threading.Lock()
_sync_estado: dict = {'corriendo': False, 'ultimo': None, 'resultado': None, 'msg': ''}


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
                {'id': p.id, 'fecha': p.fecha.strftime('%d/%m/%Y'),
                 'items': p.total_items, 'unidades': p.total_unidades}
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
            sync_estado=_sync_estado,
            pendientes=pendientes_data,
            facturas=facturas_data,
        )

    @app.route('/kellerhoff/sync/ejecutar', methods=['POST'])
    @login_required
    def kellerhoff_sync_ejecutar():
        if not _sync_lock.acquire(blocking=False):
            return jsonify({'ok': False, 'error': 'Ya hay un sync en curso'}), 409

        dias = min(int(request.form.get('dias', 7)), 60)
        hasta = date.today()
        desde = hasta - timedelta(days=dias)

        def _run():
            try:
                _sync_estado['corriendo'] = True
                _sync_estado['resultado'] = None
                _sync_estado['log'] = []   # log en vivo, arranca limpio cada corrida
                resultado = _sincronizar(desde, hasta)
                _sync_estado['resultado'] = resultado
                _sync_estado['ultimo'] = datetime.now().strftime('%d/%m/%Y %H:%M')
            except Exception as e:
                _sync_estado['resultado'] = {'ok': False, 'error': str(e)}
            finally:
                _sync_estado['corriendo'] = False
                _sync_lock.release()

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({'ok': True, 'mensaje': f'Sync iniciado ({desde} → {hasta})'})

    @app.route('/kellerhoff/sync/estado')
    @login_required
    def kellerhoff_sync_estado():
        return jsonify(_sync_estado)

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

            from services.kellerhoff_resumen import estado_item, item_tildado

            items = []
            for it in (session.query(ResumenProveedorItem)
                       .filter_by(resumen_id=r.id)
                       .order_by(ResumenProveedorItem.fecha,
                                 ResumenProveedorItem.numero).all()):
                items.append({
                    'fecha': it.fecha.strftime('%d/%m/%Y') if it.fecha else '',
                    'tipo': it.tipo, 'numero': it.numero,
                    'numero_remito': it.numero_remito or '',
                    'total': float(it.total or 0),
                    'factura_id': it.factura_id,
                    'pago_ajuste_id': it.pago_ajuste_id,
                    'tildado': item_tildado(it),
                    'checks': estado_item(it),
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
        return render_template('kellerhoff_resumen_detalle.html',
                               resumen=cab, items=items)

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
    """Actualiza el mensaje visible + acumula el log en vivo que muestra la UI."""
    import logging
    _sync_estado['msg'] = texto
    log = _sync_estado.setdefault('log', [])
    log.append(texto)
    # Cap defensivo: un sync largo no debe inflar la respuesta del endpoint.
    if len(log) > 800:
        del log[:len(log) - 800]
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
