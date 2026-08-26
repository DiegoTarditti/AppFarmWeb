"""Control de stock por laboratorio (fuera del robot).

Fija el bug del split robot/depósito: en la fila de Rowa `al_deposito` es el
"Sacar" (cuánto mover al depósito), NO el stock de depósito. Usar al_deposito
daba depósito 0 y la suma no cerraba (robot 18 + 0 ≠ total 30). El stock real es
`stock_deposito`.
"""
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import database
import routes.rowa
from database import ObsLaboratorio, ObsProducto, ObsStock
from routes.control_gondola import (
    _aplicar_filtro,
    _filas_de_lab,
    _normalizar_filtro,
    _robot_por_pid,
)


def _fila(**kw):
    base = dict(producto_observer=1, cantidad=0, stock_total=0,
                stock_deposito=0, al_deposito=0, nombre_obs='', nombre='',
                laboratorio='')
    base.update(kw)
    return SimpleNamespace(**base)


def test_deposito_sale_de_stock_deposito_no_de_al_deposito(monkeypatch):
    # OPTAMOX DUO: en robot 18, depósito 12, total 30. al_deposito (Sacar) = 0.
    fila = _fila(producto_observer=42, cantidad=18, stock_total=30,
                 stock_deposito=12, al_deposito=0, nombre_obs='OPTAMOX DUO',
                 laboratorio='Roemmers')
    monkeypatch.setattr(routes.rowa, '_cargar', lambda: {'filas': [fila]})

    robot_map, sin_robot = _robot_por_pid()
    assert sin_robot is False
    en_robot, total, deposito, nombre, lab = robot_map[42]
    assert en_robot == 18
    assert total == 30
    assert deposito == 12          # NO 0 (antes tomaba al_deposito)
    assert en_robot + deposito == total   # la suma cierra


def test_robot_no_responde_degrada(monkeypatch):
    def _boom():
        raise OSError('robot off')
    monkeypatch.setattr(routes.rowa, '_cargar', _boom)
    robot_map, sin_robot = _robot_por_pid()
    assert robot_map == {}
    assert sin_robot is True


def _session_mem():
    eng = create_engine('sqlite:///:memory:')
    database.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def test_universo_incluye_deposito_aunque_no_este_en_robot():
    """El bug: solo mostraba lo que figuraba en el robot. El universo debe ser
    TODO lo que tiene stock; 'en robot' sale de los packs reales, no del cupo."""
    s = _session_mem()
    s.add(ObsLaboratorio(observer_id=1, descripcion='Roemmers'))
    # Producto con stock en depósito, NO presente en el robot (no en robot_map).
    s.add(ObsProducto(observer_id=10, descripcion='IRAZEM 10', laboratorio_observer=1))
    s.add(ObsStock(id_farmacia=1, producto_observer=10, stock_actual=5))
    # Producto en robot + depósito (OPTAMOX): total 30, robot 18 → depósito 12.
    s.add(ObsProducto(observer_id=20, descripcion='OPTAMOX', laboratorio_observer=1))
    s.add(ObsStock(id_farmacia=1, producto_observer=20, stock_actual=30))
    s.commit()

    robot_map = {20: (18, 30, 12, 'OPTAMOX', 'Roemmers')}   # solo OPTAMOX en el robot
    filas = _filas_de_lab(s, robot_map, 'Roemmers')
    by = {f['nombre']: f for f in filas}

    # El que NO está en el robot aparece igual, todo en depósito.
    assert by['IRAZEM 10']['en_robot'] == 0
    assert by['IRAZEM 10']['deposito'] == 5
    assert by['IRAZEM 10']['total'] == 5
    # El del robot: split correcto (18 + 12 = 30).
    o = by['OPTAMOX']
    assert (o['en_robot'], o['deposito'], o['total']) == (18, 12, 30)
    # "En depósito" (inclusivo) incluye al que no está en el robot Y también a
    # OPTAMOX (que tiene depósito además del robot) → nada queda afuera.
    en_dep = {f['nombre'] for f in _aplicar_filtro(filas, 'en_deposito')}
    assert 'IRAZEM 10' in en_dep
    assert 'OPTAMOX' in en_dep


def test_caso_uno_por_columna_y_nada_afuera():
    """El caso que pidió Lisandro: con stock con al menos uno en cada columna, y
    ninguno afuera del control. Filtros INCLUSIVOS: el que está en los dos aparece
    en las dos."""
    ambos = {'nombre': 'AMBOS', 'en_robot': 18, 'deposito': 12, 'total': 30}
    solo_rob = {'nombre': 'ROBOT', 'en_robot': 5, 'deposito': 0, 'total': 5}
    solo_dep = {'nombre': 'DEPOSITO', 'en_robot': 0, 'deposito': 8, 'total': 8}
    filas = [ambos, solo_rob, solo_dep]

    con_stock = _aplicar_filtro(filas, 'con_stock')
    en_robot = _aplicar_filtro(filas, 'en_robot')
    en_dep = _aplicar_filtro(filas, 'en_deposito')

    # Con stock: los 3, y CADA columna tiene al menos uno.
    assert len(con_stock) == 3
    assert any(f['en_robot'] > 0 for f in con_stock)
    assert any(f['deposito'] > 0 for f in con_stock)
    # Inclusivos: el de ambos entra en las dos vistas.
    assert {f['nombre'] for f in en_robot} == {'AMBOS', 'ROBOT'}
    assert {f['nombre'] for f in en_dep} == {'AMBOS', 'DEPOSITO'}
    # Unión = con stock → NADA queda afuera del control.
    union = {f['nombre'] for f in en_robot} | {f['nombre'] for f in en_dep}
    assert union == {'AMBOS', 'ROBOT', 'DEPOSITO'}
    # "En robot y depósito": solo los que tienen stock en los DOS.
    en_ambos = _aplicar_filtro(filas, 'en_ambos')
    assert {f['nombre'] for f in en_ambos} == {'AMBOS'}


def test_alias_de_urls_viejas():
    # Bookmarks viejos (?filtro=solo_*) siguen andando → mapean a los inclusivos.
    assert _normalizar_filtro('solo_robot') == 'en_robot'
    assert _normalizar_filtro('solo_deposito') == 'en_deposito'
    assert _normalizar_filtro('con_stock') == 'con_stock'
    assert _normalizar_filtro('cualquier_cosa') == 'con_stock'
