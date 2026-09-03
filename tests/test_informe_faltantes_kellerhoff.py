"""Informe de faltantes de ingreso: el importe y, sobre todo, la separación
entre lo reclamable y lo que el control no puede ver.

Medido en producción el 2026-09-03 sobre 229 filas de diferencia: 29
reclamables ($1.944.798) contra 200 huecos de catálogo ($13.119.160). Meter los
dos totales en la misma bolsa convertiría $13 M de puntos ciegos en un reclamo
inventado.
"""
from datetime import date

import database
from database import Invoice, InvoiceItem, StockDifference
from flask import Flask
from flask import url_for as _real_url_for
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

    import routes.informes as inf
    inf.init_app(app)
    return app


def _factura(s, numero, cruzada=True):
    inv = Invoice(numero_factura=numero, fecha=date(2026, 9, 1),
                  proveedor_razon='DROGUERIA KELLERHOFF S.A.', proveedor_cuit='30539756490',
                  tipo_comprobante='FAC', total=0)
    if cruzada:
        inv.erp_carga_id = 999
    s.add(inv)
    s.flush()
    return inv


def _item(s, inv, ean, desc, cant, importe):
    s.add(InvoiceItem(factura_id=inv.id, codigo_barra=ean, descripcion=desc,
                      cantidad=cant, precio_unitario=importe / cant, importe=importe))


def _dif(s, inv, ean, desc, cf, ce, obs):
    s.add(StockDifference(factura_id=inv.id, codigo_barra=ean, descripcion=desc,
                          cantidad_factura=cf, cantidad_erp=ce, diferencia=cf - ce,
                          observaciones=obs))


def test_no_encontrado_que_cruza_en_otra_factura_es_reclamable():
    """Caso OBETIDE real: 'no encontrado' en una factura, pero el mismo EAN
    cruza bien en otra → el catálogo lo resuelve, así que su ausencia acá es un
    faltante y no una limitación nuestra."""
    s = database.SessionLocal()
    a = _factura(s, 'F-A')
    b = _factura(s, 'F-B')
    _item(s, a, '7796285300579', 'OBETIDE 1,7 MG', 2, 301338.70)
    _dif(s, a, '7796285300579', 'OBETIDE 1,7 MG', 2, 0, 'Artículo no encontrado en ERP')
    _item(s, b, '7796285300579', 'OBETIDE 1,7 MG', 16, 2322090.07)   # acá cruzó bien
    s.commit()

    html = _app().test_client().get('/informes/faltantes-kellerhoff').get_data(as_text=True)
    assert 'reclamable' in html
    assert '301.338,70' in html
    s.rollback()
    s.close()


def test_no_encontrado_que_no_cruza_en_ningun_lado_no_suma_al_reclamo():
    """Los 200 huecos de catálogo: no se puede saber si entró. Contarlos como
    faltante sería inventar un reclamo."""
    s = database.SessionLocal()
    a = _factura(s, 'F-C')
    _item(s, a, '999999999', 'PRODUCTO RARO', 1, 50000.0)
    _dif(s, a, '999999999', 'PRODUCTO RARO', 1, 0, 'Artículo no encontrado en ERP')
    s.commit()

    c = _app().test_client()
    html = c.get('/informes/faltantes-kellerhoff?solo=todo').get_data(as_text=True)
    assert 'sin poder opinar' in html
    # Con el filtro por defecto (reclamables) no aparece.
    assert '999999999' not in c.get('/informes/faltantes-kellerhoff').get_data(as_text=True)
    s.rollback()
    s.close()


def test_no_coincide_con_erp_es_reclamable_sin_necesitar_otra_factura():
    """Si el producto SE identificó y la cantidad no coincide, no hace falta
    evidencia extra: el faltante es directo."""
    s = database.SessionLocal()
    a = _factura(s, 'F-D')
    _item(s, a, '111222333', 'ALGO', 6, 90000.0)
    _dif(s, a, '111222333', 'ALGO', 6, 5, 'No coincide con ERP')
    s.commit()

    html = _app().test_client().get('/informes/faltantes-kellerhoff').get_data(as_text=True)
    assert '90.000,00' in html
    assert 'reclamable' in html
    s.rollback()
    s.close()


def test_factura_sin_cruzar_no_entra_ni_como_evidencia():
    """Sin cruce no hay diferencias, y esa ausencia NO significa que todo entró
    — es el mismo error del ✓ heredado que motivó este informe."""
    s = database.SessionLocal()
    a = _factura(s, 'F-E', cruzada=False)
    _item(s, a, '444555666', 'NO CRUZADA', 3, 12345.0)
    s.commit()

    html = _app().test_client().get('/informes/faltantes-kellerhoff?solo=todo').get_data(as_text=True)
    assert '444555666' not in html
    s.rollback()
    s.close()
