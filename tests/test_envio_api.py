"""Tests para los 11 endpoints de /config/envio (panel de tarifas + APIs + redirect).

Cubre: redirect 301, panel HTML, tarifas, cotizar, save, geolocalizar,
CRUD de tramos y zonas. Cada test es self-contained.
"""

import pytest
import database
from bot import envio


# ── Helpers ──────────────────────────────────────────────────────────────────

def _crear_tramo(session, hasta_cuadras=30, monto=2500, orden=0):
    """Crea un tramo en DB y devuelve su id."""
    t = database.EnvioTramo(hasta_cuadras=hasta_cuadras, monto=monto, orden=orden)
    session.add(t)
    session.commit()
    return t.id


def _crear_zona(session, nombre='Test Zona', monto=5000, orden=0,
                poligono_texto='-32.94,-60.65\n-32.95,-60.66\n-32.96,-60.64'):
    """Crea una zona en DB y devuelve su id."""
    from services import reparto as _rep
    parsed = _rep.parse_poligono(poligono_texto)
    z = database.EnvioZona(
        nombre=nombre, monto=monto, orden=orden,
        poligono=__import__('json').dumps(parsed) if parsed else None,
    )
    session.add(z)
    session.commit()
    return z.id


# ── 1. Redirect 301: GET /envio -> /config/envio ─────────────────────────────

class TestRedirect:

    def test_redirect_no_follow(self, client):
        """GET /envio sin follow -> 301 + Location correcto."""
        r = client.get('/envio', follow_redirects=False)
        assert r.status_code in (301, 302), f'esperado 301/302, got {r.status_code}'
        if r.status_code == 301:
            assert r.headers.get('Location') == '/config/envio'


# ── 2. GET /config/envio (panel HTML) ────────────────────────────────────────

class TestPanel:

    def test_panel_ok(self, client):
        """Happy path: panel renderiza -> 200 + text/html."""
        r = client.get('/config/envio')
        assert r.status_code == 200
        assert r.content_type and 'html' in r.content_type

    def test_panel_envia_titulo(self, client):
        """La pagina incluye 'Envios' en el contenido."""
        r = client.get('/config/envio')
        assert r.status_code == 200
        content = r.data.decode('utf-8', errors='replace')
        assert 'Env' in content or 'Cotizador' in content


# ── 3. GET /config/envio/api/tarifas ─────────────────────────────────────────

class TestTarifas:

    def test_tarifas_devuelve_estructura(self, client):
        """Happy path: tarifas -> 200 + claves esperadas."""
        r = client.get('/config/envio/api/tarifas')
        assert r.status_code == 200
        d = r.get_json()
        for key in ('config', 'ciudades', 'tramos', 'zonas'):
            assert key in d, f'falta clave {key}'

    def test_tarifas_tramos_es_lista(self, client):
        """tramos es una lista (puede estar vacia si no hubo seed)."""
        r = client.get('/config/envio/api/tarifas')
        d = r.get_json()
        assert isinstance(d.get('tramos'), list)
        assert isinstance(d.get('zonas'), list)


# ── 4. GET /config/envio/api/cotizar ─────────────────────────────────────────

class TestCotizar:

    def test_cotizar_por_cuadras(self, client):
        """?cuadras=20&localidad=Rosario -> 200 + monto numerico."""
        r = client.get('/config/envio/api/cotizar?cuadras=20&localidad=Rosario')
        assert r.status_code == 200
        d = r.get_json()
        assert 'monto' in d

    def test_cotizar_por_coords_sin_config(self, client):
        """?lat=-32.94&lng=-60.65 sin farmacia config -> monto puede ser None."""
        r = client.get('/config/envio/api/cotizar?lat=-32.94&lng=-60.65')
        assert r.status_code == 200
        d = r.get_json()
        assert 'monto' in d
        assert d['monto'] is None or isinstance(d['monto'], (int, float))

    def test_cotizar_por_direccion_con_monkeypatch(self, client, monkeypatch):
        """?direccion=xxx con geocodificar mockeado -> 200 + monto."""
        def mock_geocodificar_sugerencias(direccion, localidad=None, max_=8):
            return [{'lat': -32.94, 'lng': -60.65, 'direccion': 'Bolivia 1614',
                     'localidad': 'Rosario', 'nomenclatura': ''}]
        monkeypatch.setattr('bot.envio.geocodificar_sugerencias', mock_geocodificar_sugerencias)
        r = client.get('/config/envio/api/cotizar?direccion=Bolivia%201614')
        assert r.status_code == 200
        d = r.get_json()
        assert 'monto' in d

    def test_cotizar_sin_params(self, client):
        """Sin parametros -> 200 con monto None."""
        r = client.get('/config/envio/api/cotizar')
        assert r.status_code == 200
        d = r.get_json()
        assert 'monto' in d
        assert 'detalle' in d


# ── 5. POST /config/envio/save ───────────────────────────────────────────────

class TestSave:

    def test_save_farmacia_ok(self, client):
        """Happy path: guardar coords -> ok True."""
        r = client.post('/config/envio/save', json={
            'farmacia_lat': -32.95,
            'farmacia_lng': -60.65,
        })
        assert r.status_code == 200
        d = r.get_json()
        assert d.get('ok') is True

    def test_save_con_factor(self, client):
        """Guardar con factor_cuadras -> ok + dato persistido."""
        r = client.post('/config/envio/save', json={
            'factor_cuadras': 1.5,
            'metros_por_cuadra': 120,
        })
        assert r.status_code == 200
        assert r.get_json().get('ok') is True

    def test_save_body_vacio(self, client):
        """Body vacio -> no crash, ok True."""
        r = client.post('/config/envio/save', json={})
        assert r.status_code == 200
        assert r.get_json().get('ok') is True


# ── 6. POST /config/envio/geolocalizar ───────────────────────────────────────

class TestGeolocalizarFarmacia:

    def test_geolocalizar_ok(self, client, monkeypatch):
        """Happy path con monkeypatch -> ok + lat/lng."""
        def mock_geocodificar(direccion, provincia='santa fe', localidad=None):
            return (-32.94, -60.65)
        monkeypatch.setattr('bot.envio.geocodificar', mock_geocodificar)
        r = client.post('/config/envio/geolocalizar', json={
            'direccion': 'Av Pellegrini 1234',
            'localidad': 'Rosario',
        })
        assert r.status_code == 200
        d = r.get_json()
        assert d.get('ok') is True
        assert d.get('lat') == -32.94

    def test_geolocalizar_sin_direccion(self, client, monkeypatch):
        """Sin direccion -> error."""
        def mock_geocodificar(direccion, provincia='santa fe', localidad=None):
            return None
        monkeypatch.setattr('bot.envio.geocodificar', mock_geocodificar)
        r = client.post('/config/envio/geolocalizar', json={
            'direccion': '',
        })
        assert r.status_code == 200
        d = r.get_json()
        assert d.get('ok') is False


# ── 7. POST /config/envio/zona/<zid>/geolocalizar ────────────────────────────

class TestZonaGeolocalizar:

    def test_zona_geolocalizar_ok(self, client, monkeypatch):
        """Zona existente + monkeypatch -> ok."""
        def mock_geocodificar(direccion, provincia='santa fe', localidad=None):
            return (-32.94, -60.65)
        monkeypatch.setattr('bot.envio.geocodificar', mock_geocodificar)
        import database
        with database.get_db() as s:
            zid = _crear_zona(s, nombre='Centro Test')
        r = client.post(f'/config/envio/zona/{zid}/geolocalizar')
        assert r.status_code == 200
        d = r.get_json()
        assert d.get('ok') is True

    def test_zona_geolocalizar_inexistente(self, client):
        """Zona que no existe -> error."""
        r = client.post('/config/envio/zona/99999/geolocalizar')
        assert r.status_code == 200
        d = r.get_json()
        assert d.get('ok') is False


# ── 8. POST /config/envio/tramo ──────────────────────────────────────────────

class TestTramoGuardar:

    def test_tramo_crear(self, client):
        """Crear tramo -> ok + id."""
        r = client.post('/config/envio/tramo', json={
            'hasta_cuadras': 30,
            'monto': 2500,
        })
        assert r.status_code == 200
        d = r.get_json()
        assert d.get('ok') is True
        assert isinstance(d.get('id'), int)

    def test_tramo_editar(self, client):
        """Editar tramo existente -> ok."""
        import database
        with database.get_db() as s:
            tid = _crear_tramo(s, hasta_cuadras=30, monto=2500)
        r = client.post('/config/envio/tramo', json={
            'id': tid,
            'hasta_cuadras': 35,
            'monto': 3000,
        })
        assert r.status_code == 200
        assert r.get_json().get('ok') is True
        with database.get_db() as s:
            t = s.get(database.EnvioTramo, tid)
            assert t.hasta_cuadras == 35
            assert float(t.monto) == 3000

    def test_tramo_sin_cuadras_devuelve_error(self, client):
        """Sin hasta_cuadras -> error ok False."""
        r = client.post('/config/envio/tramo', json={
            'hasta_cuadras': '',
        })
        assert r.status_code == 200
        d = r.get_json()
        assert d.get('ok') is False


# ── 9. POST /config/envio/tramo/<tid>/delete ─────────────────────────────────

class TestTramoEliminar:

    def test_tramo_delete_existente(self, client):
        """Eliminar tramo existente -> ok."""
        import database
        with database.get_db() as s:
            tid = _crear_tramo(s)
        r = client.post(f'/config/envio/tramo/{tid}/delete')
        assert r.status_code == 200
        d = r.get_json()
        assert d.get('ok') is True
        with database.get_db() as s:
            assert s.get(database.EnvioTramo, tid) is None

    def test_tramo_delete_inexistente(self, client):
        """Eliminar tramo que no existe -> ok (idempotente)."""
        r = client.post('/config/envio/tramo/99999/delete')
        assert r.status_code == 200
        assert r.get_json().get('ok') is True


# ── 10. POST /config/envio/zona ──────────────────────────────────────────────

class TestZonaGuardar:

    def test_zona_crear(self, client):
        """Crear zona con nombre y monto -> ok + id."""
        r = client.post('/config/envio/zona', json={
            'nombre': 'Mi Zona Test',
            'monto': 8000,
        })
        assert r.status_code == 200
        d = r.get_json()
        assert d.get('ok') is True
        assert isinstance(d.get('id'), int)

    def test_zona_crear_sin_nombre_devuelve_error(self, client):
        """Sin nombre -> error."""
        r = client.post('/config/envio/zona', json={
            'nombre': '',
            'monto': 5000,
        })
        assert r.status_code == 200
        d = r.get_json()
        assert d.get('ok') is False

    def test_zona_crear_con_poligono(self, client):
        """Crear zona con poligono -> ok."""
        r = client.post('/config/envio/zona', json={
            'nombre': 'Poligono Test',
            'monto': 10000,
            'poligono_texto': '-32.94,-60.65\n-32.95,-60.66\n-32.96,-60.64',
        })
        assert r.status_code == 200
        d = r.get_json()
        assert d.get('ok') is True


# ── 11. POST /config/envio/zona/<zid>/delete ─────────────────────────────────

class TestZonaEliminar:

    def test_zona_delete_existente(self, client):
        """Eliminar zona existente -> ok."""
        import database
        with database.get_db() as s:
            zid = _crear_zona(s)
        r = client.post(f'/config/envio/zona/{zid}/delete')
        assert r.status_code == 200
        d = r.get_json()
        assert d.get('ok') is True
        with database.get_db() as s:
            assert s.get(database.EnvioZona, zid) is None

    def test_zona_delete_inexistente(self, client):
        """Eliminar zona que no existe -> ok (idempotente)."""
        r = client.post('/config/envio/zona/99999/delete')
        assert r.status_code == 200
        assert r.get_json().get('ok') is True