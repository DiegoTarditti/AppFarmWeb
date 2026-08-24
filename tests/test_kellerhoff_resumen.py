"""Resumen semanal de cuenta de Kellerhoff: parseo y cruce contra nuestras facturas.

El cruce existe porque cada sistema escribe el MISMO comprobante distinto
(Kellerhoff `0046A00279207`, ObServer `A004600279207`, nosotros `00046-00279207`).
Los tests de `clave_comprobante` son los que impiden que eso vuelva a dar falsos
negativos en masa — ver `docs/controles_kellerhoff.md`.
"""

from datetime import date

import pytest

import database
from helpers import clave_comprobante
from services.cuenta_corriente import corte_resumenes, movimientos_proveedor
from services.kellerhoff_resumen import parse_resumen_texto

# Pie del resumen tal como lo devuelve pdfplumber: son DOS celdas lado a lado,
# así que las etiquetas caen en una línea y los valores en la siguiente.
RESUMEN_TXT = """DROGUERIA RESUMEN N°: S34-2026
Fecha Generación: 24.08.2026 17:51:34
1000002873 - BADIA DE RODRIGUEZ GLADIS ELENA
Cierre: Viernes Semana: 34 Período: 22.08.2026 - 28.08.2026
Fecha Comprobante Nro Cpbte. Nro. Remito Total Cpbte.
22.08.2026 FAC 0046A00279207 0047R00293853 915.046,04
22.08.2026 NCR 0046A00063591 -69.124,56
23.08.2026 FAC 0046A00279939 0047R00294617 151.817,02
Vencimientos: 22.09.2026 117.908,57
Vencimientos: 25.09.2026 879.740,05
1°Vencimiento TOTAL RESUMEN
22.09.2026 997.738,50
Impreso por: DROGUERÍA KELLERHOFF S.A.
"""


# ── clave_comprobante: el mismo comprobante escrito por cada sistema ─────────

@pytest.mark.parametrize('crudo', [
    '0046A00279207',    # Kellerhoff: letra en el medio
    'A004600279207',    # ObServer: letra adelante
    '00046-00279207',   # nuestro, 5 dígitos de punto de venta
    '0046-00279207',    # nuestro, 4 dígitos
    '46-279207',        # ya normalizado
])
def test_los_formatos_de_los_tres_sistemas_dan_la_misma_clave(crudo):
    assert clave_comprobante(crudo) == '46-279207'


@pytest.mark.parametrize('crudo', ['', None, 'basura', '12345', '-'])
def test_lo_que_no_se_puede_interpretar_no_inventa_clave(crudo):
    # Preferimos no cruzar antes que cruzar mal: un match equivocado termina
    # ligando una factura al comprobante de otra.
    assert clave_comprobante(crudo) is None


def test_no_confunde_punto_de_venta_con_numero():
    # 0047 es el punto de venta de los REMITOS: no puede colisionar con el 0046
    # de las facturas aunque el número sea parecido.
    assert clave_comprobante('0047R00293853') != clave_comprobante('0046A00293853')


# ── parseo del PDF ──────────────────────────────────────────────────────────

def test_parsea_cabecera_y_periodo():
    r = parse_resumen_texto(RESUMEN_TXT)
    assert r['numero'] == 'S34-2026'
    assert r['cierre'] == 'Viernes'
    assert r['periodo_desde'] == date(2026, 8, 22)
    assert r['periodo_hasta'] == date(2026, 8, 28)
    assert r['generado_en'].hour == 17


def test_lee_el_pie_en_dos_columnas():
    """El total y el 1° vencimiento nunca están pegados a su etiqueta."""
    r = parse_resumen_texto(RESUMEN_TXT)
    assert r['total'] == 997738.50
    assert r['primer_vencimiento'] == date(2026, 9, 22)


def test_el_total_impreso_cierra_contra_la_suma_de_los_renglones():
    # El PDF es autoconsistente; esto es el checksum del parseo.
    r = parse_resumen_texto(RESUMEN_TXT)
    assert r['total_calculado'] == r['total']


def test_la_nota_de_credito_se_lee_negativa_y_sin_remito():
    r = parse_resumen_texto(RESUMEN_TXT)
    ncr = [i for i in r['items'] if i['tipo'] == 'NCR']
    assert len(ncr) == 1
    assert ncr[0]['total'] == -69124.56
    assert ncr[0]['numero_remito'] == ''


def test_todos_los_renglones_salen_con_clave_y_remito():
    r = parse_resumen_texto(RESUMEN_TXT)
    assert len(r['items']) == 3
    assert all(i['clave'] for i in r['items'])
    assert [i['numero_remito'] for i in r['items'] if i['tipo'] == 'FAC'] == [
        '0047R00293853', '0047R00294617']


def test_los_vencimientos_se_leen_agrupados_por_fecha():
    r = parse_resumen_texto(RESUMEN_TXT)
    assert r['vencimientos'] == [
        {'fecha': date(2026, 9, 22), 'importe': 117908.57},
        {'fecha': date(2026, 9, 25), 'importe': 879740.05},
    ]


# ── import + cruce contra la cuenta corriente ───────────────────────────────

def _proveedor(session):
    p = database.Provider(razon_social='Kellerhoff', cuit='30-53975649-0',
                          tipo='drogueria', activo=True)
    session.add(p)
    session.flush()
    return p


def _factura(session, numero, total, fecha=date(2026, 8, 22)):
    inv = database.Invoice(numero_factura=numero, fecha=fecha, tipo_comprobante='FAC',
                           proveedor_razon='Kellerhoff', proveedor_cuit='30-53975649-0',
                           total=total)
    session.add(inv)
    session.flush()
    return inv


def _importar(session, prov, tmp_path, texto=RESUMEN_TXT):
    """Importa `texto` como si viniera de un PDF (sin tocar pdfplumber)."""
    from unittest.mock import patch

    import services.kellerhoff_resumen as mod
    with patch.object(mod, 'parse_resumen_pdf', lambda _p: parse_resumen_texto(texto)):
        return mod.importar_resumen(session, str(tmp_path / 'x.pdf'), prov.id)


def test_liga_la_factura_aunque_este_escrita_en_otro_formato(tmp_path):
    """El corazón del asunto: el resumen dice `0046A00279207` y nosotros
    guardamos `00046-00279207`. Sin normalizar, no ligaría ninguna."""
    with database.get_db() as session:
        prov = _proveedor(session)
        inv = _factura(session, '00046-00279207', 915046.04)
        session.commit()

        res = _importar(session, prov, tmp_path)
        assert res['ligados'] == 1

        movs, _ = movimientos_proveedor(session, prov)
        fila = [m for m in movs if m['id'] == inv.id][0]
        assert fila['resumen']['numero'] == 'S34-2026'


def test_la_factura_que_no_esta_en_el_resumen_no_queda_ligada(tmp_path):
    with database.get_db() as session:
        prov = _proveedor(session)
        _factura(session, '00046-00999999', 1000)   # no figura en el resumen
        session.commit()

        res = _importar(session, prov, tmp_path)
        assert res['ligados'] == 0

        movs, _ = movimientos_proveedor(session, prov)
        assert movs[0]['resumen'] is None


def test_reimportar_el_mismo_resumen_no_duplica_renglones(tmp_path):
    with database.get_db() as session:
        prov = _proveedor(session)
        session.commit()

        primero = _importar(session, prov, tmp_path)
        segundo = _importar(session, prov, tmp_path)

        assert primero['resumen_id'] == segundo['resumen_id']
        n = (session.query(database.ResumenProveedorItem)
             .filter_by(resumen_id=segundo['resumen_id']).count())
        assert n == 3


def test_una_clave_ambigua_no_liga_ninguna_factura(tmp_path):
    """Dos facturas nuestras que normalizan igual: no se elige una arbitraria."""
    with database.get_db() as session:
        prov = _proveedor(session)
        _factura(session, '00046-00279207', 915046.04)
        _factura(session, '0046-279207', 915046.04)   # misma clave
        session.commit()

        res = _importar(session, prov, tmp_path)
        assert res['ligados'] == 0


def test_el_corte_dice_hasta_donde_llegan_los_resumenes(tmp_path):
    with database.get_db() as session:
        prov = _proveedor(session)
        session.commit()
        assert corte_resumenes(session, prov) is None

        _importar(session, prov, tmp_path)
        assert corte_resumenes(session, prov) == date(2026, 8, 28)


def test_avisa_cuando_el_total_no_cierra(tmp_path):
    """Si un renglón se leyó mal, el checksum tiene que delatarlo."""
    roto = RESUMEN_TXT.replace('22.09.2026 997.738,50', '22.09.2026 999.999,99')
    with database.get_db() as session:
        prov = _proveedor(session)
        session.commit()
        res = _importar(session, prov, tmp_path, texto=roto)

    assert res['cuadra'] is False
