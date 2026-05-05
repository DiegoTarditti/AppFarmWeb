"""Pre-popular `productos` (catálogo local) desde `obs_productos`.

Por cada ObsProducto activo (sin fecha_baja) crea un Producto local con:
  - codigo_barra: primer EAN no dado de baja en obs_codigos_barras (orden=1
    si existe, sino el primero). Si no hay EAN → 'OBS:<observer_id>'.
  - descripcion: ObsProducto.descripcion.
  - laboratorio_id: resuelto por Laboratorio.observer_id == ObsProducto.laboratorio_observer.
  - observer_id: ObsProducto.observer_id (vínculo directo, evita necesitar Bridge).
  - codigo_alfabeta: ObsProducto.codigo_alfabeta.

Idempotente: salta si ya existe un Producto con ese observer_id.

Uso:
    python scripts/popular_productos_desde_obs.py --dry-run
    python scripts/popular_productos_desde_obs.py
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault('SECRET_KEY', 'popular-productos-desde-obs')


def ejecutar(dry_run: bool = False, limit: int = None) -> dict:
    from database import (
        Laboratorio,
        ObsCodigoBarras,
        ObsProducto,
        Producto,
        get_db,
    )

    stats = {'evaluados': 0, 'ya_existen': 0, 'creados': 0, 'sin_ean': 0,
             'sin_lab': 0, 'errores': []}

    with get_db() as session:
        # Pre-load: observer_ids ya vinculados localmente.
        existentes_obs = set(
            r[0] for r in session.query(Producto.observer_id)
            .filter(Producto.observer_id.isnot(None)).all()
        )
        # Pre-load: codigo_barra ya en uso (para evitar UNIQUE violation).
        cb_en_uso = set(
            r[0] for r in session.query(Producto.codigo_barra).all()
        )
        # Pre-load: lab observer_id → Laboratorio.id local.
        lab_obs_to_local = dict(
            session.query(Laboratorio.observer_id, Laboratorio.id)
            .filter(Laboratorio.observer_id.isnot(None)).all()
        )
        # Pre-load: observer_id → primer EAN (orden ascendente).
        ean_por_obs = {}
        for obs_pid, ean in (session.query(ObsCodigoBarras.producto_observer,
                                           ObsCodigoBarras.codigo_barras)
                             .filter(ObsCodigoBarras.fecha_baja.is_(None))
                             .order_by(ObsCodigoBarras.producto_observer,
                                       ObsCodigoBarras.orden.asc()).all()):
            if obs_pid not in ean_por_obs and ean:
                ean_por_obs[obs_pid] = ean.strip()

        # Iterar ObsProducto activos.
        q = (session.query(ObsProducto)
             .filter(ObsProducto.fecha_baja.is_(None))
             .order_by(ObsProducto.observer_id))
        if limit:
            q = q.limit(limit)

        nuevos = []
        for op in q.all():
            stats['evaluados'] += 1
            if op.observer_id in existentes_obs:
                stats['ya_existen'] += 1
                continue
            ean = ean_por_obs.get(op.observer_id)
            if not ean:
                ean = f'OBS:{op.observer_id}'
                stats['sin_ean'] += 1
            # Si el EAN ya está en uso por otro producto (raro pero posible),
            # caemos al pseudo-EAN para no chocar.
            if ean in cb_en_uso:
                ean = f'OBS:{op.observer_id}'
            lab_local_id = lab_obs_to_local.get(op.laboratorio_observer)
            if not lab_local_id:
                stats['sin_lab'] += 1
            nuevos.append(Producto(
                codigo_barra=ean,
                descripcion=op.descripcion or '',
                laboratorio_id=lab_local_id,
                observer_id=op.observer_id,
                codigo_alfabeta=op.codigo_alfabeta,
            ))
            cb_en_uso.add(ean)
            stats['creados'] += 1

        if not dry_run and nuevos:
            try:
                # Bulk add en chunks de 1000 para no inflar la sesión.
                for i in range(0, len(nuevos), 1000):
                    session.add_all(nuevos[i:i+1000])
                    session.flush()
                session.commit()
            except Exception as e:
                session.rollback()
                stats['errores'].append(f'commit fallo: {e}')

    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true',
                        help='Mostrar qué crearía sin escribir.')
    parser.add_argument('--limit', type=int, default=None,
                        help='Limitar a N ObsProductos (testing).')
    args = parser.parse_args()

    from database import init_db
    db_url = os.environ.get('DATABASE_URL', 'sqlite:///farmacia.db')
    print(f'Conectando: {db_url[:50]}...')
    init_db(db_url)

    accion = 'DRY-RUN' if args.dry_run else 'EJECUTANDO'
    print(f'Pre-poblar productos desde obs_productos ({accion})')
    if args.limit:
        print(f'  Limitado a {args.limit} ObsProductos.')
    stats = ejecutar(dry_run=args.dry_run, limit=args.limit)
    print()
    print(f'  ObsProductos evaluados:   {stats["evaluados"]:>8}')
    print(f'  Ya tenían Producto local: {stats["ya_existen"]:>8}')
    print(f'  Productos {"a crear" if args.dry_run else "creados "}:        {stats["creados"]:>8}')
    print(f'  Sin EAN (pseudo OBS:N):   {stats["sin_ean"]:>8}')
    print(f'  Sin Laboratorio local:    {stats["sin_lab"]:>8}')
    if stats['errores']:
        print(f'  Errores: {stats["errores"]}')


if __name__ == '__main__':
    main()
