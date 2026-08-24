"""Verificación EN VIVO del parser de facturas Kellerhoff (sin escribir en DB).

Corre el scraper real contra el portal, parsea cada comprobante con el analizador
(regex, sin IA) y muestra un resumen: cuántos ítems sacó cada uno y por qué vía.
Responde la única pregunta abierta del pendiente #2: ¿el parser extrae los ítems
contra el sitio real, o cae al fallback HTML que nunca encontró nada?

NO toca la base de datos: solo lee del portal y parsea en memoria.

Correr DENTRO del container:
    docker exec -it appfarmweb-web-1 python scripts/verificar_kh_parser.py [DIAS]

DIAS = ventana hacia atrás (default 7, máx 60).
"""
import logging
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, '/app')

# Credenciales: las del entorno, o el default conocido del test viejo.
os.environ.setdefault('KELLERHOFF_USER', 'badiar')
os.environ.setdefault('KELLERHOFF_PASS', '3202')
os.environ.setdefault('KELLERHOFF_URL', 'https://www.kellerhoff.com.ar')

logging.basicConfig(level=logging.WARNING, format='%(message)s')


def main():
    from services.kellerhoff_scraper import scrape_comprobantes

    dias = min(int(sys.argv[1]) if len(sys.argv) > 1 else 7, 60)
    hasta = date.today()
    desde = hasta - timedelta(days=dias)

    print('\n=== Verificación parser Kellerhoff (SIN escribir DB) ===')
    print(f'Rango: {desde} → {hasta}  ·  usuario: {os.environ["KELLERHOFF_USER"]}\n')

    def cb(msg):
        print(f'  · {msg}')

    # skip_nros vacío: queremos re-navegar TODO para probar el parser, aunque
    # ya esté en DB (este script no escribe, así que no importa duplicar).
    comprobantes = scrape_comprobantes(desde, hasta, cb, skip_nros=set())

    print(f'\n=== Resultado: {len(comprobantes)} comprobante(s) ===\n')
    print(f'{"NRO":<20} {"TIPO":<14} {"CATEGORIA":<14} {"ITEMS":>6} {"FALT":>5}  DETALLE')
    print('-' * 90)

    fact_con_items = fact_sin_items = nc_fin = con_error = 0
    for comp in comprobantes:
        analisis = comp.get('analisis') or {}
        cat = analisis.get('categoria', '?')
        items = analisis.get('items') or []
        falt = analisis.get('faltantes') or []
        nro = comp.get('nro_comp_arca') or comp.get('nro_comp_kh', '?')
        tipo = comp.get('clase_doc', '') or '?'
        err = comp.get('_error_detalle', '')

        detalle = ''
        if err:
            detalle = f'ERROR: {err[:40]}'
            con_error += 1
        elif cat == 'nc_financiera':
            detalle = f'anunciante: {analisis.get("anunciante_nombre", "?")}'
            nc_fin += 1
        elif cat == 'factura':
            if items:
                fact_con_items += 1
                # Muestra el primer ítem como sanity check del parseo.
                it0 = items[0]
                detalle = f'ej: {it0.get("barcode", "?")} {(it0.get("descripcion") or "")[:28]}'
            else:
                fact_sin_items += 1
                detalle = '⚠ 0 ítems (fallback HTML?)'

        print(f'{nro:<20} {tipo:<14} {cat:<14} {len(items):>6} {len(falt):>5}  {detalle}')

    print('-' * 90)
    print(f'\nFacturas con ítems : {fact_con_items}')
    print(f'Facturas SIN ítems : {fact_sin_items}   ← si >0, el parser falló en vivo')
    print(f'NC financieras     : {nc_fin}')
    print(f'Con error detalle  : {con_error}')
    print()
    if fact_sin_items == 0 and fact_con_items > 0:
        print('✅ El parser extrae ítems contra el portal real.')
    elif fact_con_items == 0 and (fact_sin_items or nc_fin):
        print('⚠ Ninguna factura trajo ítems — revisar _detalle_via_pdf / selector del botón PDF.')
    else:
        print('⚠ Resultado mixto — revisar las filas con 0 ítems.')


if __name__ == '__main__':
    main()
