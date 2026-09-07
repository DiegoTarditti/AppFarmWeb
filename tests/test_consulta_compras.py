"""Consulta de compras por artículo: búsqueda por palabras + rango + dto."""
from datetime import date

import database
from database import Invoice, InvoiceItem
from flask import Flask, url_for as _real_url_for
from flask_login import LoginManager, UserMixin


def _app():
    app = Flask(__name__, template_folder='../templates')
    app.secret_key = 'test'
    app.config['TESTING'] = True

    class _U(UserMixin):
        id = '1'
        nombre_completo = 'T'
        username = 't'
        rol = 'dev'
        is_authenticated = True

    lm = LoginManager(app)

    @lm.request_loader
    def _load(_req):
        return _U()

    class _E:
        codigo = 'test'
        label = 'T'
        color = '#888'

    app.jinja_env.globals.update(entorno=_E(), tiene_permiso=lambda *a, **k: True,
                                 current_user=_U())

    def _tol(ep, **v):
        try:
            return _real_url_for(ep, **v)
        except Exception:
            return '#'
    app.jinja_env.globals['url_for'] = _tol

    import routes.consulta_compras as c
    c.init_app(app)
    return app


def _seed():
    s = database.SessionLocal()
    inv1 = Invoice(numero_factura='0001-1', fecha=date(2026, 8, 1), proveedor_razon='KELLERHOFF',
                   proveedor_cuit='30539756490', tipo_comprobante='FAC', total=1000)
    inv2 = Invoice(numero_factura='0001-2', fecha=date(2026, 6, 1), proveedor_razon='OTRA',
                   proveedor_cuit='30111111112', tipo_comprobante='FAC', total=500)
    s.add_all([inv1, inv2])
    s.flush()
    s.add(InvoiceItem(factura_id=inv1.id, codigo_barra='779123', descripcion='OPTAMOX DUO 1G COM X 8',
                      cantidad=5, precio_unitario=100, dto=20, importe=500))
    s.add(InvoiceItem(factura_id=inv2.id, codigo_barra='779123', descripcion='OPTAMOX DUO 1G COM X 8',
                      cantidad=3, precio_unitario=110, dto=None, importe=330))
    s.add(InvoiceItem(factura_id=inv1.id, codigo_barra='000', descripcion='ALGO QUE NO ES',
                      cantidad=1, precio_unitario=1, dto=None, importe=1))
    s.commit()


def test_busca_por_palabras_y_muestra_dto():
    _seed()
    c = _app().test_client()
    r = c.get('/compras/consulta?q=optamox+duo')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'OPTAMOX DUO 1G COM X 8' in html
    assert '20%' in html                 # el descuento de la compra de agosto
    assert 'ALGO QUE NO ES' not in html   # el otro producto no matchea


def test_filtra_por_rango_de_fecha():
    _seed()
    c = _app().test_client()
    # Solo julio en adelante → queda la de agosto, no la de junio.
    r = c.get('/compras/consulta?q=optamox&desde=2026-07-01')
    html = r.get_data(as_text=True)
    assert '01/08/2026' in html
    assert '01/06/2026' not in html


def test_filtra_por_proveedor():
    _seed()
    c = _app().test_client()
    r = c.get('/compras/consulta?q=optamox&proveedor=30111111112')
    html = r.get_data(as_text=True)
    assert 'OTRA' in html
    assert '01/06/2026' in html
    assert '01/08/2026' not in html      # la de Kellerhoff queda afuera


def test_sin_termino_no_lista():
    _seed()
    c = _app().test_client()
    r = c.get('/compras/consulta')
    html = r.get_data(as_text=True)
    assert 'Escribí un producto' in html
    assert 'OPTAMOX' not in html


def test_export_xlsx():
    _seed()
    c = _app().test_client()
    r = c.get('/compras/consulta/export.xlsx?q=optamox')
    assert r.status_code == 200
    assert 'spreadsheetml' in r.headers['Content-Type']
    assert r.headers['Content-Disposition'].endswith('.xlsx"')
    # es un xlsx real (zip: empieza con 'PK') y trae el producto
    body = r.get_data()
    assert body[:2] == b'PK'
    import io
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(body)).active
    valores = [str(v) for row in ws.iter_rows(values_only=True) for v in row]
    assert any('OPTAMOX' in v for v in valores)


def test_export_sin_termino_da_400():
    c = _app().test_client()
    assert c.get('/compras/consulta/export.xlsx').status_code == 400


# ── Columna "Ingreso": es del RENGLÓN, no de la factura ───────────────────────

def _seed_ingreso(cant_erp=None, cruzada=True, ingreso_factura=True):
    """Una factura de Kellerhoff con un renglón, cruzada o no.

    cant_erp=None → el cruce no dejó diferencia para ese EAN (entró bien).
    cant_erp=0    → el cruce dice que no ingresó nada de ese artículo.
    """
    from database import ResumenProveedorItem, StockDifference
    s = database.SessionLocal()
    inv = Invoice(numero_factura='00046-00317100', fecha=date(2026, 9, 1),
                  proveedor_razon='DROGUERIA KELLERHOFF S.A.', proveedor_cuit='30539756490',
                  tipo_comprobante='FAC', total=301338)
    if cruzada:
        inv.erp_carga_id = 12345
    s.add(inv)
    s.flush()
    s.add(InvoiceItem(factura_id=inv.id, codigo_barra='7796285300579',
                      descripcion='OBETIDE 1,7 MG INY JER PRELL X 4',
                      cantidad=2, precio_unitario=150669.35, dto=None, importe=301338.70))
    # El remito TIENE recepción: es lo que hacía que el renglón cobrara ✓ por herencia.
    s.add(ResumenProveedorItem(resumen_id=1, numero_remito='0047R00333564',
                               factura_id=inv.id, ingreso_verificado=ingreso_factura))
    if cant_erp is not None:
        s.add(StockDifference(factura_id=inv.id, codigo_barra='7796285300579',
                              descripcion='OBETIDE 1,7 MG INY JER PRELL X 4',
                              cantidad_factura=2, cantidad_erp=cant_erp,
                              diferencia=2 - cant_erp,
                              observaciones='Artículo no encontrado en ERP'))
    s.commit()
    return inv


def test_ingreso_marca_cruz_aunque_la_factura_tenga_recepcion():
    """Caso real 00046-00317100 (2026-09-03): el remito tiene recepción — de un
    MODIALEX — así que la columna daba ✓ al renglón del OBETIDE, que según el
    cruce ingresó 0 de 2. Un faltante de $301.338,70 con tilde de recibido."""
    _seed_ingreso(cant_erp=0, ingreso_factura=True)
    r = _app().test_client().get('/compras/consulta?q=obetide')
    html = r.get_data(as_text=True)
    assert 'Facturado 2, ingresó 0' in html
    assert '✗' in html


def test_ingreso_marca_ok_cuando_el_renglon_cruzo_sin_diferencia():
    _seed_ingreso(cant_erp=None, ingreso_factura=True)
    r = _app().test_client().get('/compras/consulta?q=obetide')
    html = r.get_data(as_text=True)
    assert 'cruzó sin diferencia' in html


def test_ingreso_sin_cruzar_no_afirma_nada():
    """Sin cruce no hay diferencias, y "no hay diferencias" NO es un ✓: sería
    afirmar que entró porque nunca se comparó."""
    _seed_ingreso(cant_erp=None, cruzada=False, ingreso_factura=True)
    r = _app().test_client().get('/compras/consulta?q=obetide')
    html = r.get_data(as_text=True)
    assert 'todavía no se cruzó' in html
    assert 'no de este artículo' in html   # el dato de factura queda en el tooltip
