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
from services.kellerhoff_resumen import (
    estado_item,
    estado_resumen,
    item_tildado,
    parse_resumen_texto,
)

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


def _ajuste_nc(session, numero, monto):
    """NC financiera tal como la guarda el sync: va a la cuenta corriente del
    anunciante, con `proveedor_id` NULL."""
    anunciante = (session.query(database.Anunciante)
                  .filter_by(nombre='LABORATORIO X').first())
    if anunciante is None:
        anunciante = database.Anunciante(nombre='LABORATORIO X')
        session.add(anunciante)
        session.flush()
    pa = database.PagoAjusteCC(proveedor_id=None, anunciante_id=anunciante.id,
                               tipo='AJUSTE_NEG', fecha=date(2026, 8, 22),
                               monto=monto, numero_comprobante=numero)
    session.add(pa)
    session.flush()
    return pa


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


def test_el_checksum_queda_guardado_en_la_fila(tmp_path):
    """El estado tiene que sobrevivir al import, no vivir sólo en el flash.

    Si sólo se devuelve en el dict, alguien cierra el aviso o recarga la página
    y no queda ningún rastro de que el resumen se leyó mal — que es justo lo que
    el checksum existe para evitar.
    """
    roto = RESUMEN_TXT.replace('22.09.2026 997.738,50', '22.09.2026 999.999,99')
    with database.get_db() as session:
        prov = _proveedor(session)
        session.commit()
        res = _importar(session, prov, tmp_path, texto=roto)
        session.expunge_all()          # forzar relectura desde la DB

        guardado = session.get(database.ResumenProveedor, res['resumen_id'])
        assert float(guardado.total) == 999999.99
        assert float(guardado.total_calculado) == 997738.50
        assert guardado.cuadra is False
        assert round(guardado.diferencia, 2) == round(999999.99 - 997738.50, 2)


def test_el_resumen_que_cierra_queda_marcado_como_que_cierra(tmp_path):
    with database.get_db() as session:
        prov = _proveedor(session)
        session.commit()
        res = _importar(session, prov, tmp_path)
        session.expunge_all()

        guardado = session.get(database.ResumenProveedor, res['resumen_id'])
        assert guardado.cuadra is True
        assert guardado.diferencia == 0


# ── Control: tilde por renglón y cierre de la semana ────────────────────────

def _items(session, resumen_id):
    return (session.query(database.ResumenProveedorItem)
            .filter_by(resumen_id=resumen_id)
            .order_by(database.ResumenProveedorItem.numero).all())


def test_un_renglon_sin_comprobante_no_esta_tildado(tmp_path):
    with database.get_db() as session:
        prov = _proveedor(session)
        session.commit()
        res = _importar(session, prov, tmp_path)

        assert all(not item_tildado(it) for it in _items(session, res['resumen_id']))
        assert estado_resumen(session, res['resumen_id']) == (3, 0, False)


def test_la_semana_cierra_cuando_estan_todos(tmp_path):
    with database.get_db() as session:
        prov = _proveedor(session)
        _factura(session, '00046-00279207', 915046.04)
        _factura(session, '00046-00279939', 151817.02)
        _ajuste_nc(session, '0046A00063591', 69124.56)
        session.commit()

        res = _importar(session, prov, tmp_path)
        n, tildados, cerrado = estado_resumen(session, res['resumen_id'])

    assert (n, tildados, cerrado) == (3, 3, True)


def test_la_nc_financiera_tilda_aunque_no_exista_como_factura(tmp_path):
    """Los recuperos van a `pagos_ajustes_cc` con proveedor_id NULL y NUNCA
    crean una Invoice. Sin engancharlos, un resumen con un recupero queda
    pendiente para siempre por un comprobante bien procesado."""
    with database.get_db() as session:
        prov = _proveedor(session)
        ajuste = _ajuste_nc(session, '0046A00063591', 69124.56)
        session.commit()

        res = _importar(session, prov, tmp_path)
        ncr = [it for it in _items(session, res['resumen_id']) if it.tipo == 'NCR'][0]

        assert ncr.factura_id is None
        assert ncr.pago_ajuste_id == ajuste.id
        assert item_tildado(ncr) is True


def test_los_checks_que_no_existen_todavia_no_bloquean_el_tilde(tmp_path):
    """`ingreso`/`arca`/`pago` son None = no evaluado, que no es lo mismo que
    False. Cuando se implementen, el tilde se endurece solo."""
    with database.get_db() as session:
        prov = _proveedor(session)
        _factura(session, '00046-00279207', 915046.04)
        session.commit()
        res = _importar(session, prov, tmp_path)

        ligado = [it for it in _items(session, res['resumen_id'])
                  if it.factura_id][0]
        checks = estado_item(ligado)

        assert checks['comprobante'] is True
        assert checks['ingreso'] is None
        assert checks['arca'] is None
        assert checks['pago'] is None
        assert item_tildado(ligado) is True


def test_un_resumen_vacio_no_cuenta_como_cerrado(tmp_path):
    """Cero renglones es "vacío", no "todo controlado"."""
    with database.get_db() as session:
        prov = _proveedor(session)
        session.commit()
        r = database.ResumenProveedor(proveedor_id=prov.id, numero='S99-2026')
        session.add(r)
        session.commit()

        assert estado_resumen(session, r.id) == (0, 0, False)


def test_un_ajuste_ambiguo_no_liga(tmp_path):
    with database.get_db() as session:
        prov = _proveedor(session)
        _ajuste_nc(session, '0046A00063591', 69124.56)
        _ajuste_nc(session, '00046-00063591', 69124.56)   # misma clave
        session.commit()

        res = _importar(session, prov, tmp_path)
        ncr = [it for it in _items(session, res['resumen_id']) if it.tipo == 'NCR'][0]

        assert ncr.pago_ajuste_id is None


def test_sin_total_impreso_el_checksum_dice_no_se_en_vez_de_no_cierra(tmp_path):
    """"No pude leer el total" no es lo mismo que "el total no cierra"."""
    sin_pie = RESUMEN_TXT.replace('1°Vencimiento TOTAL RESUMEN\n22.09.2026 997.738,50\n', '')
    with database.get_db() as session:
        prov = _proveedor(session)
        session.commit()
        res = _importar(session, prov, tmp_path, texto=sin_pie)
        session.expunge_all()

        guardado = session.get(database.ResumenProveedor, res['resumen_id'])
        assert guardado.total is None
        assert guardado.cuadra is None
        assert guardado.diferencia is None


# ── verificar_ingresos_resumen: eslabón "ingreso" contra ObServer ───────────
# get_recepciones_multiples pega al SQL Server real, así que en estos tests
# se mockea — lo que se prueba es la lógica de encolar/persistir, no la query.

def _mock_get_recepciones_multiples(por_numero):
    """por_numero: {numero: filas|None} — construye el reemplazo de
    observer_source.get_recepciones_multiples con esa respuesta fija."""
    def _fake(numeros, id_farmacia=None):
        return {n: por_numero.get(n, []) for n in numeros}
    return _fake


def test_verificar_ingresos_marca_encontrado_y_no_encontrado(tmp_path):
    from unittest.mock import patch

    import observer_source
    from services.kellerhoff_resumen import verificar_ingresos_resumen

    with database.get_db() as session:
        prov = _proveedor(session)
        session.commit()
        res = _importar(session, prov, tmp_path)
        items = _items(session, res['resumen_id'])
        numeros = {it.numero: it for it in items}
        assert len(numeros) == 3

        # El primero de la lista "tiene" recepción, los otros dos no.
        primero = next(iter(numeros))
        fake = _mock_get_recepciones_multiples({
            primero: [{'codigo_barra': '123', 'descripcion': 'x', 'cantidad': 1, 'precio_unitario': 0}],
        })
        with patch.object(observer_source, 'get_recepciones_multiples', fake):
            conteo = verificar_ingresos_resumen(session, res['resumen_id'])

        assert conteo == {'encontrados': 1, 'no_encontrados': 2, 'errores': 0, 'total': 3}
        session.expunge_all()
        refrescados = {it.numero: it for it in _items(session, res['resumen_id'])}
        assert refrescados[primero].ingreso_verificado is True
        assert refrescados[primero].ingreso_verificado_en is not None
        for numero, it in refrescados.items():
            if numero != primero:
                assert it.ingreso_verificado is False


def test_verificar_ingresos_no_corta_el_lote_por_un_item_que_falla(tmp_path):
    """El objetivo de diseño: un ítem que falla (None) no aborta el resto —
    se cuenta aparte y los demás se verifican igual, en la misma corrida."""
    from unittest.mock import patch

    import observer_source
    from services.kellerhoff_resumen import verificar_ingresos_resumen

    with database.get_db() as session:
        prov = _proveedor(session)
        session.commit()
        res = _importar(session, prov, tmp_path)
        items = _items(session, res['resumen_id'])
        numeros = list({it.numero for it in items})
        assert len(numeros) == 3

        def _fake(nums, id_farmacia=None):
            out = {}
            for i, n in enumerate(nums):
                out[n] = None if i == 0 else []   # el primero "falla"
            return out

        with patch.object(observer_source, 'get_recepciones_multiples', _fake):
            conteo = verificar_ingresos_resumen(session, res['resumen_id'])

        assert conteo['total'] == 3
        assert conteo['errores'] == 1
        assert conteo['encontrados'] + conteo['no_encontrados'] == 2

        session.expunge_all()
        refrescados = _items(session, res['resumen_id'])
        # Los 2 que SÍ se pudieron consultar quedaron con un resultado persistido.
        con_resultado = [it for it in refrescados if it.ingreso_verificado is not None]
        assert len(con_resultado) == 2


def test_ingreso_verificado_endurece_el_tilde(tmp_path):
    """Antes de verificar: None no bloquea (test viejo). Después de verificar
    en False: el renglón deja de estar tildado aunque tenga factura ligada."""
    from unittest.mock import patch

    import observer_source
    from services.kellerhoff_resumen import verificar_ingresos_resumen

    with database.get_db() as session:
        prov = _proveedor(session)
        _factura(session, '00046-00279207', 915046.04)
        session.commit()
        res = _importar(session, prov, tmp_path)

        with patch.object(observer_source, 'get_recepciones_multiples',
                          _mock_get_recepciones_multiples({})):  # nada encontrado
            verificar_ingresos_resumen(session, res['resumen_id'])

        session.expunge_all()
        ligado = [it for it in _items(session, res['resumen_id']) if it.factura_id][0]
        checks = estado_item(ligado)
        assert checks['comprobante'] is True
        assert checks['ingreso'] is False
        assert item_tildado(ligado) is False
