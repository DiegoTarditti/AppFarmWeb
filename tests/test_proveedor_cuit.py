"""Match de proveedor por CUIT: el mismo CUIT llega en dos formatos.

El parser de PDF de Kellerhoff emite '30-53975649-0' y su fila de `proveedores`
tiene '30539756490' (así lo mandan ARCA y el scraper del portal, que son el
origen de las 5.584 facturas cargadas). Con el match por igualdad cruda esa
factura no encontraba a su proveedor y se creaba uno duplicado.
"""
import database
from database import Invoice, Provider
from helpers import (
    _normalizar_nombre_entidad,
    buscar_proveedor_por_cuit,
    get_or_create_proveedor,
)


def _providers():
    s = database.SessionLocal()
    try:
        return s.query(Provider).all()
    finally:
        s.close()


def test_encuentra_proveedor_aunque_el_cuit_venga_con_guiones():
    """El caso real: Provider normalizado, factura con guiones."""
    s = database.SessionLocal()
    s.add(Provider(razon_social='DROGUERÍA KELLERHOFF S.A.', cuit='30539756490'))
    s.commit()
    s.close()

    s = database.SessionLocal()
    prov = get_or_create_proveedor(s, 'DROGUERÍA KELLERHOFF S.A', '30-53975649-0')
    s.commit()
    s.close()

    assert prov.cuit == '30539756490'            # devolvió el existente
    assert len(_providers()) == 1                # y no creó un duplicado


def test_encuentra_proveedor_aunque_el_cuit_este_guardado_con_guiones():
    """El inverso: Provider con guiones (como los otros 123), factura sin."""
    s = database.SessionLocal()
    s.add(Provider(razon_social='DROGUERIA DEL SUD S. A.', cuit='30-53888062-7'))
    s.commit()
    s.close()

    s = database.SessionLocal()
    prov = get_or_create_proveedor(s, 'DROGUERIA DEL SUD SA', '30538880627')
    s.commit()
    s.close()

    assert prov.cuit == '30-53888062-7'
    assert len(_providers()) == 1


def test_sigue_creando_si_el_cuit_es_de_otro_proveedor():
    """La normalización no puede hacer que dos CUITs distintos colisionen."""
    s = database.SessionLocal()
    s.add(Provider(razon_social='DROGUERÍA KELLERHOFF S.A.', cuit='30539756490'))
    s.commit()
    s.close()

    s = database.SessionLocal()
    get_or_create_proveedor(s, 'OTRA DROGUERIA SRL', '30-11111111-2')
    s.commit()
    s.close()

    assert len(_providers()) == 2


def test_buscar_proveedor_por_cuit_no_crea_nada():
    """Es un lookup: si no hay match devuelve None, no inventa un Provider."""
    s = database.SessionLocal()
    try:
        assert buscar_proveedor_por_cuit(s, '30-99999999-9') is None
        assert buscar_proveedor_por_cuit(s, '') is None
        assert buscar_proveedor_por_cuit(s, None) is None
    finally:
        s.close()
    assert _providers() == []


def test_resolver_provider_desde_la_factura_cruza_formatos():
    """`_resolve_provider_from_invoice` es el que usa el cruce factura→proveedor."""
    from data_extract import _resolve_provider_from_invoice

    s = database.SessionLocal()
    s.add(Provider(razon_social='DROGUERÍA KELLERHOFF S.A.', cuit='30539756490'))
    s.commit()
    s.close()

    s = database.SessionLocal()
    try:
        inv = Invoice(numero_factura='0046-1', proveedor_razon='DROGUERÍA KELLERHOFF S.A',
                      proveedor_cuit='30-53975649-0', tipo_comprobante='FAC')
        prov = _resolve_provider_from_invoice(s, inv)
        assert prov is not None and prov.cuit == '30539756490'
    finally:
        s.close()


# ── Sufijos societarios con puntos ───────────────────────────────────────────

def test_normalizar_nombre_saca_el_sufijo_aunque_termine_en_punto():
    """Los ejemplos del docstring de `_normalizar_nombre_entidad`, que hasta
    ahora sólo pasaban en su variante sin puntos: con 'S.A.' el `\\b` del sufijo
    no cerraba contra el punto final y el sufijo quedaba pegado al nombre."""
    assert _normalizar_nombre_entidad('Droguería Suizo Argentina S.A.') == 'suizo argentina'
    assert _normalizar_nombre_entidad('DROGUERIA SUIZO ARGENTINA SA') == 'suizo argentina'
    assert _normalizar_nombre_entidad('Roemmers S.A.I.C.F.') == 'roemmers'
    assert _normalizar_nombre_entidad('Roemmers') == 'roemmers'


def test_normalizar_nombre_iguala_las_dos_escrituras_de_kellerhoff():
    """El caso que dejaba pasar el duplicado: el parser escribe 'S.A' (sin punto
    final) y la fila de proveedores 'S.A.'."""
    assert (_normalizar_nombre_entidad('DROGUERÍA KELLERHOFF S.A')
            == _normalizar_nombre_entidad('DROGUERÍA KELLERHOFF S.A.')
            == 'kellerhoff')


def test_normalizar_nombre_no_recorta_nombres_propios():
    """Sacar el sufijo no puede comerse parte del nombre."""
    assert _normalizar_nombre_entidad('SANDOZ') == 'sandoz'
    assert _normalizar_nombre_entidad('Casasco') == 'casasco'
    assert _normalizar_nombre_entidad('Elea Phoenix S.A.') == 'elea phoenix'


# ── Invoice.proveedor_id: el proveedor se resuelve UNA vez, en el alta ───────

def test_el_alta_de_factura_guarda_el_proveedor_id():
    """`save_invoice_to_db` ya resolvía el proveedor y tiraba el resultado."""
    from data_extract import save_invoice_to_db

    s = database.SessionLocal()
    s.add(Provider(razon_social='DROGUERÍA KELLERHOFF S.A.', cuit='30539756490'))
    s.commit()
    s.close()

    s = database.SessionLocal()
    try:
        inv = save_invoice_to_db(s, {
            'numero_factura': '0046-9', 'fecha': __import__('datetime').date(2026, 9, 1),
            'proveedor_razon': 'DROGUERÍA KELLERHOFF S.A', 'proveedor_cuit': '30-53975649-0',
            'total': 100, 'items': [],
        })
        s.commit()
        assert inv.proveedor_id is not None
        assert s.get(Provider, inv.proveedor_id).cuit == '30539756490'
    finally:
        s.close()
    assert len(_providers()) == 1     # sigue sin duplicar
