"""Analiza las 124 entradas actuales de modulo_packs para ver qué tan bien
identificaríamos los packs automáticamente desde la descripción.

Reporta:
- Aciertos (descripción matchea patrón de pack)
- Falsos negativos (está en modulo_packs pero la descripción no grita "pack")
- Cantidad detectada vs cantidad guardada
"""
import os
import re
import sys
import psycopg2

# Fuerza stdout UTF-8 para Windows
if hasattr(sys.stdout, 'reconfigure'):
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass


# Patrones de "esto es un pack" — con grupo de captura para la cantidad
PATTERNS = [
    re.compile(r'\bPACK\s*X\s*(\d+)\b', re.I),          # "PACK X 10"
    re.compile(r'\bX\s*(\d+)\s*EST(?:UCHE?S?)?\b', re.I),  # "X 10 EST" / "X 10 ESTUCHES"
    re.compile(r'\bC\s*X\s*(\d+)\b', re.I),              # "C X 10" / "CX10" (caja por N)
    re.compile(r'\bPACK\s*X\s*EST\b', re.I),             # "PACK X EST" (sin número — marcar como pack pero sin cantidad)
]


def detectar_pack(descripcion):
    """Devuelve (es_pack: bool, cantidad_detectada: int|None, patron_matcheado: str|None)."""
    if not descripcion:
        return False, None, None
    for i, pat in enumerate(PATTERNS):
        m = pat.search(descripcion)
        if m:
            # Si el grupo 1 captura un número lo tomamos, sino cantidad = None
            cant = None
            if m.groups() and m.group(1) and m.group(1).isdigit():
                cant = int(m.group(1))
            return True, cant, pat.pattern
    return False, None, None


def main():
    url = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5433/farmacia')
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    conn = psycopg2.connect(url)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ean_pack, ean_unidad, cantidad, descripcion
            FROM modulo_packs
            ORDER BY descripcion
        """)
        rows = cur.fetchall()

    aciertos = []
    cant_match = 0
    cant_mismatch = []
    falsos_negativos = []

    for ean_pack, ean_unidad, cant_guardada, desc in rows:
        es_pack, cant_detectada, patron = detectar_pack(desc or '')
        if es_pack:
            aciertos.append((desc, cant_guardada, cant_detectada, patron))
            if cant_detectada == cant_guardada:
                cant_match += 1
            elif cant_detectada is not None:
                cant_mismatch.append((desc, cant_guardada, cant_detectada))
        else:
            falsos_negativos.append((desc, cant_guardada))

    total = len(rows)
    print(f"Total de entradas en modulo_packs: {total}\n")

    print(f"✓ Aciertos: {len(aciertos)}/{total} ({100*len(aciertos)/total:.0f}%)")
    print(f"  · Cantidad detectada igual a la guardada: {cant_match}")
    if cant_mismatch:
        print(f"  · Cantidad detectada distinta ({len(cant_mismatch)}):")
        for d, g, det in cant_mismatch[:10]:
            print(f"      guardado={g} detectado={det} | {d}")

    print(f"\n✗ Falsos negativos ({len(falsos_negativos)}): descripciones que NO matchean ningún patrón")
    for d, g in falsos_negativos[:30]:
        print(f"      [cant_guardada={g}] {d}")
    if len(falsos_negativos) > 30:
        print(f"      ... +{len(falsos_negativos)-30} más")

    print(f"\nResumen por patrón:")
    por_patron = {}
    for d, g, det, pat in aciertos:
        por_patron.setdefault(pat, 0)
        por_patron[pat] += 1
    for pat, n in sorted(por_patron.items(), key=lambda x: -x[1]):
        print(f"  {n:>4}  {pat}")


if __name__ == '__main__':
    main()
