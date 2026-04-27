"""Tests del módulo matviews (refresh + estado de vistas materializadas).

`refrescar_matview` y `estado_matview` son críticos: el sistema los usa
para refrescar `mv_stats_drogas` después de cada deploy. Tuvimos 2 bugs
hoy en producción cuando el fallback CONCURRENTLY → no-CONCURRENTLY no
detectaba correctamente los mensajes de error.

Estos tests fijan el comportamiento del fallback y del cálculo de
'fresco/viejo/muy_viejo' por edad.
"""
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

import database
import matviews


@pytest.fixture
def db():
    s = database.SessionLocal()
    yield s
    s.rollback()
    s.close()


# ── estado_matview ────────────────────────────────────────────────────────────

class TestEstadoMatview:

    def test_sin_logs_devuelve_nunca(self, db):
        info = matviews.estado_matview(db, 'mv_stats_drogas')
        assert info['estado'] == 'nunca'
        assert info['ultimo_refresh'] is None
        assert info['filas'] is None

    def test_log_reciente_es_fresco(self, db):
        db.add(database.MvRefreshLog(
            view_name='mv_stats_drogas',
            refrescada_en=database.now_ar() - timedelta(hours=2),
            duracion_ms=1000, filas=1234,
        ))
        db.commit()
        info = matviews.estado_matview(db, 'mv_stats_drogas')
        assert info['estado'] == 'fresco'
        assert info['filas'] == 1234

    def test_log_de_30h_es_viejo(self, db):
        db.add(database.MvRefreshLog(
            view_name='mv_stats_drogas',
            refrescada_en=database.now_ar() - timedelta(hours=30),
            duracion_ms=1000, filas=1234,
        ))
        db.commit()
        info = matviews.estado_matview(db, 'mv_stats_drogas')
        assert info['estado'] == 'viejo'

    def test_log_de_100h_es_muy_viejo(self, db):
        db.add(database.MvRefreshLog(
            view_name='mv_stats_drogas',
            refrescada_en=database.now_ar() - timedelta(hours=100),
            duracion_ms=1000, filas=1234,
        ))
        db.commit()
        info = matviews.estado_matview(db, 'mv_stats_drogas')
        assert info['estado'] == 'muy_viejo'

    def test_ignora_logs_con_error(self, db):
        # Un log con error existe, pero estado_matview lo ignora.
        db.add(database.MvRefreshLog(
            view_name='mv_stats_drogas',
            refrescada_en=database.now_ar() - timedelta(hours=1),
            duracion_ms=500, error='boom',
        ))
        db.commit()
        info = matviews.estado_matview(db, 'mv_stats_drogas')
        assert info['estado'] == 'nunca'

    def test_devuelve_el_mas_reciente(self, db):
        # Dos logs, el segundo más reciente.
        db.add(database.MvRefreshLog(
            view_name='mv_stats_drogas',
            refrescada_en=database.now_ar() - timedelta(hours=10),
            duracion_ms=1000, filas=100,
        ))
        db.add(database.MvRefreshLog(
            view_name='mv_stats_drogas',
            refrescada_en=database.now_ar() - timedelta(hours=1),
            duracion_ms=500, filas=200,
        ))
        db.commit()
        info = matviews.estado_matview(db, 'mv_stats_drogas')
        assert info['filas'] == 200

    def test_borde_24h_es_viejo(self, db):
        # 24h exactas es 'viejo' (>=24).
        db.add(database.MvRefreshLog(
            view_name='mv_stats_drogas',
            refrescada_en=database.now_ar() - timedelta(hours=24, minutes=1),
            duracion_ms=1000, filas=1234,
        ))
        db.commit()
        info = matviews.estado_matview(db, 'mv_stats_drogas')
        assert info['estado'] == 'viejo'


# ── estado_todas_matviews ─────────────────────────────────────────────────────

class TestEstadoTodas:

    def test_devuelve_dict_con_todas(self, db):
        info = matviews.estado_todas_matviews(db)
        assert 'mv_stats_drogas' in info
        assert info['mv_stats_drogas']['estado'] == 'nunca'  # sin logs


# ── refrescar_matview — validación de input ───────────────────────────────────

class TestRefrescarValidacion:

    def test_vista_no_registrada_lanza_value_error(self, db):
        with pytest.raises(ValueError, match='no registrada'):
            matviews.refrescar_matview(db, 'vista_que_no_existe')

    def test_vista_registrada_no_lanza(self, db):
        # En SQLite no anda REFRESH MATERIALIZED VIEW; capturamos el error
        # y verificamos que devuelve dict, no que lance.
        result = matviews.refrescar_matview(db, 'mv_stats_drogas', concurrently=False)
        assert isinstance(result, dict)
        assert 'ok' in result
        assert 'duracion_ms' in result


# ── refrescar_matview — fallback CONCURRENTLY → no-CONCURRENTLY ──────────────

class TestRefreshFallback:
    """Aquí está el bug que nos rompió 2 veces hoy: el fallback no detectaba
    todos los mensajes que Postgres puede tirar cuando la vista no está populada.
    """

    @pytest.mark.parametrize('error_msg', [
        'materialized view "x" has not been populated',
        'materialized view is not populated',
        'cannot refresh materialized view "x" concurrently',
        'CONCURRENTLY cannot be used with materialized view',
    ])
    def test_fallback_detecta_todos_los_mensajes(self, db, error_msg):
        """Cualquiera de estos mensajes debe disparar el fallback sin CONCURRENTLY."""
        # Mockeamos session para controlar las llamadas a execute.
        # El código hace 3 llamadas a execute: 1) REFRESH CONCURRENTLY (falla),
        # 2) REFRESH sin CONCURRENTLY (OK), 3) SELECT COUNT(*).
        mock_session = MagicMock()
        mock_session.execute.side_effect = [
            Exception(error_msg),                       # 1: CONCURRENTLY falla
            None,                                       # 2: REFRESH sin CONCURRENTLY (OK)
            MagicMock(scalar=lambda: 100),              # 3: SELECT COUNT(*)
        ]
        mock_session.rollback = MagicMock()

        result = matviews.refrescar_matview(mock_session, 'mv_stats_drogas',
                                             concurrently=True)

        assert result['ok'] is True
        assert result['filas'] == 100
        # Verificar que se llamó a rollback (fallback).
        assert mock_session.rollback.called

    def test_error_no_relacionado_se_propaga(self, db):
        """Un error que no es 'not populated' debe registrarse como error y NO
        intentar fallback.
        """
        mock_session = MagicMock()
        mock_session.execute.side_effect = [
            Exception('connection refused'),  # error totalmente no relacionado
        ]
        mock_session.rollback = MagicMock()

        result = matviews.refrescar_matview(mock_session, 'mv_stats_drogas',
                                             concurrently=True)

        assert result['ok'] is False
        assert 'connection refused' in result['error']

    def test_concurrently_false_no_intenta_fallback(self, db):
        """Si pedimos concurrently=False explícito, ejecuta directo sin try."""
        mock_session = MagicMock()
        mock_session.execute.side_effect = [
            None,                                     # REFRESH OK
            MagicMock(scalar=lambda: 50),             # SELECT COUNT
        ]

        result = matviews.refrescar_matview(mock_session, 'mv_stats_drogas',
                                             concurrently=False)

        assert result['ok'] is True
        assert result['filas'] == 50

    def test_loguea_aunque_falle(self, db):
        """Aunque el refresh falle, se inserta un MvRefreshLog con error."""
        # Real session — usamos sqlite + capturamos el log.
        result = matviews.refrescar_matview(db, 'mv_stats_drogas', concurrently=False)
        # En SQLite REFRESH MATERIALIZED VIEW no existe → falla.
        assert result['ok'] is False
        # Debe haber al menos 1 log con error.
        logs = db.query(database.MvRefreshLog).all()
        assert any(log.error is not None for log in logs)
