"""Seed de ofertas de prueba en OfertaMinimo con EANs reales de la DB.

Toma productos reales (con EAN en obs_codigos_barras y lab local resuelto),
e inserta ofertas variadas para probar el badge TRF + botón en armar pedido.

Idempotente: solo inserta si no existe (por ean + laboratorio_id).

Uso:
    docker exec appfarmweb-web-1 sh -c "cd /app && python -m scripts.seed_ofertas_prueba"
"""
from datetime import date, timedelta
from decimal import Decimal

import database
from database import (
    Laboratorio,
    ObsCodigoBarras,
    ObsLaboratorio,
    ObsProducto,
    OfertaMinimo,
    get_db,
    init_db,
)
from helpers import _normalizar_nombre_entidad as _norm

N_OFERTAS = 15


def main():
    init_db()
    with get_db() as session:
        # Labs locales activos (en round-robin para las ofertas)
        labs = session.query(Laboratorio.id, Laboratorio.nombre)\
                      .filter(Laboratorio.activo.is_(True))\
                      .order_by(Laboratorio.nombre).all()
        if not labs:
            print("No hay laboratorios locales activos. Abortando.")
            return

        # EANs reales: cualquier producto vigente con EAN orden=1
        eans = (
            session.query(
                ObsCodigoBarras.codigo_barras,
                ObsProducto.descripcion,
            )
            .join(ObsProducto,
                  ObsProducto.observer_id == ObsCodigoBarras.producto_observer)
            .filter(
                ObsCodigoBarras.orden == 1,
                ObsCodigoBarras.fecha_baja.is_(None),
                ObsCodigoBarras.codigo_barras.isnot(None),
                ObsProducto.fecha_baja.is_(None),
            )
            .order_by(ObsProducto.descripcion)
            .limit(N_OFERTAS * 2)
            .all()
        )

        if not eans:
            print("No se encontraron EANs en obs_codigos_barras. Abortando.")
            return

        vigencia_desde = date.today() - timedelta(days=10)
        vigencia_hasta = date.today() + timedelta(days=80)

        # Variedad de ofertas: sin mínimo, con mínimo chico, con mínimo grande
        configs = [
            # (descuento_psl, unidades_minima)
            (Decimal('28.00'), 1),
            (Decimal('32.50'), 6),
            (Decimal('25.00'), 1),
            (Decimal('35.00'), 12),
            (Decimal('20.00'), 1),
            (Decimal('30.00'), 24),
            (Decimal('22.50'), 1),
            (Decimal('18.00'), 6),
            (Decimal('40.00'), 12),
            (Decimal('15.00'), 1),
            (Decimal('27.00'), 18),
            (Decimal('33.00'), 1),
            (Decimal('19.50'), 6),
            (Decimal('45.00'), 24),
            (Decimal('26.00'), 1),
        ]

        insertados = 0
        for i, r in enumerate(eans):
            if insertados >= N_OFERTAS:
                break
            lab_id, lab_nombre = labs[i % len(labs)]
            ean = r.codigo_barras
            dto, um = configs[i % len(configs)]

            existing = (session.query(OfertaMinimo)
                        .filter_by(ean=ean, laboratorio_id=lab_id).first())
            if existing:
                print(f"  Ya existe: {r.descripcion[:35]} EAN={ean} — salteando")
                continue

            session.add(OfertaMinimo(
                laboratorio_id=lab_id,
                ean=ean,
                descripcion=r.descripcion[:300],
                unidades_minima=um,
                descuento_psl=dto,
                vigencia_desde=vigencia_desde,
                vigencia_hasta=vigencia_hasta,
                activo=True,
            ))
            print(f"  + [{lab_nombre[:15]:<15}] {r.descripcion[:35]:<35} EAN={ean} dto={dto}% mín={um}")
            insertados += 1

        session.commit()
        print(f"\n✓ {insertados} ofertas insertadas en {len(labs)} labs.")


if __name__ == '__main__':
    main()
