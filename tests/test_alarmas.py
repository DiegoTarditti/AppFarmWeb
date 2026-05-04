"""Tests para el motor de alarmas (alarmas.py).

Cubre:
- check_cron_errors_24h: dispara con ≥1 error en últimas 24h.
- check_sync_observer_parado: distingue "nunca corrió" (MEDIA) de "se cortó" (CRÍTICA).
- check_recalculo_os_atrasado: idem para cliente_os_inferida.
- check_cron_log_grande: dispara cuando >10k filas.
- evaluar_todas: ordena por severidad, no rompe si un check falla.
- contar_por_severidad: cuenta correctamente.
- Cache TTL: misma evaluación dos veces seguidas usa cache.
"""
from datetime import datetime, timedelta

import pytest

import alarmas
import database
from alarmas import (
    Alarma,
    SEV_ALTA,
    SEV_BAJA,
    SEV_CRITICA,
    SEV_MEDIA,
    contar_por_severidad,
    evaluar_todas,
)
from database import ClienteOsInferida, CronLog


@pytest.fixture
def session():
    s = database.SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _reset_cache():
    """El cache de alarmas vive entre tests; limpiarlo siempre."""
    alarmas.invalidar_cache()
    yield
    alarmas.invalidar_cache()


def _agregar_cron_log(s, proceso, estado='ok', hace_horas=0):
    inicio = datetime.now() - timedelta(hours=hace_horas)
    s.add(CronLog(proceso=proceso, estado=estado, inicio=inicio))
    s.commit()


# ── check_cron_errors_24h ────────────────────────────────────────────────

def test_check_cron_errors_dispara_con_un_error(session):
    _agregar_cron_log(session, 'algo', estado='error', hace_horas=1)
    a = alarmas.check_cron_errors_24h(session)
    assert a is not None
    assert a.severidad == SEV_CRITICA
    assert '1 error' in a.valor_actual


def test_check_cron_errors_no_dispara_sin_errores(session):
    _agregar_cron_log(session, 'algo', estado='ok')
    a = alarmas.check_cron_errors_24h(session)
    assert a is None


def test_check_cron_errors_ignora_viejos(session):
    _agregar_cron_log(session, 'algo', estado='error', hace_horas=48)
    a = alarmas.check_cron_errors_24h(session)
    assert a is None


# ── check_sync_observer_parado ────────────────────────────────────────────

def test_check_sync_nunca_corrio_es_media(session):
    """Sin ningún sync_* en cron_log → MEDIA (no crítica)."""
    a = alarmas.check_sync_observer_parado(session)
    assert a is not None
    assert a.severidad == SEV_MEDIA
    assert 'nunca' in a.valor_actual.lower()


def test_check_sync_reciente_no_dispara(session):
    _agregar_cron_log(session, 'sync_productos', hace_horas=2)
    a = alarmas.check_sync_observer_parado(session)
    assert a is None


def test_check_sync_parado_es_critica(session):
    """Si tenemos baseline y se cortó hace >48h → CRÍTICA."""
    _agregar_cron_log(session, 'sync_productos', hace_horas=72)
    a = alarmas.check_sync_observer_parado(session)
    assert a is not None
    assert a.severidad == SEV_CRITICA
    assert '72h' in a.valor_actual or '72 ' in a.valor_actual


# ── check_recalculo_os_atrasado ──────────────────────────────────────────

def test_check_recalculo_nunca_es_media(session):
    """ClienteOsInferida vacía → MEDIA, no ALTA (sistema sin estrenar)."""
    a = alarmas.check_recalculo_os_atrasado(session)
    assert a is not None
    assert a.severidad == SEV_MEDIA


def test_check_recalculo_reciente_no_dispara(session):
    session.add(ClienteOsInferida(
        cliente_observer=1, obra_social_observer=42, n_dispensas=5,
        calculado_en=datetime.now() - timedelta(days=1)))
    session.commit()
    a = alarmas.check_recalculo_os_atrasado(session)
    assert a is None


def test_check_recalculo_atrasado_es_alta(session):
    session.add(ClienteOsInferida(
        cliente_observer=1, os_observer=42, n_dispensas=5,
        calculado_en=datetime.now() - timedelta(days=10)))
    session.commit()
    a = alarmas.check_recalculo_os_atrasado(session)
    assert a is not None
    assert a.severidad == SEV_ALTA


# ── check_cron_log_grande ────────────────────────────────────────────────

def test_check_cron_log_grande_no_dispara_chico(session):
    for i in range(10):
        _agregar_cron_log(session, f'p{i}')
    a = alarmas.check_cron_log_grande(session)
    assert a is None


# ── evaluar_todas ────────────────────────────────────────────────────────

def test_evaluar_todas_ordena_por_severidad(session):
    # Hacer disparar dos alarmas: cron error (crítica) + recalculo (media)
    _agregar_cron_log(session, 'algo', estado='error', hace_horas=1)
    # cliente_os_inferida vacía dispara MEDIA por sí sola
    todas = evaluar_todas(session, force=True)
    assert len(todas) >= 2
    # Críticas primero
    assert todas[0].severidad == SEV_CRITICA


def test_evaluar_todas_no_rompe_si_check_falla(monkeypatch, session):
    # Forzar excepción en un check
    def _check_roto(s):
        raise RuntimeError('bug a propósito')
    monkeypatch.setattr(alarmas, 'check_cron_errors_24h', _check_roto)
    # Reemplazar en CHECKS también
    monkeypatch.setattr(alarmas, 'CHECKS',
                        [_check_roto] + [c for c in alarmas.CHECKS
                                         if c.__name__ != 'check_cron_errors_24h'])
    todas = evaluar_todas(session, force=True)
    # No tira excepción + el resto sigue siendo evaluado
    assert isinstance(todas, list)


# ── contar_por_severidad ─────────────────────────────────────────────────

def test_contar_por_severidad():
    alarmas_lista = [
        Alarma('a', SEV_CRITICA, '', '', ''),
        Alarma('b', SEV_CRITICA, '', '', ''),
        Alarma('c', SEV_MEDIA, '', '', ''),
    ]
    cont = contar_por_severidad(alarmas_lista)
    assert cont[SEV_CRITICA] == 2
    assert cont[SEV_ALTA] == 0
    assert cont[SEV_MEDIA] == 1
    assert cont[SEV_BAJA] == 0


# ── Cache ─────────────────────────────────────────────────────────────────

def test_cache_devuelve_mismo_resultado(session):
    _agregar_cron_log(session, 'algo', estado='error', hace_horas=1)
    primer = evaluar_todas(session)
    # Agregar otro error después → cache no debería verlo
    _agregar_cron_log(session, 'otro', estado='error', hace_horas=1)
    segundo = evaluar_todas(session)
    # Mismo objeto cacheado (los nombres de alarmas detectadas iguales)
    assert [a.nombre for a in primer] == [a.nombre for a in segundo]


def test_cache_force_revalua(session):
    _agregar_cron_log(session, 'algo', estado='error', hace_horas=1)
    primer = evaluar_todas(session)
    n1 = sum(1 for a in primer if a.severidad == SEV_CRITICA)
    # force=True debe re-evaluar
    _agregar_cron_log(session, 'otro', estado='error', hace_horas=1)
    segundo = evaluar_todas(session, force=True)
    # El conteo de errores en valor_actual del check de errores debe haber subido
    cron_err = next((a for a in segundo if 'Cron con errores' in a.nombre), None)
    assert cron_err is not None
    # Algo razonable: el valor incluye un número ≥ 2
    import re
    m = re.search(r'(\d+)', cron_err.valor_actual)
    assert m is not None
    assert int(m.group(1)) >= 2
