"""Tests de la detección de packs en el wizard de módulos.

`detectar_packs_modulos` (en routes/modulos_import.py) es función pura, no toca DB.
Iteramos esta lógica 4 veces el 2026-04-26; estos tests fijan el comportamiento
acordado para que cualquier cambio futuro tenga que justificar el regreso.
"""
import pytest

from routes.modulos_import import detectar_packs_modulos


# ── Helpers ──────────────────────────────────────────────────────────────────

def _item(modulo, ean=None, codigo=None, descripcion='', destacado=False):
    return {
        'nombre_modulo': modulo,
        'ean': ean,
        'codigo': codigo,
        'descripcion': descripcion,
        '_destacado': destacado,
    }


# ── Caso base: amarillo solo ─────────────────────────────────────────────────

class TestSoloAmarillo:

    def test_amarillo_marca_pack(self):
        items = [
            _item('MOD A', ean='001', descripcion='Producto pack', destacado=True),
            _item('MOD A', ean='002', descripcion='Producto unidad', destacado=False),
        ]
        packs = detectar_packs_modulos(items)
        assert '001' in packs
        assert packs['001']['confianza'] == 'alta'
        assert 'amarillo' in packs['001']['razon']
        assert '002' not in packs

    def test_sin_amarillo_no_marca(self):
        items = [
            _item('MOD A', ean='001', descripcion='Producto'),
            _item('MOD A', ean='002', descripcion='Otro'),
        ]
        packs = detectar_packs_modulos(items)
        assert packs == {}

    def test_amarillo_con_pack_xn_extrae_cantidad(self):
        items = [
            _item('MOD A', ean='001', descripcion='OPTAMOX X 8 PACK X 10', destacado=True),
        ]
        packs = detectar_packs_modulos(items)
        assert packs['001']['cantidad'] == 10
        assert 'PACKx10' in packs['001']['razon']


# ── Combo (primero + PACK + sin_ventas) y flag usar_historico ───────────────

class TestComboHistorico:

    def test_combo_sin_usar_historico_no_marca(self):
        items = [
            _item('MOD A', ean='001', descripcion='OPTAMOX PACK X 10'),  # primero del módulo
            _item('MOD A', ean='002', descripcion='OPTAMOX UNIDAD'),
        ]
        # Sin usar_historico: aunque cumpla todo, no marca (default).
        packs = detectar_packs_modulos(items)
        assert packs == {}

    def test_combo_con_usar_historico_marca_media(self):
        items = [
            _item('MOD A', ean='001', descripcion='OPTAMOX PACK X 10'),
            _item('MOD A', ean='002', descripcion='OPTAMOX UNIDAD'),
        ]
        packs = detectar_packs_modulos(items, usar_historico=True)
        assert '001' in packs
        assert packs['001']['confianza'] == 'media'  # menor que amarillo
        assert 'primero+pack+sin_ventas' in packs['001']['razon']

    def test_combo_segundo_del_modulo_no_marca(self):
        # Solo el primer item del módulo califica para 'primero'.
        items = [
            _item('MOD A', ean='001', descripcion='UNIDAD A'),
            _item('MOD A', ean='002', descripcion='OTRA PACK X 10'),  # 2do, no es primero
        ]
        packs = detectar_packs_modulos(items, usar_historico=True)
        assert packs == {}

    def test_combo_sin_palabra_pack_no_marca(self):
        items = [
            _item('MOD A', ean='001', descripcion='OPTAMOX X 10 ESTUCHES'),
            _item('MOD A', ean='002', descripcion='OPTAMOX X 1'),
        ]
        # Falta la palabra "PACK" en la descripción.
        packs = detectar_packs_modulos(items, usar_historico=True)
        assert '001' not in packs

    def test_combo_con_ventas_no_marca(self):
        items = [
            _item('MOD A', ean='001', descripcion='OPTAMOX PACK X 10'),
            _item('MOD A', ean='002', descripcion='UNIDAD'),
        ]
        # sin_ventas_func devuelve False → tiene ventas históricas → no es pack.
        packs = detectar_packs_modulos(items, usar_historico=True,
                                        sin_ventas_func=lambda e: False)
        assert packs == {}

    def test_combo_modulo_solo_un_item_no_marca(self):
        # 'primero' requiere al menos 2 items en el módulo.
        items = [_item('MOD A', ean='001', descripcion='OPTAMOX PACK X 10')]
        packs = detectar_packs_modulos(items, usar_historico=True)
        assert packs == {}


# ── Heurística envase múltiplo ───────────────────────────────────────────────

class TestEnvaseMultiplo:

    def test_envase_multiplo_sugiere_unidad_y_cantidad(self):
        items = [
            _item('MOD A', ean='001', descripcion='LOSACOR X 60', destacado=True),
            _item('MOD A', ean='002', descripcion='LOSACOR X 30'),
        ]
        # 60 / 30 = 2 → cant_pack=2, ean_unidad=002.
        packs = detectar_packs_modulos(items)
        assert packs['001']['ean_unidad_sug'] == '002'
        assert packs['001']['cantidad'] == 2

    def test_envase_no_divisor_no_sugiere(self):
        items = [
            _item('MOD A', ean='001', descripcion='PROD X 100', destacado=True),
            _item('MOD A', ean='002', descripcion='OTRO X 7'),
        ]
        # 100 % 7 != 0 → no sugiere ean_unidad.
        packs = detectar_packs_modulos(items)
        assert packs['001']['ean_unidad_sug'] == ''

    def test_envase_pack_xn_prevalece_sobre_envase_multiplo(self):
        items = [
            _item('MOD A', ean='001', descripcion='PROD X 8 PACK X 10', destacado=True),
            _item('MOD A', ean='002', descripcion='PROD X 4'),
        ]
        # PACK X 10 indica cant=10 explícito; el envase múltiplo (10/4=2.5)
        # no pisa el dato del regex porque no es divisor exacto.
        packs = detectar_packs_modulos(items)
        assert packs['001']['cantidad'] == 10

    def test_elije_el_mejor_k(self):
        items = [
            _item('MOD A', ean='001', descripcion='X 60', destacado=True),
            _item('MOD A', ean='002', descripcion='X 30'),  # k=2
            _item('MOD A', ean='003', descripcion='X 10'),  # k=6
        ]
        packs = detectar_packs_modulos(items)
        assert packs['001']['cantidad'] == 6
        assert packs['001']['ean_unidad_sug'] == '003'


# ── Edge cases: sin EAN, sin código, sin descripción ─────────────────────────

class TestEdgeCases:

    def test_sin_ean_ni_codigo_ignora(self):
        items = [
            _item('MOD A', descripcion='SIN ID PACK', destacado=True),
            _item('MOD A', ean='002', descripcion='OTRO'),
        ]
        packs = detectar_packs_modulos(items)
        assert packs == {}  # El destacado no tiene EAN → ignorado.

    def test_codigo_funciona_como_fallback(self):
        items = [
            _item('MOD A', codigo='C001', descripcion='X 10', destacado=True),
            _item('MOD A', codigo='C002', descripcion='X 5'),
        ]
        packs = detectar_packs_modulos(items)
        assert 'C001' in packs

    def test_ean_vacio_pero_codigo_lleno_usa_codigo(self):
        items = [
            _item('MOD A', ean='', codigo='C001', descripcion='PACK', destacado=True),
            _item('MOD A', ean='', codigo='C002', descripcion='UNIDAD'),
        ]
        packs = detectar_packs_modulos(items)
        assert 'C001' in packs

    def test_dos_modulos_independientes(self):
        items = [
            _item('MOD A', ean='001', descripcion='', destacado=True),
            _item('MOD A', ean='002'),
            _item('MOD B', ean='100', destacado=True),
            _item('MOD B', ean='200'),
        ]
        packs = detectar_packs_modulos(items)
        # Cada módulo evalúa "primero" por separado.
        assert '001' in packs
        assert '100' in packs

    def test_items_vacio_devuelve_dict_vacio(self):
        assert detectar_packs_modulos([]) == {}

    def test_destacado_y_combo_devuelve_amarillo(self):
        # Si cumple ambos, gana el amarillo (confianza alta).
        items = [
            _item('MOD A', ean='001', descripcion='OPTAMOX PACK X 10', destacado=True),
            _item('MOD A', ean='002', descripcion='UNIDAD'),
        ]
        packs = detectar_packs_modulos(items, usar_historico=True)
        assert packs['001']['confianza'] == 'alta'
        # Razón debe contener ambas señales.
        assert 'amarillo' in packs['001']['razon']
        assert 'primero+pack+sin_ventas' in packs['001']['razon']
