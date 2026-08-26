"""Análisis del CSV de ofertas Kellerhoff → vigentes.

Fija las reglas: Minimas>1, menor mínimo por EAN, delta = %mín − %(mínimo 1 misma
base), descarte por delta < umbral, múltiplos afuera, dedup, encoding.
"""
from services.ofertas_kellerhoff import (
    UMBRAL_DELTA_DEFAULT,
    decodificar,
    parsear_vigentes,
    _parse_descuento,
)

# Muestra con un caso de cada tipo (los % son los reales del archivo).
CSV = """Nombre producto;Codigo Barra;Unidades Minimas;Unidades Maximas;Unidades Multiplo;Unidades Bonificadas;Descripcion Oferta
ALMAXIMO 100 MG CPR MAST X 2;7798032937847;1;0;0;0;76.99% de Dto. s/PVP
ALMAXIMO 100 MG CPR MAST X 2;7798032937847;20;0;0;0;79.99% de Dto. s/PVP
ACEMUK 600 MG TABL EFERV X 20;7798129415050;6;0;0;0;33.78% de Dto. s/PVP
ACEMUK 600 MG TABL EFERV X 20;7798129415050;6;0;0;0;33.78% de Dto. s/PVP
5 ASA 1000 MG SOB X 30;7795327064622;2;0;0;0;39.3% de Dto. s/PVP
PROFIL TULIPAN CLASSIC 16 X 3;7791014001369;1;0;0;0;6% de Dto. s/P.Farmacia
PROFIL TULIPAN CLASSIC 16 X 3;7791014001369;1;0;0;0;35.16% de Dto. s/PVP
ACTRON PED 4% SUSP X 100 ML;7793640002093;0;0;6;0;37.92% de Dto. s/PVP
LIPEND 20 MG CPR X 90;7798032937250;2;0;0;0;68.99% de Dto. s/PVP
LIPEND 20 MG CPR X 90;7798032937250;1;0;0;0;58.99% de Dto. s/PVP
GABUTEN 10 MG CPR X 90;7798032937052;2;0;0;0;79.99% de Dto. s/PVP
GABUTEN 10 MG CPR X 90;7798032937052;1;0;0;0;74.99% de Dto. s/PVP
TREGINAX 100 MG CPR DISPER X 30;7795349501525;1;0;0;0;44.82% de Dto. s/PVP
FABOGESIC NIÑO 4% SUSP X 100 ML;7798032936116;18;0;0;0;67.99% de Dto. s/PVP
FABOGESIC NIÑO 4% SUSP X 100 ML;7798032936116;1;0;0;0;63.99% de Dto. s/PVP
"""


def _por_ean(vigentes):
    return {v['ean']: v for v in vigentes}


def test_solo_quedan_las_que_valen():
    vig = _por_ean(parsear_vigentes(CSV))   # umbral default 10
    # Quedan: ACEMUK (todo-o-nada), 5 ASA (todo-o-nada), LIPEND (delta 10 justo).
    assert set(vig) == {'7798129415050', '7795327064622', '7798032937250'}


def test_todo_o_nada_delta_es_el_porcentaje_entero():
    v = _por_ean(parsear_vigentes(CSV))['7798129415050']   # ACEMUK
    assert v['unidades_minimas'] == 6
    assert v['descuento_pct'] == 33.78
    assert v['base'] == 'PVP'
    assert v['baseline_pct'] == 0.0
    assert v['delta'] == 33.78


def test_escalonada_delta_es_la_diferencia_y_se_descarta():
    # ALMAXIMO: min20 79.99 vs min1 76.99 → delta 3 < 10 → afuera.
    assert '7798032937847' not in _por_ean(parsear_vigentes(CSV))
    # GABUTEN: min2 79.99 vs min1 74.99 → delta 5 < 10 → afuera.
    assert '7798032937052' not in _por_ean(parsear_vigentes(CSV))


def test_delta_en_el_limite_entra():
    # LIPEND: 68.99 − 58.99 = 10.00 exacto → entra con umbral 10.
    v = _por_ean(parsear_vigentes(CSV))['7798032937250']
    assert v['unidades_minimas'] == 2
    assert v['delta'] == 10.0


def test_minimo_1_y_multiplo_quedan_afuera():
    vig = _por_ean(parsear_vigentes(CSV))
    assert '7795349501525' not in vig   # TREGINAX, solo mínimo 1
    assert '7791014001369' not in vig   # PROFIL TULIPAN, dos filas mínimo 1
    assert '7793640002093' not in vig   # ACTRON, múltiplo (Minimas=0)


def test_umbral_configurable_deja_pasar_escalones():
    # Con umbral 5, GABUTEN (delta 5) entra; ALMAXIMO (delta 3) sigue afuera.
    vig = _por_ean(parsear_vigentes(CSV, umbral_delta=5))
    assert '7798032937052' in vig
    assert '7798032937847' not in vig


def test_toma_menor_minimo_por_ean():
    # FABOGESIC NIÑO tiene min18 (delta 4) — se descarta, pero confirma que la
    # candidata elegida es la de menor mínimo >1 (18), no la de mínimo 1.
    vig = _por_ean(parsear_vigentes(CSV, umbral_delta=1))
    assert vig['7798032936116']['unidades_minimas'] == 18


def test_parse_descuento():
    assert _parse_descuento('44.82% de Dto. s/PVP') == (44.82, 'PVP')
    assert _parse_descuento('2% de Dto. s/P.Farmacia') == (2.0, 'P.Farmacia')
    assert _parse_descuento('basura') == (None, None)


def test_decodificar_utf8_y_latin1():
    assert decodificar('NIÑO'.encode('utf-8')) == 'NIÑO'
    # bytes que NO son utf-8 válidos → cae a latin-1 sin romper.
    assert decodificar(b'NI\xd1O') == 'NIÑO'


def test_umbral_default_es_10():
    assert UMBRAL_DELTA_DEFAULT == 10.0


# ── Persistencia + rotación ──────────────────────────────────────────────────

def _session_mem():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import database
    eng = create_engine('sqlite:///:memory:')
    database.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def test_import_y_lookup_roundtrip():
    from services.ofertas_kellerhoff import (
        importar_desde_texto, oferta_para_ean, ofertas_por_ean, estado_fuente,
    )
    s = _session_mem()
    stats = importar_desde_texto(s, CSV)          # umbral 10 → 3 vigentes
    assert stats['vigentes'] == 3

    o = oferta_para_ean(s, '7798129415050')       # ACEMUK
    assert o['unidades_minimas'] == 6
    assert o['descuento_pct'] == 33.78
    assert oferta_para_ean(s, '7798032937847') is None   # ALMAXIMO descartado

    bulk = ofertas_por_ean(s, ['7795327064622', '7798032937847', 'inexistente'])
    assert set(bulk) == {'7795327064622'}         # solo el que tiene oferta

    est = estado_fuente(s)
    assert est['n_vigentes'] == 3
    assert est['delta_umbral'] == 10.0
    assert est['generado_en'] is not None


def test_reprocesar_con_otro_umbral_sin_redescargar():
    from services.ofertas_kellerhoff import importar_desde_texto, reprocesar, oferta_para_ean
    s = _session_mem()
    importar_desde_texto(s, CSV)                   # umbral 10 → 3
    # Bajar a 5 sin volver a pasar el CSV: usa el crudo guardado.
    stats = reprocesar(s, umbral_delta=5)
    assert stats['vigentes'] == 4                  # entra GABUTEN (delta 5)
    assert oferta_para_ean(s, '7798032937052') is not None


def test_reprocesar_sin_fuente_devuelve_none():
    from services.ofertas_kellerhoff import reprocesar
    assert reprocesar(_session_mem()) is None


def test_califica_por_rotacion():
    from services.ofertas_kellerhoff import califica_por_rotacion
    # mín 6, vende 10/mes → 0,6 meses ≤ 1 → califica
    assert califica_por_rotacion(6, 10) is True
    # mín 20, vende 1/mes → 20 meses > 1 → no
    assert califica_por_rotacion(20, 1) is False
    # sin venta → nunca
    assert califica_por_rotacion(6, 0) is False
    assert califica_por_rotacion(6, None) is False
    # umbral configurable
    assert califica_por_rotacion(3, 2, cobertura_max=2) is True   # 1,5 meses ≤ 2
