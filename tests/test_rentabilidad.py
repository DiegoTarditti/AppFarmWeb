"""Costeo por producto: costo de reposición, mejor precio y sobreprecio.

Los casos vienen de datos reales medidos sobre jul-sep 2026 (ver docstring de
`services/rentabilidad.py`).
"""
from datetime import date
from decimal import Decimal

import database
from database import Invoice, InvoiceItem
from services.rentabilidad import (
    confianza_costo,
    costos_por_ean,
    margen,
)

HOY = date(2026, 9, 12)


def _compra(session, ean, fecha, precio, cantidad=1, dto=None, tipo='FAC',
            proveedor='KELLERHOFF'):
    inv = Invoice(numero_factura=f'F{ean}-{fecha}-{tipo}', fecha=fecha,
                  proveedor_razon=proveedor, proveedor_cuit='30539756490',
                  tipo_comprobante=tipo, total=precio * cantidad)
    session.add(inv)
    session.flush()
    session.add(InvoiceItem(factura_id=inv.id, codigo_barra=ean, descripcion=f'PROD {ean}',
                            cantidad=cantidad, precio_unitario=precio, dto=dto,
                            importe=precio * cantidad))


def _costear(session, **kw):
    return costos_por_ean(session, hoy=HOY, **kw)


def test_el_costo_de_reposicion_es_la_ultima_compra():
    s = database.SessionLocal()
    try:
        _compra(s, '111', date(2026, 7, 21), 274902, cantidad=3)
        _compra(s, '111', date(2026, 8, 12), 273148, cantidad=4)
        _compra(s, '111', date(2026, 9, 9), 278269, cantidad=4)   # la última, no la más barata
        s.commit()
        c = _costear(s)['111']
        assert c['costo_reposicion'] == 278269
        assert c['fecha_costo'] == date(2026, 9, 9)
        assert c['dias_costo'] == 3
        assert c['unidades_compradas'] == 11
    finally:
        s.close()


def test_una_nota_de_credito_no_cuenta_como_compra():
    """En producción los 86 renglones de NCR están todos en POSITIVO: si se
    sumaran por el signo del renglón, una devolución contaría como compra."""
    s = database.SessionLocal()
    try:
        _compra(s, '111', date(2026, 8, 10), 1000, cantidad=10)
        _compra(s, '111', date(2026, 8, 20), 1000, cantidad=4, tipo='NCR')
        s.commit()
        c = _costear(s)['111']
        assert c['unidades_compradas'] == 10
        assert c['unidades_devueltas'] == 4
        # Y la NCR tampoco puede quedar como el costo de reposición.
        assert c['fecha_costo'] == date(2026, 8, 10)
    finally:
        s.close()


def test_los_renglones_a_costo_cero_no_son_el_mejor_precio():
    """Psicotrópicos y probadores entran a $0. Si contaran como mejor precio,
    toda otra compra figuraría con sobreprecio de cientos de miles por ciento."""
    s = database.SessionLocal()
    try:
        _compra(s, '111', date(2026, 8, 5), Decimal('0.01'), cantidad=3)
        _compra(s, '111', date(2026, 8, 10), 32000, cantidad=1)
        _compra(s, '111', date(2026, 9, 1), 33000, cantidad=1)
        s.commit()
        c = _costear(s)['111']
        assert c['mejor_precio'] == 32000          # no el de $0,01
        assert c['compras'][-1]['sobre_pct'] < 10  # y el sobreprecio queda razonable
    finally:
        s.close()


def test_el_costo_de_reposicion_si_puede_ser_una_compra_a_cero():
    """Excluirlos del baseline no es excluirlos del costo: si lo último que
    entró salió $0, reponerlo hoy sale $0."""
    s = database.SessionLocal()
    try:
        _compra(s, '111', date(2026, 8, 10), 32000, cantidad=1)
        _compra(s, '111', date(2026, 9, 1), Decimal('0.01'), cantidad=3)
        s.commit()
        c = _costear(s)['111']
        assert float(c['costo_reposicion']) == 0.01
        assert c['mejor_precio'] == 32000
    finally:
        s.close()


# ── Sobreprecio ajustado por inflación ───────────────────────────────────────

def test_sin_ajustar_la_inflacion_se_lee_como_mala_compra():
    """Mismo producto, mismo precio real: en julio $1.000 y en septiembre
    $1.030 con 3% de inflación acumulada NO es un sobreprecio."""
    s = database.SessionLocal()
    try:
        _compra(s, '111', date(2026, 7, 10), 1000, cantidad=10)
        _compra(s, '111', date(2026, 9, 10), 1030, cantidad=10)
        s.commit()

        crudo = _costear(s)['111']
        assert crudo['sobreprecio_total'] > 250       # ~$300: pura inflación

        factores = {'2026-07': 1.0300, '2026-09': 1.0}
        ajustado = _costear(s, factores=factores)['111']
        assert abs(ajustado['sobreprecio_total']) < 1  # en pesos de hoy, igual
    finally:
        s.close()


def test_marca_el_sobreprecio_real_aunque_se_ajuste():
    """El caso WEGOVY: +81% sobre el mejor precio propio, eso no es inflación."""
    s = database.SessionLocal()
    try:
        _compra(s, '111', date(2026, 7, 2), 227623, cantidad=2)
        _compra(s, '111', date(2026, 9, 2), 129745, cantidad=2)
        s.commit()
        factores = {'2026-07': 1.0298, '2026-09': 1.0}
        c = _costear(s, factores=factores)['111']
        cara = [x for x in c['compras'] if x['fecha'] == date(2026, 7, 2)][0]
        assert cara['sobre_pct'] > 70
        assert c['sobreprecio_total'] > 190000
    finally:
        s.close()


def test_una_diferencia_de_centavos_no_es_una_mala_compra():
    """Sin umbral, el total del trimestre pasa de $2,24M a $3,32M: casi todo en
    diferencias mínimas que no son una decisión de compra."""
    s = database.SessionLocal()
    try:
        _compra(s, '111', date(2026, 9, 1), 10000, cantidad=50)
        _compra(s, '111', date(2026, 9, 5), 10100, cantidad=50)   # +1%, bajo el umbral
        s.commit()
        assert _costear(s)['111']['sobreprecio_total'] == 0.0
        # Con el umbral en cero sí se contabiliza.
        c = _costear(s, umbral_pct=0)['111']
        assert abs(c['sobreprecio_total'] - 5000) < 1
    finally:
        s.close()


def test_una_sola_compra_no_genera_sobreprecio():
    """Sin otra compra contra la cual comparar, no se puede afirmar nada."""
    s = database.SessionLocal()
    try:
        _compra(s, '111', date(2026, 9, 1), 5000, cantidad=2)
        s.commit()
        c = _costear(s)['111']
        assert c['sobreprecio_total'] == 0.0
        assert c['mejor_precio'] == 5000
    finally:
        s.close()


def test_filtra_por_ean():
    s = database.SessionLocal()
    try:
        _compra(s, '111', date(2026, 9, 1), 100000)
        _compra(s, '222', date(2026, 9, 1), 200000)
        s.commit()
        assert set(_costear(s, eans=['111'])) == {'111'}
        assert _costear(s, eans=[]) == {}
    finally:
        s.close()


# ── Helpers ─────────────────────────────────────────────────────────────────

def test_margen():
    assert abs(margen(413519, 278269) - 32.7) < 0.1
    assert margen(0, 100) is None
    assert margen(1000, None) is None
    assert margen(21433, 22890) < 0        # se vende por debajo del costo


def test_confianza_por_antiguedad_del_costo():
    assert confianza_costo(3) == 'confiable'
    assert confianza_costo(15) == 'confiable'
    assert confianza_costo(21) == 'aceptable'
    assert confianza_costo(34) == 'optimista'
    assert confianza_costo(71) == 'no_decidir'
    assert confianza_costo(None) == 'sin_costo'
