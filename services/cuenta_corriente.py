"""Cálculo único de movimientos y saldo de la cuenta corriente de proveedores.

Lo usan las dos pantallas del módulo: el extracto de un proveedor y el listado
con el saldo de todos. Antes cada una tenía su propia versión y daban números
distintos para el mismo proveedor:

  · el extracto clasificaba por `tipo_comprobante` sobre `abs(total)`; el
    listado sumaba `total` crudo. Una PREFAC restaba en un lado y sumaba en el
    otro.
  · el extracto cruzaba el CUIT por igualdad exacta de string y el listado por
    dígitos normalizados. Los parsers escriben '30-64266156-2' y el import ARCA
    pasa por `_fmt_cuit`, así que una factura podía aparecer en el listado y no
    en el extracto del mismo proveedor.

Toda pantalla nueva que muestre saldo de proveedor tiene que entrar por acá.
"""

from collections import defaultdict

from sqlalchemy import distinct, func

import database

# Clasificación de comprobantes. Manda el TIPO, no el signo guardado en
# `total`: las NC se guardan en negativo (ver CLAUDE.md), así que se usa el
# valor absoluto y el tipo decide de qué lado cae.
TIPOS_DEBE = ('FAC',)
TIPOS_HABER = ('NCR',)

# PREFAC = "documento no válido como factura". La mercadería entró pero todavía
# no hay comprobante fiscal, así que NO suma al saldo: se muestra en el extracto
# marcada y se totaliza aparte (`total_prefac`). Confirmado por Diego el
# 2026-07-21: la prefactura no suma. No moverla a TIPOS_DEBE sin volver a
# preguntar — antes de esto el extracto la restaba y el listado la sumaba, y
# el mismo proveedor daba dos saldos distintos.
TIPOS_INFORMATIVOS = ('PREFAC',)

# Separadores que puede traer un CUIT escrito a mano o por un parser. Se
# replican en SQL (`_cuit_sql_normalizado`) para que el filtro de la query y el
# de Python coincidan.
_SEPARADORES_CUIT = ('-', ' ', '.')


def normalizar_cuit(s):
    """'30-64266156-2' → '30642661562'. Devuelve '' si no hay dígitos."""
    return ''.join(ch for ch in (s or '') if ch.isdigit())


def _cuit_sql_normalizado(col):
    """Misma normalización que `normalizar_cuit`, aplicable dentro de la query."""
    expr = col
    for sep in _SEPARADORES_CUIT:
        expr = func.replace(expr, sep, '')
    return expr


def clasificar_comprobante(tipo_comprobante, total):
    """(debe, haber, informativo) de un comprobante — los tres >= 0.

    Un comprobante cae en exactamente uno de los tres baldes. `informativo` no
    entra en el saldo (hoy: PREFAC).
    """
    monto = abs(float(total or 0))
    tipo = (tipo_comprobante or 'FAC').upper()
    if tipo in TIPOS_INFORMATIVOS:
        return 0.0, 0.0, monto
    if tipo in TIPOS_HABER:
        return 0.0, monto, 0.0
    # Default a debe: cubre FAC y cualquier tipo nuevo que aparezca sin que
    # nadie lo agregue acá (una factura desconocida es deuda, no crédito).
    return monto, 0.0, 0.0


def _query_facturas_proveedor(session, provider):
    """Facturas del proveedor: por razón social o por CUIT normalizado."""
    q = session.query(database.Invoice)
    cuit_d = normalizar_cuit(provider.cuit)
    if cuit_d:
        return q.filter(
            (_cuit_sql_normalizado(database.Invoice.proveedor_cuit) == cuit_d) |
            (database.Invoice.proveedor_razon == provider.razon_social)
        )
    return q.filter(database.Invoice.proveedor_razon == provider.razon_social)


def movimientos_proveedor(session, provider):
    """Extracto completo de un proveedor.

    Devuelve (movimientos, resumen). Cada movimiento trae `debe`/`haber` ya
    clasificados y `saldo` acumulado. El resumen lleva el saldo final y el
    total de prefacturas, que va aparte por no ser fiscal.
    """
    movimientos = []

    invoices = _query_facturas_proveedor(session, provider).order_by(
        database.Invoice.fecha).all()
    inv_ids = [inv.id for inv in invoices]

    reclamos_map = {}
    ingresadas = set()
    if inv_ids:
        for c in (session.query(database.Claim)
                  .filter(database.Claim.factura_id.in_(inv_ids)).all()):
            reclamos_map[c.factura_id] = c.estado
        ingresadas = {r[0] for r in
                      session.query(distinct(database.StockDifference.factura_id))
                      .filter(database.StockDifference.factura_id.in_(inv_ids)).all()}

    for inv in invoices:
        debe, haber, informativo = clasificar_comprobante(
            inv.tipo_comprobante, inv.total)
        reclamo_est = reclamos_map.get(inv.id)
        movimientos.append({
            'fecha': inv.fecha,
            'fecha_proceso': inv.creado_en.strftime('%d/%m/%Y') if inv.creado_en else '',
            'tipo': inv.tipo_comprobante,
            'comprobante': inv.numero_factura or '',
            'debe': debe,
            'haber': haber,
            'informativo': informativo,
            'obs': f'Reclamo: {reclamo_est}' if reclamo_est else '',
            'ingresada': inv.id in ingresadas,
            'reclamo_estado': reclamo_est,
            'conciliado': inv.conciliado,
            'origen': 'factura',
            'id': inv.id,
        })

    for pa in (session.query(database.PagoAjusteCC)
               .filter_by(proveedor_id=provider.id)
               .order_by(database.PagoAjusteCC.fecha).all()):
        es_debe = pa.tipo == 'AJUSTE_POS'
        monto = float(pa.monto or 0)
        movimientos.append({
            'fecha': pa.fecha,
            'fecha_proceso': '',
            'tipo': pa.tipo,
            'comprobante': pa.numero_comprobante or '',
            'debe': monto if es_debe else 0.0,
            'haber': 0.0 if es_debe else monto,
            'informativo': 0.0,
            'obs': pa.observaciones or '',
            'ingresada': None,
            'reclamo_estado': None,
            'conciliado': pa.conciliado,
            'origen': 'manual',
            'id': pa.id,
        })

    # Pagos estructurados (salen de una CuentaPago y aplican a facturas) → haber.
    for pg in (session.query(database.Pago)
               .filter_by(proveedor_id=provider.id)
               .order_by(database.Pago.fecha).all()):
        movimientos.append({
            'fecha': pg.fecha,
            'fecha_proceso': '',
            'tipo': 'PAGO',
            'comprobante': pg.nro_comprobante or '',
            'debe': 0.0,
            'haber': float(pg.monto or 0),
            'informativo': 0.0,
            'obs': pg.observaciones or '',
            'ingresada': None,
            'reclamo_estado': None,
            'conciliado': False,
            'origen': 'pago',
            'id': pg.id,
        })

    movimientos.sort(key=lambda m: (m['fecha'], m['tipo']))
    saldo = 0.0
    total_prefac = 0.0
    for m in movimientos:
        saldo += m['debe'] - m['haber']
        total_prefac += m['informativo']
        m['saldo'] = saldo

    resumen = {
        'saldo': saldo,
        'total_prefac': total_prefac,
        'n_comprobantes': len(invoices),
    }
    return movimientos, resumen


def saldos_por_proveedor(session):
    """Saldo de todos los proveedores de una, para el listado.

    Devuelve {provider_id: {saldo, total_prefac, n_comprobantes, ultimo_mov}}.
    Hace 4 queries en total (no una por proveedor) y usa exactamente la misma
    clasificación que `movimientos_proveedor`.
    """
    por_cuit = defaultdict(list)
    por_razon = defaultdict(list)
    for iid, cuit, razon, tipo, total, fecha in session.query(
            database.Invoice.id, database.Invoice.proveedor_cuit,
            database.Invoice.proveedor_razon, database.Invoice.tipo_comprobante,
            database.Invoice.total, database.Invoice.fecha).all():
        rec = (iid, tipo, total, fecha)
        cuit_d = normalizar_cuit(cuit)
        if cuit_d:
            por_cuit[cuit_d].append(rec)
        if razon:
            por_razon[razon].append(rec)

    ajustes = defaultdict(list)
    for pid, tipo, monto, fecha in session.query(
            database.PagoAjusteCC.proveedor_id, database.PagoAjusteCC.tipo,
            database.PagoAjusteCC.monto, database.PagoAjusteCC.fecha).all():
        ajustes[pid].append((tipo, float(monto or 0), fecha))

    pagos = defaultdict(list)
    for pid, monto, fecha in session.query(
            database.Pago.proveedor_id, database.Pago.monto,
            database.Pago.fecha).all():
        pagos[pid].append((float(monto or 0), fecha))

    out = {}
    for p in session.query(database.Provider).all():
        # Dedup por id: una factura puede matchear por CUIT y por razón social.
        vistos = {}
        for rec in por_cuit.get(normalizar_cuit(p.cuit), []):
            vistos[rec[0]] = rec
        for rec in por_razon.get(p.razon_social, []):
            vistos[rec[0]] = rec

        saldo = 0.0
        total_prefac = 0.0
        fechas = []
        for _iid, tipo, total, fecha in vistos.values():
            debe, haber, informativo = clasificar_comprobante(tipo, total)
            saldo += debe - haber
            total_prefac += informativo
            if fecha:
                fechas.append(fecha)

        for tipo, monto, fecha in ajustes.get(p.id, []):
            saldo += monto if tipo == 'AJUSTE_POS' else -monto
            if fecha:
                fechas.append(fecha)
        for monto, fecha in pagos.get(p.id, []):
            saldo -= monto
            if fecha:
                fechas.append(fecha)

        out[p.id] = {
            'saldo': saldo,
            'total_prefac': total_prefac,
            'n_comprobantes': len(vistos),
            'ultimo_mov': max(fechas) if fechas else None,
        }
    return out
