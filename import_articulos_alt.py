"""
Completa codigo_barra_alt1/2/3 en la tabla productos usando articulos.txt.
Ejecutar dentro del contenedor:
    docker-compose exec web python import_articulos_alt.py
"""
import os
import sys
from collections import defaultdict
from datetime import datetime

from sqlalchemy import create_engine, text

TXT = "/articulos/articulos.txt"   # path dentro del contenedor
# Si corrés desde fuera del contenedor, cambiá por la ruta local:
if not os.path.exists(TXT):
    TXT = "C:/articulos/articulos.txt"
if not os.path.exists(TXT):
    sys.exit(f"No se encontró: {TXT}")

DB = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@db:5432/farmacia")

# ── 1. Parsear articulos.txt ──────────────────────────────────────────────────
print("Leyendo articulos.txt…")
grupos = defaultdict(list)   # {id_producto: [barcode, ...]}  orden por Orden

with open(TXT, encoding="utf-8", errors="replace") as f:
    for i, line in enumerate(f):
        if i < 2:          # header + separador
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            id_prod  = int(parts[1])
            barcode  = str(int(parts[2]))
            orden    = int(parts[3])
        except (ValueError, IndexError):
            continue
        grupos[id_prod].append((orden, barcode))

# Ordenar por Orden dentro de cada grupo
for k in grupos:
    grupos[k].sort()
    grupos[k] = [bc for _, bc in grupos[k]]

print(f"  IdProductos únicos: {len(grupos)}")
print(f"  Barcodes totales:   {sum(len(v) for v in grupos.values())}")

# ── 2. Cruzar contra productos ────────────────────────────────────────────────
engine = create_engine(DB)

# Construir lookup inverso: barcode → id_producto
bc_to_grupo = {}
for id_prod, barcodes in grupos.items():
    for bc in barcodes:
        bc_to_grupo[bc] = id_prod

updated = 0
skipped = 0

with engine.begin() as conn:
    productos = conn.execute(text("""
        SELECT id, codigo_barra, codigo_barra_alt1, codigo_barra_alt2, codigo_barra_alt3
        FROM productos
    """)).fetchall()

    print(f"\nProductos en DB: {len(productos)}")
    print("Procesando…")

    for row in productos:
        pid, bc0, alt1, alt2, alt3 = row

        # Buscar en qué grupo cae este producto
        id_prod = None
        for bc_check in [bc0, alt1, alt2, alt3]:
            if bc_check and bc_check in bc_to_grupo:
                id_prod = bc_to_grupo[bc_check]
                break

        if id_prod is None:
            skipped += 1
            continue

        # Barcodes del grupo, excluyendo los que ya están en el producto
        existentes = {b for b in [bc0, alt1, alt2, alt3] if b}
        nuevos = [b for b in grupos[id_prod] if b not in existentes]

        if not nuevos:
            skipped += 1
            continue

        # Asignar a alt1/2/3 libres
        alts = [alt1, alt2, alt3]
        cambio = False
        for i, slot in enumerate(alts):
            if slot is None and nuevos:
                alts[i] = nuevos.pop(0)
                cambio = True

        if not cambio:
            skipped += 1
            continue

        conn.execute(text("""
            UPDATE productos
            SET codigo_barra_alt1 = :a1,
                codigo_barra_alt2 = :a2,
                codigo_barra_alt3 = :a3,
                actualizado_en    = :now
            WHERE id = :id
        """), {"a1": alts[0], "a2": alts[1], "a3": alts[2],
               "now": datetime.now(), "id": pid})
        updated += 1

print(f"\n✔  Actualizados: {updated}")
print(f"   Sin match:    {skipped}")
