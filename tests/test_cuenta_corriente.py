"""Cálculo de saldo de cuenta corriente de proveedores.

El módulo existe porque el extracto y el listado calculaban el saldo cada uno
por su cuenta y daban números distintos para el mismo proveedor. Los tests de
paridad de abajo son los que impiden que eso vuelva a pasar: si alguien toca la
clasificación en un solo lado, fallan.
"""

from datetime import date

import pytest

import database
from services.cuenta_corriente import (
    clasificar_comprobante,
    movimientos_proveedor,
    normalizar_cuit,
    saldos_por_proveedor,
)


# ── clasificar_comprobante ──────────────────────────────────────────────────

def test_factura_va_al_debe():
    assert clasificar_comprobante('FAC', 1000) == (1000.0, 0.0, 0.0)


def test_nota_de_credito_va_al_haber_este_guardada_positiva_o_negativa():
    # Las NC se guardan en negativo, pero manda el tipo: el signo del total no
    # tiene que poder dar vuelta la clasificación.
    assert clasificar_comprobante('NCR', -500) == (0.0, 500.0, 0.0)
    assert clasificar_comprobante('NCR', 500) == (0.0, 500.0, 0.0)


def test_prefactura_no_entra_en_el_saldo():
    debe, haber, informativo = clasificar_comprobante('PREFAC', 800)
    assert (debe, haber) == (0.0, 0.0)
    assert informativo == 800.0


def test_tipo_desconocido_cae_al_debe():
    # Una factura que no sabemos clasificar es deuda, no crédito: preferimos
    # sobreestimar lo que se debe antes que mostrar un saldo a favor falso.
    assert clasificar_comprobante('LO_QUE_SEA', 300) == (300.0, 0.0, 0.0)


@pytest.mark.parametrize('total', [None, 0])
def test_total_vacio_no_explota(total):
    assert clasificar_comprobante('FAC', total) == (0.0, 0.0, 0.0)


# ── normalizar_cuit ─────────────────────────────────────────────────────────

@pytest.mark.parametrize('crudo', [
    '30-64266156-2',    # lo que escriben los parsers y el import ARCA
    '30642661562',      # sin separadores
    '30 64266156 2',
    '30.64266156.2',
])
def test_normalizar_cuit_unifica_los_formatos(crudo):
    assert normalizar_cuit(crudo) == '30642661562'


@pytest.mark.parametrize('crudo', [None, '', '   '])
def test_normalizar_cuit_sin_digitos(crudo):
    assert normalizar_cuit(crudo) == ''


# ── helpers de armado ───────────────────────────────────────────────────────

def _proveedor(session, razon='DROGUERIA TEST', cuit='30-64266156-2'):
    p = database.Provider(razon_social=razon, cuit=cuit, tipo='proveedor', activo=True)
    session.add(p)
    session.flush()
    return p


def _factura(session, numero, total, tipo='FAC', cuit='30-64266156-2',
             razon='DROGUERIA TEST', fecha=date(2026, 7, 1)):
    inv = database.Invoice(numero_factura=numero, fecha=fecha, tipo_comprobante=tipo,
                           proveedor_razon=razon, proveedor_cuit=cuit, total=total)
    session.add(inv)
    session.flush()
    return inv


# ── el bug del CUIT ─────────────────────────────────────────────────────────

def test_extracto_encuentra_la_factura_con_el_cuit_en_otro_formato():
    """El bug original: el extracto cruzaba el CUIT por igualdad de string.

    Los parsers escriben '30-64266156-2' y el import ARCA pasa por _fmt_cuit.
    Si el Provider quedó guardado sin guiones, la factura aparecía en el listado
    y NO en el extracto del mismo proveedor.
    """
    with database.get_db() as session:
        prov = _proveedor(session, razon='OTRA RAZON SOCIAL', cuit='30642661562')
        _factura(session, 'A-0001', 1000, cuit='30-64266156-2', razon='DROGUERIA TEST')
        session.commit()

        movs, resumen = movimientos_proveedor(session, prov)

    assert len(movs) == 1, 'la factura no se cruzó por CUIT normalizado'
    assert resumen['saldo'] == 1000.0


def test_no_se_duplica_la_factura_que_matchea_por_cuit_y_por_razon():
    with database.get_db() as session:
        prov = _proveedor(session)
        _factura(session, 'A-0001', 1000)  # mismo cuit Y misma razón social
        session.commit()

        movs, resumen = movimientos_proveedor(session, prov)

    assert len(movs) == 1
    assert resumen['saldo'] == 1000.0


# ── paridad extracto ↔ listado ──────────────────────────────────────────────

def _armar_cuenta_completa(session):
    """Un proveedor con los 5 tipos de movimiento que existen hoy."""
    prov = _proveedor(session)
    _factura(session, 'A-0001', 10000, tipo='FAC', fecha=date(2026, 7, 1))
    _factura(session, 'A-0002', -1500, tipo='NCR', fecha=date(2026, 7, 2))
    _factura(session, 'A-0003', 800, tipo='PREFAC', fecha=date(2026, 7, 3))
    session.add(database.PagoAjusteCC(proveedor_id=prov.id, tipo='AJUSTE_POS',
                                      fecha=date(2026, 7, 4), monto=200))
    session.add(database.Pago(proveedor_id=prov.id, fecha=date(2026, 7, 5), monto=3000))
    session.commit()
    return prov


def test_extracto_y_listado_dan_el_mismo_saldo():
    with database.get_db() as session:
        prov = _armar_cuenta_completa(session)

        _movs, resumen = movimientos_proveedor(session, prov)
        listado = saldos_por_proveedor(session)[prov.id]

    # 10000 (FAC) - 1500 (NCR) + 200 (ajuste) - 3000 (pago) = 5700. La PREFAC
    # de 800 queda afuera del saldo, en su propio total.
    assert resumen['saldo'] == 5700.0
    assert listado['saldo'] == resumen['saldo']
    assert listado['total_prefac'] == resumen['total_prefac'] == 800.0
    assert listado['n_comprobantes'] == resumen['n_comprobantes'] == 3


def test_la_prefactura_no_mueve_el_saldo_en_ninguna_de_las_dos_pantallas():
    """Era la divergencia más cara: el extracto la restaba y el listado la sumaba.

    Sobre una factura de 10.000, el mismo proveedor mostraba 9.200 en una
    pantalla y 10.800 en la otra.
    """
    with database.get_db() as session:
        prov = _proveedor(session)
        _factura(session, 'A-0001', 10000, tipo='FAC')
        _factura(session, 'A-0002', 800, tipo='PREFAC')
        session.commit()

        _movs, resumen = movimientos_proveedor(session, prov)
        listado = saldos_por_proveedor(session)[prov.id]

    assert resumen['saldo'] == 10000.0
    assert listado['saldo'] == 10000.0


def test_el_saldo_acumulado_del_extracto_termina_en_el_saldo_final():
    with database.get_db() as session:
        prov = _armar_cuenta_completa(session)
        movs, resumen = movimientos_proveedor(session, prov)

    assert movs[-1]['saldo'] == resumen['saldo']
    assert [m['tipo'] for m in movs] == ['FAC', 'NCR', 'PREFAC', 'AJUSTE_POS', 'PAGO']


def test_proveedor_sin_movimientos_da_saldo_cero():
    with database.get_db() as session:
        prov = _proveedor(session)
        session.commit()
        movs, resumen = movimientos_proveedor(session, prov)
        listado = saldos_por_proveedor(session)[prov.id]

    assert movs == []
    assert resumen['saldo'] == 0.0
    assert listado['saldo'] == 0.0
    assert listado['ultimo_mov'] is None
