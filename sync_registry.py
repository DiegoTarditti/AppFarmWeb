"""Registro centralizado de tablas y su estrategia de sincronización local↔Render.

Esta es la fuente única para entender qué se sincroniza y qué no. Editar acá
cuando se agregue una tabla nueva. La pantalla /admin/sync-audit usa esta
lista para mostrar la matriz con conteos en vivo.

Categorías:
    'push_obs'      → local → Render por push_obs_to_render.py (espejo ObServer)
    'push_master'   → local → Render por push_productos_master_to_render.py (catálogo)
    'render_only'   → vive solo en Render (no tiene sentido en local)
    'local_only'    → vive solo en local (logs / locks del DockerPanel)
    'independiente' → existe en ambos pero cada lado mantiene los suyos
                      (operacional — pedidos, facturas, rendiciones, etc.)
"""

# Cada entrada: (tabla, categoria, descripcion_corta, entidad_log_opt)
# - entidad_log_opt: clave usada en obs_sync_log.entidad para buscar la última
#   sincronización. None si no aplica (independiente / sin log dedicado).
REGISTRY = [
    # ── Espejo ObServer (push automático local → Render) ──
    ('obs_laboratorios',         'push_obs', 'Catálogo de laboratorios',          'laboratorios'),
    ('obs_rubros',               'push_obs', 'Rubros',                            'rubros'),
    ('obs_subrubros',            'push_obs', 'Subrubros',                         'subrubros'),
    ('obs_nombres_drogas',       'push_obs', 'Monodrogas',                        'nombres_drogas'),
    ('obs_productos',            'push_obs', 'Productos de ObServer',             'productos'),
    ('obs_stock',                'push_obs', 'Stock por producto',                'stock'),
    ('obs_ventas_mensuales',     'push_obs', 'Ventas agregadas por mes',          'ventas_mensuales'),
    ('obs_ventas_detalle',       'push_obs', 'Detalle de ventas (por receta)',    'ventas_detalle'),
    ('obs_codigos_barras',       'push_obs', 'EANs alternativos',                 None),
    ('obs_grupos_clientes',      'push_obs', 'Grupos de clientes',                'grupos_clientes'),
    ('obs_categorias_clientes',  'push_obs', 'Categorías de clientes',            'categorias_clientes'),
    ('obs_obras_sociales',       'push_obs', 'Obras sociales',                    'obras_sociales'),
    ('obs_convenios',            'push_obs', 'Convenios OS',                      'convenios'),
    ('obs_planes',               'push_obs', 'Planes OS',                         'planes'),
    ('obs_clientes',             'push_obs', 'Clientes / afiliados',              'clientes'),
    ('obs_colegios_medicos',     'push_obs', 'Colegios médicos',                  'colegios_medicos'),
    ('obs_medicos',              'push_obs', 'Médicos',                           'medicos'),
    ('obs_medicos_matriculas',   'push_obs', 'Matrículas médicas',                'medicos_matriculas'),

    # ── Catálogo MASTER (push manual desde DockerPanel) ──
    ('laboratorios',             'push_master', 'Lista propia de labs (UPSERT por nombre)',   None),
    ('productos',                'push_master', 'Master productos: cant_fija, no_pedir, alts, PVP', None),

    # ── Operacional — vive en cada lado independiente ──
    ('configuracion',                 'independiente', 'Config singleton por DB'),
    ('proveedores',                   'independiente', 'Droguerías / proveedores'),
    ('descuentos_base',               'independiente', 'Descuentos base por lab/drog'),
    ('laboratorio_drogueria',         'independiente', 'Tabla puente lab↔drog'),
    ('proveedor_horarios_reparto',    'independiente', 'Cronograma de reparto'),
    ('proveedor_cronograma',          'independiente', 'Cronograma de pedidos'),
    ('tipo_pedido_config',            'independiente', 'Config tipos de pedido'),
    ('producto_flags',                'independiente', 'Flags por producto'),
    ('producto_codigos_barra',        'independiente', 'EANs alts en tabla normalizada'),
    ('producto_atributos',            'independiente', 'Atributos extendidos'),
    ('producto_precios_hist',         'independiente', 'Histórico de precios'),
    ('pack_equivalencias',            'independiente', 'Equivalencias pack↔unidad'),
    ('modulos',                       'independiente', 'Módulos de descuento'),
    ('modulo_packs',                  'independiente', 'Packs dentro de módulos'),
    ('pedido_borrador',               'independiente', 'Borradores de pedido'),
    ('pedidos',                       'independiente', 'Pedidos guardados'),
    ('pedido_items',                  'independiente', 'Items de pedido'),
    ('pedido_emitido',                'independiente', 'Pedidos emitidos'),
    ('pedido_emitido_item',           'independiente', 'Items emitidos'),
    ('procesos_compra',               'independiente', 'Procesos de compra'),
    ('invoice_batches',               'independiente', 'Lotes de facturas'),
    ('facturas',                      'independiente', 'Facturas recibidas'),
    ('factura_items',                 'independiente', 'Items de factura'),
    ('erp_stock',                     'independiente', 'Stock ERP (cruce)'),
    ('stock_differences',             'independiente', 'Diferencias factura↔ERP'),
    ('reclamos',                      'independiente', 'Reclamos a proveedores'),
    ('reclamo_items',                 'independiente', 'Items de reclamo'),
    ('barcode_mappings',              'independiente', 'Equivalencias EAN por proveedor'),
    ('equivalencias_proveedor',       'independiente', 'Equivalencias generales'),
    ('export_templates',              'independiente', 'Plantilla XLSX por laboratorio'),
    ('ofertas_minimo',                'independiente', 'Ofertas c/mínimo guardadas'),
    ('plantillas',                    'independiente', 'Plantillas genéricas'),
    ('plantillas_exportacion',        'independiente', 'Plantillas TXT por proveedor'),
    ('plantilla_campos',              'independiente', 'Campos de plantilla TXT'),
    ('usuarios',                      'independiente', 'Usuarios de la app'),
    ('usuario_farmacias',             'independiente', 'Acceso usuario↔farmacia'),
    ('farmacias',                     'independiente', 'Farmacias del usuario'),
    ('usuarios_pedidos',              'independiente', 'Usuarios del módulo pedidos'),
    ('estacionalidad_escenarios',     'independiente', 'Escenarios estacionalidad'),
    ('estacionalidad_productos',      'independiente', 'Productos con estacionalidad'),
    ('motivo_devolucion',             'independiente', 'Motivos de rendición'),
    ('rendicion_lote',                'independiente', 'Lotes de rendición'),
    ('vendedor_bookmark',             'independiente', 'Bookmark vendedor por usuario'),
    ('rol_filtro_obra_social',        'independiente', 'OS bloqueadas por rol'),
    ('devolucion_receta',             'independiente', 'Recetas devueltas/auditadas'),
    ('product_analytics',             'independiente', 'Métricas de uso'),
    ('home_card_clicks',              'independiente', 'Clicks de cards home'),
    ('productos_pendientes_revision', 'independiente', 'Productos a revisar (LLM matcher)'),
    ('clientes',                      'independiente', 'Clientes propios (sin ObServer)'),
    ('cliente_os_inferida',           'independiente', 'OS inferida por cliente'),
    ('pagos_ajustes_cc',              'independiente', 'Pagos / ajustes cuenta corriente'),
    ('documentos_pendientes',         'independiente', 'Bandeja de docs pendientes'),
    ('analisis_sesiones',             'independiente', 'Sesiones de análisis BI'),

    # ── Solo local (logs del DockerPanel — no sirven en Render) ──
    ('obs_sync_log',     'local_only', 'Log de syncs por entidad'),
    ('cron_log',         'local_only', 'Log del scheduler local'),
    ('sync_lock',        'local_only', 'Mutex de sync entre workers'),
    ('panel_comandos',   'local_only', 'Cola de comandos del DockerPanel'),

    # ── Solo Render ──
    ('mv_refresh_log',     'render_only', 'Log de refresh de matviews en Render'),
    ('alarmas_notificadas', 'render_only', 'Tracking de alarmas enviadas'),
]


def iter_registry():
    """Itera REGISTRY normalizando tuplas a (tabla, categoria, descripcion, entidad_log).

    Permite seguir usando 3-tuplas para entradas sin log de entidad sin tocar
    todas las filas a la vez.
    """
    for entry in REGISTRY:
        if len(entry) == 3:
            tabla, categoria, descripcion = entry
            entidad_log = None
        else:
            tabla, categoria, descripcion, entidad_log = entry
        yield tabla, categoria, descripcion, entidad_log


CATEGORIA_LABELS = {
    'push_obs':      ('🔄 Push obs_*',        '#10b981'),
    'push_master':   ('🔄 Push master',       '#06b6d4'),
    'independiente': ('⊘ Independiente',     '#94a3b8'),
    'local_only':    ('🏠 Solo local',        '#a78bfa'),
    'render_only':   ('☁ Solo Render',       '#f59e0b'),
}
