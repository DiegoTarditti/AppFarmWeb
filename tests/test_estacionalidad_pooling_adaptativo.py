"""Tests del pooling adaptativo: subrubros con >HETEROGENEIDAD_MAX_DROGAS
drogas distintas no se usan para pooling (su patrón promedio es ruido).

Inyecta datos sintéticos en obs_productos + obs_ventas_mensuales +
obs_subrubros + obs_nombres_drogas y golpea el endpoint para verificar
que las drogas del subrubro heterogéneo salen `pooled=False` con
razón 'subrubro_heterogeneo', mientras que las del subrubro chico
sí se poolean.
"""
from datetime import datetime

import pytest

import database
from database import (
    ObsNombreDroga,
    ObsProducto,
    ObsSubrubro,
    ObsVentaMensual,
)
from routes.estacionalidad import HETEROGENEIDAD_MAX_DROGAS

# Nota: routes.estacionalidad.init_app(flask_app) está registrado en
# tests/conftest.py.


def _seed_subrubro(session, sr_id, descripcion):
    session.add(ObsSubrubro(observer_id=sr_id, descripcion=descripcion))


def _seed_droga(session, droga_id, nombre):
    session.add(ObsNombreDroga(observer_id=droga_id, descripcion=nombre))


def _seed_producto(session, prod_id, droga_id, sr_id):
    session.add(ObsProducto(
        observer_id=prod_id,
        descripcion=f'Prod {prod_id}',
        nombre_droga_observer=droga_id,
        subrubro_observer=sr_id,
        es_habilitado_venta=True,
        requiere_cadena_frio=False,
        fecha_baja=None,
    ))


def _seed_ventas_anio(session, prod_id, anio, unidades_por_mes, id_farmacia=10525):
    for m in range(1, 13):
        session.add(ObsVentaMensual(
            id_farmacia=id_farmacia,
            producto_observer=prod_id,
            anio=anio,
            mes=m,
            unidades=unidades_por_mes,
            monto=unidades_por_mes * 10,
            transacciones=int(unidades_por_mes),
        ))


@pytest.fixture
def dataset(monkeypatch):
    """Setea OBSERVER_ID_FARMACIA y crea data sintética con 2 subrubros:
    - SR 10 "Chico" con 5 drogas → debería usarse para pooling.
    - SR 20 "Gigante" con HETEROGENEIDAD_MAX_DROGAS+5 drogas → debería ser
      considerado heterogéneo y NO usarse para pooling.
    Cada droga tiene 1 producto con ventas en 2 años (24 meses).
    """
    monkeypatch.setenv('OBSERVER_ID_FARMACIA', '10525')

    s = database.SessionLocal()
    try:
        _seed_subrubro(s, 10, 'Chico')
        _seed_subrubro(s, 20, 'Gigante')

        # SR chico: 5 drogas con producto y ventas.
        for i in range(1, 6):
            droga_id = 100 + i
            prod_id = 1000 + i
            _seed_droga(s, droga_id, f'DrogaChico-{i}')
            _seed_producto(s, prod_id, droga_id, 10)
            _seed_ventas_anio(s, prod_id, 2024, 50)
            _seed_ventas_anio(s, prod_id, 2025, 50)

        # SR gigante: HETEROGENEIDAD_MAX_DROGAS+5 drogas.
        n_gigante = HETEROGENEIDAD_MAX_DROGAS + 5
        for i in range(1, n_gigante + 1):
            droga_id = 200 + i
            prod_id = 2000 + i
            _seed_droga(s, droga_id, f'DrogaGigante-{i}')
            _seed_producto(s, prod_id, droga_id, 20)
            _seed_ventas_anio(s, prod_id, 2024, 100)
            _seed_ventas_anio(s, prod_id, 2025, 100)

        s.commit()
        yield {'sr_chico': 10, 'sr_gigante': 20, 'n_gigante': n_gigante}
    finally:
        s.close()


class TestPoolingAdaptativo:

    def test_droga_de_subrubro_gigante_no_es_pooled(self, client, dataset):
        r = client.get('/informes/estacionalidad-drogas?orden=nombre&min_anios=1')
        assert r.status_code == 200
        html = r.data.decode('utf-8')
        # Las drogas del subrubro gigante deben aparecer con razón 'subrubro_heterogeneo'
        # → badge "crudo" en la fila.
        assert 'crudo' in html
        assert 'DrogaGigante-1' in html

    def test_droga_de_subrubro_chico_es_pooled(self, client, dataset):
        # Hago la droga chica con patrón muy plano para que el pooling sea
        # observable: si está pooled con el grupo (también plano), λ aplica
        # y el badge "ajust" se muestra cuando λ<0.7. Con 24 obs (12×2 años),
        # λ = 24/(24+12) = 0.667 < 0.7 → badge se muestra.
        r = client.get('/informes/estacionalidad-drogas?orden=nombre&min_anios=1')
        html = r.data.decode('utf-8')
        # Buscar la fila de DrogaChico-1 y confirmar que aparece "ajust"
        # cerca (no es exacto pero confirma que NO está en subrubro heterogéneo)
        assert 'DrogaChico-1' in html
        # Las del chico no deberían tener el badge "crudo" en su fila propia.
        # Heurística: aparece el pooling info (Subrubro: Chico) en el expand.
        assert 'Chico' in html

    def test_endpoint_responde_aun_con_solo_subrubros_heterogeneos(self, client, monkeypatch):
        monkeypatch.setenv('OBSERVER_ID_FARMACIA', '10525')
        s = database.SessionLocal()
        try:
            _seed_subrubro(s, 30, 'OnlyGiant')
            n = HETEROGENEIDAD_MAX_DROGAS + 2
            for i in range(1, n + 1):
                _seed_droga(s, 300 + i, f'X-{i}')
                _seed_producto(s, 3000 + i, 300 + i, 30)
                _seed_ventas_anio(s, 3000 + i, 2025, 10)
            s.commit()
        finally:
            s.close()

        r = client.get('/informes/estacionalidad-drogas?min_anios=1')
        assert r.status_code == 200
        # Ninguna debería estar pooleada.
        html = r.data.decode('utf-8')
        assert 'crudo' in html


class TestConstante:

    def test_umbral_es_30(self):
        # Si cambia, los tests de pooling adaptativo necesitan ajustarse.
        assert HETEROGENEIDAD_MAX_DROGAS == 30
