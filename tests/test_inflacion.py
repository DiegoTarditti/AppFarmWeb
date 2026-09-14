"""Índice de inflación del costo de compra y ajuste a pesos de hoy.

El caso que motivó todo: sobre jul-sep 2026 el "sobreprecio" de las compras daba
$4.269.436 sin ajustar y $2.241.314 ajustado. La diferencia era inflación, no
malas compras.
"""
from datetime import date
from decimal import Decimal

import database
from database import IndiceInflacion, Invoice, InvoiceItem
from services.inflacion import ajustar, calcular_indice, factores_a_hoy


def _compra(session, ean, fecha, precio, cantidad=1, dto=None, tipo='FAC'):
    inv = Invoice(numero_factura=f'F-{ean}-{fecha}', fecha=fecha,
                  proveedor_razon='KELLERHOFF', proveedor_cuit='30539756490',
                  tipo_comprobante=tipo, total=precio * cantidad)
    session.add(inv)
    session.flush()
    session.add(InvoiceItem(factura_id=inv.id, codigo_barra=ean, descripcion=f'PROD {ean}',
                            cantidad=cantidad, precio_unitario=precio, dto=dto,
                            importe=precio * cantidad))


def test_calcula_la_variacion_del_mismo_producto_entre_meses():
    s = database.SessionLocal()
    try:
        # Tres productos, todos +10% de julio a agosto.
        for ean, p in (('111', 1000), ('222', 2000), ('333', 5000)):
            _compra(s, ean, date(2026, 7, 10), p)
            _compra(s, ean, date(2026, 8, 10), p * 1.10)
        s.commit()
        assert calcular_indice(s) == 1
        s.commit()
    finally:
        s.close()

    s = database.SessionLocal()
    try:
        fila = s.get(IndiceInflacion, '2026-08')
        assert fila is not None
        assert abs(float(fila.variacion_pct) - 10.0) < 0.01
        assert fila.pares == 3
        assert fila.origen == 'propio'
    finally:
        s.close()


def test_el_mix_no_ensucia_el_indice():
    """Un promedio simple de precios por mes daría +150% acá: en agosto se compró
    un producto caro que en julio no estaba. El índice tiene que dar +5%."""
    s = database.SessionLocal()
    try:
        _compra(s, '111', date(2026, 7, 10), 1000)
        _compra(s, '111', date(2026, 8, 10), 1050)      # el mismo producto: +5%
        _compra(s, '999', date(2026, 8, 10), 50000)     # caro, sólo en agosto
        s.commit()
        calcular_indice(s)
        s.commit()
        assert abs(float(s.get(IndiceInflacion, '2026-08').variacion_pct) - 5.0) < 0.01
    finally:
        s.close()


def test_ignora_renglones_a_costo_cero():
    """Psicotrópicos y probadores entran a $0 o con 99,99% de dto. Son compras
    reales pero no sirven para medir cuánto aumentó algo."""
    s = database.SessionLocal()
    try:
        _compra(s, '111', date(2026, 7, 10), 1000)
        _compra(s, '111', date(2026, 8, 10), 1020)              # +2%, el único válido
        _compra(s, '222', date(2026, 7, 10), 0.01)              # costo cero
        _compra(s, '222', date(2026, 8, 10), 3000)              # saltaría a +30.000.000%
        _compra(s, '333', date(2026, 7, 10), 5000, dto=Decimal('99.99'))
        _compra(s, '333', date(2026, 8, 10), 5000)
        s.commit()
        calcular_indice(s)
        s.commit()
        fila = s.get(IndiceInflacion, '2026-08')
        assert fila.pares == 1
        assert abs(float(fila.variacion_pct) - 2.0) < 0.01
    finally:
        s.close()


def test_no_pisa_un_periodo_cargado_a_mano():
    s = database.SessionLocal()
    try:
        s.add(IndiceInflacion(periodo='2026-08', variacion_pct=Decimal('7.5'),
                              origen='manual', nota='índice externo'))
        _compra(s, '111', date(2026, 7, 10), 1000)
        _compra(s, '111', date(2026, 8, 10), 1100)   # el automático daría +10%
        s.commit()
        calcular_indice(s)
        s.commit()
        fila = s.get(IndiceInflacion, '2026-08')
        assert float(fila.variacion_pct) == 7.5
        assert fila.origen == 'manual'
    finally:
        s.close()


# ── Factores a pesos de hoy ──────────────────────────────────────────────────

def test_factores_acumulan_hacia_atras():
    """Los números reales medidos: jul→ago +1,96%, ago→sep +1,00%.
    Con septiembre como base, julio tiene que dar 1,0100 * 1,0196 = 1,0298."""
    s = database.SessionLocal()
    try:
        s.add(IndiceInflacion(periodo='2026-08', variacion_pct=Decimal('1.96'), origen='propio'))
        s.add(IndiceInflacion(periodo='2026-09', variacion_pct=Decimal('1.00'), origen='propio'))
        s.commit()
        f = factores_a_hoy(s)
        assert abs(f['2026-09'] - 1.0000) < 0.0001
        assert abs(f['2026-08'] - 1.0100) < 0.0001
        assert abs(f['2026-07'] - 1.0298) < 0.0005   # julio hereda ago y sep
    finally:
        s.close()


def test_un_periodo_sin_indice_no_inventa_inflacion():
    s = database.SessionLocal()
    try:
        assert factores_a_hoy(s) == {}
        assert ajustar(1000, '2026-07', {}) == 1000
    finally:
        s.close()


def test_ajustar_lleva_el_monto_a_pesos_de_la_base():
    f = {'2026-07': 1.0298, '2026-08': 1.0100, '2026-09': 1.0}
    assert abs(ajustar(100000, '2026-07', f) - 102980) < 1
    assert ajustar(100000, '2026-09', f) == 100000
    assert ajustar(None, '2026-07', f) is None
