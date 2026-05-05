"""Bridge masivo: vincular productos ↔ obs_productos (Fase 2 migración EANs).

Para cada `productos` sin `observer_id`, intenta vincularlo a un `obs_productos`
por dos estrategias:

  A) EAN match — cualquier EAN en `producto_codigos_barra` (1-a-N) o en
     `productos.codigo_barra/alt1/2/3` (legacy) que coincida con cualquier
     EAN en `obs_codigos_barras` (con `fecha_baja IS NULL`).
     Si un EAN matchea con UN ÚNICO obs_producto → vínculo seguro.
     Si matchea con varios → ambiguo, queda en `dudosos`.

  B) Codigo alfabeta — si `productos.codigo_alfabeta` == `obs_productos.codigo_alfabeta`
     y matchea con uno solo → vínculo seguro.

NO hace fuzzy/dimensional acá — eso es Fase 3 una vez `producto_atributos` esté
poblado en farmacia.

Uso:
    python -m scripts.bridge_productos_observer            # ejecuta
    python -m scripts.bridge_productos_observer --dry-run  # cuenta sin escribir

Idempotente: salta productos que ya tienen `observer_id` set.
"""
import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault('SECRET_KEY', 'bridge-productos-observer-not-used-for-http')


def ejecutar(dry_run: bool = False) -> dict:
    """Vincula productos a obs_productos. Devuelve stats.

    Returns:
        dict con keys: 'productos_total', 'ya_vinculados', 'vinculados_ean',
        'vinculados_alfabeta', 'dudosos', 'sin_match', 'ejemplos_dudosos',
        'dry_run'.
    """
    from sqlalchemy import or_

    from database import (
        ObsCodigoBarras,
        ObsProducto,
        Producto,
        ProductoCodigoBarra,
        get_db,
    )

    stats = {
        'productos_total': 0,
        'ya_vinculados': 0,
        'vinculados_ean': 0,
        'vinculados_alfabeta': 0,
        'dudosos': 0,
        'sin_match': 0,
        'ejemplos_dudosos': [],  # primeros 10 productos con match múltiple
        'dry_run': dry_run,
    }

    with get_db() as session:
        productos = session.query(Producto).all()
        stats['productos_total'] = len(productos)

        # Pre-cargar índice EAN → list[observer_id] desde obs_codigos_barras
        # (solo los activos, fecha_baja IS NULL).
        ean_to_obs = defaultdict(set)
        for ean, obs_id in (session.query(ObsCodigoBarras.codigo_barras,
                                          ObsCodigoBarras.producto_observer)
                            .filter(ObsCodigoBarras.fecha_baja.is_(None)).all()):
            if ean and obs_id:
                ean_to_obs[str(ean).strip()].add(int(obs_id))

        # Pre-cargar índice codigo_alfabeta → list[observer_id] desde obs_productos
        alf_to_obs = defaultdict(set)
        for obs_id, alf in (session.query(ObsProducto.observer_id,
                                          ObsProducto.codigo_alfabeta)
                            .filter(ObsProducto.codigo_alfabeta.isnot(None),
                                    ObsProducto.fecha_baja.is_(None)).all()):
            if alf:
                alf_to_obs[str(alf).strip()].add(int(obs_id))

        # Pre-cargar EANs locales 1-a-N por producto_id
        pcb_por_prod = defaultdict(set)
        for prod_id, ean in session.query(ProductoCodigoBarra.producto_id,
                                          ProductoCodigoBarra.codigo_barra).all():
            if ean:
                pcb_por_prod[prod_id].add(str(ean).strip())

        # Set de observer_ids ya tomados (para mantener UNIQUE)
        tomados = set()
        for (oid,) in session.query(Producto.observer_id).filter(
                Producto.observer_id.isnot(None)).all():
            tomados.add(int(oid))

        for p in productos:
            if p.observer_id is not None:
                stats['ya_vinculados'] += 1
                continue

            # Reunir todos los EANs candidatos del producto local: principal +
            # alts en producto_codigos_barra (1-a-N). Las columnas legacy
            # alt1/2/3 ya no se consultan.
            eans = set()
            if p.codigo_barra:
                eans.add(str(p.codigo_barra).strip())
            eans |= pcb_por_prod.get(p.id, set())
            eans = {e for e in eans if e}

            # Estrategia A: EAN match
            obs_candidatos = set()
            for ean in eans:
                obs_candidatos |= ean_to_obs.get(ean, set())
            # Filtrar los ya tomados por otro producto
            obs_candidatos_libres = {oid for oid in obs_candidatos if oid not in tomados}

            if len(obs_candidatos_libres) == 1:
                obs_id = next(iter(obs_candidatos_libres))
                if not dry_run:
                    p.observer_id = obs_id
                tomados.add(obs_id)
                stats['vinculados_ean'] += 1
                continue

            if len(obs_candidatos_libres) > 1:
                stats['dudosos'] += 1
                if len(stats['ejemplos_dudosos']) < 10:
                    stats['ejemplos_dudosos'].append({
                        'producto_id': p.id,
                        'producto_descripcion': p.descripcion,
                        'eans': list(eans)[:5],
                        'obs_candidatos': sorted(obs_candidatos_libres)[:5],
                        'estrategia': 'ean',
                    })
                continue

            # Estrategia B: codigo alfabeta (solo si A no encontró nada)
            if p.codigo_alfabeta:
                alf = str(p.codigo_alfabeta).strip()
                cands_alf = alf_to_obs.get(alf, set())
                cands_alf_libres = {oid for oid in cands_alf if oid not in tomados}

                if len(cands_alf_libres) == 1:
                    obs_id = next(iter(cands_alf_libres))
                    if not dry_run:
                        p.observer_id = obs_id
                    tomados.add(obs_id)
                    stats['vinculados_alfabeta'] += 1
                    continue

                if len(cands_alf_libres) > 1:
                    stats['dudosos'] += 1
                    if len(stats['ejemplos_dudosos']) < 10:
                        stats['ejemplos_dudosos'].append({
                            'producto_id': p.id,
                            'producto_descripcion': p.descripcion,
                            'codigo_alfabeta': alf,
                            'obs_candidatos': sorted(cands_alf_libres)[:5],
                            'estrategia': 'alfabeta',
                        })
                    continue

            stats['sin_match'] += 1

        if not dry_run:
            try:
                session.commit()
            except Exception as e:
                session.rollback()
                stats['errores'] = [f'commit fallo: {e}']

    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true',
                        help='Mostrar qué vincularía sin escribir.')
    args = parser.parse_args()

    from database import init_db
    db_url = os.environ.get('DATABASE_URL', 'sqlite:///farmacia.db')
    print(f'Conectando: {db_url[:50]}...')
    init_db(db_url)

    print(f'Bridge productos ↔ obs_productos ({"DRY-RUN" if args.dry_run else "EJECUTANDO"})')
    stats = ejecutar(dry_run=args.dry_run)
    print()
    print(f'  Productos totales:       {stats["productos_total"]:>8}')
    print(f'  Ya vinculados (skip):    {stats["ya_vinculados"]:>8}')
    print(f'  Vinculados por EAN:      {stats["vinculados_ean"]:>8}')
    print(f'  Vinculados por alfabeta: {stats["vinculados_alfabeta"]:>8}')
    print(f'  Dudosos (multi-match):   {stats["dudosos"]:>8}')
    print(f'  Sin match:               {stats["sin_match"]:>8}')
    if stats['ejemplos_dudosos']:
        print('\n  Primeros dudosos:')
        for d in stats['ejemplos_dudosos'][:5]:
            print(f'    · prod #{d["producto_id"]} "{d["producto_descripcion"][:50]}" '
                  f'→ obs candidatos: {d["obs_candidatos"]} ({d["estrategia"]})')


if __name__ == '__main__':
    main()
