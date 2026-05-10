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
        'id': 'pedidos',
        'titulo': 'Pedidos guardados',
        'desc': 'Análisis convertidos en pedidos',
        'endpoint': 'orders_list',
        'emoji': '🛒',
        'tone': 'mint',
        'badge_key': 'pedidos_pendientes',
        'icono_path': 'M3 3h2l.4 2M7 13h10l4-8H5.4M7 13L5.4 5M7 13l-2.293 2.293c-.63.63-.184 1.707.707 1.707H17m0 0a2 2 0 100 4 2 2 0 000-4zm-8 2a2 2 0 11-4 0 2 2 0 014 0z',
        'bg_default': '#E8F3ED',
        'fg_default': '#2E7D5B',
    },
    {
        'id': 'ingresos',
        'titulo': 'Control de Ingreso',
        'desc': 'Subir facturas y cruzar con ERP',
        'endpoint': 'ingresos',
        'emoji': '📥',
        'tone': 'mint',
        'badge_key': 'docs_pendientes',
        'icono_path': 'M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12',
        'bg_default': '#FFF7DC',
        'fg_default': '#B38A00',
    },
    {
        'id': 'procesos',
        'titulo': 'Procesos de compra',
        'desc': 'Análisis → pedido → factura → cruce',
        'endpoint': 'procesos_list',
        'emoji': '📊',
        'tone': 'mint',
        'badge_key': 'procesos_abiertos',
        'icono_path': 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4',
        'bg_default': '#E8F3ED',
        'fg_default': '#2E7D5B',
    },
    {
        'id': 'reclamos',
        'titulo': 'Reclamos',
        'desc': 'Diferencias abiertas y completadas',
        'endpoint': 'claims_list',
        'emoji': '⚠️',
        'tone': 'warn',
        'badge_key': 'reclamos_abiertos',
        'icono_path': 'M12 9v2m0 4h.01M4.93 19h14.14A2 2 0 0020.93 17l-7.07-12a2 2 0 00-3.72 0L3.07 17A2 2 0 004.93 19z',
        'bg_default': '#FDE8E8',
        'fg_default': '#B91C1C',
    },
    {
        'id': 'ofertas_import',
        'titulo': 'Importar ofertas',
        'desc': 'Excel del proveedor con descuento, mínimo y plazo',
        'endpoint': 'ofertas_import_page',
        'emoji': '📈',
        'tone': 'info',
        'badge_key': None,
        'icono_path': 'M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
        'bg_default': '#FEF3C7',
        'fg_default': '#B45309',
    },
    {
        'id': 'cuentas',
        'titulo': 'Cuentas Corrientes',
        'desc': 'Saldos y pagos por proveedor',
        'endpoint': 'cuentas_corrientes',
        'emoji': '💳',
        'tone': 'mint',
        'badge_key': None,
        'icono_path': 'M17 9V7a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2m2 4h10a2 2 0 002-2v-6a2 2 0 00-2-2H9a2 2 0 00-2 2v6a2 2 0 002 2zm7-5a2 2 0 11-4 0 2 2 0 014 0z',
        'bg_default': '#E8F3ED',
        'fg_default': '#2E7D5B',
    },
    {
        'id': 'informes',
        'titulo': 'Mis Informes',
        'desc': 'Cruces de catálogo, ventas y stock — labs por droga, alertas de dependencia y más',
        'endpoint': 'informes_index',
        'emoji': '📈',
        'tone': 'info',
        'badge_key': None,
        'icono_path': 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z',
        'bg_default': '#EDE9FE',
        'fg_default': '#7C3AED',
    },
    {
        'id': 'bi',
        'titulo': 'Inteligencia de Negocios',
        'desc': 'Tablero diario — pérdida estimada, quiebres, top vendidos',
        'endpoint': 'bi_tablero',
        'emoji': '⚡',
        'tone': 'warn',
        'badge_key': None,
        'icono_path': 'M13 10V3L4 14h7v7l9-11h-7z',
        'bg_default': '#FEF3C7',
        'fg_default': '#B45309',
    },
    {
        'id': 'productos',
        'titulo': 'Productos',
        'desc': 'Catálogo master con códigos y PVP',
        'endpoint': 'productos_list',
        'emoji': '📦',
        'tone': 'mute',
        'badge_key': None,
        'icono_path': 'M4 6h16M4 10h16M4 14h16M4 18h16',
        'bg_default': '#F1F5F9',
        'fg_default': '#475569',
    },
    {
        'id': 'vademecum',
        'titulo': 'Vademécum',
        'desc': 'Búsqueda de medicamentos y monodrogas',
        'endpoint': 'vademecum_index',
        'emoji': '💊',
        'tone': 'info',
        'badge_key': None,
        'icono_path': 'M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253',
        'bg_default': '#E0F2FE',
        'fg_default': '#0369A1',
    },
    {
        'id': 'clientes',
        'titulo': 'Clientes',
        'desc': '84k clientes con datos editables (notas, WhatsApp, tags)',
        'endpoint': 'clientes_list',
        'emoji': '👥',
        'tone': 'info',
        'badge_key': None,
        'icono_path': 'M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z',
        'bg_default': '#E0F2FE',
        'fg_default': '#0369A1',
    },
    {
        'id': 'obras_sociales',
        'titulo': 'Obras Sociales',
        'desc': 'Análisis financiero, médicos, pacientes, catálogo y más',
        'endpoint': 'os_index',
        'emoji': '🏥',
        'tone': 'mint',
        'badge_key': None,
        'icono_path': 'M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4',
        'bg_default': '#CCFBF1',
        'fg_default': '#0F766E',
    },
    {
        'id': 'recetas_scan',
        'titulo': 'Scan recetas',
        'desc': 'Lectura OCR + cruce contra Observer',
        'endpoint': 'recetas_scan',
        'emoji': '📋',
        'tone': 'info',
        'badge_key': None,
        'icono_path': 'M3 4v6h6M21 20v-6h-6M3 4l4 4 4-4M21 20l-4-4-4 4',
        'bg_default': '#FCE7F3',
        'fg_default': '#BE185D',
    },
    {
        'id': 'compras_recurrentes',
        'titulo': 'Compras recurrentes',
        'desc': 'Patrones de compra cliente × producto',
        'endpoint': 'intelligence_recurrentes',
        'emoji': '🔮',
        'tone': 'info',
        'badge_key': None,
        'icono_path': 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z',
        'bg_default': '#F3E8FF',
        'fg_default': '#9333EA',
    },
    {
        'id': 'config',
        'titulo': 'Configuración',
        'desc': 'Ajustes del sistema',
        'endpoint': 'settings',
        'emoji': '⚙️',
        'tone': 'mute',
        'badge_key': None,
        'icono_path': 'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z',
        'bg_default': '#F3F4F6',
        'fg_default': '#6b7280',
    },
    {
        'id': 'productos_pendientes',
        'titulo': 'Productos pendientes',
        'desc': 'Items de imports sin match — revisar y resolver',
        'endpoint': 'productos_pendientes_revision',
        'emoji': '📋',
        'tone': 'warn',
        'badge_key': 'productos_pendientes_revision',
        'icono_path': 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
        'bg_default': '#FEF3C7',
        'fg_default': '#B45309',
    },
]

ACCIONES_HOME_BY_ID = {c['id']: c for c in ACCIONES_HOME}


# Categorías para agrupar las cards en el home (Diego pidió organización
# 2026-05-09: 15 cards en flat list = no encuentra nada).
# El orden de las claves dicta el orden de los grupos en pantalla.
CATEGORIAS_HOME = [
    ('operativo',  '🔄 Operativo diario'),
    ('analisis',   '📊 Análisis e informes'),
    ('catalogo',   '📚 Catálogo'),
    ('datos',      '⚙ Datos y configuración'),
    ('pendientes', '⏳ Pendientes'),
]
# Mapa card_id → categoria_key. Si una card no aparece, va a 'operativo' por default.
CARD_CATEGORIA = {
    'pedidos':              'operativo',
    'ingresos':             'operativo',
    'procesos':             'operativo',
    'reclamos':             'operativo',
    'informes':             'analisis',
    'bi':                   'analisis',
    'compras_recurrentes':  'analisis',
    'obras_sociales':       'analisis',
    'productos':            'catalogo',
    'vademecum':            'catalogo',
    'clientes':             'catalogo',
    'ofertas_import':       'datos',
    'config':               'datos',
    # Pendientes (al fondo)
    'productos_pendientes': 'pendientes',
    'recetas_scan':         'pendientes',
    'cuentas':              'pendientes',
}


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

    from database import HomeCardClick, Usuario, now_ar

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
        c['categoria'] = CARD_CATEGORIA.get(cid, 'operativo')
        out.append(c)
    return out, modo
