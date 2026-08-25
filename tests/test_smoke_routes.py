"""Smoke tests de rutas GET principales — verifican que no rompan al renderizar.

Filosofía: cada ruta GET pública del sistema debe responder sin 500.
Aceptamos 200 (OK), 302 (redirect), 404 (record no encontrado), 401 (no auth).
Lo que NO aceptamos es 500 (template roto, variable indefinida, query mal armada).

Esto NO es un test funcional — solo verifica que el código carga + el template
renderiza sin crashear. Cuando un PR rompe un template (`{% endif %}` huérfano,
variable indefinida, etc.), este test falla al instante en CI.

Si agregás una ruta GET nueva a `routes/`, sumala a `RUTAS_GET` abajo.
"""
import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin


# ── Fixture: app con TODOS los blueprints registrados ─────────────────────────

@pytest.fixture(scope='module')
def smoke_app(init_test_db, tmp_path_factory):
    """Versión del app de tests que registra todos los routers.
    Distinto a `flask_app` (conftest) que solo registra 4 módulos.
    """
    upload_dir = str(tmp_path_factory.mktemp('uploads_smoke'))

    app = Flask(__name__, template_folder='../templates')
    app.secret_key = 'smoke-test'
    app.config['TESTING'] = True
    app.config['UPLOAD_FOLDER'] = upload_dir
    app.config['SERVER_NAME'] = 'localhost.localdomain'

    # Globals de templates que necesita base.html.
    class _Entorno:
        codigo = 'test'
        label = 'TEST'
        color = '#888'
        descripcion = 'Test environment'
    app.jinja_env.globals['entorno'] = _Entorno()
    app.jinja_env.globals['tiene_permiso'] = lambda *a, **k: True

    # url_for tolerante: si un endpoint no existe, devuelve '#' en vez de excepción.
    from flask import url_for as _real_url_for

    def _tolerant_url_for(endpoint, **values):
        try:
            return _real_url_for(endpoint, **values)
        except Exception:
            return '#'
    app.jinja_env.globals['url_for'] = _tolerant_url_for

    # Login: cualquier request lleva a un user dummy con rol 'admin'.
    lm = LoginManager(app)

    class _DummyUser(UserMixin):
        id = '1'
        username = 'smoke'
        rol = 'admin'
        nombre_completo = 'Smoke Tester'
        is_authenticated = True

    @lm.user_loader
    def _loader(uid):
        return _DummyUser()

    @lm.request_loader
    def _req_loader(_req):
        return _DummyUser()

    # Inyectar entorno (igual que app.py en producción).
    @app.context_processor
    def _inject_entorno():
        return {'entorno': _Entorno()}

    # Filtros Jinja que app.py registra y los templates usan.
    @app.template_filter('abs')
    def _abs_f(v):
        return abs(v) if v is not None else 0

    @app.template_filter('arg_currency')
    def _arg_currency(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return '—'
        int_p, dec_p = f'{v:.2f}'.split('.')
        out = ''
        for i, ch in enumerate(reversed(int_p)):
            if i and i % 3 == 0:
                out = '.' + out
            out = ch + out
        return f'{out},{dec_p}'

    # Registrar TODOS los routers.
    from routes import register_routes
    register_routes(app)

    return app


@pytest.fixture
def smoke_client(smoke_app):
    with smoke_app.test_client() as c:
        yield c


# ── Lista de rutas GET a smoke-testear ────────────────────────────────────────
# Cada entrada: (path, descripcion). El descrip se usa como ID del test.
# Con <int:id> usamos 1 (puede dar 404, lo aceptamos). El objetivo es NO 500.

RUTAS_GET = [
    # Core
    ('/', 'home'),
    ('/dashboard', 'dashboard'),
    ('/health', 'health'),
    ('/health_web', 'health_web'),
    ('/settings', 'settings'),
    ('/ingresos', 'ingresos'),
    ('/kellerhoff/sync', 'kellerhoff_sync'),
    ('/kellerhoff/resumenes', 'kellerhoff_resumenes'),
    ('/kellerhoff/cuenta-corriente', 'kellerhoff_cuenta_corriente'),

    # Catálogos
    ('/providers', 'providers_list'),
    ('/laboratorios', 'laboratorios'),
    ('/clientes', 'clientes'),
    ('/clientes/stats', 'clientes_stats'),
    ('/productos', 'productos'),
    ('/vademecum', 'vademecum') if False else ('/productos', 'productos_alt'),

    # Compras / pedidos
    ('/orders', 'orders_list'),
    ('/purchase', 'purchase_index'),
    ('/purchase/processed', 'purchase_processed'),
    ('/procesos', 'procesos_list'),
    ('/cuentas-corrientes', 'cuentas_corrientes'),

    # Reclamos / facturas
    ('/claims', 'claims_list'),
    ('/batch/new', 'batch_new'),

    # Documentos pendientes / converter
    ('/docs-pendientes', 'docs_pendientes'),
    ('/converter', 'converter_index'),

    # Imports
    ('/ofertas/import', 'ofertas_import'),
    ('/modulos/import', 'modulos_import'),
    ('/modulo-packs', 'modulo_packs'),

    # Obras sociales
    ('/obras-sociales', 'os_dashboard'),
    ('/obras-sociales/dispensas', 'os_dispensas'),
    ('/obras-sociales/catalogo', 'os_catalogo'),

    # Informes y BI
    ('/informes', 'informes_index'),
    ('/informes/labs-por-droga', 'informes_labs'),
    ('/informes/drogas-sin-alternativa', 'informes_sin_alt'),
    ('/informes/presentaciones-por-droga', 'informes_presentaciones'),
    ('/informes/bajo-minimo', 'informes_bajo_minimo'),
    ('/informes/stock-parado', 'informe_stock_parado'),
    ('/bi', 'bi_tablero'),

    # Observer
    ('/obs/productos', 'obs_productos'),
    ('/observer/status', 'observer_status'),
    ('/observer/schema', 'observer_schema'),
    ('/observer/laboratorios', 'observer_laboratorios'),
    ('/productos/sin-vincular', 'productos_sin_vincular'),
    ('/estadisticas/drogas', 'estadisticas_drogas'),
    ('/admin/observer-sync', 'observer_sync_admin'),

    # Admin
    ('/admin', 'admin_index'),
    ('/admin/dashboard', 'admin_dashboard'),
    ('/admin/console', 'admin_console'),
    ('/admin/cron-log', 'admin_cron_log'),
]

# Eliminar duplicados conservando orden.
_seen = set()
RUTAS_GET = [(p, d) for p, d in RUTAS_GET if p not in _seen and not _seen.add(p)]

ACEPTABLES = {200, 301, 302, 303, 307, 308, 401, 403, 404}


@pytest.mark.parametrize('path,descripcion', RUTAS_GET, ids=[d for _, d in RUTAS_GET])
def test_ruta_get_no_revienta(smoke_client, path, descripcion):
    """La ruta debe responder sin 500. Aceptamos OK / redirect / not found / no auth."""
    resp = smoke_client.get(path, follow_redirects=False)
    assert resp.status_code != 500, (
        f'{path} ({descripcion}) devolvió 500.\n'
        f'Body: {resp.data[:500]!r}'
    )
    assert resp.status_code in ACEPTABLES, (
        f'{path} ({descripcion}) devolvió código inesperado: {resp.status_code}\n'
        f'Body: {resp.data[:500]!r}'
    )


# ── Rutas con parámetros — testeamos con id=1 (esperamos 404 o redirect) ──────

RUTAS_CON_ID = [
    ('/claim/1', 'claim_detail'),
    ('/clientes/1', 'cliente_detail'),
    ('/results/1', 'results_invoice'),
    ('/invoice/1/items', 'invoice_items'),
    ('/invoice/1/compare', 'invoice_compare'),
    ('/provider/1/invoices', 'provider_invoices'),
    ('/provider/1/mappings', 'provider_mappings'),
    ('/proceso/1', 'proceso_detail'),
    ('/order/1', 'order_detail'),
    ('/purchase/results/abc-123', 'purchase_results'),
    ('/laboratorio/1/ofertas-minimo', 'lab_ofertas_minimo'),
    ('/obras-sociales/catalogo/1', 'os_catalogo_detail'),
    ('/observer/factura/1/recepciones', 'observer_factura'),
    ('/kellerhoff/resumen/1', 'kellerhoff_resumen_detalle'),
    ('/batch/1/results', 'batch_results'),
    ('/docs-pendientes/1/procesar', 'docs_pendientes_procesar'),
]


@pytest.mark.parametrize('path,descripcion', RUTAS_CON_ID, ids=[d for _, d in RUTAS_CON_ID])
def test_ruta_con_id_no_revienta(smoke_client, path, descripcion):
    """Con id=1 esperamos 404, redirect, o el detalle si existe.
    Lo importante: que no sea 500 (template roto, query mal armada).
    """
    resp = smoke_client.get(path, follow_redirects=False)
    assert resp.status_code != 500, (
        f'{path} ({descripcion}) devolvió 500.\n'
        f'Body: {resp.data[:500]!r}'
    )


# ── Resúmenes de cuenta: render CON datos ────────────────────────────────────
# Las rutas de arriba se piden con la DB vacía, así que nunca dibujan un
# renglón. Estos dos ejercitan los badges de control, que es donde vive la
# lógica nueva.

def _sembrar_resumen(cerrado):
    """Un resumen con 2 renglones; si `cerrado`, los dos ligados."""
    from datetime import date
    from unittest.mock import patch

    import database
    import services.kellerhoff_resumen as mod
    from tests.test_kellerhoff_resumen import (
        RESUMEN_TXT, _ajuste_nc, _factura, _proveedor,
    )

    with database.get_db() as session:
        prov = _proveedor(session)
        if cerrado:
            _factura(session, '00046-00279207', 915046.04)
            _factura(session, '00046-00279939', 151817.02)
            _ajuste_nc(session, '0046A00063591', 69124.56)
        session.commit()
        with patch.object(mod, 'parse_resumen_pdf',
                          lambda _p: mod.parse_resumen_texto(RESUMEN_TXT)):
            res = mod.importar_resumen(session, 'x.pdf', prov.id)
        # El módulo lee KELLERHOFF_PROVIDER_ID del entorno; en el test el
        # proveedor sembrado puede tener otro id.
        import routes.kellerhoff_sync as ks
        ks.KELLERHOFF_PROVIDER_ID = prov.id
        return res['resumen_id'], date(2026, 8, 28)


def test_listado_de_resumenes_dibuja_el_estado_de_la_semana(smoke_client):
    _sembrar_resumen(cerrado=False)
    resp = smoke_client.get('/kellerhoff/resumenes')
    assert resp.status_code == 200
    html = resp.data.decode('utf-8')
    assert 'S34-2026' in html
    assert 'sin tildar' in html, 'no se dibujó el pendiente de la semana'


def test_detalle_marca_tildado_el_recupero_que_no_es_factura(smoke_client):
    """La NC financiera no crea Invoice: se liga contra el ajuste de cta cte.
    Si no, la semana no cerraría nunca."""
    resumen_id, _ = _sembrar_resumen(cerrado=True)
    resp = smoke_client.get(f'/kellerhoff/resumen/{resumen_id}')
    assert resp.status_code == 200
    html = resp.data.decode('utf-8')
    assert 'recupero' in html, 'la NC financiera no se reconoció'
    assert 'cerrado' in html, 'con todo ligado la semana tiene que cerrar'
    # El badge "no está" tiene un title propio; buscar el texto suelto matchea
    # un comentario del CSS de base.html.
    assert 'Kellerhoff te lo cobra' not in html, 'quedó un renglón sin ligar'


def test_los_vencimientos_salen_en_formato_argentino(smoke_client):
    """Se guardan en ISO; la pantalla usa dd/mm/aaaa en todos lados y quedaban
    '2026-09-22' al lado de fechas '22/08/2026'."""
    resumen_id, _ = _sembrar_resumen(cerrado=True)
    html = smoke_client.get(f'/kellerhoff/resumen/{resumen_id}').data.decode('utf-8')
    assert '22/09/2026' in html
    assert '2026-09-22' not in html


# ── Columna de detalle en las tres listas ────────────────────────────────────

def _sembrar_facturas():
    """Un proveedor con tres facturas: completa, a medias y sin detalle."""
    from datetime import date

    import database

    with database.get_db() as session:
        prov = database.Provider(razon_social='Kellerhoff', cuit='30539756490',
                                 tipo='drogueria', activo=True)
        session.add(prov)
        session.flush()
        hechas = []
        for numero, declarado, n_items in [('00046-00279207', 2, 2),
                                           ('00046-00281042', 9, 3),
                                           ('00046-00255798', 0, 0)]:
            inv = database.Invoice(numero_factura=numero, fecha=date(2026, 8, 22),
                                   tipo_comprobante='FAC', proveedor_razon='Kellerhoff',
                                   proveedor_cuit='30539756490', total=1000,
                                   total_articulos=declarado)
            session.add(inv)
            session.flush()
            for i in range(n_items):
                session.add(database.InvoiceItem(factura_id=inv.id, codigo_barra=f'{i:013d}',
                                                 cantidad=1, descripcion=f'IT{i}'))
            hechas.append(inv.id)
        session.commit()
        import routes.kellerhoff_sync as ks
        ks.KELLERHOFF_PROVIDER_ID = prov.id
        return prov.id, hechas


def test_facturas_del_proveedor_muestran_el_detalle(smoke_client):
    prov_id, _ = _sembrar_facturas()
    html = smoke_client.get(f'/provider/{prov_id}/invoices').data.decode('utf-8')
    assert 'Detalle' in html
    assert 'sin detalle' in html, 'la factura sin renglones no se marcó'
    assert '3 / 9' in html, 'no se marcó el parseo incompleto'


def test_portal_kellerhoff_muestra_el_detalle(smoke_client):
    _sembrar_facturas()
    html = smoke_client.get('/kellerhoff/sync').data.decode('utf-8')
    assert 'sin detalle' in html
    assert '3 / 9' in html


def test_cuenta_corriente_muestra_el_detalle(smoke_client):
    prov_id, _ = _sembrar_facturas()
    html = smoke_client.get(
        f'/cuentas-corrientes/extracto?proveedor={prov_id}').data.decode('utf-8')
    assert 'sin detalle' in html
    assert '3 / 9' in html


def test_la_cuenta_corriente_del_modulo_dice_en_que_resumen_entro(smoke_client):
    """La columna existía sólo en /cuentas-corrientes/extracto. La pantalla del
    módulo usa el mismo motor (`movimientos_proveedor`), así que el dato ya
    viajaba: sólo faltaba dibujarlo."""
    from datetime import date
    from unittest.mock import patch

    import database
    import services.kellerhoff_resumen as mod
    from tests.test_kellerhoff_resumen import RESUMEN_TXT, _factura, _proveedor

    with database.get_db() as session:
        prov = _proveedor(session)
        _factura(session, '00046-00279207', 915046.04)
        session.commit()
        with patch.object(mod, 'parse_resumen_pdf',
                          lambda _p: mod.parse_resumen_texto(RESUMEN_TXT)):
            mod.importar_resumen(session, 'x.pdf', prov.id)
        import routes.kellerhoff_sync as ks
        ks.KELLERHOFF_PROVIDER_ID = prov.id

    html = smoke_client.get('/kellerhoff/cuenta-corriente').data.decode('utf-8')
    assert 'Resumen' in html, 'falta la columna'
    assert 'S34-2026' in html, 'no se dibujó en qué resumen entró la factura'
