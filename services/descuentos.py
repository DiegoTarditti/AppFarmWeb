"""Lógica de descuentos para el flujo de compra rápida.

Combina los 5 niveles de descuento multiplicativamente:
1. Descuento base (DescuentoBase: lab × droguería).
2. Transfer general (OfertaMinimo sin producto específico, todo el lab).
3. Oferta por producto puntual (OfertaMinimo con cant_min=1).
4. Oferta por producto con cantidad mínima (OfertaMinimo con cant_min>1).
5. Módulos (combo de productos) — TODO en otro paso.

Fórmula validada con el usuario:
    descuento_total = 1 - Π (1 - dto_n)
    Ej: base 31.03% + transfer 25% = 1 - (0.6897 × 0.75) = 48.27%
"""
from datetime import date


def combinar_multiplicativo(*pcts):
    """Combina porcentajes multiplicativamente. Acepta None/0 (los ignora).
    Devuelve el % final como float (ej. 48.27 para 48.27%).
    """
    factores = []
    for p in pcts:
        if p is None:
            continue
        try:
            v = float(p)
        except (TypeError, ValueError):
            continue
        if v <= 0:
            continue
        factores.append(1 - v / 100)
    if not factores:
        return 0.0
    prod = 1.0
    for f in factores:
        prod *= f
    return round((1 - prod) * 100, 2)


def mejor_descuento(session, producto_observer, lab_id, cantidad=1, fecha_ref=None,
                     descuentos_excluidos=None):
    """Para un producto y laboratorio, devuelve lista de droguerías con su
    descuento total combinado, ordenada por % descendente.

    Args:
        session: SQLAlchemy session.
        producto_observer: int — observer_id del producto en obs_productos.
        lab_id: int — Laboratorio.id local (no observer_id).
        cantidad: int — para evaluar si aplica oferta con mínimo.
        fecha_ref: date — para filtrar descuentos vigentes (default: hoy).
        descuentos_excluidos: set de IDs a excluir del cálculo. IDs:
            - 'base:<lab_id>:<drog_id>' para descuentos base.
            - 'oferta:<id>' para ofertas/transfers.
            Útil para el panel de auditoría que permite desmarcar para
            simular qué pasaría sin un descuento específico.

    Returns:
        Lista de dicts:
        [
            {
                'drogueria_id': 1,
                'drogueria_nombre': 'Kellerhoff',
                'compra_minima': None,  # o monto si la drog tiene mínimo
                'descuento_total_pct': 48.27,
                'desglose': [
                    {'nivel': 'base',     'pct': 31.03, 'fuente': 'Acuerdo anual', 'plazo': '30 dias'},
                    {'nivel': 'transfer', 'pct': 25.00, 'fuente': 'TRs OTC', 'vigencia_hasta': '2026-05-15'},
                ],
                'cumple_min_compra': True,  # se setea más adelante en el optimizador
            },
            ...
        ]
    """
    from database import DescuentoBase, OfertaMinimo, Provider

    if fecha_ref is None:
        fecha_ref = date.today()
    excluidos = descuentos_excluidos or set()

    # 1. Recolectar todos los descuentos base activos del lab
    bases = (session.query(DescuentoBase, Provider)
             .join(Provider, DescuentoBase.drogueria_id == Provider.id)
             .filter(DescuentoBase.laboratorio_id == lab_id,
                     DescuentoBase.activo == True,  # noqa: E712
                     Provider.activo == True).all())  # noqa: E712

    if not bases:
        # Lab sin descuentos base configurados
        return []

    # 2. Recolectar todas las ofertas activas y vigentes del lab
    from sqlalchemy import or_
    ofertas_lab = session.query(OfertaMinimo).filter(
        OfertaMinimo.laboratorio_id == lab_id,
        OfertaMinimo.activo == True,  # noqa: E712
        # Vigencia: NULL = indefinida, o vigencia_hasta >= fecha_ref
        or_(OfertaMinimo.vigencia_hasta.is_(None),
            OfertaMinimo.vigencia_hasta >= fecha_ref),
    ).all()

    # Filtrar por producto + cantidad mínima
    ofertas_producto = [
        o for o in ofertas_lab
        if (o.ean == f'OBS:{producto_observer}'
            or _match_observer_id(o, producto_observer, session))
        and cantidad >= int(o.unidades_minima or 1)
    ]

    # 3. Armar resultado por droguería
    resultado = []
    for db_, prov in bases:
        base_id = f'base:{lab_id}:{prov.id}'
        if base_id in excluidos:
            continue  # el usuario desmarcó este descuento base — saltear esta drog
        base_pct = float(db_.descuento_pct or 0)
        plazo = db_.plazo_pago or ''
        desglose = [{
            'id':     base_id,
            'nivel':  'base',
            'pct':    base_pct,
            'fuente': 'Acuerdo anual',
            'plazo':  plazo,
        }]
        # Mejor oferta para esta droguería específica.
        # Match: drogueria_id == prov.id O drogueria_id IS NULL (global del lab).
        # Si hay específica para esta drog, gana sobre la global.
        oferta_drog = None
        oferta_global = None
        for o in ofertas_producto:
            if o.drogueria_id == prov.id:
                if oferta_drog is None or float(o.descuento_psl or 0) > float(oferta_drog.descuento_psl or 0):
                    oferta_drog = o
            elif o.drogueria_id is None:
                if oferta_global is None or float(o.descuento_psl or 0) > float(oferta_global.descuento_psl or 0):
                    oferta_global = o
        oferta_aplica = oferta_drog or oferta_global
        if oferta_aplica and f'oferta:{oferta_aplica.id}' in excluidos:
            oferta_aplica = None  # excluida por el usuario
        oferta_pct = 0.0
        if oferta_aplica:
            oferta_pct = float(oferta_aplica.descuento_psl or 0)
            scope = 'esta drog.' if oferta_aplica.drogueria_id else 'todas las drog.'
            desglose.append({
                'id':              f'oferta:{oferta_aplica.id}',
                'nivel':           'transfer' if (oferta_aplica.unidades_minima or 1) <= 1 else 'oferta c/min',
                'pct':             oferta_pct,
                'fuente':          (oferta_aplica.observacion or oferta_aplica.codigo or 'TR') + f' ({scope})',
                'unidades_minima': int(oferta_aplica.unidades_minima or 1),
                'vigencia_hasta':  oferta_aplica.vigencia_hasta.isoformat() if oferta_aplica.vigencia_hasta else None,
            })
        total = combinar_multiplicativo(base_pct, oferta_pct)
        resultado.append({
            'drogueria_id':         prov.id,
            'drogueria_nombre':     prov.razon_social,
            'compra_minima':        float(prov.compra_minima_pesos) if prov.compra_minima_pesos else None,
            'descuento_total_pct':  total,
            'desglose':             desglose,
            'cumple_min_compra':    True,  # placeholder
        })

    # 4. Ordenar por descuento total DESC (mejor primero), después por plazo
    resultado.sort(key=lambda r: (-r['descuento_total_pct'], r['drogueria_nombre']))
    return resultado


def _match_observer_id(oferta, producto_observer, session):
    """Helper: ¿la oferta corresponde al producto del observer_id dado?
    Resuelve EAN real vía obs_codigos_barras."""
    from database import ObsCodigoBarras
    if not oferta.ean:
        return False
    # Match directo por OBS:N
    if oferta.ean == f'OBS:{producto_observer}':
        return True
    # Match por EAN real
    eans_del_producto = [r[0] for r in session.query(ObsCodigoBarras.codigo_barras)
                         .filter(ObsCodigoBarras.producto_observer == producto_observer,
                                 ObsCodigoBarras.fecha_baja.is_(None)).all()]
    return oferta.ean in eans_del_producto
