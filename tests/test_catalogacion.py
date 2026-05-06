"""Tests de catalogación estructurada — extraer + match dimensional.

Cubre los 10 casos del spec en `docs/tests-catalogacion-spec.md`:
  - Extracción de atributos desde descripción libre (regex).
  - Match dimensional con score (5+ probable, 7+ casi seguro).
  - Edge cases: ML en líquido, MG/ML compuesta, número huérfano CPR/CAP.

La conftest ya monta SQLite in-memory + truncado entre tests.
"""
from decimal import Decimal

import pytest

import database
from catalogacion import (
    extraer_de_descripcion,
    match_dimensional_candidatos,
)


@pytest.fixture
def session():
    s = database.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _crear_producto_con_atributos(session, codigo_barra, descripcion,
                                   monodroga_norm=None, monodroga_display=None,
                                   concentracion_mg=None, concentracion_unidad=None,
                                   forma_farma=None, cantidad_envase=None,
                                   fuente='manual'):
    p = database.Producto(
        codigo_barra=codigo_barra,
        descripcion=descripcion,
        monodroga=monodroga_display,  # fuente única: Producto.monodroga
    )
    session.add(p)
    session.flush()
    atr = database.ProductoAtributo(
        producto_id=p.id,
        monodroga_norm=monodroga_norm,
        concentracion_mg=Decimal(str(concentracion_mg)) if concentracion_mg is not None else None,
        concentracion_unidad=concentracion_unidad,
        forma_farma=forma_farma,
        cantidad_envase=Decimal(str(cantidad_envase)) if cantidad_envase is not None else None,
        fuente=fuente,
        confianza='alta',
    )
    session.add(atr)
    session.commit()
    return p, atr


# ─── Tests de match_dimensional_candidatos ──────────────────────────────────


def test_match_completo_score_12(session):
    """Test 1: los 4 atributos matchean exacto → score = 5+3+2+2 = 12."""
    _crear_producto_con_atributos(
        session, '7791111111111', 'IBUPIRAC 600 MG CPR x 30',
        monodroga_norm='ibuprofeno', monodroga_display='IBUPROFENO',
        concentracion_mg=600, concentracion_unidad='MG',
        forma_farma='CPR', cantidad_envase=30,
    )
    candidatos = match_dimensional_candidatos(
        session,
        monodroga_norm='ibuprofeno',
        concentracion_mg=600,
        forma_farma='CPR',
        cantidad_envase=30,
    )
    assert len(candidatos) == 1
    assert candidatos[0]['score'] == 12
    assert candidatos[0]['codigo_barra'] == '7791111111111'


def test_match_solo_descripcion(session):
    """Test 2: pasar solo desc + droga → extrae atributos del texto y matchea."""
    _crear_producto_con_atributos(
        session, '7791111111112', 'IBUPIRAC 600 MG CPR x 10',
        monodroga_norm='ibuprofeno', monodroga_display='IBUPROFENO',
        concentracion_mg=600, concentracion_unidad='MG',
        forma_farma='CPR', cantidad_envase=10,
    )
    # La droga no se extrae sólo del texto (la regex no detecta drogas);
    # los demás atributos sí.
    candidatos = match_dimensional_candidatos(
        session,
        descripcion='IBUPIRAC 600 X 10 CPR',
        monodroga_norm='ibuprofeno',
    )
    assert len(candidatos) >= 1
    # Score esperado: 5 (droga) + 3 (conc 600) + 2 (CPR) + 2 (cant 10) = 12
    assert candidatos[0]['score'] == 12


def test_match_score_5_solo_droga(session):
    """Test 3: solo droga matchea, distinta concentración/forma/cantidad → score 5."""
    _crear_producto_con_atributos(
        session, '7791111111113', 'IBUPIRAC 400 CPR x 20',
        monodroga_norm='ibuprofeno', concentracion_mg=400,
        forma_farma='CPR', cantidad_envase=20,
    )
    candidatos = match_dimensional_candidatos(
        session,
        monodroga_norm='ibuprofeno',
        concentracion_mg=600,    # NO matchea (catálogo tiene 400)
        forma_farma='CAP',       # NO matchea (catálogo tiene CPR)
        cantidad_envase=10,      # NO matchea (catálogo tiene 20)
    )
    assert len(candidatos) == 1
    assert candidatos[0]['score'] == 5


def test_match_score_7(session):
    """Test 4: droga + cantidad matchea (5+2=7)."""
    _crear_producto_con_atributos(
        session, '7791111111114', 'IBUPIRAC 800 X 30',
        monodroga_norm='ibuprofeno', concentracion_mg=800,
        forma_farma='CPR', cantidad_envase=30,
    )
    candidatos = match_dimensional_candidatos(
        session,
        monodroga_norm='ibuprofeno',
        concentracion_mg=600,    # NO matchea
        forma_farma='CAP',       # NO matchea
        cantidad_envase=30,      # SÍ matchea
    )
    assert len(candidatos) == 1
    assert candidatos[0]['score'] == 7


def test_match_sin_atributos_vacio(session):
    """Test 5: sin atributos ni descripción → []."""
    candidatos = match_dimensional_candidatos(session)
    assert candidatos == []
    candidatos = match_dimensional_candidatos(session, descripcion='')
    assert candidatos == []


# ─── Tests de extraer_de_descripcion ────────────────────────────────────────


def test_extraer_ml_liquido_es_volumen():
    """Test 6: ML en forma líquida (jarabe) NO es concentración, es volumen."""
    atrs = extraer_de_descripcion('LACTULON JARABE X 200 ML')
    assert atrs.get('forma_farma') == 'SUSP'
    assert atrs.get('cantidad_envase') == Decimal(200)
    assert 'concentracion_mg' not in atrs


def test_extraer_compuesta_mg_ml():
    """Test 7: compuesta MG/ML preserva la unidad textual."""
    atrs = extraer_de_descripcion('AMOXIDAL 250 MG/5ML SUSP X 60ML')
    assert atrs.get('concentracion_mg') == Decimal(250)
    assert atrs.get('concentracion_unidad') == 'MG/5ML'
    assert atrs.get('forma_farma') == 'SUSP'
    assert atrs.get('cantidad_envase') == Decimal(60)


def test_extraer_concentracion_huerfana():
    """Test 8: número 1-1000 sin unidad pegada en CPR/CAP → mg implícito."""
    atrs = extraer_de_descripcion('ACTRON 600 RAPIDA ACCION CAP X 10')
    assert atrs.get('concentracion_mg') == Decimal(600)
    assert atrs.get('concentracion_unidad') == 'MG'
    assert atrs.get('forma_farma') == 'CAP'
    assert atrs.get('cantidad_envase') == Decimal(10)


# ─── Tests de comportamiento de match_dimensional ───────────────────────────


def test_limit_honorado(session):
    """Test 9: 50 candidatos, limit=10 → devuelve 10."""
    for i in range(50):
        _crear_producto_con_atributos(
            session, f'7791111{i:06d}', f'IBUPIRAC {i}',
            monodroga_norm='ibuprofeno',
        )
    candidatos = match_dimensional_candidatos(
        session,
        monodroga_norm='ibuprofeno',
        limit=10,
    )
    assert len(candidatos) == 10


def test_ordenamiento_por_score_desc(session):
    """Test 10: 3 productos con scores 12/8/5 devuelven en orden desc."""
    # Score 12: todos los atributos matchean
    _crear_producto_con_atributos(
        session, '7791111111120', 'PERFECT 600 CPR x 30',
        monodroga_norm='ibuprofeno', concentracion_mg=600,
        forma_farma='CPR', cantidad_envase=30,
    )
    # Score 8: droga + concentración (5+3)
    _crear_producto_con_atributos(
        session, '7791111111121', 'PARCIAL 600 CAP x 20',
        monodroga_norm='ibuprofeno', concentracion_mg=600,
        forma_farma='CAP', cantidad_envase=20,
    )
    # Score 5: solo droga
    _crear_producto_con_atributos(
        session, '7791111111122', 'MINIMO 400 CAP x 10',
        monodroga_norm='ibuprofeno', concentracion_mg=400,
        forma_farma='CAP', cantidad_envase=10,
    )
    candidatos = match_dimensional_candidatos(
        session,
        monodroga_norm='ibuprofeno',
        concentracion_mg=600,
        forma_farma='CPR',
        cantidad_envase=30,
    )
    assert len(candidatos) == 3
    scores = [c['score'] for c in candidatos]
    assert scores == [12, 8, 5]
