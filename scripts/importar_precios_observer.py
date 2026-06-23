"""Importa precios exactos desde un dump de dbo.IdProductoPrecio (Observer).

Diego generó el dump desde SSMS como TXT con columnas separadas por espacios
(formato output de SQL Server Management Studio con result-to-text). Cada
producto tiene N registros históricos; el precio "vigente" es el de la fila
con max(FechaVigencia) por IdProducto (y FechaInhabilitacion IS NULL).

El script:
  1. Streamea el archivo (no carga 2.7GB en RAM).
  2. Por cada IdProducto agarra el registro con max(FechaVigencia).
  3. Updatea obs_productos.precio_lista, .precio_lista_fecha_vigencia,
     .precio_lista_actualizado_en para los IdProducto que existan localmente.
  4. Reporta resumen: N parseadas, N target encontrados, N actualizados,
     N que no existen en obs_productos (= no se sincronizaron desde DW.Productos).

Uso (desde DENTRO del container web):
    docker compose exec web python scripts/importar_precios_observer.py \\
      /ruta/al/precios.txt

Si no se pasa path, busca por default en /downloads/precios.txt
(útil si Diego monta un volumen para pasar el archivo).

Formato de cada línea del dump (ancho fijo, lo cortamos por tokens):
  IdProductoPrecio  IdProducto  FechaVigencia(2 tokens)  FechaIngreso(2)
  FechaIngresoEnDatos(2 o NULL)  FechaInhabilitacion(2 o NULL)
  IdTipoPrecio  CostoReposicion  Utilidad  AlicuotaIVA  Precio  ...

Tomamos: IdProducto (token 1), FechaVigencia (tokens 2+3), y el 4to decimal
de la línea (los primeros 3 decimales son CostoReposicion, Utilidad,
AlicuotaIVA — Precio es el siguiente).
"""
import io
import os
import re
import sys
from datetime import datetime

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)


def main(path):
    from sqlalchemy import text

    from database import get_db, init_engine

    init_engine(os.environ.get('DATABASE_URL', 'sqlite:///farmacia.db'))

    # 1) Parsear el dump y quedarnos con el precio vigente por IdProducto.
    print(f'Procesando: {path}')
    if not os.path.exists(path):
        print(f'❌ No existe el archivo: {path}')
        return 1

    # vigente[IdProducto] = (fecha_vigencia_dt, precio)
    vigente = {}
    cnt = 0
    parsed = 0
    print('Streameando... (esto tarda 30-60s con archivos de 3M+ líneas)')
    with io.open(path, encoding='utf-8-sig', errors='replace') as f:
        # Header + separator (2 líneas)
        try:
            next(f); next(f)
        except StopIteration:
            print('❌ Archivo vacío o sin header')
            return 1

        # Regex que ancla la extracción en IdTipoPrecio (1 letra mayúscula:
        # 'M', 'L', 'O', etc.) que viene después de FechaInhabilitacion (NULL
        # o datetime). Luego capturamos los 4 decimales que siguen, el último
        # es el Precio que buscamos.
        # FechaInhabilitacion puede ser 'NULL' o 'YYYY-MM-DD HH:MM:SS.fff'.
        precio_re = re.compile(
            r'(?:NULL|\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})\s+'
            r'[A-Z]\s+'                      # IdTipoPrecio
            r'\d+\.\d{2,4}\s+'              # CostoReposicion
            r'\d+\.\d{2,4}\s+'              # Utilidad
            r'\d+\.\d{2,4}\s+'              # AlicuotaIVA
            r'(\d+\.\d{2,4})'                # ← Precio (grupo 1)
        )
        for line in f:
            cnt += 1
            if cnt % 500_000 == 0:
                print(f'  {cnt:,} líneas, {parsed:,} registros parseados')
            parts = line.split(None, 4)
            if len(parts) < 4:
                continue
            try:
                id_producto = int(parts[1])
            except (ValueError, IndexError):
                continue
            fv_str = f'{parts[2]} {parts[3]}'[:19]
            try:
                fv = datetime.strptime(fv_str, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                continue
            m = precio_re.search(line)
            if not m:
                continue
            try:
                precio = float(m.group(1))
            except ValueError:
                continue
            parsed += 1
            prev = vigente.get(id_producto)
            if prev is None or fv > prev[0]:
                vigente[id_producto] = (fv, precio)

    print(f'\n✓ Líneas totales: {cnt:,}')
    print(f'✓ Registros parseados: {parsed:,}')
    print(f'✓ Productos únicos con precio: {len(vigente):,}')

    if not vigente:
        print('❌ No se extrajo ningún precio. Revisar formato del dump.')
        return 1

    # 2) Cruzar con obs_productos y updatear.
    print('\nCruzando con obs_productos local...')
    ahora = datetime.now().replace(microsecond=0)
    actualizados = 0
    sin_match = 0
    sin_cambio = 0

    with get_db() as s:
        # Traer todos los observer_id existentes (universo a updatear)
        existentes = {r[0] for r in s.execute(text(
            'SELECT observer_id FROM obs_productos'
        )).fetchall()}

        BATCH = 500
        items = list(vigente.items())
        for i in range(0, len(items), BATCH):
            chunk = items[i:i + BATCH]
            params = []
            for obs_id, (fv, precio) in chunk:
                if obs_id not in existentes:
                    sin_match += 1
                    continue
                params.append({
                    'oid': obs_id, 'precio': precio,
                    'fv': fv, 'ahora': ahora,
                })
            if not params:
                continue
            # Update sólo si el precio o la fecha de vigencia cambiaron, para
            # no marcar updates innecesarios cada vez que se reimporta.
            result = s.execute(text("""
                UPDATE obs_productos
                   SET precio_lista = :precio,
                       precio_lista_fecha_vigencia = :fv,
                       precio_lista_actualizado_en = :ahora
                 WHERE observer_id = :oid
                   AND (precio_lista IS DISTINCT FROM :precio
                        OR precio_lista_fecha_vigencia IS DISTINCT FROM :fv)
            """), params)
            # rowcount no siempre devuelve el total real con batch en
            # SQLAlchemy; el conteo lo hacemos con el "actualizados" del SUM.
            sin_cambio += len(params) - (result.rowcount if result.rowcount and result.rowcount > 0 else 0)
            actualizados += result.rowcount if result.rowcount and result.rowcount > 0 else 0
        s.commit()

    print(f'\n✓ Actualizados:   {actualizados:,}')
    print(f'• Sin cambio:     {sin_cambio:,} (mismo precio que ya estaba)')
    print(f'• Sin match local:{sin_match:,} (no están en obs_productos)')
    print('\nListo. El buscador en /atencion ya usa los precios nuevos.')
    return 0


if __name__ == '__main__':
    path_default = '/downloads/precios.txt'
    p = sys.argv[1] if len(sys.argv) > 1 else path_default
    sys.exit(main(p) or 0)
