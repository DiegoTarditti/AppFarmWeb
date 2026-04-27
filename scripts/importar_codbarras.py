"""Importa códigos de barra (EAN) desde el dump SQL Server `codbarras.txt`.

Origen: dump manual de `dbo.IdProductoCodigosBarras` de ObServer (esa tabla
NO está expuesta en el schema DW). Resuelve el problema histórico de no
tener EAN real para los productos del catálogo Observer.

Formato esperado del .txt:
    IdProductoCodigoBarras IdProducto CodigoBarras Orden FechaIngreso AK_Datos_ID FW_FechaBaja CT_Version TS_Edicion
    ---------------------- ----------- ------------- ----- ... (separador)
    73563  101  7795349010751  1  NULL  1  2015-09-07 06:30:14.000  ...
    ...

Uso:
    docker exec appfarmweb-web-1 sh -c "cd /app && python -m scripts.importar_codbarras /app/uploads/codbarras.txt"

Idempotente: TRUNCATE + bulk insert. ~131k filas → ~30s.
"""
import sys
from datetime import datetime

import database
from database import ObsCodigoBarras, ObsProducto, get_db, init_db, now_ar


def parse_fecha(s):
    s = s.strip()
    if not s or s == 'NULL':
        return None
    # Formato: 2015-09-07 06:30:14.000
    try:
        return datetime.strptime(s.split('.')[0], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


def parse_int(s):
    s = s.strip()
    if not s or s == 'NULL':
        return None
    try:
        return int(s)
    except ValueError:
        return None


def parse_archivo(path):
    """Generador que devuelve dicts {id_codigo_barras, producto_observer,
    codigo_barras, orden, fecha_ingreso, fecha_baja}."""
    with open(path, encoding='utf-8-sig') as f:
        for i, line in enumerate(f, 1):
            line = line.rstrip('\r\n')
            if not line.strip():
                continue
            # Saltear header (primeras 2 líneas) y mensaje final "(N filas afectadas)"
            if i <= 2 or '(' in line and 'filas afectadas' in line:
                continue
            # Las columnas vienen separadas por whitespace múltiple. Pero las
            # fechas tienen un espacio interno (YYYY-MM-DD HH:MM:SS.mmm) y los
            # NULL vienen sueltos. Hay que ser cuidadoso con el split.
            # Estrategia: tomar las primeras 4 columnas por split simple,
            # después el resto se procesa por posiciones fijas.
            partes = line.split()
            if len(partes) < 4:
                continue
            try:
                id_cb = int(partes[0])
                id_prod = int(partes[1])
                ean = partes[2]
                orden = int(partes[3])
            except (ValueError, IndexError):
                continue
            # Reconstruir el resto y buscar fechas. FechaIngreso es partes[4..5]
            # si no es NULL (formato "YYYY-MM-DD HH:MM:SS.mmm").
            # FW_FechaBaja viene 3 campos después.
            idx = 4
            fecha_ingreso = None
            if idx < len(partes):
                if partes[idx] == 'NULL':
                    idx += 1
                else:
                    # Probable fecha: combinar 2 tokens
                    fecha_ingreso = parse_fecha(partes[idx] + ' ' + partes[idx+1] if idx+1 < len(partes) else partes[idx])
                    idx += 2
            # AK_Datos_ID
            idx += 1
            fecha_baja = None
            if idx < len(partes):
                if partes[idx] == 'NULL':
                    idx += 1
                else:
                    fecha_baja = parse_fecha(partes[idx] + ' ' + partes[idx+1] if idx+1 < len(partes) else partes[idx])
                    idx += 2

            yield {
                'id_codigo_barras':  id_cb,
                'producto_observer': id_prod,
                'codigo_barras':     ean,
                'orden':             orden,
                'fecha_ingreso':     fecha_ingreso,
                'fecha_baja':        fecha_baja,
            }


def importar(path):
    init_db()
    print(f'Importando códigos de barra desde {path}...')

    with get_db() as session:
        # Sets de obs_productos válidos para skipear FK rotas
        productos_validos = {i for (i,) in session.query(ObsProducto.observer_id).all()}
        print(f'  obs_productos válidos: {len(productos_validos):,}')

        # TRUNCATE para idempotencia
        from sqlalchemy import text
        session.execute(text('TRUNCATE TABLE obs_codigos_barras'))
        session.commit()
        print('  Tabla obs_codigos_barras limpiada')

        # Bulk insert por chunks
        chunk = []
        n_total = n_skip_fk = n_baja = 0
        sync_en = now_ar()
        for r in parse_archivo(path):
            if r['producto_observer'] not in productos_validos:
                n_skip_fk += 1
                continue
            r['sync_en'] = sync_en
            chunk.append(r)
            if r['fecha_baja']:
                n_baja += 1
            if len(chunk) >= 5000:
                session.bulk_insert_mappings(ObsCodigoBarras, chunk)
                session.commit()
                n_total += len(chunk)
                print(f'  ... {n_total:,} insertados')
                chunk = []
        if chunk:
            session.bulk_insert_mappings(ObsCodigoBarras, chunk)
            session.commit()
            n_total += len(chunk)

        print(f'\n✅ Total importado: {n_total:,} EANs ({n_baja:,} con fecha_baja)')
        print(f'   Skipped por FK rota: {n_skip_fk:,}')

        # Verificación
        n_productos_con_ean = session.query(ObsCodigoBarras.producto_observer).distinct().count()
        print(f'   {n_productos_con_ean:,} productos tienen al menos 1 EAN')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Uso: python -m scripts.importar_codbarras <ruta-al-txt>')
        sys.exit(1)
    importar(sys.argv[1])
