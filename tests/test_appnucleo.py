"""Smoke tests de AppNúcleo en modo DEMO (sin DBs reales).

Verifica que el data layer agrega coherente y que las pantallas renderizan 200.
No toca `database`; AppNúcleo es standalone.
"""
from appnucleo import data
from appnucleo.app import create_app


def test_demo_grupo_carga():
    g = data.cargar_grupo(force=True)
    assert g['demo'] is True
    assert len(g['farmacias']) == 4
    assert all(f['ok'] and f['rows'] for f in g['farmacias'])


def test_kpis_coherentes():
    g = data.cargar_grupo(force=True)
    tot, por_far = data.kpis(g)
    assert tot['skus'] == sum(p['skus'] for p in por_far)
    assert tot['unidades'] == sum(p['unidades'] for p in por_far)
    assert tot['ventas_val'] > 0 and tot['stock_val'] > 0


def test_tendencia_y_labels():
    g = data.cargar_grupo(force=True)
    series = data.tendencia(g)
    assert len(series) == 4
    assert all(len(s['data']) == 12 for s in series)
    assert len(data.meses_labels()) == 12


def test_ventas_multi_pivot():
    g = data.cargar_grupo(force=True)
    pivot = data.ventas_multi(g, group_by='laboratorio')
    assert pivot['filas']
    f0 = pivot['filas'][0]
    # total = suma de las columnas por farmacia
    assert f0['tot'][0] == sum(f0['far'][s][0] for s in pivot['slugs'])
    # ordenado desc por $
    vals = [f['tot'][1] for f in pivot['filas']]
    assert vals == sorted(vals, reverse=True)


def test_paginas_renderizan():
    c = create_app().test_client()
    assert c.get('/').status_code == 200
    assert c.get('/ventas-multi').status_code == 200
    assert c.get('/ventas-multi?group_by=producto&q=demo').status_code == 200
    assert c.get('/ping').status_code == 200
