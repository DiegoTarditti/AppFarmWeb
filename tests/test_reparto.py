"""Tests de rutas de reparto (v1): cuadrantes, asignación, export, panel."""
from bot import envio, store
from services import reparto


def _set_farmacia():
    envio.guardar_config(farmacia_lat=-32.95, farmacia_lng=-60.65)


# ── Motor (cuadrantes / link) ────────────────────────────────────────────────

def test_cuadrante_de_4_sectores():
    _set_farmacia()
    assert reparto.cuadrante_de(-32.90, -60.65) == 'N'   # más al norte (lat mayor)
    assert reparto.cuadrante_de(-33.00, -60.65) == 'S'   # sur
    assert reparto.cuadrante_de(-32.95, -60.60) == 'E'   # este (lng mayor)
    assert reparto.cuadrante_de(-32.95, -60.70) == 'O'   # oeste


def test_cuadrante_sin_config_es_none():
    # sin coords de farmacia configuradas → no se puede calcular
    assert reparto.cuadrante_de(-32.90, -60.65) is None


def test_link_google_maps():
    _set_farmacia()
    link = reparto.link_google_maps([(-32.90, -60.65), (-32.92, -60.66)])
    assert link and 'google.com/maps' in link and 'waypoints=' in link


def test_seed_y_ruta_para_cuadrante():
    import database
    reparto.seed_rutas_si_vacio()
    with database.get_db() as s:
        assert reparto.ruta_para_cuadrante(s, 'N').nombre == 'Norte'
        assert reparto.ruta_para_cuadrante(s, 'O').cuadrante == 'O'
    reparto.seed_rutas_si_vacio()   # idempotente
    with database.get_db() as s:
        assert s.query(database.RutaReparto).count() == 4


# ── Endpoints ────────────────────────────────────────────────────────────────

def test_alta_pedido_auto_asigna(client):
    _set_farmacia()
    reparto.seed_rutas_si_vacio()
    conv = store.get_conversacion('telegram', 'REP1', nombre='C')
    dom = store.guardar_domicilio(conv['id'], etiqueta='Casa',
                                  lat=-32.90, lng=-60.65, origen='pin')   # norte
    r = client.post('/reparto/pedido', json={'cliente_nombre': 'Ana',
                                             'domicilio_id': dom['id'], 'nota': 'x'})
    assert r.status_code == 200
    d = r.get_json()
    assert d['ok'] and d['cuadrante'] == 'N' and d['asignado']
    data = client.get('/reparto/api').get_json()
    assert len(data['pedidos']) == 1
    ruta_n = next(x for x in data['rutas'] if x['cuadrante'] == 'N')
    assert data['pedidos'][0]['ruta_id'] == ruta_n['id']


def test_reasignar_y_estado(client):
    _set_farmacia()
    reparto.seed_rutas_si_vacio()
    conv = store.get_conversacion('telegram', 'REP2', nombre='C')
    dom = store.guardar_domicilio(conv['id'], etiqueta='Casa',
                                  lat=-32.90, lng=-60.65, origen='pin')
    pid = client.post('/reparto/pedido',
                      json={'cliente_nombre': 'Ana', 'domicilio_id': dom['id']}).get_json()['id']
    rutas = client.get('/reparto/api').get_json()['rutas']
    ruta_sur = next(x for x in rutas if x['cuadrante'] == 'S')
    assert client.post(f'/reparto/pedido/{pid}/asignar',
                       json={'ruta_id': ruta_sur['id']}).get_json()['ok']
    assert client.post(f'/reparto/pedido/{pid}/estado',
                       json={'estado': 'entregado'}).get_json()['ok']
    p = client.get('/reparto/api').get_json()['pedidos'][0]
    assert p['ruta_id'] == ruta_sur['id'] and p['estado'] == 'entregado'


def test_export_arma_link(client):
    _set_farmacia()
    reparto.seed_rutas_si_vacio()
    conv = store.get_conversacion('telegram', 'REP3', nombre='C')
    dom = store.guardar_domicilio(conv['id'], etiqueta='Casa',
                                  lat=-32.90, lng=-60.65, origen='pin')
    client.post('/reparto/pedido', json={'cliente_nombre': 'Ana', 'domicilio_id': dom['id']})
    ruta_n = next(x for x in client.get('/reparto/api').get_json()['rutas'] if x['cuadrante'] == 'N')
    d = client.get(f"/reparto/ruta/{ruta_n['id']}/export").get_json()
    assert d['link'] and 'google.com/maps' in d['link'] and len(d['pedidos']) == 1


def test_paneles_renderizan(client):
    assert b'Armar reparto' in client.get('/reparto').data
    assert client.get('/rutas').status_code == 200


# ── Fase 2: secuenciación + mapa ─────────────────────────────────────────────

def test_secuenciar_vecino_mas_cercano():
    _set_farmacia()   # farmacia en lng -60.65
    items = [{'id': 3, 'lat': -32.95, 'lng': -60.62},
             {'id': 1, 'lat': -32.95, 'lng': -60.64},
             {'id': 2, 'lat': -32.95, 'lng': -60.63}]
    assert [it['id'] for it in reparto.secuenciar(items)] == [1, 2, 3]


def test_secuenciar_sin_coords_al_final():
    _set_farmacia()
    items = [{'id': 1, 'lat': -32.95, 'lng': -60.64}, {'id': 2, 'lat': None, 'lng': None}]
    assert [it['id'] for it in reparto.secuenciar(items)] == [1, 2]


def test_optimizar_setea_orden(client):
    _set_farmacia()
    reparto.seed_rutas_si_vacio()
    conv = store.get_conversacion('telegram', 'REPO', nombre='C')
    d_lejos = store.guardar_domicilio(conv['id'], etiqueta='a', lat=-32.95, lng=-60.62, origen='pin')
    d_cerca = store.guardar_domicilio(conv['id'], etiqueta='b', lat=-32.95, lng=-60.64, origen='pin')
    for d in (d_lejos, d_cerca):
        client.post('/reparto/pedido', json={'cliente_nombre': 'x', 'domicilio_id': d['id']})
    ruta_e = next(x for x in client.get('/reparto/api').get_json()['rutas'] if x['cuadrante'] == 'E')
    assert client.post(f"/reparto/ruta/{ruta_e['id']}/optimizar", json={}).get_json()['ok']
    peds = sorted(client.get('/reparto/api').get_json()['pedidos'], key=lambda p: p['orden'])
    assert [p['orden'] for p in peds] == [1, 2]
    assert peds[0]['lng'] == -60.64   # el más cercano a la farmacia va primero


def test_api_incluye_farmacia(client):
    _set_farmacia()
    assert client.get('/reparto/api').get_json()['farmacia']['lat'] == -32.95


# ── Fase 3: prioridad ────────────────────────────────────────────────────────

def test_secuenciar_prioriza_urgentes():
    _set_farmacia()
    # normal MUY cerca, urgente lejos → el urgente igual va PRIMERO
    items = [{'id': 1, 'lat': -32.95, 'lng': -60.64, 'prioridad': 'normal'},
             {'id': 2, 'lat': -32.95, 'lng': -60.55, 'prioridad': 'urgente'}]
    assert [it['id'] for it in reparto.secuenciar(items)] == [2, 1]


def test_api_incluye_ciudades(client):
    assert 'ciudades' in client.get('/reparto/api').get_json()


def test_parse_poligono_coords_y_geojson():
    p = reparto.parse_poligono('-32.95, -60.65\n-32.96, -60.64\n-32.94, -60.63')
    assert p and len(p) == 3 and p[0] == [-32.95, -60.65]
    gj = ('{"type":"FeatureCollection","features":[{"type":"Feature","geometry":'
          '{"type":"Polygon","coordinates":[[[-60.65,-32.95],[-60.64,-32.96],'
          '[-60.63,-32.94],[-60.65,-32.95]]]}}]}')
    p2 = reparto.parse_poligono(gj)
    assert p2 and p2[0] == [-32.95, -60.65]   # GeoJSON [lng,lat] → [lat,lng]
    assert reparto.parse_poligono('') is None
    assert reparto.parse_poligono('una sola línea') is None


def test_punto_en_poligono():
    sq = [[-32.96, -60.66], [-32.96, -60.64], [-32.94, -60.64], [-32.94, -60.66]]
    assert reparto._punto_en_poligono(-32.95, -60.65, sq) is True
    assert reparto._punto_en_poligono(-32.90, -60.65, sq) is False


def test_ruta_para_punto_zona_pisa_y_fuera_es_none():
    import json as _j

    import database
    _set_farmacia()
    reparto.seed_rutas_si_vacio()
    sq = [[-32.96, -60.66], [-32.96, -60.64], [-32.94, -60.64], [-32.94, -60.66]]
    with database.get_db() as s:
        oeste = next(r for r in s.query(database.RutaReparto).all() if r.cuadrante == 'O')
        oeste.poligono = _j.dumps(sq)
        s.commit()
        oid = oeste.id
        dentro = reparto.ruta_para_punto(s, -32.95, -60.648)   # dentro del polígono
        assert dentro and dentro.id == oid
        # ya hay una zona definida → un punto fuera de toda zona = sin asignar
        assert reparto.ruta_para_punto(s, -33.5, -60.65) is None


def test_seed_distritos_oficiales():
    import database
    r = reparto.seed_distritos_oficiales()
    assert r['ok'] and r['cargados'] == 6
    with database.get_db() as s:
        ds = (s.query(database.RutaReparto)
              .filter(database.RutaReparto.nombre.like('Distrito %')).all())
        assert len(ds) == 6 and all(x.poligono for x in ds)
    reparto.seed_distritos_oficiales()   # idempotente: no duplica
    with database.get_db() as s:
        assert (s.query(database.RutaReparto)
                .filter(database.RutaReparto.nombre.like('Distrito %')).count()) == 6


def test_alta_guarda_prioridad(client):
    _set_farmacia()
    reparto.seed_rutas_si_vacio()
    conv = store.get_conversacion('telegram', 'REPP', nombre='C')
    dom = store.guardar_domicilio(conv['id'], etiqueta='Casa', lat=-32.90, lng=-60.65, origen='pin')
    client.post('/reparto/pedido', json={'cliente_nombre': 'Ana',
                                         'domicilio_id': dom['id'], 'prioridad': 'urgente'})
    p = client.get('/reparto/api').get_json()['pedidos'][0]
    assert p['prioridad'] == 'urgente'


# ── Cadetes ──────────────────────────────────────────────────────────────────

def test_cadete_crud(client):
    cid = client.post('/cadetes', json={'nombre': 'Pirulo', 'telefono': '341-555',
                                        'tarifa_dia': 12000}).get_json()['id']
    cs = client.get('/cadetes/api').get_json()['cadetes']
    c = next(x for x in cs if x['id'] == cid)
    assert c['nombre'] == 'Pirulo' and c['telefono'] == '341-555'
    assert c['tarifa_dia'] == 12000.0 and c['activo'] and c['zonas'] == 0
    # editar
    client.post('/cadetes', json={'id': cid, 'nombre': 'Pirulo R.', 'activo': False})
    c = next(x for x in client.get('/cadetes/api').get_json()['cadetes'] if x['id'] == cid)
    assert c['nombre'] == 'Pirulo R.' and not c['activo']
    # baja
    assert client.post(f'/cadetes/{cid}/delete').get_json()['ok']
    assert all(x['id'] != cid for x in client.get('/cadetes/api').get_json()['cadetes'])


def test_cadete_sin_nombre_falla(client):
    r = client.post('/cadetes', json={'telefono': 'x'})
    assert r.status_code == 400 and not r.get_json()['ok']


def test_asignar_cadete_a_ruta_y_cuenta_zonas(client):
    reparto.seed_rutas_si_vacio()
    cid = client.post('/cadetes', json={'nombre': 'Gabriel'}).get_json()['id']
    rutas = client.get('/rutas/api').get_json()['rutas']
    r1, r2 = rutas[0]['id'], rutas[1]['id']
    # un cadete cubre VARIAS zonas
    client.post('/rutas', json={'id': r1, 'cadete_id': cid})
    client.post('/rutas', json={'id': r2, 'cadete_id': cid})
    d = client.get('/rutas/api').get_json()
    rd = {x['id']: x for x in d['rutas']}
    assert rd[r1]['cadete_id'] == cid and rd[r1]['cadete'] == 'Gabriel'
    assert rd[r2]['cadete_id'] == cid
    # el /cadetes/api trae el conteo de zonas
    cc = next(x for x in client.get('/cadetes/api').get_json()['cadetes'] if x['id'] == cid)
    assert cc['zonas'] == 2


def test_baja_cadete_desvincula_rutas(client):
    reparto.seed_rutas_si_vacio()
    cid = client.post('/cadetes', json={'nombre': 'Temporal'}).get_json()['id']
    rid = client.get('/rutas/api').get_json()['rutas'][0]['id']
    client.post('/rutas', json={'id': rid, 'cadete_id': cid})
    client.post(f'/cadetes/{cid}/delete')
    rd = next(x for x in client.get('/rutas/api').get_json()['rutas'] if x['id'] == rid)
    assert rd['cadete_id'] is None   # la ruta queda, sin cadete


def test_cadetes_panel_renderiza(client):
    assert client.get('/cadetes').status_code == 200


# ── Herencia cadete ruta → pedido ──────────────────────────────────────────

def _make_pedido_mock(ruta_id, cadete_id=None):
    """Crea un objeto mock de PedidoReparto con los atributos que usa
    cadete_efectivo_id."""
    class Mock:
        pass
    m = Mock()
    m.ruta_id = ruta_id
    m.cadete_id = cadete_id
    return m


def test_cadete_efectivo_hereda_de_ruta():
    """Pedido sin cadete propio hereda el de su ruta."""
    from services.reparto import cadete_efectivo_id
    rutas_cad = {1: 10, 2: 20}  # ruta_id → cadete_id
    p = _make_pedido_mock(ruta_id=1, cadete_id=None)
    assert cadete_efectivo_id(p, rutas_cad) == 10

    p2 = _make_pedido_mock(ruta_id=2, cadete_id=None)
    assert cadete_efectivo_id(p2, rutas_cad) == 20

    # ruta sin cadete en el mapa → None
    p3 = _make_pedido_mock(ruta_id=99, cadete_id=None)
    assert cadete_efectivo_id(p3, rutas_cad) is None


def test_cadete_efectivo_override_gana():
    """Si el pedido tiene cadete_id propio, ese gana sobre el de la ruta."""
    from services.reparto import cadete_efectivo_id
    rutas_cad = {1: 10}  # la ruta 1 tiene cadete 10
    p = _make_pedido_mock(ruta_id=1, cadete_id=99)  # pero el pedido fuerza el 99
    assert cadete_efectivo_id(p, rutas_cad) == 99


def test_cadete_efectivo_sin_ruta_es_none():
    """Pedido sin ruta → cadete_efectivo_id = None."""
    from services.reparto import cadete_efectivo_id
    p = _make_pedido_mock(ruta_id=None)
    assert cadete_efectivo_id(p, {1: 10}) is None


def test_cadete_efectivo_con_ruta_sin_mapa_es_none():
    """Si no se pasa rutas_cadete, el resultado debe ser None."""
    from services.reparto import cadete_efectivo_id
    p = _make_pedido_mock(ruta_id=1)
    assert cadete_efectivo_id(p, None) is None


def test_api_incluye_cadete_efectivo_en_pedido(client):
    """El endpoint /reparto/api debe incluir cadete_efectivo_id con herencia."""
    _set_farmacia()
    reparto.seed_rutas_si_vacio()
    cid = client.post('/cadetes', json={'nombre': 'Juan'}).get_json()['id']
    rutas = client.get('/rutas/api').get_json()['rutas']
    rid = rutas[0]['id']
    # asignar cadete a la ruta
    client.post('/rutas', json={'id': rid, 'cadete_id': cid})
    # crear pedido que caiga en esa ruta
    conv = store.get_conversacion('telegram', 'REP_EFF', nombre='C')
    dom = store.guardar_domicilio(conv['id'], etiqueta='Casa', lat=-32.90, lng=-60.65, origen='pin')
    client.post('/reparto/pedido', json={'cliente_nombre': 'Ana', 'domicilio_id': dom['id']})
    data = client.get('/reparto/api').get_json()
    p = data['pedidos'][0]
    assert 'cadete_efectivo_id' in p
    assert p['cadete_efectivo_id'] == cid, f'esperado {cid} got {p["cadete_efectivo_id"]}'
