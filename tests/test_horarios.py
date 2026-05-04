"""Tests para services.horarios — proximo_cierre y horarios_por_dia."""
from datetime import datetime

import pytest

import database
from services.horarios import _parse_hhmm, horarios_por_dia, proximo_cierre


@pytest.fixture
def proveedor():
    """Crea un proveedor con horarios L-V 09:00, 14:00, 19:00."""
    with database.get_db() as s:
        p = database.Provider(razon_social='Test Drog', tipo='drogueria')
        s.add(p)
        s.flush()
        for dia in range(5):  # 0..4 = Lun..Vie
            for hora in ('09:00', '14:00', '19:00'):
                s.add(database.ProveedorHorarioReparto(
                    proveedor_id=p.id, dia_semana=dia, hora=hora, activo=True))
        s.commit()
        return p.id


def test_parse_hhmm_basico():
    assert _parse_hhmm('09:30') == (9, 30)
    assert _parse_hhmm('00:00') == (0, 0)
    assert _parse_hhmm('23:59') == (23, 59)


def test_parse_hhmm_invalido():
    assert _parse_hhmm('') is None
    assert _parse_hhmm(None) is None
    assert _parse_hhmm('25:00') is None
    assert _parse_hhmm('09:60') is None
    assert _parse_hhmm('foo') is None


def test_proximo_cierre_mismo_dia(proveedor):
    """Lunes a las 08:00 → próximo cierre es 09:00 mismo día."""
    with database.get_db() as s:
        # 2026-01-05 es lunes
        ahora = datetime(2026, 1, 5, 8, 0, 0)
        res = proximo_cierre(s, proveedor, ahora=ahora)
        assert res is not None
        assert res['hora_str'] == '09:00'
        assert res['fecha'].date() == ahora.date()
        assert res['falta_segundos'] == 60 * 60  # 1 hora


def test_proximo_cierre_pasa_al_siguiente_slot(proveedor):
    """Lunes 10:00 (después de 09:00) → próximo es 14:00."""
    with database.get_db() as s:
        ahora = datetime(2026, 1, 5, 10, 0, 0)
        res = proximo_cierre(s, proveedor, ahora=ahora)
        assert res['hora_str'] == '14:00'


def test_proximo_cierre_salta_a_lunes_siguiente(proveedor):
    """Viernes 20:00 (después del último slot) → próximo es lunes 09:00."""
    with database.get_db() as s:
        # 2026-01-09 es viernes
        ahora = datetime(2026, 1, 9, 20, 0, 0)
        res = proximo_cierre(s, proveedor, ahora=ahora)
        assert res['hora_str'] == '09:00'
        # Debe ser el lunes siguiente (3 días después de viernes 20:00)
        assert res['fecha'].weekday() == 0  # lunes


def test_proximo_cierre_sin_slots():
    with database.get_db() as s:
        p = database.Provider(razon_social='Sin horarios', tipo='drogueria')
        s.add(p)
        s.commit()
        assert proximo_cierre(s, p.id) is None


def test_proximo_cierre_solo_slots_inactivos():
    """Si todos los slots están activo=False, devuelve None."""
    with database.get_db() as s:
        p = database.Provider(razon_social='Inactivos', tipo='drogueria')
        s.add(p)
        s.flush()
        s.add(database.ProveedorHorarioReparto(
            proveedor_id=p.id, dia_semana=0, hora='09:00', activo=False))
        s.commit()
        assert proximo_cierre(s, p.id) is None


def test_proximo_cierre_ignora_horas_invalidas(proveedor):
    """Una fila con hora inválida (ej. '25:99') no debe romper el cálculo."""
    with database.get_db() as s:
        s.add(database.ProveedorHorarioReparto(
            proveedor_id=proveedor, dia_semana=0, hora='99:99', activo=True))
        s.commit()
        ahora = datetime(2026, 1, 5, 8, 0, 0)
        res = proximo_cierre(s, proveedor, ahora=ahora)
        assert res is not None
        assert res['hora_str'] == '09:00'  # ignora la fila inválida


def test_horarios_por_dia_devuelve_matriz(proveedor):
    with database.get_db() as s:
        m = horarios_por_dia(s, proveedor)
        assert set(m.keys()) == {0, 1, 2, 3, 4, 5, 6}
        assert m[0] == ['09:00', '14:00', '19:00']
        assert m[5] == []  # sábado sin slots
        assert m[6] == []  # domingo sin slots


def test_horarios_por_dia_orden_ascendente():
    """Si los slots se cargan en orden caótico, salen ordenados."""
    with database.get_db() as s:
        p = database.Provider(razon_social='Orden', tipo='drogueria')
        s.add(p)
        s.flush()
        for hora in ('19:00', '09:00', '14:00'):
            s.add(database.ProveedorHorarioReparto(
                proveedor_id=p.id, dia_semana=0, hora=hora, activo=True))
        s.commit()
        m = horarios_por_dia(s, p.id)
        assert m[0] == ['09:00', '14:00', '19:00']
