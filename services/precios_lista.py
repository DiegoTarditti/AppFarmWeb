"""Captura del histórico de PVP de ObServer y patrón de aumento por laboratorio.

`ObsProducto.precio_lista` guarda sólo el valor actual: cada sync pisa el
anterior y el historial se pierde. `registrar_cambios()` compara lo vigente
contra lo último registrado y agrega una fila sólo cuando cambió, así que la
tabla crece con los cambios y no con los productos.

Para qué: `precio_lista_fecha_vigencia` mostró que cada laboratorio aumenta un
día fijo del mes (Gador el 31 en el 95% de sus 345 productos, Lafedar el 19 en
el 99%, Siegfried el 20 en el 92%). Pero ese campo es un snapshot — dice cuándo
fue el último aumento, no que se repita. Con dos o tres meses de historia el
patrón queda confirmado, y recién ahí sirve para decidir cuándo comprar.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import database

# Los productos sin precio real cargado arrastran una fecha centinela vieja
# (la más antigua en producción es 1902-06-03); no son cambios de precio.
FECHA_MINIMA = datetime(2020, 1, 1)


def registrar_cambios(session, lote=5000):
    """Agrega una fila por cada producto cuyo PVP cambió. Devuelve cuántas.

    Idempotente: si no cambió nada desde la última corrida, no escribe. El
    caller hace commit.
    """
    H = database.ObsPrecioListaHist
    P = database.ObsProducto

    # Último precio registrado de cada producto. Se traen TUPLAS y no entidades
    # ORM a propósito: esto corre pegado al sync de precios, que dispara varias
    # veces por día, y materializar 75k objetos en cada corrida para descartar
    # casi todos es caro al pedo.
    ultimos = {}
    for prod_id, precio, vig, detectado in session.query(
            H.producto_observer, H.precio_lista, H.fecha_vigencia, H.detectado_en
    ).order_by(H.producto_observer, H.detectado_en):
        ultimos[prod_id] = (precio, vig)      # el ORDER BY deja el más nuevo

    nuevos = 0
    q = (session.query(P)
         .filter(P.precio_lista.isnot(None),
                 P.precio_lista > 0,
                 P.precio_lista_fecha_vigencia.isnot(None),
                 P.precio_lista_fecha_vigencia >= FECHA_MINIMA))
    for prod in q.yield_per(lote):
        previo = ultimos.get(prod.observer_id)
        if previo is not None and previo == (prod.precio_lista,
                                             prod.precio_lista_fecha_vigencia):
            continue
        variacion = None
        if previo is not None and previo[0]:
            variacion = round(
                (Decimal(prod.precio_lista) / Decimal(previo[0]) - 1) * 100, 4)
        session.add(H(producto_observer=prod.observer_id,
                      laboratorio_observer=prod.laboratorio_observer,
                      precio_lista=prod.precio_lista,
                      fecha_vigencia=prod.precio_lista_fecha_vigencia,
                      variacion_pct=variacion,
                      detectado_en=database.now_ar()))
        nuevos += 1
    return nuevos


def patron_por_laboratorio(session, minimo_cambios=20, desde=None):
    """[{laboratorio_observer, cambios, dia_top, en_dia, pct}] — qué día del mes
    concentra los aumentos de cada laboratorio.

    Mientras la tabla tenga un solo mes esto repite lo que ya dice
    `precio_lista_fecha_vigencia`; el valor aparece cuando hay varios meses y se
    puede ver si el día se repite. `meses` dice sobre cuántos se midió: con 1
    solo, el resultado es una hipótesis, no un patrón.
    """
    H = database.ObsPrecioListaHist
    q = session.query(H.laboratorio_observer, H.fecha_vigencia).filter(
        H.laboratorio_observer.isnot(None), H.fecha_vigencia.isnot(None))
    if desde:
        q = q.filter(H.fecha_vigencia >= desde)

    por_lab = {}
    for lab, fecha in q.all():
        d = por_lab.setdefault(lab, {'dias': {}, 'meses': set(), 'total': 0})
        d['dias'][fecha.day] = d['dias'].get(fecha.day, 0) + 1
        d['meses'].add((fecha.year, fecha.month))
        d['total'] += 1

    out = []
    for lab, d in por_lab.items():
        if d['total'] < minimo_cambios:
            continue
        dia_top, en_dia = max(d['dias'].items(), key=lambda kv: kv[1])
        out.append({
            'laboratorio_observer': lab,
            'cambios': d['total'],
            'dia_top': dia_top,
            'en_dia': en_dia,
            'pct': round(100 * en_dia / d['total'], 1),
            'meses': len(d['meses']),
        })
    return sorted(out, key=lambda x: (-x['pct'], -x['cambios']))
