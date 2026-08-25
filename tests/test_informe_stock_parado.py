"""Informe de stock parado: plata inmovilizada en lo que no se vende.

Existe porque el análisis de lo que no se mueve estaba repartido en cinco
lugares que no se hablan, y ninguno daba el total de la farmacia: cadencias es
por laboratorio, el robot sólo ve lo que está adentro de la máquina, y
`ProductAnalytics.sin_mov_60d` se calculaba para todo el catálogo sin que
ninguna pantalla lo mostrara.
"""
import json

import database
# El fixture con TODOS los routers registrados vive en el smoke; se reusa en vez
# de duplicar el armado de la app.
from tests.test_smoke_routes import smoke_app, smoke_client  # noqa: F401


def _pa(session, codigo, desc, ventas, stock=10, precio=100.0, lab='BAGO'):
    session.add(database.ProductAnalytics(
        codigo_barra=codigo, descripcion=desc, laboratorio=lab, stock=stock,
        avg_monthly=sum(ventas) / 12.0, precio_pvp=precio,
        ventas_json=json.dumps(ventas)))


# ── meses sin vender ────────────────────────────────────────────────────────

def _meses(ventas):
    from routes.informes import meses_sin_vender
    return meses_sin_vender(ventas)


def test_vendio_el_ultimo_mes_no_esta_parado():
    assert _meses([0] * 11 + [3]) == 0


def test_cuenta_los_meses_desde_la_ultima_venta():
    # vendió hace 3 meses: los últimos 3 slots están en cero
    assert _meses([5] * 9 + [0, 0, 0]) == 3


def test_sin_ninguna_venta_en_doce_meses_devuelve_12():
    """El "clavo absoluto": no es lo mismo que uno que se dejó de vender."""
    assert _meses([0] * 12) == 12


def test_sin_datos_de_ventas_se_asume_lo_peor():
    assert _meses([]) == 12
    assert _meses(None) == 12


# ── la pantalla ─────────────────────────────────────────────────────────────

def test_muestra_solo_lo_que_tiene_stock(smoke_client):
    """Sin stock no hay plata parada, por más que no venda."""
    with database.get_db() as s:
        _pa(s, '111', 'CON STOCK', [0] * 12, stock=5)
        _pa(s, '222', 'SIN STOCK', [0] * 12, stock=0)
        s.commit()

    html = smoke_client.get('/informes/stock-parado').data.decode('utf-8')
    assert 'CON STOCK' in html
    assert 'SIN STOCK' not in html


def test_el_umbral_de_meses_filtra(smoke_client):
    with database.get_db() as s:
        _pa(s, '111', 'PARADO HACE 8', [4] * 4 + [0] * 8)
        _pa(s, '222', 'PARADO HACE 3', [4] * 9 + [0] * 3)
        s.commit()

    seis = smoke_client.get('/informes/stock-parado?meses=6').data.decode('utf-8')
    assert 'PARADO HACE 8' in seis
    assert 'PARADO HACE 3' not in seis

    dos = smoke_client.get('/informes/stock-parado?meses=2').data.decode('utf-8')
    assert 'PARADO HACE 8' in dos
    assert 'PARADO HACE 3' in dos


def test_filtra_por_laboratorio(smoke_client):
    with database.get_db() as s:
        _pa(s, '111', 'DE BAGO', [0] * 12, lab='BAGO')
        _pa(s, '222', 'DE ROEMMERS', [0] * 12, lab='ROEMMERS')
        s.commit()

    html = smoke_client.get('/informes/stock-parado?lab=BAGO').data.decode('utf-8')
    assert 'DE BAGO' in html
    assert 'DE ROEMMERS' not in html


def test_la_busqueda_es_multitoken_and(smoke_client):
    """Diego fue explícito: "400 susp" tiene que pedir las dos cosas."""
    with database.get_db() as s:
        _pa(s, '111', 'AMOXIDAL 400 SUSPENSION', [0] * 12)
        _pa(s, '222', 'AMOXIDAL 500 COMPRIMIDOS', [0] * 12)
        _pa(s, '333', 'IBUPIRAC 400 COMPRIMIDOS', [0] * 12)
        s.commit()

    html = smoke_client.get('/informes/stock-parado?q=amoxidal+400').data.decode('utf-8')
    assert 'AMOXIDAL 400 SUSPENSION' in html
    assert 'AMOXIDAL 500' not in html, 'el token 400 tiene que descartar la 500'
    assert 'IBUPIRAC' not in html, 'el token amoxidal tiene que descartar ibupirac'


def test_los_tokens_no_dependen_del_orden(smoke_client):
    with database.get_db() as s:
        _pa(s, '111', 'AMOXIDAL 400 SUSPENSION', [0] * 12)
        s.commit()

    html = smoke_client.get('/informes/stock-parado?q=400+amoxidal').data.decode('utf-8')
    assert 'AMOXIDAL 400 SUSPENSION' in html


def test_ordena_por_plata_parada_por_defecto(smoke_client):
    with database.get_db() as s:
        _pa(s, '111', 'BARATO PARADO', [0] * 12, stock=1, precio=10.0)
        _pa(s, '222', 'CARO PARADO', [0] * 12, stock=100, precio=500.0)
        s.commit()

    html = smoke_client.get('/informes/stock-parado').data.decode('utf-8')
    assert html.index('CARO PARADO') < html.index('BARATO PARADO')


def test_se_puede_ordenar_alfabeticamente(smoke_client):
    with database.get_db() as s:
        _pa(s, '111', 'ZZZ CARO', [0] * 12, stock=100, precio=500.0)
        _pa(s, '222', 'AAA BARATO', [0] * 12, stock=1, precio=10.0)
        s.commit()

    html = smoke_client.get('/informes/stock-parado?orden=nombre').data.decode('utf-8')
    assert html.index('AAA BARATO') < html.index('ZZZ CARO')
