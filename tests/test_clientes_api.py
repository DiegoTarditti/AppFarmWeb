"""Tests para los 8 endpoints /api/clientes/* expuestos en routes/clientes.py.

Cada grupo de tests cubre: 1 happy path + 1 validación/error por endpoint.
Los tests son self-contained: crean sus propios datos en el setup.
"""

import pytest
import database
from bot import store


# ── Helpers ──────────────────────────────────────────────────────────────────

def _crear_cliente_local(session, **kwargs):
    """Crea un cliente local (Cliente) con get_or_create_cliente y
    devuelve (cliente_id, observer_id)."""
    lead = {
        'nombre': kwargs.get('nombre', 'Ana'),
        'apellido': kwargs.get('apellido', 'Martínez'),
        'telefono': kwargs.get('telefono', '341-000'),
        'domicilio': kwargs.get('domicilio', 'Bolivia 1614'),
        'ciudad': kwargs.get('ciudad', 'Rosario'),
    }
    cid = database.get_or_create_cliente(session, lead=lead)
    session.commit()
    return cid


def _crear_obs_cliente(session, observer_id=91001, nombre='Pérez Juan'):
    """Crea un ObsCliente (cliente del ObServer). Devuelve el observer_id."""
    oc = database.ObsCliente(
        observer_id=observer_id,
        apellido_nombre=nombre,
        id_farmacia=10525,
    )
    session.add(oc)
    session.commit()
    return observer_id


def _crear_domicilio(session, conv_id, **kwargs):
    """Crea un domicilio asociado a una conversación, devuelve su id."""
    d = database.DomicilioCliente(
        conversacion_id=conv_id,
        etiqueta=kwargs.get('etiqueta', 'Casa'),
        direccion=kwargs.get('direccion', 'Bolivia 1614'),
        lat=kwargs.get('lat'), lng=kwargs.get('lng'),
    )
    session.add(d)
    session.commit()
    return d.id


# ── 1. GET /api/clientes/buscar?q= ───────────────────────────────────────────

class TestBuscar:

    def test_buscar_por_nombre_devuelve_match(self, client):
        """Happy path: buscar 'Pérez' con cliente cargado → 200 + match en clientes[]."""
        import database
        with database.get_db() as s:
            _crear_obs_cliente(s, observer_id=92001, nombre='Pérez Juan')
        r = client.get('/api/clientes/buscar?q=Pérez')
        assert r.status_code == 200
        d = r.get_json()
        assert 'clientes' in d
        assert any('Pérez' in c.get('nombre', '') for c in d['clientes'])

    def test_buscar_sin_q_devuelve_lista_vacia(self, client):
        """Sin parámetro q → clientes vacío, no rompe."""
        r = client.get('/api/clientes/buscar')
        assert r.status_code == 200
        d = r.get_json()
        assert d.get('clientes') == []

    def test_buscar_sin_resultados(self, client):
        """q sin match en DB → clientes vacío."""
        r = client.get('/api/clientes/buscar?q=ZZZZNOEXISTE')
        assert r.status_code == 200
        d = r.get_json()
        assert d.get('clientes') == []


# ── 2. GET /api/clientes/ficha?cliente_id= OR ?observer_id= ───────────────────

class TestFicha:

    def test_ficha_por_cliente_id(self, client):
        """Happy path: cliente_id válido → ficha con domicilios y raw."""
        import database
        with database.get_db() as s:
            cid = _crear_cliente_local(s, nombre='Lucía', apellido='González')
        r = client.get(f'/api/clientes/ficha?cliente_id={cid}')
        assert r.status_code == 200
        d = r.get_json()
        assert 'domicilios' in d
        assert 'raw' in d
        assert d['raw'].get('nombre') == 'Lucía'

    def test_ficha_por_observer_id_crea_cliente(self, client):
        """observer_id sin cliente local → lo crea y devuelve ficha."""
        import database
        with database.get_db() as s:
            _crear_obs_cliente(s, observer_id=92002, nombre='Carlos García')
        r = client.get('/api/clientes/ficha?observer_id=92002')
        assert r.status_code == 200
        d = r.get_json()
        assert d is not None
        assert 'domicilios' in d

    def test_ficha_sin_params_devuelve_400(self, client):
        """Sin cliente_id ni observer_id → 400 + error."""
        r = client.get('/api/clientes/ficha')
        assert r.status_code == 400
        d = r.get_json()
        assert 'error' in d and 'falta' in d['error'].lower()

    def test_ficha_cliente_id_inexistente_devuelve_404(self, client):
        """cliente_id que no existe → 404 + error."""
        r = client.get('/api/clientes/ficha?cliente_id=999999')
        assert r.status_code == 404
        d = r.get_json()
        assert 'error' in d


# ── 3. POST /api/clientes ────────────────────────────────────────────────────

class TestCrear:

    def test_crear_cliente_ok(self, client):
        """Happy path: datos válidos → 200 + cliente_id."""
        r = client.post('/api/clientes', json={
            'nombre': 'Pedro',
            'apellido': 'Gómez',
            'telefono': '341-123456',
        })
        assert r.status_code == 200
        d = r.get_json()
        assert d.get('ok') is True
        assert isinstance(d.get('cliente_id'), int)

    def test_crear_cliente_body_vacio_devuelve_400(self, client):
        """Body vacío → 400 + error 'sin datos'."""
        r = client.post('/api/clientes', json={})
        assert r.status_code == 400
        d = r.get_json()
        assert d.get('ok') is False
        assert 'sin datos' in (d.get('error') or '').lower()


# ── 4. POST /api/clientes/<cid> ──────────────────────────────────────────────

class TestEditar:

    def test_editar_cliente_ok(self, client):
        """Happy path: editar telefono y ciudad → 200 + datos persistidos."""
        import database
        with database.get_db() as s:
            cid = _crear_cliente_local(s, nombre='Ana', telefono='341-000')
        r = client.post(f'/api/clientes/{cid}', json={
            'telefono': '341-999',
            'ciudad': 'Rosario',
        })
        assert r.status_code == 200
        assert r.get_json().get('ok') is True
        with database.get_db() as s:
            c = s.get(database.Cliente, cid)
            assert c.telefono == '341-999'
            assert c.ciudad == 'Rosario'
            assert c.nombre == 'Ana'  # no se pisó

    def test_editar_cliente_inexistente_devuelve_404(self, client):
        """cid que no existe → 404 + error."""
        r = client.post('/api/clientes/999999', json={'telefono': 'x'})
        assert r.status_code == 404
        d = r.get_json()
        assert d.get('ok') is False
        assert 'no existe' in (d.get('error') or '').lower()


# ── 5. GET /api/clientes/observer/<oid>/domicilios ───────────────────────────

class TestDomiciliosObserver:

    def test_domicilios_sin_datos_devuelve_vacio(self, client):
        """Observer sin domicilios → lista vacía."""
        r = client.get('/api/clientes/observer/99999/domicilios')
        assert r.status_code == 200
        d = r.get_json()
        assert d.get('domicilios') == []

    def test_domicilios_con_datos(self, client):
        """Observer con 1 domicilio → array no vacío."""
        import database
        # Creamos conversación + domicilio asociado por observer_id
        conv = store.get_conversacion('telegram', 'DOM_TEST', nombre='Test')
        with database.get_db() as s:
            d_id = _crear_domicilio(s, conv['id'], lat=-32.95, lng=-60.65)
        # El domicilio se asoció a la conversación. El endpoint busca por
        # observer_id, así que vinculamos cliente + domicilio.
        with database.get_db() as s:
            cid = database.get_or_create_cliente(s, observer_id=999, creado_por=1)
            # Buscar el domicilio recién creado y asignarle cliente_id
            d = s.get(database.DomicilioCliente, d_id)
            d.cliente_id = cid
            s.commit()
        r = client.get('/api/clientes/observer/999/domicilios')
        assert r.status_code == 200
        d = r.get_json()
        assert len(d.get('domicilios', [])) >= 1


# ── 6. GET /api/clientes/geocodificar?q=&loc= ────────────────────────────────

class TestGeocodificar:

    def test_geocodificar_query_corto_devuelve_vacio(self, client):
        """q con menos de 3 caracteres → sugerencias vacío sin llamar externo."""
        r = client.get('/api/clientes/geocodificar?q=ab')
        assert r.status_code == 200
        d = r.get_json()
        assert d.get('sugerencias') == []

    def test_geocodificar_query_valido_ok(self, client, monkeypatch):
        """q válido → 200 + clave sugerencias.
        Monkeypatcheamos la llamada externa para evitar dependencia de API."""

        def mock_sugerencias(q, localidad=None):
            return [
                {'label': 'Córdoba 3400, Rosario', 'lat': -32.94, 'lng': -60.65},
            ]

        monkeypatch.setattr('bot.envio.geocodificar_sugerencias', mock_sugerencias)
        r = client.get('/api/clientes/geocodificar?q=Córdoba%203400&loc=Rosario')
        assert r.status_code == 200
        d = r.get_json()
        assert 'sugerencias' in d


# ── 7. POST /api/clientes/separar-direccion ──────────────────────────────────

class TestSepararDireccion:

    def test_separar_direccion_con_piso_depto(self, client):
        """'Bolivia 1614 DTO 2' → campos separados."""
        r = client.post('/api/clientes/separar-direccion', json={
            'texto': 'Bolivia 1614 DTO 2',
        })
        assert r.status_code == 200
        d = r.get_json()
        assert 'direccion' in d
        assert 'piso' in d
        assert 'depto' in d
        assert 'referencia' in d

    def test_separar_direccion_body_vacio_no_rompe(self, client):
        """Body vacío → no crash, devuelve estructura con strings."""
        r = client.post('/api/clientes/separar-direccion', json={})
        assert r.status_code == 200
        d = r.get_json()
        assert 'direccion' in d
        assert 'piso' in d
        assert 'depto' in d


# ── 8. POST /api/clientes/domicilios/<dom_id>/geo ────────────────────────────

class TestDomicilioSetGeo:

    def test_set_geo_ok(self, client):
        """dom_id válido + lat/lng → 200 ok + lat/lng en respuesta."""
        import database
        conv = store.get_conversacion('telegram', 'GEO_TEST', nombre='Test')
        with database.get_db() as s:
            dom_id = _crear_domicilio(s, conv['id'], lat=None, lng=None)
        r = client.post(f'/api/clientes/domicilios/{dom_id}/geo', json={
            'lat': -32.94,
            'lng': -60.65,
        })
        assert r.status_code == 200
        d = r.get_json()
        assert d.get('ok') is True
        assert d.get('lat') == -32.94
        assert d.get('lng') == -60.65
        with database.get_db() as s:
            dom = s.get(database.DomicilioCliente, dom_id)
            assert dom.lat == -32.94
            assert dom.lng == -60.65

    def test_set_geo_dom_id_inexistente_devuelve_404(self, client):
        """dom_id que no existe → 404 + error."""
        r = client.post('/api/clientes/domicilios/999999/geo', json={
            'lat': -32.94,
            'lng': -60.65,
        })
        assert r.status_code == 404
        d = r.get_json()
        assert d.get('ok') is False
        assert 'no existe' in (d.get('error') or '').lower()

    def test_set_geo_sin_lat_lng_devuelve_400(self, client):
        """Body sin lat/lng → 400 + error."""
        r = client.post('/api/clientes/domicilios/1/geo', json={})
        assert r.status_code == 400
        d = r.get_json()
        assert d.get('ok') is False
        assert 'inválidos' in (d.get('error') or '').lower()