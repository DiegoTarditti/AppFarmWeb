"""Parser del export masivo de comprobantes de Kellerhoff (un PDF con muchas
facturas/NC), para backfill del histórico sin depender del sync (que solo cubre
60 días).

El PDF trae, por comprobante:
  - Encabezado: FACTURA/NOTA CREDITO Nº, fecha, COND. DE PAGO.
  - Facturas, DOS secciones de ítems con layout distinto:
      · Medicamentos:  BARCODE CANT DESC PRECIO_PUB %DTO PRECIO_UNIT IMPORTE
      · *** PRODUCTOS GRAVADOS ***:  BARCODE CANT DESC PRECIO_PUB IMPORTE  (sin
        %Dto ni unitario separado — perfumería/gravados a precio pleno).
  - Líneas TRF (oferta de laboratorio) sueltas.
  - NC: la mayoría son RECUPERO (financieras, lab en el texto); pocas itemizadas
    de vencido/devolución (layout de 3 números: BARCODE CANT DESC PUB UNIT IMPORTE).
  - Pie fiscal: Monto Exento | Gravado | IVA | Percep IVA | Percepciones | TOTAL.

Un comprobante puede ocupar VARIAS páginas: se agrupa por el encabezado.
"""
from __future__ import annotations

import re

_PRECIO = r'-?[\d.]+,\d{2}'

# Encabezado que abre un comprobante. El nº viene '0046-00069104'.
_RE_HEADER = re.compile(r'(FACTURA|NOTA\s+CREDITO)\s+N.{0,3}?:?\s*(\d{3,5}-\d{6,8})',
                        re.IGNORECASE)
# Ítems con 4 números (medicamentos, con %Dto).
_RE_ITEM4 = re.compile(
    rf'^(\d{{7,14}})\s+(\d+)\s+(.+?)\s+({_PRECIO})\s+({_PRECIO})\s+({_PRECIO})\s+({_PRECIO})\s*$',
    re.MULTILINE)
# Ítems con 3 números (NC de vencido/devolución: PUB UNIT IMPORTE, sin %Dto).
_RE_ITEM3 = re.compile(
    rf'^(\d{{7,14}})\s+(\d+)\s+(.+?)\s+({_PRECIO})\s+({_PRECIO})\s+({_PRECIO})\s*$',
    re.MULTILINE)
# Ítems con 2 números (gravados: PUB IMPORTE).
_RE_ITEM2 = re.compile(
    rf'^(\d{{7,14}})\s+(\d+)\s+(.+?)\s+({_PRECIO})\s+({_PRECIO})\s*$',
    re.MULTILINE)
# TRF (oferta de laboratorio) en el renglón.
_RE_TRF = re.compile(r'\bTRF\s+([0-9]{4,}[A-Z]?)\b', re.IGNORECASE)
# COND. DE PAGO: '180 días FF' / '1 día FF'.
_RE_COND = re.compile(r'COND\.?\s*DE\s*PAGO\s*:?\s*([^\n]{0,40}?)(?:\s+Vto|\n|$)',
                      re.IGNORECASE)
# RECUPERO NC <lab> <periodo> (NC financiera).
_RE_RECUPERO = re.compile(r'RECUPERO\s+NC\s+(.+)', re.IGNORECASE)
# Marcadores de sección / corte.
_RE_GRAVADOS = re.compile(r'\*{3}\s*PRODUCTOS\s+GRAVADOS\s*\*{3}', re.IGNORECASE)
_RE_FALTA = re.compile(r'\*{3}\s*PRODUCTOS\s+EN\s+FALTA', re.IGNORECASE)
_RE_VENCIDO = re.compile(r'\*{3}\s*Producto\s+Vencido\s*\*{3}', re.IGNORECASE)
_RE_PIE = re.compile(r'Hoja\s+Cant|SUB-?TOTAL|TOTAL\s*:', re.IGNORECASE)


def _num(s: str) -> float:
    try:
        return float(s.replace('.', '').replace(',', '.'))
    except (ValueError, AttributeError):
        return 0.0


def _norm_nro(nro: str) -> str:
    """'0046-00069104' → '00046-00069104' (5 díg punto de venta, 8 número)."""
    p, n = nro.split('-', 1)
    return f'{p.zfill(5)}-{n.zfill(8)}'


def _cortar_cuerpo(texto: str) -> str:
    """Deja solo el cuerpo con ítems: corta antes del pie fiscal y de faltantes."""
    fin = len(texto)
    for rx in (_RE_FALTA, _RE_PIE):
        m = rx.search(texto)
        if m:
            fin = min(fin, m.start())
    return texto[:fin]


def _parse_items_seccion(bloque: str, regex, con_dto: bool, con_unit: bool) -> list[dict]:
    items = []
    for m in regex.finditer(bloque):
        g = m.groups()
        bc, cant, desc = g[0], g[1], g[2]
        precios = g[3:]
        pub = _num(precios[0])
        if con_dto:            # PUB %DTO UNIT IMPORTE
            dto, unit, imp = _num(precios[1]), _num(precios[2]), _num(precios[3])
        elif con_unit:         # PUB UNIT IMPORTE  (NC vencido)
            dto, unit, imp = 0.0, _num(precios[1]), _num(precios[2])
        else:                  # PUB IMPORTE  (gravados)
            dto, unit, imp = 0.0, pub, _num(precios[1])
        items.append({
            'barcode': bc, 'cantidad': int(cant),
            'descripcion': ' '.join(desc.split()),
            'precio_pub': pub, 'dto_pct': dto,
            'precio_unitario': unit, 'importe': imp,
        })
    return items


def parsear_items_comprobante(texto: str) -> list[dict]:
    """Ítems de un comprobante, manejando las dos secciones de la factura
    (medicamentos 4-num + gravados 2-num) y las NC itemizadas (3-num)."""
    cuerpo = _cortar_cuerpo(texto)
    mg = _RE_GRAVADOS.search(cuerpo)
    if mg:
        principal, gravados = cuerpo[:mg.start()], cuerpo[mg.end():]
    else:
        principal, gravados = cuerpo, ''
    items = _parse_items_seccion(principal, _RE_ITEM4, con_dto=True, con_unit=False)
    if not items:
        # NC de vencido/devolución: 3 números (PUB UNIT IMPORTE).
        items = _parse_items_seccion(principal, _RE_ITEM3, con_dto=False, con_unit=True)
    if gravados:
        items += _parse_items_seccion(gravados, _RE_ITEM2, con_dto=False, con_unit=False)
    return items


def _parse_comprobante(clase: str, nro: str, texto: str) -> dict:
    tipo = 'NCR' if 'CREDITO' in clase.upper() else 'FAC'
    out = {'tipo': tipo, 'numero': _norm_nro(nro), 'texto': texto}
    # Fecha 'FECHA: 01/07/2026 10:43'
    mf = re.search(r'FECHA:\s*(\d{2}/\d{2}/\d{4})', texto)
    out['fecha'] = mf.group(1) if mf else None
    # COND. DE PAGO
    mc = _RE_COND.search(texto)
    out['condicion_pago'] = ' '.join(mc.group(1).split()) if mc else None
    # TRF (dedup, orden)
    trfs = []
    for t in _RE_TRF.findall(texto):
        if t.upper() not in trfs:
            trfs.append(t.upper())
    out['trf'] = ', '.join(trfs) or None
    # NC financiera (RECUPERO): concepto/lab, sin ítems de producto
    mr = _RE_RECUPERO.search(texto)
    if tipo == 'NCR' and mr:
        out['nc_financiera'] = True
        out['concepto'] = ' '.join(mr.group(1).split())
        out['items'] = []
    else:
        out['nc_financiera'] = False
        out['concepto'] = None
        out['items'] = parsear_items_comprobante(texto)
    return out


def iter_comprobantes_texto(full_text: str):
    """Divide el texto completo del PDF en comprobantes por su encabezado y
    parsea cada uno. Yields dicts (ver _parse_comprobante).

    El encabezado (FACTURA/NC Nº) se REPITE en cada página del comprobante, así
    que se coalescen los bloques consecutivos con el mismo número en uno solo
    (si no, un comprobante de 2 páginas salía dos veces, el 2º con 0 ítems)."""
    matches = list(_RE_HEADER.finditer(full_text))
    if not matches:
        return
    grupos = []   # [clase, nro_norm, nro_raw, ini, fin]
    for i, m in enumerate(matches):
        ini = m.start()
        fin = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        nro_norm = _norm_nro(m.group(2))
        if grupos and grupos[-1][1] == nro_norm:
            grupos[-1][4] = fin        # misma nº → extender el bloque
        else:
            grupos.append([m.group(1), nro_norm, m.group(2), ini, fin])
    for clase, _nn, nro_raw, ini, fin in grupos:
        yield _parse_comprobante(clase, nro_raw, full_text[ini:fin])


def leer_pdf_texto(pdf_path: str) -> str:
    """Texto plano del PDF completo (sin OCR — el export es digital)."""
    import pdfplumber
    partes = []
    with pdfplumber.open(pdf_path) as pdf:
        for pg in pdf.pages:
            partes.append(pg.extract_text() or '')
    return '\n'.join(partes)


def parsear_pdf(pdf_path: str) -> list[dict]:
    return list(iter_comprobantes_texto(leer_pdf_texto(pdf_path)))


# ── Backfill contra la DB ────────────────────────────────────────────────────

def backfill(session, comps, kh_cuit, log=None) -> dict:
    """Aplica los comprobantes parseados sobre las facturas Kellerhoff YA
    existentes (matcheadas por número). ADITIVO y conservador:
      - Crea ítems SOLO si la factura no tiene (no pisa lo cargado).
      - Setea trf / condicion_pago solo si están vacíos.
      - Las NC financieras (RECUPERO) se saltean (ya son crédito en la ctacte;
        el sync las atribuye al lab por su cuenta).
      - No crea facturas nuevas: si no matchea un número, lo cuenta y sigue.
    Devuelve estadísticas.
    """
    from database import Invoice, InvoiceItem

    def _log(m):
        if log:
            log(m)

    ult8 = kh_cuit[-8:]
    st = {'total': len(comps), 'match': 0, 'sin_match': 0, 'saltados_fin': 0,
          'ya_tenian_items': 0, 'facturas_backfill': 0, 'items_creados': 0,
          'trf_set': 0, 'cond_set': 0}

    invs = (session.query(Invoice)
            .filter(Invoice.proveedor_cuit.like(f'%{ult8}%')).all())
    by_nro = {}
    for inv in invs:
        by_nro.setdefault(inv.numero_factura, inv)

    for i, c in enumerate(comps, 1):
        if c.get('nc_financiera'):
            st['saltados_fin'] += 1
            continue
        inv = by_nro.get(c['numero'])
        if inv is None:
            st['sin_match'] += 1
            continue
        st['match'] += 1
        if inv.items:
            st['ya_tenian_items'] += 1
        elif c['items']:
            for it in c['items']:
                session.add(InvoiceItem(
                    factura_id=inv.id,
                    codigo_barra=(it['barcode'] or '')[:20],
                    descripcion=(it['descripcion'] or '')[:150],
                    cantidad=it['cantidad'],
                    precio_unitario=it['precio_unitario'],
                    dto=(it['dto_pct'] or None),
                    importe=it['importe']))
            inv.total_articulos = len(c['items'])
            inv.total_unidades = sum(x['cantidad'] for x in c['items'])
            st['items_creados'] += len(c['items'])
            st['facturas_backfill'] += 1
        if c.get('trf') and not inv.trf:
            inv.trf = c['trf']
            st['trf_set'] += 1
        if c.get('condicion_pago') and not inv.condicion_pago:
            inv.condicion_pago = c['condicion_pago']
            st['cond_set'] += 1
        if i % 100 == 0:
            session.commit()
            _log(f'{i}/{len(comps)} · {st["facturas_backfill"]} con ítems, '
                 f'{st["items_creados"]} ítems')
    session.commit()
    return st
