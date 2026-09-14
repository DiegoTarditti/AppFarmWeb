"""La pantalla de rentabilidad: que renderice y que los avisos se vean."""
from datetime import date

import database
from database import Invoice, InvoiceItem
from flask import Flask
from flask import url_for as _real_url_for
from flask_login import LoginManager, UserMixin

_ID = [0]


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

    import routes.rentabilidad as r
    r.init_app(app)
    return app


def _compra(s, ean, fecha, precio, cantidad=1):
    inv = Invoice(numero_factura=f'F{ean}{fecha}', fecha=fecha,
                  proveedor_razon='KELLERHOFF', proveedor_cuit='30539756490',
                  tipo_comprobante='FAC', total=precio * cantidad)
    s.add(inv)
    s.flush()
    s.add(InvoiceItem(factura_id=inv.id, codigo_barra=ean, descripcion='X',
                      cantidad=cantidad, precio_unitario=precio,
                      importe=precio * cantidad))


def _catalogo(s, pid, ean, descripcion, lab_id=3, lab='NOVO NORDISK'):
    if not s.get(database.ObsLaboratorio, lab_id):
        s.add(database.ObsLaboratorio(observer_id=lab_id, descripcion=lab))
    s.add(database.ObsProducto(observer_id=pid, descripcion=descripcion,
                               laboratorio_observer=lab_id))
    s.flush()
    _ID[0] += 1
    s.add(database.ObsCodigoBarras(id_codigo_barras=_ID[0], producto_observer=pid,
                                   codigo_barras=ean, orden=1))


def _venta(s, pid, fecha, cantidad, importe, neto=None):
    _ID[0] += 1
    s.add(database.ObsVentaDetalle(
        id_producto_vendido=_ID[0], producto_observer=pid, cantidad=cantidad,
        importe=importe, importe_neto=neto, es_venta_particular=True,
        fecha_estadistica=fecha, tipo_operacion='V', id_farmacia=1))


def _seed_ozempic(s):
    _catalogo(s, 70, '7798058931843', 'OZEMPIC 1 mg/ds 3 ml')
    _compra(s, '7798058931843', date.today(), 278269, cantidad=16)
    _venta(s, 70, date.today(), 37, 15300230, neto=15300230)


def test_la_pantalla_renderiza_con_datos():
    s = database.SessionLocal()
    _seed_ozempic(s)
    s.commit()
    s.close()

    r = _app().test_client().get('/rentabilidad')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'OZEMPIC 1 mg/ds 3 ml' in html
    assert 'NOVO NORDISK' in html
    assert 'Costo reposición' in html


def test_muestra_el_aviso_de_unidad_dudosa():
    """PROFIL PRIME ZERO: caja a $41.952, se vende suelto a $4.500 (−832%)."""
    s = database.SessionLocal()
    _catalogo(s, 80, '7791519702754', 'PROFIL PRIME ZERO 12 X 3', lab_id=9, lab='PROFIL')
    _compra(s, '7791519702754', date.today(), 41952, cantidad=1)
    _venta(s, 80, date.today(), 10, 45000, neto=45000)
    s.commit()
    s.close()

    html = _app().test_client().get('/rentabilidad').get_data(as_text=True)
    assert 'unidad dudosa' in html
    # Y NO muestra el margen absurdo como si fuera una pérdida real.
    assert '-832' not in html


def test_avisa_cuando_la_facturacion_es_bruta():
    """Sin `importe_neto` sincronizado el margen queda inflado 2-6%: se dice."""
    s = database.SessionLocal()
    _catalogo(s, 70, '7798058931843', 'OZEMPIC 1 mg/ds 3 ml')
    _compra(s, '7798058931843', date.today(), 278269, cantidad=16)
    _venta(s, 70, date.today(), 37, 15300230)      # sin neto
    s.commit()
    s.close()

    html = _app().test_client().get('/rentabilidad').get_data(as_text=True)
    assert 'El margen está inflado' in html


def test_el_buscador_filtra():
    s = database.SessionLocal()
    _seed_ozempic(s)
    _catalogo(s, 90, '999', 'OTRA COSA', lab_id=4, lab='OTRO LAB')
    _compra(s, '999', date.today(), 1000, cantidad=5)
    _venta(s, 90, date.today(), 5, 8000, neto=8000)
    s.commit()
    s.close()

    c = _app().test_client()
    html = c.get('/rentabilidad?q=ozempic').get_data(as_text=True)
    assert 'OZEMPIC' in html and 'OTRA COSA' not in html
    # También por laboratorio y por EAN.
    assert 'OTRA COSA' in c.get('/rentabilidad?q=otro lab').get_data(as_text=True)
    assert 'OZEMPIC' in c.get('/rentabilidad?q=7798058931843').get_data(as_text=True)


def test_sin_datos_no_revienta():
    html = _app().test_client().get('/rentabilidad').get_data(as_text=True)
    assert 'No hay productos con compras registradas' in html


def test_avisa_de_la_pantalla_hermana_y_por_que_difieren():
    """Existe /obras-sociales/productos-rentabilidad haciendo un calculo
    parecido: si alguien ve numeros distintos, tiene que entender por que."""
    html = _app().test_client().get('/rentabilidad').get_data(as_text=True)
    assert 'Top productos por margen' in html
    assert 'inflaci' in html.lower()
