import importlib
import re as _re_mod
from datetime import datetime

import pandas as pd

from database import (
    BarcodeMapping,
    Claim,
    ClaimItem,
    ErpStock,
    Invoice,
    InvoiceItem,
    Producto,
    ProductoPrecioHist,
    Provider,
    StockDifference,
    now_ar,
)


def extract_provider_name_from_pdf(pdf_path):
    """Lee el encabezado del PDF y propone el nombre del proveedor."""
    info = extract_provider_info_from_pdf(pdf_path)
    return info.get('razon_social') or ''


def extract_provider_info_from_pdf(pdf_path):
    """Lee el encabezado del PDF y extrae razón social, CUIT, fecha y número.

    Lo que se detecta se precarga en el modo aprendizaje para que el usuario
    no tenga que re-seleccionar campos que ya fueron identificados.
    """
    import re

    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        text = pdf.pages[0].extract_text() or ''

    razon = None
    m = re.search(
        r'^([A-ZÁÉÍÓÚÑÜ][A-ZÁÉÍÓÚÑÜ ]+(?:S\.A\.|S\.R\.L\.|S\.A\.S\.|LTDA\.|S\.C\.))',
        text, re.MULTILINE
    )
    if m:
        razon = m.group(1).strip()

    cuit = None
    # CUIT argentino: 2 dígitos - 8 dígitos - 1 dígito (con o sin guiones, con o sin espacios)
    # Priorizamos el primer CUIT de formato emisor (30-XXXXXXXX-X o 27/20/23)
    for cm in re.finditer(r'\b(\d{2})[\-\s]?(\d{8})[\-\s]?(\d{1})\b', text):
        candidato = f'{cm.group(1)}-{cm.group(2)}-{cm.group(3)}'
        cuit = candidato
        break

    # Fecha: formatos DD/MM/YYYY, DD-MM-YYYY, DD/MM/YY
    fecha = None
    for fm in re.finditer(r'\b(\d{2})[\/\-](\d{2})[\/\-](\d{2,4})\b', text):
        d, mo, y = fm.group(1), fm.group(2), fm.group(3)
        try:
            di, mi = int(d), int(mo)
            if 1 <= di <= 31 and 1 <= mi <= 12:
                fecha = f'{d}/{mo}/{y if len(y) == 4 else "20" + y}'
                break
        except ValueError:
            continue

    # Número de factura / comprobante: patrones comunes en Arg
    numero = None
    patrones_num = [
        r'(?:FACTURA|REMITO|COMPROBANTE)\s*[:\s]*([A-Z]?\s*\d{3,5}[\-\s]?\d{5,10})',
        r'N[º°]\s*[:\s]*(\d{3,5}[\-\s]?\d{5,10})',
        r'\b(\d{4}[\-\s]\d{8})\b',  # 0001-00001234
    ]
    for pat in patrones_num:
        nm = re.search(pat, text, re.IGNORECASE)
        if nm:
            numero = re.sub(r'\s+', '-', nm.group(1).strip())
            break

    return {'razon_social': razon, 'cuit': cuit, 'fecha': fecha, 'numero': numero}


def parse_invoice_pdf(pdf_path, parser_file):
    """Carga dinámicamente el parser del proveedor y parsea el PDF."""
    module = importlib.import_module(f'parsers.{parser_file}')
    return module.parse_invoice_pdf(pdf_path)


def parse_erp_excel(excel_path):
    """
    Parsea el informe de ingreso/egreso de mercadería del ERP.
    Detecta automáticamente la fila de encabezado y los índices de columna,
    manejando el desplazamiento +1 de las columnas numéricas.
    """
    # Intentar leer con openpyxl (.xlsx); si falla, dejar que pandas auto-detecte el engine
    try:
        df_raw = pd.read_excel(excel_path, engine='openpyxl', header=None)
    except Exception:
        df_raw = pd.read_excel(excel_path, header=None)

    # Localizar la fila de encabezado buscando 'Código de Barra'
    header_idx = None
    for i, row in df_raw.iterrows():
        if 'Código de Barra' in row.values or 'codigo_barra' in [str(v).lower() for v in row.values]:
            header_idx = i
            break

    # Fallback: formato simple con columnas estándar (codigo_barra, cantidad, descripcion)
    if header_idx is None:
        df = pd.read_excel(excel_path, engine='openpyxl')
        items = []
        for _, row in df.iterrows():
            barcode = str(row.get('codigo_barra', row.get('Código de Barra', ''))).strip()
            if not barcode or barcode == 'nan':
                continue
            if barcode.endswith('.0'):
                barcode = barcode[:-2]
            precio_raw = row.get('precio_unitario', row.get('Precio', row.get('Importe', 0)))
            try:
                precio = float(precio_raw or 0)
            except (ValueError, TypeError):
                precio = 0
            try:
                cantidad = int(float(row.get('cantidad', row.get('Recibido', 0)) or 0))
            except (ValueError, TypeError):
                cantidad = 0
            # Saltear ítems sin ingreso (cantidad recibida = 0).
            if cantidad == 0:
                continue
            items.append({
                'codigo_barra': barcode,
                'descripcion': str(row.get('descripcion', row.get('Producto', ''))).strip(),
                'cantidad': cantidad,
                'precio_unitario': precio,
            })
        return items

    header = df_raw.iloc[header_idx]
    first_data_idx = header_idx + 2  # la fila siguiente al header suele estar vacía

    def _find_col(label):
        """Devuelve el índice del valor, detectando si está en col o col+1."""
        for i, v in enumerate(header):
            if str(v).strip() == label:
                # Verificar si el valor real está en i o i+1
                if first_data_idx < len(df_raw):
                    val_here = df_raw.iloc[first_data_idx, i]
                    val_next = df_raw.iloc[first_data_idx, i + 1] if i + 1 < len(header) else None
                    if pd.isna(val_here) and val_next is not None and not pd.isna(val_next):
                        return i + 1
                return i
        return None

    col_barcode  = _find_col('Código de Barra')
    col_recibido = _find_col('Recibido')
    col_producto = _find_col('Producto')
    col_precio   = next(
        (c for c in [_find_col(n) for n in ('Precio', 'Precio Unitario', 'P. Unit.', 'Importe', 'Costo')] if c is not None),
        None
    )

    if col_barcode is None:
        return []

    items = []
    for i in range(first_data_idx, len(df_raw)):
        row = df_raw.iloc[i]
        barcode_raw = row.iloc[col_barcode]
        if pd.isna(barcode_raw):
            continue
        barcode = str(barcode_raw).strip()
        if not barcode or barcode == 'nan':
            continue
        if barcode.endswith('.0'):
            barcode = barcode[:-2]

        try:
            cantidad = int(float(row.iloc[col_recibido])) if col_recibido is not None else 0
        except (ValueError, TypeError):
            cantidad = 0

        # Saltear ítems sin ingreso (cantidad recibida = 0): no son parte del
        # ingreso real, ensucian el cruce contra la factura.
        if cantidad == 0:
            continue

        try:
            precio = float(row.iloc[col_precio]) if col_precio is not None else 0
            if pd.isna(precio):
                precio = 0
        except (ValueError, TypeError):
            precio = 0

        descripcion = str(row.iloc[col_producto]).strip() if col_producto is not None else ''
        if descripcion == 'nan':
            descripcion = ''

        items.append({
            'codigo_barra': barcode,
            'descripcion': descripcion,
            'cantidad': cantidad,
            'precio_unitario': precio,
        })
    return items


def _resolve_provider_from_invoice(session, invoice):
    """Lookup read-only del Provider asociado a un Invoice: CUIT primero,
    razón social exacta después. Devuelve None si no matchea — no crea."""
    if not invoice:
        return None
    if invoice.proveedor_cuit:
        prov = session.query(Provider).filter_by(cuit=invoice.proveedor_cuit).first()
        if prov:
            return prov
    if invoice.proveedor_razon:
        return session.query(Provider).filter_by(razon_social=invoice.proveedor_razon).first()
    return None


def save_invoice_to_db(session, invoice_data, pdf_filename=None, tipo_comprobante='FAC'):
    tipo_comprobante = tipo_comprobante or 'FAC'
    es_ncr = tipo_comprobante == 'NCR'
    sign = -1 if es_ncr else 1

    total_raw = invoice_data.get('total') or 0
    invoice = Invoice(
        tipo_comprobante=tipo_comprobante,
        numero_factura=invoice_data['numero_factura'],
        fecha=invoice_data['fecha'],
        proveedor_razon=invoice_data['proveedor_razon'],
        proveedor_cuit=invoice_data.get('proveedor_cuit'),
        proveedor_domicilio=invoice_data.get('proveedor_domicilio'),
        cliente_codigo=invoice_data.get('cliente_codigo'),
        cliente_razon=invoice_data.get('cliente_razon'),
        total=sign * total_raw,
        total_articulos=invoice_data.get('total_articulos', len(invoice_data['items'])),
        total_unidades=invoice_data.get('total_unidades'),
        pdf_filename=pdf_filename,
    )
    session.add(invoice)
    session.flush()
    from helpers import get_or_create_proveedor
    prov = get_or_create_proveedor(session, invoice.proveedor_razon, invoice.proveedor_cuit,
                                   domicilio=invoice.proveedor_domicilio)
    prov_id = prov.id if prov is not None else None
    for item in invoice_data['items']:
        pu = item.get('precio_unitario')
        im = item.get('importe')
        session.add(InvoiceItem(
            factura_id=invoice.id,
            codigo_barra=item.get('codigo_barra'),
            cantidad=item.get('cantidad'),
            descripcion=item.get('descripcion'),
            precio_unitario=sign * pu if pu is not None else None,
            dto=item.get('dto'),
            importe=sign * im if im is not None else None,
            categoria=item.get('categoria'),
            lote=item.get('lote'),
            vencimiento=item.get('vencimiento')
        ))
        # Snapshot de precio histórico (append-only). Solo si hay codigo_barra.
        cb = item.get('codigo_barra')
        if cb and invoice.fecha:
            session.add(ProductoPrecioHist(
                codigo_barra=cb,
                proveedor_id=prov_id,
                proveedor_razon=invoice.proveedor_razon,
                fecha=invoice.fecha,
                precio_publico=item.get('precio_publico'),
                dto_pct=item.get('dto'),
                precio_unitario=pu,      # sin signo → el precio por unidad es positivo
                importe=im,
                factura_id=invoice.id,
                tipo_comprobante=tipo_comprobante,
            ))
    session.commit()
    session.refresh(invoice)
    return invoice


def save_erp_to_db(session, erp_items):
    """Reemplaza TODO erp_stock con esta carga. Devuelve el carga_id.

    El caller DEBE guardar el carga_id devuelto en Invoice.erp_carga_id de cada
    factura que cruce contra esta carga. Si no lo hace, la factura queda como "sin
    ERP" y no se compara — a propósito: es preferible no mostrar nada a mostrar el
    stock de otro chequeo (ver erp_pertenece_a_factura).
    """
    from sqlalchemy import func
    # Estrictamente mayor a cualquier carga_id ya usado: si dos cargas cayeran en el
    # mismo milisegundo compartirían id y una factura vieja matchearía contra la carga
    # nueva, que es justo lo que esto evita. El piso sale de las dos puntas porque el
    # DELETE de abajo borra la evidencia del lado de erp_stock.
    piso = max(v for v in (
        session.query(func.max(ErpStock.carga_id)).scalar(),
        session.query(func.max(Invoice.erp_carga_id)).scalar(),
        0,
    ) if v is not None)
    carga_id = max(int(now_ar().timestamp() * 1000), piso + 1)
    session.query(ErpStock).delete()
    for item in erp_items:
        session.add(ErpStock(
            codigo_barra=item.get('codigo_barra'),
            descripcion=item.get('descripcion'),
            cantidad=item.get('cantidad'),
            precio_unitario=item.get('precio_unitario'),
            carga_id=carga_id,
        ))
    session.commit()
    return carga_id


def carga_erp_actual(session):
    """carga_id de lo que hay hoy en erp_stock. None si está vacía o es legacy."""
    row = session.query(ErpStock.carga_id).first()
    return row[0] if row else None


def erp_pertenece_a_factura(session, invoice):
    """True si el erp_stock cargado es justo el que se cruzó contra esta factura.

    erp_stock es una tabla global que guarda UNA carga a la vez: cada Excel (o sync
    de ObServer) borra la anterior. Entonces el stock que está cargado puede no tener
    nada que ver con la factura que se está mirando — pasa siempre que se sube una
    factura sin Excel (es opcional) o cuando otra carga pisó la tabla después.
    Compararlas da diferencias fantasma del chequeo anterior.
    """
    if invoice is None or not invoice.erp_carga_id:
        return False
    actual = carga_erp_actual(session)
    return actual is not None and actual == invoice.erp_carga_id


_re_num = _re_mod.compile(r'\d+')


def _normalize(s):
    """Normaliza descripción para comparar factura vs ERP. Delega en el matcher central.

    Era `lower()` + colapsar espacios y nada más, así que un acento, un decimal o un
    "B 12" escritos distinto entre la factura y el ERP tiraban el match y el ítem caía
    a "no encontrado" (trabajo manual para el operador). `normalizar_texto` además saca
    acentos y puntuación, normaliza decimales (0.50 == 0.5) y mergea vitaminas
    ("B 12" → "b12"). Se mantiene esta función como el punto único de normalización del
    cruce; el resto de la app ya usaba el matcher (ver producto_matcher.py).
    """
    from producto_matcher import normalizar_texto
    return normalizar_texto(s)


def compare_invoice_vs_erp(session, factura_id):
    invoice = session.get(Invoice, factura_id)
    # Sin ERP propio no hay nada que comparar: el que está cargado es de otro
    # chequeo y compararlo inventa diferencias (ver erp_pertenece_a_factura).
    if not erp_pertenece_a_factura(session, invoice):
        return []
    invoice_items = session.query(InvoiceItem).filter_by(factura_id=factura_id).all()
    all_erp = session.query(ErpStock).all()
    erp_by_barcode = {item.codigo_barra: item for item in all_erp}

    # Índice por descripción. Las claves AMBIGUAS (dos ítems distintos del ERP que
    # normalizan igual) se descartan en vez de quedarse con el último: con un dict
    # comprehension ganaba uno arbitrario (el orden de la tabla) y la factura podía
    # cruzarse contra el producto equivocado, en silencio. Sin match el ítem cae al
    # cruce manual — el error barato (regla: falso negativo > falso positivo).
    erp_by_desc = {}
    _desc_ambiguas = set()
    for item in all_erp:
        if not item.descripcion:
            continue
        k = _normalize(item.descripcion)
        if not k:
            continue
        anterior = erp_by_desc.get(k)
        if anterior is not None and anterior is not item:
            _desc_ambiguas.add(k)
        else:
            erp_by_desc[k] = item
    for k in _desc_ambiguas:
        erp_by_desc.pop(k, None)

    # Expandir erp_by_barcode con códigos alternativos de la tabla productos.
    # Busca productos que tengan CUALQUIER barcode del ERP (legacy alt1/2/3,
    # tabla 1-a-N producto_codigos_barra, u observer). Usa los helpers
    # bulk de helpers.py que consultan la cascada completa.
    from helpers import _find_productos_bulk, _get_all_barcodes
    erp_barcodes = set(erp_by_barcode.keys())
    if erp_barcodes:
        prods_map = _find_productos_bulk(session, erp_barcodes)
        prods_unicos = {p.id: p for p in prods_map.values()}.values()
        for p in prods_unicos:
            todos_bcs = _get_all_barcodes(session, p)
            erp_item = None
            for bc in todos_bcs:
                if bc in erp_by_barcode:
                    erp_item = erp_by_barcode[bc]
                    break
            if erp_item:
                for bc in todos_bcs:
                    if bc not in erp_by_barcode:
                        erp_by_barcode[bc] = erp_item

    # Segunda pasada: expansión desde el lado de la factura.
    # Para cada barcode de la factura, buscar su grupo en productos y agregar
    # todos sus equivalentes al diccionario ERP si alguno ya está en él.
    invoice_barcodes_fac = {item.codigo_barra for item in invoice_items if item.codigo_barra}
    if invoice_barcodes_fac:
        prods_map = _find_productos_bulk(session, invoice_barcodes_fac)
        prods_unicos = {p.id: p for p in prods_map.values()}.values()
        for p in prods_unicos:
            all_bcs = _get_all_barcodes(session, p)
            erp_item = None
            for bc in all_bcs:
                if bc in erp_by_barcode:
                    erp_item = erp_by_barcode[bc]
                    break
            if erp_item:
                for bc in all_bcs:
                    if bc not in erp_by_barcode:
                        erp_by_barcode[bc] = erp_item

    # Cargar proveedor, estrategia de match y mappings
    prov = _resolve_provider_from_invoice(session, invoice)
    proveedor_id = prov.id if prov else None
    match_strategy = (prov.match_strategy or 'barcode') if prov else 'barcode'
    mappings_by_factura_barcode = {}
    if proveedor_id:
        for m in session.query(BarcodeMapping).filter_by(proveedor_id=proveedor_id).all():
            mappings_by_factura_barcode[m.codigo_barra_factura] = m.codigo_barra_erp

    # Resolver cada línea a su ítem de ERP y AGRUPAR: una misma factura puede
    # traer el mismo producto en varios renglones (ej. "2 + 1" bonificación).
    # El ingreso del ERP viene consolidado (cantidad 3), así que hay que sumar
    # las cantidades de factura del grupo antes de comparar; si no, cada renglón
    # se compara solo contra el total del ERP y aparecen diferencias falsas.
    grupos = {}   # key -> acumulador
    orden = []    # preserva el orden de aparición
    for line in invoice_items:
        erp = None
        match_type = None

        if match_strategy == 'descripcion':
            # Paso 1: descripción normalizada
            erp = erp_by_desc.get(_normalize(line.descripcion))
            match_type = 'descripcion'
            # Paso 2: código de barra como fallback
            if erp is None:
                erp = erp_by_barcode.get(line.codigo_barra)
                match_type = 'barcode'
        else:
            # Paso 1: código de barra exacto
            erp = erp_by_barcode.get(line.codigo_barra)
            match_type = 'barcode'
            # Paso 2: descripción normalizada
            if erp is None:
                erp = erp_by_desc.get(_normalize(line.descripcion))
                match_type = 'descripcion'

        # Paso 3 (ambas estrategias): mappings guardados
        if erp is None and line.codigo_barra in mappings_by_factura_barcode:
            mapped_erp_barcode = mappings_by_factura_barcode[line.codigo_barra]
            erp = erp_by_barcode.get(mapped_erp_barcode)
            match_type = 'mapping'

        # Guardar precio unitario del ERP en el ítem de factura
        if erp and erp.precio_unitario is not None:
            line.precio_erp = erp.precio_unitario

        # Clave: por ítem de ERP si matcheó (consolida renglones duplicados),
        # o por código/descripción de factura si no se encontró.
        if erp is not None:
            key = ('erp', erp.codigo_barra)
        else:
            key = ('nf', line.codigo_barra or _normalize(line.descripcion))

        g = grupos.get(key)
        if g is None:
            g = {'codigo_barra': line.codigo_barra, 'descripcion': line.descripcion,
                 'cantidad_factura': 0, 'erp': erp, 'match_type': match_type}
            grupos[key] = g
            orden.append(key)
        g['cantidad_factura'] += line.cantidad

    session.flush()

    differences = []
    for key in orden:
        g = grupos[key]
        erp = g['erp']
        cantidad_erp = erp.cantidad if erp else 0
        diferencia = g['cantidad_factura'] - cantidad_erp
        if diferencia == 0:
            continue
        if erp is None:
            obs = 'Artículo no encontrado en ERP'
        elif g['match_type'] == 'descripcion':
            obs = 'Coincidencia por descripción (código de barra diferente)'
        elif g['match_type'] == 'mapping':
            obs = f'Coincidencia por correspondencia guardada ({erp.codigo_barra})'
        else:
            obs = 'No coincide con ERP'

        differences.append({
            'codigo_barra': g['codigo_barra'],
            'descripcion': g['descripcion'],
            'cantidad_factura': g['cantidad_factura'],
            'cantidad_erp': cantidad_erp,
            'diferencia': diferencia,
            'observaciones': obs,
        })
    return differences


def save_differences(session, factura_id, differences):
    session.query(StockDifference).filter_by(factura_id=factura_id).delete()
    for diff in differences:
        session.add(StockDifference(
            factura_id=factura_id,
            codigo_barra=diff['codigo_barra'],
            descripcion=diff['descripcion'],
            cantidad_factura=diff['cantidad_factura'],
            cantidad_erp=diff['cantidad_erp'],
            diferencia=diff['diferencia'],
            observaciones=diff['observaciones']
        ))
    session.commit()


def sugerir_cruce_manual(diffs, erp_items, umbral=0.55, top_por_item=1):
    """Para cada ítem del ERP sin coincidencia, cuál renglón de la factura se le parece.

    NO auto-matchea: devuelve una sugerencia para que el operador la confirme de un
    click en /compare. Un falso positivo acá termina en un reclamo a la droguería por
    el producto equivocado, así que el fuzzy sugiere y decide una persona (regla:
    falso negativo > falso positivo). El cruce automático sigue siendo exacto.

    Reusa las primitivas de producto_matcher (mismo motor que /ofertas/import). No usa
    match_producto porque ese orquesta contra el catálogo (Producto/ObsProducto) y acá
    el universo son las filas de erp_stock, que no son un target del matcher.

    Args:
        diffs: diferencias YA ordenadas como se numeran en pantalla (nro = índice + 1).
        erp_items: ítems del ERP sin match (los que muestran input de cruce).
        umbral: score mínimo para sugerir. Bajo a propósito: es una pista, no un match.

    Devuelve {erp_id: {'nro', 'score', 'descripcion'}}.
    """
    from producto_matcher import jaccard, refinar_candidatos, tokens_significativos

    if not diffs or not erp_items:
        return {}

    def _numeros(desc):
        """Números de una descripción, ya normalizada (dosis y unidades por envase).

        En farmacia los números SON la presentación: "AMOXIDAL 500 COMP X 16" y
        "AMOXIDAL 600 COMP X 16" son productos distintos, pero comparten marca, forma
        y envase, así que puntúan 64% de parecido y el fuzzy los sugería. Igual pasaba
        con x16 vs x30. Exigir que los números coincidan corta esos falsos positivos
        sin perder los matches reales, que difieren en palabras y no en números
        ("COMP" vs "comprimidos", acentos, "400MG" vs "400 mg").
        """
        return set(_re_num.findall(_normalize(desc)))

    # Pre-tokenizar los renglones de la factura una sola vez (no por cada ítem del ERP).
    lineas = []
    for nro, d in enumerate(diffs, start=1):
        if not d.descripcion:
            continue
        lineas.append({'nro': nro, 'descripcion': d.descripcion,
                       '_toks': tokens_significativos(d.descripcion),
                       '_nums': _numeros(d.descripcion)})
    if not lineas:
        return {}

    # Índice invertido token → renglones. Sin esto son len(erp) × len(factura) jaccards
    # (una factura de 400 contra un ERP de 400 = 160k) y el cruce tarda segundos en
    # abrir. Con el índice sólo se comparan los renglones que comparten algún token.
    # Es el mismo truco que usa producto_matcher para su pool (_candidatos_via_inv).
    inv = {}
    for idx, l in enumerate(lineas):
        for t in l['_toks']:
            inv.setdefault(t, []).append(idx)

    sugerencias = {}
    for erp in erp_items:
        if not erp.descripcion:
            continue
        toks_erp = tokens_significativos(erp.descripcion)
        if not toks_erp:
            continue
        idxs = set()
        for t in toks_erp:
            idxs.update(inv.get(t, ()))
        if not idxs:
            continue
        nums_erp = _numeros(erp.descripcion)
        candidatos = []
        for i in idxs:
            l = lineas[i]
            # Distinta dosis o distinto envase = otro producto, por más que el texto
            # se parezca. Se descarta antes de puntuar.
            if l['_nums'] != nums_erp:
                continue
            sc = jaccard(toks_erp, l['_toks'])
            if sc > 0:
                candidatos.append({'nro': l['nro'], 'descripcion': l['descripcion'], 'score': sc})
        if not candidatos:
            continue
        # refinar_candidatos corre un Levenshtein en Python por candidato: es el 85%
        # del costo de esta función (medido con cProfile). Como sólo se usa el mejor y
        # el segundo (para el chequeo de empate), se le pasa el top-3 por Jaccard y no
        # más. Desempata con Levenshtein + prefijos ("cr" → "crema").
        candidatos.sort(key=lambda c: -c['score'])
        mejores = refinar_candidatos(erp.descripcion, candidatos[:3], top_keep=top_por_item + 1)
        mejor = mejores[0]
        if mejor['score'] < umbral:
            continue
        # Si el segundo está pegado, la sugerencia no distingue: mejor no sugerir nada
        # que mandar al operador hacia una de dos opciones equivalentes.
        if len(mejores) > 1 and (mejor['score'] - mejores[1]['score']) < 0.05:
            continue
        sugerencias[erp.id] = {'nro': mejor['nro'], 'score': round(mejor['score'], 2),
                               'descripcion': mejor['descripcion']}
    return sugerencias


def recalcular_diferencias(session, factura_id):
    """Recalcula y guarda las diferencias de una factura. Devuelve True si recalculó.

    No hace nada si el erp_stock cargado no es el de esta factura: save_differences
    borra y reinserta, así que recalcular en ese caso le borraría a la factura las
    diferencias buenas (las que sí se calcularon contra su propio ingreso) para
    reemplazarlas por nada — o peor, por las de otro chequeo.
    """
    invoice = session.get(Invoice, factura_id)
    if not erp_pertenece_a_factura(session, invoice):
        return False
    save_differences(session, factura_id, compare_invoice_vs_erp(session, factura_id))
    return True


def get_saved_differences(session, factura_id):
    return session.query(StockDifference).filter_by(factura_id=factura_id).all()


def save_barcode_mapping(session, proveedor_id, codigo_barra_factura, codigo_barra_erp,
                         descripcion_factura=None, descripcion_erp=None):
    """Guarda o actualiza una correspondencia de códigos de barra para un proveedor."""
    existing = session.query(BarcodeMapping).filter_by(
        proveedor_id=proveedor_id,
        codigo_barra_factura=codigo_barra_factura
    ).first()
    if existing:
        existing.codigo_barra_erp = codigo_barra_erp
        existing.descripcion_factura = descripcion_factura or existing.descripcion_factura
        existing.descripcion_erp = descripcion_erp or existing.descripcion_erp
    else:
        session.add(BarcodeMapping(
            proveedor_id=proveedor_id,
            codigo_barra_factura=codigo_barra_factura,
            codigo_barra_erp=codigo_barra_erp,
            descripcion_factura=descripcion_factura,
            descripcion_erp=descripcion_erp,
        ))
    session.commit()


def get_erp_items_with_issues(session, invoice_id):
    """
    Devuelve ítems del ERP cuyo código de barra no aparece en ningún ítem de la factura,
    buscando también por códigos alternativos en la tabla productos.

    Vacío si el erp_stock cargado no es el de esta factura: mostrar el ingreso de otro
    chequeo al lado de la factura es peor que no mostrar nada.
    """
    from sqlalchemy import or_
    if not erp_pertenece_a_factura(session, session.get(Invoice, invoice_id)):
        return []
    invoice_items = session.query(InvoiceItem).filter_by(factura_id=invoice_id).all()
    invoice_barcodes = {item.codigo_barra for item in invoice_items if item.codigo_barra}

    # Expandir invoice_barcodes con TODOS los alts del producto (legacy +
    # tabla 1-a-N + observer). Usa helpers bulk.
    if invoice_barcodes:
        from helpers import _find_productos_bulk, _get_all_barcodes
        prods_map = _find_productos_bulk(session, invoice_barcodes)
        for p in {pp.id: pp for pp in prods_map.values()}.values():
            for bc in _get_all_barcodes(session, p):
                invoice_barcodes.add(bc)

    all_erp = session.query(ErpStock).all()
    return [erp for erp in all_erp if erp.codigo_barra not in invoice_barcodes]


def create_claim(session, factura_id, difference_ids):
    invoice = session.get(Invoice, factura_id)
    if not invoice:
        raise ValueError('Factura no encontrada')

    # Lookup read-only por CUIT/razón social; si no existe, crear con normalización.
    provider = _resolve_provider_from_invoice(session, invoice)
    if not provider:
        from helpers import get_or_create_proveedor
        provider = get_or_create_proveedor(session, invoice.proveedor_razon,
                                           invoice.proveedor_cuit,
                                           domicilio=invoice.proveedor_domicilio)
    claim = Claim(
        proveedor_id=provider.id,
        factura_id=invoice.id,
        numero_factura=invoice.numero_factura,
        fecha=invoice.fecha,
        estado='ABIERTO'
    )
    session.add(claim)
    session.flush()

    differences = session.query(StockDifference).filter(
        StockDifference.factura_id == factura_id,
        StockDifference.id.in_(difference_ids)
    ).all()
    for diff in differences:
        session.add(ClaimItem(
            reclamo_id=claim.id,
            diferencia_id=diff.id,
            codigo_barra=diff.codigo_barra,
            descripcion=diff.descripcion,
            cantidad_factura=diff.cantidad_factura,
            cantidad_erp=diff.cantidad_erp,
            diferencia=diff.diferencia,
            observaciones=diff.observaciones
        ))
    session.commit()
    session.refresh(claim)
    return claim


def complete_claim(session, claim_id):
    claim = session.get(Claim, claim_id)
    if not claim:
        return None
    claim.estado = 'COMPLETADO'
    session.commit()
    session.refresh(claim)
    return claim
