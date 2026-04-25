"""Tests de los endpoints de inferencia (routes/inferencia.py).

Verifican que el contrato HTTP de los endpoints es estable y que delegan
correctamente en field_inference.
"""
import pytest


@pytest.fixture
def login(client):
    """El login real lo provee conftest.py vía LoginManager.request_loader,
    así que el client ya viene autenticado. Devolvemos el client tal cual."""
    return client


class TestInferirColumnas:
    def test_basico(self, login):
        r = login.post('/api/inferir-columnas', json={
            'headers': ['EAN', 'Producto', 'Descuento %'],
        })
        assert r.status_code == 200
        d = r.get_json()
        assert d['mapping']['ean'] == 0
        assert d['mapping']['descripcion'] == 1
        assert d['mapping']['descuento_psl'] == 2

    def test_con_candidatos(self, login):
        r = login.post('/api/inferir-columnas', json={
            'headers': ['EAN', 'Producto', 'Descuento'],
            'candidatos': ['ean', 'descripcion'],   # excluye descuento
        })
        d = r.get_json()
        assert 'descuento_psl' not in d['mapping']
        assert 'campos' in d
        assert set(d['campos'].keys()) <= {'ean', 'descripcion'}

    def test_headers_no_lista(self, login):
        r = login.post('/api/inferir-columnas', json={'headers': 'no es lista'})
        assert r.status_code == 400


class TestInferirTipoValor:
    @pytest.mark.parametrize('valor,esperado', [
        ('7793450121123', 'ean'),
        ('1.234,56', 'money'),
        ('25%', 'pct'),
        ('TAFIROL', 'text'),
        ('', None),
    ])
    def test_tipos(self, login, valor, esperado):
        r = login.post('/api/inferir/tipo-valor', json={'valor': valor})
        assert r.status_code == 200
        assert r.get_json()['tipo'] == esperado


class TestInferirFilaFactura:
    def test_fila_simple(self, login):
        # ean cant desc...money money
        tokens = ['7793450121123', '5', 'TAFIROL', '500', 'mg', '1500,00', '7500,00']
        r = login.post('/api/inferir/fila-factura', json={'tokens': tokens})
        assert r.status_code == 200
        d = r.get_json()
        a = d['asignaciones']
        assert a['codigo_barra'] == 0
        assert a['cantidad'] == 1
        assert a['precio_unitario'] == 5
        assert a['importe'] == 6
        assert a['descripcion'] == [2, 4]

    def test_tokens_vacio(self, login):
        r = login.post('/api/inferir/fila-factura', json={'tokens': []})
        assert r.status_code == 200
        d = r.get_json()
        assert d['asignaciones'] == {}


class TestInferirRelaciones:
    def test_cant_unit_imp(self, login):
        r = login.post('/api/inferir/relaciones', json={
            'valores': [5, 100, 500],
            'contexto': 'item',
        })
        assert r.status_code == 200
        rels = r.get_json()['relaciones']
        tipos = [x['tipo'] for x in rels]
        assert 'cant_unit_imp' in tipos

    def test_iva_gravado(self, login):
        r = login.post('/api/inferir/relaciones', json={
            'valores': [1000, 210],
            'contexto': 'totales',
        })
        rels = r.get_json()['relaciones']
        tipos = [x['tipo'] for x in rels]
        assert 'iva_gravado' in tipos

    def test_no_numericos(self, login):
        r = login.post('/api/inferir/relaciones', json={
            'valores': ['no es numero'],
            'contexto': 'item',
        })
        assert r.status_code == 400
