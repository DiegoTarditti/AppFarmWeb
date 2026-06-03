"""Tests del gap de marcas con web search (PASO 2 — cruce local) y helpers.

El PASO 1 (web search a la API de Claude) no se testea acá (requiere red/API);
se cubre el cruce de marcas vs ventas y la resolución de lab por nombre, que es
la lógica determinista sobre la DB.
"""
import database
import helpers
import referencia_mercado
from helpers import _ventana_12m_ym


def _lab(observer_id, descripcion):
    return database.ObsLaboratorio(observer_id=observer_id, descripcion=descripcion)


def test_resolver_obs_lab_por_nombre():
    s = database.SessionLocal()
    s.add(_lab(8, 'Bagó'))                  # así viene en obs_laboratorios real
    s.add(_lab(78, 'Laboratorios Gador'))   # prefijo plural → debe quitarse
    s.commit()
    # case-insensitive + sin acentos
    assert helpers.resolver_obs_lab_por_nombre(s, 'Bagó') == 8
    assert helpers.resolver_obs_lab_por_nombre(s, 'BAGO') == 8
    # prefijo "Laboratorios" (plural) normalizado
    assert helpers.resolver_obs_lab_por_nombre(s, 'Gador') == 78
    assert helpers.resolver_obs_lab_por_nombre(s, 'Roemmers') is None
    assert helpers.resolver_obs_lab_por_nombre(s, '') is None
    s.close()


def test_cruzar_marcas_vs_ventas():
    s = database.SessionLocal()
    s.add(_lab(10, 'Bagó'))
    s.add(database.ObsProducto(observer_id=100, descripcion='LOTRIAL 10 MG X 30 COMP',
                               laboratorio_observer=10, fecha_baja=None))
    desde, hasta = _ventana_12m_ym()
    s.add(database.ObsVentaMensual(id_farmacia=1, producto_observer=100,
                                   anio=hasta // 100, mes=hasta % 100,
                                   unidades=50, monto=1000))
    s.commit()

    marcas = [
        {'marca': 'Inexistente', 'molecula': 'x', 'top10_nacional': False, 'match_pattern': 'ZZZNOPE'},
        {'marca': 'Lotrial', 'molecula': 'enalapril', 'indicacion': 'HTA',
         'top10_nacional': True, 'match_pattern': 'LOTRIAL'},
    ]
    data = helpers.cruzar_marcas_vs_ventas(s, 10, marcas, 'Bagó', nota='nota X')
    s.close()

    assert data['nombre_lab'] == 'Bagó' and data['nota'] == 'nota X'
    assert data['total_u12m'] == 50
    # top10 primero (Lotrial), luego el resto
    assert data['marcas'][0]['marca'] == 'Lotrial'
    lotrial = data['marcas'][0]
    assert lotrial['n_productos'] == 1 and lotrial['u12m'] == 50 and lotrial['vende'] is True
    inexistente = data['marcas'][1]
    assert inexistente['n_productos'] == 0 and inexistente['u12m'] == 0 and inexistente['vende'] is False


def test_cruce_ignora_productos_de_baja_y_otro_lab():
    s = database.SessionLocal()
    s.add(_lab(10, 'Bagó'))
    # mismo nombre pero dado de baja → no cuenta
    s.add(database.ObsProducto(observer_id=101, descripcion='LOTRIAL BAJA',
                               laboratorio_observer=10, fecha_baja=database.now_ar()))
    # mismo nombre pero de otro lab → no cuenta
    s.add(database.ObsProducto(observer_id=102, descripcion='LOTRIAL OTRO LAB',
                               laboratorio_observer=99, fecha_baja=None))
    s.commit()
    data = helpers.cruzar_marcas_vs_ventas(
        s, 10, [{'marca': 'Lotrial', 'match_pattern': 'LOTRIAL', 'top10_nacional': True}], 'Bagó')
    s.close()
    assert data['marcas'][0]['n_productos'] == 0


def test_labs_gap_disponibles():
    s = database.SessionLocal()
    s.add(_lab(8, 'Bagó'))
    s.add(_lab(20, 'Roemmers'))
    s.commit()
    disp = referencia_mercado.labs_gap_disponibles(s)
    s.close()
    by_nombre = {d['nombre']: d['observer_id'] for d in disp}
    assert by_nombre.get('Bagó') == 8
    assert by_nombre.get('Roemmers') == 20
    # los no cargados (Elea, Gador, ...) no aparecen
    assert 'Elea' not in by_nombre
    # todos los devueltos están en la lista habilitada
    assert all(d['nombre'] in referencia_mercado.LABS_GAP_WEBSEARCH for d in disp)
