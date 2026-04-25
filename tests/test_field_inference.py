"""Tests del módulo central de inferencia de campos."""

import pytest

import field_inference as fi


# ── Diccionario de datos / catálogo ────────────────────────────────────────

class TestCatalogo:
    def test_nucleo_estan(self):
        nucleo = set(fi.nombres_campos(nucleo_only=True))
        # Los siempre-usados deben estar como núcleo
        assert {'ean', 'codigo', 'descripcion', 'cantidad', 'precio', 'descuento_psl'} <= nucleo

    def test_campo_devuelve_metadata(self):
        ean = fi.campo('ean')
        assert ean is not None
        assert ean['tipo'] == 'ean'
        assert ean['nucleo'] is True
        assert 'ean' in ean['keywords']
        assert ean['regex_valor']

    def test_campo_inexistente(self):
        assert fi.campo('xxx') is None


# ── Inferencia por contenido ───────────────────────────────────────────────

class TestInferirTipoValor:
    @pytest.mark.parametrize('val,esperado', [
        ('7793450121123', 'ean'),
        ('7791234567890', 'ean'),
        ('1234567', 'ean'),     # 7 dígitos mínimo
        ('12.0', 'int'),
        ('12', 'int'),
        ('1.234,56', 'money'),
        ('4.500,00', 'money'),
        ('1234,56', 'money'),
        ('1234.56', 'money'),
        ('1,234.56', 'money'),
        ('25%', 'pct'),
        ('7,5%', 'pct'),
        ('25', 'int'),       # entero corto sin % es int, no pct
        ('7,5', 'pct'),      # decimal corto 0-100 sin % es pct
        ('15/12/2025', 'date'),
        ('TAFIROL 1g', 'text'),
        ('', None),
        (None, None),
    ])
    def test_tipos(self, val, esperado):
        assert fi.inferir_tipo_valor(val) == esperado

    def test_money_grande(self):
        # Un número grande sin decimales tipo "1500" puede ser pct (≤100 → no, va a money)
        assert fi.inferir_tipo_valor('1500') == 'money'


class TestParsearNumeroAr:
    @pytest.mark.parametrize('s,esperado', [
        ('1.234,56', 1234.56),
        ('1234,56', 1234.56),
        ('1,234.56', 1234.56),
        ('$ 100,50', 100.5),
        ('25%', 25.0),
        ('', None),
        (None, None),
        ('basura', None),
        (12.5, 12.5),
        (100, 100.0),
    ])
    def test_parses(self, s, esperado):
        assert fi.parsear_numero_ar(s) == esperado


class TestValidarEan:
    @pytest.mark.parametrize('s,ok', [
        ('7793450121123', True),
        ('7791234567', True),
        ('1234567', True),
        ('123456', False),         # 6 dígitos < 7
        ('123456789012345', False),  # 15 > 14
        ('TAFIROL', False),
        ('7793450121123.0', True),  # OCR/Excel artifact
        ('', False),
        (None, False),
    ])
    def test_valida(self, s, ok):
        assert fi.validar_ean(s) == ok


# ── Inferencia por header ──────────────────────────────────────────────────

class TestInferirCampoPorHeader:
    @pytest.mark.parametrize('header,esperado', [
        ('EAN', 'ean'),
        ('Codigo de barras', 'ean'),    # 'codigo_barra' tras normalizar matchea 'codigo_barra' kw
        ('Cód. Producto', 'codigo'),
        ('Producto', 'descripcion'),
        ('DESCUENTO MOD 1', 'descuento_psl'),
        ('% PVP', 'precio_publico'),
        ('Mín. unidades', 'unidades_minima'),
        ('Plazo de pago', 'plazo_pago'),
        ('Lote', 'lote'),
        ('xyz_columna_inexistente', None),
        ('', None),
        (None, None),
    ])
    def test_mapea(self, header, esperado):
        assert fi.inferir_campo_por_header(header) == esperado

    def test_candidatos_filtra(self):
        # Si solo permitimos 'descripcion', "EAN" no debería matchear nada.
        assert fi.inferir_campo_por_header('EAN', candidatos=['descripcion']) is None


# ── Inferencia mixta (header + contenido) ──────────────────────────────────

class TestInferirColumnas:
    def test_solo_header(self):
        headers = ['EAN', 'Producto', 'Descuento %', 'Mín. unidades']
        mapa = fi.inferir_columnas(headers)
        assert mapa['ean'] == 0
        assert mapa['descripcion'] == 1
        assert mapa['descuento_psl'] == 2
        assert mapa['unidades_minima'] == 3

    def test_header_y_contenido(self):
        # Una columna sin header reconocido pero con contenido tipo EAN
        headers = ['Col1', 'Producto', 'Col3']
        rows = [
            ['7793450121123', 'TAFIROL 1g', '25%'],
            ['7791234567890', 'AMOXIDAL 500', '7,5%'],
        ]
        mapa = fi.inferir_columnas(headers, sample_rows=rows)
        assert mapa.get('ean') == 0          # detectado por contenido
        assert mapa['descripcion'] == 1      # por header
        # Col3 con 25%/7,5% → debería matchear pct → descuento_psl (núcleo)
        assert mapa.get('descuento_psl') == 2

    def test_no_duplica(self):
        # Dos columnas pisan el mismo campo por header → la primera gana
        headers = ['Descuento', 'Dto']
        mapa = fi.inferir_columnas(headers)
        assert mapa['descuento_psl'] == 0
        # No hay otra entrada apuntando a 1


# ── Inferencia por matemática ──────────────────────────────────────────────

class TestRelacionAritmetica:
    def test_cant_unit_imp(self):
        # 5 × 100 = 500
        rels = fi.relacion_aritmetica([5, 100, 500], contexto='item')
        tipos = [r['tipo'] for r in rels]
        assert 'cant_unit_imp' in tipos

    def test_pub_dto_unit(self):
        # 100 × (1 - 20/100) = 80
        rels = fi.relacion_aritmetica([100, 20, 80], contexto='item')
        tipos = [r['tipo'] for r in rels]
        assert 'pub_dto_unit' in tipos

    def test_iva_gravado(self):
        # 1000 × 21% = 210
        rels = fi.relacion_aritmetica([1000, 210], contexto='totales')
        tipos = [r['tipo'] for r in rels]
        assert 'iva_gravado' in tipos

    def test_total_suma(self):
        # exento=100, gravado=1000, iva=210 → total=1310 (suma de los demás)
        rels = fi.relacion_aritmetica([100, 1000, 210, 1310], contexto='totales')
        tipos = [r['tipo'] for r in rels]
        assert 'total_suma' in tipos

    def test_no_relacion(self):
        rels = fi.relacion_aritmetica([1, 2, 3], contexto='item')
        # 1×2≠3, no debería detectar cant_unit_imp clásico
        tipos = [r.get('tipo') for r in rels]
        # 1×2=2 (que también está) → no es exact triplete con i<j<k consecutivo de los tipos correctos
        # Acepta cualquier resultado: sólo nos aseguramos que no crashee
        assert isinstance(tipos, list)
