"""
Scraper Playwright para portal Kellerhoff.
URL: kellerhoff.com.ar/ctacte/ConsultaDeComprobantes

Credenciales via env vars:
  KELLERHOFF_URL   (default: https://www.kellerhoff.com.ar)
  KELLERHOFF_USER
  KELLERHOFF_PASS

Tabla de comprobantes (columnas verificadas contra screenshot real):
  0: Fecha
  1: Clase Doc  (FAC / NCR)
  2: Nº Remito  (ej: 0047R00260141 — vacío en NCR)
  3: Nº Comprobante (ej: 0046A00061895 — link azul)
  4: Monto Exento
  5: Monto Gravado
  6: IVA
  7: Monto Otros Imp.
  8: Percepción DGR
  9: Percepción Municipal
 10: Percepción IVA
 11: Total
 12: Descargar (checkbox)

Formato Nº Comprobante → ARCA: '0046A00061895' → '00046-00061895'
  (4 dígitos punto de venta, letra tipo, 8 dígitos número)
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime

KH_URL  = os.environ.get('KELLERHOFF_URL', 'https://www.kellerhoff.com.ar').rstrip('/')
KH_USER = os.environ.get('KELLERHOFF_USER', '')
KH_PASS = os.environ.get('KELLERHOFF_PASS', '')


def scrape_comprobantes(desde: date, hasta: date, status_cb=None,
                        skip_nros: set | None = None) -> list[dict]:
    """
    Retorna lista de comprobantes en el rango [desde, hasta].
    status_cb: callable(str) opcional para reportar progreso al caller.

    Cada dict:
        nro_remito     str   '0047R00260141'
        nro_comp_kh    str   '0046A00061895'   (formato portal)
        nro_comp_arca  str   '00046-00061895'  (formato ARCA para match)
        fecha          date
        clase_doc      str   'FAC' | 'NCR'
        monto_exento   float
        monto_gravado  float
        iva            float
        percepcion_dgr float
        percepcion_mun float
        percepcion_iva float
        total          float
        items          list[dict]   (ver _detalle_comprobante)
    """
    from playwright.sync_api import sync_playwright

    def _cb(msg):
        if status_cb:
            status_cb(msg)

    with sync_playwright() as p:
        _cb('Iniciando Chromium…')
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        try:
            _cb('Conectando al portal Kellerhoff…')
            _login(page)
            _cb('Login OK — cargando comprobantes…')
            comps = _listar_comprobantes(page, desde, hasta)
            _skip = skip_nros or set()
            nuevos = [c for c in comps if c.get('nro_comp_arca') not in _skip]
            _cb(f'Lista obtenida: {len(comps)} comprobante(s) ({len(nuevos)} nuevos). Bajando detalles…')
            for i, comp in enumerate(nuevos, 1):
                nro = comp.get('nro_comp_arca') or comp.get('nro_comp_kh', '?')
                clase = comp.get('clase_doc', '?')
                _cb(f'[{i}/{len(nuevos)}] {clase} {nro} — leyendo detalle…')
                try:
                    analisis = _detalle_comprobante(page, comp)
                except Exception as e:
                    analisis = {'categoria': 'factura', 'items': [], 'faltantes': []}
                    comp['_error_detalle'] = str(e)
                comp['analisis'] = analisis
                comp['items'] = analisis.get('items', [])
                # Línea de resultado por comprobante para el log en vivo.
                if comp.get('_error_detalle'):
                    _cb(f'[{i}/{len(nuevos)}] {clase} {nro} — ⚠ error: {comp["_error_detalle"][:60]}')
                elif analisis.get('categoria') == 'nc_financiera':
                    _cb(f'[{i}/{len(nuevos)}] {clase} {nro} — NC financiera → '
                        f'{analisis.get("anunciante_nombre") or "anunciante ?"}')
                else:
                    n = len(analisis.get('items') or [])
                    fal = len(analisis.get('faltantes') or [])
                    extra = f', {fal} faltante(s)' if fal else ''
                    _cb(f'[{i}/{len(nuevos)}] {clase} {nro} → {n} ítem(s){extra}')
            # Comprobantes ya existentes: no re-navegar, items vacíos (ya están en DB)
            for comp in comps:
                if comp.get('nro_comp_arca') in _skip:
                    comp['analisis'] = {'categoria': 'factura', 'items': [], 'faltantes': []}
                    comp['items'] = []
                    comp['_ya_existe'] = True
            return comps
        finally:
            browser.close()


# ── Login ─────────────────────────────────────────────────────────────────────

def _login(page) -> None:
    import logging
    log = logging.getLogger(__name__)
    page.goto(f'{KH_URL}/Home/Index', wait_until='domcontentloaded')
    page.wait_for_load_state('networkidle')
    page.fill('#login_name', KH_USER)
    page.fill('#login_password', KH_PASS)
    page.click('#botonIniciarSesion')
    # Esperar que la URL cambie — el login exitoso redirige a /mvc/Buscador
    try:
        page.wait_for_url(lambda url: '/Home/Index' not in url, timeout=15000)
    except Exception:
        pass
    page.wait_for_load_state('networkidle')
    log.warning('[KH] URL post-login: %s', page.url)


# ── Listar comprobantes ────────────────────────────────────────────────────────

def _listar_comprobantes(page, desde: date, hasta: date) -> list[dict]:
    import logging
    log = logging.getLogger(__name__)

    page.goto(f'{KH_URL}/ctacte/ConsultaDeComprobantes')
    page.wait_for_load_state('networkidle')
    page.screenshot(path='/tmp/kh_01_comprobantes.png')
    log.warning('[KH] URL actual tras nav: %s', page.url)
    log.warning('[KH] Título: %s', page.title())

    # Intentar seleccionar radio "Fecha" (ignorar si no existe)
    radio = page.query_selector('#radioFecha')
    if radio:
        radio.click()
        log.warning('[KH] Radio Fecha clickeado')
    else:
        log.warning('[KH] Radio Fecha NO encontrado — se continúa sin filtro')

    page.screenshot(path='/tmp/kh_02_radio.png')

    # Campo de rango de fecha (es un daterangepicker — fill() solo no alcanza)
    desde_str = desde.strftime('%d/%m/%Y')
    hasta_str = hasta.strftime('%d/%m/%Y')
    rango = f'{desde_str} - {hasta_str}'
    campo = page.query_selector('#ComprobanteFecha')
    if campo:
        # Setear via JS para disparar los eventos del plugin daterangepicker
        page.evaluate(f"""
            (function() {{
                var el = document.getElementById('ComprobanteFecha');
                el.value = '{rango}';
                // Disparar eventos que el daterangepicker escucha
                ['input', 'change', 'apply.daterangepicker'].forEach(function(ev) {{
                    el.dispatchEvent(new Event(ev, {{bubbles: true}}));
                }});
                // Si usa jQuery daterangepicker, setear via API
                if (window.$ && $(el).data('daterangepicker')) {{
                    $(el).data('daterangepicker').setStartDate('{desde_str}');
                    $(el).data('daterangepicker').setEndDate('{hasta_str}');
                    $(el).val('{rango}');
                }}
            }})();
        """)
        log.warning('[KH] Rango escrito via JS: %s', rango)
        # Verificar que quedó el valor correcto
        val_actual = campo.input_value()
        log.warning('[KH] Valor campo fecha tras set: %s', val_actual)
    else:
        log.warning('[KH] Campo fecha NO encontrado')

    page.screenshot(path='/tmp/kh_03_fecha.png')

    # Botón consultar
    boton = page.query_selector('#btnConsultarFecha')
    if boton:
        boton.click()
        # Esperar que la tabla tenga más de 2 filas (asegura que el AJAX cargó datos reales)
        try:
            page.wait_for_function(
                "document.querySelectorAll('table')[0].querySelectorAll('tbody tr').length > 2",
                timeout=15000,
            )
        except Exception:
            # Fallback: networkidle
            try:
                page.wait_for_load_state('networkidle', timeout=8000)
            except Exception:
                pass
        log.warning('[KH] Botón consultar clickeado')
    else:
        log.warning('[KH] Botón CONSULTAR no encontrado')

    page.screenshot(path='/tmp/kh_04_resultado.png')

    # Hay 2 tablas: [0] = comprobantes, [1] = pie de página
    todas_tablas = page.query_selector_all('table')
    log.warning('[KH] Tablas en página: %d', len(todas_tablas))
    tabla_datos = todas_tablas[0] if todas_tablas else None
    filas = tabla_datos.query_selector_all('tbody tr') if tabla_datos else []
    log.warning('[KH] Filas en tabla[0]: %d', len(filas))
    # Log primeras 3 filas para diagnóstico
    for i, fila in enumerate(filas[:3]):
        t = [c.inner_text().strip()[:20] for c in fila.query_selector_all('td')]
        log.warning('[KH] Fila[%d]: %s', i, t)
    comps = []
    for fila in filas:
        celdas = fila.query_selector_all('td')
        if len(celdas) < 4:
            continue
        t = [c.inner_text().strip() for c in celdas]

        # Link del Nº Comprobante (columna 3). El detalle NO se abre por href
        # (es '#') sino por el onclick verComprobanteEnPestaña(tipo,nro,fecha,cod)
        # → POST a /ctacte/DetalleComprobantes. Capturamos esos args para
        # navegar de verdad (ver _ir_a_detalle).
        link_el = celdas[3].query_selector('a') if len(celdas) > 3 else None
        href = link_el.get_attribute('href') if link_el else None
        onclick = link_el.get_attribute('onclick') if link_el else None
        detalle_args = None
        if onclick:
            m_oc = _RE_ONCLICK_DETALLE.search(onclick)
            if m_oc:
                detalle_args = m_oc.groups()   # (tipoDoc, numero, fecha, codUsuario)

        nro_kh = t[3] if len(t) > 3 else ''
        # Saltear filas de pie/encabezado (sin número de comprobante válido)
        if not _RE_NRO_KH.match(nro_kh.strip()):
            continue
        comp = {
            'fecha':          _parse_fecha(t[0]),
            'clase_doc':      t[1].upper() if len(t) > 1 else 'FAC',
            'nro_remito':     t[2] if len(t) > 2 else '',
            'nro_comp_kh':    nro_kh,
            'nro_comp_arca':  _kh_nro_a_arca(nro_kh),
            'monto_exento':   _parse_dec(t[4]) if len(t) > 4 else 0.0,
            'monto_gravado':  _parse_dec(t[5]) if len(t) > 5 else 0.0,
            'iva':            _parse_dec(t[6]) if len(t) > 6 else 0.0,
            'otros_imp':      _parse_dec(t[7]) if len(t) > 7 else 0.0,
            'percepcion_dgr': _parse_dec(t[8]) if len(t) > 8 else 0.0,
            'percepcion_mun': _parse_dec(t[9]) if len(t) > 9 else 0.0,
            'percepcion_iva': _parse_dec(t[10]) if len(t) > 10 else 0.0,
            'total':          _parse_dec(t[11]) if len(t) > 11 else 0.0,
            '_href':          href,
            '_detalle_args':  detalle_args,
        }
        comps.append(comp)
    return comps


# ── Detalle de un comprobante (clasificación + ítems) ─────────────────────────

def _ir_a_detalle(page, comp: dict, log) -> bool:
    """Abre la página de detalle del comprobante. Devuelve True si llegó.

    El portal NO usa una URL: el link hace onclick verComprobanteEnPestaña(...)
    que POSTea a /ctacte/DetalleComprobantes y renderiza el detalle en la misma
    página (SPA). Llamamos a esa función del sitio con los args capturados del
    listado. Funciona en cadena (de un detalle al siguiente) sin volver al
    listado. El detalle está listo cuando aparece el botón 'Generar PDF'.
    """
    args = comp.get('_detalle_args')
    nro = comp.get('nro_comp_kh', '?')
    if not args:
        log.warning('[KH-DET] %s: sin args de detalle (onclick no capturado)', nro)
        return False
    try:
        page.evaluate(
            "([t, n, f, c]) => verComprobanteEnPestaña(t, n, f, c)", list(args))
        page.wait_for_selector('button[onclick*="generarPDF"]', timeout=20000)
        return True
    except Exception as e:
        log.warning('[KH-DET] %s: no cargó el detalle: %s', nro, str(e)[:80])
        return False


def _detalle_comprobante(page, comp: dict) -> dict:
    """Navega al detalle y extrae categoría + ítems + faltantes.

    Ítems: de la TABLA HTML del detalle (limpia y consistente FAC/NCR). El PDF
    se usa solo para clasificar `nc_financiera` y sacar faltantes (el analizador
    regex ya validado); su parseo de ítems queda como fallback porque el texto
    del PDF es inconsistente (columnas WEB/DTO/ref que ensucian el renglón).
    """
    import logging
    log = logging.getLogger(__name__)
    nro = comp.get('nro_comp_kh', '?')

    if not _ir_a_detalle(page, comp, log):
        return {'categoria': 'factura', 'items': [], 'faltantes': []}

    # Vencimiento de pago + TRF del header del detalle (best-effort, sin bajar el
    # PDF: la página ya está cargada). El detector ancla en 'COND. DE PAGO', así
    # que no toma los otros 'Vto.' del comprobante (renglón TRF ni CAEA).
    venc_trf = {}
    try:
        from helpers import _normalize_quadrupled, detectar_vencimiento_trf
        _txt = _normalize_quadrupled(page.inner_text('body'))
        venc_trf = detectar_vencimiento_trf(_txt, fecha_factura=comp.get('fecha'))
    except Exception:  # noqa: BLE001 — el vencimiento es opcional, no frenar el sync
        venc_trf = {}

    items_html = _detalle_via_html(page, comp, log)

    # Caso común (factura con mercadería): la tabla HTML ya trae los ítems, no
    # hace falta bajar el PDF. Se saltea → el sync es órdenes de magnitud más
    # rápido (un PDF por comprobante era el cuello de botella).
    # (TODO: faltantes '*** PRODUCTOS EN FALTA ***' quedan sin capturar en este
    #  camino; nunca funcionaron en prod porque la navegación estaba rota. Se
    #  agregan cuando tengamos una muestra HTML de una factura con faltantes.)
    if items_html:
        # Completar dto (la tabla HTML no lo trae) + vto/TRF desde el PDF, solo
        # si falta. Aditivo: no cambia los ítems, solo rellena.
        venc_trf = _enriquecer_con_pdf(page, comp, items_html, venc_trf, log)
        return {'categoria': 'factura', 'items': items_html, 'faltantes': [], **venc_trf}

    # Sin ítems en el HTML → candidato a NC financiera (recupero, un solo
    # renglón sin barcode). Ahí sí bajamos el PDF para clasificar con el
    # analizador (nc_financiera + anunciante) o sacar faltantes/ítems de un
    # layout raro como último recurso.
    analisis_pdf = _detalle_via_pdf(page, comp, log) or {}
    if analisis_pdf.get('categoria') == 'nc_financiera':
        return analisis_pdf
    items = analisis_pdf.get('items') or []
    if not items:
        log.warning('[KH-DET] %s: sin ítems (ni HTML ni PDF)', nro)
    return {
        'categoria': 'factura',
        'items': items,
        'faltantes': analisis_pdf.get('faltantes') or [],
        **venc_trf,
    }


# ── Conversión de número de comprobante ───────────────────────────────────────

_RE_NRO_KH = re.compile(r'^(\d+)[A-Za-z]+(\d+)$')

# onclick del link de comprobante: verComprobanteEnPestaña('DG','0046A00061895',
# '2026-08-19','1000002873'). El nombre lleva ñ; \w+ la cubre sin depender del
# encoding del atributo.
_RE_ONCLICK_DETALLE = re.compile(
    r"verComprobanteEnPesta\w+\('([^']*)',\s*'([^']*)',\s*'([^']*)',\s*'([^']*)'\)")

def _kh_nro_a_arca(nro: str) -> str:
    """
    '0046A00061895' → '00046-00061895'
    '0046A246995'   → '00046-00246995'  (normaliza a 8 dígitos)
    """
    nro = nro.strip()
    m = _RE_NRO_KH.match(nro)
    if m:
        pto = m.group(1).lstrip('0') or '0'
        num = m.group(2).lstrip('0') or '0'
        return f"{pto.zfill(5)}-{num.zfill(8)}"
    # Fallback: si ya viene con guión o formato desconocido
    if '-' in nro:
        partes = nro.split('-', 1)
        return f"{partes[0].zfill(5)}-{partes[1].zfill(8)}"
    return nro


# ── Helpers numéricos ─────────────────────────────────────────────────────────

def _parse_fecha(s: str) -> date:
    s = s.strip()
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return date.today()


def _parse_int(s: str) -> int:
    try:
        return int(s.strip().replace('.', '').replace(',', ''))
    except (ValueError, AttributeError):
        return 0


def _parse_dec(s: str) -> float:
    """Números del portal Kellerhoff: formato US (coma=miles, punto=decimal).
    Ej: '$230,261.36' → 230261.36
    """
    try:
        s = s.strip().replace('\xa0', '').replace(' ', '').replace('$', '')
        s = s.replace(',', '')   # quitar separador de miles
        return float(s) if s else 0.0
    except (ValueError, AttributeError):
        return 0.0


def _parse_dec_ar(s: str) -> float:
    """Números de la TABLA DE DETALLE (formato argentino: punto=miles, coma=decimal).
    Ej: '22.814,37' → 22814.37. Distinto al listado, que viene en formato US.
    """
    try:
        s = s.strip().replace('\xa0', '').replace(' ', '').replace('$', '')
        s = s.replace('.', '').replace(',', '.')
        return float(s) if s else 0.0
    except (ValueError, AttributeError):
        return 0.0


# ── Detalle via PDF + parser regex/clasificador ───────────────────────────────

def _detalle_via_pdf(page, comp: dict, log) -> dict | None:
    """Descarga el PDF del comprobante y lo clasifica/parsea con
    `services.kellerhoff_analizador` (regex, sin IA — ver CLAUDE.md).

    Devuelve None si no se pudo ni descargar el PDF (el caller cae al
    fallback HTML). Un PDF descargado que da 0 ítems reales SÍ devuelve dict
    (no None) — eso es una NC financiera o factura vacía real, no una falla.
    """
    import os
    import tempfile

    from services import kellerhoff_analizador

    nro = comp.get('nro_comp_kh', '?')

    pdf_btn = (
        page.query_selector('a[onclick*="generarPDF"]') or
        page.query_selector('a.btn_download') or
        page.query_selector('button[onclick*="PDF"]')
    )
    if not pdf_btn:
        log.warning('[KH-PDF] %s: botón PDF no encontrado en %s', nro, page.url)
        return None

    pdf_path = os.path.join(tempfile.gettempdir(), f'kh_{nro}.pdf')
    try:
        with page.expect_download(timeout=30000) as dl_info:
            pdf_btn.click()
        dl_info.value.save_as(pdf_path)
        log.warning('[KH-PDF] %s: PDF descargado (%d bytes)', nro, os.path.getsize(pdf_path))
    except Exception as e:
        log.warning('[KH-PDF] %s: error descargando PDF: %s', nro, e)
        return None

    try:
        analisis = kellerhoff_analizador.analizar_pdf(pdf_path)
        if analisis['categoria'] == 'nc_financiera':
            log.warning('[KH-PDF] %s: NC financiera → %s', nro, analisis['anunciante_nombre'])
        else:
            log.warning('[KH-PDF] %s: factura → %d ítem(s), %d faltante(s)',
                        nro, len(analisis['items']), len(analisis['faltantes']))
        return analisis
    except Exception as e:
        log.warning('[KH-PDF] %s: error parseando PDF: %s', nro, e)
        return None
    finally:
        try:
            os.unlink(pdf_path)
        except OSError:
            pass


# ── Enriquecer desde el PDF lo que el HTML no trae (dto, vto, TRF) ────────────

def _bajar_pdf_texto(page, comp: dict, log) -> str | None:
    """Baja el PDF del comprobante y devuelve su texto normalizado, o None.

    Para completar dto/vencimiento/TRF que la tabla HTML del portal no trae. No
    frena el sync: ante cualquier error devuelve None y el caller sigue con lo
    que tenga. (Descarga propia, no toca `_detalle_via_pdf`, que sigue igual.)
    """
    import os
    import tempfile

    from services.kellerhoff_analizador import leer_texto_pdf
    nro = comp.get('nro_comp_kh', '?')
    pdf_btn = (
        page.query_selector('a[onclick*="generarPDF"]') or
        page.query_selector('a.btn_download') or
        page.query_selector('button[onclick*="PDF"]')
    )
    if not pdf_btn:
        return None
    pdf_path = os.path.join(tempfile.gettempdir(), f'kh_enr_{nro}.pdf')
    try:
        with page.expect_download(timeout=30000) as dl_info:
            pdf_btn.click()
        dl_info.value.save_as(pdf_path)
        return leer_texto_pdf(pdf_path)
    except Exception as e:  # noqa: BLE001 — enriquecer es best-effort
        log.warning('[KH-PDF-ENR] %s: no se pudo bajar el PDF: %s', nro, str(e)[:80])
        return None
    finally:
        try:
            os.unlink(pdf_path)
        except OSError:
            pass


def _enriquecer_con_pdf(page, comp: dict, items: list[dict], venc_trf: dict, log) -> dict:
    """Completa dto por barcode + vencimiento/TRF desde el PDF.

    ADITIVO: no cambia los ítems ni su cantidad — solo rellena `dto_pct` que
    venía None (la tabla HTML nunca lo trae) y vto/TRF vacíos. Baja el PDF SOLO
    si falta alguno de esos datos. Devuelve el venc_trf (posiblemente completado).
    Nunca rompe: si el PDF no baja o no matchea, queda todo como estaba.
    """
    nro = comp.get('nro_comp_kh', '?')
    falta_dto = any(i.get('dto_pct') is None for i in items)
    falta_vt = not venc_trf.get('vencimiento') and not venc_trf.get('trf')
    if not (falta_dto or falta_vt):
        return venc_trf
    texto = _bajar_pdf_texto(page, comp, log)
    if not texto:
        return venc_trf
    try:
        from helpers import detectar_vencimiento_trf
        from services.kellerhoff_analizador import dto_por_barcode
        if falta_dto:
            dmap = dto_por_barcode(texto)
            n = 0
            for it in items:
                if it.get('dto_pct') is None and it.get('barcode') in dmap:
                    it['dto_pct'] = dmap[it['barcode']]
                    n += 1
            log.warning('[KH-PDF-ENR] %s: dto completado en %d/%d ítem(s)',
                        nro, n, len(items))
        if falta_vt:
            vt = detectar_vencimiento_trf(texto, fecha_factura=comp.get('fecha'))
            venc_trf = {**venc_trf, **{k: v for k, v in vt.items() if v}}
            log.warning('[KH-PDF-ENR] %s: vto=%s trf=%s',
                        nro, venc_trf.get('vencimiento'), venc_trf.get('trf'))
    except Exception as e:  # noqa: BLE001 — best-effort
        log.warning('[KH-PDF-ENR] %s: error enriqueciendo: %s', nro, str(e)[:80])
    return venc_trf


# ── Detalle via HTML (fallback si no hay API key) ────────────────────────────

def _detalle_via_html(page, comp: dict, log) -> list[dict]:
    """Ítems desde la tabla HTML del detalle. Fuente PRIMARIA de ítems.

    Columnas reales (idénticas en FAC y NCR, verificado contra el portal):
        BARCODE | DESCRIPCIÓN | CANT | PRECIO_PÚB | PRECIO_UNIT | IMPORTE
    Números en formato argentino ('22.814,37'). Sin columna de DTO (a diferencia
    del texto del PDF, que además trae tokens WEB/ref que ensucian el renglón —
    por eso preferimos esta tabla). Se identifica la tabla por tener el barcode
    (7-14 díg) en la primera celda, no por índice (el índice varía).
    """
    import re as _re
    nro = comp.get('nro_comp_kh', '?')
    _RE_BC = _re.compile(r'^\d{7,14}$')
    for ti, tabla in enumerate(page.query_selector_all('table')):
        items = []
        for fila in tabla.query_selector_all('tr'):
            celdas = fila.query_selector_all('td')
            if len(celdas) < 6:
                continue
            t = [c.inner_text().strip() for c in celdas]
            if not _RE_BC.match(t[0]):
                continue
            # desc = todo entre barcode y los 4 últimos campos (cant, pub, unit, imp).
            desc = ' '.join(p for p in t[1:len(t) - 4]
                            if p not in ('WEB', 'TRZ', '')).strip()
            items.append({
                'barcode':         t[0],
                'descripcion':     desc,
                'cantidad':        _parse_int(t[-4]),
                'precio_pub':      _parse_dec_ar(t[-3]),
                'dto_pct':         None,   # la tabla HTML no trae DTO
                'precio_unitario': _parse_dec_ar(t[-2]),
                'importe':         _parse_dec_ar(t[-1]),
            })
        if items:
            log.warning('[KH-HTML] %s: tabla[%d] → %d ítem(s)', nro, ti, len(items))
            return items
    log.warning('[KH-HTML] %s: sin ítems en ninguna tabla', nro)
    return []
