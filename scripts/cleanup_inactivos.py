"""Limpieza de proveedores y laboratorios sin movimientos.

Uso:
    python scripts/cleanup_inactivos.py                  # dry-run (solo muestra qué borraría)
    python scripts/cleanup_inactivos.py --ejecutar       # borra de verdad

Considera "tiene movimiento" si:
  Proveedor:
    - Hay una Invoice cuyo proveedor_cuit o proveedor_razon coincide
    - Tiene Claim / DescuentoCampana / BarcodeMapping / PagoAjusteCC
    - Está referenciado por DocumentoPendiente
    - Está referenciado como partner_id en ProcesoCompra (tipo='drogueria')

  Laboratorio:
    - Tiene Productos asociados (laboratorio_id)
    - Tiene Modulos
    - Tiene ExportTemplate / OfertaMinimo
    - Aparece como laboratorio_id en AnalisisSesion
    - Aparece por nombre en Pedidos o product_analytics
    - Está referenciado como partner_id en ProcesoCompra (tipo='laboratorio')
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
from database import (
    init_db, get_db, Provider, Laboratorio, Invoice, Claim, DescuentoCampana,
    BarcodeMapping, PagoAjusteCC, DocumentoPendiente, ProcesoCompra,
    Producto, Modulo, ExportTemplate, OfertaMinimo, AnalisisSesion,
    Pedido, ProductAnalytics,
)


def proveedores_con_movimiento(session):
    """Devuelve el set de IDs de proveedores con algún movimiento."""
    con_mov = set()

    # Por FK directa
    for cls, col in [
        (Claim, 'proveedor_id'),
        (DescuentoCampana, 'proveedor_id'),
        (BarcodeMapping, 'proveedor_id'),
        (PagoAjusteCC, 'proveedor_id'),
        (DocumentoPendiente, 'proveedor_id'),
    ]:
        rows = session.query(getattr(cls, col)).filter(getattr(cls, col).isnot(None)).distinct().all()
        con_mov.update(r[0] for r in rows if r[0])

    # ProcesoCompra tipo=drogueria con partner_id
    rows = session.query(ProcesoCompra.partner_id).filter(
        ProcesoCompra.tipo == 'drogueria',
        ProcesoCompra.partner_id.isnot(None),
    ).distinct().all()
    con_mov.update(r[0] for r in rows if r[0])

    # Matcheo por cuit/razon en Invoice (Invoice no tiene FK a Provider)
    facturas = session.query(Invoice.proveedor_cuit, Invoice.proveedor_razon).distinct().all()
    for cuit, razon in facturas:
        prov = None
        if cuit:
            prov = session.query(Provider).filter_by(cuit=cuit).first()
        if not prov and razon:
            prov = session.query(Provider).filter(
                Provider.razon_social.ilike(razon)
            ).first()
        if prov:
            con_mov.add(prov.id)

    return con_mov


def laboratorios_con_movimiento(session):
    """Devuelve el set de IDs de laboratorios con algún movimiento."""
    con_mov = set()

    # FK directas
    for cls, col in [
        (Producto, 'laboratorio_id'),
        (Modulo, 'laboratorio_id'),
        (ExportTemplate, 'laboratorio_id'),
        (OfertaMinimo, 'laboratorio_id'),
        (AnalisisSesion, 'laboratorio_id'),
    ]:
        rows = session.query(getattr(cls, col)).filter(getattr(cls, col).isnot(None)).distinct().all()
        con_mov.update(r[0] for r in rows if r[0])

    # ProcesoCompra tipo=laboratorio
    rows = session.query(ProcesoCompra.partner_id).filter(
        ProcesoCompra.tipo == 'laboratorio',
        ProcesoCompra.partner_id.isnot(None),
    ).distinct().all()
    con_mov.update(r[0] for r in rows if r[0])

    # Pedidos tienen 'laboratorio' como string — matcheo por nombre
    nombres_en_pedidos = set(
        r[0].strip() for r in session.query(Pedido.laboratorio).distinct().all() if r[0]
    )
    # ProductAnalytics también tiene 'laboratorio' como string
    nombres_en_analytics = set(
        r[0].strip() for r in session.query(ProductAnalytics.laboratorio).distinct().all() if r[0]
    )
    # AnalisisSesion también tiene 'laboratorio_nombre'
    nombres_en_sesiones = set(
        r[0].strip() for r in session.query(AnalisisSesion.laboratorio_nombre).distinct().all() if r[0]
    )
    nombres = nombres_en_pedidos | nombres_en_analytics | nombres_en_sesiones

    # Matchear por nombre (case-insensitive)
    if nombres:
        todos_labs = session.query(Laboratorio).all()
        for lab in todos_labs:
            lab_nombre = (lab.nombre or '').strip().lower()
            if any(lab_nombre == n.lower() for n in nombres):
                con_mov.add(lab.id)

    return con_mov


def main():
    parser = argparse.ArgumentParser(description='Limpia proveedores y laboratorios sin movimientos')
    parser.add_argument('--ejecutar', action='store_true',
                        help='Ejecuta el borrado. Sin este flag es dry-run.')
    parser.add_argument('--solo-proveedores', action='store_true')
    parser.add_argument('--solo-laboratorios', action='store_true')
    args = parser.parse_args()

    init_db()

    with get_db() as session:
        # Proveedores
        if not args.solo_laboratorios:
            con_mov = proveedores_con_movimiento(session)
            todos = session.query(Provider).order_by(Provider.razon_social).all()
            sin_mov = [p for p in todos if p.id not in con_mov]
            print(f'\n=== Proveedores ===')
            print(f'Total: {len(todos)} · Con movimiento: {len(con_mov)} · '
                  f'Sin movimiento (candidatos a borrar): {len(sin_mov)}')
            for p in sin_mov:
                print(f'  [{p.id:4}] {p.razon_social!r:50} cuit={p.cuit or "—":20} tipo={p.tipo}')

            if args.ejecutar and sin_mov:
                for p in sin_mov:
                    session.delete(p)
                session.commit()
                print(f'  → Borrados {len(sin_mov)} proveedores.')

        # Laboratorios
        if not args.solo_proveedores:
            con_mov = laboratorios_con_movimiento(session)
            todos = session.query(Laboratorio).order_by(Laboratorio.nombre).all()
            sin_mov = [l for l in todos if l.id not in con_mov]
            print(f'\n=== Laboratorios ===')
            print(f'Total: {len(todos)} · Con movimiento: {len(con_mov)} · '
                  f'Sin movimiento (candidatos a borrar): {len(sin_mov)}')
            for l in sin_mov:
                print(f'  [{l.id:4}] {l.nombre!r}')

            if args.ejecutar and sin_mov:
                for l in sin_mov:
                    session.delete(l)
                session.commit()
                print(f'  → Borrados {len(sin_mov)} laboratorios.')

        if not args.ejecutar:
            print('\n(dry-run, no se borró nada. Agregá --ejecutar para borrar de verdad)')


if __name__ == '__main__':
    main()
