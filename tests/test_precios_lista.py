"""Histórico de PVP de ObServer y patrón de aumento por laboratorio."""
from datetime import datetime
from decimal import Decimal

import database
from database import ObsPrecioListaHist, ObsProducto
from services.precios_lista import patron_por_laboratorio, registrar_cambios


def _producto(session, oid, precio, vigencia, lab=1):
    p = session.get(ObsProducto, oid)
    if p is None:
        p = ObsProducto(observer_id=oid, descripcion=f'PROD {oid}')
        session.add(p)
    p.precio_lista = Decimal(str(precio))
    p.precio_lista_fecha_vigencia = vigencia
    p.laboratorio_observer = lab
    session.flush()
    return p


def _hist(session):
    return session.query(ObsPrecioListaHist).order_by(ObsPrecioListaHist.id).all()


def test_registra_el_precio_vigente_la_primera_vez():
    s = database.SessionLocal()
    try:
        _producto(s, 1, 1000, datetime(2026, 9, 20))
        s.commit()
        assert registrar_cambios(s) == 1
        s.commit()
        h = _hist(s)[0]
        assert float(h.precio_lista) == 1000
        assert h.fecha_vigencia == datetime(2026, 9, 20)
        assert h.variacion_pct is None      # no hay contra qué comparar
    finally:
        s.close()


def test_no_duplica_si_no_cambio_nada():
    """Idempotente: correrlo dos veces seguidas no escribe de nuevo."""
    s = database.SessionLocal()
    try:
        _producto(s, 1, 1000, datetime(2026, 9, 20))
        s.commit()
        registrar_cambios(s)
        s.commit()
        assert registrar_cambios(s) == 0
        s.commit()
        assert len(_hist(s)) == 1
    finally:
        s.close()


def test_registra_el_cambio_y_calcula_la_variacion():
    s = database.SessionLocal()
    try:
        _producto(s, 1, 1000, datetime(2026, 8, 20))
        s.commit()
        registrar_cambios(s)
        s.commit()

        _producto(s, 1, 1100, datetime(2026, 9, 20))   # +10%
        s.commit()
        assert registrar_cambios(s) == 1
        s.commit()

        filas = _hist(s)
        assert len(filas) == 2
        assert float(filas[1].precio_lista) == 1100
        assert abs(float(filas[1].variacion_pct) - 10.0) < 0.01
    finally:
        s.close()


def test_ignora_la_fecha_centinela():
    """Los productos sin precio real arrastran una fecha de 1902: no son cambios."""
    s = database.SessionLocal()
    try:
        _producto(s, 1, 500, datetime(1902, 6, 3))
        _producto(s, 2, 900, datetime(2026, 9, 1))
        s.commit()
        assert registrar_cambios(s) == 1
        s.commit()
        assert [h.producto_observer for h in _hist(s)] == [2]
    finally:
        s.close()


def test_guarda_el_laboratorio_del_momento():
    """Desnormalizado a propósito: si el producto se reasigna después, la
    historia no se reescribe."""
    s = database.SessionLocal()
    try:
        _producto(s, 1, 1000, datetime(2026, 8, 1), lab=7)
        s.commit()
        registrar_cambios(s)
        s.commit()

        _producto(s, 1, 1200, datetime(2026, 9, 1), lab=9)   # cambió de lab
        s.commit()
        registrar_cambios(s)
        s.commit()

        assert [h.laboratorio_observer for h in _hist(s)] == [7, 9]
    finally:
        s.close()


# ── Patrón por laboratorio ──────────────────────────────────────────────────

def test_detecta_el_dia_del_mes_que_concentra_los_aumentos():
    """El caso Gador: el 95% de sus productos con vigencia el mismo día."""
    s = database.SessionLocal()
    try:
        for i in range(19):
            _producto(s, 100 + i, 1000 + i, datetime(2026, 8, 31), lab=5)
        _producto(s, 200, 2000, datetime(2026, 8, 14), lab=5)   # el que se corre
        s.commit()
        registrar_cambios(s)
        s.commit()

        patron = patron_por_laboratorio(s, minimo_cambios=10)
        assert len(patron) == 1
        assert patron[0]['dia_top'] == 31
        assert patron[0]['cambios'] == 20
        assert patron[0]['pct'] == 95.0
    finally:
        s.close()


def test_avisa_sobre_cuantos_meses_se_midio():
    """Con un solo mes el resultado es una hipotesis, no un patron: `meses` lo dice."""
    s = database.SessionLocal()
    try:
        for i in range(25):
            _producto(s, 300 + i, 500 + i, datetime(2026, 9, 20), lab=6)
        s.commit()
        registrar_cambios(s)
        s.commit()
        assert patron_por_laboratorio(s, minimo_cambios=10)[0]['meses'] == 1
    finally:
        s.close()


def test_ignora_laboratorios_con_pocos_cambios():
    s = database.SessionLocal()
    try:
        _producto(s, 1, 100, datetime(2026, 9, 5), lab=1)
        _producto(s, 2, 200, datetime(2026, 9, 5), lab=1)
        s.commit()
        registrar_cambios(s)
        s.commit()
        assert patron_por_laboratorio(s, minimo_cambios=20) == []
    finally:
        s.close()
