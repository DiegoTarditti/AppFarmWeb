"""Rename del módulo Contabilidad → Cuentas corrientes.

Las URLs pasaron de /contabilidad* a /cuentas-corrientes*; los endpoints (nombres
de función) NO cambiaron, así que los url_for de los templates siguen resolviendo.
El extracto se movió de /cuentas-corrientes a /cuentas-corrientes/extracto para
dejarle la raíz al landing del módulo.

Estos tests fijan las dos trampas: que la raíz no colisione y que los bookmarks
viejos (/contabilidad* y /cuentas-corrientes?proveedor=N) sigan cayendo bien.
"""


def test_urls_nuevas(flask_app):
    with flask_app.test_request_context():
        from flask import url_for
        assert url_for('contabilidad_index') == '/cuentas-corrientes'
        assert url_for('cuentas_corrientes') == '/cuentas-corrientes/extracto'
        assert url_for('contabilidad_proveedores') == '/cuentas-corrientes/proveedores'
        assert url_for('contabilidad_pagos') == '/cuentas-corrientes/pagos'
        assert url_for('comprobantes_importar') == '/comprobantes/importar'


def test_landing_responde(client):
    r = client.get('/cuentas-corrientes')
    assert r.status_code == 200
    assert 'Cuentas corrientes' in r.get_data(as_text=True)


def test_extracto_responde_en_su_nueva_url(client):
    r = client.get('/cuentas-corrientes/extracto')
    assert r.status_code == 200


def test_landing_con_proveedor_redirige_al_extracto(client):
    """Favorito viejo: /cuentas-corrientes?proveedor=N era el extracto."""
    r = client.get('/cuentas-corrientes?proveedor=7', follow_redirects=False)
    assert r.status_code == 302
    assert '/cuentas-corrientes/extracto' in r.headers['Location']
    assert 'proveedor=7' in r.headers['Location']


def test_landing_sin_proveedor_no_redirige(client):
    r = client.get('/cuentas-corrientes', follow_redirects=False)
    assert r.status_code == 200


def test_bookmarks_viejos_contabilidad_redirigen_301(client):
    casos = {
        '/contabilidad': '/cuentas-corrientes',
        '/contabilidad/proveedores': '/cuentas-corrientes/proveedores',
        '/contabilidad/pagos': '/cuentas-corrientes/pagos',
        '/contabilidad/formas-pago': '/cuentas-corrientes/formas-pago',
    }
    for viejo, nuevo in casos.items():
        r = client.get(viejo, follow_redirects=False)
        assert r.status_code == 301, f'{viejo} no redirige 301'
        assert r.headers['Location'].endswith(nuevo), \
            f'{viejo} → {r.headers["Location"]}, esperaba {nuevo}'
