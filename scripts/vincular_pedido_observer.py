"""Vincula items de un pedido (o todos) con productos de ObServer.

Estrategia:
    1. Resolver el laboratorio del pedido contra obs_laboratorios (fuzzy).
    2. Filtrar obs_productos a ese lab.
    3. Para cada PedidoItem sin observer_id resuelto, normalizar su nombre y buscar
       match contra obs_productos.descripcion del lab.
    4. Si match unívoco: upsert Producto local con codigo_barra del item +
       observer_id resuelto. Si ya hay un Producto con ese EAN, le setea observer_id.

Uso:
    docker-compose exec web python scripts/vincular_pedido_observer.py            # todos
    docker-compose exec web python scripts/vincular_pedido_observer.py 7          # solo pedido 7
    docker-compose exec web python scripts/vincular_pedido_observer.py --dry      # sin escribir
"""
import os
import sys
from collections import defaultdict

# Imports del proyecto
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from database import ObsLaboratorio, ObsProducto, Pedido, PedidoItem, Producto
from producto_matcher import match_producto
from producto_matcher import normalizar_texto as _norm
from producto_matcher import tokens_significativos as _tokens


def _resolver_lab_observer(session, lab_pedido):
    """Devuelve el observer_id del laboratorio que más se parezca al nombre del pedido."""
    norm_target = _norm(lab_pedido)
    if not norm_target:
        return None
    labs = session.query(ObsLaboratorio).filter(ObsLaboratorio.fecha_baja.is_(None)).all()
    # Match exacto primero
    for l in labs:
        if _norm(l.descripcion) == norm_target:
            return l
    # Match por contains
    candidatos = [l for l in labs if norm_target in _norm(l.descripcion)
                  or _norm(l.descripcion) in norm_target]
    if len(candidatos) == 1:
        return candidatos[0]
    # Match por overlap de tokens (mejor candidato)
    target_tokens = _tokens(lab_pedido)
    if target_tokens:
        scored = []
        for l in labs:
            lt = _tokens(l.descripcion)
            if not lt:
                continue
            inter = target_tokens & lt
            if not inter:
                continue
            score = len(inter) / max(len(target_tokens), len(lt))
            scored.append((score, l))
        scored.sort(key=lambda x: -x[0])
        if scored and scored[0][0] >= 0.5:
            return scored[0][1]
    return None


def _matchear(pedido_nombre, obs_prods, session=None):
    """Devuelve (ObsProducto | None, motivo).

    Wrapper sobre `producto_matcher.match_producto(target='obs_producto')` que
    pasa `obs_prods` como pool precargado (ya filtrado por fecha_baja). Lo dejo
    como helper local para mantener la firma original que devuelve un motivo
    legible para los logs.
    """
    if not pedido_nombre or not _norm(pedido_nombre):
        return None, 'nombre vacío'
    res = match_producto(
        descripcion=pedido_nombre,
        target='obs_producto',
        pool=obs_prods,
        threshold=0.80,
        incluir_candidatos=False,
        session=session,
    )
    if res.producto is not None:
        return res.producto, res.estrategia
    if 'match_ambiguo' in res.warnings:
        return None, 'ambiguo'
    return None, 'sin match'


def procesar_pedido(session, pedido, dry_run=False):
    print(f'\n=== Pedido #{pedido.id} — {pedido.laboratorio} ===')

    lab = _resolver_lab_observer(session, pedido.laboratorio)
    if not lab:
        print(f'  ✖  No pude resolver el lab "{pedido.laboratorio}" en obs_laboratorios.')
        return {'linkeados': 0, 'ambiguos': 0, 'no_encontrados': 0, 'ya_linkeado': 0, 'errores': 0}

    print(f'  Lab ObServer: "{lab.descripcion}" (#{lab.observer_id})')

    obs_prods = session.query(ObsProducto).filter(
        ObsProducto.laboratorio_observer == lab.observer_id,
        ObsProducto.fecha_baja.is_(None)
    ).all()
    print(f'  {len(obs_prods)} productos del lab en ObServer.')

    items = session.query(PedidoItem).filter_by(pedido_id=pedido.id).all()
    print(f'  {len(items)} items en el pedido.')

    stats = {'linkeados': 0, 'ambiguos': 0, 'no_encontrados': 0, 'ya_linkeado': 0, 'errores': 0}
    detalles_no = []
    detalles_amb = []

    for it in items:
        cb = (it.codigo_barra or '').strip()
        if not cb or not it.nombre:
            stats['errores'] += 1
            continue

        # ¿Producto local existe? ¿Ya tiene observer_id?
        prod_local = session.query(Producto).filter_by(codigo_barra=cb).first()
        if prod_local and prod_local.observer_id:
            stats['ya_linkeado'] += 1
            continue

        match, motivo = _matchear(it.nombre, obs_prods, session=session)
        if not match:
            if 'ambiguo' in motivo:
                stats['ambiguos'] += 1
                detalles_amb.append((it.nombre, motivo))
            else:
                stats['no_encontrados'] += 1
                detalles_no.append((it.nombre, motivo))
            continue

        # Verificar que ese observer_id no esté ya tomado por otro Producto local
        ya_tomado = session.query(Producto).filter(
            Producto.observer_id == match.observer_id,
            Producto.codigo_barra != cb
        ).first()
        if ya_tomado:
            print(f'  ⚠  observer_id {match.observer_id} ya está en otro Producto ({ya_tomado.codigo_barra}). Skip.')
            stats['errores'] += 1
            continue

        # Upsert
        if not dry_run:
            if prod_local:
                prod_local.observer_id = match.observer_id
                if not prod_local.codigo_alfabeta and match.codigo_alfabeta:
                    prod_local.codigo_alfabeta = match.codigo_alfabeta
            else:
                session.add(Producto(
                    codigo_barra=cb,
                    descripcion=it.nombre,
                    observer_id=match.observer_id,
                    codigo_alfabeta=match.codigo_alfabeta,
                ))
        stats['linkeados'] += 1

    if not dry_run:
        session.commit()

    print(f'  → linkeados:    {stats["linkeados"]}')
    print(f'  → ya linkeados: {stats["ya_linkeado"]}')
    print(f'  → ambiguos:     {stats["ambiguos"]}')
    print(f'  → no encontrados: {stats["no_encontrados"]}')
    if stats['errores']:
        print(f'  → errores:      {stats["errores"]}')

    if detalles_amb[:5]:
        print('  Ejemplos ambiguos:')
        for n, m in detalles_amb[:5]:
            print(f'     · {n[:60]:60s}  → {m}')
    if detalles_no[:5]:
        print('  Ejemplos no encontrados:')
        for n, m in detalles_no[:5]:
            print(f'     · {n[:60]:60s}  → {m}')

    return stats


def main():
    args = sys.argv[1:]
    dry_run = '--dry' in args
    args = [a for a in args if not a.startswith('--')]
    pedido_id = int(args[0]) if args else None

    if not os.environ.get('DATABASE_URL'):
        os.environ['DATABASE_URL'] = 'postgresql://postgres:postgres@db:5432/farmacia'

    database.init_db(os.environ['DATABASE_URL'])
    session = database.SessionLocal()
    try:
        if pedido_id:
            pedido = session.get(Pedido, pedido_id)
            if not pedido:
                print(f'Pedido #{pedido_id} no encontrado.')
                sys.exit(1)
            pedidos = [pedido]
        else:
            pedidos = session.query(Pedido).order_by(Pedido.creado_en.desc()).all()
            print(f'Procesando {len(pedidos)} pedidos…')

        if dry_run:
            print('*** DRY RUN — no se escriben cambios ***')

        totals = defaultdict(int)
        for p in pedidos:
            r = procesar_pedido(session, p, dry_run=dry_run)
            for k, v in r.items():
                totals[k] += v

        print('\n=== TOTAL ===')
        for k, v in totals.items():
            print(f'  {k}: {v}')

        if dry_run:
            print('\n(no se escribió nada — quitá --dry para persistir)')
    finally:
        session.close()


if __name__ == '__main__':
    main()
