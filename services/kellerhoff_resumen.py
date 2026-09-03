"""Resumen semanal de cuenta de Kellerhoff: parseo del PDF + import a DB.

El PDF ("descargaResumenSemanalSap.pdf", `DOCUMENTO NO VALIDO COMO FACTURA`) es
el cierre de la semana: lista los comprobantes que la droguería te cobra, con su
remito, y los vencimientos. Es el único control que cierra la PLATA — ver
`docs/controles_kellerhoff.md` para por qué las otras fuentes no sirven para eso.

Es autoconsistente: la suma de los renglones da el TOTAL RESUMEN impreso. Eso se
usa como checksum del parseo (`total_calculado` vs `total`), así que un renglón
mal leído se detecta solo en vez de meter un número silenciosamente torcido.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta

from helpers import _normalize_quadrupled, clave_comprobante, extract_text_with_ocr_fallback

log = logging.getLogger(__name__)

# Marca en Invoice.erp_filename para las cargas que vienen del cruce
# automático contra ObServer (_guardar_cruce_erp) — así una alarma puede
# distinguir "esto lo cruzamos nosotros" de un Excel subido a mano o un
# /observer/factura/<id>/sync manual (que usan otros textos).
MARCA_CRUCE_AUTOMATICO = 'ObServer (verificar ingresos)'

# 22.08.2026 FAC 0046A00279207 0047R00293853 915.046,04
# 22.08.2026 NCR 0046A00063591               -69.124,56   ← las NC no traen remito
_RE_ITEM = re.compile(
    r'^(\d{2}\.\d{2}\.\d{4})\s+'          # fecha
    r'([A-Z]{2,8})\s+'                    # FAC / NCR
    r'(\d{3,5}[A-Z]\d{4,10})'             # nro comprobante
    r'(?:\s+(\d{3,5}[A-Z]\d{4,10}))?'     # nro remito (opcional)
    r'\s+(-?[\d.]+,\d{2})\s*$'            # total
)
_RE_NUMERO   = re.compile(r'RESUMEN\s*N[°º:\s]*\s*([A-Z0-9-]+)', re.I)
_RE_PERIODO  = re.compile(r'Per[ií]odo[:\s]*(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})', re.I)
_RE_CIERRE   = re.compile(r'Cierre[:\s]*([A-Za-zÁÉÍÓÚáéíóú]+)', re.I)
_RE_GENERADO = re.compile(r'Fecha\s*Generaci[óo]n[:\s]*(\d{2}\.\d{2}\.\d{4})\s*(\d{2}:\d{2}:\d{2})?', re.I)
_RE_VENC     = re.compile(r'Vencimientos?[:\s]*(\d{2}\.\d{2}\.\d{4})\s+([\d.]+,\d{2})', re.I)

# El pie son DOS celdas lado a lado, así que pdfplumber devuelve las etiquetas en
# una línea y los valores en la siguiente:
#     1°Vencimiento TOTAL RESUMEN
#     22.09.2026 13.230.715,38
# Por eso no alcanza con buscar "TOTAL RESUMEN <importe>": el importe nunca está
# pegado a su etiqueta.
_RE_PIE = re.compile(
    r'1\S{0,2}\s*Vencimiento\s+TOTAL\s*RESUMEN\s*\n\s*'
    r'(\d{2}\.\d{2}\.\d{4})\s+([\d.]+,\d{2})', re.I)
# Fallbacks por si otro resumen sale con el pie en una sola columna.
_RE_TOTAL    = re.compile(r'TOTAL\s*RESUMEN\s*[\r\n]*\s*([\d.]+,\d{2})', re.I)
_RE_PRIMER_V = re.compile(r'1\S{0,2}\s*Vencimiento\s*[\r\n]*\s*(\d{2}\.\d{2}\.\d{4})', re.I)


def _fecha(s):
    return datetime.strptime(s, '%d.%m.%Y').date() if s else None


def _monto(s):
    """'915.046,04' → 915046.04 · '-69.124,56' → -69124.56"""
    return float((s or '0').replace('.', '').replace(',', '.'))


def parse_resumen_pdf(pdf_path):
    """PDF del resumen → dict con cabecera, ítems y vencimientos.

    No toca la DB. Devuelve `total_calculado` aparte de `total` para que el
    caller decida qué hacer si no cierran.
    """
    return parse_resumen_texto(
        _normalize_quadrupled(extract_text_with_ocr_fallback(pdf_path)))


def parse_resumen_texto(texto):
    """El parseo propiamente dicho, separado de la lectura del PDF para poder
    testearlo sin un archivo."""

    def _g(rx, grupo=1, default=None):
        m = rx.search(texto)
        return m.group(grupo) if m else default

    generado = None
    m = _RE_GENERADO.search(texto)
    if m:
        fmt = '%d.%m.%Y %H:%M:%S' if m.group(2) else '%d.%m.%Y'
        generado = datetime.strptime(
            f'{m.group(1)} {m.group(2)}'.strip() if m.group(2) else m.group(1), fmt)

    items = []
    for linea in texto.splitlines():
        mi = _RE_ITEM.match(linea.strip())
        if not mi:
            continue
        fecha, tipo, numero, remito, total = mi.groups()
        items.append({
            'fecha': _fecha(fecha),
            'tipo': tipo.upper(),
            'numero': numero,
            'clave': clave_comprobante(numero),
            'numero_remito': remito or '',
            'total': _monto(total),
        })

    vencimientos = [{'fecha': _fecha(f), 'importe': _monto(i)}
                    for f, i in _RE_VENC.findall(texto)]

    pie = _RE_PIE.search(texto)
    if pie:
        primer_venc, total_impreso = _fecha(pie.group(1)), _monto(pie.group(2))
    else:
        m_tot = _RE_TOTAL.search(texto)
        primer_venc = _fecha(_g(_RE_PRIMER_V))
        total_impreso = _monto(m_tot.group(1)) if m_tot else None

    per = _RE_PERIODO.search(texto)
    return {
        'numero': _g(_RE_NUMERO),
        'cierre': _g(_RE_CIERRE),
        'periodo_desde': _fecha(per.group(1)) if per else None,
        'periodo_hasta': _fecha(per.group(2)) if per else None,
        'generado_en': generado,
        'primer_vencimiento': primer_venc,
        'total': total_impreso,
        'total_calculado': round(sum(i['total'] for i in items), 2),
        'vencimientos': vencimientos,
        'items': items,
    }


def importar_resumen(session, pdf_path, proveedor_id, pdf_filename=None):
    """Parsea el PDF y lo guarda. Reimportar el mismo número REEMPLAZA sus ítems.

    Devuelve el dict del parseo + `resumen_id`, `ligados` y `cuadra`.
    """
    import database

    datos = parse_resumen_pdf(pdf_path)
    if not datos['numero']:
        raise ValueError('No se pudo leer el número de resumen del PDF')
    if not datos['items']:
        raise ValueError('El PDF no tiene renglones de comprobantes')

    resumen = (session.query(database.ResumenProveedor)
               .filter_by(proveedor_id=proveedor_id, numero=datos['numero']).first())
    if resumen:
        # Reimport: se borran los ítems viejos para no duplicar renglones.
        (session.query(database.ResumenProveedorItem)
         .filter_by(resumen_id=resumen.id).delete(synchronize_session=False))
    else:
        resumen = database.ResumenProveedor(proveedor_id=proveedor_id,
                                            numero=datos['numero'])
        session.add(resumen)

    resumen.periodo_desde = datos['periodo_desde']
    resumen.periodo_hasta = datos['periodo_hasta']
    resumen.cierre = datos['cierre']
    resumen.generado_en = datos['generado_en']
    resumen.total = datos['total']
    resumen.total_calculado = datos['total_calculado']
    resumen.primer_vencimiento = datos['primer_vencimiento']
    resumen.vencimientos_json = json.dumps(
        [{'fecha': v['fecha'].isoformat() if v['fecha'] else None,
          'importe': v['importe']} for v in datos['vencimientos']])
    if pdf_filename:
        resumen.pdf_filename = pdf_filename
    session.flush()

    por_clave = _indice_facturas(session, proveedor_id)
    ajustes_por_clave = _indice_ajustes(session)
    ligados = 0
    for it in datos['items']:
        clave = it['clave']
        factura_id = por_clave.get(clave) if clave else None
        # Sólo se busca entre los ajustes si no hay factura: una NC financiera
        # nunca crea Invoice, pero al revés no puede pasar.
        ajuste_id = (ajustes_por_clave.get(clave)
                     if (clave and not factura_id) else None)
        session.add(database.ResumenProveedorItem(
            resumen_id=resumen.id, fecha=it['fecha'], tipo=it['tipo'],
            numero=it['numero'], clave=clave,
            numero_remito=it['numero_remito'], total=it['total'],
            factura_id=factura_id, pago_ajuste_id=ajuste_id,
        ))
        if factura_id or ajuste_id:
            ligados += 1

    session.commit()
    # `cuadra` sale del modelo, no de una variable local: el estado tiene que
    # quedar en la fila para que se vea meses después en el listado y en el
    # detalle, no sólo en el flash de este import.
    if resumen.cuadra is False:
        log.warning('[KH-RESUMEN] %s no cuadra: impreso=%s calculado=%s (dif %s)',
                    datos['numero'], resumen.total, resumen.total_calculado,
                    resumen.diferencia)
    return dict(datos, resumen_id=resumen.id, ligados=ligados,
                cuadra=resumen.cuadra, diferencia=resumen.diferencia)


# ── Control: ¿está tildado el renglón? ¿está cerrada la semana? ──────────────

# Los checks que componen el tilde de un renglón, en orden. Los que no están
# implementados devuelven None = "todavía no lo evaluamos" y NO bloquean el
# tilde. Cuando se completan (ARCA, pago) el tilde se endurece solo — sin
# migración y sin tocar la UI. Ver `docs/controles_kellerhoff.md`.
CHECKS = ('comprobante', 'ingreso', 'cruce_erp', 'arca', 'pago')


def cruce_erp_map(session, items):
    """{factura_id: True|False} para los ítems con factura_id ligada.

    True = tiene ERP cargado y sin diferencias. False = tiene ERP cargado y
    hay diferencias. Ausente del dict = nunca se cruzó nada (mismo criterio
    que /results/<id>, ver templates/results.html) — NO es lo mismo que
    "coincide", por eso no se completa con False."""
    from database import Invoice, StockDifference

    fids = [it.factura_id for it in items if it.factura_id]
    if not fids:
        return {}
    cargados = {fid for fid, in session.query(Invoice.id)
               .filter(Invoice.id.in_(fids), Invoice.erp_carga_id.isnot(None))}
    if not cargados:
        return {}
    con_diffs = {fid for fid, in session.query(StockDifference.factura_id)
                .filter(StockDifference.factura_id.in_(cargados)).distinct()}
    return {fid: (fid not in con_diffs) for fid in cargados}


def estado_item(item, cruce_erp=None):
    """{check: True | False | None} de un renglón del resumen.

    None = no evaluado todavía (el control no existe, o no se corrió, o no
    aplica a este renglón), que NO es lo mismo que False = lo evaluamos y no
    está. `cruce_erp` es el dict de `cruce_erp_map` (bulk, uno por resumen);
    sin pasarlo queda None para todos, que es correcto para tests unitarios.
    """
    return {
        # Encontramos el comprobante de nuestro lado. Puede ser una factura o,
        # si es una NC financiera, un ajuste de cuenta corriente.
        'comprobante': bool(item.factura_id or item.pago_ajuste_id),
        # Eslabón 4: ¿ObServer tiene una recepción para este comprobante?
        # None hasta que se corra "Verificar ingresos" (ver
        # verificar_ingresos_resumen) — ahí queda True/False persistido. Las
        # NC no tienen remito y no generan ingreso: quedan en None (no
        # aplica) para siempre, nunca se les corre la verificación.
        'ingreso': item.ingreso_verificado,
        # Cruce de cantidades (StockDifference) — hoy se completa solo cuando
        # "Verificar ingresos" encontró una recepción y pudo cruzarla (ver
        # verificar_ingresos_resumen); antes de eso, o si nunca hubo Excel/
        # sync manual, queda None.
        'cruce_erp': (cruce_erp or {}).get(item.factura_id) if item.factura_id else None,
        'arca': None,       # Invoice.origen == 'arca' / cae
        'pago': None,       # suma de PagoAplicacion vs total
    }


def _guardar_cruce_erp(session, factura_id, recepciones):
    """Guarda el cruce de cantidades (StockDifference) para UNA factura con
    los ítems ya traídos de ObServer — mismo motor que usa
    /observer/factura/<id>/sync a mano, llamado en loop desde
    verificar_ingresos_resumen.

    `save_erp_to_db` reemplaza `erp_stock` (tabla GLOBAL, un solo snapshot a
    la vez) en cada llamada — llamarla 30-40 veces seguidas dentro de un
    resumen dice que, al terminar el lote, sólo la última factura "es dueña"
    del snapshot vivo (ver `erp_pertenece_a_factura`). No importa para lo que
    lee esta función: `Invoice.erp_carga_id` y `StockDifference` quedan
    persistidos y correctos por factura, que es lo que usan `results.html` y
    `cruce_erp_map`. Sólo `/invoice/<id>/compare` podría mostrar el aviso de
    "otro chequeo" para una factura vieja del mismo lote — no se pisa nada
    higiénicamente, es la misma limitación que ya tenía el botón manual.
    """
    from data_extract import compare_invoice_vs_erp, save_differences, save_erp_to_db
    from database import Invoice

    inv = session.get(Invoice, factura_id)
    if inv is None:
        return
    erp_items = [{
        'codigo_barra': r['codigo_barra'],
        'descripcion': r['descripcion'],
        'cantidad': r['cantidad'],
        'precio_unitario': r.get('precio_unitario') or 0,
    } for r in recepciones]
    inv.erp_filename = MARCA_CRUCE_AUTOMATICO
    inv.erp_carga_id = save_erp_to_db(session, erp_items)
    session.commit()
    differences = compare_invoice_vs_erp(session, factura_id)
    save_differences(session, factura_id, differences)


def verificar_ingresos_resumen(session, resumen_id):
    """Corre el cruce contra ObServer (DW.Recepciones) para TODOS los ítems
    del resumen: persiste `ingreso_verificado` (existencia) y, cuando hay
    factura ligada y se encontró la recepción, también el cruce de
    cantidades (`StockDifference`, vía `_guardar_cruce_erp`).

    No corta ante el primer error o comprobante no encontrado: sigue con el
    resto y junta todo para revisar al final (mismo criterio que /batch con
    varias facturas — nunca se corta el lote entero por un ítem puntual).
    Devuelve un resumen de conteos.
    """
    import observer_source
    from database import ResumenProveedorItem, now_ar

    items = (session.query(ResumenProveedorItem)
             .filter_by(resumen_id=resumen_id).all())
    if not items:
        return {'encontrados': 0, 'no_encontrados': 0, 'errores': 0, 'no_aplica': 0, 'total': 0}

    # ObServer registra la recepción contra el REMITO, no la factura (son
    # series de numeración distintas — comprobado contra datos reales: la
    # factura 0046A00193832 no aparece en DW.Recepciones, pero su remito
    # 0047R00204015 sí). Las NCR no traen remito Y, por diseño (ver
    # docs/controles_kellerhoff.md, "Eslabón 4"), una NC no genera ingreso de
    # mercadería — no es "falta el ingreso", es que estructuralmente no puede
    # haber uno. Se excluyen de la búsqueda: no se les toca ingreso_verificado
    # (queda None = no aplica; ver estado_item, que lo lee por numero_remito).
    con_remito = [it for it in items if it.numero_remito]
    numeros = sorted({it.numero_remito for it in con_remito})
    resultados = observer_source.get_recepciones_multiples(numeros)

    conteo = {'encontrados': 0, 'no_encontrados': 0, 'errores': 0,
             'no_aplica': len(items) - len(con_remito), 'total': len(items)}
    ahora = now_ar()
    for it in con_remito:
        recepciones = resultados.get(it.numero_remito)
        if recepciones is None:
            # Ese ítem puntual falló en ObServer (ver get_recepciones_multiples) —
            # se deja como estaba (no se pisa un True/False previo con un error
            # transitorio) y se cuenta aparte para que el operador sepa que hay
            # que reintentar, no que "no está".
            conteo['errores'] += 1
            continue
        it.ingreso_verificado = bool(recepciones)
        it.ingreso_verificado_en = ahora
        if recepciones:
            conteo['encontrados'] += 1
            if it.factura_id:
                _guardar_cruce_erp(session, it.factura_id, recepciones)
        else:
            conteo['no_encontrados'] += 1
    session.commit()
    return conteo


def verificar_ingresos_recientes(session, semanas=4):
    """Re-corre verificar_ingresos_resumen para los resúmenes de las últimas
    N semanas — pensado para un cron diario (ver /api/cron/kellerhoff-
    verificar-ingresos), no para clickear a mano.

    Por qué re-verificar lo ya verificado: una recepción puede cargarse en
    ObServer DÍAS después del comprobante (visto en producción: el resumen
    S35-2026 recién generado tenía varias sin recepción todavía — no eran
    faltantes, era que nadie había hecho el check-in físico aún). Correr esto
    una sola vez deja plantados esos "✗" para siempre aunque la mercadería
    haya llegado. `verificar_ingresos_resumen` es idempotente por ítem, así
    que repetirlo no duplica nada — solo actualiza lo que cambió.
    """
    from database import ResumenProveedor, now_ar

    corte = now_ar().date() - timedelta(days=semanas * 7)
    resumenes = (session.query(ResumenProveedor)
                .filter(ResumenProveedor.periodo_desde >= corte).all())

    total = {'resumenes': 0, 'encontrados': 0, 'no_encontrados': 0,
            'errores': 0, 'no_aplica': 0, 'total': 0}
    for r in resumenes:
        try:
            c = verificar_ingresos_resumen(session, r.id)
        except Exception:
            log.exception('[KH-CRON] verificar_ingresos_resumen falló para resumen %s — sigo con el resto', r.id)
            continue
        total['resumenes'] += 1
        for k in ('encontrados', 'no_encontrados', 'errores', 'no_aplica', 'total'):
            total[k] += c.get(k, 0)
    return total


def item_tildado(item, cruce_erp=None):
    """True si todos los checks YA IMPLEMENTADOS del renglón están OK."""
    evaluados = [v for v in estado_item(item, cruce_erp).values() if v is not None]
    return bool(evaluados) and all(evaluados)


def estado_resumen(session, resumen_id):
    """(n_items, n_tildados, cerrado) — el rollup de la semana.

    `cerrado` es "no queda nada por controlar de lo que hoy sabemos controlar".
    Un resumen sin renglones no está cerrado: está vacío, que es otra cosa.
    """
    import database

    items = (session.query(database.ResumenProveedorItem)
             .filter_by(resumen_id=resumen_id).all())
    cruce_erp = cruce_erp_map(session, items)
    tildados = sum(1 for it in items if item_tildado(it, cruce_erp))
    return len(items), tildados, bool(items) and tildados == len(items)


def _indice_facturas(session, proveedor_id):
    """{clave_comprobante: factura_id} de todas las facturas del proveedor.

    Se arma una sola vez por import y se normaliza en Python, no en SQL:
    `numero_factura` es texto libre (con guión, sin guión, con o sin ceros a la
    izquierda) y ningún LIKE lo cubre.

    Si dos facturas normalizan a la misma clave, la clave se DESCARTA en vez de
    quedarse con una arbitraria — misma regla que el cruce de `/compare`: un
    match ambiguo silencioso es peor que ninguno.
    """
    import database
    from services.cuenta_corriente import _query_facturas_proveedor

    prov = session.get(database.Provider, proveedor_id)
    if prov is None:
        return {}
    por_clave, ambiguas = {}, set()
    for inv_id, numero in _query_facturas_proveedor(session, prov).with_entities(
            database.Invoice.id, database.Invoice.numero_factura):
        clave = clave_comprobante(numero)
        if not clave:
            continue
        if clave in por_clave and por_clave[clave] != inv_id:
            ambiguas.add(clave)
        por_clave[clave] = inv_id
    for clave in ambiguas:
        del por_clave[clave]
        log.warning('[KH-RESUMEN] clave %s ambigua (2+ facturas) — no se liga', clave)
    return por_clave


def _indice_ajustes(session):
    """{clave_comprobante: pago_ajuste_id} de las NC financieras ya registradas.

    Son los recuperos de publicidad: el sync los manda a `pagos_ajustes_cc` con
    `anunciante_id` en vez de crear una `Invoice`. No se filtra por proveedor
    porque esas filas tienen `proveedor_id` NULL a propósito (van a la cuenta
    corriente del anunciante, que es otra); el filtro es la clave, que ya
    incluye punto de venta.

    Misma regla de ambigüedad que las facturas: duplicada → no se liga.
    """
    import database

    por_clave, ambiguas = {}, set()
    for pa_id, numero in (session.query(database.PagoAjusteCC.id,
                                        database.PagoAjusteCC.numero_comprobante)
                          .filter(database.PagoAjusteCC.anunciante_id.isnot(None))):
        clave = clave_comprobante(numero)
        if not clave:
            continue
        if clave in por_clave and por_clave[clave] != pa_id:
            ambiguas.add(clave)
        por_clave[clave] = pa_id
    for clave in ambiguas:
        del por_clave[clave]
        log.warning('[KH-RESUMEN] clave %s ambigua (2+ ajustes) — no se liga', clave)
    return por_clave
