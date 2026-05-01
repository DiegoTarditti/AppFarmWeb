"""Seed sintético de Farmacia Pieri (id=2) para pruebas multi-tenant.

Crea:
- Farmacia id=2 "Pieri" con es_demo=True (si no existe).
- ~15 Pedidos + items sintéticos asociados a farmacia_id=2, usando laboratorios
  reales y productos del catálogo local existente.

Idempotente: si Pieri ya existe, NO duplica pedidos. Para volver a sembrar,
borrar manualmente con `DELETE FROM pedidos WHERE farmacia_id = 2`.

Uso:
    docker-compose exec web python scripts/seed_pieri_prueba.py
    docker-compose exec web python scripts/seed_pieri_prueba.py --force  # re-siembra

Spec multi-tenant: c:/AppSeguimiento/plan-pieri-multitenant.md
"""

import os
import random
import sys
from datetime import timedelta
from decimal import Decimal

# Path hack para imports del proyecto
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from database import Farmacia, Laboratorio, Pedido, PedidoItem, Producto, now_ar

PIERI_ID = 2
PIERI_NOMBRE = 'Farmacia Pieri'


def crear_farmacia_pieri(session):
    """Idempotente: crea Farmacia id=2 "Pieri" con es_demo=True si no existe."""
    existente = session.get(Farmacia, PIERI_ID)
    if existente:
        return existente, False
    pieri = Farmacia(
        id=PIERI_ID,
        nombre=PIERI_NOMBRE,
        razon_social='Farmacia Pieri SRL (DEMO)',
        cuit='30-99999999-9',
        direccion='— DEMO — sin dirección real',
        id_farmacia_observer=None,
        es_demo=True,
        activa=True,
    )
    session.add(pieri)
    session.flush()
    # PostgreSQL: si forzamos id=2 y la sequence quedó en 1, futuros INSERTs sin id van a colisionar.
    # Reseteamos la sequence al máximo actual.
    try:
        from sqlalchemy import text as _t
        session.execute(_t("SELECT setval(pg_get_serial_sequence('farmacias', 'id'), "
                           "(SELECT MAX(id) FROM farmacias))"))
    except Exception:
        # SQLite no tiene sequences nombradas; en tests no aplica.
        pass
    return pieri, True


def crear_pedidos_sinteticos(session, farmacia_id, n_pedidos=15):
    """Crea N pedidos sintéticos para `farmacia_id` usando productos+labs locales."""
    # Tomamos hasta 50 productos con observer_id para que sean reales.
    productos_pool = (session.query(Producto)
                      .filter(Producto.observer_id.isnot(None))
                      .limit(50).all())
    if len(productos_pool) < 5:
        # Fallback: cualquier producto con codigo_barra.
        productos_pool = (session.query(Producto)
                          .filter(Producto.codigo_barra.isnot(None))
                          .limit(50).all())
    if not productos_pool:
        print('  ⚠ No hay productos en el catálogo local. Saltando pedidos sintéticos.')
        return 0

    labs_pool = (session.query(Laboratorio)
                 .filter(Laboratorio.activo)
                 .limit(20).all())
    if not labs_pool:
        # Fallback: usar nombre de lab del primer producto si lo tiene.
        labs_pool = []

    rnd = random.Random(42)  # determinista
    creados = 0
    hoy = now_ar()

    for i in range(n_pedidos):
        # Lab: elige al azar del pool, sino derivar de un producto.
        if labs_pool:
            lab_obj = rnd.choice(labs_pool)
            lab_nombre = lab_obj.nombre
        else:
            lab_nombre = f'Lab Demo {i+1}'

        # Período sintético: pedidos repartidos en los últimos 60 días.
        dias_atras = rnd.randint(0, 60)
        fecha_creado = hoy - timedelta(days=dias_atras)

        pedido = Pedido(
            farmacia_id=farmacia_id,
            laboratorio=lab_nombre,
            farmacia=PIERI_NOMBRE,   # campo legado de string libre, igual queda.
            periodo=f'Pieri demo {fecha_creado.strftime("%Y-%m")}',
            n_days=30,
            estado=rnd.choice(['PENDIENTE', 'PENDIENTE', 'ENVIADO']),
            creado_en=fecha_creado,
            canal=rnd.choice(['drogueria', 'laboratorio', None]),
        )
        session.add(pedido)
        session.flush()

        # 3-12 items por pedido.
        n_items = rnd.randint(3, 12)
        items_seleccionados = rnd.sample(productos_pool, min(n_items, len(productos_pool)))
        for prod in items_seleccionados:
            cant = rnd.choice([1, 2, 3, 5, 10])
            precio = Decimal(str(round(rnd.uniform(500, 15000), 2)))
            session.add(PedidoItem(
                pedido_id=pedido.id,
                farmacia_id=farmacia_id,
                codigo_barra=prod.codigo_barra,
                nombre=(prod.descripcion or 'Producto demo')[:200],
                cantidad=cant,
                precio_pvp=precio,
                subtotal=precio * cant,
            ))
        creados += 1

    session.commit()
    return creados


def seed(force=False):
    if not os.environ.get('DATABASE_URL'):
        os.environ['DATABASE_URL'] = 'postgresql://postgres:postgres@db:5432/farmacia'
    database.init_db(os.environ['DATABASE_URL'])
    session = database.SessionLocal()
    try:
        print('\n=== Seed Pieri (multi-tenant Fase 2 — DEMO) ===')

        pieri, creada = crear_farmacia_pieri(session)
        if creada:
            print(f'  ✓ Farmacia id={pieri.id} "{pieri.nombre}" creada.')
            session.commit()
        else:
            print(f'  → Farmacia id={pieri.id} "{pieri.nombre}" ya existía.')

        # ¿Ya tiene pedidos? Si sí, skipear (idempotente) salvo --force.
        n_existentes = (session.query(Pedido)
                        .filter(Pedido.farmacia_id == PIERI_ID).count())
        if n_existentes > 0 and not force:
            print(f'  → Pieri ya tiene {n_existentes} pedidos sintéticos — skip.')
            print('    (usar --force para re-sembrar después de DELETE manual)')
            return

        if force and n_existentes > 0:
            print(f'  ⚠ --force activo: borrando {n_existentes} pedidos previos de Pieri…')
            # CASCADE no aplica directamente — borramos primero items.
            session.query(PedidoItem).filter(PedidoItem.farmacia_id == PIERI_ID).delete()
            session.query(Pedido).filter(Pedido.farmacia_id == PIERI_ID).delete()
            session.commit()

        n_creados = crear_pedidos_sinteticos(session, PIERI_ID, n_pedidos=15)
        print(f'  ✓ {n_creados} pedidos sintéticos creados para Pieri.')

        # Resumen.
        n_items_total = (session.query(PedidoItem)
                         .filter(PedidoItem.farmacia_id == PIERI_ID).count())
        print(f'\nResumen Pieri (farmacia_id={PIERI_ID}):')
        print(f'  Pedidos:  {n_creados}')
        print(f'  Items:    {n_items_total}')
        print('\nMicros de validación:')
        print('  SELECT COUNT(*) FROM pedidos WHERE farmacia_id=2;')
        print('  SELECT COUNT(*) FROM pedidos WHERE farmacia_id=1;')
    finally:
        session.close()


if __name__ == '__main__':
    seed(force='--force' in sys.argv)
