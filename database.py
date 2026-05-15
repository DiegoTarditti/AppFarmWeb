import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    DECIMAL,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)

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
    # Backups automáticos (ejecutados por DockerPanel host)
    backup_ruta_remota        = Column(String(500), nullable=True)   # UNC tipo \\server-1\backups\farmacia
    backup_hora               = Column(Integer, nullable=False, default=17)  # 0-23
    backup_diarios_max        = Column(Integer, nullable=False, default=7)
    backup_semanales_max      = Column(Integer, nullable=False, default=0)
    backup_quincenales_max    = Column(Integer, nullable=False, default=1)
    backup_mensuales_max      = Column(Integer, nullable=False, default=0)
    # Status del último backup (lo escribe DockerPanel via API)
    backup_ultimo_status      = Column(String(10), nullable=True)    # 'OK' / 'FAIL' / NULL
    backup_ultimo_corrida_en  = Column(DateTime, nullable=True)
    backup_ultimo_error       = Column(String(500), nullable=True)
    backup_ultimo_tamano_mb   = Column(DECIMAL(10, 2), nullable=True)
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
    tipo_operacion                 = Column(String(2), nullable=True, index=True)  # 'V'=venta, 'D'=devol., 'NC'=nota crédito
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
    count_total = Column(Integer, nullable=False, default=0)
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
    estado = Column(String(20), nullable=False, default='pendiente')
    solicitado_en = Column(DateTime, default=now_ar, nullable=False)
    solicitado_por = Column(String(80), nullable=True)
    tomado_en = Column(DateTime, nullable=True)
    ejecutado_en = Column(DateTime, nullable=True)
    duracion_ms = Column(Integer, nullable=True)
    resultado = Column(Text, nullable=True)
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
    descripcion_supplier = Column(String(300), nullable=False, index=True)
    supplier_id = Column(Integer, nullable=True, index=True)         # laboratorio/proveedor (sin FK estricto: puede venir de Producto.laboratorio_id, Provider.id, etc.)
    supplier_nombre = Column(String(200), nullable=True)
    archivo_origen = Column(String(60), nullable=True)               # 'ofertas_import' | 'modulos_import' | 'factura' | etc.
    fecha_creacion = Column(DateTime, default=now_ar, nullable=False, index=True)
    veces_aparecido = Column(Integer, nullable=False, default=1)
    score_top_candidato = Column(Float, nullable=True)               # 0.0-1.0; None si bulk no devolvió ningún candidato
    top_candidatos_json = Column(Text, nullable=True)                # snapshot JSON: [{producto_id, descripcion, score}, ...]
    oferta_data_json = Column(Text, nullable=True)                   # snapshot JSON de la oferta original que disparó el queue:
                                                                      # {descuento_psl, unidades_minima, plazo_pago, rentabilidad,
                                                                      #  vigencia_hasta, drogueria_id, observacion, archivo_origen,
                                                                      #  laboratorio_id}. Al resolver, se aplica a OfertaMinimo del
                                                                      # producto creado/vinculado para cerrar el loop import → queue → oferta.
    estado = Column(String(20), nullable=False, default='pendiente', index=True)  # pendiente / agregado / vinculado / descartado
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
    drogueria_id    = Column(Integer, ForeignKey('proveedores.id'), nullable=True, index=True)
    # Vigencia: el optimizador filtra automáticamente los vencidos.
    vigencia_desde  = Column(Date, nullable=True)
    vigencia_hasta  = Column(Date, nullable=True, index=True)
    # Categoría/observación libre (ej "TR Lanzamiento", "TRs OTC", "TR Excepcional").
    observacion     = Column(String(200), nullable=True)
    # Activación manual (para "pausar" sin borrar)
    activo          = Column(Boolean, nullable=False, default=True)
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
    compra_minima_pesos     = Column(DECIMAL(14, 2), nullable=True)
    # Descuento base de la droguería (independiente del lab). NULL = sin acuerdo cargado.
    descuento_con_transfer  = Column(DECIMAL(5, 2), nullable=True)
    descuento_sin_transfer  = Column(DECIMAL(5, 2), nullable=True)
    matriz_visible = Column(Boolean, nullable=False, default=True)
    matriz_orden   = Column(Integer, nullable=True)
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
    proveedor_id  = Column(Integer, ForeignKey('proveedores.id', ondelete='CASCADE'),
                           nullable=False, index=True)
    dia_semana    = Column(Integer, nullable=False)   # 0-6
    hora          = Column(String(5), nullable=False)  # 'HH:MM' formato 24h, simple
    activo        = Column(Boolean, nullable=False, default=True)
    creado_en     = Column(DateTime, default=now_ar)
    proveedor     = relationship('Provider')
    __table_args__ = (
        UniqueConstraint('proveedor_id', 'dia_semana', 'hora', name='uq_horario_prov_dia_hora'),
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
    partner_tipo        = Column(String(12), nullable=False, default='drogueria',
                                  index=True)
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
    )


class PedidoBorrador(Base):
    """Borrador de pedido en armado por usuario y droguería.

    Persiste el "A pedir" durante la sesión de armado para que sobreviva refreshes
    y cambios de droguería sin perder el trabajo. Una fila por (drog × producto).
    """
    __tablename__ = 'pedido_borrador'
    id              = Column(Integer, primary_key=True)
    drogueria_id    = Column(Integer, ForeignKey('proveedores.id', ondelete='CASCADE'),
                              nullable=False, index=True)
    producto_id     = Column(Integer, ForeignKey('productos.id', ondelete='CASCADE'),
                              nullable=True, index=True)
    observer_id     = Column(Integer, nullable=True, index=True)  # ObsProducto.observer_id si aún no hay Producto local
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
    laboratorio_id = Column(Integer, ForeignKey('laboratorios.id', ondelete='CASCADE'),
                            nullable=True, index=True)
    drogueria_id = Column(Integer, ForeignKey('proveedores.id', ondelete='CASCADE'),
                          nullable=True, index=True)
    descripcion_proveedor = Column(String(200), nullable=True)
    descripcion_proveedor_norm = Column(String(200), nullable=True, index=True)
    codigo_proveedor = Column(String(50), nullable=True, index=True)
    producto_id = Column(Integer, ForeignKey('productos.id', ondelete='SET NULL'),
                         nullable=True, index=True)
    creado_en = Column(DateTime, default=now_ar)
    laboratorio = relationship('Laboratorio')
    producto = relationship('Producto')
    __table_args__ = (
        # UC legacy (solo aplica cuando lab está seteado). En drogueria_id,
        # se chequea uniqueness vía helper guardar_equivalencia (SELECT-then-
        # insert) — los partial unique indexes se crean inline en init_db.
        UniqueConstraint('laboratorio_id', 'descripcion_proveedor_norm',
                         name='uq_equiv_lab_desc'),
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
    fuente_creacion = Column(String(30), nullable=True)   # 'oferta_import' / 'manual' / NULL
    # Compra rápida v2: exclusiones manuales del armado de pedido.
    excluido_armado_actual = Column(Boolean, nullable=False, default=False)
    no_pedir               = Column(Boolean, nullable=False, default=False)
    # Cronograma de pedidos: cantidad fija a pedir cuando se llega al punto
    # de pedido. NULL = cálculo dinámico (default). Útil para productos donde
    # el operador ya sabe la dosis óptima de reposición.
    cantidad_reposicion_fija = Column(Integer, nullable=True)
    codigos_barra = relationship('ProductoCodigoBarra',
                                 back_populates='producto',
                                 cascade='all, delete-orphan',
                                 order_by='desc(ProductoCodigoBarra.es_principal), ProductoCodigoBarra.id')


class ProductoCodigoBarra(Base):
    """EANs de un producto en una relación 1-a-N (reemplazo gradual de alt1/2/3).

    Uno de los registros tiene `es_principal=True`; ese se sincroniza con
    `Producto.codigo_barra` para no romper código viejo. El resto son
    alternativos (sin límite a diferencia de los 3 slots fijos).

    Trazabilidad: cada EAN sabe de dónde vino (`fuente` + `factura_id`).
    """
    __tablename__ = 'producto_codigos_barra'
    id           = Column(Integer, primary_key=True)
    producto_id  = Column(Integer, ForeignKey('productos.id', ondelete='CASCADE'), nullable=False, index=True)
    codigo_barra = Column(String(20), nullable=False, index=True)
    es_principal = Column(Boolean, nullable=False, default=False)
    fuente       = Column(String(20), nullable=False, default='manual')  # manual / factura / observer / import / cruce / legacy_alt
    factura_id   = Column(Integer, ForeignKey('facturas.id', ondelete='SET NULL'), nullable=True)
    creado_en    = Column(DateTime, default=now_ar)
    producto = relationship('Producto', back_populates='codigos_barra')


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
    producto_id        = Column(Integer, ForeignKey('productos.id', ondelete='CASCADE'), primary_key=True)
    monodroga_norm     = Column(String(500), nullable=True, index=True)  # lower-case sin acentos para match
    concentracion_mg   = Column(DECIMAL(12, 4), nullable=True, index=True)  # ej 500, 250.5, 0.05
    concentracion_unidad = Column(String(15), nullable=True)             # MG, MCG, G, UI, %, MG/ML, MG/5ML
    forma_farma        = Column(String(10), nullable=True, index=True)   # CPR, CAP, SUSP, SUP, AMP, JER, CRE, POM, GTS, SOL, INH, OVU, PCH, POL
    cantidad_envase    = Column(DECIMAL(10, 3), nullable=True)           # 16, 100, 10
    via_admin          = Column(String(10), nullable=True)               # ORAL, IV, IM, SC, TOP, OFT, NAS, OTI, INH, RECT, VAG
    fuente             = Column(String(15), nullable=False, default='regex')  # observer / regex / llm / manual / mixto
    confianza          = Column(String(8),  nullable=False, default='MEDIA')  # ALTA / MEDIA / BAJA
    raw_descripcion    = Column(String(300), nullable=True)              # snapshot de la descripción cuando se extrajo (detección de drift)
    extraido_en        = Column(DateTime, default=now_ar)
    producto = relationship('Producto', backref='atributos')

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
    farmacia_id = Column(Integer, ForeignKey('farmacias.id'),
                          nullable=False, default=1, index=True)
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
    mostrar_hasta = Column(Date, nullable=True, index=True)
    items = relationship('PedidoItem', back_populates='pedido', cascade='all, delete-orphan')
    analisis_sesion = relationship('AnalisisSesion')


class PedidoItem(Base):
    __tablename__ = 'pedido_items'
    id = Column(Integer, primary_key=True)
    pedido_id = Column(Integer, ForeignKey('pedidos.id'), nullable=False)
    # Multi-tenant: denormalizado desde Pedido para evitar joins en queries de
    # agregación cross-farmacia (ej. "qué se pidió este mes en F1+Pieri por
    # producto"). Siempre debe coincidir con pedido.farmacia_id.
    farmacia_id = Column(Integer, ForeignKey('farmacias.id'),
                          nullable=False, default=1, index=True)
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
    # Multi-tenant: a qué farmacia pertenece este proceso de compra. Default=1.
    farmacia_id = Column(Integer, ForeignKey('farmacias.id'),
                          nullable=False, default=1, index=True)
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
    aprendido_de    = Column(Integer, ForeignKey('modulos.id', ondelete='SET NULL'),
                             nullable=True)   # módulo donde se aprendió por primera vez
    creado_en       = Column(DateTime, default=now_ar)
    actualizado_en  = Column(DateTime, default=now_ar, onupdate=now_ar)


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


class DestinoDevolucion(Base):
    """A quién/qué área se le devuelve (vendedor original, cobranzas,
    auditoría, etc.). ABM desde la app."""
    __tablename__ = 'destino_devolucion'
    id = Column(Integer, primary_key=True)
    nombre = Column(String(150), nullable=False, unique=True)
    activo = Column(Boolean, nullable=False, default=True)
    creado_en = Column(DateTime, default=now_ar)


class DevolucionReceta(Base):
    """Registro de una receta devuelta. Apunta a una operación de venta en
    ObServer vía `id_operacion_observer` pero los datos clave se snapshot-ean
    al momento de registrar para que el reporte sobreviva cambios en ObServer."""
    __tablename__ = 'devolucion_receta'
    id = Column(Integer, primary_key=True)
    nro_presentacion = Column(String(50), nullable=True, index=True)
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
    destino_id = Column(Integer, ForeignKey('destino_devolucion.id'), nullable=True)  # legacy
    # Destino = vendedor de ObServer (a quién se devuelve la receta para corregir)
    destino_vendedor_observer_id = Column(String(36), nullable=True)
    destino_vendedor_nombre = Column(String(100), nullable=True)
    observaciones = Column(Text, nullable=True)
    creado_en = Column(DateTime, default=now_ar, index=True)
    creado_por = Column(String(100), nullable=True)            # email del user
    # Cierre del ciclo
    estado = Column(String(20), nullable=False, default='pendiente', index=True)
                                                               # pendiente | resuelta | descartada
    nota_cierre = Column(Text, nullable=True)
    cerrada_en = Column(DateTime, nullable=True)
    cerrada_por = Column(String(100), nullable=True)

    motivo = relationship('MotivoDevolucion')
    destino = relationship('DestinoDevolucion')

    __table_args__ = (
        UniqueConstraint('id_operacion_observer', 'creado_en',
                         name='uq_devolucion_op_creado'),
    )


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
                        'plantillas', 'producto_precios_hist', 'producto_atributos',
                        'producto_codigos_barra',
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
                        'descuentos_base', 'obs_codigos_barras',
                        'proveedor_horarios_reparto', 'pedido_borrador',
                        'laboratorio_drogueria',
                        'pedido_emitido', 'pedido_emitido_item',
                        'equivalencias_proveedor',
                        'pack_equivalencias', 'cliente_os_inferida',
                        'panel_comandos', 'farmacias', 'usuario_farmacias',
                        'alarmas_notificadas', 'sync_lock',
                        'productos_pendientes_revision',
                        'motivo_devolucion', 'destino_devolucion',
                        'devolucion_receta',
                        'proveedor_cronograma')
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
            except Exception:
                pass
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
                for ddl in (f'DROP TABLE IF EXISTS "{zombie}" CASCADE',
                            f'DROP TYPE  IF EXISTS "{zombie}" CASCADE',
                            f'DROP SEQUENCE IF EXISTS "{zombie}_id_seq" CASCADE',
                            f'DROP SEQUENCE IF EXISTS "{zombie}" CASCADE'):
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
    # Provider: mínimo de compra (puede no estar en deploys viejos)
    conn.execute(text("ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS compra_minima_pesos DECIMAL(14, 2)"))
    conn.execute(text("ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS descuento_con_transfer DECIMAL(5, 2)"))
    conn.execute(text("ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS descuento_sin_transfer DECIMAL(5, 2)"))
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
    # Migración: copiar monodroga_display → productos.monodroga donde esté vacío,
    # luego dropear la columna (eliminada como dup de Producto.monodroga).
    try:
        conn.execute(text("""
            UPDATE productos p
               SET monodroga = pa.monodroga_display
              FROM producto_atributos pa
             WHERE pa.producto_id = p.id
               AND (p.monodroga IS NULL OR p.monodroga = '')
               AND pa.monodroga_display IS NOT NULL
               AND pa.monodroga_display != ''
        """))
    except Exception:
        pass
    try:
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
