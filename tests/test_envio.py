"""Tests de cotización de envío: zonas pisan, tramos por cuadras, seed, CRUD,
endpoints (Fase 1) + coords/geocoder/config/pin (Fase 2)."""
from bot import envio
from bot.cerebro import procesar


# ── cotizar (lógica pura sobre la grilla) ────────────────────────────────────

def test_seed_carga_grilla_real():
    envio.seed_si_vacio()
    t = envio.listar_tarifas()
    assert len(t['tramos']) == 5 and len(t['zonas']) == 5
    # idempotente: no duplica
    envio.seed_si_vacio()
    assert len(envio.listar_tarifas()['tramos']) == 5


def test_cotizar_tramos_por_limite():
    envio.seed_si_vacio()
    assert envio.cotizar(cuadras=0)['monto'] == 2500       # hasta 14
    assert envio.cotizar(cuadras=14)['monto'] == 2500
    assert envio.cotizar(cuadras=15)['monto'] == 3000      # 15-24
    assert envio.cotizar(cuadras=34)['monto'] == 3500      # 25-34
    assert envio.cotizar(cuadras=49)['monto'] == 4000      # 35-49
    assert envio.cotizar(cuadras=200)['monto'] == 4500     # 50 o más
    assert envio.cotizar(cuadras=22)['fuente'] == 'tramo'


def test_cotizar_zona_pisa_a_tramo():
    envio.seed_si_vacio()
    # Roldán es zona fija (15000) aunque pasemos pocas cuadras.
    r = envio.cotizar(localidad='Roldán', cuadras=5)
    assert r['monto'] == 15000 and r['fuente'] == 'zona'
    # match tolerante (sin tilde)
    assert envio.cotizar(localidad='roldan')['monto'] == 15000
    assert envio.cotizar(localidad='Refineria')['monto'] == 8000


def test_cotizar_a_convenir_sin_datos():
    envio.seed_si_vacio()
    r = envio.cotizar(localidad='Pergamino', cuadras=None)   # no matchea zona, sin cuadras
    assert r['monto'] is None and r['detalle'] == 'a convenir'


# ── CRUD ─────────────────────────────────────────────────────────────────────

def test_crud_zona_y_tramo():
    z = envio.guardar_zona(None, 'Pérez', 9000)
    assert z['ok']
    assert envio.cotizar(localidad='perez')['monto'] == 9000
    assert envio.guardar_zona(z['id'], 'Pérez', 9500)['ok']
    assert envio.cotizar(localidad='perez')['monto'] == 9500
    assert envio.eliminar_zona(z['id'])['ok']
    assert envio.cotizar(localidad='perez')['monto'] is None or \
        envio.cotizar(localidad='perez')['fuente'] != 'zona'
    assert envio.guardar_zona(None, '', 100)['ok'] is False   # nombre vacío


# ── Endpoints ────────────────────────────────────────────────────────────────

def test_panel_envio_renderiza(client):
    r = client.get('/envio')
    assert r.status_code == 200
    assert b'Cotizador de env' in r.data


def test_api_cotizar_json(client):
    envio.seed_si_vacio()
    r = client.get('/envio/api/cotizar?localidad=Roldán')
    assert r.status_code == 200 and r.get_json()['monto'] == 15000
    r2 = client.get('/envio/api/tarifas')
    assert r2.status_code == 200 and 'tramos' in r2.get_json()


# ── Fase 2: coords / geocoder / config / pin ─────────────────────────────────

def test_guardar_config_no_pisa_coords():
    envio.guardar_config(farmacia_lat=-32.9, farmacia_lng=-60.6)
    envio.guardar_config(factor_cuadras=1.5)   # editar solo el factor
    c = envio.get_config()
    assert c['farmacia_lat'] == -32.9 and c['farmacia_lng'] == -60.6
    assert c['factor_cuadras'] == 1.5


def test_cuadras_desde_coords():
    envio.guardar_config(farmacia_lat=-32.95, farmacia_lng=-60.65,
                         factor_cuadras=1.3, metros_por_cuadra=100)
    assert envio.cuadras_desde_coords(-32.95, -60.65) == 0
    cu = envio.cuadras_desde_coords(-32.959, -60.65)   # ~1 km al sur
    assert 8 <= cu <= 18                                # ~13 cuadras


def test_cotizar_por_coords_circulo_pisa_y_tramo():
    envio.seed_si_vacio()
    envio.guardar_config(farmacia_lat=-32.95, farmacia_lng=-60.65)
    z = next(x for x in envio.listar_tarifas()['zonas'] if x['nombre'] == 'Roldán')
    envio.guardar_zona(z['id'], 'Roldán', 15000, lat=-32.90, lng=-60.91, radio_km=5)
    dentro = envio.cotizar_por_coords(-32.90, -60.91)   # dentro del círculo
    assert dentro['monto'] == 15000 and dentro['fuente'] == 'zona'
    cerca = envio.cotizar_por_coords(-32.952, -60.652)  # cerca de la farmacia
    assert cerca['fuente'] == 'tramo' and cerca['cuadras'] is not None


def test_cotizar_por_coords_sin_config_no_revienta():
    envio.seed_si_vacio()   # sin guardar coords de farmacia
    r = envio.cotizar_por_coords(-32.95, -60.65)
    assert r['monto'] is None   # no puede estimar cuadras → a convenir


def test_cotizar_por_direccion(monkeypatch):
    envio.seed_si_vacio()
    envio.guardar_config(farmacia_lat=-32.95, farmacia_lng=-60.65)
    # atajo por zona (sin geocodificar)
    assert envio.cotizar_por_direccion('lo que sea', localidad='Roldán')['monto'] == 15000
    # con geocoder (mockeado) → tramo
    monkeypatch.setattr(envio, 'geocodificar', lambda *a, **k: (-32.952, -60.652))
    assert envio.cotizar_por_direccion('Córdoba 1500')['fuente'] == 'tramo'
    # geocoder falla → no se puede
    monkeypatch.setattr(envio, 'geocodificar', lambda *a, **k: None)
    assert envio.cotizar_por_direccion('dir rara')['monto'] is None


def test_procesar_pin_cotiza_envio():
    envio.seed_si_vacio()
    envio.guardar_config(farmacia_lat=-32.95, farmacia_lng=-60.65)
    resp = procesar('telegram', 'PIN1', '', nombre='C', linea='Telegram',
                    ubicacion={'lat': -32.952, 'lng': -60.652})
    assert resp and 'envío' in resp['texto'].lower()
