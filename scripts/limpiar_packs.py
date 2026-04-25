"""Limpia la tabla modulo_packs:

1. Borra filas placeholder (cant=1 y ean_pack == ean_unidad) que se cargaron
   para 'todos' los productos de módulos sin ser packs reales.
2. Corrige las cantidades de los packs reales usando el regex de la descripción.

Uso:
    python scripts/limpiar_packs.py --dry-run   # muestra qué cambiaría
    python scripts/limpiar_packs.py             # aplica los cambios
"""
import os
import re
import sys

import psycopg2

if hasattr(sys.stdout, 'reconfigure'):
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


PATTERNS = [
    re.compile(r'\bPACK\s*X\s*(\d+)\b', re.I),
    re.compile(r'\bX\s*(\d+)\s*EST(?:UCHE?S?)?\b', re.I),
    re.compile(r'\bC\s*X\s*(\d+)\b', re.I),
]


def cantidad_desde_desc(desc):
    if not desc:
        return None
    for pat in PATTERNS:
        m = pat.search(desc)
        if m and m.groups() and m.group(1) and m.group(1).isdigit():
            return int(m.group(1))
    return None


def main():
    dry_run = '--dry-run' in sys.argv

    url = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5433/farmacia')
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)

    conn = psycopg2.connect(url)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            # 1. Identificar placeholders
            cur.execute("""
                SELECT id, ean_pack, ean_unidad, cantidad, descripcion
                FROM modulo_packs
                WHERE cantidad = 1 AND ean_pack = ean_unidad
            """)
            placeholders = cur.fetchall()
            print(f"\nPaso 1: {len(placeholders)} placeholders para borrar")
            print("  (cantidad=1 y ean_pack==ean_unidad)")
            for row in placeholders[:5]:
                print(f"  ej: #{row[0]} {row[4][:60] if row[4] else '(sin desc)'}")
            if len(placeholders) > 5:
                print(f"  ... +{len(placeholders) - 5} más")

            # 2. Corregir cantidades de packs reales
            cur.execute("""
                SELECT id, ean_pack, ean_unidad, cantidad, descripcion
                FROM modulo_packs
                WHERE NOT (cantidad = 1 AND ean_pack = ean_unidad)
            """)
            reales = cur.fetchall()
            correcciones = []
            for row_id, ep, eu, cant_actual, desc in reales:
                nueva = cantidad_desde_desc(desc)
                if nueva and nueva != cant_actual:
                    correcciones.append((row_id, cant_actual, nueva, desc))

            print(f"\nPaso 2: {len(correcciones)} cantidades a corregir")
            for row_id, a, n, d in correcciones:
                print(f"  #{row_id} {a} -> {n}  | {d[:60] if d else ''}")

            if dry_run:
                print("\n[DRY-RUN] No se aplicó nada. Corré sin --dry-run para aplicar.")
                return

            # APLICAR
            if placeholders:
                ids_borrar = [r[0] for r in placeholders]
                cur.execute("DELETE FROM modulo_packs WHERE id = ANY(%s)", (ids_borrar,))
                print(f"\n✓ Borradas {cur.rowcount} filas placeholder")

            for row_id, _, nueva, _ in correcciones:
                cur.execute("UPDATE modulo_packs SET cantidad = %s WHERE id = %s",
                            (nueva, row_id))
            print(f"✓ Actualizadas {len(correcciones)} cantidades")

            conn.commit()
            print("\nListo. Total restante en modulo_packs:")
            cur.execute("SELECT COUNT(*) FROM modulo_packs")
            print(f"  {cur.fetchone()[0]} entradas (antes 124)")

    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
