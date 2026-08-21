"""
Test interactivo del scraper Kellerhoff.
Correr DENTRO del container:

    docker exec -it appfarmweb-web-1 python scripts/test_kh_scraper.py

Guarda screenshots en /tmp/kh_test_*.png.
"""
import os
import sys

sys.path.insert(0, '/app')

os.environ.setdefault('KELLERHOFF_USER', 'badiar')
os.environ.setdefault('KELLERHOFF_PASS', '3202')
os.environ.setdefault('KELLERHOFF_URL', 'https://www.kellerhoff.com.ar')

from datetime import date, timedelta

KH_URL  = os.environ['KELLERHOFF_URL'].rstrip('/')
KH_USER = os.environ['KELLERHOFF_USER']
KH_PASS = os.environ['KELLERHOFF_PASS']

def run():
    from playwright.sync_api import sync_playwright

    hasta = date.today()
    desde = hasta - timedelta(days=7)
    desde_str = desde.strftime('%d/%m/%Y')
    hasta_str  = hasta.strftime('%d/%m/%Y')
    rango = f'{desde_str} - {hasta_str}'

    print(f'[KH] Rango: {rango}')
    print(f'[KH] Usuario: {KH_USER}')

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context().new_page()

        # ── Login ──────────────────────────────────────────────────────────
        print('[KH] >> Login...')
        page.goto(f'{KH_URL}/Account/Login')
        page.wait_for_load_state('networkidle')
        page.screenshot(path='/tmp/kh_test_01_login.png')
        print(f'[KH]    URL: {page.url}')

        page.fill('#login_name', KH_USER)
        page.fill('#login_password', KH_PASS)
        page.click('#botonIniciarSesion')
        page.wait_for_load_state('networkidle')
        page.screenshot(path='/tmp/kh_test_02_post_login.png')
        print(f'[KH]    URL tras login: {page.url}  |  Título: {page.title()}')

        # ── Comprobantes ───────────────────────────────────────────────────
        print('[KH] >> Comprobantes...')
        page.goto(f'{KH_URL}/ctacte/ConsultaDeComprobantes')
        page.wait_for_load_state('networkidle')
        page.screenshot(path='/tmp/kh_test_03_comp.png')

        radio = page.query_selector('#radioFecha')
        print(f'[KH]    #radioFecha: {radio is not None}')
        if radio:
            radio.click()

        campo = page.query_selector('#ComprobanteFecha')
        print(f'[KH]    #ComprobanteFecha: {campo is not None}')
        if campo:
            page.evaluate(f"""
                (function() {{
                    var el = document.getElementById('ComprobanteFecha');
                    el.value = '{rango}';
                    ['input', 'change', 'apply.daterangepicker'].forEach(function(ev) {{
                        el.dispatchEvent(new Event(ev, {{bubbles: true}}));
                    }});
                    if (window.$ && $(el).data('daterangepicker')) {{
                        $(el).data('daterangepicker').setStartDate('{desde_str}');
                        $(el).data('daterangepicker').setEndDate('{hasta_str}');
                        $(el).val('{rango}');
                    }}
                }})();
            """)
            print(f'[KH]    Valor campo: {campo.input_value()!r}')

        boton = page.query_selector('#btnConsultarFecha')
        print(f'[KH]    #btnConsultarFecha: {boton is not None}')
        if boton:
            boton.click()
            try:
                page.wait_for_load_state('networkidle', timeout=10000)
            except Exception:
                pass

        page.screenshot(path='/tmp/kh_test_04_resultado.png')

        filas = page.query_selector_all('table tbody tr')
        print(f'[KH]    Filas en tabla: {len(filas)}')
        for i, fila in enumerate(filas[:5]):
            celdas = fila.query_selector_all('td')
            t = [c.inner_text().strip() for c in celdas]
            print(f'[KH]    Fila {i}: {t[:6]}')

        # ── HTML de la tabla (debug) ───────────────────────────────────────
        if not filas:
            tablas = page.query_selector_all('table')
            print(f'[KH]    Tablas en página: {len(tablas)}')
            if tablas:
                print(f'[KH]    HTML tabla[0] (800): {tablas[0].inner_html()[:800]}')
            else:
                # Buscar cualquier div con datos
                cuerpo = page.inner_html('body')
                print(f'[KH]    Body (1000): {cuerpo[:1000]}')

        browser.close()

    print('[KH] Screenshots guardadas en /tmp/kh_test_*.png')
    print('[KH] Para copiar al host:')
    print('  docker cp appfarmweb-web-1:/tmp/kh_test_01_login.png .')
    print('  docker cp appfarmweb-web-1:/tmp/kh_test_04_resultado.png .')

if __name__ == '__main__':
    run()
