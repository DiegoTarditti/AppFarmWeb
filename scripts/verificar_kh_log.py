"""Validación rápida (capada a N comprobantes) del parser + formato de log.

Replica el loop del sync pero solo sobre los primeros N comprobantes, imprimiendo
las mismas líneas que verá el log en vivo de la UI. Confirma que:
  - la navegación al detalle funciona (antes daba 0 ítems)
  - los ítems salen de la tabla HTML (barcode, desc, cant, precios)
  - el log por comprobante trae tipo + número + cantidad de ítems
Solo lee; no escribe DB.

    docker exec -it appfarmweb-web-1 python scripts/verificar_kh_log.py [N]
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, '/app')
os.environ.setdefault('KELLERHOFF_USER', 'badiar')
os.environ.setdefault('KELLERHOFF_PASS', '3202')
os.environ.setdefault('KELLERHOFF_URL', 'https://www.kellerhoff.com.ar')


def main():
    import logging
    logging.basicConfig(level=logging.ERROR)   # silenciar los [KH-*] warnings

    from playwright.sync_api import sync_playwright

    from services.kellerhoff_scraper import (
        _detalle_comprobante,
        _listar_comprobantes,
        _login,
    )

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5

    with sync_playwright() as p:
        page = p.chromium.launch(headless=True).new_context(accept_downloads=True).new_page()
        try:
            print('· Conectando al portal…')
            _login(page)
            print('· Login OK — descargando lista de comprobantes…')
            comps = _listar_comprobantes(page, date.today() - timedelta(days=7), date.today())
            print(f'· Lista: {len(comps)} comprobante(s). Parseando los primeros {n}:\n')

            for i, comp in enumerate(comps[:n], 1):
                nro = comp.get('nro_comp_arca') or comp.get('nro_comp_kh', '?')
                clase = comp.get('clase_doc', '?')
                a = _detalle_comprobante(page, comp)
                if a.get('categoria') == 'nc_financiera':
                    print(f'[{i}/{n}] {clase} {nro} — NC financiera → {a.get("anunciante_nombre") or "?"}')
                else:
                    items = a.get('items') or []
                    print(f'[{i}/{n}] {clase} {nro} → {len(items)} ítem(s)')
                    for it in items[:3]:
                        print(f'        {it.get("barcode",""):<15} '
                              f'{(it.get("descripcion") or "")[:34]:<34} '
                              f'x{it.get("cantidad","?"):<3} $unit {it.get("precio_unitario","?")}  '
                              f'$imp {it.get("importe","?")}')
        finally:
            page.context.browser.close()


if __name__ == '__main__':
    main()
