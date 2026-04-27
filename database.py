import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import DECIMAL, Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, text

_AR_TZ = timezone(timedelta(hours=-3))

def now_ar():
    """Hora actual en Argentina (UTC-3), sin tzinfo para SQLAlchemy DateTime."""
    return datetime.now(_AR_TZ).replace(tzinfo=None)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


class Config(Base):
    __tablename__ = 'configuracion'
    id = Column(Integer, primary_key=True)
    farmacia_nombre = Column(String(200), nullable=False, default='Farmacia')
    ruta_facturas = Column(String(500), nullable=True)
    # Rutas predeterminadas adicionales (todas opcionales)
    ruta_excels = Column(String(500), nullable=True)        # ofertas, módulos, ERP
    ruta_descargas = Column(String(500), nullable=True)     # destino de exports XLSX/PDF
    ruta_backups = Column(String(500), nullable=True)       # destino de pg_dump local
    ruta_plantillas_lab = Column(String(500), nullable=True)  # XLSX que vienen del laboratorio
    umbral_pico = Column(DECIMAL(4, 2), nullable=False, default=1.30)
    umbral_baja = Column(DECIMAL(4, 2), nullable=False, default=0.70)
    umbral_tendencia = Column(DECIMAL(4, 2), nullable=False, default=0.20)
    # Rotación de ventas
    rot_alta_min = Column(DECIMAL(6, 1), nullable=False, default=20.0)
    rot_alta_tol = Column(DECIMAL(6, 1), nullable=False, default=0.0)
    rot_media_min = Column(DECIMAL(6, 1), nullable=False, default=5.0)
    rot_media_tol = Column(DECIMAL(6, 1), nullable=False, default=0.0)
    rot_baja_tol = Column(DECIMAL(6, 1), nullable=False, default=0.0)
    # Keep-alive: evitar que Render duerma el servicio vía self-ping periódico
    keep_alive_enabled = Column(Boolean, nullable=False, default=False)
    keep_alive_interval_min = Column(Integer, nullable=False, default=10)
    # Ruta local al ejecutable del DockerPanel (solo usada desde localhost)
    dockerpanel_ruta = Column(String(500), nullable=True)
    # Observer: cuántos meses hacia atrás trae sync_ventas_mensuales
    observer_ventas_meses = Column(Integer, nullable=False, default=16)


class Laboratorio(Base):
    __tablename__ = 'laboratorios'
    id = Column(Integer, primary_key=True)
    nombre = Column(String(150), nullable=False, unique=True)
    activo = Column(Boolean, nullable=False, default=True)
    observer_id = Column(Integer, nullable=True, unique=True)
    creado_en = Column(DateTime, default=now_ar)


# ──────────────────────────────────────────────────────────────────────────
# Espejo de ObServer (solo las vistas DW.* que usa la app).
# La PK en cada tabla es el `observer_id` real de ObServer. Sync periódico
# desde observer_source; estas tablas nunca se editan desde la UI.
# ──────────────────────────────────────────────────────────────────────────

class ObsLaboratorio(Base):
    __tablename__ = 'obs_laboratorios'
    observer_id = Column(Integer, primary_key=True, autoincrement=False)  # DW.Laboratorios.IdLaboratorio
    descripcion = Column(String(150), nullable=False)
    fecha_baja  = Column(DateTime, nullable=True)
    sync_en     = Column(DateTime, default=now_ar)


class ObsRubro(Base):
    __tablename__ = 'obs_rubros'
    observer_id = Column(Integer, primary_key=True, autoincrement=False)  # DW.Rubros.IdRubro
    descripcion = Column(String(150), nullable=False)
    sync_en     = Column(DateTime, default=now_ar)


class ObsSubrubro(Base):
    __tablename__ = 'obs_subrubros'
    observer_id    = Column(Integer, primary_key=True, autoincrement=False)  # DW.Subrubros.IdSubrubro
    descripcion    = Column(String(150), nullable=False)
    rubro_observer = Column(Integer, ForeignKey('obs_rubros.observer_id'), nullable=True)
    sync_en        = Column(DateTime, default=now_ar)


class ObsNombreDroga(Base):
    __tablename__ = 'obs_nombres_drogas'
    observer_id = Column(Integer, primary_key=True, autoincrement=False)  # DW.NombresDrogas.IdNombresDrogas
    descripcion = Column(String(300), nullable=False)
    sync_en     = Column(DateTime, default=now_ar)


class ObsProducto(Base):
    __tablename__ = 'obs_productos'
    observer_id            = Column(Integer, primary_key=True, autoincrement=False)  # DW.Productos.IdProducto
    descripcion            = Column(String(200), nullable=False)
    laboratorio_observer   = Column(Integer, ForeignKey('obs_laboratorios.observer_id'), nullable=True, index=True)
    subrubro_observer      = Column(Integer, ForeignKey('obs_subrubros.observer_id'), nullable=True)
    nombre_droga_observer  = Column(Integer, ForeignKey('obs_nombres_drogas.observer_id'), nullable=True)
    codigo_alfabeta        = Column(String(10), nullable=True, index=True)
    id_tipo_venta_control  = Column(String(1), nullable=True, index=True)  # DW.TiposVentaYControl: L=Venta Libre, R=Bajo Receta, A=Receta Archivada, 1-4=Psicotrópico, 5-8=Estupefaciente
    # Descripción editable localmente. Si está, se muestra en lugar de `descripcion`.
    # NO se toca al sincronizar desde Observer ni al pushear a Render.
    descripcion_custom     = Column(String(200), nullable=True)
    troquel                = Column(Integer, nullable=True)
    cantidad_envase        = Column(DECIMAL(10, 3), nullable=True)
    es_habilitado_venta    = Column(Boolean, nullable=False, default=True)
    requiere_cadena_frio   = Column(Boolean, nullable=False, default=False)
    fecha_baja             = Column(DateTime, nullable=True)
    sync_en                = Column(DateTime, default=now_ar)


class ObsStock(Base):
    """Stock actual por farmacia + producto (DW.StockFarmaciasProductos)."""
    __tablename__ = 'obs_stock'
    id_farmacia = Column(Integer, primary_key=True, autoincrement=False)
    producto_observer = Column(Integer, ForeignKey('obs_productos.observer_id'), primary_key=True, autoincrement=False, index=True)
    stock_actual = Column(Integer, nullable=False, default=0)
    maximo = Column(Integer, nullable=True)
    minimo = Column(Integer, nullable=True)
    sync_en = Column(DateTime, default=now_ar)


class ObsVentaMensual(Base):
    """Ventas agregadas por (farmacia, producto, año, mes). Cache local de un
    GROUP BY sobre DW.ProductosVendidos — las vistas DW.* no traen esto agregado."""
    __tablename__ = 'obs_ventas_mensuales'
    id_farmacia       = Column(Integer, primary_key=True, autoincrement=False)
    producto_observer = Column(Integer, ForeignKey('obs_productos.observer_id'), primary_key=True, autoincrement=False, index=True)
    anio              = Column(Integer, primary_key=True, autoincrement=False)
    mes               = Column(Integer, primary_key=True, autoincrement=False)
    unidades          = Column(DECIMAL(14, 3), nullable=False, default=0)
    monto             = Column(DECIMAL(14, 2), nullable=False, default=0)
    transacciones     = Column(Integer, nullable=False, default=0)
    sync_en           = Column(DateTime, default=now_ar)


class ObsCodigoBarras(Base):
    """Tabla lookup IdProducto → CodigoBarras (EAN). Importada manualmente
    desde dbo.IdProductoCodigosBarras de ObServer (NO expuesta en schema DW).

    Resuelve el problema histórico de no tener EAN real para los productos
    de ObServer. Un producto puede tener múltiples EANs (orden 1=principal,
    2/3=alternativos).
    """
    __tablename__ = 'obs_codigos_barras'
    id_codigo_barras  = Column(Integer, primary_key=True, autoincrement=False)  # IdProductoCodigoBarras
    producto_observer = Column(Integer, ForeignKey('obs_productos.observer_id'), nullable=False, index=True)
    codigo_barras     = Column(String(20), nullable=False, index=True)
    orden             = Column(Integer, nullable=False, default=1)  # 1 = principal, 2/3 = alt
    fecha_ingreso     = Column(DateTime, nullable=True)
    fecha_baja        = Column(DateTime, nullable=True, index=True)
    sync_en           = Column(DateTime, default=now_ar)


class ObsColegioMedico(Base):
    __tablename__ = 'obs_colegios_medicos'
    observer_id     = Column(Integer, primary_key=True, autoincrement=False)  # DW.ColegiosMedicos.IdColegioMedico
    descripcion     = Column(String(100), nullable=True)
    id_provincia    = Column(String(1), nullable=True)
    id_tipo_colegio = Column(String(1), nullable=True)
    fecha_baja      = Column(DateTime, nullable=True)
    sync_en         = Column(DateTime, default=now_ar)


class ObsMedico(Base):
    __tablename__ = 'obs_medicos'
    observer_id = Column(Integer, primary_key=True, autoincrement=False)  # DW.Medicos.IdMedico
    nombre      = Column(String(100), nullable=False)
    cuit        = Column(String(11), nullable=True, index=True)
    habilitado  = Column(Boolean, nullable=True)
    fecha_baja  = Column(DateTime, nullable=True)
    sync_en     = Column(DateTime, default=now_ar)


class ObsMedicoMatricula(Base):
    __tablename__ = 'obs_medicos_matriculas'
    observer_id      = Column(Integer, primary_key=True, autoincrement=False)  # DW.MedicosMatriculas.IdMedicoMatricula
    medico_observer  = Column(Integer, ForeignKey('obs_medicos.observer_id'), nullable=False, index=True)
    matricula        = Column(String(10), nullable=True, index=True)
    colegio_observer = Column(Integer, ForeignKey('obs_colegios_medicos.observer_id'), nullable=True)
    fecha_baja       = Column(DateTime, nullable=True)
    sync_en          = Column(DateTime, default=now_ar)


class ObsVentaDetalle(Base):
    """Detalle de cada venta (cada renglón = un producto vendido en una operación).
    Subset de las 66 columnas de DW.ProductosVendidos. Sync incremental por
    FechaEstadistica. Granularidad inicial: últimos 24 meses.

    Se popula con sync_ventas_detalle(). Permite armar:
    - Ventas por OS / Plan / Convenio (cobranza, rentabilidad por OS).
    - Top médicos prescriptores.
    - Cliente → OS principal (derivada).
    - Receta reconstruida agrupando por (cliente, médico, operación, fecha).
    """
    __tablename__ = 'obs_ventas_detalle'
    id_producto_vendido            = Column(Integer, primary_key=True, autoincrement=False)  # IdProductoVendido
    id_operacion                   = Column(Integer, nullable=True, index=True)
    numero_renglon                 = Column(Integer, nullable=True)
    # Producto
    producto_observer              = Column(Integer, ForeignKey('obs_productos.observer_id'), nullable=False, index=True)
    # Quién compra
    cliente_observer               = Column(Integer, ForeignKey('obs_clientes.observer_id'), nullable=True, index=True)
    medico_observer                = Column(Integer, nullable=True, index=True)
    medico_matricula_observer      = Column(Integer, nullable=True)
    # OS / Plan
    es_venta_particular            = Column(Boolean, nullable=True)
    obra_social_observer           = Column(Integer, ForeignKey('obs_obras_sociales.observer_id'), nullable=True, index=True)
    plan_principal_observer        = Column(Integer, ForeignKey('obs_planes.observer_id'), nullable=True, index=True)
    plan_complemento1_observer     = Column(Integer, nullable=True)
    plan_complemento2_observer     = Column(Integer, nullable=True)
    plan_complemento3_observer     = Column(Integer, nullable=True)
    # Cantidades
    cantidad                       = Column(DECIMAL(10, 3), nullable=True)
    cantidad_reconocida_principal  = Column(DECIMAL(10, 3), nullable=True)
    # Importes
    importe                        = Column(DECIMAL(12, 2), nullable=True)
    importe_a_cargo_os             = Column(DECIMAL(12, 2), nullable=True)
    a_cargo_plan_principal         = Column(DECIMAL(12, 2), nullable=True)
    importe_efectivo               = Column(DECIMAL(12, 2), nullable=True)
    importe_tarjeta                = Column(DECIMAL(12, 2), nullable=True)
    importe_cheque                 = Column(DECIMAL(12, 2), nullable=True)
    importe_cuenta_corriente       = Column(DECIMAL(12, 2), nullable=True)
    # Fecha
    fecha_operacion                = Column(DateTime, nullable=True)
    fecha_estadistica              = Column(Date, nullable=True, index=True)
    anio                           = Column(Integer, nullable=True, index=True)
    mes                            = Column(Integer, nullable=True)
    dia                            = Column(Integer, nullable=True)
    # Otros
    id_farmacia                    = Column(Integer, nullable=False, index=True)
    canal_venta_observer           = Column(Integer, nullable=True)
    sync_en                        = Column(DateTime, default=now_ar)


class ObsGrupoCliente(Base):
    __tablename__ = 'obs_grupos_clientes'
    observer_id = Column(Integer, primary_key=True, autoincrement=False)  # DW.GruposClientes.IdGrupoCliente
    descripcion = Column(String(100), nullable=False)
    fecha_baja  = Column(DateTime, nullable=True)
    sync_en     = Column(DateTime, default=now_ar)


class ObsCategoriaCliente(Base):
    __tablename__ = 'obs_categorias_clientes'
    observer_id = Column(Integer, primary_key=True, autoincrement=False)  # DW.CategoriasClientes.IdCategoriaCliente
    descripcion = Column(String(100), nullable=False)
    fecha_baja  = Column(DateTime, nullable=True)
    sync_en     = Column(DateTime, default=now_ar)


class ObsObraSocial(Base):
    __tablename__ = 'obs_obras_sociales'
    observer_id = Column(Integer, primary_key=True, autoincrement=False)  # DW.ObrasSociales.IdObraSocial
    descripcion = Column(String(150), nullable=False)
    fecha_baja  = Column(DateTime, nullable=True)
    sync_en     = Column(DateTime, default=now_ar)


class ObsConvenio(Base):
    __tablename__ = 'obs_convenios'
    observer_id         = Column(Integer, primary_key=True, autoincrement=False)  # DW.Convenios.IdConvenio
    descripcion         = Column(String(200), nullable=True)
    obra_social_observer = Column(Integer, ForeignKey('obs_obras_sociales.observer_id'), nullable=True, index=True)
    fecha_baja          = Column(DateTime, nullable=True)
    sync_en             = Column(DateTime, default=now_ar)


class ObsPlan(Base):
    __tablename__ = 'obs_planes'
    observer_id       = Column(Integer, primary_key=True, autoincrement=False)  # DW.Planes.IdPlan
    descripcion       = Column(String(150), nullable=False)
    convenio_observer = Column(Integer, ForeignKey('obs_convenios.observer_id'), nullable=True, index=True)
    habilitado        = Column(Boolean, nullable=False, default=True)
    fecha_baja        = Column(DateTime, nullable=True)
    sync_en           = Column(DateTime, default=now_ar)


class ObsCliente(Base):
    __tablename__ = 'obs_clientes'
    observer_id           = Column(Integer, primary_key=True, autoincrement=False)  # DW.Clientes.IdCliente
    apellido_nombre       = Column(String(100), nullable=False)
    documento_tipo        = Column(String(3),  nullable=True)
    documento_numero      = Column(Integer,    nullable=True, index=True)
    domicilio_cp          = Column(String(10), nullable=True)
    domicilio_direccion   = Column(String(100), nullable=True)
    localidad             = Column(String(100), nullable=True)
    provincia             = Column(String(1),  nullable=True)
    grupo_observer        = Column(Integer, ForeignKey('obs_grupos_clientes.observer_id'), nullable=True, index=True)
    categoria_observer    = Column(Integer, ForeignKey('obs_categorias_clientes.observer_id'), nullable=True, index=True)
    id_farmacia           = Column(Integer, nullable=False)
    telefono              = Column(String(35), nullable=True)
    sync_en               = Column(DateTime, default=now_ar)


class Cliente(Base):
    """Extensión local editable de ObsCliente (notas, contacto, tags).
    Se vincula 1:1 por observer_id al cliente de ObServer."""
    __tablename__ = 'clientes'
    id = Column(Integer, primary_key=True)
    observer_id = Column(Integer, ForeignKey('obs_clientes.observer_id'),
                         nullable=False, unique=True, index=True)
    notas = Column(Text, nullable=True)
    tags = Column(String(200), nullable=True)  # comma-separated
    whatsapp = Column(String(30), nullable=True)
    email = Column(String(120), nullable=True)
    fecha_nacimiento = Column(Date, nullable=True)
    creado_en = Column(DateTime, default=now_ar)
    actualizado_en = Column(DateTime, default=now_ar, onupdate=now_ar)
    obs_cliente = relationship('ObsCliente')


class ObsSyncLog(Base):
    """Log de cada corrida de sync por entidad (última ejecución + resultados)."""
    __tablename__ = 'obs_sync_log'
    id = Column(Integer, primary_key=True)
    entidad = Column(String(40), nullable=False, index=True)   # 'laboratorios', 'productos', etc.
    filas_upsert = Column(Integer, nullable=False, default=0)
    duracion_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    ejecutado_en = Column(DateTime, default=now_ar)


class CronLog(Base):
    """Registro unificado de procesos automáticos (sync, refresh, push, agente, etc.).

    Cada ejecución crea una fila al iniciar (estado='corriendo') y la actualiza
    al terminar con 'ok' o 'error'. Se purga > 7 días.
    """
    __tablename__ = 'cron_log'
    id = Column(Integer, primary_key=True)
    proceso = Column(String(80), nullable=False, index=True)  # 'sync_ventas', 'push_render', 'mv_refresh', etc.
    origen = Column(String(40), nullable=True)  # 'web', 'dockerpanel', 'manual'
    inicio = Column(DateTime, default=now_ar, nullable=False, index=True)
    fin = Column(DateTime, nullable=True)
    duracion_ms = Column(Integer, nullable=True)
    estado = Column(String(15), nullable=False, default='corriendo')  # corriendo / ok / error
    mensaje = Column(Text, nullable=True)
    error = Column(Text, nullable=True)


class MvRefreshLog(Base):
    """Log de cada refresh de una vista materializada.

    Permite mostrar al usuario cuán frescos son los datos cacheados.
    """
    __tablename__ = 'mv_refresh_log'
    id = Column(Integer, primary_key=True)
    view_name = Column(String(80), nullable=False, index=True)
    refrescada_en = Column(DateTime, default=now_ar, nullable=False)
    duracion_ms = Column(Integer, nullable=True)
    filas = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)


class ExportTemplate(Base):
    __tablename__ = 'export_templates'
    laboratorio_id = Column(Integer, ForeignKey('laboratorios.id'), primary_key=True)
    columns_json  = Column(Text, nullable=False, default='[]')
    custom_header = Column(String(200))


class OfertaMinimo(Base):
    """Tabla de descuentos por producto + lab.

    El nombre histórico es 'minimo' pero hoy contiene los DOS tipos:
    - tipo_descuento='simple': descuento sin mínimo de unidades (unidades_minima=NULL).
    - tipo_descuento='con_minimo': el descuento solo aplica si se compra >= unidades_minima.

    El análisis de pedido (`/order/<id>`) lee de acá y filtra por lab + tipo.
    """
    __tablename__ = 'ofertas_minimo'
    id              = Column(Integer, primary_key=True)
    laboratorio_id  = Column(Integer, ForeignKey('laboratorios.id'), nullable=False)
    ean             = Column(String(20), nullable=False)
    descripcion     = Column(String(300))
    codigo          = Column(String(50))
    unidades_minima = Column(Integer)
    descuento_psl   = Column(DECIMAL(6, 2))
    rentabilidad    = Column(DECIMAL(6, 2))
    plazo_pago      = Column(String(100))
    grupo_id        = Column(Integer)
    tipo_descuento  = Column(String(20))   # 'simple' | 'con_minimo'
    actualizado_en  = Column(DateTime, default=now_ar)


class Provider(Base):
    __tablename__ = 'proveedores'
    id = Column(Integer, primary_key=True)
    razon_social = Column(String(100), nullable=False)
    cuit = Column(String(20))
    domicilio = Column(String(200))
    parser_file = Column(String(100))
    match_strategy = Column(String(20), nullable=False, default='barcode')
    ruta_facturas = Column(String(500), nullable=True)
    grabar_productos = Column(Integer, nullable=False, default=1)
    tipo = Column(String(20), nullable=False, default='drogueria')
    activo = Column(Boolean, nullable=False, default=True)
    # Mínimo de compra para que la droguería acepte el pedido. NULL = sin mínimo.
    compra_minima_pesos = Column(DECIMAL(14, 2), nullable=True)
    claims = relationship('Claim', back_populates='provider')


class DescuentoBase(Base):
    """Descuento base acordado entre laboratorio y droguería (acuerdo anual/semestral).
    Es el primer nivel de descuento. Se acumula multiplicativamente con transfers,
    ofertas por producto y módulos.

    UNIQUE(laboratorio_id, drogueria_id): un único descuento base por combinación.
    """
    __tablename__ = 'descuentos_base'
    id              = Column(Integer, primary_key=True)
    laboratorio_id  = Column(Integer, ForeignKey('laboratorios.id'), nullable=False, index=True)
    drogueria_id    = Column(Integer, ForeignKey('proveedores.id'),  nullable=False, index=True)
    descuento_pct   = Column(DECIMAL(5, 2), nullable=False)  # Ej: 31.03 = 31.03%
    plazo_pago      = Column(String(50), nullable=True)      # "30 dias", "contado", "45 dias"
    vigencia_desde  = Column(Date, nullable=True)
    vigencia_hasta  = Column(Date, nullable=True)
    activo          = Column(Boolean, nullable=False, default=True)  # Para pausar sin borrar
    observacion     = Column(Text, nullable=True)
    creado_en       = Column(DateTime, default=now_ar)
    actualizado_en  = Column(DateTime, default=now_ar, onupdate=now_ar)
    laboratorio = relationship('Laboratorio')
    drogueria   = relationship('Provider')
    __table_args__ = (
        UniqueConstraint('laboratorio_id', 'drogueria_id', name='uq_desc_base_lab_drog'),
    )


class InvoiceBatch(Base):
    __tablename__ = 'invoice_batches'
    id = Column(Integer, primary_key=True)
    proveedor_id = Column(Integer, ForeignKey('proveedores.id'), nullable=False)
    erp_filename = Column(String(200))
    fecha = Column(DateTime, default=now_ar)
    estado = Column(String(20), nullable=False, default='PENDIENTE')
    invoices = relationship('Invoice', back_populates='batch')


class Invoice(Base):
    __tablename__ = 'facturas'
    id = Column(Integer, primary_key=True)
    numero_factura = Column(String(20), nullable=False)
    fecha = Column(Date, nullable=False)
    proveedor_razon = Column(String(100))
    proveedor_cuit = Column(String(20))
    proveedor_domicilio = Column(String(200))
    cliente_codigo = Column(String(20))
    cliente_razon = Column(String(100))
    tipo_comprobante = Column(String(5), nullable=False, default='FAC')
    total = Column(DECIMAL(14, 2))
    total_articulos = Column(Integer)
    total_unidades = Column(Integer)
    pdf_filename = Column(String(200))
    erp_filename = Column(String(200))
    batch_id = Column(Integer, ForeignKey('invoice_batches.id'), nullable=True)
    conciliado = Column(Boolean, nullable=False, default=False)
    # Desglose fiscal (opcional, cargado desde converter o /verify)
    monto_exento  = Column(DECIMAL(14, 2), nullable=True)
    monto_gravado = Column(DECIMAL(14, 2), nullable=True)
    iva_105       = Column(DECIMAL(14, 2), nullable=True)
    iva_21        = Column(DECIMAL(14, 2), nullable=True)
    percepciones  = Column(DECIMAL(14, 2), nullable=True)
    otros         = Column(DECIMAL(14, 2), nullable=True)
    creado_en = Column(DateTime, default=now_ar)
    items = relationship('InvoiceItem', back_populates='invoice')
    batch = relationship('InvoiceBatch', back_populates='invoices')


class InvoiceItem(Base):
    __tablename__ = 'factura_items'
    id = Column(Integer, primary_key=True)
    factura_id = Column(Integer, ForeignKey('facturas.id'), nullable=False, index=True)
    codigo_barra = Column(String(20))
    cantidad = Column(Integer)
    descripcion = Column(String(150))
    precio_unitario = Column(DECIMAL(14, 2))
    precio_erp = Column(DECIMAL(14, 2))
    dto = Column(DECIMAL(6, 2))
    importe = Column(DECIMAL(14, 2))
    categoria = Column(String(50))
    lote = Column(String(30))
    vencimiento = Column(String(20))
    invoice = relationship('Invoice', back_populates='items')


class ErpStock(Base):
    __tablename__ = 'erp_stock'
    id = Column(Integer, primary_key=True)
    codigo_barra = Column(String(20), nullable=False, index=True)
    descripcion = Column(String(150))
    cantidad = Column(Integer)
    precio_unitario = Column(DECIMAL(14, 2))


class StockDifference(Base):
    __tablename__ = 'stock_differences'
    id = Column(Integer, primary_key=True)
    factura_id = Column(Integer, ForeignKey('facturas.id'), nullable=False, index=True)
    codigo_barra = Column(String(20))
    descripcion = Column(String(150))
    cantidad_factura = Column(Integer)
    cantidad_erp = Column(Integer)
    diferencia = Column(Integer)
    observaciones = Column(Text)
    claim_items = relationship('ClaimItem', back_populates='difference')


class Claim(Base):
    __tablename__ = 'reclamos'
    id = Column(Integer, primary_key=True)
    proveedor_id = Column(Integer, ForeignKey('proveedores.id'), nullable=False)
    factura_id = Column(Integer, ForeignKey('facturas.id'), index=True)
    numero_factura = Column(String(20))
    fecha = Column(Date, nullable=False)
    estado = Column(String(20), nullable=False, default='ABIERTO')
    creado_en = Column(DateTime, default=now_ar)
    provider = relationship('Provider', back_populates='claims')
    factura = relationship('Invoice')
    items = relationship('ClaimItem', back_populates='claim')


class ClaimItem(Base):
    __tablename__ = 'reclamo_items'
    id = Column(Integer, primary_key=True)
    reclamo_id = Column(Integer, ForeignKey('reclamos.id'), nullable=False)
    diferencia_id = Column(Integer, ForeignKey('stock_differences.id'), nullable=True)
    codigo_barra = Column(String(20))
    descripcion = Column(String(150))
    cantidad_factura = Column(Integer)
    cantidad_erp = Column(Integer)
    diferencia = Column(Integer)
    observaciones = Column(Text)
    claim = relationship('Claim', back_populates='items')
    difference = relationship('StockDifference', back_populates='claim_items')


# DescuentoCampana / DescuentoModulo / DescuentoModuloItem fueron eliminados
# (legacy, primer prototipo de gestión de campañas que nunca se usó en producción).
# Las tablas se dropean en init_db si todavía existen en el schema.


class BarcodeMapping(Base):
    __tablename__ = 'barcode_mappings'
    id = Column(Integer, primary_key=True)
    proveedor_id = Column(Integer, ForeignKey('proveedores.id'), nullable=False)
    codigo_barra_factura = Column(String(20), nullable=False)
    codigo_barra_erp = Column(String(20), nullable=False)
    descripcion_factura = Column(String(150))
    descripcion_erp = Column(String(150))
    creado_en = Column(DateTime, default=now_ar)


class Producto(Base):
    __tablename__ = 'productos'
    id = Column(Integer, primary_key=True)
    codigo_barra = Column(String(20), nullable=False, unique=True)
    descripcion = Column(String(200))
    codigo_barra_alt1 = Column(String(20), index=True)
    codigo_barra_alt2 = Column(String(20), index=True)
    codigo_barra_alt3 = Column(String(20), index=True)
    es_pack = Column(Integer, nullable=False, default=0)   # 1 = es un pack de unidades
    precio_pvp = Column(DECIMAL(14, 2))
    laboratorio_id = Column(Integer, ForeignKey('laboratorios.id'), nullable=True)
    laboratorio = relationship('Laboratorio')
    # Puente a ObServer: único nexo entre EAN (local) y IdProducto (ObServer)
    # unique=True: un observer_id NO puede estar asignado a más de un producto local.
    # NULL permitido múltiple (productos todavía no vinculados a ObServer).
    observer_id = Column(Integer, ForeignKey('obs_productos.observer_id'),
                         nullable=True, unique=True, index=True)
    obs_producto = relationship('ObsProducto')
    # Código Alfabeta (vademécum). Bridge robusto con obs_productos.codigo_alfabeta.
    codigo_alfabeta = Column(String(10), nullable=True, index=True)
    monodroga = Column(String(200), nullable=True)
    presentacion = Column(String(500), nullable=True)
    accion_terapeutica = Column(String(200), nullable=True)
    actualizado_en = Column(DateTime, default=now_ar)
    ultima_compra = Column(Date, nullable=True)


class Modulo(Base):
    """Módulo de descuento: agrupación de packs de un laboratorio."""
    __tablename__ = 'modulos'
    id = Column(Integer, primary_key=True)
    nombre = Column(String(200), nullable=False)
    laboratorio_id = Column(Integer, ForeignKey('laboratorios.id'), nullable=True)
    lista_nombre = Column(String(200), nullable=True)  # Nombre de la lista/importación a la que pertenece
    creado_en = Column(DateTime, default=now_ar)
    activo = Column(Boolean, default=False, nullable=False, server_default='false')
    laboratorio = relationship('Laboratorio')
    packs = relationship('ModuloPack', back_populates='modulo', cascade='all, delete-orphan')


class ModuloPack(Base):
    """Pack de módulo: un EAN del proveedor equivale a N unidades de otro EAN del ERP."""
    __tablename__ = 'modulo_packs'
    id = Column(Integer, primary_key=True)
    ean_pack = Column(String(30), nullable=False)   # EAN del pack (proveedor/módulo)
    ean_unidad = Column(String(30), nullable=False)              # EAN de la unidad individual (ERP/pedido)
    cantidad = Column(Integer, nullable=False, default=1)        # Unidades individuales por pack
    cant_modulo = Column(Integer, nullable=True)                 # Cant. de packs en el módulo (CANT. del Excel)
    desc_pct = Column(DECIMAL(5, 2), nullable=True)             # Descuento % del módulo (DESC.% del Excel)
    descripcion = Column(String(255))
    modulo_id = Column(Integer, ForeignKey('modulos.id'), nullable=True)
    creado_en = Column(DateTime, default=now_ar)
    modulo = relationship('Modulo', back_populates='packs')


class Pedido(Base):
    __tablename__ = 'pedidos'
    id = Column(Integer, primary_key=True)
    laboratorio = Column(String(150), nullable=False)
    farmacia = Column(String(200))
    periodo = Column(String(100))
    n_days = Column(Integer)
    analisis_sesion_id = Column(Integer, ForeignKey('analisis_sesiones.id'), nullable=True)
    # Canal de compra: 'laboratorio' (directo) o 'drogueria' (vía proveedor).
    # None = todavía no decidido. Se setea al elegir droguería o lab en el resumen.
    canal = Column(String(12), nullable=True)
    # partner_id apunta a proveedores.id cuando canal='drogueria', o al lab cuando canal='laboratorio'
    # (en la tabla proveedores si el lab está registrado; de lo contrario queda None y se usa
    # el campo `laboratorio` como identificador).
    partner_id = Column(Integer, nullable=True, index=True)
    canal_elegido_en = Column(DateTime, nullable=True)
    creado_en = Column(DateTime, default=now_ar)
    analizado_en = Column(DateTime, nullable=True)
    estado = Column(String(20), nullable=False, default='PENDIENTE')
    analisis_json = Column(Text, nullable=True)
    analisis_guardado_en = Column(DateTime, nullable=True)
    items = relationship('PedidoItem', back_populates='pedido', cascade='all, delete-orphan')
    analisis_sesion = relationship('AnalisisSesion')


class PedidoItem(Base):
    __tablename__ = 'pedido_items'
    id = Column(Integer, primary_key=True)
    pedido_id = Column(Integer, ForeignKey('pedidos.id'), nullable=False)
    codigo_barra = Column(String(20))
    nombre = Column(String(200))
    cantidad = Column(Integer, nullable=False, default=0)
    precio_pvp = Column(DECIMAL(14, 2))
    subtotal = Column(DECIMAL(14, 2))
    rotacion = Column(String(1), nullable=True)       # A/M/B
    avg_monthly = Column(DECIMAL(10, 2), nullable=True)
    pedido = relationship('Pedido', back_populates='items')


class ProcesoCompra(Base):
    """Ciclo de compra: análisis → pedido → factura → cruce → (reclamo) → cierre."""
    __tablename__ = 'procesos_compra'
    id = Column(Integer, primary_key=True)
    tipo = Column(String(20), nullable=False)             # 'laboratorio' | 'drogueria'
    partner_id = Column(Integer, nullable=True)
    partner_nombre = Column(String(200), nullable=False)
    estado = Column(String(20), nullable=False, default='BORRADOR')
    pedido_id = Column(Integer, ForeignKey('pedidos.id', ondelete='SET NULL'), nullable=True)
    factura_id = Column(Integer, ForeignKey('facturas.id', ondelete='SET NULL'), nullable=True)
    reclamo_id = Column(Integer, ForeignKey('reclamos.id', ondelete='SET NULL'), nullable=True)
    analisis_sesion_id = Column(Integer, ForeignKey('analisis_sesiones.id'), nullable=True)
    analisis_periodo = Column(String(100))
    analisis_hecho_en = Column(DateTime, nullable=True)
    analisis_pasos_json = Column(Text, nullable=True)     # {modulos:{hecho,cant},ofertas:{...},...}
    pedido_hecho_en = Column(DateTime, nullable=True)
    factura_hecha_en = Column(DateTime, nullable=True)
    cruce_hecho_en = Column(DateTime, nullable=True)
    reclamo_hecho_en = Column(DateTime, nullable=True)
    cerrado_en = Column(DateTime, nullable=True)
    notas = Column(Text, nullable=True)
    creado_en = Column(DateTime, default=now_ar)
    actualizado_en = Column(DateTime, default=now_ar)


class PagoAjusteCC(Base):
    """Pagos y ajustes de cuenta corriente de proveedores."""
    __tablename__ = 'pagos_ajustes_cc'
    id = Column(Integer, primary_key=True)
    proveedor_id = Column(Integer, ForeignKey('proveedores.id', ondelete='CASCADE'), nullable=False)
    tipo = Column(String(10), nullable=False)  # PAGO, AJUSTE_POS, AJUSTE_NEG
    fecha = Column(Date, nullable=False)
    monto = Column(DECIMAL(14, 2), nullable=False)
    numero_comprobante = Column(String(30))
    observaciones = Column(Text)
    conciliado = Column(Boolean, nullable=False, default=False)
    creado_en = Column(DateTime, default=now_ar)
    proveedor = relationship('Provider')


class DocumentoPendiente(Base):
    """Documentos detectados en carpeta pendientes, listos para procesar."""
    __tablename__ = 'documentos_pendientes'
    id = Column(Integer, primary_key=True)
    filename = Column(String(300), nullable=False)
    ruta_completa = Column(String(500), nullable=False)
    fecha_detectado = Column(DateTime, default=now_ar)
    estado = Column(String(20), nullable=False, default='PENDIENTE')  # PENDIENTE / PROCESADO
    proveedor_id = Column(Integer, ForeignKey('proveedores.id'), nullable=True)
    factura_id = Column(Integer, ForeignKey('facturas.id'), nullable=True)
    creado_en = Column(DateTime, default=now_ar)
    proveedor = relationship('Provider')
    factura = relationship('Invoice')


class ProductAnalytics(Base):
    """Snapshot del último análisis de ventas por producto."""
    __tablename__ = 'product_analytics'
    codigo_barra = Column(String(20), primary_key=True)
    descripcion = Column(String(200))
    laboratorio = Column(String(150), nullable=True)
    stock = Column(Integer, nullable=False, default=0)
    avg_monthly = Column(DECIMAL(10, 2), nullable=False, default=0)
    rotacion = Column(String(1), nullable=True)     # A/M/B
    slope = Column(DECIMAL(10, 4), nullable=True)   # tendencia por mes
    forecast_next = Column(DECIMAL(10, 2), nullable=True)
    sin_mov_60d = Column(Integer, nullable=False, default=0)
    precio_pvp = Column(DECIMAL(14, 2), nullable=True)
    tipo = Column(String(1), nullable=True)            # C=crónico, N=normal
    ventas_json = Column(Text, nullable=True)           # JSON: array de 12 valores mensuales
    start_month = Column(Integer, nullable=True)        # mes de inicio (1-12)
    n_days = Column(Integer, nullable=True)             # días del período analizado
    actualizado_en = Column(DateTime, default=now_ar)


class Usuario(Base):
    """Usuarios de la aplicación con rol y permisos."""
    __tablename__ = 'usuarios'
    id = Column(Integer, primary_key=True)
    username = Column(String(50), nullable=False, unique=True)
    email = Column(String(150))
    password_hash = Column(String(255), nullable=False)
    nombre_completo = Column(String(200))
    rol = Column(String(20), nullable=False, default='remoto')   # farmacia | dev | remoto | admin
    permisos_json = Column(Text, nullable=False, default='{}')   # {"facturas":"editar","stock":"ver",...}
    activo = Column(Boolean, nullable=False, default=True)
    debe_cambiar_password = Column(Boolean, nullable=False, default=False)
    ultimo_login = Column(DateTime, nullable=True)
    creado_en = Column(DateTime, default=now_ar)
    # {"modo":"auto|fijo","orden":[...ids],"colores":{id:"#xxx"},"ocultos":[...ids]}
    preferencias_home_json = Column(Text, nullable=True)

    def get_id(self):
        return str(self.id)

    @property
    def is_authenticated(self):
        return True

    @property
    def is_active(self):
        return self.activo

    @property
    def is_anonymous(self):
        return False


class HomeCardClick(Base):
    """Tracking de clicks en las cards de 'Acciones frecuentes' del home.
    Alimenta el modo Auto que rankea por uso reciente."""
    __tablename__ = 'home_card_clicks'
    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey('usuarios.id'), nullable=False, index=True)
    card_id = Column(String(40), nullable=False, index=True)
    clicked_at = Column(DateTime, default=now_ar, nullable=False, index=True)


class ProductoPrecioHist(Base):
    """Snapshot de precio por producto + proveedor en cada factura importada.
    Append-only: cada fila es un punto histórico.
    """
    __tablename__ = 'producto_precios_hist'
    id = Column(Integer, primary_key=True)
    codigo_barra     = Column(String(20), nullable=False, index=True)
    proveedor_id     = Column(Integer, ForeignKey('proveedores.id', ondelete='CASCADE'), nullable=True, index=True)
    proveedor_razon  = Column(String(150), nullable=True)  # fallback si no hay proveedor_id
    fecha            = Column(Date, nullable=False, index=True)  # fecha de la factura
    precio_publico   = Column(DECIMAL(14, 2), nullable=True)
    dto_pct          = Column(DECIMAL(6, 2),  nullable=True)
    precio_unitario  = Column(DECIMAL(14, 2), nullable=True)
    importe          = Column(DECIMAL(14, 2), nullable=True)
    factura_id       = Column(Integer, ForeignKey('facturas.id', ondelete='SET NULL'), nullable=True)
    tipo_comprobante = Column(String(5), nullable=True)  # FAC / NCR (para filtrar)
    creado_en        = Column(DateTime, default=now_ar)


class AnalisisSesion(Base):
    """Registro de cada ejecución de análisis de ventas."""
    __tablename__ = 'analisis_sesiones'
    id = Column(Integer, primary_key=True)
    laboratorio_nombre = Column(String(150), nullable=False)
    laboratorio_id = Column(Integer, ForeignKey('laboratorios.id'), nullable=True)
    periodo = Column(String(100))
    farmacia = Column(String(200))
    n_days = Column(Integer, nullable=False)
    fuente = Column(String(20), nullable=False, default='pdf')  # pdf | xls | html | observer
    n_productos = Column(Integer, nullable=False, default=0)
    creado_en = Column(DateTime, default=now_ar)
    laboratorio = relationship('Laboratorio')


class PlantillaExportacion(Base):
    """Formato de exportación de ancho fijo para un proveedor."""
    __tablename__ = 'plantillas_exportacion'
    id = Column(Integer, primary_key=True)
    proveedor_id = Column(Integer, ForeignKey('proveedores.id'), nullable=False)
    nombre = Column(String(100), nullable=False, default='Plantilla')
    extension = Column(String(10), nullable=False, default='txt')
    creado_en = Column(DateTime, default=now_ar)
    proveedor = relationship('Provider')
    campos = relationship('PlantillaCampo', back_populates='plantilla',
                          order_by='PlantillaCampo.col_inicio',
                          cascade='all, delete-orphan')


class PlantillaCampo(Base):
    """Campo de ancho fijo dentro de una PlantillaExportacion."""
    __tablename__ = 'plantilla_campos'
    id = Column(Integer, primary_key=True)
    plantilla_id = Column(Integer, ForeignKey('plantillas_exportacion.id'), nullable=False)
    nombre = Column(String(80), nullable=False)
    campo_sistema = Column(String(30), nullable=False)
    col_inicio = Column(Integer, nullable=False, default=0)
    longitud = Column(Integer, nullable=False, default=1)
    valor_fijo = Column(String(100), nullable=True)
    alineacion = Column(String(1), nullable=False, default='L')
    relleno = Column(String(1), nullable=False, default=' ')
    plantilla = relationship('PlantillaExportacion', back_populates='campos')


class Plantilla(Base):
    """Plantilla unificada para las 3 entidades (laboratorio/drogueria/proveedor).
    Reemplaza gradualmente ExportTemplate + PlantillaExportacion/PlantillaCampo.
    config_json guarda columnas (xlsx) o lista de campos con col_inicio/longitud (txt_fijo)."""
    __tablename__ = 'plantillas'
    id = Column(Integer, primary_key=True)
    entidad_tipo = Column(String(20), nullable=False)  # laboratorio | drogueria | proveedor
    entidad_id = Column(Integer, nullable=False)
    nombre = Column(String(100), nullable=False)
    formato = Column(String(20), nullable=False, default='xlsx')  # xlsx | txt_fijo | csv
    tipo_doc = Column(String(30), nullable=False, default='pedido')  # pedido | recepcion | descuento
    config_json = Column(Text, nullable=False, default='{}')
    es_default = Column(Boolean, nullable=False, default=False)
    actualizada_en = Column(DateTime, default=now_ar, onupdate=now_ar)


CAMPOS_SISTEMA = [
    ('fijo',            'Valor fijo / constante'),
    ('codigo_barra',    'Código de barra (EAN)'),
    ('descripcion',     'Descripción del producto'),
    ('cantidad',        'Cantidad total (mod+oferta+sin deal)'),
    ('cant_modulo',     'Cantidad módulo'),
    ('cant_oferta',     'Cantidad oferta'),
    ('cant_oferta_min', 'Cantidad oferta c/mín'),
    ('cant_nodeal',     'Cantidad sin deal'),
    ('precio',          'Precio PVP'),
    ('erp_qty',         'Stock ERP'),
    ('rotacion',        'Rotación'),
    ('avg_monthly',     'Promedio mensual'),
    ('espacio',         'Espacio en blanco'),
]


engine = None
SessionLocal = None


from contextlib import contextmanager


@contextmanager
def get_db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db(database_url=None):
    global engine, SessionLocal
    database_url = database_url or os.environ.get('DATABASE_URL', 'sqlite:///farmacia.db')
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    engine = create_engine(database_url, echo=False, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                               expire_on_commit=False)
    if not database_url.startswith('sqlite'):
        # Limpia zombies en pg_type / pg_class que bloquean CREATE TABLE con
        # "duplicate key ... pg_type_typname_nsp_index". Puede pasar en Render
        # cuando un deploy previo dejó un pg_type huérfano sin tabla real.
        # Usamos AUTOCOMMIT para que cada DDL se confirme aunque el siguiente falle.
        zombie_names = ('export_templates', 'ofertas_minimo', 'procesos_compra',
                        'analisis_sesiones', 'usuarios',
                        'plantillas_exportacion', 'plantilla_campos',
                        'plantillas', 'producto_precios_hist',
                        'obs_laboratorios', 'obs_rubros', 'obs_subrubros',
                        'obs_nombres_drogas', 'obs_productos', 'obs_stock',
                        'obs_sync_log', 'obs_ventas_mensuales',
                        'home_card_clicks',
                        'obs_grupos_clientes', 'obs_categorias_clientes',
                        'obs_obras_sociales', 'obs_convenios', 'obs_planes',
                        'obs_clientes', 'clientes',
                        'cron_log', 'mv_refresh_log',
                        'obs_colegios_medicos', 'obs_medicos',
                        'obs_medicos_matriculas', 'obs_ventas_detalle',
                        'descuentos_base', 'obs_codigos_barras')
        with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            for tname in zombie_names:
                # ¿Hay una tabla real (relkind='r') con ese nombre? Si sí, no tocar nada.
                real_table = conn.execute(text("""
                    SELECT 1 FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relname = :t AND c.relkind = 'r'
                """), {'t': tname}).first()
                if real_table:
                    continue
                # No hay tabla real pero puede quedar pg_type / secuencia / vista huérfana.
                for ddl in (f'DROP TABLE IF EXISTS "{tname}" CASCADE',
                            f'DROP TYPE IF EXISTS "{tname}" CASCADE',
                            f'DROP SEQUENCE IF EXISTS "{tname}_id_seq" CASCADE'):
                    try:
                        conn.execute(text(ddl))
                    except Exception:
                        pass
    Base.metadata.create_all(engine)
    is_sqlite = database_url.startswith('sqlite')
    # Migraciones incrementales: agrega columnas nuevas si no existen
    with engine.connect() as conn:
        if is_sqlite:
            _sqlite_add_columns(conn)
        else:
            _pg_add_columns(conn)
            _crear_matviews(conn)
        conn.commit()

    # One-shot: importar plantillas legacy a la tabla plantillas nueva
    _migrate_legacy_plantillas()


def _crear_matviews(conn):
    """Crea las vistas materializadas (una vez, idempotente).

    NO hace REFRESH automático — eso queda a cargo del cron del DockerPanel
    o de un endpoint manual. La vista nace vacía y se llena al primer refresh.
    """
    # Stats agregados por monodroga (powers /estadisticas/drogas).
    # Ventana móvil 12m basada en CURRENT_DATE al momento del refresh.
    conn.execute(text("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS mv_stats_drogas AS
        WITH ventana AS (
            SELECT
                EXTRACT(YEAR FROM CURRENT_DATE)::INT * 100 +
                EXTRACT(MONTH FROM CURRENT_DATE)::INT AS hasta,
                EXTRACT(YEAR FROM (CURRENT_DATE - INTERVAL '11 months'))::INT * 100 +
                EXTRACT(MONTH FROM (CURRENT_DATE - INTERVAL '11 months'))::INT AS desde_12m,
                EXTRACT(YEAR FROM (CURRENT_DATE - INTERVAL '2 months'))::INT * 100 +
                EXTRACT(MONTH FROM (CURRENT_DATE - INTERVAL '2 months'))::INT AS desde_3m
        )
        SELECT
            v.id_farmacia,
            p.nombre_droga_observer AS droga_id,
            COUNT(DISTINCT p.laboratorio_observer)::INT AS labs,
            COUNT(DISTINCT p.observer_id)::INT AS prods,
            SUM(CASE WHEN (v.anio*100 + v.mes) BETWEEN ventana.desde_3m AND ventana.hasta
                     THEN v.unidades ELSE 0 END)::NUMERIC(14,3) AS u3m,
            SUM(v.unidades)::NUMERIC(14,3) AS u12m,
            SUM(v.monto)::NUMERIC(14,2) AS m12m
        FROM obs_productos p
        JOIN obs_ventas_mensuales v ON v.producto_observer = p.observer_id
        CROSS JOIN ventana
        WHERE p.fecha_baja IS NULL
          AND p.nombre_droga_observer IS NOT NULL
          AND (v.anio*100 + v.mes) BETWEEN ventana.desde_12m AND ventana.hasta
        GROUP BY v.id_farmacia, p.nombre_droga_observer
        HAVING SUM(v.unidades) > 0
        WITH NO DATA
    """))
    # Index único requerido para REFRESH MATERIALIZED VIEW CONCURRENTLY.
    conn.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_stats_drogas
        ON mv_stats_drogas (id_farmacia, droga_id)
    """))
    # Index para ORDER BY u12m DESC (lectura en /estadisticas/drogas).
    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_mv_stats_drogas_u12m
        ON mv_stats_drogas (id_farmacia, u12m DESC)
    """))

    # Trigram index para búsquedas ilike '%...%' en obs_productos.descripcion
    # (200k+ filas → con ilike sin index hace full scan; con GIN trigram cae a
    # decenas de ms). Usado en /obs/productos, modulo_packs, pack_detector,
    # purchase. Si la extensión pg_trgm no está disponible, se loggea y se
    # sigue (no bloquea init_db).
    try:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_obs_productos_descripcion_trgm
            ON obs_productos USING gin (descripcion gin_trgm_ops)
        """))
    except Exception as e:
        # Permisos insuficientes para CREATE EXTENSION (raro en Render pero
        # posible en DBs gestionadas con superuser limitado).
        print(f'[init_db] aviso: no pude crear trigram index — {e}')


def _migrate_legacy_plantillas():
    """Copia ExportTemplate (lab XLSX) y PlantillaExportacion (prov TXT fijo)
    a la tabla `plantillas` nueva. Idempotente: saltea si ya hay una plantilla
    para la misma entidad con origen legacy (nombre empieza con '[legacy] ')."""
    import json
    session = SessionLocal()
    try:
        # Labs (ExportTemplate) → xlsx
        for et in session.query(ExportTemplate).all():
            exists = session.query(Plantilla).filter_by(
                entidad_tipo='laboratorio', entidad_id=et.laboratorio_id,
                nombre='[legacy] Plantilla XLSX'
            ).first()
            if exists:
                continue
            try:
                cols = json.loads(et.columns_json or '[]')
            except Exception:
                cols = []
            cfg = {'columnas': cols}
            if et.custom_header:
                cfg['custom_header'] = et.custom_header
            session.add(Plantilla(
                entidad_tipo='laboratorio', entidad_id=et.laboratorio_id,
                nombre='[legacy] Plantilla XLSX', formato='xlsx',
                tipo_doc='pedido', config_json=json.dumps(cfg),
                es_default=True,
            ))

        # Proveedores/Droguerías (PlantillaExportacion) → txt_fijo
        for pe in session.query(PlantillaExportacion).all():
            prov = session.get(Provider, pe.proveedor_id)
            tipo_ent = (prov.tipo if prov else 'drogueria') or 'drogueria'
            nombre_new = '[legacy] ' + pe.nombre
            exists = session.query(Plantilla).filter_by(
                entidad_tipo=tipo_ent, entidad_id=pe.proveedor_id, nombre=nombre_new
            ).first()
            if exists:
                continue
            campos = [{
                'campo': c.campo_sistema,
                'col_inicio': c.col_inicio,
                'longitud': c.longitud,
                'alineacion': c.alineacion,
                'relleno': c.relleno,
                'valor_fijo': c.valor_fijo,
                'nombre': c.nombre,
            } for c in pe.campos]
            cfg = {'campos': campos, 'extension': pe.extension, 'encoding': 'UTF-8', 'eol': 'LF'}
            session.add(Plantilla(
                entidad_tipo=tipo_ent, entidad_id=pe.proveedor_id,
                nombre=nombre_new, formato='txt_fijo',
                tipo_doc='pedido', config_json=json.dumps(cfg),
                es_default=True,
            ))
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def _pg_add_columns(conn):
    """Migraciones para PostgreSQL (soporta IF NOT EXISTS)."""
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS laboratorios (
            id SERIAL PRIMARY KEY,
            nombre VARCHAR(150) NOT NULL UNIQUE,
            creado_en TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(text(
        "ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS grabar_productos INTEGER NOT NULL DEFAULT 1"
    ))
    conn.execute(text(
        "ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS activo BOOLEAN NOT NULL DEFAULT TRUE"
    ))
    conn.execute(text(
        "ALTER TABLE laboratorios ADD COLUMN IF NOT EXISTS activo BOOLEAN NOT NULL DEFAULT TRUE"
    ))
    conn.execute(text(
        "ALTER TABLE laboratorios ADD COLUMN IF NOT EXISTS observer_id INTEGER UNIQUE"
    ))
    # Espejo de ObServer: tablas obs_*
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS obs_laboratorios (
            observer_id INTEGER PRIMARY KEY,
            descripcion VARCHAR(150) NOT NULL,
            fecha_baja TIMESTAMP,
            sync_en TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS obs_rubros (
            observer_id INTEGER PRIMARY KEY,
            descripcion VARCHAR(150) NOT NULL,
            sync_en TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS obs_subrubros (
            observer_id INTEGER PRIMARY KEY,
            descripcion VARCHAR(150) NOT NULL,
            rubro_observer INTEGER REFERENCES obs_rubros(observer_id),
            sync_en TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS obs_nombres_drogas (
            observer_id INTEGER PRIMARY KEY,
            descripcion VARCHAR(300) NOT NULL,
            sync_en TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS obs_productos (
            observer_id INTEGER PRIMARY KEY,
            descripcion VARCHAR(200) NOT NULL,
            laboratorio_observer INTEGER REFERENCES obs_laboratorios(observer_id),
            subrubro_observer INTEGER REFERENCES obs_subrubros(observer_id),
            nombre_droga_observer INTEGER REFERENCES obs_nombres_drogas(observer_id),
            codigo_alfabeta VARCHAR(10),
            troquel INTEGER,
            cantidad_envase DECIMAL(10, 3),
            es_habilitado_venta BOOLEAN NOT NULL DEFAULT TRUE,
            requiere_cadena_frio BOOLEAN NOT NULL DEFAULT FALSE,
            fecha_baja TIMESTAMP,
            sync_en TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_obs_prod_lab ON obs_productos(laboratorio_observer)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_obs_prod_alfabeta ON obs_productos(codigo_alfabeta)"))
    conn.execute(text("ALTER TABLE obs_productos ADD COLUMN IF NOT EXISTS id_tipo_venta_control VARCHAR(1)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_obs_prod_tvc ON obs_productos(id_tipo_venta_control)"))
    conn.execute(text("ALTER TABLE obs_productos ADD COLUMN IF NOT EXISTS descripcion_custom VARCHAR(200)"))
    # Provider: mínimo de compra (puede no estar en deploys viejos)
    conn.execute(text("ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS compra_minima_pesos DECIMAL(14, 2)"))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS obs_stock (
            id_farmacia INTEGER NOT NULL,
            producto_observer INTEGER NOT NULL REFERENCES obs_productos(observer_id),
            stock_actual INTEGER NOT NULL DEFAULT 0,
            maximo INTEGER,
            minimo INTEGER,
            sync_en TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (id_farmacia, producto_observer)
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS obs_sync_log (
            id SERIAL PRIMARY KEY,
            entidad VARCHAR(40) NOT NULL,
            filas_upsert INTEGER NOT NULL DEFAULT 0,
            duracion_ms INTEGER,
            error TEXT,
            ejecutado_en TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_obs_sync_entidad ON obs_sync_log(entidad, ejecutado_en DESC)"))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS obs_ventas_mensuales (
            id_farmacia INTEGER NOT NULL,
            producto_observer INTEGER NOT NULL REFERENCES obs_productos(observer_id),
            anio INTEGER NOT NULL,
            mes INTEGER NOT NULL,
            unidades DECIMAL(14, 3) NOT NULL DEFAULT 0,
            monto DECIMAL(14, 2) NOT NULL DEFAULT 0,
            transacciones INTEGER NOT NULL DEFAULT 0,
            sync_en TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (id_farmacia, producto_observer, anio, mes)
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_obs_vtas_anio_mes ON obs_ventas_mensuales(anio, mes)"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS observer_ventas_meses INTEGER NOT NULL DEFAULT 16"))
    # Rutas predeterminadas adicionales (cliente local)
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS ruta_excels VARCHAR(500)"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS ruta_descargas VARCHAR(500)"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS ruta_backups VARCHAR(500)"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS ruta_plantillas_lab VARCHAR(500)"))
    conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS preferencias_home_json TEXT"))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS home_card_clicks (
            id SERIAL PRIMARY KEY,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            card_id VARCHAR(40) NOT NULL,
            clicked_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_hcc_user ON home_card_clicks(usuario_id, clicked_at DESC)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_hcc_card ON home_card_clicks(card_id)"))
    # Puente en productos
    conn.execute(text("ALTER TABLE productos ADD COLUMN IF NOT EXISTS observer_id INTEGER REFERENCES obs_productos(observer_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_productos_observer_id ON productos(observer_id)"))
    # UNIQUE partial index: un observer_id no puede estar asignado a dos productos locales.
    # Partial (WHERE observer_id IS NOT NULL) permite múltiples NULL (productos sin vincular).
    # Antes de crear, chequear si hay duplicados para no romper el startup.
    dup = conn.execute(text(
        "SELECT observer_id, COUNT(*) AS n FROM productos "
        "WHERE observer_id IS NOT NULL GROUP BY observer_id HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if dup:
        import logging
        logging.getLogger(__name__).warning(
            'productos.observer_id tiene duplicados (ej: observer_id=%s aparece %s veces). '
            'No se crea el UNIQUE INDEX hasta resolverlos.', dup[0], dup[1]
        )
    else:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_productos_observer_id "
            "ON productos(observer_id) WHERE observer_id IS NOT NULL"
        ))
    conn.execute(text(
        "ALTER TABLE productos ADD COLUMN IF NOT EXISTS laboratorio_id INTEGER REFERENCES laboratorios(id) ON DELETE SET NULL"
    ))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS configuracion (
            id INTEGER PRIMARY KEY,
            farmacia_nombre VARCHAR(200) NOT NULL DEFAULT 'Farmacia',
            ruta_facturas VARCHAR(500),
            umbral_pico DECIMAL(4,2) NOT NULL DEFAULT 1.30,
            umbral_baja DECIMAL(4,2) NOT NULL DEFAULT 0.70,
            umbral_tendencia DECIMAL(4,2) NOT NULL DEFAULT 0.20
        )
    """))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS umbral_pico DECIMAL(4,2) NOT NULL DEFAULT 1.30"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS umbral_baja DECIMAL(4,2) NOT NULL DEFAULT 0.70"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS umbral_tendencia DECIMAL(4,2) NOT NULL DEFAULT 0.20"))
    conn.execute(text(
        "ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS rot_alta_min DECIMAL(6,1) NOT NULL DEFAULT 20.0"
    ))
    conn.execute(text(
        "ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS rot_alta_tol DECIMAL(6,1) NOT NULL DEFAULT 0.0"
    ))
    conn.execute(text(
        "ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS rot_media_min DECIMAL(6,1) NOT NULL DEFAULT 5.0"
    ))
    conn.execute(text(
        "ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS rot_media_tol DECIMAL(6,1) NOT NULL DEFAULT 0.0"
    ))
    conn.execute(text(
        "ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS rot_baja_tol DECIMAL(6,1) NOT NULL DEFAULT 0.0"
    ))
    # Asegurar columnas keep_alive antes del INSERT (si la tabla ya existe sin ellas,
    # el INSERT explota con NOT NULL porque no les ponemos valor).
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS keep_alive_enabled BOOLEAN NOT NULL DEFAULT FALSE"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS keep_alive_interval_min INTEGER NOT NULL DEFAULT 10"))
    # Asegurar DEFAULT en observer_ventas_meses (si se agregó sin default en versiones previas).
    conn.execute(text("ALTER TABLE configuracion ALTER COLUMN observer_ventas_meses SET DEFAULT 16"))
    conn.execute(text(
        "INSERT INTO configuracion "
        "(id, farmacia_nombre, umbral_pico, umbral_baja, umbral_tendencia, "
        " rot_alta_min, rot_alta_tol, rot_media_min, rot_media_tol, rot_baja_tol, "
        " keep_alive_enabled, keep_alive_interval_min, observer_ventas_meses) "
        "VALUES (1, 'Farmacia', 1.30, 0.70, 0.20, 20.0, 0.0, 5.0, 0.0, 0.0, FALSE, 10, 16) "
        "ON CONFLICT DO NOTHING"
    ))
    conn.execute(text(
        "ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS ruta_facturas VARCHAR(500)"
    ))
    conn.execute(text(
        "ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS parser_file VARCHAR(100)"
    ))
    conn.execute(text(
        "ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS match_strategy VARCHAR(20) NOT NULL DEFAULT 'barcode'"
    ))
    conn.execute(text(
        "ALTER TABLE facturas ADD COLUMN IF NOT EXISTS tipo_comprobante VARCHAR(5) NOT NULL DEFAULT 'FAC'"
    ))
    conn.execute(text(
        "ALTER TABLE facturas ADD COLUMN IF NOT EXISTS total_articulos INTEGER"
    ))
    conn.execute(text(
        "ALTER TABLE facturas ADD COLUMN IF NOT EXISTS total_unidades INTEGER"
    ))
    conn.execute(text(
        "ALTER TABLE facturas ADD COLUMN IF NOT EXISTS pdf_filename VARCHAR(200)"
    ))
    conn.execute(text(
        "ALTER TABLE facturas ADD COLUMN IF NOT EXISTS erp_filename VARCHAR(200)"
    ))
    conn.execute(text(
        "ALTER TABLE factura_items ADD COLUMN IF NOT EXISTS dto DECIMAL(6,2)"
    ))
    conn.execute(text(
        "ALTER TABLE factura_items ADD COLUMN IF NOT EXISTS precio_erp DECIMAL(14,2)"
    ))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS barcode_mappings (
            id SERIAL PRIMARY KEY,
            proveedor_id INTEGER NOT NULL REFERENCES proveedores(id) ON DELETE CASCADE,
            codigo_barra_factura VARCHAR(20) NOT NULL,
            codigo_barra_erp VARCHAR(20) NOT NULL,
            descripcion_factura VARCHAR(150),
            descripcion_erp VARCHAR(150),
            creado_en TIMESTAMP DEFAULT NOW(),
            UNIQUE (proveedor_id, codigo_barra_factura)
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS invoice_batches (
            id SERIAL PRIMARY KEY,
            proveedor_id INTEGER NOT NULL REFERENCES proveedores(id),
            erp_filename VARCHAR(200),
            fecha TIMESTAMP DEFAULT NOW(),
            estado VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE'
        )
    """))
    conn.execute(text(
        "ALTER TABLE facturas ADD COLUMN IF NOT EXISTS batch_id INTEGER REFERENCES invoice_batches(id)"
    ))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS rot_alta_min DECIMAL(6,1) NOT NULL DEFAULT 20.0"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS rot_alta_tol DECIMAL(6,1) NOT NULL DEFAULT 0.0"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS rot_media_min DECIMAL(6,1) NOT NULL DEFAULT 5.0"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS rot_media_tol DECIMAL(6,1) NOT NULL DEFAULT 0.0"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS rot_baja_tol DECIMAL(6,1) NOT NULL DEFAULT 0.0"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS keep_alive_enabled BOOLEAN NOT NULL DEFAULT FALSE"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS keep_alive_interval_min INTEGER NOT NULL DEFAULT 10"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS dockerpanel_ruta VARCHAR(500)"))

    # Cleanup legacy: descuento_campanas, descuento_modulos, descuento_modulo_items.
    # Eran del primer prototipo de gestión de campañas, nunca se usó en producción.
    # DROP en lugar de mantener tablas huérfanas.
    conn.execute(text("DROP TABLE IF EXISTS descuento_modulo_items CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS descuento_modulos CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS descuento_campanas CASCADE"))
    conn.execute(text("ALTER TABLE pedido_items ADD COLUMN IF NOT EXISTS rotacion VARCHAR(1)"))
    conn.execute(text("ALTER TABLE pedido_items ADD COLUMN IF NOT EXISTS avg_monthly DECIMAL(10,2)"))
    conn.execute(text("ALTER TABLE productos ADD COLUMN IF NOT EXISTS es_pack INTEGER NOT NULL DEFAULT 0"))
    conn.execute(text("ALTER TABLE facturas ADD COLUMN IF NOT EXISTS creado_en TIMESTAMP DEFAULT NOW()"))
    conn.execute(text("ALTER TABLE facturas ADD COLUMN IF NOT EXISTS conciliado BOOLEAN NOT NULL DEFAULT false"))
    for _col in ('monto_exento', 'monto_gravado', 'iva_105', 'iva_21', 'percepciones', 'otros'):
        conn.execute(text(f"ALTER TABLE facturas ADD COLUMN IF NOT EXISTS {_col} DECIMAL(14,2)"))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS pagos_ajustes_cc (
            id SERIAL PRIMARY KEY,
            proveedor_id INTEGER NOT NULL REFERENCES proveedores(id) ON DELETE CASCADE,
            tipo VARCHAR(10) NOT NULL,
            fecha DATE NOT NULL,
            monto DECIMAL(14,2) NOT NULL,
            numero_comprobante VARCHAR(30),
            observaciones TEXT,
            creado_en TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(text("ALTER TABLE pagos_ajustes_cc ADD COLUMN IF NOT EXISTS conciliado BOOLEAN NOT NULL DEFAULT false"))
    conn.execute(text("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS analizado_en TIMESTAMP"))
    conn.execute(text("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS analisis_json TEXT"))
    conn.execute(text("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS analisis_guardado_en TIMESTAMP"))
    conn.execute(text("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS canal VARCHAR(12)"))
    conn.execute(text("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS partner_id INTEGER"))
    conn.execute(text("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS canal_elegido_en TIMESTAMP"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_pedidos_partner_id ON pedidos(partner_id)"))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS modulo_packs (
            id SERIAL PRIMARY KEY,
            ean_pack VARCHAR(30) UNIQUE NOT NULL,
            ean_unidad VARCHAR(30) NOT NULL,
            cantidad INTEGER NOT NULL DEFAULT 1,
            descripcion VARCHAR(255),
            creado_en TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS modulos (
            id SERIAL PRIMARY KEY,
            nombre VARCHAR(200) NOT NULL,
            laboratorio_id INTEGER REFERENCES laboratorios(id),
            creado_en TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(text("ALTER TABLE modulo_packs ADD COLUMN IF NOT EXISTS modulo_id INTEGER REFERENCES modulos(id) ON DELETE SET NULL"))
    conn.execute(text("ALTER TABLE modulos ADD COLUMN IF NOT EXISTS lista_nombre VARCHAR(200)"))
    conn.execute(text("ALTER TABLE modulos ADD COLUMN IF NOT EXISTS activo BOOLEAN NOT NULL DEFAULT false"))
    conn.execute(text("ALTER TABLE modulo_packs ADD COLUMN IF NOT EXISTS cant_modulo INTEGER"))
    conn.execute(text("ALTER TABLE modulo_packs ADD COLUMN IF NOT EXISTS desc_pct DECIMAL(5,2)"))
    # Migrar datos viejos: cantidad almacenaba el CANT del Excel → mover a cant_modulo, resetear cantidad=1
    conn.execute(text("UPDATE modulo_packs SET cant_modulo = cantidad, cantidad = 1 WHERE cant_modulo IS NULL AND cantidad != 1"))
    # Drop UNIQUE de ean_pack global: un EAN puede pertenecer a varios módulos
    conn.execute(text("ALTER TABLE modulo_packs DROP CONSTRAINT IF EXISTS modulo_packs_ean_pack_key"))
    conn.execute(text(
        "ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS tipo VARCHAR(20) NOT NULL DEFAULT 'drogueria'"
    ))
    conn.execute(text(
        "ALTER TABLE productos ADD COLUMN IF NOT EXISTS ultima_compra DATE"
    ))
    conn.execute(text(
        "ALTER TABLE productos ADD COLUMN IF NOT EXISTS monodroga VARCHAR(200)"
    ))
    conn.execute(text(
        "ALTER TABLE productos ADD COLUMN IF NOT EXISTS presentacion VARCHAR(500)"
    ))
    conn.execute(text(
        "ALTER TABLE productos ADD COLUMN IF NOT EXISTS accion_terapeutica VARCHAR(200)"
    ))
    conn.execute(text(
        "ALTER TABLE productos ADD COLUMN IF NOT EXISTS codigo_alfabeta VARCHAR(10)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_productos_alfabeta ON productos(codigo_alfabeta)"
    ))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS product_analytics (
            codigo_barra VARCHAR(20) PRIMARY KEY,
            descripcion VARCHAR(200),
            laboratorio VARCHAR(150),
            stock INTEGER NOT NULL DEFAULT 0,
            avg_monthly DECIMAL(10,2) NOT NULL DEFAULT 0,
            rotacion VARCHAR(1),
            slope DECIMAL(10,4),
            forecast_next DECIMAL(10,2),
            sin_mov_60d INTEGER NOT NULL DEFAULT 0,
            precio_pvp DECIMAL(14,2),
            actualizado_en TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(text(
        "ALTER TABLE product_analytics ADD COLUMN IF NOT EXISTS tipo VARCHAR(1)"
    ))
    conn.execute(text(
        "ALTER TABLE product_analytics ADD COLUMN IF NOT EXISTS ventas_json TEXT"
    ))
    conn.execute(text(
        "ALTER TABLE product_analytics ADD COLUMN IF NOT EXISTS start_month INTEGER"
    ))
    conn.execute(text(
        "ALTER TABLE product_analytics ADD COLUMN IF NOT EXISTS n_days INTEGER"
    ))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS export_templates (
            laboratorio_id INTEGER PRIMARY KEY REFERENCES laboratorios(id) ON DELETE CASCADE,
            columns_json TEXT NOT NULL DEFAULT '[]',
            custom_header VARCHAR(200)
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS ofertas_minimo (
            id SERIAL PRIMARY KEY,
            laboratorio_id INTEGER NOT NULL REFERENCES laboratorios(id) ON DELETE CASCADE,
            ean VARCHAR(20) NOT NULL,
            descripcion VARCHAR(300),
            codigo VARCHAR(50),
            unidades_minima INTEGER,
            descuento_psl DECIMAL(6,2),
            rentabilidad DECIMAL(6,2),
            plazo_pago VARCHAR(100),
            grupo_id INTEGER,
            tipo_descuento VARCHAR(20),
            actualizado_en TIMESTAMP DEFAULT NOW()
        )
    """))
    # Migración: agregar tipo_descuento + backfill desde unidades_minima.
    conn.execute(text(
        "ALTER TABLE ofertas_minimo ADD COLUMN IF NOT EXISTS tipo_descuento VARCHAR(20)"
    ))
    conn.execute(text("""
        UPDATE ofertas_minimo
        SET tipo_descuento = CASE
            WHEN unidades_minima IS NULL OR unidades_minima <= 1 THEN 'simple'
            ELSE 'con_minimo'
        END
        WHERE tipo_descuento IS NULL
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS documentos_pendientes (
            id SERIAL PRIMARY KEY,
            filename VARCHAR(300) NOT NULL,
            ruta_completa VARCHAR(500) NOT NULL,
            fecha_detectado TIMESTAMP DEFAULT NOW(),
            estado VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',
            proveedor_id INTEGER REFERENCES proveedores(id),
            factura_id INTEGER REFERENCES facturas(id),
            creado_en TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS plantillas_exportacion (
            id SERIAL PRIMARY KEY,
            proveedor_id INTEGER NOT NULL REFERENCES proveedores(id) ON DELETE CASCADE,
            nombre VARCHAR(100) NOT NULL DEFAULT 'Plantilla',
            extension VARCHAR(10) NOT NULL DEFAULT 'txt',
            creado_en TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS plantilla_campos (
            id SERIAL PRIMARY KEY,
            plantilla_id INTEGER NOT NULL REFERENCES plantillas_exportacion(id) ON DELETE CASCADE,
            nombre VARCHAR(80) NOT NULL,
            campo_sistema VARCHAR(30) NOT NULL,
            col_inicio INTEGER NOT NULL DEFAULT 0,
            longitud INTEGER NOT NULL DEFAULT 1,
            valor_fijo VARCHAR(100),
            alineacion CHAR(1) NOT NULL DEFAULT 'L',
            relleno CHAR(1) NOT NULL DEFAULT ' '
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS analisis_sesiones (
            id SERIAL PRIMARY KEY,
            laboratorio_nombre VARCHAR(150) NOT NULL,
            laboratorio_id INTEGER REFERENCES laboratorios(id) ON DELETE SET NULL,
            periodo VARCHAR(100),
            farmacia VARCHAR(200),
            n_days INTEGER NOT NULL,
            fuente VARCHAR(20) NOT NULL DEFAULT 'pdf',
            n_productos INTEGER NOT NULL DEFAULT 0,
            creado_en TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(text(
        "ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS analisis_sesion_id INTEGER REFERENCES analisis_sesiones(id) ON DELETE SET NULL"
    ))
    conn.execute(text(
        "ALTER TABLE procesos_compra ADD COLUMN IF NOT EXISTS analisis_sesion_id INTEGER REFERENCES analisis_sesiones(id) ON DELETE SET NULL"
    ))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS producto_precios_hist (
            id SERIAL PRIMARY KEY,
            codigo_barra VARCHAR(20) NOT NULL,
            proveedor_id INTEGER REFERENCES proveedores(id) ON DELETE CASCADE,
            proveedor_razon VARCHAR(150),
            fecha DATE NOT NULL,
            precio_publico DECIMAL(14,2),
            dto_pct DECIMAL(6,2),
            precio_unitario DECIMAL(14,2),
            importe DECIMAL(14,2),
            factura_id INTEGER REFERENCES facturas(id) ON DELETE SET NULL,
            tipo_comprobante VARCHAR(5),
            creado_en TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_precios_codigo_barra ON producto_precios_hist (codigo_barra)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_precios_proveedor    ON producto_precios_hist (proveedor_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_precios_fecha        ON producto_precios_hist (fecha)"))
    # Backfill: si la tabla está vacía pero hay facturas cargadas, generar snapshots
    # desde los InvoiceItem existentes. Se ejecuta una sola vez.
    try:
        hay_precios = conn.execute(text("SELECT 1 FROM producto_precios_hist LIMIT 1")).first()
        if not hay_precios:
            conn.execute(text("""
                INSERT INTO producto_precios_hist
                    (codigo_barra, proveedor_id, proveedor_razon, fecha,
                     dto_pct, precio_unitario, importe, factura_id, tipo_comprobante)
                SELECT
                    fi.codigo_barra,
                    p.id,
                    f.proveedor_razon,
                    f.fecha,
                    fi.dto,
                    CASE WHEN f.tipo_comprobante = 'NCR' THEN -fi.precio_unitario ELSE fi.precio_unitario END,
                    CASE WHEN f.tipo_comprobante = 'NCR' THEN -fi.importe         ELSE fi.importe         END,
                    f.id,
                    f.tipo_comprobante
                FROM factura_items fi
                JOIN facturas f ON f.id = fi.factura_id
                LEFT JOIN proveedores p ON (
                    (p.cuit IS NOT NULL AND p.cuit = f.proveedor_cuit)
                    OR (p.cuit IS NULL AND p.razon_social = f.proveedor_razon)
                )
                WHERE fi.codigo_barra IS NOT NULL AND fi.codigo_barra <> ''
                  AND f.fecha IS NOT NULL
            """))
    except Exception:
        # Si falla el backfill (ej: columnas faltantes en tablas viejas), no bloquear el boot.
        pass
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) NOT NULL UNIQUE,
            email VARCHAR(150),
            password_hash VARCHAR(255) NOT NULL,
            nombre_completo VARCHAR(200),
            rol VARCHAR(20) NOT NULL DEFAULT 'remoto',
            permisos_json TEXT NOT NULL DEFAULT '{}',
            activo BOOLEAN NOT NULL DEFAULT TRUE,
            debe_cambiar_password BOOLEAN NOT NULL DEFAULT FALSE,
            ultimo_login TIMESTAMP,
            creado_en TIMESTAMP DEFAULT NOW()
        )
    """))
    # Índices para queries frecuentes
    for stmt in [
        "CREATE INDEX IF NOT EXISTS idx_factura_items_factura ON factura_items(factura_id)",
        "CREATE INDEX IF NOT EXISTS idx_stock_diff_factura ON stock_differences(factura_id)",
        "CREATE INDEX IF NOT EXISTS idx_reclamos_factura ON reclamos(factura_id)",
        "CREATE INDEX IF NOT EXISTS idx_erp_stock_codigo ON erp_stock(codigo_barra)",
        "CREATE INDEX IF NOT EXISTS idx_productos_alt1 ON productos(codigo_barra_alt1)",
        "CREATE INDEX IF NOT EXISTS idx_productos_alt2 ON productos(codigo_barra_alt2)",
        "CREATE INDEX IF NOT EXISTS idx_productos_alt3 ON productos(codigo_barra_alt3)",
        "CREATE INDEX IF NOT EXISTS idx_ofertas_minimo_lab_tipo ON ofertas_minimo(laboratorio_id, tipo_descuento)",
    ]:
        conn.execute(text(stmt))


def _sqlite_add_columns(conn):
    """Migraciones para SQLite (no soporta IF NOT EXISTS en ALTER TABLE)."""
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS laboratorios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre VARCHAR(150) NOT NULL UNIQUE,
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    existing_prov = {row[1] for row in conn.execute(text("PRAGMA table_info(proveedores)"))}
    if 'grabar_productos' not in existing_prov:
        conn.execute(text("ALTER TABLE proveedores ADD COLUMN grabar_productos INTEGER NOT NULL DEFAULT 1"))
    if 'activo' not in existing_prov:
        conn.execute(text("ALTER TABLE proveedores ADD COLUMN activo BOOLEAN NOT NULL DEFAULT 1"))
    existing_lab = {row[1] for row in conn.execute(text("PRAGMA table_info(laboratorios)"))}
    if 'activo' not in existing_lab:
        conn.execute(text("ALTER TABLE laboratorios ADD COLUMN activo BOOLEAN NOT NULL DEFAULT 1"))
    if 'observer_id' not in existing_lab:
        conn.execute(text("ALTER TABLE laboratorios ADD COLUMN observer_id INTEGER UNIQUE"))
    # Espejo ObServer
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS obs_laboratorios (
            observer_id INTEGER PRIMARY KEY,
            descripcion VARCHAR(150) NOT NULL,
            fecha_baja TIMESTAMP,
            sync_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS obs_rubros (
            observer_id INTEGER PRIMARY KEY,
            descripcion VARCHAR(150) NOT NULL,
            sync_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS obs_subrubros (
            observer_id INTEGER PRIMARY KEY,
            descripcion VARCHAR(150) NOT NULL,
            rubro_observer INTEGER REFERENCES obs_rubros(observer_id),
            sync_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS obs_nombres_drogas (
            observer_id INTEGER PRIMARY KEY,
            descripcion VARCHAR(300) NOT NULL,
            sync_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS obs_productos (
            observer_id INTEGER PRIMARY KEY,
            descripcion VARCHAR(200) NOT NULL,
            laboratorio_observer INTEGER REFERENCES obs_laboratorios(observer_id),
            subrubro_observer INTEGER REFERENCES obs_subrubros(observer_id),
            nombre_droga_observer INTEGER REFERENCES obs_nombres_drogas(observer_id),
            codigo_alfabeta VARCHAR(10),
            troquel INTEGER,
            cantidad_envase DECIMAL(10, 3),
            es_habilitado_venta BOOLEAN NOT NULL DEFAULT 1,
            requiere_cadena_frio BOOLEAN NOT NULL DEFAULT 0,
            fecha_baja TIMESTAMP,
            sync_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS obs_stock (
            id_farmacia INTEGER NOT NULL,
            producto_observer INTEGER NOT NULL REFERENCES obs_productos(observer_id),
            stock_actual INTEGER NOT NULL DEFAULT 0,
            maximo INTEGER,
            minimo INTEGER,
            sync_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id_farmacia, producto_observer)
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS obs_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entidad VARCHAR(40) NOT NULL,
            filas_upsert INTEGER NOT NULL DEFAULT 0,
            duracion_ms INTEGER,
            error TEXT,
            ejecutado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS obs_ventas_mensuales (
            id_farmacia INTEGER NOT NULL,
            producto_observer INTEGER NOT NULL REFERENCES obs_productos(observer_id),
            anio INTEGER NOT NULL,
            mes INTEGER NOT NULL,
            unidades DECIMAL(14, 3) NOT NULL DEFAULT 0,
            monto DECIMAL(14, 2) NOT NULL DEFAULT 0,
            transacciones INTEGER NOT NULL DEFAULT 0,
            sync_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id_farmacia, producto_observer, anio, mes)
        )
    """))
    existing_cfg = {row[1] for row in conn.execute(text("PRAGMA table_info(configuracion)"))}
    if 'observer_ventas_meses' not in existing_cfg:
        conn.execute(text("ALTER TABLE configuracion ADD COLUMN observer_ventas_meses INTEGER NOT NULL DEFAULT 16"))
    existing_users = {row[1] for row in conn.execute(text("PRAGMA table_info(usuarios)"))}
    if existing_users and 'preferencias_home_json' not in existing_users:
        conn.execute(text("ALTER TABLE usuarios ADD COLUMN preferencias_home_json TEXT"))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS home_card_clicks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            card_id VARCHAR(40) NOT NULL,
            clicked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    existing_prod = {row[1] for row in conn.execute(text("PRAGMA table_info(productos)"))}
    if 'observer_id' not in existing_prod:
        conn.execute(text("ALTER TABLE productos ADD COLUMN observer_id INTEGER REFERENCES obs_productos(observer_id)"))
    # Unique partial index sobre observer_id (compatible con SQLite y PostgreSQL)
    dup = conn.execute(text(
        "SELECT observer_id FROM productos WHERE observer_id IS NOT NULL "
        "GROUP BY observer_id HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if not dup:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_productos_observer_id "
            "ON productos(observer_id) WHERE observer_id IS NOT NULL"
        ))
    existing_prod = {row[1] for row in conn.execute(text("PRAGMA table_info(productos)"))}
    if 'laboratorio_id' not in existing_prod:
        conn.execute(text("ALTER TABLE productos ADD COLUMN laboratorio_id INTEGER REFERENCES laboratorios(id)"))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS configuracion (
            id INTEGER PRIMARY KEY,
            farmacia_nombre VARCHAR(200) NOT NULL DEFAULT 'Farmacia',
            ruta_facturas VARCHAR(500),
            umbral_pico DECIMAL(4,2) NOT NULL DEFAULT 1.30,
            umbral_baja DECIMAL(4,2) NOT NULL DEFAULT 0.70,
            umbral_tendencia DECIMAL(4,2) NOT NULL DEFAULT 0.20
        )
    """))
    cfg_exists = conn.execute(text("SELECT COUNT(*) FROM configuracion WHERE id=1")).scalar()
    if not cfg_exists:
        conn.execute(text("INSERT INTO configuracion (id, farmacia_nombre) VALUES (1, 'Farmacia')"))
    existing_cfg = {row[1] for row in conn.execute(text("PRAGMA table_info(configuracion)"))}
    for col, typedef in [('umbral_pico', 'DECIMAL(4,2) NOT NULL DEFAULT 1.30'),
                         ('umbral_baja', 'DECIMAL(4,2) NOT NULL DEFAULT 0.70'),
                         ('umbral_tendencia', 'DECIMAL(4,2) NOT NULL DEFAULT 0.20'),
                         ('rot_alta_min', 'DECIMAL(6,1) NOT NULL DEFAULT 20.0'),
                         ('rot_alta_tol', 'DECIMAL(6,1) NOT NULL DEFAULT 0.0'),
                         ('rot_media_min', 'DECIMAL(6,1) NOT NULL DEFAULT 5.0'),
                         ('rot_media_tol', 'DECIMAL(6,1) NOT NULL DEFAULT 0.0'),
                         ('rot_baja_tol', 'DECIMAL(6,1) NOT NULL DEFAULT 0.0'),
                         ('dockerpanel_ruta', 'VARCHAR(500)'),
                         ('ruta_excels', 'VARCHAR(500)'),
                         ('ruta_descargas', 'VARCHAR(500)'),
                         ('ruta_backups', 'VARCHAR(500)'),
                         ('ruta_plantillas_lab', 'VARCHAR(500)')]:
        if col not in existing_cfg:
            conn.execute(text(f"ALTER TABLE configuracion ADD COLUMN {col} {typedef}"))

    existing = {row[1] for row in conn.execute(text("PRAGMA table_info(proveedores)"))}
    if 'ruta_facturas' not in existing:
        conn.execute(text("ALTER TABLE proveedores ADD COLUMN ruta_facturas VARCHAR(500)"))
    if 'parser_file' not in existing:
        conn.execute(text("ALTER TABLE proveedores ADD COLUMN parser_file VARCHAR(100)"))
    if 'match_strategy' not in existing:
        conn.execute(text("ALTER TABLE proveedores ADD COLUMN match_strategy VARCHAR(20) NOT NULL DEFAULT 'barcode'"))

    existing = {row[1] for row in conn.execute(text("PRAGMA table_info(facturas)"))}
    existing_fi = {row[1] for row in conn.execute(text("PRAGMA table_info(factura_items)"))}
    if 'dto' not in existing_fi:
        conn.execute(text("ALTER TABLE factura_items ADD COLUMN dto DECIMAL(6,2)"))
    if 'precio_erp' not in existing_fi:
        conn.execute(text("ALTER TABLE factura_items ADD COLUMN precio_erp DECIMAL(14,2)"))

    existing = {row[1] for row in conn.execute(text("PRAGMA table_info(facturas)"))}
    for col, typedef in [('tipo_comprobante', "VARCHAR(5) NOT NULL DEFAULT 'FAC'"),
                         ('total_articulos', 'INTEGER'), ('total_unidades', 'INTEGER'),
                         ('pdf_filename', 'VARCHAR(200)'), ('erp_filename', 'VARCHAR(200)')]:
        if col not in existing:
            conn.execute(text(f"ALTER TABLE facturas ADD COLUMN {col} {typedef}"))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS barcode_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor_id INTEGER NOT NULL REFERENCES proveedores(id) ON DELETE CASCADE,
            codigo_barra_factura VARCHAR(20) NOT NULL,
            codigo_barra_erp VARCHAR(20) NOT NULL,
            descripcion_factura VARCHAR(150),
            descripcion_erp VARCHAR(150),
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (proveedor_id, codigo_barra_factura)
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS invoice_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor_id INTEGER NOT NULL REFERENCES proveedores(id),
            erp_filename VARCHAR(200),
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            estado VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE'
        )
    """))
    existing_f = {row[1] for row in conn.execute(text("PRAGMA table_info(facturas)"))}
    if 'batch_id' not in existing_f:
        conn.execute(text("ALTER TABLE facturas ADD COLUMN batch_id INTEGER REFERENCES invoice_batches(id)"))
    # Cleanup legacy: tablas descuento_* (primer prototipo no usado).
    conn.execute(text("DROP TABLE IF EXISTS descuento_modulo_items"))
    conn.execute(text("DROP TABLE IF EXISTS descuento_modulos"))
    conn.execute(text("DROP TABLE IF EXISTS descuento_campanas"))
    existing_prod2 = {row[1] for row in conn.execute(text("PRAGMA table_info(productos)"))}
    if 'es_pack' not in existing_prod2:
        conn.execute(text("ALTER TABLE productos ADD COLUMN es_pack INTEGER NOT NULL DEFAULT 0"))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS modulo_packs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ean_pack VARCHAR(30) UNIQUE NOT NULL,
            ean_unidad VARCHAR(30) NOT NULL,
            cantidad INTEGER NOT NULL DEFAULT 1,
            descripcion VARCHAR(255),
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS modulos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre VARCHAR(200) NOT NULL,
            laboratorio_id INTEGER REFERENCES laboratorios(id),
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    existing_mp = {row[1] for row in conn.execute(text("PRAGMA table_info(modulo_packs)"))}
    if 'modulo_id' not in existing_mp:
        conn.execute(text("ALTER TABLE modulo_packs ADD COLUMN modulo_id INTEGER REFERENCES modulos(id)"))
    existing_mod2 = {row[1] for row in conn.execute(text("PRAGMA table_info(modulos)"))}
    if 'lista_nombre' not in existing_mod2:
        conn.execute(text("ALTER TABLE modulos ADD COLUMN lista_nombre VARCHAR(200)"))
    if 'activo' not in existing_mod2:
        conn.execute(text("ALTER TABLE modulos ADD COLUMN activo INTEGER NOT NULL DEFAULT 0"))
    if 'cant_modulo' not in existing_mp:
        conn.execute(text("ALTER TABLE modulo_packs ADD COLUMN cant_modulo INTEGER"))
        conn.execute(text("UPDATE modulo_packs SET cant_modulo = cantidad, cantidad = 1 WHERE cant_modulo IS NULL AND cantidad != 1"))
    if 'desc_pct' not in existing_mp:
        conn.execute(text("ALTER TABLE modulo_packs ADD COLUMN desc_pct DECIMAL(5,2)"))
    existing_prov3 = {row[1] for row in conn.execute(text("PRAGMA table_info(proveedores)"))}
    if 'tipo' not in existing_prov3:
        conn.execute(text("ALTER TABLE proveedores ADD COLUMN tipo VARCHAR(20) NOT NULL DEFAULT 'drogueria'"))
    existing_prod3 = {row[1] for row in conn.execute(text("PRAGMA table_info(productos)"))}
    if 'ultima_compra' not in existing_prod3:
        conn.execute(text("ALTER TABLE productos ADD COLUMN ultima_compra DATE"))
    if 'monodroga' not in existing_prod3:
        conn.execute(text("ALTER TABLE productos ADD COLUMN monodroga VARCHAR(200)"))
    if 'presentacion' not in existing_prod3:
        conn.execute(text("ALTER TABLE productos ADD COLUMN presentacion VARCHAR(500)"))
    if 'accion_terapeutica' not in existing_prod3:
        conn.execute(text("ALTER TABLE productos ADD COLUMN accion_terapeutica VARCHAR(200)"))
    if 'codigo_alfabeta' not in existing_prod3:
        conn.execute(text("ALTER TABLE productos ADD COLUMN codigo_alfabeta VARCHAR(10)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_productos_alfabeta ON productos(codigo_alfabeta)"))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS product_analytics (
            codigo_barra VARCHAR(20) PRIMARY KEY,
            descripcion VARCHAR(200),
            laboratorio VARCHAR(150),
            stock INTEGER NOT NULL DEFAULT 0,
            avg_monthly DECIMAL(10,2) NOT NULL DEFAULT 0,
            rotacion VARCHAR(1),
            slope DECIMAL(10,4),
            forecast_next DECIMAL(10,2),
            sin_mov_60d INTEGER NOT NULL DEFAULT 0,
            precio_pvp DECIMAL(14,2),
            actualizado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS documentos_pendientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename VARCHAR(300) NOT NULL,
            ruta_completa VARCHAR(500) NOT NULL,
            fecha_detectado TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            estado VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',
            proveedor_id INTEGER REFERENCES proveedores(id),
            factura_id INTEGER REFERENCES facturas(id),
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS plantillas_exportacion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor_id INTEGER NOT NULL REFERENCES proveedores(id) ON DELETE CASCADE,
            nombre VARCHAR(100) NOT NULL DEFAULT 'Plantilla',
            extension VARCHAR(10) NOT NULL DEFAULT 'txt',
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS plantilla_campos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plantilla_id INTEGER NOT NULL REFERENCES plantillas_exportacion(id) ON DELETE CASCADE,
            nombre VARCHAR(80) NOT NULL,
            campo_sistema VARCHAR(30) NOT NULL,
            col_inicio INTEGER NOT NULL DEFAULT 0,
            longitud INTEGER NOT NULL DEFAULT 1,
            valor_fijo VARCHAR(100),
            alineacion CHAR(1) NOT NULL DEFAULT 'L',
            relleno CHAR(1) NOT NULL DEFAULT ' '
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS analisis_sesiones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            laboratorio_nombre VARCHAR(150) NOT NULL,
            laboratorio_id INTEGER REFERENCES laboratorios(id) ON DELETE SET NULL,
            periodo VARCHAR(100),
            farmacia VARCHAR(200),
            n_days INTEGER NOT NULL,
            fuente VARCHAR(20) NOT NULL DEFAULT 'pdf',
            n_productos INTEGER NOT NULL DEFAULT 0,
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    existing_ped = {row[1] for row in conn.execute(text("PRAGMA table_info(pedidos)"))}
    if 'analisis_sesion_id' not in existing_ped:
        conn.execute(text("ALTER TABLE pedidos ADD COLUMN analisis_sesion_id INTEGER REFERENCES analisis_sesiones(id)"))
    if 'analisis_json' not in existing_ped:
        conn.execute(text("ALTER TABLE pedidos ADD COLUMN analisis_json TEXT"))
    if 'analisis_guardado_en' not in existing_ped:
        conn.execute(text("ALTER TABLE pedidos ADD COLUMN analisis_guardado_en TIMESTAMP"))
    if 'canal' not in existing_ped:
        conn.execute(text("ALTER TABLE pedidos ADD COLUMN canal VARCHAR(12)"))
    if 'partner_id' not in existing_ped:
        conn.execute(text("ALTER TABLE pedidos ADD COLUMN partner_id INTEGER"))
    if 'canal_elegido_en' not in existing_ped:
        conn.execute(text("ALTER TABLE pedidos ADD COLUMN canal_elegido_en TIMESTAMP"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_pedidos_partner_id ON pedidos(partner_id)"))
    existing_proc = {row[1] for row in conn.execute(text("PRAGMA table_info(procesos_compra)"))}
    if 'analisis_sesion_id' not in existing_proc:
        conn.execute(text("ALTER TABLE procesos_compra ADD COLUMN analisis_sesion_id INTEGER REFERENCES analisis_sesiones(id)"))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username VARCHAR(50) NOT NULL UNIQUE,
            email VARCHAR(150),
            password_hash VARCHAR(255) NOT NULL,
            nombre_completo VARCHAR(200),
            rol VARCHAR(20) NOT NULL DEFAULT 'remoto',
            permisos_json TEXT NOT NULL DEFAULT '{}',
            activo INTEGER NOT NULL DEFAULT 1,
            debe_cambiar_password INTEGER NOT NULL DEFAULT 0,
            ultimo_login TIMESTAMP,
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    # Migración SQLite: tipo_descuento en ofertas_minimo + backfill.
    try:
        existing_om = {row[1] for row in conn.execute(text("PRAGMA table_info(ofertas_minimo)"))}
        if existing_om and 'tipo_descuento' not in existing_om:
            conn.execute(text("ALTER TABLE ofertas_minimo ADD COLUMN tipo_descuento VARCHAR(20)"))
        if existing_om:
            conn.execute(text("""
                UPDATE ofertas_minimo
                SET tipo_descuento = CASE
                    WHEN unidades_minima IS NULL OR unidades_minima <= 1 THEN 'simple'
                    ELSE 'con_minimo'
                END
                WHERE tipo_descuento IS NULL
            """))
    except Exception:
        pass

    # Índices para queries frecuentes
    for stmt in [
        "CREATE INDEX IF NOT EXISTS idx_factura_items_factura ON factura_items(factura_id)",
        "CREATE INDEX IF NOT EXISTS idx_stock_diff_factura ON stock_differences(factura_id)",
        "CREATE INDEX IF NOT EXISTS idx_reclamos_factura ON reclamos(factura_id)",
        "CREATE INDEX IF NOT EXISTS idx_erp_stock_codigo ON erp_stock(codigo_barra)",
        "CREATE INDEX IF NOT EXISTS idx_productos_alt1 ON productos(codigo_barra_alt1)",
        "CREATE INDEX IF NOT EXISTS idx_productos_alt2 ON productos(codigo_barra_alt2)",
        "CREATE INDEX IF NOT EXISTS idx_productos_alt3 ON productos(codigo_barra_alt3)",
        "CREATE INDEX IF NOT EXISTS idx_ofertas_minimo_lab_tipo ON ofertas_minimo(laboratorio_id, tipo_descuento)",
    ]:
        conn.execute(text(stmt))
