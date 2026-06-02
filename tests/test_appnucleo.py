"""Smoke tests de AppNúcleo en modo DEMO (sin DBs reales).

Verifica que el data layer agrega coherente y que las pantallas renderizan 200.
No toca `database`; AppNúcleo es standalone.
"""
import pytest

from appnucleo import data
from appnucleo.app import create_app


@pytest.fixture(autouse=True)
def _forzar_demo(monkeypatch):
    """Smoke tests deterministas: sin registro accesible → modo DEMO.
    Evita que el fan-out real (tabla sucursales → DBs de Render) se dispare en CI."""
    monkeypatch.setattr(data, '_farmacias_desde_sucursales', lambda: [])
    monkeypatch.delenv('NUCLEO_FARMACIAS', raising=False)
    data._CACHE.clear()


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


def test_heatmap_cobertura():
    g = data.cargar_grupo(force=True)
    h = data.heatmap_cobertura(g, n=8)
    assert len(h['labs']) <= 8
    assert h['slugs'] == [f['slug'] for f in g['farmacias']]
    # cada lab tiene una celda por farmacia y max ≥ cualquier celda
    for lab in h['labs']:
        assert set(h['celdas'][lab]) == set(h['slugs'])
        assert h['max'] >= max(h['celdas'][lab].values())
        assert 1 <= h['presentes'][lab] <= len(h['slugs'])
    # labs ordenados desc por total
    tots = [h['tot_lab'][l] for l in h['labs']]
    assert tots == sorted(tots, reverse=True)


def test_top_labs_por_farmacia():
    g = data.cargar_grupo(force=True)
    t = data.top_laboratorios_por_farmacia(g, n=10)
    assert len(t['labs']) <= 10
    assert t['slugs'] == [f['slug'] for f in g['farmacias']]
    # cada farmacia tiene un valor por lab (arrays alineados)
    for s in t['slugs']:
        assert len(t['data'][s]) == len(t['labs'])
    # labs ordenados desc por total del grupo (suma de farmacias)
    totales = [sum(t['data'][s][i] for s in t['slugs']) for i in range(len(t['labs']))]
    assert totales == sorted(totales, reverse=True)


def test_detalle_por_farmacia():
    g = data.cargar_grupo(force=True)
    det = data.detalle_por_farmacia(g, n_labs=5)
    assert set(det) == {f['slug'] for f in g['farmacias']}
    for d in det.values():
        assert len(d['serie']) == 12
        assert len(d['top_labs']) <= 5
        vals = [l['ventas_val'] for l in d['top_labs']]
        assert vals == sorted(vals, reverse=True)
        assert sum(d['rotacion'].values()) > 0


def test_paginas_renderizan():
    c = create_app().test_client()
    assert c.get('/').status_code == 200
    assert c.get('/ventas-multi').status_code == 200
    assert c.get('/ventas-multi?group_by=producto&q=demo').status_code == 200
    assert c.get('/comparar').status_code == 200
    assert c.get('/comparar?a=badia&b=pieri').status_code == 200
    assert c.get('/ping').status_code == 200
