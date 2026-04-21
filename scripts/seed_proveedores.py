"""Seed de proveedores desde los parsers existentes en `parsers/*.py`.

Lee la razón social y CUIT de cada docstring (primeras líneas) y crea un Provider
en la DB si no existe. Si ya existe uno con el mismo CUIT, solo actualiza el parser_file.

Uso:
    python scripts/seed_proveedores.py           # dry-run (solo muestra)
    python scripts/seed_proveedores.py --ejecutar
"""

import os
import re
import sys
import argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import init_db, get_db, Provider


# Parsers auxiliares que NO son de proveedor (no crear Provider desde ellos)
PARSERS_AUXILIARES = {
    '_template', '__init__', 'sales_history', 'sales_history_xls', 'sales_history_html',
    'bernabo_ofertas', 'modulos_xlsx', 'ofertas_xlsx',
    'descuento_libre_parser', 'descuento_modulos_ocr', 'descuento_modulos_xls',
    'descuento_xlsx_parser', 'vademecum',
}


def _leer_info_parser(path):
    """Extrae razón social y CUIT del docstring del parser."""
    razon, cuit = None, None
    with open(path, 'r', encoding='utf-8') as fh:
        # Leer solo las primeras 20 líneas (suele estar ahí el docstring)
        head = ''.join(fh.readlines()[:20])
    m = re.search(r'Parser para:\s*(.+?)[\r\n]', head)
    if m:
        razon = m.group(1).strip()
    m = re.search(r'CUIT:\s*([\d\-\s]+)[\r\n]', head)
    if m:
        c = m.group(1).strip()
        # Normalizar a XX-XXXXXXXX-X
        digits = re.sub(r'\D', '', c)
        if len(digits) == 11:
            cuit = f'{digits[0:2]}-{digits[2:10]}-{digits[10]}'
        elif len(digits) > 0:
            cuit = c  # dejarlo como viene
    return razon, cuit


def _es_template_vacio(path):
    """True si el parser es todavía la plantilla base sin personalizar."""
    with open(path, 'r', encoding='utf-8') as fh:
        head = fh.read(500)
    return 'Generado automáticamente como plantilla base' in head


def _tamano(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def listar_parsers_proveedor():
    """Lista los archivos .py en parsers/ que son de proveedor.

    Deduplica por CUIT: si hay varios parsers para el mismo CUIT, elige el mejor
    (con CUIT definido > más grande > nombre alfabético).
    """
    parsers_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'parsers')
    candidatos = []
    for fname in sorted(os.listdir(parsers_dir)):
        if not fname.endswith('.py'):
            continue
        slug = fname[:-3]
        if slug in PARSERS_AUXILIARES:
            continue
        path = os.path.join(parsers_dir, fname)
        razon, cuit = _leer_info_parser(path)
        if not razon:
            continue
        if _es_template_vacio(path):
            continue  # descartar plantillas sin personalizar
        candidatos.append({
            'slug': slug, 'razon_social': razon, 'cuit': cuit,
            'path': path, 'tamano': _tamano(path),
        })

    # Deduplicar por CUIT (el primero que aparece ya es el mejor porque los ordenamos)
    por_cuit = {}
    sin_cuit = []
    for c in candidatos:
        if c['cuit']:
            prev = por_cuit.get(c['cuit'])
            # Quedarse con el más grande (más código = más desarrollado)
            if not prev or c['tamano'] > prev['tamano']:
                por_cuit[c['cuit']] = c
        else:
            sin_cuit.append(c)

    # Deduplicar los sin CUIT por razón social normalizada
    por_razon = {}
    for c in sin_cuit:
        key = (c['razon_social'] or '').strip().lower()
        prev = por_razon.get(key)
        if not prev or c['tamano'] > prev['tamano']:
            por_razon[key] = c

    return list(por_cuit.values()) + list(por_razon.values())


def seed_proveedores(ejecutar=False):
    """Ejecuta el seed y retorna un dict con el resumen para mostrar al usuario.

    Si ejecutar=False, solo calcula qué haría (dry-run).
    Retorna: {'parsers': [...], 'crear': [...], 'actualizar': [...], 'saltar': [...], 'aplicado': bool}
    """
    parsers = listar_parsers_proveedor()
    with get_db() as session:
        todos_provs = session.query(Provider).all()
        by_cuit = {p.cuit: p for p in todos_provs if p.cuit}
        by_razon = {(p.razon_social or '').lower(): p for p in todos_provs if p.razon_social}

        crear, actualizar, saltar = [], [], []
        for pp in parsers:
            prov = None
            if pp['cuit']:
                prov = by_cuit.get(pp['cuit'])
            if not prov:
                prov = by_razon.get(pp['razon_social'].lower())
            if prov:
                if prov.parser_file != pp['slug']:
                    actualizar.append({'id': prov.id, 'razon_actual': prov.razon_social,
                                       'parser_actual': prov.parser_file, 'slug': pp['slug'],
                                       'cuit': pp['cuit']})
                else:
                    saltar.append({'id': prov.id, 'razon': prov.razon_social})
            else:
                crear.append(pp)

        if ejecutar:
            for pp in crear:
                tipo = 'laboratorio' if 'LABORATORIO' in (pp['razon_social'] or '').upper() else 'drogueria'
                prov = Provider(
                    razon_social=pp['razon_social'][:100],
                    cuit=pp['cuit'],
                    parser_file=pp['slug'],
                    tipo=tipo,
                )
                session.add(prov)
            for item in actualizar:
                prov = session.get(Provider, item['id'])
                if prov:
                    prov.parser_file = item['slug']
                    if not prov.cuit and item['cuit']:
                        prov.cuit = item['cuit']
            session.commit()

        return {
            'parsers': parsers, 'crear': crear, 'actualizar': actualizar,
            'saltar': saltar, 'aplicado': ejecutar,
        }


def main():
    parser = argparse.ArgumentParser(description='Seed de proveedores desde parsers existentes')
    parser.add_argument('--ejecutar', action='store_true', help='Crea/actualiza de verdad')
    args = parser.parse_args()

    init_db()
    parsers = listar_parsers_proveedor()

    print(f'\n=== Parsers de proveedor encontrados: {len(parsers)} ===\n')
    for p in parsers:
        print(f'  [{p["slug"]:40}] razón={p["razon_social"]!r:45} cuit={p["cuit"] or "—"}')

    if not parsers:
        print('\nNo hay parsers de proveedor.')
        return

    with get_db() as session:
        todos_provs = session.query(Provider).all()
        by_cuit = {p.cuit: p for p in todos_provs if p.cuit}
        by_razon = {(p.razon_social or '').lower(): p for p in todos_provs if p.razon_social}

        crear, actualizar, saltar = [], [], []
        for pp in parsers:
            prov = None
            if pp['cuit']:
                prov = by_cuit.get(pp['cuit'])
            if not prov:
                prov = by_razon.get(pp['razon_social'].lower())
            if prov:
                if prov.parser_file != pp['slug']:
                    actualizar.append((prov, pp))
                else:
                    saltar.append((prov, pp))
            else:
                crear.append(pp)

        print(f'\n=== Resumen ===')
        print(f'  A crear:      {len(crear)}')
        print(f'  A actualizar: {len(actualizar)} (ya existen pero sin parser_file o diferente)')
        print(f'  Sin cambios:  {len(saltar)}')

        if crear:
            print(f'\n  Crear:')
            for pp in crear:
                print(f'    + {pp["razon_social"]!r} cuit={pp["cuit"] or "—"} → parser={pp["slug"]}')

        if actualizar:
            print(f'\n  Actualizar:')
            for prov, pp in actualizar:
                print(f'    ~ [{prov.id}] {prov.razon_social!r} parser: {prov.parser_file!r} → {pp["slug"]!r}')

        if not args.ejecutar:
            print('\n(dry-run, no se tocó nada. Agregá --ejecutar para aplicar)')
            return

        # Aplicar
        for pp in crear:
            tipo = 'laboratorio' if 'LABORATORIO' in (pp['razon_social'] or '').upper() else 'drogueria'
            prov = Provider(
                razon_social=pp['razon_social'][:100],
                cuit=pp['cuit'],
                parser_file=pp['slug'],
                tipo=tipo,
            )
            session.add(prov)
        for prov, pp in actualizar:
            prov.parser_file = pp['slug']
            if not prov.cuit and pp['cuit']:
                prov.cuit = pp['cuit']
        session.commit()
        print(f'\n✓ Aplicado: {len(crear)} creados, {len(actualizar)} actualizados.')


if __name__ == '__main__':
    main()
