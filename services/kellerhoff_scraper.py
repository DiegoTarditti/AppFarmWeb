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
                nro = comp.get('nro_comp_kh', '?')
                _cb(f'Detalle {i}/{len(nuevos)}: {nro}')
                try:
                    comp['items'] = _detalle_comprobante(page, comp)
                except Exception as e:
                    comp['items'] = []
                    comp['_error_detalle'] = str(e)
            # Comprobantes ya existentes: no re-navegar, items vacíos (ya están en DB)
            for comp in comps:
                if comp.get('nro_comp_arca') in _skip:
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

        # Link del Nº Comprobante (columna 3)
        link_el = celdas[3].query_selector('a') if len(celdas) > 3 else None
        href = link_el.get_attribute('href') if link_el else None

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
        }
        comps.append(comp)
    return comps


# ── Detalle de un comprobante (ítems) ─────────────────────────────────────────

def _detalle_comprobante(page, comp: dict) -> list[dict]:
    href = comp.get('_href')
    if not href:
        return []

    url = href if href.startswith('http') else f'{KH_URL}{href}'
    page.goto(url)
    page.wait_for_load_state('networkidle')

    import logging
    import os
    import re as _re
    log = logging.getLogger(__name__)
    nro = comp.get('nro_comp_kh', '?')

    items = _detalle_via_pdf(page, comp, log) or _detalle_via_html(page, comp, log)
    if not items:
        log.warning('[KH-DET] %s: sin ítems', nro)
    return items


# ── Conversión de número de comprobante ───────────────────────────────────────

_RE_NRO_KH = re.compile(r'^(\d+)[A-Za-z]+(\d+)$')

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


def _to_float(v) -> float:
    if v is None:
        return 0.0
    try:
        return float(str(v).replace(',', '.'))
    except (ValueError, TypeError):
        return 0.0


# ── Detalle via PDF + factura_ia ──────────────────────────────────────────────

def _detalle_via_pdf(page, comp: dict, log) -> list[dict]:
    """Descarga el PDF del comprobante del portal y lo parsea con factura_ia."""
    import os
    import tempfile

    from services import factura_ia

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        log.warning('[KH-PDF] ANTHROPIC_API_KEY no seteada — skip pdf')
        return []

    nro = comp.get('nro_comp_kh', '?')

    pdf_btn = (
        page.query_selector('a[onclick*="generarPDF"]') or
        page.query_selector('a.btn_download') or
        page.query_selector('button[onclick*="PDF"]')
    )
    if not pdf_btn:
        log.warning('[KH-PDF] %s: botón PDF no encontrado en %s', nro, page.url)
        return []

    pdf_path = os.path.join(tempfile.gettempdir(), f'kh_{nro}.pdf')
    try:
        with page.expect_download(timeout=30000) as dl_info:
            pdf_btn.click()
        dl_info.value.save_as(pdf_path)
        log.warning('[KH-PDF] %s: PDF descargado (%d bytes)', nro, os.path.getsize(pdf_path))
    except Exception as e:
        log.warning('[KH-PDF] %s: error descargando PDF: %s', nro, e)
        return []

    try:
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()
        data, usage = factura_ia.extraer_factura_json(pdf_bytes, api_key)
        items = []
        for it in (data.get('detalle') or []):
            qty = it.get('cantidad')
            try:
                qty = int(float(str(qty).replace(',', '.'))) if qty is not None else 0
            except (ValueError, TypeError):
                qty = 0
            items.append({
                'barcode':         str(it.get('codigo_barra') or '').strip(),
                'descripcion':     str(it.get('descripcion') or '').strip(),
                'cantidad':        qty,
                'precio_pub':      _to_float(it.get('precio_publico')),
                'dto_pct':         _to_float(it.get('dto_pct')),
                'precio_unitario': _to_float(it.get('precio_unitario')),
                'importe':         _to_float(it.get('importe')),
            })
        log.warning('[KH-PDF] %s: factura_ia → %d ítem(s) (input_tokens=%s)',
                    nro, len(items), getattr(usage, 'input_tokens', '?'))
        return items
    except Exception as e:
        log.warning('[KH-PDF] %s: error en factura_ia: %s', nro, e)
        return []
    finally:
        try:
            os.unlink(pdf_path)
        except OSError:
            pass


# ── Detalle via HTML (fallback si no hay API key) ────────────────────────────

def _detalle_via_html(page, comp: dict, log) -> list[dict]:
    """Extrae ítems de la tabla HTML del portal (fallback sin ANTHROPIC_API_KEY)."""
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
            desc_parts = t[2:len(t) - 4]
            desc = ' '.join(p for p in desc_parts if p not in ('WEB', 'TRZ', '')).strip()
            items.append({
                'barcode':         t[0],
                'descripcion':     desc,
                'cantidad':        _parse_int(t[1]),
                'precio_pub':      _parse_dec(t[-4]),
                'dto_pct':         _parse_dec(t[-3]),
                'precio_unitario': _parse_dec(t[-2]),
                'importe':         _parse_dec(t[-1]),
            })
        if items:
            log.warning('[KH-HTML] %s: tabla[%d] → %d ítem(s)', nro, ti, len(items))
            return items
    log.warning('[KH-HTML] %s: sin ítems en ninguna tabla', nro)
    return []
