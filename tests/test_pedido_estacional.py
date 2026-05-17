"""Tests del cálculo estacional para /pedido/prueba.

Cubre:
- Fórmula u12m/365 × indice × cobertura - stock.
- Selector de escenario en cascada: producto > droga > auto.
- Mes objetivo default (hoy + lead_dias) y override puntual.
- Flag DISCONTINUADO (excluir) → sugerido_final=0.
- Resguardo mínimo (si sugerido < déficit_mínimo, sube al mínimo).
- Sin ventas → 0.
"""
import json
from datetime import date

import pytest

import database
from database import (
    EstacionalidadEscenario,
    ObsCodigoBarras,
    ObsLaboratorio,
    ObsNombreDroga,
    ObsProducto,
    ProductoFlag,
    TipoPedidoConfig,
)
from services.pedido_estacional import (
    calcular_sugerido_estacional,
    mes_objetivo_default,
    obtener_escenario_aplicable,
    obtener_eans_producto,
    obtener_flag_producto,
)


@pytest.fixture
def session_demo():
    """Setea data sintética y devuelve una session abierta."""
    s = database.SessionLocal()
    try:
        s.add(ObsLaboratorio(observer_id=500, descripcion='LabDemo'))
        s.add(ObsNombreDroga(observer_id=600, descripcion='DrogaDemo'))
        s.add(ObsProducto(
            observer_id=700, descripcion='ProdDemo',
            nombre_droga_observer=600, laboratorio_observer=500,
            fecha_baja=None,
        ))
        s.commit()
        yield s
    finally:
        s.close()


def _add_escenario(s, droga_id, producto_id=None, indices=None,
                   lead=0, cob=30, nombre='Generico'):
    if indices is None:
        indices = [1.0] * 12
    e = EstacionalidadEscenario(
        droga_id=droga_id,
        producto_id=producto_id,
        nombre=nombre,
        indices_json=json.dumps(indices),
        lead_time_dias=lead,
        cobertura_dias=cob,
        es_default=True,
    )
    s.add(e)
    s.commit()
    return e


def _producto(s, observer_id=700):
    return s.query(ObsProducto).filter_by(observer_id=observer_id).first()


class TestFormula:

    def test_sin_escenario_usa_indices_neutros(self, session_demo):
        """Sin escenario en ningún nivel: indices=1.0 todos los meses,
        lead=0, cob=30 → demanda = u12m/365 * 30."""
        r = calcular_sugerido_estacional(
            session_demo, _producto(session_demo),
            u12m=365, stock_actual=0, minimo=0,
            hoy=date(2026, 5, 17),
        )
        # u12m=365 → ritmo_diario_base = 1.0. cob=30 → demanda=30.
        assert r['origen_escenario'] == 'auto'
        assert r['ritmo_diario_base'] == 1.0
        assert r['demanda_proyectada'] == 30
        assert r['sugerido_base'] == 30
        assert r['sugerido_final'] == 30

    def test_con_indice_estacional_amplifica(self, session_demo):
        """Si indice del mes objetivo es 2.0 → demanda se duplica."""
        indices = [1.0] * 12
        indices[5] = 2.0  # Junio
        _add_escenario(session_demo, droga_id=600, indices=indices,
                       lead=30, cob=30)
        # hoy 17 mayo + lead 30d → 16 jun. Mes objetivo = 6.
        r = calcular_sugerido_estacional(
            session_demo, _producto(session_demo),
            u12m=365, stock_actual=0, minimo=0,
            hoy=date(2026, 5, 17),
        )
        assert r['mes_objetivo'] == 6
        assert r['indice_aplicado'] == 2.0
        # ritmo_diario_base 1.0 × indice 2.0 × cob 30 = 60.
        assert r['demanda_proyectada'] == 60
        assert r['sugerido_base'] == 60

    def test_resta_stock_actual(self, session_demo):
        r = calcular_sugerido_estacional(
            session_demo, _producto(session_demo),
            u12m=365, stock_actual=20, minimo=0,
            hoy=date(2026, 5, 17),
        )
        # demanda 30 - stock 20 = 10.
        assert r['sugerido_base'] == 10

    def test_resguardo_minimo_cuando_sugerido_es_cero(self, session_demo):
        """Si stock cubre la demanda pero está debajo del mínimo,
        sube al mínimo."""
        r = calcular_sugerido_estacional(
            session_demo, _producto(session_demo),
            u12m=365, stock_actual=5, minimo=20,
            hoy=date(2026, 5, 17),
        )
        # demanda 30 - stock 5 = 25 → mayor que déficit_min (20-5=15).
        # sugerido = 25.
        assert r['sugerido_base'] == 25

    def test_sin_ventas_no_sugiere_nada(self, session_demo):
        r = calcular_sugerido_estacional(
            session_demo, _producto(session_demo),
            u12m=0, stock_actual=0, minimo=10,
            hoy=date(2026, 5, 17),
        )
        assert r['sugerido_final'] == 0
        assert 'sin ventas' in r['razon'].lower()


class TestEscenarioHierarchy:

    def test_escenario_producto_gana_sobre_droga(self, session_demo):
        # Escenario de droga: indices 1.0 plano.
        _add_escenario(session_demo, droga_id=600, producto_id=None)
        # Escenario del producto: indices con peak en jul (índice 3.0).
        idx_prod = [1.0] * 12
        idx_prod[6] = 3.0
        _add_escenario(session_demo, droga_id=600, producto_id=700,
                       indices=idx_prod, lead=60)

        # hoy 17 mayo + lead 60d → 16 jul (mes 7).
        r = calcular_sugerido_estacional(
            session_demo, _producto(session_demo),
            u12m=365, stock_actual=0, minimo=0,
            hoy=date(2026, 5, 17),
        )
        assert r['origen_escenario'] == 'producto'
        assert r['mes_objetivo'] == 7
        assert r['indice_aplicado'] == 3.0

    def test_droga_gana_si_no_hay_producto(self, session_demo):
        _add_escenario(session_demo, droga_id=600, producto_id=None,
                       lead=15, cob=45)
        r = calcular_sugerido_estacional(
            session_demo, _producto(session_demo),
            u12m=365, stock_actual=0, minimo=0,
            hoy=date(2026, 5, 17),
        )
        assert r['origen_escenario'] == 'droga'
        assert r['lead_dias'] == 15
        assert r['cobertura_dias'] == 45

    def test_obtener_escenario_aplicable_devuelve_none_si_no_hay(self, session_demo):
        esc, origen = obtener_escenario_aplicable(session_demo, 600, 700)
        assert esc is None
        assert origen == 'auto'


class TestMesObjetivo:

    def test_default_es_hoy_mas_lead(self):
        # 17 mayo + 30 días = 16 jun.
        mes, anio, label = mes_objetivo_default(30, hoy=date(2026, 5, 17))
        assert mes == 6
        assert anio == 2026
        assert 'Jun' in label

    def test_default_cruza_anio(self):
        # 15 dic + 30 días = 14 ene del año siguiente.
        mes, anio, label = mes_objetivo_default(30, hoy=date(2026, 12, 15))
        assert mes == 1
        assert anio == 2027

    def test_override_usa_mes_pasado(self, session_demo):
        """Si pido override=3 (marzo) y hoy es mayo, debería usar marzo
        del año próximo (no del actual ya pasado)."""
        r = calcular_sugerido_estacional(
            session_demo, _producto(session_demo),
            u12m=365, stock_actual=0, minimo=0,
            override_mes_obj=3,
            hoy=date(2026, 5, 17),
        )
        assert r['mes_objetivo'] == 3
        assert r['mes_objetivo_anio'] == 2027  # próximo

    def test_override_usa_mes_futuro_mismo_anio(self, session_demo):
        r = calcular_sugerido_estacional(
            session_demo, _producto(session_demo),
            u12m=365, stock_actual=0, minimo=0,
            override_mes_obj=11,
            hoy=date(2026, 5, 17),
        )
        assert r['mes_objetivo'] == 11
        assert r['mes_objetivo_anio'] == 2026


class TestFlags:

    def _seed_flag_lab(self, s, slug='DISCONTINUADO', efecto='excluir',
                      icono='🚫', color='red', lab_id=500):
        # Tipo de flag.
        s.add(TipoPedidoConfig(
            slug=slug, nombre=slug.title(),
            config_json=json.dumps({'efecto_armado': efecto,
                                    'icono': icono, 'color': color}),
            categoria='flag',
        ))
        # Aplicación: a todo el lab 500.
        s.add(ProductoFlag(flag_slug=slug, ean=None, laboratorio_id=lab_id))
        s.commit()

    def test_flag_excluir_pone_sugerido_en_cero(self, session_demo):
        self._seed_flag_lab(session_demo)
        r = calcular_sugerido_estacional(
            session_demo, _producto(session_demo),
            u12m=365, stock_actual=0, minimo=0,
            hoy=date(2026, 5, 17),
        )
        assert r['sugerido_final'] == 0
        assert r['excluido_por_flag'] is True
        assert r['flag']['slug'] == 'DISCONTINUADO'

    def test_flag_solo_badge_no_excluye(self, session_demo):
        self._seed_flag_lab(session_demo, slug='NOTA',
                            efecto='ninguno', icono='📝', color='sky')
        r = calcular_sugerido_estacional(
            session_demo, _producto(session_demo),
            u12m=365, stock_actual=0, minimo=0,
            hoy=date(2026, 5, 17),
        )
        # Demanda 30, no se excluye, solo agrega el badge.
        assert r['sugerido_final'] == 30
        assert r['excluido_por_flag'] is False
        assert r['flag']['slug'] == 'NOTA'

    def test_sin_flag_no_devuelve_dict(self, session_demo):
        r = calcular_sugerido_estacional(
            session_demo, _producto(session_demo),
            u12m=365, stock_actual=0, minimo=0,
            hoy=date(2026, 5, 17),
        )
        assert r['flag'] is None


class TestFlagPorEAN:
    """Bridge ObsProducto → ObsCodigoBarras → ProductoFlag por EAN."""

    def _seed_ean(self, s, producto_id, ean, orden=1, baja=None):
        s.add(ObsCodigoBarras(
            id_codigo_barras=hash((producto_id, ean)) % 10**9,
            producto_observer=producto_id,
            codigo_barras=ean, orden=orden, fecha_baja=baja,
        ))
        s.commit()

    def _seed_tipo_flag(self, s, slug, efecto, icono, color):
        s.add(TipoPedidoConfig(
            slug=slug, nombre=slug.title(),
            config_json=json.dumps({'efecto_armado': efecto,
                                    'icono': icono, 'color': color}),
            categoria='flag',
        ))
        s.commit()

    def test_obtener_eans_devuelve_principal_y_alternativos(self, session_demo):
        self._seed_ean(session_demo, 700, '7791111', orden=1)
        self._seed_ean(session_demo, 700, '7792222', orden=2)
        self._seed_ean(session_demo, 700, '7793333', orden=3)
        eans = obtener_eans_producto(session_demo, 700)
        assert eans == ['7791111', '7792222', '7793333']

    def test_obtener_eans_excluye_dados_de_baja(self, session_demo):
        from datetime import datetime
        self._seed_ean(session_demo, 700, '7791111', orden=1)
        self._seed_ean(session_demo, 700, '7792222', orden=2,
                       baja=datetime(2026, 1, 1))
        eans = obtener_eans_producto(session_demo, 700)
        assert eans == ['7791111']

    def test_flag_por_ean_alt_aplica(self, session_demo):
        """Flag está cargado contra el EAN alt2; debe matchear igual."""
        self._seed_ean(session_demo, 700, '7791111', orden=1)
        self._seed_ean(session_demo, 700, '7792222', orden=2)
        self._seed_tipo_flag(session_demo, 'DISCONTINUADO',
                             'excluir', '🚫', 'red')
        session_demo.add(ProductoFlag(
            flag_slug='DISCONTINUADO', ean='7792222', laboratorio_id=None,
        ))
        session_demo.commit()

        r = calcular_sugerido_estacional(
            session_demo, _producto(session_demo),
            u12m=365, stock_actual=0, minimo=0,
            hoy=date(2026, 5, 17),
        )
        assert r['excluido_por_flag'] is True
        assert r['flag']['slug'] == 'DISCONTINUADO'

    def test_flag_por_ean_gana_a_flag_por_lab(self, session_demo):
        """Si el producto tiene flag por EAN, ese gana al flag del lab."""
        self._seed_ean(session_demo, 700, '7791111', orden=1)
        self._seed_tipo_flag(session_demo, 'NOTA',
                             'ninguno', '📝', 'sky')
        self._seed_tipo_flag(session_demo, 'DISCONTINUADO',
                             'excluir', '🚫', 'red')
        # Flag por lab: DISCONTINUADO. Flag por EAN puntual: NOTA.
        session_demo.add(ProductoFlag(
            flag_slug='DISCONTINUADO', ean=None, laboratorio_id=500,
        ))
        session_demo.add(ProductoFlag(
            flag_slug='NOTA', ean='7791111', laboratorio_id=None,
        ))
        session_demo.commit()

        r = calcular_sugerido_estacional(
            session_demo, _producto(session_demo),
            u12m=365, stock_actual=0, minimo=0,
            hoy=date(2026, 5, 17),
        )
        # Gana el de EAN (NOTA, ninguno) → no excluye.
        assert r['flag']['slug'] == 'NOTA'
        assert r['excluido_por_flag'] is False
        assert r['sugerido_final'] == 30

    def test_flag_por_lab_se_aplica_si_no_hay_ean(self, session_demo):
        """Si no hay flag por EAN, cae al flag por lab."""
        self._seed_ean(session_demo, 700, '7791111', orden=1)
        self._seed_tipo_flag(session_demo, 'SIN_DESCUENTO',
                             'solo_badge', '💡', 'amber')
        session_demo.add(ProductoFlag(
            flag_slug='SIN_DESCUENTO', ean=None, laboratorio_id=500,
        ))
        session_demo.commit()

        r = calcular_sugerido_estacional(
            session_demo, _producto(session_demo),
            u12m=365, stock_actual=0, minimo=0,
            hoy=date(2026, 5, 17),
        )
        assert r['flag']['slug'] == 'SIN_DESCUENTO'
        assert r['excluido_por_flag'] is False  # solo_badge no excluye
