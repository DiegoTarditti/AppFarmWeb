"""Definición canónica de las cards de 'Acciones frecuentes' del home.

Formato de cada card:
    id              — string estable, se usa en /go/<id>, tracking y preferencias.
    titulo          — título visible.
    desc            — texto secundario.
    endpoint        — nombre de la ruta Flask para url_for().
    icono_path      — SVG path (el <svg> exterior lo pone el template).
    bg_default      — color por defecto del cuadradito del icono.
    fg_default      — color por defecto del icono (currentColor).

Para agregar una card nueva: una entrada más abajo. Nada más.
"""

ACCIONES_HOME = [
    {
        'id': 'procesos',
        'titulo': 'Procesos de compra',
        'desc': 'Ciclo análisis → pedido → factura → cruce',
        'endpoint': 'procesos_list',
        'icono_path': 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4',
        'bg_default': '#E8F3ED',
        'fg_default': '#2E7D5B',
    },
    {
        'id': 'analisis',
        'titulo': 'Análisis y proyección',
        'desc': 'Sugerencia de pedido según ventas y stock',
        'endpoint': 'purchase_index',
        'icono_path': 'M3 3v18h18M7 14l4-4 4 4 5-5',
        'bg_default': '#E8F3ED',
        'fg_default': '#2E7D5B',
    },
    {
        'id': 'ingresos',
        'titulo': 'Control de Ingreso',
        'desc': 'Subir facturas y cruzar con ERP',
        'endpoint': 'ingresos',
        'icono_path': 'M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12',
        'bg_default': '#FFF7DC',
        'fg_default': '#B38A00',
    },
    {
        'id': 'cuentas',
        'titulo': 'Cuentas Corrientes',
        'desc': 'Saldos y pagos por proveedor',
        'endpoint': 'cuentas_corrientes',
        'icono_path': 'M17 9V7a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2m2 4h10a2 2 0 002-2v-6a2 2 0 00-2-2H9a2 2 0 00-2 2v6a2 2 0 002 2zm7-5a2 2 0 11-4 0 2 2 0 014 0z',
        'bg_default': '#E8F3ED',
        'fg_default': '#2E7D5B',
    },
    {
        'id': 'reclamos',
        'titulo': 'Reclamos',
        'desc': 'Diferencias pendientes y completadas',
        'endpoint': 'claims_list',
        'icono_path': 'M12 9v2m0 4h.01M4.93 19h14.14A2 2 0 0020.93 17l-7.07-12a2 2 0 00-3.72 0L3.07 17A2 2 0 004.93 19z',
        'bg_default': '#FDE8E8',
        'fg_default': '#B91C1C',
    },
    {
        'id': 'pedidos',
        'titulo': 'Pedidos guardados',
        'desc': 'Análisis convertidos en pedidos',
        'endpoint': 'orders_list',
        'icono_path': 'M3 3h2l.4 2M7 13h10l4-8H5.4M7 13L5.4 5M7 13l-2.293 2.293c-.63.63-.184 1.707.707 1.707H17m0 0a2 2 0 100 4 2 2 0 000-4zm-8 2a2 2 0 11-4 0 2 2 0 014 0z',
        'bg_default': '#E8F3ED',
        'fg_default': '#2E7D5B',
    },
    {
        'id': 'productos',
        'titulo': 'Productos',
        'desc': 'Catálogo master con códigos y PVP',
        'endpoint': 'productos_list',
        'icono_path': 'M4 6h16M4 10h16M4 14h16M4 18h16',
        'bg_default': '#F1F5F9',
        'fg_default': '#475569',
    },
    {
        'id': 'catalogo_observer',
        'titulo': 'Catálogo ObServer',
        'desc': 'Los 122k productos con ventas, stock y lab',
        'endpoint': 'obs_productos',
        'icono_path': 'M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10',
        'bg_default': '#EDE9FE',
        'fg_default': '#7C3AED',
    },
    {
        'id': 'config',
        'titulo': 'Configuración',
        'desc': 'Ajustes del sistema',
        'endpoint': 'settings',
        'icono_path': 'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z',
        'bg_default': '#F3F4F6',
        'fg_default': '#6b7280',
    },
]

ACCIONES_HOME_BY_ID = {c['id']: c for c in ACCIONES_HOME}


def resolve_cards_para_usuario(session, usuario_id, clicks_desde_dias=30):
    """Devuelve la lista de cards a renderizar, ordenada y con color/visibilidad
    aplicados según las prefs del usuario y el ranking de uso.

    Lógica:
    - Si el user tiene modo='fijo' y un 'orden' guardado → respetar ese orden.
    - Si modo='auto' (default) → ordenar por COUNT(clicks) DESC en los últimos N días.
    - Colores personalizados override los defaults.
    - Cards en 'ocultos' se filtran.
    """
    import json
    from datetime import timedelta
    from database import Usuario, HomeCardClick, now_ar

    prefs = {}
    if usuario_id:
        u = session.get(Usuario, usuario_id)
        if u and u.preferencias_home_json:
            try:
                prefs = json.loads(u.preferencias_home_json)
            except (json.JSONDecodeError, TypeError):
                prefs = {}

    modo = prefs.get('modo', 'auto')
    orden_pref = prefs.get('orden') or []
    colores = prefs.get('colores') or {}
    ocultos = set(prefs.get('ocultos') or [])

    # Ranking por clicks
    clicks_map = {}
    if usuario_id:
        from sqlalchemy import func as _func
        desde = now_ar() - timedelta(days=clicks_desde_dias)
        rows = (session.query(HomeCardClick.card_id, _func.count(HomeCardClick.id))
                .filter(HomeCardClick.usuario_id == usuario_id,
                        HomeCardClick.clicked_at >= desde)
                .group_by(HomeCardClick.card_id).all())
        clicks_map = {cid: int(n) for cid, n in rows}

    # Determinar orden final
    orden_default = [c['id'] for c in ACCIONES_HOME]
    if modo == 'fijo' and orden_pref:
        # Respetar orden fijo; agregar al final lo que falta del default
        restantes = [x for x in orden_default if x not in orden_pref]
        orden_final = orden_pref + restantes
    else:
        # Auto: ordenar por clicks desc, fallback al orden_default
        orden_final = sorted(
            orden_default,
            key=lambda cid: (-clicks_map.get(cid, 0), orden_default.index(cid))
        )

    # Armar lista final con colores + clicks + oculto flag
    out = []
    for cid in orden_final:
        if cid not in ACCIONES_HOME_BY_ID:
            continue
        c = dict(ACCIONES_HOME_BY_ID[cid])
        c['bg'] = colores.get(cid, c['bg_default'])
        c['fg'] = c['fg_default']
        c['clicks_30d'] = clicks_map.get(cid, 0)
        c['oculto'] = cid in ocultos
        out.append(c)
    return out, modo
