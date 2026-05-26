"""Extracción de facturas de droguerías a JSON estructurado vía Claude (Vision).

Motor reusable: dado el PDF (bytes) + la API key, le pide a Claude que devuelva
el JSON anidado del formato AppFarmWeb (encabezado + detalle + falta_momentanea)
y corre la auto-validación matemática/fiscal.

Las reglas viven en PROMPT — espejo de `.claude/skills/extraer-facturas/SKILL.md`
(si cambia uno, actualizar el otro). Usado por routes/converter.py
(`/converter/<token>/extraer-json`).
"""
import base64
import json
import re

MODEL = 'claude-opus-4-7'   # máxima precisión (mismo que reocr-vision)

PROMPT = """Sos un extractor de facturas de droguerías argentinas. Leé el PDF adjunto y devolvé SOLO un JSON (sin markdown, sin comentarios, sin texto extra) con esta estructura exacta:

{
  "encabezado": {
    "tipo_comprobante": "FAC",
    "letra": "A",
    "numero_factura": "0000-00000000",
    "fecha": "YYYY-MM-DD",
    "proveedor": {"razon_social": "...", "cuit": "...", "domicilio": null, "localidad": null, "cp": null, "gln": null, "iibb": null},
    "cliente":   {"codigo": null, "razon_social": null, "cuit": null, "condicion_iva": null},
    "referencias": {"cufe": null, "nro_op": null, "nro_sap": null, "it": null, "nro_pedido": null, "nro_remito": null},
    "totales": {
      "total_articulos": 0, "total_unidades": 0,
      "subtotal_bruto": null, "descuentos": null,
      "monto_exento": null, "monto_gravado": null,
      "iva_105": null, "iva_21": null, "percepciones": null, "otros": null,
      "total": 0
    }
  },
  "detalle": [
    {"codigo_barra": null, "codigo_interno": null, "cantidad": 0, "descripcion": "...",
     "precio_publico": null, "dto_pct": null, "precio_unitario": null, "importe": null,
     "grupo": "general", "lote": null, "vencimiento": null}
  ],
  "falta_momentanea": [
    {"codigo_barra": null, "codigo_interno": null, "cantidad": 0, "descripcion": "..."}
  ]
}

REGLAS:
- Números: punto decimal, SIN separador de miles (1183326.62). El PDF usa formato argentino (1.183.326,62) → convertir.
- Fechas en ISO (YYYY-MM-DD).
- NO inventes datos: campo ausente → null. NO omitas ítems.
- tipo_comprobante: "FAC" (factura), "NCR" (nota de crédito), "PREFAC" si dice "PREFACTURA" o "DOCUMENTO NO VÁLIDO COMO FACTURA".
- codigo_barra SOLO si es un EAN real (8-14 dígitos). Los códigos internos del proveedor (ej. 1134O, 80-1308, 79-65) van en codigo_interno y codigo_barra queda null.
- CRÍTICO: copiá los códigos EXACTAMENTE como figuran, con letras incluidas. NO conviertas la letra O en cero 0, ni l/I en 1. "1134O" se queda "1134O".
- grupo = el título de la sección que precede al ítem, en slug (minúsculas, sin acentos): general, psicofarmacos, sustancias_controladas, cadena_frio, gravados. Si hay otro título de sección, usá su slug.
- "Productos en falta momentánea / no facturados" (sin precio) NO van a detalle → array falta_momentanea.
- BONIFICACIONES: una bonificación aparece como una línea "BONIFICACION ..." con importe NEGATIVO, más la línea de la unidad bonificada que la origina (suele venir con el mismo flag, ej. "(11)"). NINGUNA de las dos va al detalle: netean a cero. Incluí en el detalle SOLO las unidades facturadas a precio que suman al neto. Si un producto aparece ÚNICAMENTE como bonificación (no tiene línea facturada a precio), NO lo pongas en el detalle.
- Precios: poné lo que muestra el PDF. Algunos proveedores muestran precio_unitario NETO; otros muestran el público/bruto + dto%. Capturá precio_publico, dto_pct, precio_unitario e importe según estén.
- IVA: a iva_105 o iva_21 según la tasa (iva ÷ monto_gravado ≈ 0,105 o 0,21); el otro queda null.
- DESCUENTO GLOBAL: si el PDF aplica descuentos sobre el subtotal (ej. "DESCUENTO ESPECIAL CLIENTE 7,20%", "Descuento a Farmacia", "Descuento Financiero"), poné descuentos = suma de TODAS esas líneas como número POSITIVO (la magnitud, SIN el signo menos aunque en el PDF figuren negativas; si hay varias, sumá sus magnitudes). subtotal_bruto = la SUMA de los importes del detalle (el bruto), NO una línea rotulada "SUBTOTAL"/"NETO" del pie (que puede venir ya neta). Se cumple: Σ(importes del detalle) − descuentos = monto_exento + monto_gravado (neto).
- Multi-página: concatená el detalle de TODAS las hojas; los totales salen del pie de la ÚLTIMA hoja.
- Trazables: si un ítem trae "Lote: X Vto: dd.mm.aaaa", capturá lote y vencimiento (ISO).
- total_articulos = cantidad de RENGLONES del detalle (contá los ítems facturados; NO lo deduzcas del pie de la factura).
- proveedor.iibb = el número rotulado "IIBB:". NO uses el de "Ing.Brutos Conv. Mult." (que suele coincidir con el CUIT).

Devolvé el JSON y nada más."""


def _parse_json(raw):
    """Parsea el texto de Claude a dict, tolerando fences ```json y texto alrededor."""
    if not raw:
        return None
    s = raw.strip()
    m = re.search(r'```(?:json)?\s*(.+?)\s*```', s, re.DOTALL)
    if m:
        s = m.group(1).strip()
    i, j = s.find('{'), s.rfind('}')
    if i >= 0 and j > i:
        s = s[i:j + 1]
    try:
        return json.loads(s)
    except Exception:
        return None


def extraer_factura_json(pdf_bytes, api_key, model=MODEL):
    """Llama a Claude con el PDF y devuelve (data, usage).
    Lanza ImportError si falta el paquete anthropic, ValueError si la respuesta
    no es JSON, y propaga las excepciones de la API (crédito, key, rate limit)."""
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode('utf-8')
    content = [
        {'type': 'document', 'source': {'type': 'base64', 'media_type': 'application/pdf', 'data': pdf_b64}},
        {'type': 'text', 'text': PROMPT},
    ]
    # Streaming: con max_tokens alto el SDK lo exige (request potencialmente >10 min).
    # Facturas largas (5+ págs, ~150 ítems) pasan 16K tokens de salida.
    with client.messages.stream(
        model=model,
        max_tokens=32000,
        messages=[{'role': 'user', 'content': content}],
    ) as stream:
        resp = stream.get_final_message()
    raw = ''.join(b.text for b in resp.content if getattr(b, 'type', '') == 'text').strip()
    data = _parse_json(raw)
    if data is None:
        raise ValueError('Claude no devolvió un JSON válido (puede haberse truncado en facturas muy largas).')
    return data, resp.usage


def _f(v):
    """Float tolerante: acepta None, número, o string en formato argentino."""
    if v is None or v == '':
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(' ', '')
    try:
        if ',' in s:
            s = s.replace('.', '').replace(',', '.')
        return float(s)
    except Exception:
        return None


def validar(data, eps=0.05):
    """Auto-validación matemática/fiscal del JSON extraído.

    Devuelve {estado: 'ok'|'warn'|'error', checks, errores, warnings, resumen}.
    estado 'error' = NO importar. Generaliza Kellerhoff (importe neto) y
    Bernabó/Pharmos (importe bruto + descuento global)."""
    errores, warnings, checks = [], [], []
    enc = (data or {}).get('encabezado') or {}
    tot = enc.get('totales') or {}
    det = (data or {}).get('detalle') or []

    def add(check, ok, detalle):
        checks.append({'check': check, 'ok': bool(ok), 'detalle': detalle})

    # Obligatorios
    obl = {'numero_factura': enc.get('numero_factura'), 'fecha': enc.get('fecha'),
           'proveedor': (enc.get('proveedor') or {}).get('razon_social'), 'total': tot.get('total')}
    faltan = [k for k, v in obl.items() if not v]
    add('obligatorios', not faltan, 'OK' if not faltan else 'faltan: ' + ', '.join(faltan))
    if faltan:
        errores.append('Faltan campos obligatorios: ' + ', '.join(faltan))

    # Línea: cantidad x precio_unitario = importe
    bad = []
    for i, it in enumerate(det):
        pu, im, cant = _f(it.get('precio_unitario')), _f(it.get('importe')), _f(it.get('cantidad'))
        if None not in (pu, im, cant) and abs(cant * pu - im) > max(eps, abs(im) * 0.001):
            bad.append(it.get('codigo_barra') or it.get('codigo_interno') or f'#{i + 1}')
    add('lineas', not bad, 'OK' if not bad else f'{len(bad)} renglón(es) no cuadran')
    if bad:
        warnings.append('cant×unit≠importe: ' + ', '.join(map(str, bad[:8])))

    # Grupos
    sin_g = sum(1 for it in det if not it.get('grupo'))
    add('grupos', sin_g == 0, 'OK' if not sin_g else f'{sin_g} sin grupo')
    if sin_g:
        warnings.append(f'{sin_g} ítem(s) sin grupo')

    # total_articulos debe coincidir con la cantidad de renglones del detalle
    ta = tot.get('total_articulos')
    ta_n = int(ta) if isinstance(ta, (int, float)) else None
    if ta_n is not None:
        okt = ta_n == len(det)
        add('total_articulos', okt, f'declarado {ta_n} vs detalle {len(det)}')
        if not okt:
            warnings.append(f'total_articulos={ta_n} no coincide con {len(det)} renglones del detalle')

    # Fiscal: neto + IVA + percepciones + otros = total
    ex = _f(tot.get('monto_exento')) or 0
    gr = _f(tot.get('monto_gravado')) or 0
    iva = (_f(tot.get('iva_105')) or 0) + (_f(tot.get('iva_21')) or 0)
    perc = _f(tot.get('percepciones')) or 0
    otros = _f(tot.get('otros')) or 0
    total = _f(tot.get('total'))
    neto = ex + gr
    if total is not None and neto:
        calc = neto + iva + perc + otros
        okf = abs(calc - total) <= max(eps, abs(total) * 0.001)
        add('fiscal_total', okf, f'neto+IVA+percep+otros={calc:.2f} vs total={total:.2f}')
        if not okf:
            errores.append(f'El total no cuadra: {calc:.2f} ≠ {total:.2f}')

    # El detalle ES el bruto: el bruto se DERIVA de Σdetalle (la IA reporta mal
    # subtotal_bruto seguido: lo confunde con el neto). El descuento se toma en
    # magnitud (|desc|, a veces viene con signo negativo).
    #  - suma_detalle:       Σdetalle - |descuentos| ≈ neto   (check fiscal duro)
    #  - subtotal_declarado: subtotal_bruto reportado ≈ Σdetalle  (cross-check informativo)
    sdet = sum(_f(it.get('importe')) or 0 for it in det)
    desc = abs(_f(tot.get('descuentos')) or 0)
    sub = _f(tot.get('subtotal_bruto'))
    if neto:
        base = sdet - desc
        oks = abs(base - neto) <= max(eps, abs(neto) * 0.005)
        add('suma_detalle', oks, f'(Sdetalle {round(sdet, 2)} - desc {desc:.2f}) vs neto {neto:.2f}')
        if not oks:
            warnings.append('Sdetalle - descuentos no cuadra con el neto.')
    if sub is not None:
        okb = abs(sdet - sub) <= max(eps, abs(sub) * 0.005)
        add('subtotal_declarado', okb, f'subtotal_bruto reportado {sub:.2f} vs Sdetalle {sdet:.2f}')
        if not okb:
            warnings.append(f'subtotal_bruto reportado ({sub:.2f}) != Sdetalle ({sdet:.2f}); se usa Sdetalle.')

    estado = 'error' if errores else ('warn' if warnings else 'ok')
    return {
        'estado': estado, 'checks': checks, 'errores': errores, 'warnings': warnings,
        'resumen': {'items': len(det),
                    'faltantes': len((data or {}).get('falta_momentanea') or []),
                    'sigma_detalle': round(sdet, 2), 'total': total},
    }
