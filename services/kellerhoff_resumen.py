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
from datetime import datetime

from helpers import _normalize_quadrupled, clave_comprobante, extract_text_with_ocr_fallback

log = logging.getLogger(__name__)

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
    resumen.primer_vencimiento = datos['primer_vencimiento']
    resumen.vencimientos_json = json.dumps(
        [{'fecha': v['fecha'].isoformat() if v['fecha'] else None,
          'importe': v['importe']} for v in datos['vencimientos']])
    if pdf_filename:
        resumen.pdf_filename = pdf_filename
    session.flush()

    por_clave = _indice_facturas(session, proveedor_id)
    ligados = 0
    for it in datos['items']:
        factura_id = por_clave.get(it['clave']) if it['clave'] else None
        session.add(database.ResumenProveedorItem(
            resumen_id=resumen.id, fecha=it['fecha'], tipo=it['tipo'],
            numero=it['numero'], clave=it['clave'],
            numero_remito=it['numero_remito'], total=it['total'],
            factura_id=factura_id,
        ))
        if factura_id:
            ligados += 1

    session.commit()
    cuadra = (datos['total'] is not None
              and abs(datos['total'] - datos['total_calculado']) < 0.01)
    if not cuadra:
        log.warning('[KH-RESUMEN] %s no cuadra: impreso=%s calculado=%s',
                    datos['numero'], datos['total'], datos['total_calculado'])
    return dict(datos, resumen_id=resumen.id, ligados=ligados, cuadra=cuadra)


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
