import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, DECIMAL, ForeignKey, Text, text, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base, relationship

Base = declarative_base()


class Config(Base):
    __tablename__ = 'configuracion'
    id = Column(Integer, primary_key=True)
    farmacia_nombre = Column(String(200), nullable=False, default='Farmacia')
    ruta_facturas = Column(String(500), nullable=True)
    umbral_pico = Column(DECIMAL(4, 2), nullable=False, default=1.30)
    umbral_baja = Column(DECIMAL(4, 2), nullable=False, default=0.70)
    umbral_tendencia = Column(DECIMAL(4, 2), nullable=False, default=0.20)
    # Rotación de ventas
    rot_alta_min = Column(DECIMAL(6, 1), nullable=False, default=20.0)
    rot_alta_tol = Column(DECIMAL(6, 1), nullable=False, default=0.0)
    rot_media_min = Column(DECIMAL(6, 1), nullable=False, default=5.0)
    rot_media_tol = Column(DECIMAL(6, 1), nullable=False, default=0.0)
    rot_baja_tol = Column(DECIMAL(6, 1), nullable=False, default=0.0)


class Laboratorio(Base):
    __tablename__ = 'laboratorios'
    id = Column(Integer, primary_key=True)
    nombre = Column(String(150), nullable=False, unique=True)
    creado_en = Column(DateTime, default=datetime.utcnow)


class ExportTemplate(Base):
    __tablename__ = 'export_templates'
    laboratorio_id = Column(Integer, ForeignKey('laboratorios.id'), primary_key=True)
    columns_json  = Column(Text, nullable=False, default='[]')
    custom_header = Column(String(200))


class OfertaMinimo(Base):
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
    actualizado_en  = Column(DateTime, default=datetime.utcnow)


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
    claims = relationship('Claim', back_populates='provider')


class InvoiceBatch(Base):
    __tablename__ = 'invoice_batches'
    id = Column(Integer, primary_key=True)
    proveedor_id = Column(Integer, ForeignKey('proveedores.id'), nullable=False)
    erp_filename = Column(String(200))
    fecha = Column(DateTime, default=datetime.utcnow)
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
    creado_en = Column(DateTime, default=datetime.utcnow)
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
    creado_en = Column(DateTime, default=datetime.utcnow)
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


class DescuentoCampana(Base):
    __tablename__ = 'descuento_campanas'
    id = Column(Integer, primary_key=True)
    proveedor_id = Column(Integer, ForeignKey('proveedores.id'), nullable=True)
    laboratorio_nombre = Column(String(150), nullable=False)
    fecha = Column(Date, nullable=True)
    observacion = Column(Text, nullable=True)
    creado_en = Column(DateTime, default=datetime.utcnow)
    proveedor = relationship('Provider')
    modulos = relationship('DescuentoModulo', back_populates='campana',
                           cascade='all, delete-orphan')


class DescuentoModulo(Base):
    __tablename__ = 'descuento_modulos'
    id = Column(Integer, primary_key=True)
    campana_id = Column(Integer, ForeignKey('descuento_campanas.id'), nullable=True)
    codigo = Column(String(30), nullable=True)   # legacy, ya no se usa
    laboratorio = Column(String(100), nullable=True)  # legacy
    nombre = Column(String(150))
    descuento_default = Column(DECIMAL(5, 2), nullable=True)  # legacy
    activo = Column(Integer, nullable=False, default=1)
    creado_en = Column(DateTime, default=datetime.utcnow)
    campana = relationship('DescuentoCampana', back_populates='modulos')
    items = relationship('DescuentoModuloItem', back_populates='modulo',
                         cascade='all, delete-orphan')


class DescuentoModuloItem(Base):
    __tablename__ = 'descuento_modulo_items'
    id = Column(Integer, primary_key=True)
    modulo_id = Column(Integer, ForeignKey('descuento_modulos.id'), nullable=False)
    codigo_ean = Column(String(20), nullable=False)
    descripcion = Column(String(200))
    cantidad = Column(Integer, nullable=False, default=1)
    descuento = Column(DECIMAL(5, 2), nullable=False, default=0)
    es_principal = Column(Integer, nullable=False, default=0)
    modulo = relationship('DescuentoModulo', back_populates='items')


class BarcodeMapping(Base):
    __tablename__ = 'barcode_mappings'
    id = Column(Integer, primary_key=True)
    proveedor_id = Column(Integer, ForeignKey('proveedores.id'), nullable=False)
    codigo_barra_factura = Column(String(20), nullable=False)
    codigo_barra_erp = Column(String(20), nullable=False)
    descripcion_factura = Column(String(150))
    descripcion_erp = Column(String(150))
    creado_en = Column(DateTime, default=datetime.utcnow)


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
    monodroga = Column(String(200), nullable=True)
    presentacion = Column(String(500), nullable=True)
    accion_terapeutica = Column(String(200), nullable=True)
    actualizado_en = Column(DateTime, default=datetime.utcnow)
    ultima_compra = Column(Date, nullable=True)


class Modulo(Base):
    """Módulo de descuento: agrupación de packs de un laboratorio."""
    __tablename__ = 'modulos'
    id = Column(Integer, primary_key=True)
    nombre = Column(String(200), nullable=False)
    laboratorio_id = Column(Integer, ForeignKey('laboratorios.id'), nullable=True)
    lista_nombre = Column(String(200), nullable=True)  # Nombre de la lista/importación a la que pertenece
    creado_en = Column(DateTime, default=datetime.utcnow)
    activo = Column(Boolean, default=False, nullable=False, server_default='false')
    laboratorio = relationship('Laboratorio')
    packs = relationship('ModuloPack', back_populates='modulo', cascade='all, delete-orphan')


class ModuloPack(Base):
    """Pack de módulo: un EAN del proveedor equivale a N unidades de otro EAN del ERP."""
    __tablename__ = 'modulo_packs'
    id = Column(Integer, primary_key=True)
    ean_pack = Column(String(30), unique=True, nullable=False)   # EAN del pack (proveedor/módulo)
    ean_unidad = Column(String(30), nullable=False)              # EAN de la unidad individual (ERP/pedido)
    cantidad = Column(Integer, nullable=False, default=1)        # Unidades individuales por pack
    cant_modulo = Column(Integer, nullable=True)                 # Cant. de packs en el módulo (CANT. del Excel)
    desc_pct = Column(DECIMAL(5, 2), nullable=True)             # Descuento % del módulo (DESC.% del Excel)
    descripcion = Column(String(255))
    modulo_id = Column(Integer, ForeignKey('modulos.id'), nullable=True)
    creado_en = Column(DateTime, default=datetime.utcnow)
    modulo = relationship('Modulo', back_populates='packs')


class Pedido(Base):
    __tablename__ = 'pedidos'
    id = Column(Integer, primary_key=True)
    laboratorio = Column(String(150), nullable=False)
    farmacia = Column(String(200))
    periodo = Column(String(100))
    n_days = Column(Integer)
    creado_en = Column(DateTime, default=datetime.utcnow)
    analizado_en = Column(DateTime, nullable=True)
    estado = Column(String(20), nullable=False, default='PENDIENTE')
    items = relationship('PedidoItem', back_populates='pedido', cascade='all, delete-orphan')


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
    creado_en = Column(DateTime, default=datetime.utcnow)
    proveedor = relationship('Provider')


class DocumentoPendiente(Base):
    """Documentos detectados en carpeta pendientes, listos para procesar."""
    __tablename__ = 'documentos_pendientes'
    id = Column(Integer, primary_key=True)
    filename = Column(String(300), nullable=False)
    ruta_completa = Column(String(500), nullable=False)
    fecha_detectado = Column(DateTime, default=datetime.utcnow)
    estado = Column(String(20), nullable=False, default='PENDIENTE')  # PENDIENTE / PROCESADO
    proveedor_id = Column(Integer, ForeignKey('proveedores.id'), nullable=True)
    factura_id = Column(Integer, ForeignKey('facturas.id'), nullable=True)
    creado_en = Column(DateTime, default=datetime.utcnow)
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
    actualizado_en = Column(DateTime, default=datetime.utcnow)


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
        # Limpia entradas stale en pg_type que bloquean CREATE TABLE
        with engine.connect() as conn:
            for tname in ('export_templates', 'ofertas_minimo'):
                conn.execute(text(f"""
                    DO $$ BEGIN
                        IF NOT EXISTS (SELECT FROM pg_tables WHERE tablename = '{tname}') THEN
                            DROP TYPE IF EXISTS {tname};
                        END IF;
                    END $$
                """))
            conn.commit()
    Base.metadata.create_all(engine)
    is_sqlite = database_url.startswith('sqlite')
    # Migraciones incrementales: agrega columnas nuevas si no existen
    with engine.connect() as conn:
        if is_sqlite:
            _sqlite_add_columns(conn)
        else:
            _pg_add_columns(conn)
        conn.commit()


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
    conn.execute(text(
        "INSERT INTO configuracion "
        "(id, farmacia_nombre, umbral_pico, umbral_baja, umbral_tendencia, "
        " rot_alta_min, rot_alta_tol, rot_media_min, rot_media_tol, rot_baja_tol) "
        "VALUES (1, 'Farmacia', 1.30, 0.70, 0.20, 20.0, 0.0, 5.0, 0.0, 0.0) "
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
    # descuento_campanas, descuento_modulos, descuento_modulo_items los crea
    # Base.metadata.create_all(). Solo necesitamos ALTER TABLE para datos viejos.
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS rot_alta_min DECIMAL(6,1) NOT NULL DEFAULT 20.0"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS rot_alta_tol DECIMAL(6,1) NOT NULL DEFAULT 0.0"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS rot_media_min DECIMAL(6,1) NOT NULL DEFAULT 5.0"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS rot_media_tol DECIMAL(6,1) NOT NULL DEFAULT 0.0"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS rot_baja_tol DECIMAL(6,1) NOT NULL DEFAULT 0.0"))
    conn.execute(text("ALTER TABLE descuento_modulos ADD COLUMN IF NOT EXISTS campana_id INTEGER REFERENCES descuento_campanas(id) ON DELETE CASCADE"))
    conn.execute(text("ALTER TABLE descuento_modulos ALTER COLUMN codigo DROP NOT NULL"))
    conn.execute(text("ALTER TABLE descuento_modulos ALTER COLUMN laboratorio DROP NOT NULL"))
    conn.execute(text("ALTER TABLE descuento_modulos ALTER COLUMN descuento_default DROP NOT NULL"))
    conn.execute(text("ALTER TABLE descuento_modulo_items ADD COLUMN IF NOT EXISTS es_principal INTEGER NOT NULL DEFAULT 0"))
    conn.execute(text("ALTER TABLE descuento_modulo_items ALTER COLUMN descuento SET DEFAULT 0"))
    conn.execute(text("ALTER TABLE pedido_items ADD COLUMN IF NOT EXISTS rotacion VARCHAR(1)"))
    conn.execute(text("ALTER TABLE pedido_items ADD COLUMN IF NOT EXISTS avg_monthly DECIMAL(10,2)"))
    conn.execute(text("ALTER TABLE productos ADD COLUMN IF NOT EXISTS es_pack INTEGER NOT NULL DEFAULT 0"))
    conn.execute(text("ALTER TABLE facturas ADD COLUMN IF NOT EXISTS creado_en TIMESTAMP DEFAULT NOW()"))
    conn.execute(text("ALTER TABLE facturas ADD COLUMN IF NOT EXISTS conciliado BOOLEAN NOT NULL DEFAULT false"))
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
            actualizado_en TIMESTAMP DEFAULT NOW()
        )
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
    # Índices para queries frecuentes
    for stmt in [
        "CREATE INDEX IF NOT EXISTS idx_factura_items_factura ON factura_items(factura_id)",
        "CREATE INDEX IF NOT EXISTS idx_stock_diff_factura ON stock_differences(factura_id)",
        "CREATE INDEX IF NOT EXISTS idx_reclamos_factura ON reclamos(factura_id)",
        "CREATE INDEX IF NOT EXISTS idx_erp_stock_codigo ON erp_stock(codigo_barra)",
        "CREATE INDEX IF NOT EXISTS idx_productos_alt1 ON productos(codigo_barra_alt1)",
        "CREATE INDEX IF NOT EXISTS idx_productos_alt2 ON productos(codigo_barra_alt2)",
        "CREATE INDEX IF NOT EXISTS idx_productos_alt3 ON productos(codigo_barra_alt3)",
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
                         ('rot_baja_tol', 'DECIMAL(6,1) NOT NULL DEFAULT 0.0')]:
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
    # descuento_campanas, descuento_modulos, descuento_modulo_items los crea
    # Base.metadata.create_all(). Solo ALTER TABLE para datos viejos.
    existing_mod = {row[1] for row in conn.execute(text("PRAGMA table_info(descuento_modulos)"))}
    if 'campana_id' not in existing_mod:
        conn.execute(text("ALTER TABLE descuento_modulos ADD COLUMN campana_id INTEGER REFERENCES descuento_campanas(id) ON DELETE CASCADE"))
    existing_items = {row[1] for row in conn.execute(text("PRAGMA table_info(descuento_modulo_items)"))}
    if 'es_principal' not in existing_items:
        conn.execute(text("ALTER TABLE descuento_modulo_items ADD COLUMN es_principal INTEGER NOT NULL DEFAULT 0"))
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
    # Índices para queries frecuentes
    for stmt in [
        "CREATE INDEX IF NOT EXISTS idx_factura_items_factura ON factura_items(factura_id)",
        "CREATE INDEX IF NOT EXISTS idx_stock_diff_factura ON stock_differences(factura_id)",
        "CREATE INDEX IF NOT EXISTS idx_reclamos_factura ON reclamos(factura_id)",
        "CREATE INDEX IF NOT EXISTS idx_erp_stock_codigo ON erp_stock(codigo_barra)",
        "CREATE INDEX IF NOT EXISTS idx_productos_alt1 ON productos(codigo_barra_alt1)",
        "CREATE INDEX IF NOT EXISTS idx_productos_alt2 ON productos(codigo_barra_alt2)",
        "CREATE INDEX IF NOT EXISTS idx_productos_alt3 ON productos(codigo_barra_alt3)",
    ]:
        conn.execute(text(stmt))
