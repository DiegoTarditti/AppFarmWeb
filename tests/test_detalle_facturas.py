"""Conteo de renglones de detalle por factura.

Existe para responder dos cosas de un vistazo en las listas de facturas: si la
factura tiene algo adentro, y si lo que se guardó coincide con lo que la factura
declara en su encabezado. El hueco entre esos dos números es la señal de que el
parseo falló, y por eso se muestran los dos y no sólo el conteo.
"""
from datetime import date

import pytest

import database
from helpers import conteo_items_por_factura, detalle_facturas, estado_detalle_factura


# ── estado_detalle_factura ──────────────────────────────────────────────────

@pytest.mark.parametrize('n_items,declarado,esperado', [
    (137, 137, 'ok'),
    (0,   137, 'vacia'),      # no se parseó el detalle
    (120, 137, 'parcial'),    # faltan renglones
    (150, 137, 'parcial'),    # sobran: el parser duplicó
    (12,  0,   'sin_ref'),    # hay items pero la factura no declara total
    (12,  None, 'sin_ref'),
    (0,   0,   'vacia'),
    (0,   None, 'vacia'),
])
def test_estados(n_items, declarado, esperado):
    assert estado_detalle_factura(n_items, declarado) == esperado


def test_una_nc_con_articulos_negativos_no_se_lee_como_sin_referencia():
    """Las NC se guardan con montos negativos (ver CLAUDE.md); si el parser
    también guardó los artículos en negativo, `abs` evita que caiga en
    'sin_ref' y marque como no verificable algo que sí coincide."""
    assert estado_detalle_factura(3, -3) == 'ok'


# ── conteo y armado ─────────────────────────────────────────────────────────

def _factura(session, numero, total_articulos=None):
    inv = database.Invoice(numero_factura=numero, fecha=date(2026, 8, 22),
                           tipo_comprobante='FAC', proveedor_razon='Kellerhoff',
                           proveedor_cuit='30-53975649-0', total=1000,
                           total_articulos=total_articulos)
    session.add(inv)
    session.flush()
    return inv


def _items(session, inv, n):
    for i in range(n):
        session.add(database.InvoiceItem(factura_id=inv.id, codigo_barra=f'{i:013d}',
                                         cantidad=1, descripcion=f'ITEM {i}'))
    session.flush()


def test_cuenta_los_renglones_de_cada_factura():
    with database.get_db() as session:
        a = _factura(session, 'A-1', 3)
        b = _factura(session, 'A-2', 5)
        _items(session, a, 3)
        _items(session, b, 2)
        session.commit()

        conteos = conteo_items_por_factura(session, [a.id, b.id])
        assert conteos == {a.id: 3, b.id: 2}


def test_la_factura_sin_renglones_no_aparece_en_el_conteo_pero_si_en_el_detalle():
    """Un GROUP BY no devuelve grupos vacíos: el caller tiene que resolver el 0,
    y es JUSTO el caso que la columna existe para mostrar."""
    with database.get_db() as session:
        vacia = _factura(session, 'A-3', 137)
        session.commit()

        assert conteo_items_por_factura(session, [vacia.id]) == {}

        d = detalle_facturas(session, [vacia])
        assert d[vacia.id] == {'renglones': 0, 'declarado': 137, 'estado': 'vacia'}


def test_detalle_marca_ok_parcial_y_vacia_en_el_mismo_lote():
    with database.get_db() as session:
        ok = _factura(session, 'A-4', 2)
        parcial = _factura(session, 'A-5', 9)
        vacia = _factura(session, 'A-6', 4)
        _items(session, ok, 2)
        _items(session, parcial, 3)
        session.commit()

        d = detalle_facturas(session, [ok, parcial, vacia])
        assert d[ok.id]['estado'] == 'ok'
        assert d[parcial.id] == {'renglones': 3, 'declarado': 9, 'estado': 'parcial'}
        assert d[vacia.id]['estado'] == 'vacia'


def test_sin_facturas_no_hace_query():
    with database.get_db() as session:
        assert conteo_items_por_factura(session, []) == {}
        assert detalle_facturas(session, []) == {}
        assert detalle_facturas(session, None) == {}


# ── Re-bajar del portal las facturas que quedaron sin detalle ───────────────

def test_una_factura_sin_renglones_no_se_saltea_en_el_proximo_sync():
    """El bug de 0046-00255798: encabezado perfecto, cero ítems, y volver a
    sincronizar no la arreglaba porque `skip_nros` la daba por completa."""
    from routes.kellerhoff_sync import nros_ya_completos

    with database.get_db() as session:
        completa = database.Invoice(
            numero_factura='00046-00279207', fecha=date(2026, 8, 22),
            tipo_comprobante='FAC', proveedor_razon='Kellerhoff',
            proveedor_cuit='30539756490', total=1000, total_articulos=2)
        vacia = database.Invoice(
            numero_factura='00046-00255798', fecha=date(2026, 8, 18),
            tipo_comprobante='FAC', proveedor_razon='Kellerhoff',
            proveedor_cuit='30539756490', total=4316113.99, total_articulos=0)
        session.add_all([completa, vacia])
        session.flush()
        session.add(database.InvoiceItem(factura_id=completa.id, codigo_barra='1',
                                         cantidad=1, descripcion='X'))
        session.commit()

        nros = nros_ya_completos(session)

    assert '00046-00279207' in nros, 'la que ya tiene detalle no debe re-bajarse'
    assert '00046-00255798' not in nros, (
        'la que quedó sin renglones tiene que volver a bajarse del portal')


def test_las_nc_financieras_siguen_salteandose():
    """No tienen detalle que bajar: son un renglón de cuenta corriente."""
    from routes.kellerhoff_sync import nros_ya_completos

    with database.get_db() as session:
        anunciante = database.Anunciante(nombre='LAB Y')
        session.add(anunciante)
        session.flush()
        session.add(database.PagoAjusteCC(
            proveedor_id=None, anunciante_id=anunciante.id, tipo='AJUSTE_NEG',
            fecha=date(2026, 8, 22), monto=100, numero_comprobante='00046-00063591'))
        session.commit()

        assert '00046-00063591' in nros_ya_completos(session)
