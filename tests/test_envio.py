"""Tests de cotización de envío (Fase 1): zonas pisan, tramos por cuadras,
seed, CRUD y endpoints del panel."""
from bot import envio


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
