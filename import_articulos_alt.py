"""
Completa equivalencias de barcodes en `productos` usando un dump de
`articulos.txt` (export legacy de Observer).

⚠ DEPRECATED: este script existe por compatibilidad histórica. La fuente
correcta de equivalencias hoy es `obs_codigos_barras` que se sincroniza
desde `dbo.IdProductoCodigosBarras` de Observer (ver
`scripts/importar_codbarras.py` y la tabla 1-a-N `producto_codigos_barra`).

Se mantiene funcional para casos donde se necesite popular equivalencias
de un dump TXT manual sin acceso a Observer en vivo.

Las equivalencias se persisten en AMBOS lugares (legacy + 1-a-N) vía
`helpers._add_alt_barcode` para no romper compatibilidad mientras dura
la migración a la tabla 1-a-N.

Ejecutar dentro del contenedor:
    docker-compose exec web python import_articulos_alt.py
"""
import os
import sys
from collections import defaultdict

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

TXT = "/articulos/articulos.txt"   # path dentro del contenedor
if not os.path.exists(TXT):
    TXT = "C:/articulos/articulos.txt"
if not os.path.exists(TXT):
    sys.exit(f"No se encontró: {TXT}")

DB = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@db:5432/farmacia")

print("⚠ DEPRECATED: para sincronizar barcodes desde Observer en vivo,")
print("  preferí scripts/importar_codbarras.py que pobla obs_codigos_barras.")
print()

# ── 1. Parsear articulos.txt ──────────────────────────────────────────────────
print("Leyendo articulos.txt…")
grupos = defaultdict(list)   # {id_producto: [barcode, ...]} ordenados por Orden

with open(TXT, encoding="utf-8", errors="replace") as f:
    for i, line in enumerate(f):
        if i < 2:          # header + separador
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            id_prod = int(parts[1])
            barcode = str(int(parts[2]))
            orden = int(parts[3])
        except (ValueError, IndexError):
            continue
        grupos[id_prod].append((orden, barcode))

# Ordenar por Orden dentro de cada grupo
for k in grupos:
    grupos[k].sort()
    grupos[k] = [bc for _, bc in grupos[k]]

print(f"  IdProductos únicos: {len(grupos)}")
print(f"  Barcodes totales:   {sum(len(v) for v in grupos.values())}")

# ── 2. Cruzar contra productos vía SQLAlchemy + helpers ─────────────────────
import database
from database import ProductoCodigoBarra
from helpers import _add_alt_barcode, _find_producto

# Init connection (mismo que app)
engine = create_engine(DB)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
database.engine = engine
database.SessionLocal = SessionLocal

# Construir lookup inverso: barcode → id_producto Observer
bc_to_grupo = {}
for id_prod, barcodes in grupos.items():
    for bc in barcodes:
        bc_to_grupo[bc] = id_prod

updated = 0
skipped = 0
agregados_total = 0

session = SessionLocal()
try:
    productos = session.query(database.Producto).all()
    print(f"\nProductos en DB: {len(productos)}")
    print("Procesando…")

    # Pre-cargar barcodes ya asociados a cada producto desde la 1-a-N (única
    # fuente de verdad para alts; las columnas legacy alt1/2/3 ya no se usan).
    from collections import defaultdict
    barcodes_por_prod = defaultdict(set)
    for prod_id, ean in session.query(ProductoCodigoBarra.producto_id,
                                       ProductoCodigoBarra.codigo_barra).all():
        if ean:
            barcodes_por_prod[prod_id].add(ean)

    for prod in productos:
        # Buscar en qué grupo cae este producto: principal + barcodes ya
        # registrados en producto_codigos_barra contra el dump.
        id_prod = None
        candidatos = {prod.codigo_barra} | barcodes_por_prod.get(prod.id, set())
        candidatos.discard(None)
        candidatos.discard('')
        for bc_check in candidatos:
            if bc_check in bc_to_grupo:
                id_prod = bc_to_grupo[bc_check]
                break

        if id_prod is None:
            skipped += 1
            continue

        # Barcodes del grupo que aún NO están en el producto.
        existentes = candidatos
        nuevos = [b for b in grupos[id_prod] if b not in existentes]

        if not nuevos:
            skipped += 1
            continue

        # `_add_alt_barcode` escribe en producto_codigos_barra (1-a-N).
        for bc_nuevo in nuevos:
            _add_alt_barcode(session, prod.codigo_barra, bc_nuevo,
                             fuente='import_articulos_alt')
            agregados_total += 1
        updated += 1

    session.commit()
finally:
    session.close()

print(f"\n✔  Productos actualizados: {updated}")
print(f"   Equivalencias agregadas: {agregados_total}")
print(f"   Sin match:               {skipped}")
print()
print("Las equivalencias quedan en producto_codigos_barra (1-a-N).")
