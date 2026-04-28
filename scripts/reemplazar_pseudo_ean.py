"""Reemplaza pseudo-EANs `OBS:<observer_id>` por el EAN real desde obs_codigos_barras.

Update one-shot que limpia los productos y pedido_items locales que se crearon
durante el período donde NO teníamos los EANs reales (antes de importar
codbarras.txt). Idempotente — se puede correr varias veces.

Uso:
    docker exec appfarmweb-web-1 sh -c "cd /app && python -m scripts.reemplazar_pseudo_ean"
"""
import database
from database import ObsCodigoBarras, PedidoItem, Producto, get_db, init_db


def main():
    init_db()
    with get_db() as session:
        # 1. Productos con codigo_barra OBS:N
        prods = session.query(Producto).filter(Producto.codigo_barra.like('OBS:%')).all()
        print(f'Productos con pseudo-EAN: {len(prods)}')

        eans_disponibles = {pid: ean for (pid, ean) in session.query(
            ObsCodigoBarras.producto_observer, ObsCodigoBarras.codigo_barras
        ).filter(ObsCodigoBarras.orden == 1,
                 ObsCodigoBarras.fecha_baja.is_(None)).all()}

        n_updated_prod = n_no_ean = 0
        old_to_new_cb = {}  # mapping para actualizar pedido_items
        for p in prods:
            try:
                obs_id = int(p.codigo_barra[4:])
            except (ValueError, TypeError):
                continue
            ean_real = eans_disponibles.get(obs_id)
            if not ean_real:
                n_no_ean += 1
                continue
            # Verificar que el EAN real no esté ya tomado por otro Producto
            ya_existe = session.query(Producto.id)\
                               .filter(Producto.codigo_barra == ean_real,
                                       Producto.id != p.id).first()
            if ya_existe:
                # Hay un duplicado: este pseudo-EAN debería fusionarse con el
                # producto que ya tiene el EAN real. Por ahora, solo loggear.
                print(f'  ⚠ {p.codigo_barra} → EAN real {ean_real} ya está tomado por otro producto. Skip.')
                continue
            old_cb = p.codigo_barra
            p.codigo_barra = ean_real
            old_to_new_cb[old_cb] = ean_real
            n_updated_prod += 1
        session.commit()
        print(f'  ✅ {n_updated_prod} productos actualizados con EAN real')
        print(f'  ⚠  {n_no_ean} productos sin EAN registrado en obs_codigos_barras')

        # 2. PedidoItems con codigo_barra OBS:N — propagar mapping
        n_updated_items = 0
        for old_cb, new_cb in old_to_new_cb.items():
            r = session.query(PedidoItem).filter(PedidoItem.codigo_barra == old_cb).update({
                'codigo_barra': new_cb
            })
            n_updated_items += r
        session.commit()
        print(f'  ✅ {n_updated_items} pedido_items actualizados')

        # 3. Resumen
        residuales = session.query(Producto.id).filter(Producto.codigo_barra.like('OBS:%')).count()
        print(f'\n  Residuales con pseudo-EAN: {residuales} productos')
        print('  (Estos son productos que aún no tienen EAN cargado en obs_codigos_barras)')


if __name__ == '__main__':
    main()
