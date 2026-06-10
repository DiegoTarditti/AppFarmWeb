import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    DECIMAL,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    text,
)

_AR_TZ = timezone(timedelta(hours=-3))

def now_ar():
    """Hora actual en Argentina (UTC-3), sin tzinfo para SQLAlchemy DateTime."""
    return datetime.now(_AR_TZ).replace(tzinfo=None)
from sqlalchemy import event
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


class Config(Base):
    __tablename__ = 'configuracion'
    id = Column(Integer, primary_key=True)
    farmacia_nombre = Column(String(200), nullable=False, default='Farmacia')
    farmacia_cuit = Column(String(20), nullable=True)   # identidad para archivos (filtro droguería)
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
    # Backups automáticos (ejecutados por DockerPanel host)
    backup_ruta_remota        = Column(String(500), nullable=True)   # UNC tipo \\server-1\backups\farmacia
    backup_hora               = Column(Integer, nullable=False, default=17, server_default='17')  # 0-23
    backup_diarios_max        = Column(Integer, nullable=False, default=7, server_default='7')
    backup_semanales_max      = Column(Integer, nullable=False, default=0, server_default='0')
    backup_quincenales_max    = Column(Integer, nullable=False, default=1, server_default='1')
    backup_mensuales_max      = Column(Integer, nullable=False, default=0, server_default='0')
    # Status del último backup (lo escribe DockerPanel via API)
    backup_ultimo_status      = Column(String(10), nullable=True)    # 'OK' / 'FAIL' / NULL
    backup_ultimo_corrida_en  = Column(DateTime, nullable=True)
    backup_ultimo_error       = Column(String(500), nullable=True)
    backup_ultimo_tamano_mb   = Column(DECIMAL(10, 2), nullable=True)
    # Ruta local al ejecutable del DockerPanel (solo usada desde localhost)
    dockerpanel_ruta = Column(String(500), nullable=True)
    # Observer: cuántos meses hacia atrás trae sync_ventas_mensuales
    observer_ventas_meses = Column(Integer, nullable=False, default=16, server_default='16')
    # Transferencias entre sucursales (/transferencias): umbrales de cobertura.
    # excedente = cobertura > N meses; necesita = vende y cobertura < M meses.
    transfer_excedente_meses = Column(DECIMAL(5, 1), nullable=False, default=6.0, server_default='6.0')
    transfer_necesita_meses  = Column(DECIMAL(5, 1), nullable=False, default=2.0, server_default='2.0')


class Laboratorio(Base):
    __tablename__ = 'laboratorios'
    id = Column(Integer, primary_key=True)
    nombre = Column(String(150), nullable=False, unique=True)
    activo = Column(Boolean, nullable=False, default=True)
    observer_id = Column(Integer, nullable=True, unique=True)
    # Descuento base de la compra directa al laboratorio (cuando no se compra
    # vía droguería). Se aplica al monto estimado del flujo de fondos. NULL = 0.
    descuento_base = Column(DECIMAL(5, 2), nullable=True)
    # Si el lab maneja packs (blísters/displays) → habilita "Cargar Packs"
    # en /compras/laboratorio (lleva al import de módulo_packs del lab).
    usa_packs = Column(Boolean, nullable=False, default=False, server_default='false')
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
    # index=True quitado en laboratorio_observer/codigo_alfabeta/id_tipo_venta_control:
    # los idx_obs_prod_* (custom, declarados abajo) los cubren; index=True generaba
    # ix_* duplicados. Ver lote 3.
    laboratorio_observer   = Column(Integer, ForeignKey('obs_laboratorios.observer_id'), nullable=True)
    subrubro_observer      = Column(Integer, ForeignKey('obs_subrubros.observer_id'), nullable=True)
    nombre_droga_observer  = Column(Integer, ForeignKey('obs_nombres_drogas.observer_id'), nullable=True)
    codigo_alfabeta        = Column(String(10), nullable=True)
    id_tipo_venta_control  = Column(String(1), nullable=True)  # DW.TiposVentaYControl: L=Venta Libre, R=Bajo Receta, A=Receta Archivada, 1-4=Psicotrópico, 5-8=Estupefaciente
    # Descripción editable localmente. Si está, se muestra en lugar de `descripcion`.
    # NO se toca al sincronizar desde Observer ni al pushear a Render.
    descripcion_custom     = Column(String(200), nullable=True)
    troquel                = Column(Integer, nullable=True)
    cantidad_envase        = Column(DECIMAL(10, 3), nullable=True)
    es_habilitado_venta    = Column(Boolean, nullable=False, default=True)
    requiere_cadena_frio   = Column(Boolean, nullable=False, default=False)
    es_fraccionable        = Column(Boolean, nullable=False, default=False, server_default=text('false'))  # DW.Productos.EsFraccionable
    fecha_baja             = Column(DateTime, nullable=True)
    sync_en                = Column(DateTime, default=now_ar)
    __table_args__ = (
        Index('idx_obs_prod_lab', 'laboratorio_observer'),
        Index('idx_obs_prod_alfabeta', 'codigo_alfabeta'),
        Index('idx_obs_prod_tvc', 'id_tipo_venta_control'),
        Index('idx_obs_productos_descripcion_trgm', 'descripcion',
              postgresql_using='gin', postgresql_ops={'descripcion': 'gin_trgm_ops'}),
    )


class ObsStock(Base):
    """Stock actual por farmacia + producto (DW.StockFarmaciasProductos)."""
    __tablename__ = 'obs_stock'
    id_farmacia = Column(Integer, primary_key=True, autoincrement=False)
    producto_observer = Column(Integer, ForeignKey('obs_productos.observer_id'), primary_key=True, autoincrement=False, index=True)
    stock_actual = Column(Integer, nullable=False, default=0)
    maximo = Column(Integer, nullable=True)
    minimo = Column(Integer, nullable=True)
    fraccionado = Column(Boolean, nullable=False, default=False, server_default=text('false'))  # DW.StockFarmaciasProductos.Fraccionado
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
    __table_args__ = (
        Index('idx_obs_vtas_anio_mes', 'anio', 'mes'),
    )


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
    # index=True quitado: idx_ovd_tipo / idx_obs_vd_operador (custom) los cubren.
    tipo_operacion                 = Column(String(2), nullable=True)  # 'V'=venta, 'D'=devol., 'NC'=nota crédito
    operador_observer              = Column(String(40), nullable=True)  # IdOperador (UUID) → ObsOperador. Stats x vendedor
    sync_en                        = Column(DateTime, default=now_ar)
    __table_args__ = (
        # Single-col custom (dups de los ix_* que se removieron).
        Index('idx_ovd_tipo', 'tipo_operacion'),
        Index('idx_obs_vd_operador', 'operador_observer'),
        # Compuestos (entidad + fecha) para los reportes de ventas multi-dim.
        Index('idx_ovd_cliente_fecha', 'cliente_observer', 'fecha_estadistica'),
        Index('idx_ovd_medico_fecha', 'medico_observer', 'fecha_estadistica'),
        Index('idx_ovd_os_fecha', 'obra_social_observer', 'fecha_estadistica'),
        Index('idx_ovd_producto_fecha', 'producto_observer', 'fecha_estadistica'),
    )


class ObsOperador(Base):
    """Vendedores/operadores del POS (espejo de DW.OperadoresVenta).

    `observer_id` = IdUsuario (UUID), igual al `operador_observer` de obs_ventas_detalle.
    Se sincroniza con `observer_source.sync_operadores`."""
    __tablename__ = 'obs_operadores'
    observer_id = Column(String(40), primary_key=True, autoincrement=False)
    nombre      = Column(String(120))
    sync_en     = Column(DateTime, default=now_ar)


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


class ClienteOsConfirmada(Base):
    """OS confirmada manualmente por un operador. Toma precedencia sobre la inferida."""
    __tablename__ = 'cliente_os_confirmada'
    cliente_observer_id = Column(Integer, primary_key=True, autoincrement=False)
    obra_social_observer_id = Column(Integer, nullable=False)
    obra_social_nombre = Column(String(150), nullable=False, default='')
    confirmado_por = Column(String(80), nullable=True)
    confirmado_en = Column(DateTime, default=now_ar)


class ClienteOsInferida(Base):
    """OS principal inferida por cliente — derivada del histórico de ventas.
    DW.Clientes NO expone IdObraSocialPrincipal directamente, así que
    calculamos la OS más frecuente por cliente desde obs_ventas_detalle.

    Tabla puente: NO se toca en el sync de obs_clientes (que viene de
    Observer). Se recalcula con `recalcular_os_por_cliente()` (cron o
    manual). Permite filtros instantáneos tipo 'clientes de PAMI' sin
    escanear el detalle de ventas cada vez.
    """
    __tablename__ = 'cliente_os_inferida'
    cliente_observer       = Column(Integer, ForeignKey('obs_clientes.observer_id'),
                                    primary_key=True, autoincrement=False)
    obra_social_observer   = Column(Integer, ForeignKey('obs_obras_sociales.observer_id'),
                                    nullable=True, index=True)
    n_dispensas            = Column(Integer, nullable=False, default=0)   # cuántas veces compró con esta OS
    n_dispensas_total      = Column(Integer, nullable=False, default=0)   # total dispensas del cliente (con o sin OS)
    confianza_pct          = Column(DECIMAL(5, 2), nullable=True)         # n_dispensas / n_dispensas_total * 100
    calculado_en           = Column(DateTime, default=now_ar)


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
    """Tabla ÚNICA de clientes propios (unificación 2026-06-07, Opción A).

    Absorbe la vieja extensión editable de ObServer Y los leads locales:
    - `observer_id` NOT NULL → cliente vinculado a ObServer (datos maestros en
      `obs_clientes`, esta fila guarda la capa editable: notas/tags/whatsapp/...).
    - `observer_id` NULL → lead capturado por el bot/panel que aún no está en
      ObServer (la identidad vive acá: nombre/apellido/dni/domicilio/telefono).
    `unique` sobre observer_id permite N filas con NULL (varios leads) y una sola
    por observer_id. El resto del sistema referencia esta fila por `clientes.id`
    (un único `cliente_id`), no por el doble id viejo. Ver docs/plan_clientes_unica.md."""
    __tablename__ = 'clientes'
    id = Column(Integer, primary_key=True)
    observer_id = Column(Integer, ForeignKey('obs_clientes.observer_id'),
                         nullable=True, unique=True, index=True)  # NULL = lead puro
    # --- identidad (para leads / override editable; en clientes ObServer puede
    #     quedar vacío y se lee de obs_clientes) ---
    nombre = Column(String(80), nullable=True)
    apellido = Column(String(80), nullable=True)
    dni = Column(String(20), nullable=True, index=True)
    domicilio = Column(String(200), nullable=True)
    telefono = Column(String(35), nullable=True)
    ciudad = Column(String(120), nullable=True)
    # --- capa editable ---
    notas = Column(Text, nullable=True)
    tags = Column(String(200), nullable=True)  # comma-separated
    whatsapp = Column(String(30), nullable=True)
    email = Column(String(120), nullable=True)
    fecha_nacimiento = Column(Date, nullable=True)
    # --- meta ---
    creado_por = Column(Integer, ForeignKey('usuarios.id'), nullable=True)
    creado_en = Column(DateTime, default=now_ar)
    actualizado_en = Column(DateTime, default=now_ar, onupdate=now_ar)
    obs_cliente = relationship('ObsCliente')


class ClienteLocal(Base):
    """Cliente capturado localmente (lead) por el bot/panel cuando todavía no
    existe en ObServer (ObServer es read-only desde la web). La farmacia luego
    lo carga en su sistema; mientras tanto se usa para atender por el bot.
    Si después se vincula a ObServer, se guarda el observer_id."""
    __tablename__ = 'clientes_locales'
    id = Column(Integer, primary_key=True)
    nombre = Column(String(80), nullable=True)
    apellido = Column(String(80), nullable=True)
    dni = Column(String(20), nullable=True, index=True)
    domicilio = Column(String(200), nullable=True)
    telefono = Column(String(35), nullable=True)
    ciudad = Column(String(120), nullable=True)
    notas = Column(Text, nullable=True)
    observer_id = Column(Integer, nullable=True)   # si luego se vincula a ObServer
    creado_por = Column(Integer, ForeignKey('usuarios.id'), nullable=True)
    creado_en = Column(DateTime, default=now_ar)


def get_or_create_cliente(s, observer_id=None, lead=None, creado_por=None):
    """Resuelve la fila ÚNICA de `clientes` y devuelve su id (o None).

    - `observer_id`: get-or-create por observer_id (cliente de ObServer).
    - `lead`: dict de alta {nombre,apellido,dni,domicilio,ciudad,telefono} → crea
      fila con observer_id NULL (lead). Si además viene observer_id, completa los
      campos vacíos de la fila con los del lead (no pisa lo ya cargado).
    Opera DENTRO de la sesión `s`: hace flush, NO commit (commitea el caller).
    Es el único punto de entrada para vincular algo a un cliente por `cliente_id`."""
    lead = lead or {}
    _campos = ('nombre', 'apellido', 'dni', 'domicilio', 'ciudad', 'telefono')

    def _norm(v):
        return v.strip() if isinstance(v, str) else v

    def _aplicar_lead(c):
        for k in _campos:
            v = _norm(lead.get(k))
            if v and not getattr(c, k, None):
                setattr(c, k, v)

    if observer_id:
        c = s.query(Cliente).filter_by(observer_id=observer_id).first()
        if not c:
            c = Cliente(observer_id=observer_id, creado_por=creado_por)
            s.add(c)
        if lead:
            _aplicar_lead(c)
        s.flush()
        return c.id
    if any(_norm(lead.get(k)) for k in _campos):   # lead puro (sin observer_id)
        c = Cliente(observer_id=None, creado_por=creado_por)
        _aplicar_lead(c)
        s.add(c)
        s.flush()
        return c.id
    return None


class Ciudad(Base):
    """Catálogo de ciudades/localidades para el alta de clientes (dropdown)."""
    __tablename__ = 'ciudades'
    id = Column(Integer, primary_key=True)
    nombre = Column(String(120), nullable=False, unique=True)
    provincia = Column(String(80), nullable=True)
    activa = Column(Boolean, nullable=False, default=True)
    creado_en = Column(DateTime, default=now_ar)


class TicketCaja(Base):
    """Pedido confirmado por un operador que pasa a la cola de caja para cobrar.
    El cobro/entrega lo hace un cajero. NO procesa pago online (Meta lo prohíbe
    para farmacia): solo registra el medio de pago, como un POS interno."""
    __tablename__ = 'tickets_caja'
    id = Column(Integer, primary_key=True)
    conversacion_id = Column(Integer, ForeignKey('bot_conversaciones.id', ondelete='SET NULL'),
                             nullable=True, index=True)
    cliente_nombre = Column(String(160), nullable=True)
    cliente_id = Column(Integer, ForeignKey('clientes.id', ondelete='SET NULL'),
                        nullable=True, index=True)   # tabla única de clientes
    cliente_observer_id = Column(Integer, nullable=True)   # legacy (2a) — se dropea en 2b
    cliente_local_id = Column(Integer, nullable=True)      # legacy (2a) — se dropea en 2b
    total = Column(DECIMAL(14, 2), nullable=False, default=0)
    # confirmado → cobrado → entregado · anulado
    estado = Column(String(15), nullable=False, default='confirmado', index=True)
    forma_pago = Column(String(40), nullable=True)
    nota = Column(Text, nullable=True)
    operador_id = Column(Integer, ForeignKey('usuarios.id'), nullable=True)
    cajero_id = Column(Integer, ForeignKey('usuarios.id'), nullable=True)
    creado_en = Column(DateTime, default=now_ar, index=True)
    cobrado_en = Column(DateTime, nullable=True)


class TicketItem(Base):
    __tablename__ = 'ticket_items'
    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey('tickets_caja.id', ondelete='CASCADE'),
                       nullable=False, index=True)
    nombre = Column(String(200), nullable=False)
    detalle = Column(String(200), nullable=True)   # droga / presentación
    precio = Column(DECIMAL(14, 2), nullable=False, default=0)
    cantidad = Column(Integer, nullable=False, default=1)
    subtotal = Column(DECIMAL(14, 2), nullable=False, default=0)


class FormaPago(Base):
    """Catálogo de medios de pago (editable). Se registra al cobrar en caja."""
    __tablename__ = 'formas_pago'
    id = Column(Integer, primary_key=True)
    nombre = Column(String(40), nullable=False, unique=True)
    activa = Column(Boolean, nullable=False, default=True)
    orden = Column(Integer, nullable=False, default=0)


class EnvioTramo(Base):
    """Tarifa de envío por distancia (cuadras). `hasta_cuadras` es el límite
    superior inclusive del tramo; el último ("50 o más") usa un número grande
    como tope. Resolución: el primer tramo cuyo `hasta_cuadras` >= cuadras."""
    __tablename__ = 'envio_tramos'
    id = Column(Integer, primary_key=True)
    hasta_cuadras = Column(Integer, nullable=False)
    monto = Column(DECIMAL(12, 2), nullable=False, default=0)
    orden = Column(Integer, nullable=False, default=0)


class EnvioZona(Base):
    """Tarifa fija por zona nombrada (refinería, centro, Roldán…). PISA a los
    tramos. poligono: JSON [[lat,lng], ...] para point-in-polygon (reemplaza
    el círculo lat/lng/radio_km). lat/lng/radio_km quedan deprecados."""
    __tablename__ = 'envio_zonas'
    id = Column(Integer, primary_key=True)
    nombre = Column(String(80), nullable=False)
    monto = Column(DECIMAL(12, 2), nullable=False, default=0)
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    radio_km = Column(Float, nullable=True)
    poligono = Column(Text, nullable=True)  # JSON [[lat,lng], ...]
    activa = Column(Boolean, nullable=False, default=True)
    orden = Column(Integer, nullable=False, default=0)


class DomicilioCliente(Base):
    """Libreta de direcciones del cliente (Casa/Trabajo/Otro). Link polimórfico:
    se cuelga del cliente (observer_id o local_id) si la conversación está
    vinculada, o de la conversación si no. lat/lng del pin o del geocode."""
    __tablename__ = 'domicilios_cliente'
    id = Column(Integer, primary_key=True)
    cliente_id = Column(Integer, ForeignKey('clientes.id', ondelete='CASCADE'),
                        index=True, nullable=True)   # tabla única de clientes
    cliente_observer_id = Column(Integer, index=True, nullable=True)   # legacy (2a)
    cliente_local_id = Column(Integer,
                              ForeignKey('clientes_locales.id', ondelete='CASCADE'),
                              index=True, nullable=True)   # legacy (2a)
    conversacion_id = Column(Integer,
                             ForeignKey('bot_conversaciones.id', ondelete='CASCADE'),
                             index=True, nullable=True)
    etiqueta = Column(String(40))          # Casa | Trabajo | Otro (o libre)
    direccion = Column(String(200))        # texto legible si lo escribió
    localidad = Column(String(120))
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    piso = Column(String(20), nullable=True)        # "1", "PB", "12"
    depto = Column(String(20), nullable=True)        # "2", "B", "A"
    referencia = Column(String(200), nullable=True)  # monoblock/torre/barrio/entre-calles
    geo_actualizado_en = Column(DateTime, nullable=True)
    origen = Column(String(12))            # pin | direccion
    creado_en = Column(DateTime, default=now_ar)
    ultimo_uso_en = Column(DateTime, nullable=True)


class EnvioConfig(Base):
    """Config de envío (fila única). Coordenadas de la farmacia (origen) +
    parámetros para convertir distancia en línea recta a 'cuadras' del cadete:
    cuadras ≈ (metros / metros_por_cuadra) × factor_cuadras (rodeo de la grilla)."""
    __tablename__ = 'envio_config'
    id = Column(Integer, primary_key=True)
    farmacia_lat = Column(Float, nullable=True)
    farmacia_lng = Column(Float, nullable=True)
    factor_cuadras = Column(Float, nullable=False, default=1.3)
    metros_por_cuadra = Column(Integer, nullable=False, default=100)
    actualizado_en = Column(DateTime, default=now_ar)


class Cadete(Base):
    """Repartidor. La misma ficha sirve para asignar zonas (un cadete puede
    cubrir varias rutas) y para los pagos (tarifa por jornada)."""
    __tablename__ = 'cadetes'
    id = Column(Integer, primary_key=True)
    nombre = Column(String(80), nullable=False)
    telefono = Column(String(35), nullable=True)
    tarifa_dia = Column(DECIMAL(12, 2), nullable=True)   # jornada (para pagos)
    activo = Column(Boolean, nullable=False, default=True)
    token = Column(String(12), nullable=True, unique=True, index=True)  # link móvil
    creado_en = Column(DateTime, default=now_ar)


class RutaReparto(Base):
    """Ruta de reparto. v1: una por cuadrante (Norte/Sur/Este/Oeste) según el
    ángulo desde la farmacia. `cuadrante` es el criterio de auto-asignación."""
    __tablename__ = 'rutas_reparto'
    id = Column(Integer, primary_key=True)
    nombre = Column(String(60), nullable=False)
    cuadrante = Column(String(1), index=True)      # N | S | E | O (fallback si no hay polígono)
    # Zona real (opcional): JSON con las esquinas [[lat,lng], ...]. Si está, la
    # asignación es point-in-polygon (pisa al cuadrante).
    poligono = Column(Text, nullable=True)
    color = Column(String(9), default='#1D9E75')
    cadete = Column(String(80), nullable=True)     # (deprecado: ahora cadete_id)
    cadete_id = Column(Integer, ForeignKey('cadetes.id', ondelete='SET NULL'),
                       nullable=True, index=True)
    activa = Column(Boolean, nullable=False, default=True)
    orden = Column(Integer, nullable=False, default=0)


class PedidoReparto(Base):
    """Pedido a repartir (lo carga el operador). Se auto-asigna a la ruta del
    cuadrante de su domicilio; el operador puede reasignar a mano."""
    __tablename__ = 'pedidos_reparto'
    id = Column(Integer, primary_key=True)
    fecha = Column(Date, default=lambda: now_ar().date(), index=True)
    cliente_id = Column(Integer, ForeignKey('clientes.id', ondelete='SET NULL'),
                        nullable=True, index=True)   # tabla única de clientes
    cliente_observer_id = Column(Integer, nullable=True)   # legacy (2a) — se dropea en 2b
    cliente_local_id = Column(Integer, nullable=True)      # legacy (2a) — se dropea en 2b
    cliente_nombre = Column(String(160))
    direccion = Column(String(200))
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    nota = Column(Text, nullable=True)
    cuadrante = Column(String(1))                  # calculado
    prioridad = Column(String(12), nullable=False, default='normal')  # urgente|normal|programado
    ruta_id = Column(Integer, ForeignKey('rutas_reparto.id', ondelete='SET NULL'),
                     nullable=True, index=True)
    orden_en_ruta = Column(Integer, default=0)     # secuencia (fase 2)
    estado = Column(String(15), nullable=False, default='pendiente', index=True)
    creado_en = Column(DateTime, default=now_ar)
    # Campos nuevos de la planilla real (2026-06-07)
    tomo = Column(String(35), nullable=True)
    canal = Column(String(15), nullable=False, default='manual')
    importe = Column(DECIMAL(12, 2), nullable=True)
    forma_pago = Column(String(20), nullable=True)
    vuelto = Column(String(80), nullable=True)
    requiere_receta = Column(Boolean, nullable=False, default=False)
    pagado = Column(Boolean, nullable=False, default=False)
    turno = Column(String(6), nullable=True)
    cadete_id = Column(Integer, ForeignKey('cadetes.id', ondelete='SET NULL'),
                       nullable=True, index=True)
    entregado_por = Column(String(35), nullable=True)
    recibio = Column(String(35), nullable=True)
    observacion = Column(Text, nullable=True)
    producto = Column(String(200), nullable=True)
    envio_costo = Column(DECIMAL(10, 2), nullable=True)
    producto_observer_id = Column(Integer, nullable=True, index=True)
    piso = Column(String(20), nullable=True)
    depto = Column(String(20), nullable=True)
    referencia = Column(String(200), nullable=True)
    # WhatsApp publicación + tomado por cadete
    waha_msg_id = Column(String(120), nullable=True, index=True)
    publicado_en = Column(DateTime, nullable=True)
    tomado_por_wsap = Column(String(80), nullable=True)
    tomado_en = Column(DateTime, nullable=True)


class ObsSyncLog(Base):
    """Log de cada corrida de sync por entidad (última ejecución + resultados)."""
    __tablename__ = 'obs_sync_log'
    id = Column(Integer, primary_key=True)
    entidad = Column(String(40), nullable=False, index=True)   # 'laboratorios', 'productos', etc.
    filas_upsert = Column(Integer, nullable=False, default=0)
    duracion_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    ejecutado_en = Column(DateTime, default=now_ar)
    __table_args__ = (
        Index('idx_obs_sync_entidad', 'entidad', text('ejecutado_en DESC')),
    )


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


class AlarmaNotificada(Base):
    """Estado de notificaciones de alarmas (dedup para no spamear Telegram).

    Una fila por nombre de alarma. Cuando dispara, comparamos `ultima_notif`:
    - Si pasó MIN_GAP_HORAS (4h) → renotificar.
    - Si la alarma estaba 'resuelta' y volvió → renotificar (resucitó).
    Cuando una alarma deja de disparar, marcamos estado_actual='resuelta'.
    """
    __tablename__ = 'alarmas_notificadas'
    nombre = Column(String(120), primary_key=True)
    ultima_notif = Column(DateTime, nullable=True)
    ultima_severidad = Column(String(20), nullable=True)
    count_total = Column(Integer, nullable=False, default=0, server_default='0')
    estado_actual = Column(String(20), nullable=True)  # 'activa' / 'resuelta'


class SyncLock(Base):
    """Singleton (id=1) que protege el sync ObServer entre workers de gunicorn.

    Reemplaza al `threading.Lock` en memoria, que con `--preload --workers 2`
    no sirve: cada worker forkea su propio lock y dos workers pueden disparar
    el sync en paralelo. Acá el `acquire` es un UPDATE atómico contra la fila.

    Si `iniciado_en` quedó viejo (>60 min) sin liberar, el lock se considera
    abandonado (worker se mató mid-sync) y se puede tomar igual.
    """
    __tablename__ = 'sync_lock'
    id = Column(Integer, primary_key=True)
    en_curso = Column(Boolean, nullable=False, default=False)
    iniciado_en = Column(DateTime, nullable=True)
    finalizado_en = Column(DateTime, nullable=True)
    paso_actual = Column(String(80), nullable=True)
    ultimo_resultado = Column(Text, nullable=True)  # JSON serializado


class PanelComando(Base):
    """Buzón de comandos remotos: vos los encolás desde Render, la PC farmacia
    los ejecuta vía polling outbound (DockerPanel no necesita estar accesible).

    Flujo: estado pendiente → en_proceso → ok / error.
    """
    __tablename__ = 'panel_comandos'
    id = Column(Integer, primary_key=True)
    comando = Column(String(40), nullable=False)
    # estado: pendiente / en_proceso / ok / error
    estado = Column(String(20), nullable=False, default='pendiente', server_default='pendiente')
    solicitado_en = Column(DateTime, default=now_ar, nullable=False, server_default=func.now())
    solicitado_por = Column(String(80), nullable=True)
    tomado_en = Column(DateTime, nullable=True)
    ejecutado_en = Column(DateTime, nullable=True)
    duracion_ms = Column(Integer, nullable=True)
    resultado = Column(Text, nullable=True)
    origen = Column(String(40), nullable=True)
    __table_args__ = (
        Index('idx_panel_comandos_estado', 'estado', 'solicitado_en'),
    )


class PanelHeartbeat(Base):
    """Latido del DockerPanel: se estampa en cada poll de comandos.

    Singleton (id=1). Permite ver desde Render cuándo fue la última vez que la PC
    de la farmacia poleó el buzón → si fue hace poco, la PC está prendida.
    """
    __tablename__ = 'panel_heartbeat'
    id = Column(Integer, primary_key=True)  # singleton id=1
    ultimo_visto = Column(DateTime, nullable=True)
    origen = Column(String(40), nullable=True)


class ProductoPendienteRevision(Base):
    """Queue de items de import sin match en catálogo (o donde el user hizo Skip).

    Concentra decisiones diferidas de imports (ofertas, módulos, facturas, etc.)
    en una sola tabla revisable, en lugar de obligar a decidir "crear nuevo /
    descartar / vincular" en caliente durante el wizard.

    Estados:
      - pendiente: aún no resuelto.
      - agregado: se creó un Producto nuevo (FK en producto_creado_id).
      - vinculado: se vinculó a un Producto existente (FK en producto_vinculado_id).
      - descartado: el operador decidió no catalogarlo.

    Si re-aparece la misma descripcion_supplier desde el mismo lab/supplier,
    sumar al counter `veces_aparecido` en lugar de duplicar la fila.
    """
    __tablename__ = 'productos_pendientes_revision'
    id = Column(Integer, primary_key=True)
    # Indices custom declarados en __table_args__ al final de la clase.
    descripcion_supplier = Column(String(300), nullable=False, index=True)
    supplier_id = Column(Integer, nullable=True)                     # laboratorio/proveedor (sin FK estricto: puede venir de Producto.laboratorio_id, Provider.id, etc.)
    supplier_nombre = Column(String(200), nullable=True)
    archivo_origen = Column(String(60), nullable=True)               # 'ofertas_import' | 'modulos_import' | 'factura' | etc.
    fecha_creacion = Column(DateTime, default=now_ar, nullable=False)
    veces_aparecido = Column(Integer, nullable=False, default=1)
    score_top_candidato = Column(Float, nullable=True)               # 0.0-1.0; None si bulk no devolvió ningún candidato
    top_candidatos_json = Column(Text, nullable=True)                # snapshot JSON: [{producto_id, descripcion, score}, ...]
    oferta_data_json = Column(Text, nullable=True)                   # snapshot JSON de la oferta original que disparó el queue:
                                                                      # {descuento_psl, unidades_minima, plazo_pago, rentabilidad,
                                                                      #  vigencia_hasta, drogueria_id, observacion, archivo_origen,
                                                                      #  laboratorio_id}. Al resolver, se aplica a OfertaMinimo del
                                                                      # producto creado/vinculado para cerrar el loop import → queue → oferta.
    estado = Column(String(20), nullable=False, default='pendiente')  # pendiente / agregado / vinculado / descartado
    producto_creado_id = Column(Integer, ForeignKey('productos.id', ondelete='SET NULL'), nullable=True)
    producto_vinculado_id = Column(Integer, ForeignKey('productos.id', ondelete='SET NULL'), nullable=True)
    usuario_resuelve = Column(String(80), nullable=True)
    fecha_resolucion = Column(DateTime, nullable=True)
    # Análisis IA — sugerencias del LLM (Claude Haiku 4.5 por default).
    # Pobladas por POST /productos/pendientes-revision/analizar-ia.
    # Action: 'vincular' (con confidence>=0.85) | 'ambiguo' | 'crear_nuevo' | 'descartar'.
    # llm_pick_producto_id / llm_pick_observer_id apuntan al candidato elegido del top_candidatos_json.
    llm_analizado_en = Column(DateTime, nullable=True)
    llm_pick_producto_id = Column(Integer, nullable=True)
    llm_pick_observer_id = Column(Integer, nullable=True)
    llm_confidence = Column(Float, nullable=True)
    llm_reasoning = Column(Text, nullable=True)
    llm_action = Column(String(20), nullable=True)
    llm_modelo_usado = Column(String(60), nullable=True)
    __table_args__ = (
        Index('idx_pend_rev_supplier', 'supplier_id'),
        Index('idx_pend_rev_estado', 'estado', 'fecha_creacion'),
        Index('idx_pend_rev_llm', 'llm_analizado_en',
              postgresql_where=text('llm_analizado_en IS NULL')),
    )


class Farmacia(Base):
    """Una de las farmacias del grupo. Multi-tenant Fase 1.

    Por compatibilidad: la primera farmacia (la actual) tiene id=1 y
    `id_farmacia_observer` igual al env var OBSERVER_ID_FARMACIA.

    Cuando se sumen farmacias nuevas (ej. Pieri):
      - id=2, 3, ... etc.
      - `id_farmacia_observer` = el id real en DW.Farmacias de Observer.
      - `es_demo=True` mientras se prueba con datos sintéticos. False cuando
        se conecte el Observer real.
    """
    __tablename__ = 'farmacias'
    id = Column(Integer, primary_key=True)
    nombre = Column(String(150), nullable=False)
    razon_social = Column(String(200), nullable=True)
    cuit = Column(String(20), nullable=True, index=True)
    direccion = Column(String(300), nullable=True)
    id_farmacia_observer = Column(Integer, nullable=True, unique=True, index=True)
    es_demo = Column(Boolean, nullable=False, default=False)   # True = datos sintéticos
    activa = Column(Boolean, nullable=False, default=True)
    creado_en = Column(DateTime, default=now_ar)


class UsuarioFarmacia(Base):
    """Tabla puente: qué farmacias puede ver/operar cada usuario y con qué rol.

    Si un usuario tiene rol 'admin_grupal' en al menos una fila → puede ver
    todas las farmacias y elegir 'Vista grupal' en el selector.
    """
    __tablename__ = 'usuario_farmacias'
    usuario_id = Column(Integer, ForeignKey('usuarios.id', ondelete='CASCADE'),
                         primary_key=True)
    farmacia_id = Column(Integer, ForeignKey('farmacias.id', ondelete='CASCADE'),
                         primary_key=True)
    rol = Column(String(20), nullable=False, default='operador')
        # 'admin_grupal' — ve todas las farmacias del grupo, puede consolidar
        # 'admin'        — admin de UNA farmacia, ve solo la suya
        # 'operador'     — operador, ve solo lectura/escritura limitada
    creado_en = Column(DateTime, default=now_ar)


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


class BackupLog(Base):
    r"""Log de backups diarios obligatorios de la DB local.

    El DockerPanel al iniciar chequea si hay una fila con `fecha=hoy AND ok=true`.
    Si no, dispara `pg_dump -Fc` a `\\server-1\D\RespaldoFarmWeb\farmacia_<fecha>.dump`
    y registra el resultado acá. Permite saber con un SELECT si falta backup
    aunque la share esté caída, y mantiene historial/auditoría de éxitos y errores
    para investigar después. Rotación: archivos > 30 días se eliminan del share.
    """
    __tablename__ = 'backup_log'
    id           = Column(Integer, primary_key=True)
    fecha        = Column(Date, nullable=False, index=True)
    # server_default además del default Python: así un INSERT crudo (DockerPanel)
    # que no setee creado_en no viola el NOT NULL en DBs nuevas.
    creado_en    = Column(DateTime, default=now_ar, server_default=text('now()'), nullable=False)
    destino      = Column(Text, nullable=True)
    tamano_bytes = Column(BigInteger, nullable=True)
    ok           = Column(Boolean, nullable=False, default=False)
    error        = Column(Text, nullable=True)


class ExportTemplate(Base):
    __tablename__ = 'export_templates'
    laboratorio_id = Column(Integer, ForeignKey('laboratorios.id'), primary_key=True)
    columns_json  = Column(Text, nullable=False, default='[]')
    custom_header = Column(String(200))


class OfertaMinimo(Base):
    """Tabla de descuentos por producto + lab.

    El nombre histórico es 'minimo' pero hoy contiene los DOS tipos:
    - tipo_descuento='simple': oferta con mínimo 1 (unidades_minima=1). Una
      oferta "simple sin mínimo" es equivalente a mínimo 1.
    - tipo_descuento='con_minimo': el descuento solo aplica si se compra >= unidades_minima.

    Toda oferta importada se normaliza a unidades_minima >= 1 (ver
    `helpers.normalizar_unidades_minima`). El análisis de pedido (`/order/<id>`)
    lee de acá y filtra por lab + tipo.
    """
    __tablename__ = 'ofertas_minimo'
    id              = Column(Integer, primary_key=True)
    # nullable=True: en ofertas multi-lab por droguería el lab se deduce por
    # producto (puede no encontrarse) o queda directamente NULL.
    laboratorio_id  = Column(Integer, ForeignKey('laboratorios.id'), nullable=True, index=True)
    ean             = Column(String(20), nullable=False)
    descripcion     = Column(String(300))
    codigo          = Column(String(50))
    unidades_minima = Column(Integer)
    descuento_psl   = Column(DECIMAL(6, 2))
    rentabilidad    = Column(DECIMAL(6, 2))
    plazo_pago      = Column(String(100))
    grupo_id        = Column(Integer)
    tipo_descuento  = Column(String(20))   # 'simple' | 'con_minimo'
    # === Fase 2 compra rápida (2026-04-27) ===
    # Droguería a la que aplica este transfer/oferta. NULL = aplica a venta DIRECTA
    # al laboratorio (sin pasar por droguería). Si está cargado → solo aplica
    # cuando el optimizador evalúa esa droguería para este producto.
    # index=True quitado: idx_ofertas_drog (custom) cubre drogueria_id; index=True
    # generaba ix_ofertas_minimo_drogueria_id duplicado. Ver lote 3.
    drogueria_id    = Column(Integer, ForeignKey('proveedores.id'), nullable=True)
    # Vigencia: el optimizador filtra automáticamente los vencidos.
    vigencia_desde  = Column(Date, nullable=True)
    # index=True quitado: idx_ofertas_vig (custom) cubre vigencia_hasta.
    vigencia_hasta  = Column(Date, nullable=True)
    # Categoría/observación libre (ej "TR Lanzamiento", "TRs OTC", "TR Excepcional").
    observacion     = Column(String(200), nullable=True)
    # Activación manual (para "pausar" sin borrar)
    activo          = Column(Boolean, nullable=False, default=True)
    actualizado_en  = Column(DateTime, default=now_ar)
    __table_args__ = (
        Index('idx_ofertas_drog', 'drogueria_id'),
        Index('idx_ofertas_vig', 'vigencia_hasta'),
        Index('idx_ofertas_minimo_lab_tipo', 'laboratorio_id', 'tipo_descuento'),
    )


class ParserOfertasLab(Base):
    """Formato/parser de import de ofertas aprendido POR LABORATORIO.

    Permite que el wizard /ofertas/import recuerde, por lab, qué columna del
    Excel es cada campo (y el tipo de formato) → al re-importar ese lab no hay
    que volver a mapear. Es el equivalente, para ofertas, del "guardar parser"
    por proveedor del conversor de facturas (parsers/<slug>.py), pero acá
    alcanza con persistir el mapeo (no hace falta generar código).

    - `column_mapping`: JSON {campo: nombre_header}. Se guarda por NOMBRE de
      header (no por índice) para sobrevivir reordenamientos de columnas.
    - `formato`: 'plano' | 'bernabo_grupos' (grupos por filas vacías + mínimo
      compartido). El wiring del parseo Bernabó es una segunda etapa.
    """
    __tablename__ = 'parser_ofertas_lab'
    laboratorio_id = Column(Integer, ForeignKey('laboratorios.id'), primary_key=True)
    column_mapping = Column(Text, nullable=False, default='{}', server_default='{}')
    formato        = Column(String(30), nullable=False, default='plano', server_default='plano')
    header_row     = Column(Integer, nullable=True)
    creado_por     = Column(String(80), nullable=True)
    actualizado_en = Column(DateTime, default=now_ar)


class ArchivoCompartido(Base):
    """Buzón de salida de cada farmacia: datasets curados para compartir.

    Modelo peer-to-peer (sin hub): cada instancia publica acá (INSERT local) y
    las otras sucursales lo LEEN read-only vía `Sucursal.url_externa` (mismo
    patrón que /transferencias). Lo consumido se registra en `CompartidoImportado`.
    Tipos soportados: 'oferta_minimo', 'modulos', 'equivalencias'.
    """
    __tablename__ = 'archivos_compartidos'
    id              = Column(Integer, primary_key=True)
    tipo            = Column(String(50), nullable=False, index=True)
    nombre          = Column(String(200), nullable=False)
    descripcion     = Column(Text, nullable=True)
    farmacia_origen = Column(String(100), nullable=False)
    destinatarios   = Column(String(200), nullable=False, default='todos', server_default='todos')  # 'todos' o slugs csv
    json_data       = Column(Text, nullable=False)
    n_items         = Column(Integer, default=0)
    creado_en       = Column(DateTime, default=now_ar)


class CompartidoImportado(Base):
    """Log LOCAL de lo que esta instancia ya consumió de los peers.

    Evita re-avisar/re-importar. `origen_slug` = slug de la sucursal de la que
    vino; `archivo_id` = id en el `archivos_compartidos` de ESE peer.
    """
    __tablename__ = 'compartido_importado'
    id          = Column(Integer, primary_key=True)
    origen_slug = Column(String(50), nullable=False)
    archivo_id  = Column(Integer, nullable=False)
    tipo        = Column(String(50))
    nombre      = Column(String(200))
    accion      = Column(String(20), nullable=False, default='importado',
                         server_default='importado')  # 'importado' | 'descartado'
    usuario     = Column(String(80))
    creado_en   = Column(DateTime, nullable=False, default=now_ar, server_default=func.now())
    __table_args__ = (UniqueConstraint('origen_slug', 'archivo_id',
                                       name='uq_compartido_importado'),)


class Sucursal(Base):
    """Registro de sucursales del grupo para /transferencias (comparador N-way).

    Cada fila = una sucursal con su DB. `url_externa` es la URL de conexión
    (externa de Render: funciona desde local Y desde Render). La instancia
    compara su DB local (DATABASE_URL) contra las otras del registro.
    Reemplaza el viejo BADIA_DATABASE_URL.
    """
    __tablename__ = 'sucursales'
    id             = Column(Integer, primary_key=True)
    slug           = Column(String(50), unique=True, nullable=False)  # 'badia', 'pieri'
    nombre         = Column(String(100), nullable=False)              # display: 'Badia'
    app_name       = Column(String(100), nullable=True)               # 'farmacia-web'
    db_name        = Column(String(100), nullable=True)               # 'farmacia_yhvp'
    url_externa    = Column(Text, nullable=True)                      # URL de conexión (externa)
    activa         = Column(Boolean, nullable=False, default=True)
    actualizado_en = Column(DateTime, default=now_ar)


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
    compra_minima_pesos     = Column(DECIMAL(14, 2), nullable=True)
    # Descuento base de la droguería (independiente del lab). NULL = sin acuerdo cargado.
    descuento_con_transfer  = Column(DECIMAL(5, 2), nullable=True)
    descuento_sin_transfer  = Column(DECIMAL(5, 2), nullable=True)
    matriz_visible = Column(Boolean, nullable=False, default=True)
    matriz_orden   = Column(Integer, nullable=True)
    # Si el proveedor maneja packs (blísters/displays) → habilita "Cargar Packs".
    usa_packs = Column(Boolean, nullable=False, default=False, server_default='false')
    # Filtro droguería: config del archivo de pedido (antes hardcodeada en DROG_CFG).
    codcli          = Column(String(20), nullable=True)    # código de cliente con esta droguería
    formato_archivo = Column(String(20), nullable=True)    # 'ped' | 'txt20j' | None
    sufijo          = Column(String(10), nullable=True)    # 'KEL' | '20J'
    carpeta_filtro  = Column(String(200), nullable=True)   # 'P:\\Kellerhoff'
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
    # Descuento aplicable cuando NO hay transfer/oferta vigente para el producto.
    # Suele ser un poco mayor que descuento_pct (que se usa combinado con la oferta).
    # Si NULL → la pantalla cae a descuento_pct también para el caso "sin transfer".
    descuento_pct_sin_transfer = Column(DECIMAL(5, 2), nullable=True)


class UsuarioPedido(Base):
    """Operadores que intervienen en el flujo de pedidos (sin login completo)."""
    __tablename__ = 'usuarios_pedidos'
    id     = Column(Integer, primary_key=True)
    nombre = Column(String(50), nullable=False, unique=True)
    activo = Column(Boolean, nullable=False, default=True)


class PedidoEmitido(Base):
    """Pedido enviado a una droguería desde Compra del Día.

    Snapshot del armado al momento de "Generar pedido". Permite tracking
    de recepción manual hasta que tengamos sync real de ingresos.

    Estados:
    - ABIERTO: emitido, sin recibir nada.
    - RECIBIDO_PARCIAL: al menos un item con cantidad_recibida > 0.
    - CERRADO: todos los items con estado != 'PENDIENTE'.
    """
    __tablename__ = 'pedido_emitido'
    id              = Column(Integer, primary_key=True)
    drogueria_id    = Column(Integer, ForeignKey('proveedores.id', ondelete='CASCADE'),
                              nullable=False, index=True)
    fecha           = Column(DateTime, default=now_ar, nullable=False, index=True)
    usuario         = Column(String(50), nullable=True)
    total_items     = Column(Integer, nullable=False, default=0)
    total_unidades  = Column(Integer, nullable=False, default=0)
    estado          = Column(String(20), nullable=False, default='ABIERTO')
    observacion     = Column(Text, nullable=True)
    emitido_por     = Column(String(50), nullable=True)
    recibido_por    = Column(String(50), nullable=True)
    cargado_por     = Column(String(50), nullable=True)
    # Sigla que identifica desde dónde se disparó el pedido (P.Dia.Drog, P.Dia.Lab, etc).
    origen          = Column(String(20), nullable=True, index=True)
    drogueria       = relationship('Provider')
    items           = relationship('PedidoEmitidoItem', back_populates='pedido',
                                   cascade='all, delete-orphan')


class PedidoEmitidoItem(Base):
    """Detalle de un PedidoEmitido — un renglón por producto pedido."""
    __tablename__ = 'pedido_emitido_item'
    id                  = Column(Integer, primary_key=True)
    pedido_id           = Column(Integer, ForeignKey('pedido_emitido.id', ondelete='CASCADE'),
                                  nullable=False, index=True)
    observer_id         = Column(Integer, nullable=True, index=True)
    producto_id_local   = Column(Integer, ForeignKey('productos.id', ondelete='SET NULL'),
                                  nullable=True, index=True)
    descripcion         = Column(String(200), nullable=False)
    lab_nombre          = Column(String(150), nullable=True)
    cantidad_pedida     = Column(Integer, nullable=False, default=0)
    # Dos vías de recepción:
    # 1) Primera revisión: el operador marca manualmente al recibir (típicamente
    #    sólo toca lo que NO entró; lo demás queda en cantidad_pedida).
    cantidad_revisada_op   = Column(Integer, nullable=True)
    revisada_en            = Column(DateTime, nullable=True)
    # 2) Confirmación: se llena desde el ingreso real de ObServer (cuando esté
    #    disponible). Pisa la primera revisión si difiere.
    cantidad_confirmada_obs = Column(Integer, nullable=True)
    confirmada_en           = Column(DateTime, nullable=True)
    # Canónico = COALESCE(confirmada_obs, revisada_op, 0). Mantengo cantidad_recibida
    # como cache para queries simples — se actualiza al guardar revisión/confirmación.
    cantidad_recibida   = Column(Integer, nullable=False, default=0)
    estado              = Column(String(20), nullable=False, default='PENDIENTE')  # PENDIENTE/RECIBIDO/NO_VINO
    # Foto del TRF al momento de emitir — persiste aunque el transfer se borre después.
    oferta_dto          = Column(DECIMAL(6, 2), nullable=True)   # % descuento esperado
    oferta_min          = Column(Integer, nullable=True)          # unidades mínimas para activarlo
    pedido              = relationship('PedidoEmitido', back_populates='items')


class LaboratorioDrogueria(Base):
    """Match simple lab × droguería: indica por qué drogerías se pide cada lab.

    Independiente de DescuentoBase (que tiene el % de descuento). Sirve para
    saber rápido si un producto cae en el armado de una droguería.
    Un lab puede ir por más de una droguería (ej. NUTRICIA BAGÓ por Kel y 20J).
    """
    __tablename__ = 'laboratorio_drogueria'
    id              = Column(Integer, primary_key=True)
    laboratorio_id  = Column(Integer, ForeignKey('laboratorios.id', ondelete='CASCADE'),
                              nullable=False, index=True)
    drogueria_id    = Column(Integer, ForeignKey('proveedores.id', ondelete='CASCADE'),
                              nullable=False, index=True)
    creado_en       = Column(DateTime, default=now_ar)
    laboratorio     = relationship('Laboratorio')
    drogueria       = relationship('Provider')
    __table_args__ = (
        UniqueConstraint('laboratorio_id', 'drogueria_id', name='uq_lab_drog'),
    )


class ProveedorHorarioReparto(Base):
    """Horarios de cierre/reparto por droguería.

    Una fila por slot semanal. dia_semana: 0=Lunes, 1=Martes, ... 6=Domingo.
    hora: hora de cierre (después de eso entra al próximo reparto).
    """
    __tablename__ = 'proveedor_horarios_reparto'
    id            = Column(Integer, primary_key=True)
    # index=True quitado: el DDL real usa el índice custom idx_horarios_prov
    # (declarado abajo). Con index=True SQLAlchemy generaba ADEMÁS un
    # ix_proveedor_horarios_reparto_proveedor_id duplicado. Ver lote 3.
    proveedor_id  = Column(Integer, ForeignKey('proveedores.id', ondelete='CASCADE'),
                           nullable=False)
    dia_semana    = Column(Integer, nullable=False)   # 0-6
    hora          = Column(String(5), nullable=False)  # 'HH:MM' formato 24h, simple
    activo        = Column(Boolean, nullable=False, default=True)
    creado_en     = Column(DateTime, default=now_ar)
    proveedor     = relationship('Provider')
    __table_args__ = (
        UniqueConstraint('proveedor_id', 'dia_semana', 'hora', name='uq_horario_prov_dia_hora'),
        Index('idx_horarios_prov', 'proveedor_id'),
    )


class ProveedorCronograma(Base):
    """Cronograma de pedidos por proveedor (lab/drog).

    Permite registrar la cadencia esperada de pedidos por proveedor y tipo:
    - tipo_pedido='reposicion': pedidos cortos diarios. `horas_entre_pedidos`
      define el espaciamiento mínimo entre dos repos en el día (ajuste fino
      de cantidades). `cadencia_dias` típicamente 1.
    - tipo_pedido='programado': pedido grande con módulos / mejor descuento
      (ej. Roemmers cada 15 días). `cadencia_dias` define el espaciamiento
      base, `proxima_fecha` el override manual para mover una emisión puntual
      sin romper la serie.

    Un proveedor puede tener filas de ambos tipos (Roemmers como lab hace
    programado, y como drog vía Kellerhoff hace reposición → 2 filas).
    """
    __tablename__ = 'proveedor_cronograma'
    id                  = Column(Integer, primary_key=True)
    # partner_tipo + proveedor_id forman partner polimórfico. Mismo patrón que
    # Pedido.canal+partner_id (database.py:1124-1131): sin FK estricto a una
    # tabla específica — id apunta a `laboratorios` o `proveedores` según tipo.
    # index=True quitado en partner_tipo: el DDL real usa idx_cronograma_partner_tipo
    # (declarado abajo); index=True generaba un ix_*_partner_tipo duplicado. Ver
    # lote 3. proveedor_id/canal_drog_id/proxima_fecha NO tienen dup → conservan
    # index=True (su ix_* es el único índice).
    partner_tipo        = Column(String(12), nullable=False, default='drogueria')
    proveedor_id        = Column(Integer, nullable=False, index=True)
    # Solo cuando partner_tipo='laboratorio': por qué droguería entra el pedido.
    # NULL = compra directa al laboratorio (sin intermediario).
    canal_drog_id       = Column(Integer, ForeignKey('proveedores.id', ondelete='SET NULL'),
                                  nullable=True, index=True)
    tipo_pedido         = Column(String(15), nullable=False)  # 'programado' por ahora
    cadencia_dias       = Column(Integer, nullable=True)  # NULL = manual
    proxima_fecha       = Column(Date, nullable=True, index=True)
    horas_entre_pedidos = Column(Integer, nullable=True)  # legacy reposicion, no se usa
    activo              = Column(Boolean, nullable=False, default=True)
    notas               = Column(Text, nullable=True)
    creado_en           = Column(DateTime, default=now_ar)
    actualizado_en      = Column(DateTime, default=now_ar, onupdate=now_ar)
    canal_drogueria     = relationship('Provider', foreign_keys=[canal_drog_id])
    __table_args__ = (
        UniqueConstraint('partner_tipo', 'proveedor_id', 'tipo_pedido',
                         name='uq_cronograma_partner_tipo'),
        Index('idx_cronograma_partner_tipo', 'partner_tipo'),
    )


class TipoPedidoConfig(Base):
    """Matriz de comportamiento configurable. Dos categorías:

    categoria='pedido': REPOSICION, COMPRA_LAB, etc.
      config_json keys: piso_ideal, target_horizonte, buffer_pct, universo,
                        override_producto, redondeo, dias_cobertura_fijo.

    categoria='flag': DISCONTINUADO, REEMPLAZADO, SIN_DESCUENTO, NOTA, etc.
      config_json keys: efecto_armado ('excluir'|'badge_cero'|'solo_badge'|'ninguno'),
                        icono (emoji), color ('red'|'amber'|'violet'|'sky'|'gray'),
                        permite_reemplazo (bool), permite_vigencia (bool).
    """
    __tablename__ = 'tipo_pedido_config'
    id             = Column(Integer, primary_key=True)
    slug           = Column(String(30), nullable=False, unique=True, index=True)
    nombre         = Column(String(80), nullable=False)
    descripcion    = Column(Text, nullable=True)
    config_json    = Column(Text, nullable=False)
    categoria      = Column(String(20), nullable=False, default='pedido')  # 'pedido' | 'flag'
    activo         = Column(Boolean, nullable=False, default=True)
    creado_en      = Column(DateTime, default=now_ar)
    actualizado_en = Column(DateTime, default=now_ar, onupdate=now_ar)


class ProductoFlag(Base):
    """Comportamiento excepcional por producto (EAN) o laboratorio.

    Referencia un tipo de flag via flag_slug (slug de TipoPedidoConfig con
    categoria='flag'). El efecto concreto en el armado lo define config_json
    del tipo (efecto_armado, etc.).
    """
    __tablename__ = 'producto_flags'
    id              = Column(Integer, primary_key=True)
    flag_slug       = Column(String(30), nullable=False, index=True)
    ean             = Column(String(30), nullable=True, index=True)
    laboratorio_id  = Column(Integer, ForeignKey('laboratorios.id', ondelete='CASCADE'),
                              nullable=True, index=True)
    nota            = Column(Text, nullable=True)
    ean_reemplazo   = Column(String(30), nullable=True)
    vigente_hasta   = Column(Date, nullable=True)
    creado_en       = Column(DateTime, default=now_ar)
    creado_por      = Column(String(80), nullable=True)
    laboratorio     = relationship('Laboratorio')


class KellerhoffCatalogo(Base):
    """Snapshot del catálogo de productos de Kellerhoff (CSV que ellos exportan).

    `codigo_kellerhoff` es la llave estable con la que Kellerhoff identifica sus
    SKUs (los EANs nuestros no siempre coinciden). Se reemplaza por completo en
    cada import. Puentes para resolver equivalencias: ean, alfabeta, troquel.
    Ver docs/kellerhoff_equivalencias.md.
    """
    __tablename__ = 'kellerhoff_catalogo'
    codigo_kellerhoff = Column(String(20), primary_key=True)
    tipo              = Column(String(1), nullable=True)        # D (ético) / P (perfumería)
    descripcion       = Column(String(200), nullable=True)
    alfabeta          = Column(String(15), nullable=True, index=True)
    troquel           = Column(String(15), nullable=True, index=True)
    ean               = Column(String(20), nullable=True, index=True)  # 212 sin EAN
    laboratorio       = Column(String(120), nullable=True)
    precio            = Column(DECIMAL(14, 2), nullable=True)
    neto              = Column(Boolean, nullable=False, default=False)
    cadena_frio       = Column(Boolean, nullable=False, default=False)
    requiere_vale     = Column(Boolean, nullable=False, default=False)
    trazable          = Column(Boolean, nullable=False, default=False)
    importado_en      = Column(DateTime, default=now_ar)


class KellerhoffEquivalencia(Base):
    """Puente nuestro EAN → codigo_kellerhoff para los casos que NO matchean
    directo por EAN contra el catálogo. Solo guarda los rescatados (alfabeta/
    troquel/nombre) y los resueltos a mano. Los directos se resuelven al vuelo.
    """
    __tablename__ = 'kellerhoff_equivalencia'
    id                = Column(Integer, primary_key=True)
    ean               = Column(String(30), nullable=False, unique=True, index=True)
    codigo_kellerhoff = Column(String(20), nullable=False)
    metodo            = Column(String(12), nullable=True)   # alfabeta / troquel / nombre / manual
    confianza         = Column(String(8), nullable=True)    # ALTA / MEDIA / BAJA
    revisado          = Column(Boolean, nullable=False, default=False)
    creado_en         = Column(DateTime, default=now_ar)
    creado_por        = Column(String(80), nullable=True)


class PedidoBorrador(Base):
    """Borrador de pedido en armado por usuario y droguería.

    Persiste el "A pedir" durante la sesión de armado para que sobreviva refreshes
    y cambios de droguería sin perder el trabajo. Una fila por (drog × producto).
    """
    __tablename__ = 'pedido_borrador'
    id              = Column(Integer, primary_key=True)
    # index=True quitado en drogueria_id/observer_id: idx_borrador_drog/_obs
    # (custom) los cubren; index=True generaba ix_* duplicados. producto_id
    # conserva index=True (su ix_pedido_borrador_producto_id es el único índice).
    drogueria_id    = Column(Integer, ForeignKey('proveedores.id', ondelete='CASCADE'),
                              nullable=False)
    producto_id     = Column(Integer, ForeignKey('productos.id', ondelete='CASCADE'),
                              nullable=True, index=True)
    observer_id     = Column(Integer, nullable=True)  # ObsProducto.observer_id si aún no hay Producto local
    laboratorio_id  = Column(Integer, ForeignKey('laboratorios.id'), nullable=True)
    cantidad        = Column(Integer, nullable=False, default=0)
    dto_aplicado    = Column(DECIMAL(5, 2), nullable=True)  # snapshot del % aplicado al armar
    motivo          = Column(String(40), nullable=True)     # 'transfer', 'sin_transfer', 'manual'
    actualizado_en  = Column(DateTime, default=now_ar, onupdate=now_ar)
    drogueria       = relationship('Provider')
    laboratorio     = relationship('Laboratorio')
    __table_args__ = (
        UniqueConstraint('drogueria_id', 'producto_id', 'observer_id',
                          name='uq_borrador_drog_prod'),
        Index('idx_borrador_drog', 'drogueria_id'),
        Index('idx_borrador_obs', 'observer_id'),
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
    tipo_comprobante = Column(String(10), nullable=False, default='FAC')  # FAC / NCR / PREFAC
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
    # index=True quitado: idx_factura_items_factura (custom) cubre factura_id.
    factura_id = Column(Integer, ForeignKey('facturas.id'), nullable=False)
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
    __table_args__ = (
        Index('idx_factura_items_factura', 'factura_id'),
    )


class FacturaFaltante(Base):
    """Ítems en falta momentánea / no facturados de una factura importada.

    NUNCA suman al total ni van a `factura_items`. Se guardan aparte para
    cruzar con `pedidos`/`pedido_items` por codigo_barra (faltantes de droguería).
    """
    __tablename__ = 'factura_faltante'
    id = Column(Integer, primary_key=True)
    # index=True quitado: idx_factura_faltante_fac/_cb (custom) cubren estas cols.
    factura_id = Column(Integer, ForeignKey('facturas.id', ondelete='CASCADE'), nullable=False)
    codigo_barra = Column(String(20))
    codigo_interno = Column(String(30))
    cantidad = Column(Integer)
    descripcion = Column(String(150))
    creado_en = Column(DateTime, default=now_ar, server_default=func.now())
    __table_args__ = (
        Index('idx_factura_faltante_fac', 'factura_id'),
        Index('idx_factura_faltante_cb', 'codigo_barra'),
    )


class ErpStock(Base):
    __tablename__ = 'erp_stock'
    id = Column(Integer, primary_key=True)
    # index=True quitado: idx_erp_stock_codigo (custom) cubre codigo_barra.
    codigo_barra = Column(String(20), nullable=False)
    descripcion = Column(String(150))
    cantidad = Column(Integer)
    precio_unitario = Column(DECIMAL(14, 2))
    __table_args__ = (
        Index('idx_erp_stock_codigo', 'codigo_barra'),
    )


class StockDifference(Base):
    __tablename__ = 'stock_differences'
    id = Column(Integer, primary_key=True)
    # index=True quitado: idx_stock_diff_factura (custom) cubre factura_id.
    factura_id = Column(Integer, ForeignKey('facturas.id'), nullable=False)
    codigo_barra = Column(String(20))
    descripcion = Column(String(150))
    cantidad_factura = Column(Integer)
    cantidad_erp = Column(Integer)
    diferencia = Column(Integer)
    observaciones = Column(Text)
    claim_items = relationship('ClaimItem', back_populates='difference')
    __table_args__ = (
        Index('idx_stock_diff_factura', 'factura_id'),
    )


class Claim(Base):
    __tablename__ = 'reclamos'
    id = Column(Integer, primary_key=True)
    proveedor_id = Column(Integer, ForeignKey('proveedores.id'), nullable=False)
    # index=True quitado: idx_reclamos_factura (custom) cubre factura_id.
    factura_id = Column(Integer, ForeignKey('facturas.id'))
    numero_factura = Column(String(20))
    fecha = Column(Date, nullable=False)
    estado = Column(String(20), nullable=False, default='ABIERTO')
    creado_en = Column(DateTime, default=now_ar)
    provider = relationship('Provider', back_populates='claims')
    factura = relationship('Invoice')
    items = relationship('ClaimItem', back_populates='claim')
    __table_args__ = (
        Index('idx_reclamos_factura', 'factura_id'),
    )


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


class EquivalenciaProveedor(Base):
    """Mapea (lab/drog + descripcion o codigo del proveedor) → Producto local.

    Distinto de BarcodeMapping (que mapea códigos cortos de factura → EAN).
    Acá guardamos la equivalencia que el operador genera al resolver items
    en el queue de pendientes (Vincular / Aplicar IA / chip click) o al
    confirmar el wizard de ofertas. La próxima vez que entre el mismo Excel,
    el matcher consulta esta tabla ANTES del fuzzy → 0% ambigüedad.

    Scope: lab_id O drogueria_id (al menos uno seteado). Para ofertas vía
    droguería (Ciafarma multi-lab), se guarda con drogueria_id; para ofertas
    directas de lab, con laboratorio_id.

    Match keys (cada equivalencia puede tener una o las dos):
    - `codigo_proveedor`: código interno corto del archivo (ej. "AX-123").
      Match más confiable que descripción.
    - `descripcion_proveedor_norm`: lowercase + sin acentos + sin puntuación.

    Lookup en `producto_matcher`: primero codigo (si vino en el item nuevo),
    después desc_norm. Si encuentra → estrategia='equivalencia_aprendida',
    score=1.0.
    """
    __tablename__ = 'equivalencias_proveedor'
    id = Column(Integer, primary_key=True)
    # Nullable: o lab o drog, al menos uno tiene que estar.
    # Indices custom declarados en __table_args__ (incluyen partial uniques).
    laboratorio_id = Column(Integer, ForeignKey('laboratorios.id', ondelete='CASCADE'),
                            nullable=True)
    drogueria_id = Column(Integer, ForeignKey('proveedores.id', ondelete='CASCADE'),
                          nullable=True)
    descripcion_proveedor = Column(String(200), nullable=True)
    descripcion_proveedor_norm = Column(String(200), nullable=True)
    codigo_proveedor = Column(String(50), nullable=True)
    producto_id = Column(Integer, ForeignKey('productos.id', ondelete='SET NULL'),
                         nullable=True, index=True)
    creado_en = Column(DateTime, default=now_ar)
    laboratorio = relationship('Laboratorio')
    producto = relationship('Producto')
    __table_args__ = (
        # UC legacy (solo aplica cuando lab está seteado).
        UniqueConstraint('laboratorio_id', 'descripcion_proveedor_norm',
                         name='uq_equiv_lab_desc'),
        Index('idx_equiv_codigo', 'codigo_proveedor'),
        Index('idx_equiv_drog', 'drogueria_id'),
        Index('uq_equiv_drog_codigo', 'drogueria_id', 'codigo_proveedor', unique=True,
              postgresql_where=text('(drogueria_id IS NOT NULL) AND (codigo_proveedor IS NOT NULL)')),
        Index('uq_equiv_drog_desc', 'drogueria_id', 'descripcion_proveedor_norm', unique=True,
              postgresql_where=text('(drogueria_id IS NOT NULL) AND (descripcion_proveedor_norm IS NOT NULL)')),
        Index('uq_equiv_lab_codigo', 'laboratorio_id', 'codigo_proveedor', unique=True,
              postgresql_where=text('(laboratorio_id IS NOT NULL) AND (codigo_proveedor IS NOT NULL)')),
    )


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
    # Indices custom declarados en __table_args__ (no usamos index=True
    # para que el nombre matchee el DDL real `idx_productos_*`).
    codigo_barra_alt1 = Column(String(20))
    codigo_barra_alt2 = Column(String(20))
    codigo_barra_alt3 = Column(String(20))
    es_pack = Column(Integer, nullable=False, default=0)   # 1 = es un pack de unidades
    precio_pvp = Column(DECIMAL(14, 2))
    laboratorio_id = Column(Integer, ForeignKey('laboratorios.id'), nullable=True)
    laboratorio = relationship('Laboratorio')
    # Puente a ObServer: único nexo entre EAN (local) y IdProducto (ObServer).
    # Unicidad implementada vía Index parcial `uq_productos_observer_id`
    # (WHERE observer_id IS NOT NULL). NULL permitido múltiple — productos
    # todavía no vinculados a ObServer.
    observer_id = Column(Integer, ForeignKey('obs_productos.observer_id'),
                         nullable=True)
    obs_producto = relationship('ObsProducto')
    # Código Alfabeta (vademécum). Bridge robusto con obs_productos.codigo_alfabeta.
    codigo_alfabeta = Column(String(10), nullable=True)
    monodroga = Column(String(200), nullable=True)
    presentacion = Column(String(500), nullable=True)
    accion_terapeutica = Column(String(200), nullable=True)
    actualizado_en = Column(DateTime, default=now_ar)
    ultima_compra = Column(Date, nullable=True)
    fuente_creacion = Column(String(30), nullable=True)   # 'oferta_import' / 'manual' / NULL
    # Compra rápida v2: exclusiones manuales del armado de pedido.
    excluido_armado_actual = Column(Boolean, nullable=False, default=False)
    no_pedir               = Column(Boolean, nullable=False, default=False)
    # Fraccionado: se vende de a unidad suelta (ej. 1 sobre) pero se pide por
    # envase completo (caja de N). El "a pedir" debe convertir unidades vendidas
    # → envases usando ProductoAtributo.cantidad_envase. Se configura a mano
    # producto por producto desde /productos/flags (tarjeta Presentación).
    fraccionado            = Column(Boolean, nullable=False, default=False)
    # Cronograma de pedidos: cantidad fija a pedir cuando se llega al punto
    # de pedido. NULL = cálculo dinámico (default). Útil para productos donde
    # el operador ya sabe la dosis óptima de reposición.
    cantidad_reposicion_fija = Column(Integer, nullable=True)
    codigos_barra = relationship('ProductoCodigoBarra',
                                 back_populates='producto',
                                 cascade='all, delete-orphan',
                                 order_by='desc(ProductoCodigoBarra.es_principal), ProductoCodigoBarra.id')
    __table_args__ = (
        Index('idx_productos_alt1', 'codigo_barra_alt1'),
        Index('idx_productos_alt2', 'codigo_barra_alt2'),
        Index('idx_productos_alt3', 'codigo_barra_alt3'),
        Index('idx_productos_alfabeta', 'codigo_alfabeta'),
        Index('idx_productos_observer_id', 'observer_id'),
        Index('uq_productos_observer_id', 'observer_id', unique=True,
              postgresql_where=text('observer_id IS NOT NULL')),
        Index('idx_prod_no_pedir', 'no_pedir',
              postgresql_where=text('no_pedir = true')),
    )


class ProductoCodigoBarra(Base):
    """EANs de un producto en una relación 1-a-N (reemplazo gradual de alt1/2/3).

    Uno de los registros tiene `es_principal=True`; ese se sincroniza con
    `Producto.codigo_barra` para no romper código viejo. El resto son
    alternativos (sin límite a diferencia de los 3 slots fijos).

    Trazabilidad: cada EAN sabe de dónde vino (`fuente` + `factura_id`).
    """
    __tablename__ = 'producto_codigos_barra'
    id           = Column(Integer, primary_key=True)
    # Indices custom declarados en __table_args__ para matchear DDL real `idx_pcb_*`.
    producto_id  = Column(Integer, ForeignKey('productos.id', ondelete='CASCADE'), nullable=False)
    codigo_barra = Column(String(20), nullable=False)
    es_principal = Column(Boolean, nullable=False, default=False)
    fuente       = Column(String(20), nullable=False, default='manual')  # manual / factura / observer / import / cruce / legacy_alt
    factura_id   = Column(Integer, ForeignKey('facturas.id', ondelete='SET NULL'), nullable=True)
    creado_en    = Column(DateTime, default=now_ar)
    producto = relationship('Producto', back_populates='codigos_barra')
    __table_args__ = (
        Index('idx_pcb_producto', 'producto_id'),
        Index('idx_pcb_codigo', 'codigo_barra'),
        Index('uq_pcb_producto_codigo', 'producto_id', 'codigo_barra', unique=True),
    )


class ProductoAtributo(Base):
    """Atributos estructurados de un producto: droga, concentración, forma, cantidad.

    1-a-1 con Producto. Se puebla mezclando 3 fuentes (en este orden de confianza):
      1. obs_productos (cantidad_envase, nombre_droga, codigo_alfabeta, troquel) → fuente='observer'
      2. Regex sobre descripción (concentracion_mg, forma_farma, via) → fuente='regex'
      3. LLM fallback para descripciones residuales → fuente='llm'

    Si el usuario corrige a mano: fuente='manual' (no se pisa con backfill posterior).

    Sirve para:
      - Match dimensional de ofertas (droga + concentración + cantidad + forma)
      - Filtros / agrupaciones / dashboards transversales (BI por droga, por forma, etc.)
      - Identificación rápida sin EAN
    """
    __tablename__ = 'producto_atributos'
    # Indices custom (matchean DDL `idx_atributos_*`).
    producto_id        = Column(Integer, ForeignKey('productos.id', ondelete='CASCADE'), primary_key=True)
    monodroga_norm     = Column(String(500), nullable=True)              # lower-case sin acentos para match
    concentracion_mg   = Column(DECIMAL(12, 4), nullable=True)           # ej 500, 250.5, 0.05
    concentracion_unidad = Column(String(15), nullable=True)             # MG, MCG, G, UI, %, MG/ML, MG/5ML
    forma_farma        = Column(String(10), nullable=True)               # CPR, CAP, SUSP, SUP, AMP, JER, CRE, POM, GTS, SOL, INH, OVU, PCH, POL
    cantidad_envase    = Column(DECIMAL(10, 3), nullable=True)           # 16, 100, 10
    via_admin          = Column(String(10), nullable=True)               # ORAL, IV, IM, SC, TOP, OFT, NAS, OTI, INH, RECT, VAG
    fuente             = Column(String(15), nullable=False, default='regex')  # observer / regex / llm / manual / mixto
    confianza          = Column(String(8),  nullable=False, default='MEDIA')  # ALTA / MEDIA / BAJA
    raw_descripcion    = Column(String(300), nullable=True)              # snapshot de la descripción cuando se extrajo (detección de drift)
    extraido_en        = Column(DateTime, default=now_ar)
    producto = relationship('Producto', backref='atributos')
    __table_args__ = (
        Index('idx_atributos_droga', 'monodroga_norm'),
        Index('idx_atributos_conc', 'concentracion_mg'),
        Index('idx_atributos_forma', 'forma_farma'),
        Index('idx_atributos_fuente', 'fuente'),
    )

    @property
    def monodroga_display(self):
        """Fuente única: Producto.monodroga. monodroga_display era un dup eliminado."""
        return self.producto.monodroga if self.producto is not None else None

    @monodroga_display.setter
    def monodroga_display(self, value):
        if self.producto is not None:
            self.producto.monodroga = value


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
    # Multi-tenant: a qué farmacia del grupo pertenece este pedido. Default=1
    # (la farmacia original / actual). Cuando se sumen Pieri, etc., los pedidos
    # de cada una llevan su farmacia_id.
    # index=True quitado: idx_pedidos_farmacia (custom) cubre farmacia_id.
    farmacia_id = Column(Integer, ForeignKey('farmacias.id'),
                          nullable=False, default=1)
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
    # index=True quitado: idx_pedidos_partner_id (custom) cubre partner_id.
    partner_id = Column(Integer, nullable=True)
    canal_elegido_en = Column(DateTime, nullable=True)
    creado_en = Column(DateTime, default=now_ar, index=True)
    analizado_en = Column(DateTime, nullable=True)
    estado = Column(String(20), nullable=False, default='PENDIENTE', index=True)
    analisis_json = Column(Text, nullable=True)
    analisis_guardado_en = Column(DateTime, nullable=True)
    # Sigla que identifica desde dónde se disparó el pedido (Inf.Auto, Movil.Lab,
    # Analisis, etc). Sirve para trazar el origen y filtrar en la lista.
    origen = Column(String(20), nullable=True, index=True)
    # Hasta cuándo mostrar este pedido como candidato en "Pedido Reposición"
    # (compras_dia armado). NULL = no inyectar. Si fecha >= hoy, los productos
    # del pedido aparecen como sugerencia en el armado.
    # index=True quitado: idx_pedidos_mostrar_hasta (custom) cubre mostrar_hasta.
    mostrar_hasta = Column(Date, nullable=True)
    items = relationship('PedidoItem', back_populates='pedido', cascade='all, delete-orphan')
    analisis_sesion = relationship('AnalisisSesion')
    __table_args__ = (
        Index('idx_pedidos_farmacia', 'farmacia_id'),
        Index('idx_pedidos_partner_id', 'partner_id'),
        Index('idx_pedidos_mostrar_hasta', 'mostrar_hasta'),
        Index('idx_pedidos_estado_creado', 'estado', 'creado_en'),
    )


class PedidoItem(Base):
    __tablename__ = 'pedido_items'
    id = Column(Integer, primary_key=True)
    pedido_id = Column(Integer, ForeignKey('pedidos.id'), nullable=False)
    # Multi-tenant: denormalizado desde Pedido para evitar joins en queries de
    # agregación cross-farmacia (ej. "qué se pidió este mes en F1+Pieri por
    # producto"). Siempre debe coincidir con pedido.farmacia_id.
    # index=True quitado: idx_pedido_items_farmacia (custom) cubre farmacia_id.
    farmacia_id = Column(Integer, ForeignKey('farmacias.id'),
                          nullable=False, default=1)
    codigo_barra = Column(String(20))
    nombre = Column(String(200))
    cantidad = Column(Integer, nullable=False, default=0)
    precio_pvp = Column(DECIMAL(14, 2))
    subtotal = Column(DECIMAL(14, 2))
    rotacion = Column(String(1), nullable=True)       # A/M/B
    avg_monthly = Column(DECIMAL(10, 2), nullable=True)
    pedido = relationship('Pedido', back_populates='items')
    __table_args__ = (
        Index('idx_pedido_items_farmacia', 'farmacia_id'),
    )


class ProcesoCompra(Base):
    """Ciclo de compra: análisis → pedido → factura → cruce → (reclamo) → cierre."""
    __tablename__ = 'procesos_compra'
    id = Column(Integer, primary_key=True)
    # Multi-tenant: a qué farmacia pertenece este proceso de compra. Default=1.
    # index=True quitado: idx_procesos_compra_farmacia (custom) cubre farmacia_id.
    farmacia_id = Column(Integer, ForeignKey('farmacias.id'),
                          nullable=False, default=1)
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
    __table_args__ = (
        Index('idx_procesos_compra_farmacia', 'farmacia_id'),
    )


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
    rubro = Column(String(150), nullable=True)   # rubro ObServer (filtro stats; default Medicamentos)
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


class CadenciaLabSnapshot(Base):
    """Snapshot del análisis de cadencias por laboratorio (1 fila por lab).

    Materializa `analizar_cadencias_lab` para TODOS los labs de una vez (botón
    Recalcular, ~25s), para que la plataforma de análisis cross-lab lea al
    instante y filtre/ordene client-side. Los params (cobertura, meses_rot)
    están baked en el snapshot: cambiarlos = recalcular."""
    __tablename__ = 'cadencia_lab_snapshot'
    lab_id = Column(Integer, primary_key=True, autoincrement=False)
    lab_nombre = Column(String(150))
    # RFM (recencia × rotación, sobre todos los productos del lab)
    core = Column(Integer, nullable=False, default=0)
    ocasional = Column(Integer, nullable=False, default=0)
    caida = Column(Integer, nullable=False, default=0)
    dormido = Column(Integer, nullable=False, default=0)
    # Buckets de rotación (solo productos con ventas en la ventana)
    alta = Column(Integer, nullable=False, default=0)
    media_alta = Column(Integer, nullable=False, default=0)
    media = Column(Integer, nullable=False, default=0)
    baja = Column(Integer, nullable=False, default=0)
    muy_baja = Column(Integer, nullable=False, default=0)
    # $/mes por cuadrante/bucket (para el toggle Cantidad ⇄ Monto)
    core_monto = Column(DECIMAL(16, 2), nullable=False, default=0)
    ocasional_monto = Column(DECIMAL(16, 2), nullable=False, default=0)
    caida_monto = Column(DECIMAL(16, 2), nullable=False, default=0)
    dormido_monto = Column(DECIMAL(16, 2), nullable=False, default=0)
    alta_monto = Column(DECIMAL(16, 2), nullable=False, default=0)
    media_alta_monto = Column(DECIMAL(16, 2), nullable=False, default=0)
    media_monto = Column(DECIMAL(16, 2), nullable=False, default=0)
    baja_monto = Column(DECIMAL(16, 2), nullable=False, default=0)
    muy_baja_monto = Column(DECIMAL(16, 2), nullable=False, default=0)
    # Negocio
    con_ventas = Column(Integer, nullable=False, default=0)
    sin_ventas = Column(Integer, nullable=False, default=0)
    monto_mensual = Column(DECIMAL(16, 2), nullable=False, default=0)
    dormido_valor = Column(DECIMAL(16, 2), nullable=False, default=0)
    dormido_con_stock = Column(Integer, nullable=False, default=0)
    dormido_stock_u = Column(Integer, nullable=False, default=0)
    # Params usados + timestamp
    cobertura = Column(Integer, nullable=True)
    meses_rot = Column(Integer, nullable=True)
    actualizado_en = Column(DateTime, default=now_ar)


class AnalisisIaCache(Base):
    """Último análisis IA por informe (+ lab) para re-mostrarlo SIN volver a
    llamar a la API. Pensado para demos: cero gasto y cero riesgo de fallo en
    vivo. Se upsertea en cada análisis nuevo (1 fila por clave)."""
    __tablename__ = 'analisis_ia_cache'
    clave = Column(String(80), primary_key=True)   # 'cadencias' | 'lab_gap_marcas:152' | ...
    titulo = Column(String(200))
    texto = Column(Text, nullable=False)
    tokens_in = Column(Integer)
    tokens_out = Column(Integer)
    creado_en = Column(DateTime, default=now_ar, server_default=func.now())


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
    # Presencia en el panel de atención: estado manual + heartbeat.
    estado_presencia = Column(String(12), nullable=False, default='online')  # online|ocupado|ausente
    ultima_actividad = Column(DateTime, nullable=True)

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
    __table_args__ = (
        Index('idx_hcc_user', 'usuario_id', text('clicked_at DESC')),
        Index('idx_hcc_card', 'card_id'),
    )


class PackEquivalencia(Base):
    """Equivalencia global ean_pack → ean_unidad aprendida al resolver packs
    de módulos. Una vez que un pack se resuelve manualmente o por heurística,
    queda persistido acá para que CUALQUIER módulo futuro que lo use lo
    aplique automáticamente sin pedirle al user que vuelva a resolver.

    Por ej: si en el módulo Roemmers Abril aprendiste que
    7795345000459 (pack AMOXIDAL X 8 CIREX X 10) → 7795345000282 (X 8
    individual), en Roemmers Mayo el mismo pack se auto-resuelve.

    UNIQUE(ean_pack): un pack tiene UNA equivalencia. Si surge ambigüedad
    (mismo pack con distintas unidades), el último upsert gana — esto es
    excepcional, los packs no cambian de unidad.
    """
    __tablename__ = 'pack_equivalencias'
    id              = Column(Integer, primary_key=True)
    ean_pack        = Column(String(30), nullable=False, unique=True, index=True)
    ean_unidad      = Column(String(30), nullable=False)
    cantidad        = Column(Integer, nullable=False, default=1)  # cuántas unidades en el pack
    desc_pack       = Column(String(255), nullable=True)
    desc_unidad     = Column(String(255), nullable=True)
    # index=True quitado: idx_pack_equiv_lab (parcial, WHERE laboratorio_id IS NOT
    # NULL) cubre laboratorio_id; index=True generaba ix_* duplicado. Ver lote 3.
    laboratorio_id  = Column(Integer, ForeignKey('laboratorios.id', ondelete='SET NULL'),
                             nullable=True)   # filtro per-lab (manual o derivado)
    aprendido_de    = Column(Integer, ForeignKey('modulos.id', ondelete='SET NULL'),
                             nullable=True)   # módulo donde se aprendió por primera vez
    fuente          = Column(String(20), nullable=False, default='aprendido',
                             server_default='aprendido')  # 'aprendido' | 'manual' | 'excel'
    creado_en       = Column(DateTime, default=now_ar)
    actualizado_en  = Column(DateTime, default=now_ar, onupdate=now_ar)
    laboratorio = relationship('Laboratorio')
    __table_args__ = (
        Index('idx_pack_equiv_lab', 'laboratorio_id',
              postgresql_where=text('laboratorio_id IS NOT NULL')),
    )


class ProductoPrecioHist(Base):
    """Snapshot de precio por producto + proveedor en cada factura importada.
    Append-only: cada fila es un punto histórico.
    """
    __tablename__ = 'producto_precios_hist'
    id = Column(Integer, primary_key=True)
    # Indices custom (matchean DDL `idx_precios_*`).
    codigo_barra     = Column(String(20), nullable=False)
    proveedor_id     = Column(Integer, ForeignKey('proveedores.id', ondelete='CASCADE'), nullable=True)
    proveedor_razon  = Column(String(150), nullable=True)  # fallback si no hay proveedor_id
    fecha            = Column(Date, nullable=False)  # fecha de la factura
    precio_publico   = Column(DECIMAL(14, 2), nullable=True)
    dto_pct          = Column(DECIMAL(6, 2),  nullable=True)
    precio_unitario  = Column(DECIMAL(14, 2), nullable=True)
    importe          = Column(DECIMAL(14, 2), nullable=True)
    factura_id       = Column(Integer, ForeignKey('facturas.id', ondelete='SET NULL'), nullable=True)
    tipo_comprobante = Column(String(5), nullable=True)  # FAC / NCR (para filtrar)
    creado_en        = Column(DateTime, default=now_ar)
    __table_args__ = (
        Index('idx_precios_codigo_barra', 'codigo_barra'),
        Index('idx_precios_proveedor', 'proveedor_id'),
        Index('idx_precios_fecha', 'fecha'),
    )


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


# ──────────────────────────────────────────────────────────────────────────
# Devoluciones de recetas (OS rechaza recetas presentadas — tracking interno)
# ──────────────────────────────────────────────────────────────────────────

class MotivoDevolucion(Base):
    """Catálogo de motivos por los que se devuelve una receta (falta firma,
    diagnóstico ilegible, fuera de vademécum, etc.). ABM desde la app."""
    __tablename__ = 'motivo_devolucion'
    id = Column(Integer, primary_key=True)
    nombre = Column(String(150), nullable=False, unique=True)
    activo = Column(Boolean, nullable=False, default=True)
    creado_en = Column(DateTime, default=now_ar)
    # Restringe qué rol puede usar este motivo. 'rendicion' = motivo de la
    # 1ra etapa (operador de mostrador), 'auditor' = 2da etapa (revisión),
    # 'ambos' = visible para los dos. Default 'ambos' para compat con datos viejos.
    uso_rol = Column(String(20), nullable=False, default='ambos')
    # Si True, al elegir este motivo el check "Rendida" se desmarca y bloquea
    # (la receta no está físicamente disponible para rendir — ej. EXTRAVIADA,
    # "la tiene el cadete"). Configurable desde el ABM de motivos.
    bloquea_rendida = Column(Boolean, nullable=False, default=False)


class RendicionLote(Base):
    """Lote de rendición: agrupador real (con su propia identidad) de las
    devoluciones/checks de recetas que se presentan juntas a una o varias OS.

    Reemplaza el concepto de "nro_presentacion" como string libre en
    DevolucionReceta por una entidad con FK. RendicionLote.nro es el ÚNICO
    source of truth — DevolucionReceta.nro_presentacion sigue existiendo
    como cache desnormalizado pero se sincroniza vía event-listener cada
    vez que se cambia RendicionLote.nro (ver `_sync_nro_presentacion` abajo).
    """
    __tablename__ = 'rendicion_lote'
    id = Column(Integer, primary_key=True)
    nro = Column(String(50), nullable=False, index=True)
    # Vendedor "dueño" de la rendición (UUID de ObServer + nombre cacheado).
    vendedor_observer_id = Column(String(36), nullable=True, index=True)
    vendedor_nombre = Column(String(100), nullable=True)
    # Período declarado por el operador (puede no coincidir con
    # fecha_operacion de cada receta). Útil cuando el lote cierra un quincenal
    # y arrastra unas recetas viejas o nuevas.
    periodo_desde = Column(Date, nullable=True)
    periodo_hasta = Column(Date, nullable=True)
    # Etiqueta libre del operador ("Mayo 1ra quincena", "Bonarea junio", etc.).
    etiqueta = Column(String(200), nullable=True)
    # abierta | cerrada (la cerrada queda inmutable salvo que un admin la reabra).
    estado = Column(String(20), nullable=False, default='abierta', index=True)
    creado_en = Column(DateTime, default=now_ar)
    creado_por = Column(String(100), nullable=True)
    cerrado_en = Column(DateTime, nullable=True)
    cerrado_por = Column(String(100), nullable=True)
    # Estado físico: ¿el lote fue entregado a la OS / canal correspondiente?
    # Lo marca el auditor (o admin) cuando confirma que el batch ya se mandó.
    entregada = Column(Boolean, nullable=False, default=False, index=True)
    entregada_en = Column(DateTime, nullable=True)
    entregada_por = Column(String(100), nullable=True)
    __table_args__ = (
        UniqueConstraint('nro', 'vendedor_observer_id',
                         name='uq_rendicion_lote_nro_vendedor'),
    )


@event.listens_for(RendicionLote.nro, 'set', propagate=True)
def _sync_nro_presentacion(lote, new_value, old_value, initiator):
    """Si cambia RendicionLote.nro, propagar a todas las DevolucionReceta
    asociadas (mantener cache desnormalizado en sync). Skip si es la primera
    asignación (old_value es symbol NO_VALUE) o si no cambia el valor."""
    from sqlalchemy.orm.attributes import NO_VALUE
    if old_value is NO_VALUE or old_value == new_value or lote.id is None:
        return
    from sqlalchemy import inspect
    sess = inspect(lote).session
    if sess is None:
        return
    sess.query(DevolucionReceta).filter(
        DevolucionReceta.rendicion_lote_id == lote.id
    ).update({DevolucionReceta.nro_presentacion: new_value},
             synchronize_session=False)


class VendedorBookmark(Base):
    """Bookmark por vendedor: guarda la última operación procesada en alguna
    rendición. Sirve para auto-rellenar el filtro 'desde' en el form de
    búsqueda y evitar que el operador re-procese recetas que ya pasaron por
    un lote anterior.

    Se actualiza al guardar devoluciones en /rend-recetas/guardar.
    """
    __tablename__ = 'vendedor_bookmark'
    id = Column(Integer, primary_key=True)
    vendedor_observer_id = Column(String(36), nullable=False, unique=True, index=True)
    vendedor_nombre = Column(String(100), nullable=True)
    ultima_op_id = Column(Integer, nullable=True)
    ultima_fecha_op = Column(DateTime, nullable=True)
    ultimo_lote_id = Column(Integer, ForeignKey('rendicion_lote.id'), nullable=True)
    actualizado_en = Column(DateTime, default=now_ar, onupdate=now_ar)


class RendicionGrupo(Base):
    """Grupos para clasificar obras sociales en el flujo de rendición.
    Ej: 'Esencial', 'Receta Solidario', 'Vale Salud'. Cada grupo agrupa N
    obras sociales (ver RendicionGrupoOS). En `/rend-recetas/buscar` el
    operador filtra recetas por grupo para procesarlas de a una tanda.

    `operador_user_id` (nullable) — para más adelante asignar un operador
    responsable. Por ahora no se usa.
    """
    __tablename__ = 'rendicion_grupo'
    id = Column(Integer, primary_key=True)
    nombre = Column(String(80), nullable=False, unique=True)
    descripcion = Column(String(300), nullable=True)
    operador_user_id = Column(Integer, ForeignKey('usuarios.id'), nullable=True)
    activo = Column(Boolean, nullable=False, default=True)
    creado_en = Column(DateTime, default=now_ar)
    operador = relationship('Usuario')
    os_items = relationship('RendicionGrupoOS', backref='grupo',
                             cascade='all, delete-orphan')


class RendicionGrupoOS(Base):
    """OS pertenecientes a un grupo de rendición. UNIQUE(grupo, os)."""
    __tablename__ = 'rendicion_grupo_os'
    id = Column(Integer, primary_key=True)
    grupo_id = Column(Integer, ForeignKey('rendicion_grupo.id', ondelete='CASCADE'),
                      nullable=False, index=True)
    obra_social_observer_id = Column(Integer, nullable=False)
    nombre_cached = Column(String(200), nullable=False, default='')
    creado_en = Column(DateTime, default=now_ar)
    __table_args__ = (
        UniqueConstraint('grupo_id', 'obra_social_observer_id',
                         name='uq_rend_grupo_os'),
    )


class RolFiltroObraSocial(Base):
    """Filtra qué obras sociales puede VER un rol al buscar/listar recetas.
    Si hay registros para un rol, esas OS quedan OCULTAS para los usuarios
    de ese rol (lista negra). Caso típico: rol=rendicion no debe ver PAMI
    ni AMTAE porque las maneja otro circuito.

    En el futuro puede haber overrides por usuario individual (otra tabla),
    pero MVP es solo por rol.
    """
    __tablename__ = 'rol_filtro_obra_social'
    id = Column(Integer, primary_key=True)
    rol = Column(String(20), nullable=False, index=True)
    obra_social_observer_id = Column(Integer, nullable=False)
    # Nombre cacheado para mostrar en ABM sin tener que joinear cada vez.
    nombre_cached = Column(String(200), nullable=False, default='')
    creado_en = Column(DateTime, default=now_ar)
    __table_args__ = (
        UniqueConstraint('rol', 'obra_social_observer_id',
                         name='uq_rol_filtro_os'),
    )


class DevolucionReceta(Base):
    """Registro de una receta devuelta. Apunta a una operación de venta en
    ObServer vía `id_operacion_observer` pero los datos clave se snapshot-ean
    al momento de registrar para que el reporte sobreviva cambios en ObServer."""
    __tablename__ = 'devolucion_receta'
    id = Column(Integer, primary_key=True)
    nro_presentacion = Column(String(50), nullable=True, index=True)
    # FK al lote de rendición (modelo nuevo 2026-05-18). Mantenemos
    # nro_presentacion también para no romper queries viejas; en rendiciones
    # nuevas ambos quedan sincronizados.
    rendicion_lote_id = Column(Integer, ForeignKey('rendicion_lote.id'), nullable=True, index=True)
    # Vendedor (Observer)
    vendedor_observer_id = Column(String(36), nullable=True)   # UUID DW.OperadoresVenta
    vendedor_nombre = Column(String(100), nullable=True)
    # Receta (Observer)
    id_operacion_observer = Column(Integer, nullable=False, index=True)
    fecha_operacion = Column(DateTime, nullable=True)
    obra_social_nombre = Column(String(200), nullable=True)
    importe_total = Column(DECIMAL(12, 2), nullable=True)
    importe_a_cargo_os = Column(DECIMAL(12, 2), nullable=True)
    # Devolución
    motivo_id = Column(Integer, ForeignKey('motivo_devolucion.id'), nullable=True)
    # destino_id legacy borrado 2026-05-18 — DestinoDevolucion eliminado del sistema.
    # Destino = vendedor de ObServer (a quién se devuelve la receta para corregir)
    destino_vendedor_observer_id = Column(String(36), nullable=True)
    destino_vendedor_nombre = Column(String(100), nullable=True)
    observaciones = Column(Text, nullable=True)
    # Etapa 1 — operador rendicion (mostrador). Notas exclusivas del operador
    # que cargó el chequeo inicial, separadas de `observaciones` (que puede
    # quedar para auditor o legacy).
    observaciones_rendicion = Column(Text, nullable=True)
    # Sub-checkboxes para motivo "AGREGAR DATOS": JSON array de strings
    # (afiliado / fecha / diagnostico / concentracion / cant_comprom / monodroga).
    agregar_datos_json = Column(Text, nullable=True)
    creado_en = Column(DateTime, default=now_ar, index=True)
    creado_por = Column(String(100), nullable=True)            # email del user
    # Etapa 2 — auditor (revisa lo que cargó rendicion).
    auditor_motivo_id = Column(Integer, ForeignKey('motivo_devolucion.id'), nullable=True)
    auditor_observaciones = Column(Text, nullable=True)
    auditor_user = Column(String(100), nullable=True)
    auditor_fecha = Column(DateTime, nullable=True)
    # Posesión / etapa del flujo. False = la tiene el vendedor (la está
    # rindiendo). True = el vendedor la marcó "Rendida" → pasa al auditor.
    # El auditor al accionar OK/Resuelta deja en_auditoria=True ("Rendida OK");
    # Devuelta la vuelve a poner en False (regresa al vendedor a recorregir).
    en_auditoria = Column(Boolean, nullable=False, default=False, index=True)
    # Rendida a la obra social: el auditor la presentó/rindió a la OS para
    # cobrar. Solo aplica a recetas en estado 'ok'. Al marcarse True, la receta
    # pasa a histórico (sale de la pantalla "Rendición a Obras Sociales").
    rendida_os = Column(Boolean, nullable=False, default=False, index=True)
    rendida_os_en = Column(DateTime, nullable=True)
    rendida_os_por = Column(String(100), nullable=True)
    # Cierre del ciclo
    estado = Column(String(20), nullable=False, default='pendiente', index=True)
                                                               # pendiente | ok | resuelta | descartada | devuelta
    nota_cierre = Column(Text, nullable=True)
    cerrada_en = Column(DateTime, nullable=True)
    cerrada_por = Column(String(100), nullable=True)

    motivo = relationship('MotivoDevolucion', foreign_keys=[motivo_id])
    auditor_motivo = relationship('MotivoDevolucion', foreign_keys=[auditor_motivo_id])
    # rendicion_lote relationship → ver `rendicion_lote_id` definido arriba.

    __table_args__ = (
        UniqueConstraint('id_operacion_observer', 'creado_en',
                         name='uq_devolucion_op_creado'),
    )


class EstacionalidadEscenario(Base):
    """Escenario de ajuste estacional para una droga.

    Permite al usuario guardar variantes nombradas ("base", "agresivo", etc.)
    con sus 12 indices mensuales + parametros de lead time y cobertura. Uno
    puede ser default y se usa al calcular cantidades sugeridas en pedidos.

    El indice del mes m es un multiplicador sobre el promedio anual de la
    droga: 1.0 = neutro, 2.0 = el doble del promedio en ese mes.

    lead_time_dias y cobertura_dias se almacenan en DIAS (no meses) porque
    la operatoria farmaceutica natural pasa en dias (proveedor tarda 3d,
    cubris 15d, etc). Para el chart mensual se convierten dividiendo por 30.
    """
    __tablename__ = 'estacionalidad_escenarios'
    id = Column(Integer, primary_key=True)
    droga_id = Column(Integer, ForeignKey('obs_nombres_drogas.observer_id'),
                      nullable=False, index=True)
    nombre = Column(String(60), nullable=False, default='base')
    indices_json = Column(Text, nullable=False)  # JSON array de 12 floats
    lead_time_dias = Column(Integer, nullable=False, default=0)
    cobertura_dias = Column(Integer, nullable=False, default=30)
    es_default = Column(Boolean, nullable=False, default=False, index=True)
    creado_por = Column(String(80), nullable=True)
    creado_en = Column(DateTime, default=now_ar)
    actualizado_en = Column(DateTime, default=now_ar, onupdate=now_ar)

    __table_args__ = (
        UniqueConstraint('droga_id', 'nombre', name='uq_estac_droga_nombre'),
    )


class EstacionalidadProducto(Base):
    """Asignación de un escenario estacional a un producto concreto (observer_id).

    Un producto solo puede tener un escenario activo. La asignación es explícita:
    si un producto no tiene registro acá, hereda el default de su droga (si lo hay).
    """
    __tablename__ = 'estacionalidad_productos'
    id                   = Column(Integer, primary_key=True)
    producto_observer_id = Column(Integer,
                                  ForeignKey('obs_productos.observer_id'),
                                  nullable=False, unique=True, index=True)
    droga_id             = Column(Integer,
                                  ForeignKey('obs_nombres_drogas.observer_id'),
                                  nullable=False)
    escenario_id         = Column(Integer,
                                  ForeignKey('estacionalidad_escenarios.id'),
                                  nullable=False)
    aplicado_por         = Column(String(80), nullable=True)
    aplicado_en          = Column(DateTime, default=now_ar, onupdate=now_ar)


CAMPOS_SISTEMA = [
    ('fijo',            'Valor fijo / constante'),
    ('codigo_barra',    'Código de barra (EAN)'),
    ('ean_kellerhoff',  'EAN-Kellerhoff (corregido si difiere)'),
    ('descripcion',     'Descripción del producto'),
    ('cantidad',        'Cantidad total (mod+oferta+sin deal)'),
    ('cant_modulo',     'Cantidad módulo'),
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


def init_engine(database_url=None):
    global engine, SessionLocal
    database_url = database_url or os.environ.get('DATABASE_URL', 'sqlite:///farmacia.db')
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    is_postgres = not database_url.startswith('sqlite')
    connect_args = {'connect_timeout': 10} if is_postgres else {}
    engine = create_engine(database_url, echo=False, future=True,
                           connect_args=connect_args,
                           pool_timeout=15, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                               expire_on_commit=False)
    return database_url


def _alembic_sync(database_url):
    """Pone la DB bajo control de Alembic de forma idempotente, sin romper el boot.

    Patrón bootstrap (corre DESPUÉS de create_all + _pg_add_columns, que durante
    la transición siguen gestionando el schema de forma idempotente):
      - DB ya stampeada (existe alembic_version con revisión) → `upgrade head`
        (aplica migraciones pendientes; no-op si ya está en head).
      - DB sin stampear (instancia pre-Alembic o fresca recién creada por
        create_all) → `stamp head` (el schema ya existe, solo la marcamos).

    - SQLite: se saltea (Alembic apunta a Postgres; dev SQLite sigue con create_all).
    - **Fail-soft**: cualquier excepción se loguea y NO tira abajo el boot — la
      lección del deploy 19-may es que init_db NUNCA debe colgar/abortar el arranque
      (Render mata el instance si no bindea el puerto). Una migración que falla se ve
      en los logs (ERROR) y se corrige; el servicio igual levanta.
    """
    if database_url.startswith('sqlite'):
        return
    import logging as _logging
    log = _logging.getLogger(__name__)
    try:
        import os as _os

        from alembic.config import Config

        from alembic import command
        _here = _os.path.dirname(_os.path.abspath(__file__))
        cfg = Config(_os.path.join(_here, 'alembic.ini'))
        cfg.set_main_option('script_location', _os.path.join(_here, 'alembic'))
        cfg.set_main_option('sqlalchemy.url', database_url)
        # env.py resuelve ALEMBIC_DATABASE_URL > DATABASE_URL > config. Forzamos
        # ALEMBIC_DATABASE_URL = la url que init_db está usando, para que el
        # stamp/upgrade vaya SIEMPRE a ESTA DB (no a la del env DATABASE_URL si
        # difieren, ej. staging o tests). Restauramos el valor previo después.
        _prev = _os.environ.get('ALEMBIC_DATABASE_URL')
        _os.environ['ALEMBIC_DATABASE_URL'] = database_url
        try:
            with engine.connect() as conn:
                has_version = conn.execute(text(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name='alembic_version')"
                )).scalar()
                current = conn.execute(text(
                    'SELECT version_num FROM alembic_version LIMIT 1'
                )).scalar() if has_version else None
            if current:
                # Guard de base compartida: Alembic usa UNA sola tabla
                # alembic_version por base. Si la revisión actual NO es de ESTE
                # repo (no está entre nuestras migraciones), la base está bajo el
                # control de OTRA app que comparte la base — ej. Badia comparte
                # `farmacia_yhvp` con la app magistral (alembic_version=d3h8a5e2f6c1).
                # En ese caso NO tocamos Alembic: este repo sigue gestionando su
                # schema con create_all + _pg_add_columns. No es error → skip limpio.
                from alembic.script import ScriptDirectory
                _known = {r.revision for r in ScriptDirectory.from_config(cfg).walk_revisions()}
                if current not in _known:
                    log.warning(
                        'Alembic: revisión actual %s es AJENA a este repo (base '
                        'compartida con otra app) → no toco Alembic acá. Schema lo '
                        'gestiona create_all + _pg_add_columns.', current)
                else:
                    log.info('Alembic: upgrade head (revisión actual=%s)', current)
                    command.upgrade(cfg, 'head')
            else:
                log.info('Alembic: DB sin stampear → stamp head')
                command.stamp(cfg, 'head')
        finally:
            if _prev is None:
                _os.environ.pop('ALEMBIC_DATABASE_URL', None)
            else:
                _os.environ['ALEMBIC_DATABASE_URL'] = _prev
    except Exception as e:
        log.error('Alembic sync FALLÓ (no-fatal, sigo el boot): %s', e, exc_info=True)


class BotConversacion(Base):
    """Conversación del asistente con un cliente, por canal + usuario del canal.
    Soporta multi-línea (`linea`) y handoff bot↔operador (`estado_atencion`).
    El estado del flujo (`nodo`/`esperando`) se persiste acá (antes en memoria)."""
    __tablename__ = 'bot_conversaciones'
    id = Column(Integer, primary_key=True)
    canal = Column(String(20), nullable=False, default='telegram')    # telegram | whatsapp
    linea = Column(String(40))                  # número/línea de entrada (multi-línea)
    canal_user_id = Column(String(80), nullable=False, index=True)    # chat_id / wa_id del cliente
    nombre_cliente = Column(String(120))
    # bot = lo atiende el bot · cola = derivada, esperando operador · humano = la tomó un operador
    estado_atencion = Column(String(20), nullable=False, default='bot', index=True)
    operador_user_id = Column(Integer, ForeignKey('usuarios.id'), nullable=True)
    # Vinculación con la ficha del cliente (ObServer). Se autocompleta por teléfono
    # en WhatsApp; en Telegram queda NULL hasta que el operador la vincule a mano.
    cliente_id = Column(Integer, ForeignKey('clientes.id', ondelete='SET NULL'),
                        nullable=True, index=True)   # tabla única de clientes
    cliente_observer_id = Column(Integer, ForeignKey('obs_clientes.observer_id'),
                                 nullable=True, index=True)   # legacy (2a)
    # Alternativa: cliente capturado localmente (lead) que aún no está en ObServer.
    cliente_local_id = Column(Integer, ForeignKey('clientes_locales.id'),
                              nullable=True, index=True)   # legacy (2a)
    cliente = relationship('Cliente', foreign_keys=[cliente_id])
    nodo = Column(String(50), default='inicio')      # estado del flujo conversacional
    esperando = Column(String(50))                   # acción esperando input del usuario
    # Lo que el cliente "iba a encargar" cuando arranca el flujo de identificación
    # (DNI / nombre). Se guarda acá porque los pasos de id pueden ser 1-3 turnos
    # y `esperando` (50 chars) no alcanza para nombres largos de productos.
    producto_pendiente = Column(Text, nullable=True)
    tiene_encargo = Column(Boolean, default=False, nullable=False,
                           server_default='false')   # hay un pedido concreto esperando
    creado_en = Column(DateTime, default=now_ar)
    ultimo_en = Column(DateTime, default=now_ar, index=True)


class BotMensaje(Base):
    """Un mensaje dentro de una conversación (historial para el panel)."""
    __tablename__ = 'bot_mensajes'
    id = Column(Integer, primary_key=True)
    conversacion_id = Column(Integer,
                             ForeignKey('bot_conversaciones.id', ondelete='CASCADE'),
                             nullable=False, index=True)
    origen = Column(String(12), nullable=False)      # cliente | bot | operador
    texto = Column(Text)
    tiene_imagen = Column(Boolean, nullable=False, default=False)
    creado_en = Column(DateTime, default=now_ar, index=True)


class OfertaBot(Base):
    """Ofertas cargadas manualmente para el bot (descuento % o 2x1)."""
    __tablename__ = 'ofertas_bot'
    id = Column(Integer, primary_key=True)
    observer_id = Column(Integer, nullable=False, index=True)
    descripcion = Column(String(200), nullable=False)
    tipo = Column(String(20), nullable=False)   # 'descuento_pct' | '2x1'
    valor = Column(DECIMAL(6, 2), nullable=True)  # % si descuento, null si 2x1
    activo = Column(Boolean, default=True, nullable=False)
    creado_en = Column(DateTime, default=now_ar)


class OfertaRegistro(Base):
    """Registro de cuándo un operador ofreció una oferta al cliente."""
    __tablename__ = 'ofertas_registro'
    id = Column(Integer, primary_key=True)
    conversacion_id = Column(Integer, ForeignKey('bot_conversaciones.id', ondelete='CASCADE'),
                             nullable=False, index=True)
    oferta_bot_id = Column(Integer, ForeignKey('ofertas_bot.id', ondelete='CASCADE'),
                           nullable=False, index=True)
    mensaje_enviado = Column(Boolean, default=True, nullable=False)
    enviado_por = Column(Integer, ForeignKey('usuarios.id'), nullable=True)
    enviado_en = Column(DateTime, default=now_ar)


class RespuestaRapida(Base):
    """Botones de respuesta rápida configurables para el panel de atención."""
    __tablename__ = 'respuestas_rapidas'
    id = Column(Integer, primary_key=True)
    emoji = Column(String(8), nullable=True)
    etiqueta = Column(String(40), nullable=False)
    texto = Column(Text, nullable=False)
    orden = Column(Integer, default=0)
    activa = Column(Boolean, default=True, nullable=False)


class InformeEnviado(Base):
    """Registro anti-spam de informes proactivos enviados por Telegram.
    Evita renotificar la misma conversación hasta que avance (operador responde
    o conv se cierra)."""
    __tablename__ = 'informe_enviado'
    id               = Column(Integer, primary_key=True)
    tipo             = Column(String(40), nullable=False)
    conversacion_id  = Column(Integer, nullable=False, index=True)
    enviado_en       = Column(DateTime, default=now_ar)
    __table_args__ = (
        UniqueConstraint('tipo', 'conversacion_id', name='uq_informe_conv'),
    )


class BotInteraccion(Base):
    """Analítica: una fila por mensaje del cliente que procesa el bot, clasificada
    por camino/intent y con el motivo si NO se pudo resolver. Alimenta el panel de
    'memoria de no-resueltos' (demanda perdida, agujeros del flujo).

    `texto`/`canal`/`linea` se copian (no solo FK) para que la métrica sobreviva a
    una purga del chat y se filtre/agregue sin JOIN."""
    __tablename__ = 'bot_interacciones'
    id = Column(Integer, primary_key=True)
    conversacion_id = Column(Integer,
                             ForeignKey('bot_conversaciones.id', ondelete='CASCADE'),
                             nullable=False, index=True)
    canal = Column(String(20))
    linea = Column(String(40), index=True)
    texto = Column(Text)
    # precio | encargo | consulta_ia | receta | horarios | derivar | menu | otro
    camino = Column(String(30), index=True)
    resuelto = Column(Boolean, nullable=False, default=True, index=True)
    # sin_stock | no_entendido | derivado | receta_ilegible | falta_info | rechazado_malicioso
    motivo = Column(String(30), index=True)
    tema = Column(String(80), index=True)            # reservado para tanda 2 (IA)
    producto = Column(String(160))                   # texto buscado cuando sin_stock
    creado_en = Column(DateTime, default=now_ar, index=True)


def init_db(database_url=None):
    database_url = init_engine(database_url)
    if not database_url.startswith('sqlite'):
        # Limpia zombies en pg_type / pg_class que bloquean CREATE TABLE con
        # "duplicate key ... pg_type_typname_nsp_index". Puede pasar en Render
        # cuando un deploy previo dejó un pg_type huérfano sin tabla real.
        # Usamos AUTOCOMMIT para que cada DDL se confirme aunque el siguiente falle.
        zombie_names = ('export_templates', 'ofertas_minimo', 'procesos_compra',
                        'analisis_sesiones', 'usuarios',
                        'plantillas_exportacion', 'plantilla_campos',
                        'plantillas', 'producto_precios_hist', 'producto_atributos',
                        'producto_codigos_barra',
                        'obs_laboratorios', 'obs_rubros', 'obs_subrubros',
                        'obs_nombres_drogas', 'obs_productos', 'obs_stock',
                        'obs_sync_log', 'obs_ventas_mensuales',
                        'home_card_clicks',
                        'obs_grupos_clientes', 'obs_categorias_clientes',
                        'obs_obras_sociales', 'obs_convenios', 'obs_planes',
                        'obs_clientes', 'clientes',
                        'cron_log', 'mv_refresh_log', 'backup_log',
                        'obs_colegios_medicos', 'obs_medicos',
                        'obs_medicos_matriculas', 'obs_ventas_detalle',
                        'descuentos_base', 'obs_codigos_barras',
                        'proveedor_horarios_reparto', 'pedido_borrador',
                        'laboratorio_drogueria',
                        'pedido_emitido', 'pedido_emitido_item',
                        'equivalencias_proveedor',
                        'pack_equivalencias', 'cliente_os_inferida', 'cliente_os_confirmada',
                        'panel_comandos', 'farmacias', 'usuario_farmacias',
                        'alarmas_notificadas', 'sync_lock',
                        'productos_pendientes_revision',
                        'motivo_devolucion',
                        'devolucion_receta',
                        'rendicion_lote',
                        'vendedor_bookmark',
                        'rol_filtro_obra_social',
                        'rendicion_grupo', 'rendicion_grupo_os',
                        'proveedor_cronograma',
                        'tipo_pedido_config',
                        'producto_flags',
                        'kellerhoff_catalogo',
                        'kellerhoff_equivalencia',
                        'estacionalidad_escenarios',
                        'estacionalidad_productos',
                        'cadencia_lab_snapshot',
                        'archivos_compartidos', 'sucursales',
                        'compartido_importado', 'obs_operadores',
                        'parser_ofertas_lab', 'factura_faltante',
                        'analisis_ia_cache', 'panel_heartbeat',
                        'bot_conversaciones', 'bot_mensajes', 'bot_interacciones',
                        'clientes_locales',
                        'ciudades', 'tickets_caja', 'ticket_items', 'formas_pago',
                        'envio_tramos', 'envio_zonas', 'envio_config',
                        'domicilios_cliente', 'rutas_reparto', 'pedidos_reparto',
                        'cadetes', 'ofertas_bot', 'ofertas_registro',
                        'respuestas_rapidas', 'informe_enviado')
        with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            for tname in zombie_names:
                # Caso A: hay tabla real en public → no tocar.
                real_table = conn.execute(text("""
                    SELECT 1 FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relname = :t AND c.relkind = 'r'
                """), {'t': tname}).first()
                if real_table:
                    # Adicional: chequear si hay pg_type con ese nombre PERO en
                    # un namespace distinto al de la tabla, o sin tabla matching.
                    # Eso es zombie aunque la tabla "principal" exista — bloquea
                    # CREATE TABLE de SQLAlchemy con UniqueViolation.
                    huerfanos = conn.execute(text("""
                        SELECT t.typnamespace
                        FROM pg_type t
                        LEFT JOIN pg_class c
                            ON c.relname = t.typname
                           AND c.relnamespace = t.typnamespace
                           AND c.relkind = 'r'
                        WHERE t.typname = :t AND c.oid IS NULL
                    """), {'t': tname}).fetchall()
                    for (ns,) in huerfanos:
                        try:
                            # Resolver el schema del namespace y dropear con FQN.
                            schema = conn.execute(text(
                                'SELECT nspname FROM pg_namespace WHERE oid = :o'
                            ), {'o': ns}).scalar()
                            if schema:
                                conn.execute(text(
                                    f'DROP TYPE IF EXISTS "{schema}"."{tname}" CASCADE'
                                ))
                        except Exception:
                            pass
                    continue
                # Caso B: no hay tabla real pero puede quedar pg_type / secuencia
                # / vista huérfana en CUALQUIER schema.
                for ddl in (f'DROP TABLE IF EXISTS "{tname}" CASCADE',
                            f'DROP TYPE IF EXISTS "{tname}" CASCADE',
                            f'DROP SEQUENCE IF EXISTS "{tname}_id_seq" CASCADE'):
                    try:
                        conn.execute(text(ddl))
                    except Exception:
                        # Idempotente: si el tipo/sequence ya estaba limpio,
                        # IF EXISTS de Postgres no debería tirar pero por las
                        # dudas absorbemos. Es parte del workaround zombie pg_type.
                        pass
            # Crear tablas críticas explícitamente acá (AUTOCOMMIT) para que
            # NO dependan de la transacción de _pg_add_columns. Si una migración
            # más arriba en _pg_add_columns abortaba la transacción, las CREATE
            # TABLE inline de tablas nuevas como panel_comandos nunca corrían y
            # los endpoints que las usaban tiraban "relation does not exist".
            try:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS panel_comandos (
                        id SERIAL PRIMARY KEY,
                        comando VARCHAR(40) NOT NULL,
                        estado VARCHAR(20) NOT NULL DEFAULT 'pendiente',
                        solicitado_en TIMESTAMP NOT NULL DEFAULT NOW(),
                        solicitado_por VARCHAR(80),
                        tomado_en TIMESTAMP,
                        ejecutado_en TIMESTAMP,
                        duracion_ms INTEGER,
                        resultado TEXT,
                        origen VARCHAR(40)
                    )
                """))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_panel_comandos_estado "
                    "ON panel_comandos(estado, solicitado_en)"
                ))
                # Latido del DockerPanel (singleton id=1): se estampa en cada poll
                # del buzón. Misma razón que panel_comandos para crearla acá.
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS panel_heartbeat (
                        id INTEGER PRIMARY KEY,
                        ultimo_visto TIMESTAMP,
                        origen VARCHAR(40)
                    )
                """))
                # Estado de notificaciones de alarmas (dedup Telegram).
                # Mismo motivo que panel_comandos: tabla crítica usada por
                # endpoints + cron, no debe depender de _pg_add_columns.
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS alarmas_notificadas (
                        nombre VARCHAR(120) PRIMARY KEY,
                        ultima_notif TIMESTAMP,
                        ultima_severidad VARCHAR(20),
                        count_total INTEGER NOT NULL DEFAULT 0,
                        estado_actual VARCHAR(20)
                    )
                """))
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS proveedor_horarios_reparto (
                        id SERIAL PRIMARY KEY,
                        proveedor_id INTEGER NOT NULL REFERENCES proveedores(id) ON DELETE CASCADE,
                        dia_semana INTEGER NOT NULL,
                        hora VARCHAR(5) NOT NULL,
                        activo BOOLEAN NOT NULL DEFAULT TRUE,
                        creado_en TIMESTAMP DEFAULT NOW(),
                        UNIQUE (proveedor_id, dia_semana, hora)
                    )
                """))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_horarios_prov "
                    "ON proveedor_horarios_reparto (proveedor_id)"
                ))
                # Ítems en falta momentánea de facturas importadas (cruce con pedidos).
                # Tabla nueva crítica del import IA: no debe depender de _pg_add_columns.
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS factura_faltante (
                        id SERIAL PRIMARY KEY,
                        factura_id INTEGER NOT NULL REFERENCES facturas(id) ON DELETE CASCADE,
                        codigo_barra VARCHAR(20),
                        codigo_interno VARCHAR(30),
                        cantidad INTEGER,
                        descripcion VARCHAR(150),
                        creado_en TIMESTAMP DEFAULT NOW()
                    )
                """))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_factura_faltante_fac "
                    "ON factura_faltante (factura_id)"
                ))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_factura_faltante_cb "
                    "ON factura_faltante (codigo_barra)"
                ))
                # PREFAC (prefactura) no entra en VARCHAR(5): ensanchar a 10.
                conn.execute(text(
                    "ALTER TABLE facturas ALTER COLUMN tipo_comprobante TYPE VARCHAR(10)"
                ))
                # Caché del último análisis IA por informe (re-mostrar sin gastar API).
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS analisis_ia_cache (
                        clave VARCHAR(80) PRIMARY KEY,
                        titulo VARCHAR(200),
                        texto TEXT NOT NULL,
                        tokens_in INTEGER,
                        tokens_out INTEGER,
                        creado_en TIMESTAMP DEFAULT NOW()
                    )
                """))
                # Queue de items de import sin match en catálogo.
                # Mismo motivo que panel_comandos: tabla nueva crítica usada
                # por imports y por una pantalla de revisión, no debe depender
                # del path de _pg_add_columns (que solo agrega columns a tablas
                # existentes, no crea tablas).
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS productos_pendientes_revision (
                        id SERIAL PRIMARY KEY,
                        descripcion_supplier VARCHAR(300) NOT NULL,
                        supplier_id INTEGER,
                        supplier_nombre VARCHAR(200),
                        archivo_origen VARCHAR(60),
                        fecha_creacion TIMESTAMP NOT NULL DEFAULT NOW(),
                        veces_aparecido INTEGER NOT NULL DEFAULT 1,
                        score_top_candidato DOUBLE PRECISION,
                        top_candidatos_json TEXT,
                        oferta_data_json TEXT,
                        estado VARCHAR(20) NOT NULL DEFAULT 'pendiente',
                        producto_creado_id INTEGER,
                        producto_vinculado_id INTEGER,
                        usuario_resuelve VARCHAR(80),
                        fecha_resolucion TIMESTAMP
                    )
                """))
                # Migración para tablas existentes (creadas antes del campo)
                conn.execute(text(
                    "ALTER TABLE productos_pendientes_revision "
                    "ADD COLUMN IF NOT EXISTS oferta_data_json TEXT"
                ))
                # Análisis IA (Claude Haiku 4.5) — agregadas 2026-05-11
                for col_def in (
                    "llm_analizado_en TIMESTAMP",
                    "llm_pick_producto_id INTEGER",
                    "llm_pick_observer_id INTEGER",
                    "llm_confidence DOUBLE PRECISION",
                    "llm_reasoning TEXT",
                    "llm_action VARCHAR(20)",
                    "llm_modelo_usado VARCHAR(60)",
                ):
                    col_name = col_def.split()[0]
                    conn.execute(text(
                        f"ALTER TABLE productos_pendientes_revision "
                        f"ADD COLUMN IF NOT EXISTS {col_def}"
                    ))
                    _ = col_name  # silenciar lint
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_pend_rev_llm "
                    "ON productos_pendientes_revision(llm_analizado_en) "
                    "WHERE llm_analizado_en IS NULL"
                ))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_pend_rev_estado "
                    "ON productos_pendientes_revision(estado, fecha_creacion)"
                ))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_pend_rev_supplier "
                    "ON productos_pendientes_revision(supplier_id)"
                ))
                # Lock singleton para coordinar el sync ObServer entre workers.
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS sync_lock (
                        id INTEGER PRIMARY KEY,
                        en_curso BOOLEAN NOT NULL DEFAULT FALSE,
                        iniciado_en TIMESTAMP,
                        finalizado_en TIMESTAMP,
                        paso_actual VARCHAR(80),
                        ultimo_resultado TEXT
                    )
                """))
                # Compartido peer-to-peer: columna destinatarios + log local de importados.
                conn.execute(text(
                    "ALTER TABLE archivos_compartidos "
                    "ADD COLUMN IF NOT EXISTS destinatarios VARCHAR(200) NOT NULL DEFAULT 'todos'"
                ))
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS compartido_importado (
                        id SERIAL PRIMARY KEY,
                        origen_slug VARCHAR(50) NOT NULL,
                        archivo_id INTEGER NOT NULL,
                        tipo VARCHAR(50),
                        nombre VARCHAR(200),
                        accion VARCHAR(20) NOT NULL DEFAULT 'importado',
                        usuario VARCHAR(80),
                        creado_en TIMESTAMP NOT NULL DEFAULT NOW(),
                        CONSTRAINT uq_compartido_importado UNIQUE (origen_slug, archivo_id)
                    )
                """))
                # Estadísticas por vendedor: operador en ventas_detalle + tabla de operadores.
                conn.execute(text(
                    "ALTER TABLE obs_ventas_detalle "
                    "ADD COLUMN IF NOT EXISTS operador_observer VARCHAR(40)"
                ))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_obs_vd_operador "
                    "ON obs_ventas_detalle(operador_observer)"
                ))
                # Prioridad de reparto (DBs existentes; fresh la crea create_all).
                try:
                    conn.execute(text(
                        "ALTER TABLE pedidos_reparto "
                        "ADD COLUMN IF NOT EXISTS prioridad VARCHAR(12) DEFAULT 'normal'"
                    ))
                except Exception:  # noqa: BLE001 (tabla aún no existe en DB nueva)
                    pass
                # Zona (polígono) por ruta de reparto.
                try:
                    conn.execute(text(
                        "ALTER TABLE rutas_reparto ADD COLUMN IF NOT EXISTS poligono TEXT"))
                except Exception:  # noqa: BLE001
                    pass
                # Zona de envío: polígono GeoJSON (reemplaza círculo lat/lng/radio_km).
                try:
                    conn.execute(text(
                        "ALTER TABLE envio_zonas ADD COLUMN IF NOT EXISTS poligono TEXT"))
                except Exception:  # noqa: BLE001
                    pass
                # Cadete (FK) por ruta — tabla de cadetes.
                try:
                    conn.execute(text(
                        "ALTER TABLE rutas_reparto ADD COLUMN IF NOT EXISTS cadete_id INTEGER"))
                except Exception:  # noqa: BLE001
                    pass
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS obs_operadores (
                        observer_id VARCHAR(40) PRIMARY KEY,
                        nombre VARCHAR(120),
                        sync_en TIMESTAMP
                    )
                """))
                # Parser de import de ofertas aprendido por laboratorio.
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS parser_ofertas_lab (
                        laboratorio_id INTEGER PRIMARY KEY,
                        column_mapping TEXT NOT NULL DEFAULT '{}',
                        formato VARCHAR(30) NOT NULL DEFAULT 'plano',
                        header_row INTEGER,
                        creado_por VARCHAR(80),
                        actualizado_en TIMESTAMP
                    )
                """))
                # Migración estacionalidad_escenarios: meses → días.
                # lead_time_meses (INT) → lead_time_dias (INT) — solo rename.
                # cobertura_meses (DECIMAL meses) → cobertura_dias (INT días):
                # multiplicar valor existente por 30 y cambiar tipo.
                try:
                    col_names = {
                        r.column_name for r in conn.execute(text("""
                            SELECT column_name FROM information_schema.columns
                            WHERE table_name = 'estacionalidad_escenarios'
                        """)).fetchall()
                    }
                    if 'lead_time_meses' in col_names and 'lead_time_dias' not in col_names:
                        conn.execute(text(
                            "ALTER TABLE estacionalidad_escenarios "
                            "RENAME COLUMN lead_time_meses TO lead_time_dias"
                        ))
                    if 'cobertura_meses' in col_names and 'cobertura_dias' not in col_names:
                        conn.execute(text(
                            "ALTER TABLE estacionalidad_escenarios "
                            "ALTER COLUMN cobertura_meses TYPE INTEGER "
                            "USING ROUND(cobertura_meses * 30)::INTEGER"
                        ))
                        conn.execute(text(
                            "ALTER TABLE estacionalidad_escenarios "
                            "ALTER COLUMN cobertura_meses SET DEFAULT 30"
                        ))
                        conn.execute(text(
                            "ALTER TABLE estacionalidad_escenarios "
                            "RENAME COLUMN cobertura_meses TO cobertura_dias"
                        ))
                except Exception as _e_estac:
                    print(f'Migración estacionalidad_escenarios meses→días: {_e_estac}')

                # Cleanup: si un deploy previo agrego producto_id a
                # estacionalidad_escenarios (idea descartada — ahora la
                # asignacion va por la tabla estacionalidad_productos),
                # revertir para volver al schema original.
                try:
                    conn.execute(text(
                        "ALTER TABLE estacionalidad_escenarios "
                        "DROP CONSTRAINT IF EXISTS uq_estac_droga_producto_nombre"
                    ))
                    conn.execute(text(
                        "DROP INDEX IF EXISTS idx_estac_producto_id"
                    ))
                    conn.execute(text(
                        "ALTER TABLE estacionalidad_escenarios "
                        "DROP COLUMN IF EXISTS producto_id"
                    ))
                    # Asegurar el UNIQUE original.
                    try:
                        conn.execute(text(
                            "ALTER TABLE estacionalidad_escenarios "
                            "ADD CONSTRAINT uq_estac_droga_nombre "
                            "UNIQUE (droga_id, nombre)"
                        ))
                    except Exception:
                        pass  # Ya existe.
                except Exception as _e_estac2:
                    print(f'Cleanup estacionalidad_escenarios producto_id: {_e_estac2}')
            except Exception:
                pass
    # pg_trgm DEBE existir ANTES de create_all: el modelo declara el índice GIN
    # idx_obs_productos_descripcion_trgm (gin_trgm_ops), y create_all lo intenta
    # crear → sin la extensión falla en una DB fresca con "operator class
    # gin_trgm_ops does not exist". Idempotente. Si no hay permisos para CREATE
    # EXTENSION, se loguea y se sigue (create_all fallará ruidoso en ese caso, que
    # es correcto: el schema no se puede construir como está declarado).
    if not database_url.startswith('sqlite'):
        try:
            with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as _ext_conn:
                _ext_conn.execute(text('CREATE EXTENSION IF NOT EXISTS pg_trgm'))
        except Exception as _ext_e:
            import logging as _lg_ext
            _lg_ext.getLogger(__name__).warning(
                'init_db: no pude crear extensión pg_trgm antes de create_all: %s', _ext_e)

    # create_all puede fallar con dos índices distintos cuando hay objetos
    # huérfanos de un deploy previo:
    #   - pg_type_typname_nsp_index   → pg_type zombie (composite type huérfano)
    #   - pg_class_relname_nsp_index  → pg_class zombie (sequence/index/table huérfano)
    # En ambos casos parseamos el nombre del culpable, lo dropeamos y reintentamos.
    # Retry loop: si hay 2+ zombies distintos en el mismo deploy fallido
    # (ej. tabla foo Y sequence bar_id_seq), un solo intento no alcanza.
    # Iteramos hasta 5 veces, dropeando un zombie por iteración.
    import re as _re
    _MAX_RETRIES = 5
    for _intento in range(_MAX_RETRIES + 1):
        try:
            Base.metadata.create_all(engine)
            break  # éxito
        except Exception as exc:
            if database_url.startswith('sqlite'):
                raise
            if _intento >= _MAX_RETRIES:
                raise  # demasiados zombies, algo más grave pasa
            msg = str(exc)
            zombie = None
            # pg_type huérfano → DETAIL: Key (typname, typnamespace)=(NAME, ...)
            if 'pg_type_typname_nsp_index' in msg:
                m = _re.search(r'\(typname, typnamespace\)=\(([^,]+),', msg)
                if m:
                    zombie = m.group(1)
            # pg_class huérfano (sequence, index, vista) → Key (relname, relnamespace)=(NAME, ...)
            elif 'pg_class_relname_nsp_index' in msg:
                m = _re.search(r'\(relname, relnamespace\)=\(([^,]+),', msg)
                if m:
                    zombie = m.group(1)
                    # Si el nombre termina en _id_seq, también dropeamos la tabla padre.
                    if zombie.endswith('_id_seq'):
                        parent = zombie[:-len('_id_seq')]
                        zombie = parent  # los DROP de abajo cubren tabla + sequence
            if not zombie:
                raise  # error desconocido, no lo silenciamos
            import logging as _logging
            _logging.getLogger(__name__).warning(
                'init_db retry %s/%s: dropeando zombie "%s" (%s)',
                _intento + 1, _MAX_RETRIES, zombie, msg.split('\n')[0][:200],
            )
            with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
                # GUARD: si "zombie" es en realidad una tabla con datos, NO la
                # dropeamos — preferimos que el deploy falle ruidosamente a
                # destruir data en silencio. Histórico: el handler dropeó
                # `configuracion` por un conflicto de pg_type y el CASCADE
                # arrastró tablas grandes (productos/obs_*/etc.). El bug real
                # estaba en otro lado, no acá. Ver mejoras_pendientes.md.
                try:
                    n_rows = conn.execute(text(
                        f'SELECT COUNT(*) FROM "{zombie}"'
                    )).scalar() or 0
                except Exception:
                    n_rows = 0  # tabla no existe → es un pg_type huérfano real
                if n_rows > 0:
                    raise RuntimeError(
                        f'init_db: "{zombie}" tiene {n_rows} filas — me niego a '
                        f'dropearla. El conflicto pg_type debe resolverse a mano. '
                        f'Error original: {msg.split(chr(10))[0][:200]}'
                    )
                # RESTRICT (default de PG) en vez de CASCADE: si "zombie" tiene
                # objetos dependientes (FK desde tablas hijas con data, columnas
                # usando el type, etc.) → el DROP falla y aborta init_db.
                # CASCADE silenciosamente arrastraba esas dependencias y borraba
                # data. Fallar ruidoso > destruir en silencio.
                for ddl in (f'DROP TABLE IF EXISTS "{zombie}" RESTRICT',
                            f'DROP TYPE  IF EXISTS "{zombie}" RESTRICT',
                            f'DROP SEQUENCE IF EXISTS "{zombie}_id_seq" RESTRICT',
                            f'DROP SEQUENCE IF EXISTS "{zombie}" RESTRICT'):
                    try:
                        conn.execute(text(ddl))
                    except Exception as drop_err:
                        # Loguear silenciosos para detectar bloqueos reales
                        _logging.getLogger(__name__).debug(
                            'DROP zombie %s ignored: %s', ddl[:60], drop_err,
                        )
    is_sqlite = database_url.startswith('sqlite')
    # Migraciones incrementales: agrega columnas nuevas si no existen.
    # PostgreSQL usa AUTOCOMMIT para que un try-except silenciado no deje la
    # conexión en estado abortado (InFailedSqlTransaction). Cada DDL es su
    # propia transacción implícita — idempotente por IF NOT EXISTS.
    if is_sqlite:
        with engine.connect() as conn:
            _sqlite_add_columns(conn)
            conn.commit()
    else:
        with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            _pg_add_columns(conn)
            _crear_matviews(conn)

    # Alembic: el schema ya está construido arriba (create_all + _pg_add_columns,
    # idempotentes). Acá ponemos la DB bajo control de Alembic (stamp en la 1ra,
    # upgrade head en las siguientes). Migraciones futuras entran por Alembic;
    # los _pg_add_columns inline se irán migrando gradualmente (conviven, son IF
    # NOT EXISTS). Fail-soft: no rompe el boot. Ver docs/alembic_baseline_review.md.
    # Gateado por env var ALEMBIC_AUTO_SYNC (default 0 = off). En pieristei
    # arranca prendido; acá local sigue con init_db inline hasta que decidamos
    # normalizar y meter Alembic. Evita el ruido de "FALLÓ" y la tabla
    # alembic_version creada sin querer.
    import os as _os
    if _os.environ.get('ALEMBIC_AUTO_SYNC', '0').lower() in ('1', 'true', 'yes'):
        _alembic_sync(database_url)

    # One-shot: importar plantillas legacy a la tabla plantillas nueva
    _migrate_legacy_plantillas()

    # One-shot idempotente: bootstrap de la farmacia 1 (Multi-tenant Fase 1).
    # Si la tabla `farmacias` quedó vacía después de crearla, crear la fila
    # con id=1 representando la farmacia actual. Cualquier dato pre-existente
    # se asocia implícitamente a esta farmacia 1.
    _bootstrap_farmacia_inicial()

    # Backfills opcionales — solo corren si RUN_BACKFILLS=1 está seteado.
    # En deploys normales no se tocan; correr manualmente con:
    #   RUN_BACKFILLS=1 python -c "from database import init_db; init_db()"
    # o via scripts/run_backfills.py
    if not is_sqlite and os.environ.get('RUN_BACKFILLS') == '1':
        _ejecutar_backfills_async()


def _bootstrap_farmacia_inicial():
    """Si la tabla `farmacias` está vacía, crear la primera fila representando
    la farmacia actual (id=1). Idempotente: solo inserta si está vacía.

    Esta es la Fase 1 de la migración a multi-tenant. La farmacia 1 hereda
    todos los datos pre-existentes implícitamente (no necesita backfill de
    farmacia_id porque por ahora ninguna tabla transaccional lo tiene).
    """
    import os as _os
    s = SessionLocal()
    try:
        existe = s.query(Farmacia).first()
        if existe is None:
            obs_id = int(_os.environ.get('OBSERVER_ID_FARMACIA', '10525'))
            s.add(Farmacia(
                id=1,
                nombre='Farmacia 1',
                id_farmacia_observer=obs_id,
                es_demo=False,
                activa=True,
            ))
            s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()


def _ejecutar_backfills_async():
    """Backfills idempotentes que se ejecutan post-boot.

    Cada uno chequea si la tabla está vacía antes de insertar — si ya hubo un
    deploy anterior que los corrió, son no-op. Cualquier excepción queda
    contenida; no afecta al worker que está sirviendo HTTP.
    """
    if engine is None:
        return
    try:
        with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as bf_conn:
            # producto_codigos_barra ← productos.codigo_barra (solo principales).
            # Los alts (alt1/2/3) NO se backfilean acá porque ya están vacíos
            # en producción y las columnas van a DROP COLUMN. El script manual
            # `scripts/backfill_codigos_barra.py` cubre el escenario de devs
            # locales con data legacy en alt1/2/3.
            try:
                hay = bf_conn.execute(text(
                    "SELECT 1 FROM producto_codigos_barra LIMIT 1"
                )).first()
                if not hay:
                    bf_conn.execute(text("""
                        INSERT INTO producto_codigos_barra (producto_id, codigo_barra, es_principal, fuente)
                        SELECT id, codigo_barra, TRUE, 'legacy_principal'
                        FROM productos
                        WHERE codigo_barra IS NOT NULL AND codigo_barra <> ''
                        ON CONFLICT (producto_id, codigo_barra) DO NOTHING
                    """))
            except Exception:
                pass
            # producto_precios_hist ← factura_items × facturas
            try:
                hay_precios = bf_conn.execute(text(
                    "SELECT 1 FROM producto_precios_hist LIMIT 1"
                )).first()
                if not hay_precios:
                    bf_conn.execute(text("""
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
                pass
            # Domicilios estructurados (idempotente — 2026-06-09): parsea
            # `domicilios_cliente.direccion` mezclado (p.ej. 'bolivia 1614 DTO 2')
            # y separa `piso`/`depto`/`referencia`. Gate: solo filas con los
            # 3 campos NULL. Tras la 1ra corrida todo queda estructurado y es
            # no-op. Loguea cuántas tocó para detectar volumen legacy.
            try:
                from bot.direcciones import separar_direccion
                cand = bf_conn.execute(text(
                    "SELECT id, direccion FROM domicilios_cliente "
                    "WHERE piso IS NULL AND depto IS NULL AND referencia IS NULL "
                    "  AND direccion IS NOT NULL AND direccion <> ''"
                )).fetchall()
                n_fix = 0
                for (did, d) in cand:
                    r = separar_direccion(d)
                    # Solo persistir si realmente extrajimos algo de unidad
                    if r['piso'] or r['depto'] or r['referencia']:
                        bf_conn.execute(text(
                            "UPDATE domicilios_cliente "
                            "SET direccion = :d, piso = :p, depto = :dep, "
                            "    referencia = :ref "
                            "WHERE id = :id"
                        ), {'d': r['direccion'], 'p': r['piso'],
                            'dep': r['depto'], 'ref': r['referencia'],
                            'id': did})
                        n_fix += 1
                if n_fix:
                    import logging
                    logging.getLogger(__name__).info(
                        f'backfill_estructura_legacy: {n_fix} domicilios normalizados')
            except Exception as _e_bf:
                import logging
                logging.getLogger(__name__).warning(
                    f'backfill_estructura_legacy FALLÓ (no-fatal): {_e_bf}')
    except Exception:
        # Si ni la conexión arranca, lo dejamos pasar — son backfills opcionales.
        pass


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
            # Si ya hay plantilla NO-legacy para esta entidad, no migrar el legacy
            has_real = session.query(Plantilla).filter(
                Plantilla.entidad_tipo == tipo_ent,
                Plantilla.entidad_id == pe.proveedor_id,
                ~Plantilla.nombre.like('[legacy]%'),
            ).first()
            if has_real:
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
    # cadencia_lab_snapshot: columnas de $/mes por cuadrante/bucket (toggle Cant⇄Monto).
    for _cm in ('core_monto', 'ocasional_monto', 'caida_monto', 'dormido_monto',
                'alta_monto', 'media_alta_monto', 'media_monto', 'baja_monto',
                'muy_baja_monto'):
        conn.execute(text(
            f"ALTER TABLE cadencia_lab_snapshot ADD COLUMN IF NOT EXISTS {_cm} DECIMAL(16,2) NOT NULL DEFAULT 0"
        ))
    # Compra rápida v2 (Kel/20j): columna nueva en descuentos_base + tablas
    # proveedor_horarios_reparto y pedido_borrador.
    conn.execute(text(
        "ALTER TABLE descuentos_base ADD COLUMN IF NOT EXISTS descuento_pct_sin_transfer DECIMAL(5,2)"
    ))
    conn.execute(text(
        "ALTER TABLE productos ADD COLUMN IF NOT EXISTS fuente_creacion VARCHAR(30)"
    ))
    conn.execute(text(
        "ALTER TABLE productos ADD COLUMN IF NOT EXISTS excluido_armado_actual BOOLEAN NOT NULL DEFAULT FALSE"
    ))
    conn.execute(text(
        "ALTER TABLE productos ADD COLUMN IF NOT EXISTS no_pedir BOOLEAN NOT NULL DEFAULT FALSE"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_prod_no_pedir ON productos (no_pedir) WHERE no_pedir = TRUE"
    ))
    conn.execute(text(
        "ALTER TABLE productos ADD COLUMN IF NOT EXISTS fraccionado BOOLEAN NOT NULL DEFAULT FALSE"
    ))
    # pack_equivalencias: agregar laboratorio_id (filtrado per-lab) + fuente
    # (origen del row: 'aprendido' por import, 'manual' edicion en UI, 'excel'
    # carga masiva). Backfill: para filas con aprendido_de set, copiar
    # Modulo.laboratorio_id.
    conn.execute(text(
        "ALTER TABLE pack_equivalencias ADD COLUMN IF NOT EXISTS laboratorio_id INTEGER REFERENCES laboratorios(id) ON DELETE SET NULL"
    ))
    conn.execute(text(
        "ALTER TABLE pack_equivalencias ADD COLUMN IF NOT EXISTS fuente VARCHAR(20) NOT NULL DEFAULT 'aprendido'"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_pack_equiv_lab ON pack_equivalencias (laboratorio_id) WHERE laboratorio_id IS NOT NULL"
    ))
    conn.execute(text("""
        UPDATE pack_equivalencias pe
        SET laboratorio_id = m.laboratorio_id
        FROM modulos m
        WHERE pe.aprendido_de = m.id
          AND pe.laboratorio_id IS NULL
          AND m.laboratorio_id IS NOT NULL
    """))
    # Multi-tenant Fase 2: farmacia_id en tablas transaccionales (pedidos,
    # pedido_items, procesos_compra). Default=1 hace el backfill automático
    # sobre filas existentes — todo el histórico cae en la farmacia original.
    #
    # Orden importante:
    # 1. Asegurar Farmacia id=1 existe (necesaria para que la FK no falle).
    # 2. Agregar las columnas farmacia_id con DEFAULT 1.
    # 3. Crear índices.
    # 4. Crear las FK (al final, cuando ya hay datos válidos).
    # Cada paso es idempotente (IF NOT EXISTS / ON CONFLICT / try-except).

    # Paso 1: bootstrap idempotente de Farmacia id=1 para que la FK no rompa.
    # _bootstrap_farmacia_inicial() corre más abajo, pero acá necesitamos
    # garantizarlo ANTES de las FK constraints.
    try:
        conn.execute(text("""
            INSERT INTO farmacias (id, nombre, es_demo, activa)
            VALUES (1, 'Farmacia', FALSE, TRUE)
            ON CONFLICT (id) DO NOTHING
        """))
    except Exception:
        pass

    # Paso 2 + 3: columnas + índices.
    for ddl in (
        "ALTER TABLE pedidos          ADD COLUMN IF NOT EXISTS farmacia_id INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE pedido_items     ADD COLUMN IF NOT EXISTS farmacia_id INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE procesos_compra  ADD COLUMN IF NOT EXISTS farmacia_id INTEGER NOT NULL DEFAULT 1",
        "CREATE INDEX IF NOT EXISTS idx_pedidos_farmacia        ON pedidos (farmacia_id)",
        "CREATE INDEX IF NOT EXISTS idx_pedido_items_farmacia   ON pedido_items (farmacia_id)",
        "CREATE INDEX IF NOT EXISTS idx_procesos_compra_farmacia ON procesos_compra (farmacia_id)",
    ):
        try:
            conn.execute(text(ddl))
        except Exception:
            pass

    # Paso 4: FK constraints. Ya hay Farmacia id=1 y los datos preexistentes
    # apuntan a 1, así que la validación pasa. Si el constraint ya existe,
    # PostgreSQL tira error y lo silenciamos.
    for ddl in (
        "ALTER TABLE pedidos          ADD CONSTRAINT fk_pedidos_farmacia        FOREIGN KEY (farmacia_id) REFERENCES farmacias(id)",
        "ALTER TABLE pedido_items     ADD CONSTRAINT fk_pedido_items_farmacia   FOREIGN KEY (farmacia_id) REFERENCES farmacias(id)",
        "ALTER TABLE procesos_compra  ADD CONSTRAINT fk_procesos_compra_farmacia FOREIGN KEY (farmacia_id) REFERENCES farmacias(id)",
    ):
        try:
            conn.execute(text(ddl))
        except Exception:
            pass
    # Operadores de pedidos y tracking de quién hizo cada etapa.
    for ddl in (
        """CREATE TABLE IF NOT EXISTS usuarios_pedidos (
            id SERIAL PRIMARY KEY,
            nombre VARCHAR(50) NOT NULL UNIQUE,
            activo BOOLEAN NOT NULL DEFAULT TRUE
        )""",
        "ALTER TABLE pedido_emitido ADD COLUMN IF NOT EXISTS emitido_por VARCHAR(50)",
        "ALTER TABLE pedido_emitido ADD COLUMN IF NOT EXISTS recibido_por VARCHAR(50)",
        "ALTER TABLE pedido_emitido ADD COLUMN IF NOT EXISTS cargado_por VARCHAR(50)",
        "ALTER TABLE pedido_emitido ADD COLUMN IF NOT EXISTS origen VARCHAR(20)",
        "CREATE INDEX IF NOT EXISTS ix_pedido_emitido_origen ON pedido_emitido(origen)",
    ):
        try:
            conn.execute(text(ddl))
        except Exception:
            pass
    # Recepción de pedidos: 4 columnas nuevas para 2 vías (operador + Observer).
    for ddl in (
        "ALTER TABLE pedido_emitido_item ADD COLUMN IF NOT EXISTS cantidad_revisada_op INTEGER",
        "ALTER TABLE pedido_emitido_item ADD COLUMN IF NOT EXISTS revisada_en TIMESTAMP",
        "ALTER TABLE pedido_emitido_item ADD COLUMN IF NOT EXISTS cantidad_confirmada_obs INTEGER",
        "ALTER TABLE pedido_emitido_item ADD COLUMN IF NOT EXISTS confirmada_en TIMESTAMP",
        "ALTER TABLE pedido_emitido_item ADD COLUMN IF NOT EXISTS oferta_dto DECIMAL(6,2)",
        "ALTER TABLE pedido_emitido_item ADD COLUMN IF NOT EXISTS oferta_min INTEGER",
    ):
        try:
            conn.execute(text(ddl))
        except Exception:
            pass
    try:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS proveedor_horarios_reparto (
                id SERIAL PRIMARY KEY,
                proveedor_id INTEGER NOT NULL REFERENCES proveedores(id) ON DELETE CASCADE,
                dia_semana INTEGER NOT NULL,
                hora VARCHAR(5) NOT NULL,
                activo BOOLEAN NOT NULL DEFAULT TRUE,
                creado_en TIMESTAMP DEFAULT NOW(),
                UNIQUE (proveedor_id, dia_semana, hora)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_horarios_prov ON proveedor_horarios_reparto (proveedor_id)"))
    except Exception:
        pass
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS pedido_borrador (
            id SERIAL PRIMARY KEY,
            drogueria_id INTEGER NOT NULL REFERENCES proveedores(id) ON DELETE CASCADE,
            producto_id INTEGER REFERENCES productos(id) ON DELETE CASCADE,
            observer_id INTEGER,
            laboratorio_id INTEGER REFERENCES laboratorios(id),
            cantidad INTEGER NOT NULL DEFAULT 0,
            dto_aplicado DECIMAL(5,2),
            motivo VARCHAR(40),
            actualizado_en TIMESTAMP DEFAULT NOW(),
            UNIQUE (drogueria_id, producto_id, observer_id)
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_borrador_drog ON pedido_borrador (drogueria_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_borrador_obs  ON pedido_borrador (observer_id)"))
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
    # Fraccionado: flag de producto (DW.Productos.EsFraccionable) + de stock
    # (DW.StockFarmaciasProductos.Fraccionado). Para fraccionables, obs_stock.
    # stock_actual viene en UNIDADES sueltas, no en envases.
    conn.execute(text("ALTER TABLE obs_productos ADD COLUMN IF NOT EXISTS es_fraccionable BOOLEAN NOT NULL DEFAULT FALSE"))
    conn.execute(text("ALTER TABLE obs_stock ADD COLUMN IF NOT EXISTS fraccionado BOOLEAN NOT NULL DEFAULT FALSE"))
    # Provider: mínimo de compra (puede no estar en deploys viejos)
    conn.execute(text("ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS compra_minima_pesos DECIMAL(14, 2)"))
    conn.execute(text("ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS descuento_con_transfer DECIMAL(5, 2)"))
    conn.execute(text("ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS descuento_sin_transfer DECIMAL(5, 2)"))
    # usa_packs: habilita "Cargar Packs" en /compras/laboratorio (lab y prov).
    conn.execute(text("ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS usa_packs BOOLEAN NOT NULL DEFAULT FALSE"))
    # Filtro droguería: config del archivo de pedido por droguería (antes DROG_CFG).
    conn.execute(text("ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS codcli VARCHAR(20)"))
    conn.execute(text("ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS formato_archivo VARCHAR(20)"))
    conn.execute(text("ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS sufijo VARCHAR(10)"))
    conn.execute(text("ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS carpeta_filtro VARCHAR(200)"))
    conn.execute(text("ALTER TABLE laboratorios ADD COLUMN IF NOT EXISTS usa_packs BOOLEAN NOT NULL DEFAULT FALSE"))
    # OfertaMinimo: campos nuevos Fase 2 compra rápida
    conn.execute(text("ALTER TABLE ofertas_minimo ADD COLUMN IF NOT EXISTS drogueria_id INTEGER REFERENCES proveedores(id)"))
    conn.execute(text("ALTER TABLE ofertas_minimo ADD COLUMN IF NOT EXISTS vigencia_desde DATE"))
    conn.execute(text("ALTER TABLE ofertas_minimo ADD COLUMN IF NOT EXISTS vigencia_hasta DATE"))
    conn.execute(text("ALTER TABLE ofertas_minimo ADD COLUMN IF NOT EXISTS observacion VARCHAR(200)"))
    conn.execute(text("ALTER TABLE ofertas_minimo ADD COLUMN IF NOT EXISTS activo BOOLEAN NOT NULL DEFAULT TRUE"))
    # Hacer laboratorio_id nullable: ahora permitimos ofertas por-droguería
    # multi-lab donde el lab se deduce por producto (puede ser NULL).
    try:
        conn.execute(text("ALTER TABLE ofertas_minimo ALTER COLUMN laboratorio_id DROP NOT NULL"))
    except Exception:
        pass  # ya era nullable
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ofertas_drog ON ofertas_minimo(drogueria_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ofertas_vig  ON ofertas_minimo(vigencia_hasta)"))
    # ── EquivalenciaProveedor: extensión 2026-05-11 ──
    # Soporte para equivalencias vía droguería + código del proveedor.
    try:
        conn.execute(text(
            "ALTER TABLE equivalencias_proveedor ALTER COLUMN laboratorio_id DROP NOT NULL"
        ))
    except Exception:
        pass
    conn.execute(text(
        "ALTER TABLE equivalencias_proveedor "
        "ADD COLUMN IF NOT EXISTS drogueria_id INTEGER REFERENCES proveedores(id) ON DELETE CASCADE"
    ))
    conn.execute(text(
        "ALTER TABLE equivalencias_proveedor "
        "ADD COLUMN IF NOT EXISTS codigo_proveedor VARCHAR(50)"
    ))
    try:
        conn.execute(text(
            "ALTER TABLE equivalencias_proveedor "
            "ALTER COLUMN descripcion_proveedor DROP NOT NULL"
        ))
    except Exception:
        pass
    try:
        conn.execute(text(
            "ALTER TABLE equivalencias_proveedor "
            "ALTER COLUMN descripcion_proveedor_norm DROP NOT NULL"
        ))
    except Exception:
        pass
    # Cada CREATE INDEX wrapped en try/except: si quedó un índice zombie de
    # un deploy anterior (pg_class entry sin definición usable), `IF NOT EXISTS`
    # no es suficiente — PG arroja UniqueViolation en pg_class_relname_nsp_index.
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_equiv_drog ON equivalencias_proveedor(drogueria_id)",
        "CREATE INDEX IF NOT EXISTS idx_equiv_codigo ON equivalencias_proveedor(codigo_proveedor)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_equiv_drog_desc "
        "ON equivalencias_proveedor (drogueria_id, descripcion_proveedor_norm) "
        "WHERE drogueria_id IS NOT NULL AND descripcion_proveedor_norm IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_equiv_drog_codigo "
        "ON equivalencias_proveedor (drogueria_id, codigo_proveedor) "
        "WHERE drogueria_id IS NOT NULL AND codigo_proveedor IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_equiv_lab_codigo "
        "ON equivalencias_proveedor (laboratorio_id, codigo_proveedor) "
        "WHERE laboratorio_id IS NOT NULL AND codigo_proveedor IS NOT NULL",
    ):
        try:
            conn.execute(text(ddl))
        except Exception:
            pass
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
    # Composite indexes para análisis OS — las queries del módulo filtran por una entidad
    # (OS / médico / cliente / producto) y rango de fechas. Sin estos índices, full scan
    # de 891k filas en cada request del módulo OS.
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ovd_os_fecha ON obs_ventas_detalle(obra_social_observer, fecha_estadistica)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ovd_medico_fecha ON obs_ventas_detalle(medico_observer, fecha_estadistica)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ovd_cliente_fecha ON obs_ventas_detalle(cliente_observer, fecha_estadistica)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ovd_producto_fecha ON obs_ventas_detalle(producto_observer, fecha_estadistica)"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS observer_ventas_meses INTEGER NOT NULL DEFAULT 16"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS transfer_excedente_meses DECIMAL(5,1) NOT NULL DEFAULT 6.0"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS transfer_necesita_meses DECIMAL(5,1) NOT NULL DEFAULT 2.0"))
    conn.execute(text("ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS farmacia_cuit VARCHAR(20)"))
    # sucursales: se unificó a una sola URL — limpiar la columna interna obsoleta.
    conn.execute(text("ALTER TABLE sucursales DROP COLUMN IF EXISTS url_interna"))
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
    # NOTA: panel_comandos y alarmas_notificadas se crean en el bloque AUTOCOMMIT
    # temprano (database.py ~1310) para que NO dependan de esta transacción.
    # Si una migración acá aborta, esas tablas críticas ya quedaron persistidas.
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
    # Backups automáticos
    for ddl in [
        "ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS backup_ruta_remota VARCHAR(500)",
        "ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS backup_hora INTEGER NOT NULL DEFAULT 17",
        "ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS backup_diarios_max INTEGER NOT NULL DEFAULT 7",
        "ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS backup_semanales_max INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS backup_quincenales_max INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS backup_mensuales_max INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS backup_ultimo_status VARCHAR(10)",
        "ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS backup_ultimo_corrida_en TIMESTAMP",
        "ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS backup_ultimo_error VARCHAR(500)",
        "ALTER TABLE configuracion ADD COLUMN IF NOT EXISTS backup_ultimo_tamano_mb DECIMAL(10, 2)",
    ]:
        conn.execute(text(ddl))
    # Asegurar DEFAULT en columnas NOT NULL — SQLAlchemy `default=...` es
    # client-side, no genera SQL DEFAULT, y un INSERT que omite la columna
    # NotNull falla si la tabla se creó sin DEFAULT (problema histórico de
    # tablas viejas: ALTER ADD COLUMN IF NOT EXISTS no actualiza el default).
    for ddl in (
        "ALTER TABLE configuracion ALTER COLUMN observer_ventas_meses SET DEFAULT 16",
        "ALTER TABLE configuracion ALTER COLUMN backup_hora SET DEFAULT 17",
        "ALTER TABLE configuracion ALTER COLUMN backup_diarios_max SET DEFAULT 7",
        "ALTER TABLE configuracion ALTER COLUMN backup_semanales_max SET DEFAULT 0",
        "ALTER TABLE configuracion ALTER COLUMN backup_quincenales_max SET DEFAULT 1",
        "ALTER TABLE configuracion ALTER COLUMN backup_mensuales_max SET DEFAULT 0",
        # Defaults recuperados el 2026-05-28 (causa raíz del wipe): el INSERT
        # inicial no lista estas columnas y, sin DEFAULT a nivel DB, dispara
        # NotNullViolation → zombie handler → wipe. Idempotente.
        "ALTER TABLE configuracion ALTER COLUMN transfer_excedente_meses SET DEFAULT 6.0",
        "ALTER TABLE configuracion ALTER COLUMN transfer_necesita_meses SET DEFAULT 2.0",
    ):
        try:
            conn.execute(text(ddl))
        except Exception:
            pass
    conn.execute(text(
        "INSERT INTO configuracion "
        "(id, farmacia_nombre, umbral_pico, umbral_baja, umbral_tendencia, "
        " rot_alta_min, rot_alta_tol, rot_media_min, rot_media_tol, rot_baja_tol, "
        " keep_alive_enabled, keep_alive_interval_min, observer_ventas_meses, "
        " backup_hora, backup_diarios_max, backup_semanales_max, "
        " backup_quincenales_max, backup_mensuales_max) "
        "VALUES (1, 'Farmacia', 1.30, 0.70, 0.20, 20.0, 0.0, 5.0, 0.0, 0.0, FALSE, 10, 16, "
        " 17, 7, 0, 1, 0) "
        "ON CONFLICT DO NOTHING"
    ))
    conn.execute(text(
        "ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS ruta_facturas VARCHAR(500)"
    ))
    conn.execute(text(
        "ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS matriz_visible BOOLEAN NOT NULL DEFAULT TRUE"
    ))
    conn.execute(text(
        "ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS matriz_orden INTEGER"
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
    conn.execute(text("ALTER TABLE productos ADD COLUMN IF NOT EXISTS cantidad_reposicion_fija INTEGER"))
    conn.execute(text("ALTER TABLE laboratorios ADD COLUMN IF NOT EXISTS descuento_base DECIMAL(5,2)"))
    # Cronograma: partner polimórfico (lab|drog) + canal_drog (vía droguería
    # cuando partner=lab, NULL=directo). Reemplaza el FK estricto a proveedores.
    # Cada DDL en su propio try/except: PG en algunos modos lanza UniqueViolation
    # aunque uses IF NOT EXISTS (en pg_class), así que silenciamos esos casos.
    for _ddl in (
        "ALTER TABLE proveedor_cronograma DROP CONSTRAINT IF EXISTS proveedor_cronograma_proveedor_id_fkey",
        "ALTER TABLE proveedor_cronograma ADD COLUMN IF NOT EXISTS partner_tipo VARCHAR(12) NOT NULL DEFAULT 'drogueria'",
        "ALTER TABLE proveedor_cronograma ADD COLUMN IF NOT EXISTS canal_drog_id INTEGER REFERENCES proveedores(id) ON DELETE SET NULL",
        "CREATE INDEX IF NOT EXISTS idx_cronograma_partner_tipo ON proveedor_cronograma (partner_tipo)",
        "ALTER TABLE proveedor_cronograma DROP CONSTRAINT IF EXISTS uq_cronograma_prov_tipo",
        """DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_cronograma_partner_tipo'
            ) THEN
                ALTER TABLE proveedor_cronograma
                ADD CONSTRAINT uq_cronograma_partner_tipo
                UNIQUE (partner_tipo, proveedor_id, tipo_pedido);
            END IF;
        END $$;""",
    ):
        try:
            conn.execute(text(_ddl))
        except Exception as _e:
            # Si el objeto ya existe en pg_class/pg_constraint con el mismo nombre,
            # asumimos idempotencia y seguimos.
            if 'already exists' in str(_e) or 'duplicate key' in str(_e):
                continue
            raise

    # Tabla `tipo_pedido_config` + seed inicial. La tabla la crea SQLAlchemy
    # con create_all (corre ANTES que _pg_add_columns), así que acá ya existe.
    # ON CONFLICT (slug) DO NOTHING hace el INSERT idempotente sobre re-deploys.
    import json as _json
    conn.execute(text(
        "ALTER TABLE tipo_pedido_config ADD COLUMN IF NOT EXISTS categoria VARCHAR(20) NOT NULL DEFAULT 'pedido'"
    ))
    _seed_tipos = [
        ('REPOSICION', 'Reposición (matriz lab/drog)', 'pedido',
         'Pedido corto y rutinario por droguería. Piso = tasa diaria × 4 días. '
         'Sin target adicional — pide lo mínimo necesario para cubrir el período.',
         {'piso_ideal': 'daily_rate_x_cubrir_dias', 'target_horizonte': 'none',
          'buffer_pct': 0, 'universo': 'bajo_min_o_cobertura',
          'override_producto': 'cantidad_reposicion_fija', 'redondeo': 'ceil',
          'dias_cobertura_fijo': 4}),
        ('COMPRA_LAB', 'Compra directa al laboratorio', 'pedido',
         'Pedido grande y planificado al lab. Sin piso de mínimo del producto. '
         'Cantidad = tasa diaria × cubrir_dias (configurable por slider).',
         {'piso_ideal': 'daily_rate_x_cubrir_dias', 'target_horizonte': 'none',
          'buffer_pct': 0, 'universo': 'lab_x',
          'override_producto': 'none', 'redondeo': 'ceil'}),
        ('PRUEBA', 'Planificación estacional', 'pedido',
         'Planificación grande con estacionalidad. Base = ventas 12m × índice '
         'estacional de la droga × cobertura. El mínimo de oferta solo informa '
         '(chip), no sube la cantidad. La cantidad fija del producto gana.',
         {'piso_ideal': 'min_efectivo', 'target_horizonte': 'none',
          'buffer_pct': 0, 'universo': 'manual',
          'override_producto': 'cantidad_reposicion_fija', 'redondeo': 'ceil',
          'base_demanda': 'u12m_estacional',
          'cant_fija_efecto': 'override',
          'oferta_min_efecto': 'indicador'}),
        ('PEDIDO_ROEMMERS', 'Pedido Roemmers (módulos + ofertas)', 'pedido',
         'Flujo exclusivo Roemmers: módulos de descuento primero + ofertas con '
         'mínimo después + saldo final. Ventana de promedio = 3 meses recientes. '
         'En step 2 (ofertas), el mínimo de oferta sube la cantidad (piso). '
         'Excluye productos sin deal por default (toggle para ver).',
         {'piso_ideal': 'daily_rate_x_cubrir_dias', 'target_horizonte': 'none',
          'buffer_pct': 0, 'universo': 'lab_x',
          'override_producto': 'cantidad_reposicion_fija', 'redondeo': 'ceil',
          'base_demanda': 'u3m',
          'cant_fija_efecto': 'override',
          'oferta_min_efecto': 'piso'}),
        ('DISCONTINUADO', 'Discontinuado', 'flag',
         'Producto fuera de línea. Vender hasta agotar stock, no reponer.',
         {'efecto_armado': 'badge_cero', 'icono': '🚫', 'color': 'red',
          'permite_reemplazo': False, 'permite_vigencia': False}),
        ('REEMPLAZADO', 'Reemplazado por otro', 'flag',
         'El producto fue reemplazado por una nueva presentación o marca.',
         {'efecto_armado': 'solo_badge', 'icono': '↔', 'color': 'violet',
          'permite_reemplazo': True, 'permite_vigencia': False}),
        ('SIN_DESCUENTO', 'Sin descuento vigente', 'flag',
         'El descuento que tenía este producto/lab ya no aplica.',
         {'efecto_armado': 'solo_badge', 'icono': '💡', 'color': 'amber',
          'permite_reemplazo': False, 'permite_vigencia': True}),
        ('NOTA', 'Nota informativa', 'flag',
         'Comentario libre sobre el producto o laboratorio.',
         {'efecto_armado': 'ninguno', 'icono': '📝', 'color': 'sky',
          'permite_reemplazo': False, 'permite_vigencia': False}),
        ('SOLO_UNO', 'Pedir solo 1 unidad', 'flag',
         'Al armar el pedido, la cantidad de este producto se topea en 1 unidad.',
         {'efecto_armado': 'tope_uno', 'icono': '1️⃣', 'color': 'sky',
          'permite_reemplazo': False, 'permite_vigencia': False}),
        ('AGOTAR_TODO', 'Agotar stock', 'flag',
         'Nunca repone (discontinuar). a_pedir = 0 siempre.',
         {'efecto_armado': 'agotar_todo', 'icono': '📉', 'color': 'amber',
          'permite_reemplazo': False, 'permite_vigencia': False}),
        ('AGOTAR_HASTA_1', 'Agotar hasta 1', 'flag',
         'No repone mientras tenga stock; repone 1 solo cuando llega a 0 (mantiene 1 unidad).',
         {'efecto_armado': 'agotar_hasta_1', 'icono': '📉', 'color': 'amber',
          'permite_reemplazo': False, 'permite_vigencia': False}),
    ]
    for slug, nombre, cat, desc, cfg in _seed_tipos:
        try:
            conn.execute(text(
                "INSERT INTO tipo_pedido_config (slug, nombre, categoria, descripcion, config_json, activo) "
                "VALUES (:slug, :nombre, :cat, :desc, :cfg, true) "
                "ON CONFLICT (slug) DO NOTHING"
            ), {'slug': slug, 'nombre': nombre, 'cat': cat, 'desc': desc, 'cfg': _json.dumps(cfg)})
        except Exception:
            pass
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
    conn.execute(text("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS mostrar_hasta DATE"))
    conn.execute(text("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS origen VARCHAR(20)"))
    try:
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_pedidos_origen ON pedidos(origen)"))
    except Exception:
        pass
    # CREATE INDEX IF NOT EXISTS puede tirar UniqueViolation si pg_class tiene
    # row huérfana (deploy previo abortó). Como corremos en AUTOCOMMIT, cada
    # DDL es su propia tx — absorbemos el error y seguimos. La data queda OK.
    for _idx_sql in (
        "CREATE INDEX IF NOT EXISTS idx_pedidos_mostrar_hasta ON pedidos(mostrar_hasta)",
        "CREATE INDEX IF NOT EXISTS idx_pedidos_partner_id ON pedidos(partner_id)",
        # Para el check `check_pedidos_pendientes_viejos` (filtra estado + creado_en).
        "CREATE INDEX IF NOT EXISTS idx_pedidos_estado_creado ON pedidos(estado, creado_en)",
    ):
        try:
            conn.execute(text(_idx_sql))
        except Exception:
            pass
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
    conn.execute(text(
        "ALTER TABLE product_analytics ADD COLUMN IF NOT EXISTS rubro VARCHAR(150)"
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
    # 2026-05-28: unificar tipos. Toda oferta es 'con_minimo'; sin mín importado
    # se normaliza a unidades_minima=1. El split 'simple'/'con_minimo' se eliminó
    # del importer — esta migración colapsa lo viejo para que el listado no
    # muestre filas duplicadas por lab.
    conn.execute(text("""
        UPDATE ofertas_minimo
        SET unidades_minima = 1
        WHERE unidades_minima IS NULL OR unidades_minima < 1
    """))
    conn.execute(text(
        "UPDATE ofertas_minimo SET tipo_descuento = 'con_minimo' "
        "WHERE tipo_descuento IS NULL OR tipo_descuento = 'simple'"
    ))
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
    # Atributos estructurados de productos (1-a-1 con productos): droga, concentración,
    # forma farmacéutica, cantidad de envase. Se puebla con backfill incremental.
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS producto_atributos (
            producto_id          INTEGER PRIMARY KEY REFERENCES productos(id) ON DELETE CASCADE,
            monodroga_norm       VARCHAR(500),
            monodroga_display    VARCHAR(500),
            concentracion_mg     DECIMAL(12, 4),
            concentracion_unidad VARCHAR(15),
            forma_farma          VARCHAR(10),
            cantidad_envase      DECIMAL(10, 3),
            via_admin            VARCHAR(10),
            fuente               VARCHAR(15) NOT NULL DEFAULT 'regex',
            confianza            VARCHAR(8)  NOT NULL DEFAULT 'MEDIA',
            raw_descripcion      VARCHAR(300),
            extraido_en          TIMESTAMP DEFAULT NOW()
        )
    """))
    # Bump VARCHAR(200) → VARCHAR(500) en monodroga_norm.
    try:
        conn.execute(text("ALTER TABLE producto_atributos ALTER COLUMN monodroga_norm TYPE VARCHAR(500)"))
    except Exception:
        pass
    # Migración one-time: copiar monodroga_display → productos.monodroga donde esté
    # vacío, luego dropear la columna (eliminada como dup de Producto.monodroga).
    # Guardado con un check de existencia: en DBs ya migradas la columna no existe
    # y el UPDATE tiraba "column does not exist" en cada arranque (ruido en el log).
    try:
        col_existe = conn.execute(text("""
            SELECT 1 FROM information_schema.columns
             WHERE table_name = 'producto_atributos'
               AND column_name = 'monodroga_display'
        """)).first()
        if col_existe:
            conn.execute(text("""
                UPDATE productos p
                   SET monodroga = pa.monodroga_display
                  FROM producto_atributos pa
                 WHERE pa.producto_id = p.id
                   AND (p.monodroga IS NULL OR p.monodroga = '')
                   AND pa.monodroga_display IS NOT NULL
                   AND pa.monodroga_display != ''
            """))
            conn.execute(text("ALTER TABLE producto_atributos DROP COLUMN IF EXISTS monodroga_display"))
    except Exception:
        pass
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_atributos_droga    ON producto_atributos (monodroga_norm)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_atributos_conc     ON producto_atributos (concentracion_mg)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_atributos_forma    ON producto_atributos (forma_farma)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_atributos_fuente   ON producto_atributos (fuente)"))
    # EANs por producto en 1-a-N (reemplazo gradual de alt1/2/3).
    # CREATE + indexes en la conexión transaccional (idempotente, no tira).
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS producto_codigos_barra (
            id           SERIAL PRIMARY KEY,
            producto_id  INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
            codigo_barra VARCHAR(20) NOT NULL,
            es_principal BOOLEAN NOT NULL DEFAULT FALSE,
            fuente       VARCHAR(20) NOT NULL DEFAULT 'manual',
            factura_id   INTEGER REFERENCES facturas(id) ON DELETE SET NULL,
            creado_en    TIMESTAMP DEFAULT NOW(),
            UNIQUE (producto_id, codigo_barra)
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_pcb_producto ON producto_codigos_barra (producto_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_pcb_codigo   ON producto_codigos_barra (codigo_barra)"))
    # UNIQUE compuesto idempotente. La tabla original lo declara como `UNIQUE (...)`
    # inline en el CREATE TABLE, pero si se creó antes de ese cambio, falta. Acá
    # garantizamos que esté — el backfill `ON CONFLICT (producto_id, codigo_barra)`
    # lo necesita o tira "no unique or exclusion constraint matching".
    # Antes de crear, chequear duplicados — sin esto, si hay dupes el CREATE
    # UNIQUE INDEX rompe y aborta la transacción completa de _pg_add_columns,
    # haciendo que migraciones posteriores (CREATE TABLE panel_comandos, etc.)
    # nunca corran.
    pcb_dup = conn.execute(text(
        "SELECT producto_id, codigo_barra, COUNT(*) AS n FROM producto_codigos_barra "
        "GROUP BY producto_id, codigo_barra HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if pcb_dup:
        import logging
        logging.getLogger(__name__).warning(
            'producto_codigos_barra tiene duplicados (producto_id=%s codigo_barra=%s aparece %s veces). '
            'No se crea uq_pcb_producto_codigo hasta resolverlos.', pcb_dup[0], pcb_dup[1], pcb_dup[2]
        )
    else:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_pcb_producto_codigo "
            "ON producto_codigos_barra (producto_id, codigo_barra)"
        ))
    # Backfills movidos a thread background (ver _ejecutar_backfills_async al final
    # de init_db). En Render, correr estos INSERTs masivos en el path crítico de
    # boot hace que el HTTP port no abra a tiempo y el deploy falle con
    # "No open HTTP ports detected". Ahora arrancan después de que el master ya
    # forkeó los workers y el port está accesible.
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
    # tipo_operacion en obs_ventas_detalle: distingue 'V' (venta) de devoluciones/NC.
    # OJO: NO hacer UPDATE masivo de filas existentes — la tabla puede ser
    # millones de rows y un UPDATE las reescribe (DELETE+INSERT en PG) llenando
    # el disco de Render (incidente 2026-05-06: DiskFull). El ADD COLUMN con
    # DEFAULT 'V' en PG ≥11 es instantáneo (catálogo, sin reescribir filas).
    # Las filas existentes leen 'V' implícito; las nuevas también arrancan en
    # 'V' hasta que un re-sync las pise con el tipo real (V/D/NC).
    conn.execute(text(
        "ALTER TABLE obs_ventas_detalle ADD COLUMN IF NOT EXISTS tipo_operacion VARCHAR(2) DEFAULT 'V'"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_ovd_tipo ON obs_ventas_detalle(tipo_operacion)"
    ))
    # Devoluciones v2 (2026-05-18): rol auditor + etapa 2 + AGREGAR DATOS
    # estructurado + observaciones exclusivas del operador rendicion.
    for stmt in [
        "ALTER TABLE motivo_devolucion ADD COLUMN IF NOT EXISTS uso_rol VARCHAR(20) NOT NULL DEFAULT 'ambos'",
        # Motivo que bloquea el check "Rendida" (receta no disponible físicamente).
        "ALTER TABLE motivo_devolucion ADD COLUMN IF NOT EXISTS bloquea_rendida BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE devolucion_receta ADD COLUMN IF NOT EXISTS observaciones_rendicion TEXT",
        "ALTER TABLE devolucion_receta ADD COLUMN IF NOT EXISTS agregar_datos_json TEXT",
        "ALTER TABLE devolucion_receta ADD COLUMN IF NOT EXISTS auditor_motivo_id INTEGER REFERENCES motivo_devolucion(id)",
        "ALTER TABLE devolucion_receta ADD COLUMN IF NOT EXISTS auditor_observaciones TEXT",
        "ALTER TABLE devolucion_receta ADD COLUMN IF NOT EXISTS auditor_user VARCHAR(100)",
        "ALTER TABLE devolucion_receta ADD COLUMN IF NOT EXISTS auditor_fecha TIMESTAMP",
        "ALTER TABLE devolucion_receta ADD COLUMN IF NOT EXISTS rendicion_lote_id INTEGER REFERENCES rendicion_lote(id)",
        # Posesión / etapa: False = la tiene el vendedor, True = "Rendida" (pasó al auditor).
        "ALTER TABLE devolucion_receta ADD COLUMN IF NOT EXISTS en_auditoria BOOLEAN NOT NULL DEFAULT FALSE",
        # Rendida a la obra social (solo recetas OK). True → pasa a histórico.
        "ALTER TABLE devolucion_receta ADD COLUMN IF NOT EXISTS rendida_os BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE devolucion_receta ADD COLUMN IF NOT EXISTS rendida_os_en TIMESTAMP",
        "ALTER TABLE devolucion_receta ADD COLUMN IF NOT EXISTS rendida_os_por VARCHAR(100)",
        # Cleanup 2026-05-18: DestinoDevolucion deprecado completamente.
        "ALTER TABLE devolucion_receta DROP COLUMN IF EXISTS destino_id",
        "DROP TABLE IF EXISTS destino_devolucion CASCADE",
        # Estado físico de entrega del lote — lo marca el auditor.
        "ALTER TABLE rendicion_lote ADD COLUMN IF NOT EXISTS entregada BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE rendicion_lote ADD COLUMN IF NOT EXISTS entregada_en TIMESTAMP",
        "ALTER TABLE rendicion_lote ADD COLUMN IF NOT EXISTS entregada_por VARCHAR(100)",
        # Bot: vinculación de la conversación con la ficha del cliente (ObsCliente).
        "ALTER TABLE bot_conversaciones ADD COLUMN IF NOT EXISTS cliente_observer_id INTEGER",
        "ALTER TABLE bot_conversaciones ADD COLUMN IF NOT EXISTS cliente_local_id INTEGER",
        # Flag de encargo pendiente (producto encargado por el bot, visible en bandeja).
        "ALTER TABLE bot_conversaciones ADD COLUMN IF NOT EXISTS tiene_encargo BOOLEAN NOT NULL DEFAULT FALSE",
        # Producto que el cliente "iba a encargar" cuando arranca el flujo de identificación (DNI/nombre).
        "ALTER TABLE bot_conversaciones ADD COLUMN IF NOT EXISTS producto_pendiente TEXT",
        # Presencia de agentes en el panel de atención.
        "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS estado_presencia VARCHAR(12) NOT NULL DEFAULT 'online'",
        "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS ultima_actividad TIMESTAMP",
        # Ciudad en el lead local (alta de clientes del bot).
        "ALTER TABLE clientes_locales ADD COLUMN IF NOT EXISTS ciudad VARCHAR(120)",
    ]:
        conn.execute(text(stmt))
    # Migración PedidoReparto — campos de la planilla real (2026-06-07)
    for stmt in [
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS tomo VARCHAR(35)",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS canal VARCHAR(15) NOT NULL DEFAULT 'manual'",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS importe DECIMAL(12,2)",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS forma_pago VARCHAR(20)",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS vuelto VARCHAR(80)",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS requiere_receta BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS pagado BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS turno VARCHAR(6)",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS cadete_id INTEGER REFERENCES cadetes(id) ON DELETE SET NULL",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS entregado_por VARCHAR(35)",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS recibio VARCHAR(35)",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS observacion TEXT",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS producto VARCHAR(200)",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS envio_costo NUMERIC(10,2)",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS producto_observer_id INTEGER",
        "CREATE INDEX IF NOT EXISTS idx_pedidos_reparto_producto_obs ON pedidos_reparto(producto_observer_id)",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS waha_msg_id VARCHAR(120)",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS publicado_en TIMESTAMP",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS tomado_por_wsap VARCHAR(80)",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS tomado_en TIMESTAMP",
        "CREATE INDEX IF NOT EXISTS idx_pedidos_reparto_waha_msg ON pedidos_reparto(waha_msg_id)",
        "ALTER TABLE domicilios_cliente ADD COLUMN IF NOT EXISTS geo_actualizado_en TIMESTAMP",
        # Domicilios estructurados: piso/depto/referencia separados de direccion
        "ALTER TABLE domicilios_cliente ADD COLUMN IF NOT EXISTS piso VARCHAR(20)",
        "ALTER TABLE domicilios_cliente ADD COLUMN IF NOT EXISTS depto VARCHAR(20)",
        "ALTER TABLE domicilios_cliente ADD COLUMN IF NOT EXISTS referencia VARCHAR(200)",
        "CREATE INDEX IF NOT EXISTS idx_pedidos_reparto_cadete ON pedidos_reparto(cadete_id)",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS piso VARCHAR(20)",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS depto VARCHAR(20)",
        "ALTER TABLE pedidos_reparto ADD COLUMN IF NOT EXISTS referencia VARCHAR(200)",
    ]:
        conn.execute(text(stmt))
    # Token para link móvil del cadete (vista de reparto sin login)
    conn.execute(text(
        "ALTER TABLE cadetes ADD COLUMN IF NOT EXISTS token VARCHAR(12)"
    ))
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_cadetes_token ON cadetes(token)"
    ))

    # ── Unificación de clientes en tabla única — Fase 2a ADITIVA (2026-06-07) ──
    # Ver docs/plan_clientes_unica.md. SOLO agrega columnas + backfill idempotente;
    # NO dropea nada (los DROP de columnas viejas + clientes_locales van en 2b, commit
    # aparte). Seguro de correr en cada boot (Badia, sin Alembic, se migra acá).
    for stmt in [
        "ALTER TABLE clientes ALTER COLUMN observer_id DROP NOT NULL",
        "ALTER TABLE clientes ADD COLUMN IF NOT EXISTS nombre VARCHAR(80)",
        "ALTER TABLE clientes ADD COLUMN IF NOT EXISTS apellido VARCHAR(80)",
        "ALTER TABLE clientes ADD COLUMN IF NOT EXISTS dni VARCHAR(20)",
        "ALTER TABLE clientes ADD COLUMN IF NOT EXISTS domicilio VARCHAR(200)",
        "ALTER TABLE clientes ADD COLUMN IF NOT EXISTS telefono VARCHAR(35)",
        "ALTER TABLE clientes ADD COLUMN IF NOT EXISTS ciudad VARCHAR(120)",
        "ALTER TABLE clientes ADD COLUMN IF NOT EXISTS creado_por INTEGER",
        # mapa lead→cliente para backfill idempotente (se dropea en 2b)
        "ALTER TABLE clientes ADD COLUMN IF NOT EXISTS legacy_local_id INTEGER",
        "CREATE INDEX IF NOT EXISTS idx_clientes_dni ON clientes(dni)",
    ]:
        conn.execute(text(stmt))
    # cliente_id (FK clientes.id) en las 4 tablas referenciantes.
    for _t, _od in [('bot_conversaciones', 'SET NULL'), ('tickets_caja', 'SET NULL'),
                    ('pedidos_reparto', 'SET NULL'), ('domicilios_cliente', 'CASCADE')]:
        conn.execute(text(
            f"ALTER TABLE {_t} ADD COLUMN IF NOT EXISTS cliente_id INTEGER "
            f"REFERENCES clientes(id) ON DELETE {_od}"))
        conn.execute(text(
            f"CREATE INDEX IF NOT EXISTS idx_{_t}_cliente ON {_t}(cliente_id)"))
    # Backfill idempotente (clientes_locales todavía existe en 2a).
    # 1. Marcar el lead que corresponde a una fila clientes ya existente (por observer_id).
    conn.execute(text("""
        UPDATE clientes c SET legacy_local_id = cl.id
          FROM clientes_locales cl
         WHERE cl.observer_id IS NOT NULL AND c.observer_id = cl.observer_id
           AND c.legacy_local_id IS NULL
    """))
    # 2. Completar campos vacíos de esa fila con los del lead (no pisa lo cargado).
    conn.execute(text("""
        UPDATE clientes c SET
            nombre = COALESCE(c.nombre, cl.nombre),
            apellido = COALESCE(c.apellido, cl.apellido),
            dni = COALESCE(c.dni, cl.dni),
            domicilio = COALESCE(c.domicilio, cl.domicilio),
            telefono = COALESCE(c.telefono, cl.telefono),
            ciudad = COALESCE(c.ciudad, cl.ciudad),
            notas = COALESCE(c.notas, cl.notas),
            creado_por = COALESCE(c.creado_por, cl.creado_por)
          FROM clientes_locales cl
         WHERE c.legacy_local_id = cl.id
    """))
    # 3. Insertar leads que todavía no tienen fila clientes (idempotente por legacy_local_id;
    #    evita colisión de UNIQUE(observer_id) si el observer ya tiene fila).
    conn.execute(text("""
        INSERT INTO clientes (observer_id, nombre, apellido, dni, domicilio, telefono,
                              ciudad, notas, creado_por, creado_en, legacy_local_id)
        SELECT cl.observer_id, cl.nombre, cl.apellido, cl.dni, cl.domicilio, cl.telefono,
               cl.ciudad, cl.notas, cl.creado_por, cl.creado_en, cl.id
          FROM clientes_locales cl
         WHERE NOT EXISTS (SELECT 1 FROM clientes c WHERE c.legacy_local_id = cl.id)
           AND (cl.observer_id IS NULL
                OR NOT EXISTS (SELECT 1 FROM clientes c2 WHERE c2.observer_id = cl.observer_id))
    """))
    # 4. Crear fila clientes faltante por cada observer_id referenciado en las 4 tablas.
    for _t in ('bot_conversaciones', 'tickets_caja', 'pedidos_reparto', 'domicilios_cliente'):
        conn.execute(text(f"""
            INSERT INTO clientes (observer_id)
            SELECT DISTINCT t.cliente_observer_id FROM {_t} t
             WHERE t.cliente_observer_id IS NOT NULL
               AND NOT EXISTS (SELECT 1 FROM clientes c WHERE c.observer_id = t.cliente_observer_id)
        """))
    # 5. Backfill cliente_id (por observer_id y por local_id vía legacy_local_id).
    for _t in ('bot_conversaciones', 'tickets_caja', 'pedidos_reparto', 'domicilios_cliente'):
        conn.execute(text(f"""
            UPDATE {_t} t SET cliente_id = c.id FROM clientes c
             WHERE t.cliente_id IS NULL AND t.cliente_observer_id IS NOT NULL
               AND c.observer_id = t.cliente_observer_id
        """))
        conn.execute(text(f"""
            UPDATE {_t} t SET cliente_id = c.id FROM clientes c
             WHERE t.cliente_id IS NULL AND t.cliente_local_id IS NOT NULL
               AND c.legacy_local_id = t.cliente_local_id
        """))

    # OS confirmada manualmente por el operador (toma precedencia sobre la inferida)
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS cliente_os_confirmada (
            cliente_observer_id     INTEGER PRIMARY KEY,
            obra_social_observer_id INTEGER NOT NULL,
            obra_social_nombre      VARCHAR(150) NOT NULL DEFAULT '',
            confirmado_por          VARCHAR(80),
            confirmado_en           TIMESTAMP DEFAULT NOW()
        )
    """))

    # Ofertas para el bot (descuento % o 2x1) — debe crearse ANTES de ofertas_registro (FK)
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS ofertas_bot (
            id          SERIAL PRIMARY KEY,
            observer_id INTEGER      NOT NULL,
            descripcion VARCHAR(200) NOT NULL,
            tipo        VARCHAR(20)  NOT NULL,
            valor       DECIMAL(6,2),
            activo      BOOLEAN      NOT NULL DEFAULT TRUE,
            creado_en   TIMESTAMP    DEFAULT NOW()
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_ofertas_bot_observer ON ofertas_bot(observer_id)"))

    # Registro de ofertas ofrecidas (Fase 2: integración bot) — REFERENCES ofertas_bot, va después
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS ofertas_registro (
            id               SERIAL PRIMARY KEY,
            conversacion_id  INTEGER NOT NULL REFERENCES bot_conversaciones(id) ON DELETE CASCADE,
            oferta_bot_id    INTEGER NOT NULL REFERENCES ofertas_bot(id) ON DELETE CASCADE,
            mensaje_enviado  BOOLEAN NOT NULL DEFAULT TRUE,
            enviado_por      INTEGER REFERENCES usuarios(id),
            enviado_en       TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_ofertas_registro_conv ON ofertas_registro(conversacion_id)"))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_ofertas_registro_oferta ON ofertas_registro(oferta_bot_id)"))

    # Respuestas rápidas configurables (panel de atención)
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS respuestas_rapidas (
            id          SERIAL PRIMARY KEY,
            emoji       VARCHAR(8),
            etiqueta    VARCHAR(40)  NOT NULL,
            texto       TEXT         NOT NULL,
            orden       INTEGER      DEFAULT 0,
            activa      BOOLEAN      NOT NULL DEFAULT TRUE
        )
    """))
    # Seed idempotente con los 8 chips que ya existían hardcodeados
    _seed_rr = [
        ('📍', 'Domicilio', '¿Me confirmás la dirección para el envío? 📍'),
        ('💊', 'Producto', '¿Me confirmás qué producto necesitás y la cantidad?'),
        ('🏥', 'Obra social', '¿Tenés obra social? ¿Cuál?'),
        ('✅', 'Confirmado', '¡Perfecto! En unos minutos te confirmamos precio y disponibilidad 🙂'),
        ('🛵', '¿Envío o retiro?', '¿Querés que te lo enviemos a domicilio o pasás a buscarlo?'),
        ('📋', 'Receta', '¿Tenés receta médica? 📋'),
        ('⏳', 'Demora', 'Disculpá la demora, te atendemos en un momento 🙏'),
        ('👤', 'Nombre', '¿Cuál es tu nombre completo?'),
    ]
    count_rr = conn.execute(text("SELECT COUNT(*) FROM respuestas_rapidas")).scalar() or 0
    if count_rr == 0:
        for _i, (_emoji, _etq, _txt) in enumerate(_seed_rr):
            conn.execute(text(
                "INSERT INTO respuestas_rapidas (emoji, etiqueta, texto, orden, activa) "
                "VALUES (:e, :et, :tx, :o, true)"
            ), {'e': _emoji, 'et': _etq, 'tx': _txt, 'o': _i + 1})

    # Registro anti-spam de informes proactivos (Telegram → dueño)
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS informe_enviado (
            id              SERIAL PRIMARY KEY,
            tipo            VARCHAR(40) NOT NULL,
            conversacion_id INTEGER NOT NULL,
            enviado_en      TIMESTAMP,
            CONSTRAINT uq_informe_conv UNIQUE (tipo, conversacion_id)
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_informe_conv ON informe_enviado(conversacion_id)"))

    # Domicilios estructurados (migración SQLite)
    try:
        existing_dc = {row[1] for row in conn.execute(text("PRAGMA table_info(domicilios_cliente)"))}
        for col, sql_type in [
            ('piso', 'VARCHAR(20)'),
            ('depto', 'VARCHAR(20)'),
            ('referencia', 'VARCHAR(200)'),
            ('geo_actualizado_en', 'TIMESTAMP'),
            ('cliente_id', 'INTEGER'),
        ]:
            if col not in existing_dc:
                conn.execute(text(f"ALTER TABLE domicilios_cliente ADD COLUMN {col} {sql_type}"))
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
    if 'descuento_con_transfer' not in existing:
        conn.execute(text("ALTER TABLE proveedores ADD COLUMN descuento_con_transfer DECIMAL(5, 2)"))
    if 'descuento_sin_transfer' not in existing:
        conn.execute(text("ALTER TABLE proveedores ADD COLUMN descuento_sin_transfer DECIMAL(5, 2)"))

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
        # Fase 2 compra rápida (2026-04-27)
        if existing_om:
            for col, sql_type in [
                ('drogueria_id',   'INTEGER REFERENCES proveedores(id)'),
                ('vigencia_desde', 'DATE'),
                ('vigencia_hasta', 'DATE'),
                ('observacion',    'VARCHAR(200)'),
                ('activo',         'BOOLEAN NOT NULL DEFAULT 1'),
            ]:
                if col not in existing_om:
                    conn.execute(text(f'ALTER TABLE ofertas_minimo ADD COLUMN {col} {sql_type}'))
    except Exception as e:
        # Migración SQLite con varios pasos. Si falla algo (ej. ya estaba
        # aplicado en formato distinto), seguimos. Loggeamos para detectar
        # divergencias entre dev y prod.
        import logging
        logging.getLogger(__name__).warning(
            'Migración SQLite ofertas_minimo falló (puede ser idempotente): %s', e)

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
