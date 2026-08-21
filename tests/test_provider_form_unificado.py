"""Formulario unificado de proveedor (operativo + contable).

Un solo endpoint /provider/save y un partial compartido (_provider_form.html)
que usan /providers y /contabilidad/proveedores. Estos tests fijan:
  - alta y edición completas desde CUALQUIERA de las dos pantallas
  - que los campos que una pantalla no muestra NO se borren al guardar (los
    marcadores has_*)
  - el checkbox grabar_productos (bug del hidden-primero que arrastraba el code viejo)
  - que las dos pantallas rinden sin romper y postean al mismo endpoint
"""
import database

# providers + contabilidad se registran en el app de conftest.


def _crear_provider(**kw):
    with database.get_db() as s:
        p = database.Provider(razon_social=kw.pop('razon_social', 'PROV TEST'),
                              tipo=kw.pop('tipo', 'drogueria'), activo=True, **kw)
        s.add(p)
        s.commit()
        return p.id


def _get(pid):
    with database.get_db() as s:
        return s.get(database.Provider, pid)


# ── Las pantallas rinden ─────────────────────────────────────────────────────

def test_ambas_pantallas_rinden_y_apuntan_al_mismo_endpoint(client):
    _crear_provider(razon_social='DROGUERIA UNO', cuit='30-11111111-1')
    r1 = client.get('/providers')
    r2 = client.get('/contabilidad/proveedores')
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert '/provider/save' in r1.get_data(as_text=True)
    assert '/provider/save' in r2.get_data(as_text=True)


# ── Alta ─────────────────────────────────────────────────────────────────────

def test_alta_desde_providers_guarda_operativo_y_contable(client):
    r = client.post('/provider/save', data={
        'return_to': 'providers', 'id': '',
        'has_core': '1', 'has_grabar_productos': '1', 'has_drogueria': '1',
        'razon_social': 'DROGUERIA NUEVA', 'cuit': '30-22222222-2',
        'tipo': 'drogueria', 'condicion_iva': 'Responsable Inscripto',
        'domicilio': 'Calle 1', 'parser_file': 'nueva', 'match_strategy': 'descripcion',
        'grabar_productos': ['0', '1'], 'codcli': '2440',
    }, follow_redirects=False)
    assert r.status_code == 302
    with database.get_db() as s:
        p = s.query(database.Provider).filter_by(razon_social='DROGUERIA NUEVA').first()
    assert p is not None
    # Contable Y operativo en el mismo alta:
    assert p.condicion_iva == 'Responsable Inscripto'
    assert p.parser_file == 'nueva'
    assert p.match_strategy == 'descripcion'
    assert p.codcli == '2440'
    assert p.grabar_productos == 1


def test_alta_desde_contabilidad_vuelve_a_contabilidad(client):
    r = client.post('/provider/save', data={
        'return_to': 'contabilidad', 'id': '',
        'has_core': '1',
        'razon_social': 'ESTUDIO CONTABLE', 'tipo': 'proveedor',
        'condicion_iva': 'Monotributo',
    })
    assert r.status_code == 302
    assert '/contabilidad/proveedores' in r.headers['Location']
    with database.get_db() as s:
        p = s.query(database.Provider).filter_by(razon_social='ESTUDIO CONTABLE').first()
    assert p is not None and p.condicion_iva == 'Monotributo'
    # tipo 'proveedor' se respeta (no lo pisa a 'drogueria').
    assert p.tipo == 'proveedor'


def test_alta_no_duplica_por_razon_normalizada(client):
    _crear_provider(razon_social='DROGUERIA REPETIDA')
    r = client.post('/provider/save', data={
        'return_to': 'providers', 'id': '', 'has_core': '1',
        'razon_social': '  droguería  repetida  ', 'tipo': 'drogueria',
    })
    assert r.status_code == 302
    with database.get_db() as s:
        n = s.query(database.Provider).filter(
            database.Provider.razon_social.ilike('%REPETIDA%')).count()
    assert n == 1, 'creó un duplicado'


# ── Edición: no pisar lo que la pantalla no mostró ───────────────────────────

def test_editar_sin_marcador_drogueria_no_borra_esos_campos(client):
    """Simula guardar desde una pantalla que NO muestra la sección droguería:
    codcli/sufijo tienen que sobrevivir."""
    pid = _crear_provider(razon_social='CON CODCLI', codcli='9999', sufijo='KEL')
    r = client.post('/provider/save', data={
        'return_to': 'contabilidad', 'id': str(pid),
        'has_core': '1',  # SIN has_drogueria
        'razon_social': 'CON CODCLI', 'tipo': 'drogueria',
        'condicion_iva': 'Exento',
    })
    assert r.status_code == 302
    p = _get(pid)
    assert p.condicion_iva == 'Exento'      # lo que sí vino se aplicó
    assert p.codcli == '9999'               # lo que no vino, intacto
    assert p.sufijo == 'KEL'


def test_editar_desde_contabilidad_no_borra_parser(client):
    """El caso real: editar datos contables no debe borrar el parser operativo."""
    pid = _crear_provider(razon_social='CON PARSER', parser_file='pharmos',
                          match_strategy='descripcion')
    client.post('/provider/save', data={
        'return_to': 'contabilidad', 'id': str(pid), 'has_core': '1',
        'razon_social': 'CON PARSER', 'tipo': 'drogueria',
        'parser_file': 'pharmos', 'match_strategy': 'descripcion',
        'domicilio': 'Nueva dir',
    })
    p = _get(pid)
    assert p.parser_file == 'pharmos'
    assert p.match_strategy == 'descripcion'
    assert p.domicilio == 'Nueva dir'


# ── Checkbox grabar_productos (el bug del hidden-primero) ─────────────────────

def test_grabar_productos_tildado_queda_en_1(client):
    pid = _crear_provider(razon_social='GP ON', grabar_productos=0)
    client.post('/provider/save', data={
        'return_to': 'providers', 'id': str(pid),
        'has_core': '1', 'has_grabar_productos': '1',
        'razon_social': 'GP ON', 'tipo': 'drogueria',
        # tildado: viajan el hidden '0' y el checkbox '1'
        'grabar_productos': ['0', '1'],
    })
    assert _get(pid).grabar_productos == 1


def test_grabar_productos_destildado_queda_en_0(client):
    pid = _crear_provider(razon_social='GP OFF', grabar_productos=1)
    client.post('/provider/save', data={
        'return_to': 'providers', 'id': str(pid),
        'has_core': '1', 'has_grabar_productos': '1',
        'razon_social': 'GP OFF', 'tipo': 'drogueria',
        'grabar_productos': '0',   # solo el hidden: destildado
    })
    assert _get(pid).grabar_productos == 0


def test_editar_sin_marcador_grabar_no_lo_toca(client):
    pid = _crear_provider(razon_social='GP KEEP', grabar_productos=0)
    client.post('/provider/save', data={
        'return_to': 'contabilidad', 'id': str(pid), 'has_core': '1',
        'razon_social': 'GP KEEP', 'tipo': 'drogueria',
    })
    assert _get(pid).grabar_productos == 0


# ── Rutas legacy (delegan en el mismo núcleo) ────────────────────────────────

def test_ruta_legacy_create_preserva_cuit(client):
    """El form de /providers/activos postea a /provider/create con has_core.
    Sin el marcador el CUIT se perdía en el alta."""
    r = client.post('/provider/create', data={
        'has_core': '1', 'razon_social': 'DROG LEGACY', 'cuit': '30-33333333-3',
        'tipo': 'drogueria',
    })
    assert r.status_code == 302
    with database.get_db() as s:
        p = s.query(database.Provider).filter_by(razon_social='DROG LEGACY').first()
    assert p is not None and p.cuit == '30-33333333-3'


def test_ruta_legacy_edit_por_url(client):
    """/provider/<id>/edit sigue andando: el id viene por la URL, no por el form."""
    pid = _crear_provider(razon_social='LEGACY EDIT', parser_file='viejo')
    r = client.post(f'/provider/{pid}/edit', data={
        'has_core': '1', 'razon_social': 'LEGACY EDIT', 'tipo': 'drogueria',
        'parser_file': 'nuevo', 'match_strategy': 'barcode',
    })
    assert r.status_code == 302
    assert _get(pid).parser_file == 'nuevo'
