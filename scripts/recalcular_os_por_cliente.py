"""Recalcula la OS principal inferida por cliente desde obs_ventas_detalle.

DW.Clientes no expone IdObraSocialPrincipal directamente. Para cada cliente,
buscamos la OS más frecuente en sus ventas (excluyendo particulares) y la
guardamos en `cliente_os_inferida` (tabla puente).

Idempotente: borra y recrea. Corre rápido (~5s para 80k clientes).

Uso:
    docker exec appfarmweb-web-1 sh -c "cd /app && python -m scripts.recalcular_os_por_cliente"

Disparable también desde la UI: POST /api/obs/recalcular-os-clientes (admin).
"""
import os

from sqlalchemy import func

import database
from database import (
    ClienteOsInferida,
    ObsCliente,
    ObsVentaDetalle,
    get_db,
    init_db,
    now_ar,
)
from services.farmacia import farmacia_operativa


def recalcular(min_ventas=1):
    """Recalcula la OS principal por cliente.

    Args:
        min_ventas: solo procesa clientes con >= N ventas con OS.
            Default 1 (cualquier cliente que haya tenido al menos una
            venta con OS).

    Returns:
        dict con {procesados, con_os, sin_os, top_os}.
    """
    init_db()
    id_farmacia = farmacia_operativa()

    with get_db() as session:
        # 1. Total de dispensas por cliente (con o sin OS) en la farmacia.
        total_q = (session.query(
                        ObsVentaDetalle.cliente_observer.label('cli'),
                        func.count(ObsVentaDetalle.id_producto_vendido).label('n_total'),
                    )
                    .filter(
                        ObsVentaDetalle.id_farmacia == id_farmacia,
                        ObsVentaDetalle.cliente_observer.isnot(None),
                    )
                    .group_by(ObsVentaDetalle.cliente_observer)
                    .subquery())

        # 2. OS más frecuente por cliente (excluyendo particulares y NULL).
        os_count_q = (session.query(
                          ObsVentaDetalle.cliente_observer.label('cli'),
                          ObsVentaDetalle.obra_social_observer.label('os'),
                          func.count(ObsVentaDetalle.id_producto_vendido).label('n'),
                      )
                      .filter(
                          ObsVentaDetalle.id_farmacia == id_farmacia,
                          ObsVentaDetalle.cliente_observer.isnot(None),
                          ObsVentaDetalle.obra_social_observer.isnot(None),
                          ObsVentaDetalle.es_venta_particular.isnot(True),
                      )
                      .group_by(ObsVentaDetalle.cliente_observer,
                                ObsVentaDetalle.obra_social_observer)
                      .subquery())

        # 3. Para cada cliente, quedarse con la OS de mayor n.
        # Usamos window function para ranking dentro del grupo.
        from sqlalchemy import and_, desc
        # Approach simpler sin window: traer todo y resolver en Python.
        rows = session.query(
            os_count_q.c.cli,
            os_count_q.c.os,
            os_count_q.c.n,
        ).order_by(os_count_q.c.cli, desc(os_count_q.c.n)).all()

        mejor_os_por_cliente = {}   # cli → (os_id, n)
        for r in rows:
            if r.cli not in mejor_os_por_cliente:
                mejor_os_por_cliente[r.cli] = (r.os, r.n)

        # 4. Total por cliente.
        total_por_cliente = {r.cli: r.n_total
                             for r in session.query(total_q).all()}

        # 5. Borrar tabla y reinsertar.
        session.query(ClienteOsInferida).delete()
        session.flush()

        from decimal import Decimal
        contador_procesados = 0
        contador_con_os = 0
        contador_sin_os = 0
        top_os_count = {}   # os_id → cuántos clientes la tienen como principal

        for cli, total in total_por_cliente.items():
            mejor = mejor_os_por_cliente.get(cli)
            if mejor and mejor[1] >= min_ventas:
                os_id, n = mejor
                conf = round((n / total) * 100, 2) if total > 0 else 0
                session.add(ClienteOsInferida(
                    cliente_observer=cli,
                    obra_social_observer=os_id,
                    n_dispensas=n,
                    n_dispensas_total=total,
                    confianza_pct=Decimal(str(conf)),
                    calculado_en=now_ar(),
                ))
                contador_con_os += 1
                top_os_count[os_id] = top_os_count.get(os_id, 0) + 1
            else:
                # Cliente sin OS frecuente — guardamos con os_id=NULL y total
                # para saber que ya lo procesamos.
                session.add(ClienteOsInferida(
                    cliente_observer=cli,
                    obra_social_observer=None,
                    n_dispensas=0,
                    n_dispensas_total=total,
                    confianza_pct=None,
                    calculado_en=now_ar(),
                ))
                contador_sin_os += 1
            contador_procesados += 1

            if contador_procesados % 5000 == 0:
                session.commit()
                print(f'   ... {contador_procesados} clientes procesados')
        session.commit()

        return {
            'procesados': contador_procesados,
            'con_os': contador_con_os,
            'sin_os': contador_sin_os,
            'top_os': sorted(top_os_count.items(), key=lambda x: -x[1])[:10],
        }


if __name__ == '__main__':
    print('🔄 Recalculando OS principal por cliente...')
    res = recalcular()
    print(f"\n✅ Procesados: {res['procesados']}")
    print(f"   Con OS principal: {res['con_os']}")
    print(f"   Sin OS frecuente: {res['sin_os']}")
    print("\n   Top 10 OS por cantidad de clientes:")
    with get_db() as session:
        from database import ObsObraSocial
        for os_id, n in res['top_os']:
            os_obj = session.get(ObsObraSocial, os_id)
            nombre = os_obj.descripcion if os_obj else f'OS#{os_id}'
            print(f'     {n:5d}  {nombre}')
