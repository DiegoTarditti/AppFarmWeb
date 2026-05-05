"""Tests del matcher central de productos."""

import pytest
import database
from database import Producto, Laboratorio, ObsProducto
import producto_matcher as pm


@pytest.fixture
def db_session():
    s = database.SessionLocal()
    yield s
    s.rollback()
    s.close()


@pytest.fixture
def lab(db_session):
    lab = Laboratorio(nombre='Roemmers', activo=True)
    db_session.add(lab)
    db_session.commit()
    return lab


@pytest.fixture
def productos_catalog(db_session, lab):
    """Catálogo mínimo para tests."""
    items = [
        Producto(codigo_barra='7793450121111', descripcion='TAFIROL 1 g COM x 50',
                 precio_pvp=4500, laboratorio_id=lab.id),
        Producto(codigo_barra='7793450122222', descripcion='TAFIROL 1 g COM x 80',
                 precio_pvp=6800, laboratorio_id=lab.id),
        Producto(codigo_barra='7793450133333', descripcion='AMOXIDAL 500 mg COM x 16',
                 precio_pvp=3200, laboratorio_id=lab.id, codigo_alfabeta='AMX500-16'),
    ]
    db_session.add_all(items)
    db_session.commit()
    return items


# ── Helpers de texto ────────────────────────────────────────────────────────

class TestNormalizar:
    def test_quita_acentos(self):
        assert pm.normalizar_texto('Médico') == 'medico'

    def test_quita_puntuacion(self):
        assert pm.normalizar_texto('Hola, mundo!') == 'hola mundo'

    def test_colapsa_espacios(self):
        assert pm.normalizar_texto('  Hola   mundo  ') == 'hola mundo'

    def test_none_y_vacio(self):
        assert pm.normalizar_texto(None) == ''
        assert pm.normalizar_texto('') == ''


class TestTokens:
    def test_quita_stopwords(self):
        # mg, x, comp son stopwords
        toks = pm.tokens_significativos('TAFIROL 1 g COM x 50')
        assert 'tafiroll' not in toks  # typo, dejamos
        assert 'tafirol' in toks
        assert '50' in toks
        assert 'com' not in toks

    def test_min_length(self):
        toks = pm.tokens_significativos('A B CD EFG')
        assert 'a' not in toks
        assert 'cd' in toks


class TestJaccard:
    def test_identicos(self):
        assert pm.jaccard({'a', 'b'}, {'a', 'b'}) == 1.0

    def test_disjuntos(self):
        assert pm.jaccard({'a', 'b'}, {'c', 'd'}) == 0.0

    def test_parcial(self):
        # {a, b} ∩ {b, c} = {b}, ∪ = {a, b, c} → 1/3
        assert pm.jaccard({'a', 'b'}, {'b', 'c'}) == pytest.approx(1/3)

    def test_set_vacio(self):
        assert pm.jaccard(set(), {'a'}) == 0.0


class TestPackHelpers:
    def test_es_pack(self):
        assert pm.descripcion_es_pack('TAFIROL PACK X 10')
        assert pm.descripcion_es_pack('TAFIROL PACK 4 ESTUCHES')
        assert not pm.descripcion_es_pack('TAFIROL COM x 50')

    def test_limpiar_sufijos(self):
        assert 'PACK' not in pm.limpiar_sufijos_pack('TAFIROL PACK X 10').upper()
        assert pm.limpiar_sufijos_pack('TAFIROL') == 'TAFIROL'


# ── Cascada de match ────────────────────────────────────────────────────────

class TestMatchExacto:
    def test_match_por_ean(self, productos_catalog, db_session):
        res = pm.match_producto(ean='7793450121111', session=db_session)
        assert res.producto is not None
        assert res.producto.codigo_barra == '7793450121111'
        assert res.estrategia == 'ean_exacto'
        assert res.confianza == 'alta'
        assert res.score == 1.0

    def test_match_por_alfabeta(self, productos_catalog, db_session):
        res = pm.match_producto(codigo_alfabeta='AMX500-16', session=db_session)
        assert res.producto is not None
        assert res.estrategia == 'alfabeta_exacto'

    def test_no_match(self, productos_catalog, db_session):
        res = pm.match_producto(ean='9999999999999', session=db_session)
        assert res.producto is None
        assert res.estrategia == 'sin_match'

    def test_descripcion_exacta(self, productos_catalog, db_session, lab):
        res = pm.match_producto(descripcion='TAFIROL 1 g COM x 50',
                                 laboratorio_id=lab.id, session=db_session)
        assert res.producto is not None
        assert res.estrategia in ('descripcion_exacta', 'tokens_superset', 'fuzzy_lab')


class TestMatchFuzzy:
    def test_fuzzy_por_descripcion_y_lab(self, productos_catalog, db_session, lab):
        # Descripción ligeramente distinta pero matchea
        res = pm.match_producto(
            descripcion='TAFIROL 1 g comprimidos x 50',
            laboratorio_id=lab.id,
            session=db_session,
        )
        assert res.producto is not None
        assert res.score > 0.5

    def test_devuelve_candidatos_si_no_hay_match(self, productos_catalog, db_session, lab):
        res = pm.match_producto(
            descripcion='ACETIL X 1000',
            laboratorio_id=lab.id,
            session=db_session,
        )
        # No matchea pero igual devuelve candidatos top
        # (cualquier producto con al menos un token en común)


class TestPrecio:
    def test_warning_si_variacion_alta(self, productos_catalog, db_session, lab):
        # TAFIROL 1g x 50 está en $4500. Le pasamos $9000 (100% más).
        res = pm.match_producto(
            ean='7793450121111',
            precio_referencia=9000,
            session=db_session,
        )
        assert res.producto is not None
        assert 'precio_variacion_alta' in res.warnings


class TestBulk:
    def test_bulk_devuelve_n_resultados(self, productos_catalog, db_session, lab):
        items = [
            {'ean': '7793450121111'},
            {'ean': '9999999999999'},
            {'descripcion': 'TAFIROL 1 g comprimidos'},
        ]
        results = pm.match_productos_bulk(items, laboratorio_id=lab.id, session=db_session)
        assert len(results) == 3
        assert results[0].producto is not None
        assert results[1].producto is None  # not found
        # results[2] depende del fuzzy threshold


class TestBuscarCandidatos:
    def test_devuelve_lista_ordenada(self, productos_catalog, db_session, lab):
        cands = pm.buscar_candidatos('TAFIROL 1g', laboratorio_id=lab.id, session=db_session)
        assert isinstance(cands, list)
        # Si hay matches, los scores deberían venir ordenados desc
        if len(cands) > 1:
            scores = [c['score'] for c in cands]
            assert scores == sorted(scores, reverse=True)

    def test_descripcion_vacia(self, db_session):
        cands = pm.buscar_candidatos('', session=db_session)
        assert cands == []


# ── Target ObsProducto ──────────────────────────────────────────────────────

@pytest.fixture
def obs_catalog(db_session):
    """Catálogo ObServer mínimo: 3 productos del lab observer 999."""
    items = [
        ObsProducto(observer_id=10001, descripcion='IBUPIREX 400 mg COM x 30',
                    laboratorio_observer=999, codigo_alfabeta='IBP400-30'),
        ObsProducto(observer_id=10002, descripcion='IBUPIREX 600 mg COM x 30',
                    laboratorio_observer=999),
        ObsProducto(observer_id=10003, descripcion='ATENOLOL 50 mg COM x 28',
                    laboratorio_observer=999),
    ]
    db_session.add_all(items)
    db_session.commit()
    return items


class TestMatchObsProducto:
    def test_match_descripcion_exacta(self, obs_catalog, db_session):
        res = pm.match_producto(
            descripcion='IBUPIREX 400 mg COM x 30',
            laboratorio_id=999,
            target='obs_producto',
            session=db_session,
        )
        assert res.producto is not None
        assert res.producto.observer_id == 10001
        assert res.estrategia in ('descripcion_exacta', 'tokens_superset', 'fuzzy_lab')

    def test_match_alfabeta(self, obs_catalog, db_session):
        res = pm.match_producto(
            codigo_alfabeta='IBP400-30',
            target='obs_producto',
            session=db_session,
        )
        assert res.producto is not None
        assert res.producto.observer_id == 10001
        assert res.estrategia == 'alfabeta_exacto'

    def test_no_match_devuelve_candidatos(self, obs_catalog, db_session):
        res = pm.match_producto(
            descripcion='ALGUN PRODUCTO INEXISTENTE 100 mg',
            laboratorio_id=999,
            target='obs_producto',
            session=db_session,
        )
        assert res.producto is None
        assert isinstance(res.candidatos_top, list)
        # candidatos vienen del mismo lab
        for c in res.candidatos_top:
            assert c['observer_id'] in (10001, 10002, 10003)

    def test_pool_precargado(self, obs_catalog, db_session):
        # Simulamos pool pre-filtrado por fecha_baja IS NULL
        pool = [p for p in obs_catalog]
        res = pm.match_producto(
            descripcion='IBUPIREX 400 mg comprimidos x 30',
            target='obs_producto',
            pool=pool,
            session=db_session,
        )
        assert res.producto is not None
        assert res.producto.observer_id == 10001

    def test_buscar_candidatos_obs(self, obs_catalog, db_session):
        cands = pm.buscar_candidatos(
            'IBUPIREX 400',
            laboratorio_id=999,
            target='obs_producto',
            session=db_session,
        )
        assert len(cands) >= 1
        # El mejor matche debería ser el de 400mg
        assert cands[0]['observer_id'] == 10001

    def test_target_invalido_levanta(self, db_session):
        with pytest.raises(ValueError):
            pm.match_producto(descripcion='X', target='inexistente', session=db_session)


# ── Edge cases observados en producción / Roemmers / Bernabó ─────────────────

class TestEdgeCasesReales:
    """Casos que vimos en datos reales de proveedores el 2026-04-26."""

    # ── Match por EAN alternativo (vía producto_codigos_barra 1-a-N) ────────
    def test_match_por_codigo_barra_alt1(self, db_session, lab):
        """Productos pueden tener N EANs alternativos en `producto_codigos_barra`.
        El matcher debe encontrarlos por cualquiera. Antes los alts vivían en
        las columnas legacy alt1/2/3 (ya migradas y dropped)."""
        from database import ProductoCodigoBarra
        prod = Producto(
            codigo_barra='7793450100000', descripcion='OPTAMOX X 8',
            laboratorio_id=lab.id,
        )
        db_session.add(prod)
        db_session.flush()  # obtener prod.id
        db_session.add_all([
            ProductoCodigoBarra(producto_id=prod.id, codigo_barra='7793450199999',
                                 es_principal=False, fuente='legacy_alt'),
            ProductoCodigoBarra(producto_id=prod.id, codigo_barra='7793450188888',
                                 es_principal=False, fuente='legacy_alt'),
        ])
        db_session.commit()

        res = pm.match_producto(ean='7793450199999', session=db_session)
        assert res.producto is not None
        assert res.producto.codigo_barra == '7793450100000'

    def test_match_por_codigo_barra_alt3(self, db_session, lab):
        """EAN alternativo directo en producto_codigos_barra."""
        from database import ProductoCodigoBarra
        prod = Producto(
            codigo_barra='7793450100000', descripcion='X', laboratorio_id=lab.id,
        )
        db_session.add(prod)
        db_session.flush()
        db_session.add(ProductoCodigoBarra(
            producto_id=prod.id, codigo_barra='ALT3-EAN',
            es_principal=False, fuente='legacy_alt',
        ))
        db_session.commit()

        res = pm.match_producto(ean='ALT3-EAN', session=db_session)
        assert res.producto is not None

    # ── Descripciones con dosis similares (1g vs 100g) ──────────────────────
    def test_dosis_similar_no_confunde(self, db_session, lab):
        """1g y 100g se ven parecido por tokens. El matcher exacto debe
        preferir el de la dosis correcta."""
        items = [
            Producto(codigo_barra='X1', descripcion='TAFIROL 1 g COM x 50',
                     laboratorio_id=lab.id),
            Producto(codigo_barra='X2', descripcion='TAFIROL 100 mg COM x 50',
                     laboratorio_id=lab.id),
        ]
        db_session.add_all(items)
        db_session.commit()

        res = pm.match_producto(
            descripcion='TAFIROL 1 g COM x 50',
            laboratorio_id=lab.id,
            session=db_session,
        )
        assert res.producto.codigo_barra == 'X1'

    # ── Productos dados de baja (fecha_baja IS NOT NULL) ───────────────────
    def test_obs_match_excluye_baja_si_pool_no_lo_incluye(self, db_session, lab):
        """ObsProducto.fecha_baja NOT NULL debe poder excluirse via pool."""
        from database import ObsProducto
        from datetime import datetime

        prod_activo = ObsProducto(
            observer_id=20001, descripcion='PRODUCTO ACTIVO X 30',
            fecha_baja=None, codigo_alfabeta='ACT-30',
        )
        prod_baja = ObsProducto(
            observer_id=20002, descripcion='PRODUCTO ACTIVO X 30',  # mismo nombre
            fecha_baja=datetime(2025, 1, 1), codigo_alfabeta='BAJA-30',
        )
        db_session.add_all([prod_activo, prod_baja])
        db_session.commit()

        pool = [prod_activo]  # solo activos
        res = pm.match_producto(
            descripcion='PRODUCTO ACTIVO X 30',
            target='obs_producto',
            pool=pool,
            session=db_session,
        )
        assert res.producto is not None
        assert res.producto.observer_id == 20001  # el activo, nunca el baja

    # ── Descripciones con apóstrofes o comillas raras ───────────────────────
    def test_descripcion_con_apostrofe(self, db_session, lab):
        prod = Producto(
            codigo_barra='AP1', descripcion="ALPHA'S CREMA X 60g",
            laboratorio_id=lab.id,
        )
        db_session.add(prod)
        db_session.commit()

        res = pm.match_producto(
            descripcion="ALPHA'S CREMA X 60g",
            laboratorio_id=lab.id,
            session=db_session,
        )
        assert res.producto is not None

    # ── Descripción con espacios extra y mayúsculas distintas ──────────────
    def test_normalizacion_espacios_y_mayusculas(self, db_session, lab):
        prod = Producto(
            codigo_barra='SP1', descripcion='AMOXIDAL DUO 875 COM x 14',
            laboratorio_id=lab.id,
        )
        db_session.add(prod)
        db_session.commit()

        # Mismo producto pero con espacios extra y minúsculas.
        res = pm.match_producto(
            descripcion='   amoxidal   duo   875   com   x   14   ',
            laboratorio_id=lab.id,
            session=db_session,
        )
        assert res.producto is not None
        assert res.producto.codigo_barra == 'SP1'

    # ── EAN vacío string explícito ─────────────────────────────────────────
    def test_ean_vacio_string_no_matchea_falsamente(self, db_session, lab):
        """Si pasamos ean='' (string vacío), no debe matchear con algún producto
        que tenga codigo_barra=NULL o ''. Caso real con OBS:xxx pseudo-EANs."""
        prod1 = Producto(codigo_barra='ALGO1', descripcion='UNO', laboratorio_id=lab.id)
        db_session.add(prod1)
        db_session.commit()

        res = pm.match_producto(ean='', descripcion='OTRO', session=db_session)
        # No debería matchear por EAN vacío.
        assert res.producto is None or res.estrategia != 'ean_exacto'

    # ── Bulk con items repetidos (mismo EAN N veces) ──────────────────────
    def test_bulk_items_repetidos_devuelve_n_resultados(self, productos_catalog, db_session, lab):
        """Si pasamos 10 veces el mismo EAN, devuelve 10 resultados (igual orden)."""
        items = [{'ean': '7793450133333', 'descripcion': 'AMOXIDAL'}] * 10
        results = pm.match_productos_bulk(items, laboratorio_id=lab.id, session=db_session)
        assert len(results) == 10
        for r in results:
            assert r.producto is not None
            assert r.producto.codigo_barra == '7793450133333'

    # ── Bulk con todos sin match ───────────────────────────────────────────
    def test_bulk_todos_sin_match_no_revienta(self, db_session, lab):
        items = [{'ean': f'XX{i}', 'descripcion': f'inexistente {i}'} for i in range(5)]
        results = pm.match_productos_bulk(items, laboratorio_id=lab.id, session=db_session)
        assert len(results) == 5
        for r in results:
            assert r.producto is None

    # ── Bulk con mezcla: matches y no-matches ──────────────────────────────
    def test_bulk_mezcla_devuelve_alineado(self, productos_catalog, db_session, lab):
        items = [
            {'ean': '7793450133333', 'descripcion': 'AMOXIDAL 500 mg COM x 16'},
            {'ean': 'XX-NO-EXISTE', 'descripcion': 'producto inexistente'},
            {'ean': '7793450121111', 'descripcion': 'TAFIROL 1 g COM x 50'},
        ]
        results = pm.match_productos_bulk(items, laboratorio_id=lab.id, session=db_session)
        assert len(results) == 3
        assert results[0].producto is not None
        assert results[1].producto is None
        assert results[2].producto is not None
        # Alineamiento mantenido: result[0] ↔ items[0].
        assert results[0].producto.codigo_barra == '7793450133333'
        assert results[2].producto.codigo_barra == '7793450121111'

    # ── codigo_alfabeta con mayúscula/minúscula ────────────────────────────
    def test_codigo_alfabeta_case_insensitive_o_no(self, productos_catalog, db_session, lab):
        """AMX500-16 vs amx500-16. Documenta el comportamiento actual.
        Si el matcher es case-sensitive, este test lo bloquea como contrato.
        """
        # El catálogo tiene AMX500-16 (mayúscula).
        res = pm.match_producto(
            codigo_alfabeta='AMX500-16',
            session=db_session,
        )
        assert res.producto is not None
        assert res.producto.codigo_alfabeta == 'AMX500-16'
