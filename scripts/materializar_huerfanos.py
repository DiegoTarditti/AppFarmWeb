"""Materializa productos huérfanos contra el maestro ObServer.

Recorre ``productos WHERE observer_id IS NULL`` y, para cada uno, busca su
EAN en ``obs_codigos_barras``. Si lo encuentra vigente:
  - linkea ``observer_id``
  - setea ``fuente_creacion = 'materializar_obs'``
  - sincroniza ``descripcion`` y ``codigo_alfabeta`` desde ``obs_productos``
  - resuelve/crea el ``laboratorio_id`` local desde ``obs_laboratorios``

Si NO encuentra el EAN en ObServer:
  - marca ``fuente_creacion = 'import_huerfano'`` (si estaba NULL)
  - lo deja para revisión manual; no se borra (puede ser un EAN privado).

Uso:
    docker exec appfarmweb-web-1 python -m scripts.materializar_huerfanos [--dry-run]

Idempotente: correrlo dos veces no rompe nada.
"""
import argparse
import sys

import database
from database import (Laboratorio, ObsCodigoBarras, ObsLaboratorio,
                      ObsProducto, Producto)


def materializar_huerfanos(dry_run=False):
    database.init_db()
    materializados = sin_match = colision = ya_huerfano = 0

    with database.get_db() as session:
        huerfanos = (session.query(Producto)
                     .filter(Producto.observer_id.is_(None))
                     .all())
        total = len(huerfanos)
        print(f'Encontrados {total} productos sin observer_id.')

        # 1) Pre-fetch bulk: obs_codigos_barras.codigo_barras -> producto_observer
        eans = [p.codigo_barra for p in huerfanos if p.codigo_barra]
        obs_by_ean = {}
        if eans:
            for ean, oid in (session.query(ObsCodigoBarras.codigo_barras,
                                           ObsCodigoBarras.producto_observer)
                             .filter(ObsCodigoBarras.codigo_barras.in_(eans),
                                     ObsCodigoBarras.fecha_baja.is_(None))
                             .all()):
                obs_by_ean.setdefault(ean, oid)

        # 2) Pre-fetch bulk: ObsProducto por los observer_ids candidatos
        obs_ids = list({oid for oid in obs_by_ean.values()})
        obs_prods = {}
        if obs_ids:
            for op in (session.query(ObsProducto)
                       .filter(ObsProducto.observer_id.in_(obs_ids))
                       .all()):
                obs_prods[op.observer_id] = op

        # 3) Productos que ya tienen esos observer_id (UNIQUE constraint)
        tomados = {oid for (oid,) in
                   (session.query(Producto.observer_id)
                    .filter(Producto.observer_id.in_(obs_ids))
                    .all())} if obs_ids else set()

        # 4) Materializar uno a uno
        for prod in huerfanos:
            obs_id = obs_by_ean.get(prod.codigo_barra)
            if not obs_id:
                if not prod.fuente_creacion:
                    if not dry_run:
                        prod.fuente_creacion = 'import_huerfano'
                    ya_huerfano += 1
                sin_match += 1
                continue
            if obs_id in tomados:
                # Ya hay otro Producto con este observer_id. No podemos linkear.
                # El caller debería deduplicar/mergear manualmente.
                colision += 1
                continue

            obs_prod = obs_prods.get(obs_id)
            if not obs_prod:
                sin_match += 1
                continue

            if not dry_run:
                prod.observer_id = obs_id
                prod.fuente_creacion = 'materializar_obs'
                if not prod.descripcion or prod.descripcion.strip() == '':
                    prod.descripcion = obs_prod.descripcion
                if not prod.codigo_alfabeta and obs_prod.codigo_alfabeta:
                    prod.codigo_alfabeta = obs_prod.codigo_alfabeta
                if not prod.laboratorio_id and obs_prod.laboratorio_observer:
                    lab = (session.query(Laboratorio)
                           .filter_by(observer_id=obs_prod.laboratorio_observer)
                           .first())
                    if not lab:
                        obs_lab = session.get(ObsLaboratorio,
                                              obs_prod.laboratorio_observer)
                        if obs_lab:
                            lab = Laboratorio(nombre=obs_lab.descripcion,
                                              observer_id=obs_prod.laboratorio_observer,
                                              activo=True)
                            session.add(lab)
                            session.flush()
                    if lab:
                        prod.laboratorio_id = lab.id
                tomados.add(obs_id)
            materializados += 1

        if dry_run:
            session.rollback()
            print('[DRY-RUN] No se aplicaron cambios.')
        else:
            session.commit()

    print(f'  Materializados:   {materializados}')
    print(f'  Sin match en obs: {sin_match}')
    print(f'  Marcados huerfano: {ya_huerfano}')
    print(f'  Colision observer_id: {colision}')
    return materializados


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true',
                        help='Reportar sin escribir.')
    args = parser.parse_args()
    materializar_huerfanos(dry_run=args.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
