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
from routes.control_gondola import _aplicar_filtro, _filas_de_lab, _robot_por_pid


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
    # "Solo en depósito" ahora incluye el que no está en el robot.
    solo_dep = {f['nombre'] for f in _aplicar_filtro(filas, 'solo_deposito')}
    assert 'IRAZEM 10' in solo_dep
    assert 'OPTAMOX' not in solo_dep   # está en los dos → no es "solo depósito"


def test_filtro_solo_deposito_y_solo_robot():
    filas = [
        {'en_robot': 18, 'deposito': 12, 'total': 30},   # en los dos
        {'en_robot': 5, 'deposito': 0, 'total': 5},       # solo robot
        {'en_robot': 0, 'deposito': 8, 'total': 8},       # solo depósito
    ]
    assert len(_aplicar_filtro(filas, 'con_stock')) == 3
    assert _aplicar_filtro(filas, 'solo_robot') == [filas[1]]
    assert _aplicar_filtro(filas, 'solo_deposito') == [filas[2]]
