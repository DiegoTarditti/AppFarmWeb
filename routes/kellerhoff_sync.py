"""Ciclo de compra Kellerhoff: scraping del portal → InvoiceItems → liga PedidoEmitido."""
from __future__ import annotations

import os
import threading
from datetime import date, datetime, timedelta

from flask import jsonify, render_template, request
from flask_login import login_required

import database
from database import FacturaFaltante, Invoice, InvoiceItem, PagoAjusteCC, PedidoEmitido, get_db
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
            facturas_data = [
                {
                    'id': f.id,
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
    """Actualiza el mensaje visible en el UI durante el sync."""
    import logging
    _sync_estado['msg'] = texto
    logging.getLogger(__name__).warning('[KH-SYNC] %s', texto)


def _sincronizar(desde: date, hasta: date) -> dict:
    from services.kellerhoff_scraper import scrape_comprobantes

    # Pre-cargar nros ya en DB para no re-navegar páginas de detalle: tanto
    # facturas/NCR de mercadería (Invoice) como NC financieras ya registradas
    # (PagoAjusteCC.numero_comprobante con anunciante_id seteado).
    with get_db() as session:
        rows = (
            session.query(Invoice.numero_factura)
            .filter(Invoice.proveedor_cuit.like(f'%{KELLERHOFF_CUIT[-8:]}%'))
            .all()
        )
        _nros_db = {r[0] for r in rows}
        rows_nc = (
            session.query(PagoAjusteCC.numero_comprobante)
            .filter(PagoAjusteCC.anunciante_id.isnot(None))
            .all()
        )
        _nros_db |= {r[0] for r in rows_nc if r[0]}

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

                es_nuevo = comp.get('_ya_existe') is None
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
