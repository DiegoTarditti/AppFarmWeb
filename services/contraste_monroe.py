"""Contraste de un pedido armado contra Monroe.

Se usa al final de `/pedidos/dia/armar`, antes de cerrar el pedido: dice si hay
renglones que convendría comprarle a Monroe en vez de a la droguería elegida.

**El costo de referencia sale de la factura, no de la matriz de descuentos.**
La matriz se carga a mano por laboratorio y envejece; la factura es lo que se
pagó de verdad. Pero un precio de factura de hace dos meses comparado contra una
cotización de hoy hace parecer caro a Monroe sólo porque pasó el tiempo, así que
lo que se compara es el **descuento** —que es la relación comercial y no se mueve
seguido— aplicado sobre el PVP de hoy.

Medido el 14/09/2026 sobre las compras reales de 10 días: Kellerhoff sale 3%
más barato en el total, pero hay ~85 productos donde Monroe gana, casi todos
dermocosmética y venta libre con oferta por cantidad. O sea que esto no sirve
para cambiar de droguería: sirve para pescar esos renglones sueltos.
"""
from __future__ import annotations

import os

import requests
from sqlalchemy import text

MOTOR_URL = os.environ.get('MOTOR_COMPRAS_URL', 'http://motor-compras:8000')
TIMEOUT = 25          # si el motor o Monroe tardan más, se sigue sin contraste

# Un costo por debajo de esta fracción del PVP es un renglón mal cargado, no una
# ganga: en las facturas hay precio_unitario en 0 y en 1.
PISO_COSTO_SOBRE_PVP = 0.10

SQL_COSTOS = text("""
    WITH pedidos AS (SELECT unnest(:eans) AS ean),
    ult AS (
        SELECT DISTINCT ON (fi.codigo_barra)
               fi.codigo_barra AS ean, f.fecha, fi.dto, fi.precio_unitario
          FROM factura_items fi
          JOIN facturas f ON f.id = fi.factura_id
         WHERE fi.codigo_barra IN (SELECT ean FROM pedidos)
           AND fi.precio_unitario > 0
         ORDER BY fi.codigo_barra, f.fecha DESC, fi.id DESC
    )
    SELECT p.ean, u.fecha, u.dto, u.precio_unitario, pr.precio_lista
      FROM pedidos p
      LEFT JOIN ult u ON u.ean = p.ean
      LEFT JOIN obs_codigos_barras cb
             ON cb.codigo_barras = p.ean AND cb.fecha_baja IS NULL
      LEFT JOIN obs_productos pr
             ON pr.observer_id = cb.producto_observer AND pr.fecha_baja IS NULL
""")


def costos_de_referencia(session, eans):
    """{ean: {costo, origen, fecha, dto, pvp}} para los EAN pedidos.

    origen:
      'factura_dto'    — descuento real de la última factura sobre el PVP de hoy
      'factura_precio' — precio de la última factura (no había descuento cargado)
      'sin_costo'      — nunca se compró: no hay con qué comparar
    """
    if not eans:
        return {}
    salida = {}
    for ean, fecha, dto, unitario, pvp in session.execute(
            SQL_COSTOS, {'eans': list(eans)}):
        pvp = float(pvp) if pvp else None
        dto = float(dto) if dto else None
        unitario = float(unitario) if unitario else None

        if dto and pvp:
            costo, origen = pvp * (1 - dto / 100.0), 'factura_dto'
        elif unitario:
            costo, origen = unitario, 'factura_precio'
        else:
            salida[ean] = {'costo': None, 'origen': 'sin_costo', 'fecha': None,
                           'dto': None, 'pvp': pvp}
            continue

        # Descartamos la basura acá, antes de que el motor calcule un ahorro
        # imposible sobre un costo que no existe.
        if pvp and costo < pvp * PISO_COSTO_SOBRE_PVP:
            salida[ean] = {'costo': None, 'origen': 'costo_invalido',
                           'fecha': fecha, 'dto': dto, 'pvp': pvp}
            continue

        salida[ean] = {'costo': round(costo, 2), 'origen': origen,
                       'fecha': fecha, 'dto': dto, 'pvp': pvp}
    return salida


def contrastar(session, renglones, proveedor=None, umbral_pct=5.0,
               umbral_pesos=500.0, tasa_mensual=0.0):
    """renglones: [{ean, nombre, cantidad}]. Devuelve el panel listo para mostrar.

    Nunca levanta: si el motor no contesta, devuelve `fuente_caida` y la pantalla
    sigue. Trabar el cierre de un pedido porque una droguería está de
    mantenimiento sería peor que no avisar.
    """
    eans = [r['ean'] for r in renglones if r.get('ean')]
    costos = costos_de_referencia(session, eans)

    payload = {
        'proveedor': proveedor,
        'referencia': 'pedidos-dia',
        'umbral_pct': umbral_pct,
        'umbral_pesos': umbral_pesos,
        'tasa_mensual': tasa_mensual,
        'renglones': [],
    }
    for r in renglones:
        ref = costos.get(r.get('ean')) or {}
        payload['renglones'].append({
            'ean': r.get('ean'),
            'nombre': r.get('nombre'),
            'unidades': int(r.get('cantidad') or 0),
            'costo_unitario': ref.get('costo'),
            'plazo_dias': 0,
            'descuento_desde': ref['fecha'].isoformat() if ref.get('fecha') else None,
        })

    try:
        resp = requests.post(f'{MOTOR_URL}/precios/contrastar',
                             json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        panel = resp.json()
    except Exception as e:
        return {'fuente_caida': True, 'motivo': f'{type(e).__name__}: {e}'[:200],
                'mas_barato_en_monroe': 0, 'ahorro_estimado': 0, 'detalle': [],
                'oportunidades_por_cantidad': [], 'no_consultados': [],
                'costos': _resumen_costos(costos)}

    panel['costos'] = _resumen_costos(costos)
    # El origen del costo va en cada renglón: una diferencia grande contra un
    # costo viejo es primero una alarma de dato viejo, y después una oportunidad.
    origen_por_ean = {e: c.get('origen') for e, c in costos.items()}
    for d in panel.get('detalle', []):
        d['origen_costo'] = origen_por_ean.get(d.get('ean'))
    return panel


def _resumen_costos(costos):
    resumen = {}
    for c in costos.values():
        resumen[c['origen']] = resumen.get(c['origen'], 0) + 1
    return resumen


def cotizar_monroe(eans):
    """Precio de Monroe hoy para una lista de EAN, sin comparar nada.

    Lo usa la consulta de compras para mostrar una columna al lado de lo que se
    pagó. Si Monroe no contesta, devuelve `fuente_caida` y la tabla se muestra
    igual, sin la columna.
    """
    try:
        resp = requests.post(
            f'{MOTOR_URL}/precios/cotizar',
            json={'referencia': 'consulta-compras',
                  'items': [{'ean': e, 'unidades': 1} for e in eans]},
            timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {'fuente_caida': True, 'motivo': f'{type(e).__name__}: {e}'[:200],
                'precios': {}}
