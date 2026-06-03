"""Tests del mapa de mercado por droga (services/mercado_drogas).

Cubre la agregación cross-lab por molécula y el estado del caché por lab,
sobre AnalisisIaCache + tablas ObServer en SQLite in-memory.
"""
import json

import database
from helpers import _ventana_12m_ym
from services import mercado_drogas


def _setup(s):
    s.add(database.ObsLaboratorio(observer_id=8, descripcion='Bagó'))
    s.add(database.ObsLaboratorio(observer_id=20, descripcion='Roemmers'))
    s.add(database.ObsLaboratorio(observer_id=154, descripcion='Raffo'))   # competencia
    s.add(database.ObsNombreDroga(observer_id=500, descripcion='DICLOFENAC'))
    s.add(database.AnalisisIaCache(
        clave='gap_ws_data:bago', titulo='Marcas Bagó (web)',
        texto=json.dumps({'marcas': [{'marca': 'Dioxaflex', 'molecula': 'diclofenac',
                                       'indicacion': 'AINE', 'top10_nacional': True,
                                       'match_pattern': 'DIOXAFLEX'}],
                          'fuentes': [{'titulo': 'x', 'url': 'http://x'}]}),
        creado_en=database.now_ar()))
    s.add(database.AnalisisIaCache(
        clave='gap_ws_data:roemmers', titulo='Marcas Roemmers (web)',
        texto=json.dumps({'marcas': [{'marca': 'Lotrial', 'molecula': 'enalapril',
                                      'indicacion': 'HTA', 'top10_nacional': True,
                                      'match_pattern': 'LOTRIAL'}], 'fuentes': []}),
        creado_en=database.now_ar()))
    # Diclofenac: Bagó vende la marca estrella (Dioxaflex, 30u) + Raffo vende
    # competencia genérica de la misma droga (70u). Lotrial NO tiene producto.
    s.add(database.ObsProducto(observer_id=100, descripcion='DIOXAFLEX 75 X 20',
                               laboratorio_observer=8, nombre_droga_observer=500, fecha_baja=None))
    s.add(database.ObsProducto(observer_id=101, descripcion='DICLOFENAC GENERICO X 20',
                               laboratorio_observer=154, nombre_droga_observer=500, fecha_baja=None))
    _desde, hasta = _ventana_12m_ym()
    s.add(database.ObsVentaMensual(id_farmacia=1, producto_observer=100,
                                   anio=hasta // 100, mes=hasta % 100, unidades=30, monto=600))
    s.add(database.ObsVentaMensual(id_farmacia=1, producto_observer=101,
                                   anio=hasta // 100, mes=hasta % 100, unidades=70, monto=1400))
    s.commit()


def test_labs_cacheados_estado():
    s = database.SessionLocal()
    _setup(s)
    est = mercado_drogas.labs_cacheados_estado(s)
    s.close()
    by = {e['nombre']: e for e in est}
    assert by['Bagó']['cacheado'] and by['Bagó']['n_marcas'] == 1 and by['Bagó']['en_observer']
    assert by['Bagó']['edad_dias'] == 0 and by['Bagó']['consultado_en']
    assert by['Roemmers']['cacheado']
    # un lab no creado en ObServer → en_observer False, sin consultar
    assert by['Gador']['en_observer'] is False and by['Gador']['cacheado'] is False
    # todos los labs habilitados están listados
    assert len(est) == len(mercado_drogas.referencia_mercado.LABS_GAP_WEBSEARCH)


def test_comparativa_por_droga():
    s = database.SessionLocal()
    _setup(s)
    comp = mercado_drogas.comparativa_mercado_por_droga(s)
    s.close()
    # Solo aparece DICLOFENAC (donde vendo la marca estrella). Lotrial/enalapril
    # no tiene producto → no se mapea a droga → fuera (caso conocido).
    assert len(comp) == 1
    d = comp[0]
    assert d['droga'] == 'DICLOFENAC' and d['droga_id'] == 500
    assert d['n_labs'] == 2 and d['total_u12m'] == 100
    # ordenado por u12m desc: Raffo (70, competencia genérica) primero, Bagó (30, estrella)
    assert d['labs'][0]['lab'] == 'Raffo' and d['labs'][0]['es_estrella'] is False
    bago = next(l for l in d['labs'] if l['lab'] == 'Bagó')
    assert bago['es_estrella'] and bago['marca_estrella'] == 'Dioxaflex'
    assert bago['top10_nacional'] and bago['u12m'] == 30 and bago['share_pct'] == 30.0
    # la marca líder de mercado (Bagó/Dioxaflex) captura solo 30% → gap de share
    assert d['lider_marca'] == 'Dioxaflex' and d['lider_share_pct'] == 30.0 and d['gap'] is True
