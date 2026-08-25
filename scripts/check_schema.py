# -*- coding: utf-8 -*-
"""Compara los modelos SQLAlchemy contra el schema REAL de la base.

Existe porque este bug ya pasó dos veces, y las dos se manifestó como un 500 en
producción y no antes:

  · `resumen_proveedor.total_calculado` — la columna se agregó al modelo en un PR
    posterior al que creó la tabla. Como la tabla ya existía en producción,
    `create_all` no la tocó y `/kellerhoff/resumenes` reventaba con
    "column ... does not exist".
  · `rowa_nuevos.obs_verificado_en` — idéntico.

Los tests NO lo detectan: arman la base con `create_all` sobre SQLite vacío, así
que el modelo y el schema coinciden siempre. La única forma de verlo es mirar una
base que ya venía de antes.

**Toda columna agregada a una tabla YA DESPLEGADA necesita su
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` en `init_db()`**, aunque el modelo la
declare.

Uso:
    python scripts/check_schema.py          # usa DATABASE_URL del entorno
    python scripts/check_schema.py <url>

Sale con código 1 si falta algo, para poder usarlo en un deploy.
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

import database  # noqa: E402


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('DATABASE_URL')
    if not url:
        print('Falta DATABASE_URL (o pasala como argumento).')
        return 2

    from sqlalchemy import inspect

    database.init_engine(url)
    insp = inspect(database.engine)
    tablas_reales = set(insp.get_table_names())

    faltan_tablas, faltan_columnas = [], []
    for nombre, tabla in database.Base.metadata.tables.items():
        if nombre not in tablas_reales:
            faltan_tablas.append(nombre)
            continue
        reales = {c['name'] for c in insp.get_columns(nombre)}
        dif = {c.name for c in tabla.columns} - reales
        if dif:
            faltan_columnas.append((nombre, sorted(dif)))

    print('Modelos: %d tablas · Base: %d tablas'
          % (len(database.Base.metadata.tables), len(tablas_reales)))
    print()

    if faltan_tablas:
        print('TABLAS que el modelo declara y NO existen en la base: %d'
              % len(faltan_tablas))
        for t in faltan_tablas:
            print('   %s' % t)
        print()

    if faltan_columnas:
        print('COLUMNAS faltantes: %d tabla(s)' % len(faltan_columnas))
        for t, cols in faltan_columnas:
            print('   %s -> %s' % (t, ', '.join(cols)))
        print()
        print('Cada una necesita su ALTER TABLE ... ADD COLUMN IF NOT EXISTS')
        print('en init_db() (ver las migraciones inline en database.py).')

    if not faltan_tablas and not faltan_columnas:
        print('OK — el schema real coincide con los modelos.')
        return 0
    return 1


if __name__ == '__main__':
    sys.exit(main())
