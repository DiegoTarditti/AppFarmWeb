"""Tests del helper aplicar_overrides_planificador (helpers.py).

Cubre las 3 ramas: cant_fija (hard override), oferta_min (piso) y sin override.
Y el orden de precedencia: cant_fija gana cuando ambos están seteados.
"""
from helpers import aplicar_overrides_planificador


class TestCantFija:
    """cant_fija: hard override cuando stock <= minimo."""

    def test_aplica_cuando_stock_igual_minimo(self):
        # stock=10 == minimo=10 → cae al mínimo → cant_fija aplica.
        res, slug, valor = aplicar_overrides_planificador(
            sugerido=18, stock=10, minimo=10, cant_fija=30, oferta_min=None)
        assert (res, slug, valor) == (30, 'cant_fija', 30)

    def test_aplica_cuando_stock_menor_minimo(self):
        res, slug, valor = aplicar_overrides_planificador(
            sugerido=5, stock=3, minimo=10, cant_fija=30, oferta_min=None)
        assert (res, slug, valor) == (30, 'cant_fija', 30)

    def test_no_aplica_si_stock_mayor_minimo(self):
        # stock=20 > minimo=10 → todavía no es momento de reponer cant_fija.
        res, slug, valor = aplicar_overrides_planificador(
            sugerido=8, stock=20, minimo=10, cant_fija=30, oferta_min=None)
        assert (res, slug, valor) == (8, None, None)

    def test_override_sobre_sugerido_mayor(self):
        # Sugerido natural era 50, cant_fija lo BAJA a 30 (decisión operativa).
        res, slug, valor = aplicar_overrides_planificador(
            sugerido=50, stock=2, minimo=10, cant_fija=30, oferta_min=None)
        assert res == 30

    def test_cant_fija_cero_no_aplica(self):
        res, slug, valor = aplicar_overrides_planificador(
            sugerido=8, stock=2, minimo=10, cant_fija=0, oferta_min=None)
        assert slug is None


class TestOfertaMin:
    """oferta_min: piso cuando sugerido > 0 pero < oferta_min."""

    def test_sube_a_oferta_min_cuando_sugerido_menor(self):
        # Sugerido=4, oferta=6 → subir a 6 (gano descuento TRF).
        res, slug, valor = aplicar_overrides_planificador(
            sugerido=4, stock=2, minimo=10, cant_fija=None, oferta_min=6)
        assert (res, slug, valor) == (6, 'oferta_min', 6)

    def test_no_sube_si_sugerido_es_cero(self):
        # Sugerido=0 (sin necesidad) → no compro solo para alcanzar mínimo.
        res, slug, valor = aplicar_overrides_planificador(
            sugerido=0, stock=50, minimo=10, cant_fija=None, oferta_min=6)
        assert (res, slug, valor) == (0, None, None)

    def test_no_modifica_si_sugerido_es_igual(self):
        res, slug, valor = aplicar_overrides_planificador(
            sugerido=6, stock=2, minimo=10, cant_fija=None, oferta_min=6)
        assert (res, slug, valor) == (6, None, None)

    def test_no_modifica_si_sugerido_es_mayor(self):
        # Sugerido=20, oferta=6 → ya cubro el mínimo, dejo 20.
        res, slug, valor = aplicar_overrides_planificador(
            sugerido=20, stock=2, minimo=10, cant_fija=None, oferta_min=6)
        assert (res, slug, valor) == (20, None, None)

    def test_oferta_min_cero_no_aplica(self):
        res, slug, valor = aplicar_overrides_planificador(
            sugerido=4, stock=2, minimo=10, cant_fija=None, oferta_min=0)
        assert slug is None


class TestPrecedencia:
    """cant_fija gana sobre oferta_min cuando ambos aplican."""

    def test_cant_fija_gana_sobre_oferta_min(self):
        # stock<=minimo activa cant_fija. oferta_min ignorada.
        res, slug, valor = aplicar_overrides_planificador(
            sugerido=4, stock=2, minimo=10, cant_fija=30, oferta_min=6)
        assert (res, slug, valor) == (30, 'cant_fija', 30)

    def test_oferta_min_aplica_si_cant_fija_no_dispara(self):
        # stock>minimo → cant_fija NO aplica → cae a oferta_min.
        res, slug, valor = aplicar_overrides_planificador(
            sugerido=4, stock=20, minimo=10, cant_fija=30, oferta_min=6)
        assert (res, slug, valor) == (6, 'oferta_min', 6)


class TestSinOverride:
    def test_ambos_none(self):
        res, slug, valor = aplicar_overrides_planificador(
            sugerido=12, stock=2, minimo=10, cant_fija=None, oferta_min=None)
        assert (res, slug, valor) == (12, None, None)

    def test_inputs_none_no_revientan(self):
        res, slug, valor = aplicar_overrides_planificador(
            sugerido=None, stock=None, minimo=None, cant_fija=None, oferta_min=None)
        assert (res, slug, valor) == (0, None, None)


class TestEfectosConfigurables:
    """Ejes nuevos cant_fija_efecto / oferta_min_efecto (configurables por tipo)."""

    def test_cant_fija_piso_floor_aunque_stock_mayor_minimo(self):
        # stock>minimo: en 'override' no aplicaría; en 'piso' floorea a cant_fija
        # porque cant_fija(30) > sugerido(8).
        res, slug, valor = aplicar_overrides_planificador(
            sugerido=8, stock=20, minimo=10, cant_fija=30, oferta_min=None,
            cant_fija_efecto='piso')
        assert (res, slug, valor) == (30, 'cant_fija', 30)

    def test_cant_fija_piso_no_baja_si_sugerido_mayor(self):
        # piso: si el sugerido ya es mayor que cant_fija, NO lo baja (a diferencia
        # de 'override' que sí lo bajaría).
        res, slug, valor = aplicar_overrides_planificador(
            sugerido=50, stock=20, minimo=10, cant_fija=30, oferta_min=None,
            cant_fija_efecto='piso')
        assert (res, slug, valor) == (50, None, None)

    def test_cant_fija_ninguno_ignora(self):
        res, slug, valor = aplicar_overrides_planificador(
            sugerido=8, stock=2, minimo=10, cant_fija=30, oferta_min=None,
            cant_fija_efecto='ninguno')
        assert (res, slug, valor) == (8, None, None)

    def test_oferta_min_indicador_no_toca_cantidad(self):
        # 'indicador': el chip se muestra aparte, pero NO sube la cantidad.
        res, slug, valor = aplicar_overrides_planificador(
            sugerido=4, stock=2, minimo=10, cant_fija=None, oferta_min=6,
            oferta_min_efecto='indicador')
        assert (res, slug, valor) == (4, None, None)

    def test_oferta_min_ninguno_no_toca_cantidad(self):
        res, slug, valor = aplicar_overrides_planificador(
            sugerido=4, stock=2, minimo=10, cant_fija=None, oferta_min=6,
            oferta_min_efecto='ninguno')
        assert (res, slug, valor) == (4, None, None)
