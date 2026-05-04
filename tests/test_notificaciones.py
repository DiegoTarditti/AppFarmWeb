"""Tests para notificaciones.py — dedup de alarmas a Telegram.

Cubre:
- enviar_telegram: fail-safe sin TOKEN/CHAT_ID, manejo de HTTP error.
- evaluar_y_notificar: notifica primera vez, deduplica gap <4h, re-notifica
  después del gap, marca como 'resuelta' las que dejan de aparecer,
  re-notifica al resucitar.
- Filtro de severidades.
- _formatear_alarma: incluye link, emoji, info clave.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import alarmas as _alarmas
import database
import notificaciones
from alarmas import Alarma
from database import AlarmaNotificada


@pytest.fixture
def session():
    s = database.SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _reset_alarmas_cache():
    _alarmas.invalidar_cache()
    yield


@pytest.fixture
def fake_telegram_ok():
    """Patch enviar_telegram para que devuelva OK sin pegarle a la red."""
    sent = []

    def _stub(mensaje, parse_mode='HTML'):
        sent.append(mensaje)
        return True, None

    with patch.object(notificaciones, 'enviar_telegram', _stub):
        yield sent


@pytest.fixture
def fake_telegram_fail():
    """Patch para simular error HTTP."""
    def _stub(mensaje, parse_mode='HTML'):
        return False, 'HTTP 500 server error'
    with patch.object(notificaciones, 'enviar_telegram', _stub):
        yield


@pytest.fixture
def alarma_critica():
    return Alarma(
        nombre='Test crítica',
        severidad='critica',
        valor_actual='10 errores',
        threshold='≥1',
        accion='Mirar logs',
        link='/admin/cron-log',
    )


@pytest.fixture
def alarma_media():
    return Alarma(
        nombre='Test media',
        severidad='media',
        valor_actual='nunca',
        threshold='al menos 1',
        accion='Esperar',
        link='/admin',
    )


# ── enviar_telegram (sin red, solo lógica de config) ──────────────────────

def test_enviar_telegram_sin_config():
    """Sin TOKEN/CHAT_ID seteados → False sin tirar."""
    with patch.dict('os.environ', {}, clear=False):
        # Quitar las env vars si están
        import os as _os
        for k in ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID'):
            _os.environ.pop(k, None)
        ok, err = notificaciones.enviar_telegram('hola')
    assert ok is False
    assert 'no configurado' in err.lower()


def test_telegram_config_lee_envs():
    with patch.dict('os.environ', {
            'TELEGRAM_BOT_TOKEN': 'tok123',
            'TELEGRAM_CHAT_ID': '999'}):
        cfg = notificaciones._telegram_config()
    assert cfg == {'token': 'tok123', 'chat_id': '999'}


def test_telegram_config_strips_whitespace():
    with patch.dict('os.environ', {
            'TELEGRAM_BOT_TOKEN': '  tok123  ',
            'TELEGRAM_CHAT_ID': '\n999\n'}):
        cfg = notificaciones._telegram_config()
    assert cfg == {'token': 'tok123', 'chat_id': '999'}


# ── _formatear_alarma ─────────────────────────────────────────────────────

def test_formatear_incluye_emoji_y_link(alarma_critica):
    msg = notificaciones._formatear_alarma(alarma_critica, 'https://app.com')
    assert '🚨' in msg
    assert 'Test crítica' in msg
    assert 'CRITICA' in msg.upper()
    assert '10 errores' in msg
    assert 'Mirar logs' in msg
    assert 'https://app.com/admin/cron-log' in msg


def test_formatear_sin_link_usa_admin_alarmas():
    a = Alarma('x', 'alta', 'val', 'th', 'acc', link=None)
    msg = notificaciones._formatear_alarma(a, 'https://app.com')
    assert 'https://app.com/admin/alarmas' in msg


# ── evaluar_y_notificar: primera notificación ─────────────────────────────

def _patch_evaluar_todas(monkeypatch, alarmas_a_devolver):
    """Hace que alarmas.evaluar_todas devuelva una lista fija."""
    def _stub(session, force=False):
        return list(alarmas_a_devolver)
    monkeypatch.setattr(_alarmas, 'evaluar_todas', _stub)


def test_notifica_primera_vez(monkeypatch, session, alarma_critica, fake_telegram_ok):
    _patch_evaluar_todas(monkeypatch, [alarma_critica])
    res = notificaciones.evaluar_y_notificar(session)
    assert res['notificadas'] == 1
    assert res['silenciadas'] == 0
    assert len(fake_telegram_ok) == 1

    # Persistió estado
    estado = session.get(AlarmaNotificada, 'Test crítica')
    assert estado is not None
    assert estado.estado_actual == 'activa'
    assert estado.count_total == 1


def test_no_notifica_si_severidad_filtrada(monkeypatch, session, alarma_media, fake_telegram_ok):
    """Default sólo notifica critica + alta. Una media debería silenciarse."""
    _patch_evaluar_todas(monkeypatch, [alarma_media])
    res = notificaciones.evaluar_y_notificar(session)
    assert res['notificadas'] == 0
    assert res['silenciadas'] == 1
    assert len(fake_telegram_ok) == 0


def test_notifica_media_si_se_pide_explicito(monkeypatch, session, alarma_media, fake_telegram_ok):
    _patch_evaluar_todas(monkeypatch, [alarma_media])
    res = notificaciones.evaluar_y_notificar(
        session, severidades=('critica', 'alta', 'media'))
    assert res['notificadas'] == 1


# ── Dedup: gap <4h silencia ──────────────────────────────────────────────

def test_dedup_silencia_dentro_del_gap(monkeypatch, session, alarma_critica, fake_telegram_ok):
    # Estado pre-existente: notificada hace 1h
    session.add(AlarmaNotificada(
        nombre='Test crítica',
        ultima_notif=datetime.now() - timedelta(hours=1),
        ultima_severidad='critica',
        count_total=1,
        estado_actual='activa',
    ))
    session.commit()

    _patch_evaluar_todas(monkeypatch, [alarma_critica])
    res = notificaciones.evaluar_y_notificar(session)
    assert res['notificadas'] == 0
    assert res['silenciadas'] == 1
    assert len(fake_telegram_ok) == 0


def test_dedup_re_notifica_despues_del_gap(monkeypatch, session, alarma_critica, fake_telegram_ok):
    # Estado: notificada hace 5h (>4h gap)
    session.add(AlarmaNotificada(
        nombre='Test crítica',
        ultima_notif=datetime.now() - timedelta(hours=5),
        ultima_severidad='critica',
        count_total=3,
        estado_actual='activa',
    ))
    session.commit()

    _patch_evaluar_todas(monkeypatch, [alarma_critica])
    res = notificaciones.evaluar_y_notificar(session)
    assert res['notificadas'] == 1

    estado = session.get(AlarmaNotificada, 'Test crítica')
    assert estado.count_total == 4  # incrementó


# ── Resurrección: re-notifica si estaba 'resuelta' ───────────────────────

def test_resurreccion_renotifica(monkeypatch, session, alarma_critica, fake_telegram_ok):
    # Estado: estaba resuelta, hace 1h (dentro del gap, pero resuelta gana)
    session.add(AlarmaNotificada(
        nombre='Test crítica',
        ultima_notif=datetime.now() - timedelta(hours=1),
        ultima_severidad='critica',
        count_total=2,
        estado_actual='resuelta',
    ))
    session.commit()

    _patch_evaluar_todas(monkeypatch, [alarma_critica])
    res = notificaciones.evaluar_y_notificar(session)
    assert res['notificadas'] == 1  # resucitó → notifica

    estado = session.get(AlarmaNotificada, 'Test crítica')
    assert estado.estado_actual == 'activa'


# ── Marcar 'resuelta' las que dejan de aparecer ──────────────────────────

def test_marca_resuelta_si_no_aparece(monkeypatch, session, fake_telegram_ok):
    # Estado pre-existente: alarma activa
    session.add(AlarmaNotificada(
        nombre='Test crítica',
        ultima_notif=datetime.now() - timedelta(hours=1),
        ultima_severidad='critica',
        count_total=1,
        estado_actual='activa',
    ))
    session.commit()

    # Ahora evaluar_todas devuelve VACÍO (la alarma se resolvió)
    _patch_evaluar_todas(monkeypatch, [])
    res = notificaciones.evaluar_y_notificar(session)
    assert res['evaluadas'] == 0
    # No notifica nada (no spamean "resuelta" — solo cosas malas)
    assert res['notificadas'] == 0
    assert len(fake_telegram_ok) == 0

    estado = session.get(AlarmaNotificada, 'Test crítica')
    assert estado.estado_actual == 'resuelta'


# ── Errores en envío Telegram no rompen el flujo ─────────────────────────

def test_error_telegram_no_rompe(monkeypatch, session, alarma_critica, fake_telegram_fail):
    _patch_evaluar_todas(monkeypatch, [alarma_critica])
    res = notificaciones.evaluar_y_notificar(session)
    assert res['notificadas'] == 0  # no incrementó (envío falló)
    assert len(res['errores']) == 1
    assert 'HTTP 500' in res['errores'][0]
    # NO persistió estado porque el envío falló
    estado = session.get(AlarmaNotificada, 'Test crítica')
    assert estado is None


# ── App URL prefix ───────────────────────────────────────────────────────

def test_app_url_aplica_prefix(monkeypatch, session, alarma_critica):
    sent = []

    def _stub(mensaje, parse_mode='HTML'):
        sent.append(mensaje)
        return True, None

    _patch_evaluar_todas(monkeypatch, [alarma_critica])
    with patch.object(notificaciones, 'enviar_telegram', _stub):
        notificaciones.evaluar_y_notificar(
            session, app_url='https://prod.example.com')

    assert len(sent) == 1
    assert 'https://prod.example.com/admin/cron-log' in sent[0]
