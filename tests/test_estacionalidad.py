"""Tests del cálculo de estacionalidad por droga.

`_calcular_estacionalidad_droga` y `_calcular_indice_subrubro` son funciones
puras en routes/estacionalidad.py.
"""
from routes.estacionalidad import (
    K_PRIOR,
    _calcular_estacionalidad_droga,
    _calcular_indice_subrubro,
)


def _plano(unidades_por_mes):
    """Helper: dict mes -> [unidades] simulando 1 año de data."""
    return {m: [unidades_por_mes] for m in range(1, 13)}


def _con_invierno(base, factor_invierno):
    """Helper: base todo el año, pero junio-agosto multiplicado por factor."""
    data = {m: [base] for m in range(1, 13)}
    for m in (6, 7, 8):
        data[m] = [base * factor_invierno]
    return data


class TestEstacionalidadCruda:

    def test_droga_sin_estacionalidad_da_indices_neutros(self):
        est = _calcular_estacionalidad_droga(_plano(100))
        for v in est['indices']:
            assert abs(v - 1.0) < 0.001
        assert est['cv'] < 0.01

    def test_droga_con_pico_de_invierno(self):
        # Junio/Julio/Agosto venden 3x el resto. Promedio mensual sube y
        # los meses de invierno quedan con índice > 1.
        est = _calcular_estacionalidad_droga(_con_invierno(100, 3.0))
        assert est['indices'][5] > 1.5  # Jun
        assert est['indices'][6] > 1.5  # Jul
        assert est['indices'][7] > 1.5  # Ago
        assert est['indices'][0] < 1.0  # Ene
        assert est['cv'] > 0.3

    def test_meses_sin_ventas_se_completan_con_neutro_si_no_hay_grupo(self):
        # Solo enero a junio.
        meses = {m: [100] for m in range(1, 7)}
        est = _calcular_estacionalidad_droga(meses)
        # Meses sin obs (jul-dic) → 1.0 (neutro).
        for m_idx in range(6, 12):
            assert est['indices'][m_idx] == 1.0

    def test_descarta_drogas_con_menos_de_6_meses_observados(self):
        meses = {m: [100] for m in range(1, 6)}  # solo 5 meses
        assert _calcular_estacionalidad_droga(meses) is None

    def test_descarta_drogas_con_promedio_global_cero(self):
        meses = {m: [0.0] for m in range(1, 13)}
        assert _calcular_estacionalidad_droga(meses) is None


class TestPooling:

    def test_pooling_acerca_drogas_de_poca_historia_al_grupo(self):
        # Droga con 1 año de data: peak fortísimo en julio (5x).
        # Grupo (subrubro) sin estacionalidad fuerte: índice 1.0 todo el año.
        # Resultado: el peak se atenúa hacia el grupo.
        meses_droga = {m: [100] for m in range(1, 13)}
        meses_droga[7] = [500]
        grupo = {m: 1.0 for m in range(1, 13)}

        sin_pool = _calcular_estacionalidad_droga(meses_droga)
        con_pool = _calcular_estacionalidad_droga(meses_droga, grupo)

        # Mismo n_obs (12), pero el con_pool aplica λ ≈ 0.5 y suaviza
        # hacia el grupo (1.0).
        assert con_pool['indices'][6] < sin_pool['indices'][6]
        assert con_pool['pooled'] is True
        assert sin_pool['pooled'] is False

    def test_lambda_crece_con_mas_observaciones(self):
        # Más años de data → λ más cerca de 1 → el patrón crudo pesa más.
        meses_1a = {m: [100] for m in range(1, 13)}  # 12 obs
        meses_3a = {m: [100, 100, 100] for m in range(1, 13)}  # 36 obs

        grupo = {m: 1.0 for m in range(1, 13)}
        est_1a = _calcular_estacionalidad_droga(meses_1a, grupo)
        est_3a = _calcular_estacionalidad_droga(meses_3a, grupo)

        assert est_1a['lambda_shrink'] < est_3a['lambda_shrink']
        # Sanity: λ = 12/(12+12) = 0.5 vs 36/(36+12) = 0.75
        assert abs(est_1a['lambda_shrink'] - 0.5) < 0.01
        assert abs(est_3a['lambda_shrink'] - 0.75) < 0.01

    def test_meses_faltantes_se_rellenan_con_grupo_si_hay_pool(self):
        meses = {m: [100] for m in range(1, 7)}  # solo ene-jun
        grupo = {m: 2.0 if m in (10, 11, 12) else 0.8 for m in range(1, 13)}
        est = _calcular_estacionalidad_droga(meses, grupo)
        # Meses sin obs en la droga tomaron el índice del grupo entero.
        assert est['indices'][9] == 2.0   # Oct
        assert est['indices'][10] == 2.0  # Nov
        assert est['indices'][11] == 2.0  # Dic


class TestIndiceSubrubro:

    def test_calcula_patron_agregado(self):
        # Subrubro con clara estacionalidad de verano.
        meses = {m: [100] for m in range(1, 13)}
        for m in (12, 1, 2):
            meses[m] = [300]
        idx = _calcular_indice_subrubro(meses)
        assert idx[12] > 1.3
        assert idx[1] > 1.3
        assert idx[6] < 1.0

    def test_devuelve_none_con_pocos_meses(self):
        assert _calcular_indice_subrubro({m: [100] for m in range(1, 4)}) is None


class TestConstantes:

    def test_k_prior_es_12(self):
        # Doc: K=12 → λ=0.5 con 1 año (12 meses) de data. Si cambia, los
        # tests de pooling fallan; sirve como recordatorio.
        assert K_PRIOR == 12
