# Mapa de AppFarmWeb

> ⚠️ **GENERADO — no editar a mano.** Se regenera con `python scripts/mapa.py`.
> Todo acá sale del código, así que no puede quedar desactualizado sin que
> se note. Lo que el código NO dice (decisiones, trampas, por qué) va en
> [CLAUDE.md](../CLAUDE.md), no acá.

Generado: 2026-07-23 21:56 · rama `feat/admin-actualizar-endpoint-v2` · commit `c9735a4`

**773 rutas** en 74 archivos · **123 modelos** · **21 syncs** · **28 services** · **11 parsers**

## Syncs de ObServer (`observer_source.py`)

⭐ = premium (lee el schema `Gestion` → **requiere usuario SA**).

| Función | Lee de | Línea |
|---|---|---|
| `sync_laboratorios` | `DW.Laboratorios` | [356](../observer_source.py#L356) |
| `sync_rubros` | `DW.Rubros` | [382](../observer_source.py#L382) |
| `sync_subrubros` | `DW.Subrubros` | [410](../observer_source.py#L410) |
| `sync_nombres_drogas` | `DW.NombresDrogas` | [440](../observer_source.py#L440) |
| `sync_productos` | `DW.Productos` | [468](../observer_source.py#L468) |
| `sync_precios_vigentes` ⭐ | `Gestion.ProductosPreciosVigentes` | [517](../observer_source.py#L517) |
| `sync_condiciones_comerciales` ⭐ | `Gestion.CondicionesComerciales` | [597](../observer_source.py#L597) |
| `sync_fraccionado_master` | — | [717](../observer_source.py#L717) |
| `sync_colegios_medicos` | `DW.ColegiosMedicos` | [790](../observer_source.py#L790) |
| `sync_medicos` | `DW.Medicos` | [818](../observer_source.py#L818) |
| `sync_medicos_matriculas` | `DW.MedicosMatriculas` | [848](../observer_source.py#L848) |
| `sync_ventas_detalle` | `DW.ProductosVendidos` | [887](../observer_source.py#L887) |
| `sync_operadores` | `DW.OperadoresVenta` | [1069](../observer_source.py#L1069) |
| `sync_grupos_clientes` | `DW.GruposClientes` | [1100](../observer_source.py#L1100) |
| `sync_categorias_clientes` | `DW.CategoriasClientes` | [1126](../observer_source.py#L1126) |
| `sync_obras_sociales` | `DW.ObrasSociales` | [1152](../observer_source.py#L1152) |
| `sync_convenios` | `DW.Convenios` | [1178](../observer_source.py#L1178) |
| `sync_planes` | `DW.Planes` | [1210](../observer_source.py#L1210) |
| `sync_clientes` | `DW.Clientes` | [1243](../observer_source.py#L1243) |
| `sync_stock` | `DW.StockFarmaciasProductos` | [1303](../observer_source.py#L1303) |
| `sync_ventas_mensuales` | `DW.ProductosVendidos` | [1354](../observer_source.py#L1354) |

## Modelos (`database.py`)

| Tabla | Clase | Línea |
|---|---|---|
| `alarmas_notificadas` | `AlarmaNotificada` | [959](../database.py#L959) |
| `analisis_ia_cache` | `AnalisisIaCache` | [2261](../database.py#L2261) |
| `analisis_sesiones` | `AnalisisSesion` | [2388](../database.py#L2388) |
| `api_keys` | `ApiKey` | [3047](../database.py#L3047) |
| `archivos_compartidos` | `ArchivoCompartido` | [1260](../database.py#L1260) |
| `backup_log` | `BackupLog` | [1141](../database.py#L1141) |
| `barcode_mappings` | `BarcodeMapping` | [1895](../database.py#L1895) |
| `bot_conversaciones` | `BotConversacion` | [2854](../database.py#L2854) |
| `bot_interacciones` | `BotInteraccion` | [3022](../database.py#L3022) |
| `bot_mensajes` | `BotMensaje` | [2916](../database.py#L2916) |
| `cadencia_lab_snapshot` | `CadenciaLabSnapshot` | [2217](../database.py#L2217) |
| `cadetes` | `Cadete` | [771](../database.py#L771) |
| `ciudades` | `Ciudad` | [581](../database.py#L581) |
| `cliente_os_confirmada` | `ClienteOsConfirmada` | [439](../database.py#L439) |
| `cliente_os_inferida` | `ClienteOsInferida` | [449](../database.py#L449) |
| `clientes` | `Cliente` | [487](../database.py#L487) |
| `clientes_locales` | `ClienteLocal` | [523](../database.py#L523) |
| `compartido_importado` | `CompartidoImportado` | [1280](../database.py#L1280) |
| `configuracion` | `Config` | [34](../database.py#L34) |
| `cron_log` | `CronLog` | [941](../database.py#L941) |
| `cuentas_pago` | `CuentaPago` | [636](../database.py#L636) |
| `descuentos_base` | `DescuentoBase` | [1352](../database.py#L1352) |
| `devolucion_receta` | `DevolucionReceta` | [2604](../database.py#L2604) |
| `documentos_pendientes` | `DocumentoPendiente` | [2181](../database.py#L2181) |
| `domicilios_cliente` | `DomicilioCliente` | [704](../database.py#L704) |
| `envio_config` | `EnvioConfig` | [733](../database.py#L733) |
| `envio_tramos` | `EnvioTramo` | [677](../database.py#L677) |
| `envio_zonas` | `EnvioZona` | [688](../database.py#L688) |
| `equivalencias_proveedor` | `EquivalenciaProveedor` | [1842](../database.py#L1842) |
| `erp_stock` | `ErpStock` | [1765](../database.py#L1765) |
| `estacionalidad_escenarios` | `EstacionalidadEscenario` | [2673](../database.py#L2673) |
| `estacionalidad_productos` | `EstacionalidadProducto` | [2705](../database.py#L2705) |
| `eventos_sla` | `EventoSLA` | [2979](../database.py#L2979) |
| `export_templates` | `ExportTemplate` | [1181](../database.py#L1181) |
| `factura_faltante` | `FacturaFaltante` | [1744](../database.py#L1744) |
| `factura_items` | `InvoiceItem` | [1723](../database.py#L1723) |
| `facturas` | `Invoice` | [1669](../database.py#L1669) |
| `farmacias` | `Farmacia` | [1085](../database.py#L1085) |
| `formas_pago` | `FormaPago` | [627](../database.py#L627) |
| `home_card_clicks` | `HomeCardClick` | [2311](../database.py#L2311) |
| `informe_enviado` | `InformeEnviado` | [2965](../database.py#L2965) |
| `invoice_batches` | `InvoiceBatch` | [1659](../database.py#L1659) |
| `kellerhoff_catalogo` | `KellerhoffCatalogo` | [1588](../database.py#L1588) |
| `kellerhoff_equivalencia` | `KellerhoffEquivalencia` | [1612](../database.py#L1612) |
| `laboratorio_drogueria` | `LaboratorioDrogueria` | [1452](../database.py#L1452) |
| `laboratorios` | `Laboratorio` | [91](../database.py#L91) |
| `modulo_packs` | `ModuloPack` | [2050](../database.py#L2050) |
| `modulos` | `Modulo` | [2037](../database.py#L2037) |
| `motivo_devolucion` | `MotivoDevolucion` | [2452](../database.py#L2452) |
| `mv_refresh_log` | `MvRefreshLog` | [1127](../database.py#L1127) |
| `obs_categorias_clientes` | `ObsCategoriaCliente` | [341](../database.py#L341) |
| `obs_clientes` | `ObsCliente` | [470](../database.py#L470) |
| `obs_codigos_barras` | `ObsCodigoBarras` | [210](../database.py#L210) |
| `obs_colegios_medicos` | `ObsColegioMedico` | [228](../database.py#L228) |
| `obs_condiciones_comerciales` | `ObsCondicionComercial` | [376](../database.py#L376) |
| `obs_convenios` | `ObsConvenio` | [357](../database.py#L357) |
| `obs_grupos_clientes` | `ObsGrupoCliente` | [333](../database.py#L333) |
| `obs_laboratorios` | `ObsLaboratorio` | [112](../database.py#L112) |
| `obs_medicos` | `ObsMedico` | [238](../database.py#L238) |
| `obs_medicos_matriculas` | `ObsMedicoMatricula` | [248](../database.py#L248) |
| `obs_nombres_drogas` | `ObsNombreDroga` | [135](../database.py#L135) |
| `obs_obras_sociales` | `ObsObraSocial` | [349](../database.py#L349) |
| `obs_operadores` | `ObsOperador` | [322](../database.py#L322) |
| `obs_planes` | `ObsPlan` | [366](../database.py#L366) |
| `obs_productos` | `ObsProducto` | [142](../database.py#L142) |
| `obs_rubros` | `ObsRubro` | [120](../database.py#L120) |
| `obs_stock` | `ObsStock` | [181](../database.py#L181) |
| `obs_stock_snapshot_diario` | `ObsStockSnapshotDiario` | [1162](../database.py#L1162) |
| `obs_subrubros` | `ObsSubrubro` | [127](../database.py#L127) |
| `obs_sync_log` | `ObsSyncLog` | [926](../database.py#L926) |
| `obs_ventas_detalle` | `ObsVentaDetalle` | [258](../database.py#L258) |
| `obs_ventas_mensuales` | `ObsVentaMensual` | [193](../database.py#L193) |
| `ofertas_bot` | `OfertaBot` | [2929](../database.py#L2929) |
| `ofertas_minimo` | `OfertaMinimo` | [1188](../database.py#L1188) |
| `ofertas_registro` | `OfertaRegistro` | [2941](../database.py#L2941) |
| `pack_equivalencias` | `PackEquivalencia` | [2325](../database.py#L2325) |
| `pago_aplicaciones` | `PagoAplicacion` | [667](../database.py#L667) |
| `pagos` | `Pago` | [650](../database.py#L650) |
| `pagos_ajustes_cc` | `PagoAjusteCC` | [2166](../database.py#L2166) |
| `panel_comandos` | `PanelComando` | [994](../database.py#L994) |
| `panel_heartbeat` | `PanelHeartbeat` | [1017](../database.py#L1017) |
| `parser_ofertas_lab` | `ParserOfertasLab` | [1237](../database.py#L1237) |
| `pedido_borrador` | `PedidoBorrador` | [1628](../database.py#L1628) |
| `pedido_emitido` | `PedidoEmitido` | [1390](../database.py#L1390) |
| `pedido_emitido_item` | `PedidoEmitidoItem` | [1421](../database.py#L1421) |
| `pedido_items` | `PedidoItem` | [2111](../database.py#L2111) |
| `pedido_obs_presets` | `PedidoObsPreset` | [759](../database.py#L759) |
| `pedidos` | `Pedido` | [2065](../database.py#L2065) |
| `pedidos_reparto` | `PedidoReparto` | [813](../database.py#L813) |
| `plantilla_campos` | `PlantillaCampo` | [2417](../database.py#L2417) |
| `plantillas` | `Plantilla` | [2432](../database.py#L2432) |
| `plantillas_exportacion` | `PlantillaExportacion` | [2403](../database.py#L2403) |
| `procesos_compra` | `ProcesoCompra` | [2134](../database.py#L2134) |
| `product_analytics` | `ProductAnalytics` | [2196](../database.py#L2196) |
| `producto_atributos` | `ProductoAtributo` | [1990](../database.py#L1990) |
| `producto_codigos_barra` | `ProductoCodigoBarra` | [1964](../database.py#L1964) |
| `producto_flags` | `ProductoFlag` | [1567](../database.py#L1567) |
| `producto_precios_hist` | `ProductoPrecioHist` | [2363](../database.py#L2363) |
| `productos` | `Producto` | [1906](../database.py#L1906) |
| `productos_pendientes_revision` | `ProductoPendienteRevision` | [1029](../database.py#L1029) |
| `proveedor_cronograma` | `ProveedorCronograma` | [1497](../database.py#L1497) |
| `proveedor_horarios_reparto` | `ProveedorHorarioReparto` | [1473](../database.py#L1473) |
| `proveedores` | `Provider` | [1319](../database.py#L1319) |
| `reclamo_items` | `ClaimItem` | [1822](../database.py#L1822) |
| `reclamos` | `Claim` | [1804](../database.py#L1804) |
| `rendicion_grupo` | `RendicionGrupo` | [2546](../database.py#L2546) |
| `rendicion_grupo_os` | `RendicionGrupoOS` | [2567](../database.py#L2567) |
| `rendicion_lote` | `RendicionLote` | [2470](../database.py#L2470) |
| `respuestas_rapidas` | `RespuestaRapida` | [2954](../database.py#L2954) |
| `rol_filtro_obra_social` | `RolFiltroObraSocial` | [2582](../database.py#L2582) |
| `rutas_reparto` | `RutaReparto` | [795](../database.py#L795) |
| `stock_differences` | `StockDifference` | [1787](../database.py#L1787) |
| `sucursales` | `Sucursal` | [1300](../database.py#L1300) |
| `sync_lock` | `SyncLock` | [975](../database.py#L975) |
| `ticket_items` | `TicketItem` | [615](../database.py#L615) |
| `tickets_caja` | `TicketCaja` | [591](../database.py#L591) |
| `tipo_pedido_config` | `TipoPedidoConfig` | [1543](../database.py#L1543) |
| `usuario_farmacias` | `UsuarioFarmacia` | [1109](../database.py#L1109) |
| `usuarios` | `Usuario` | [2274](../database.py#L2274) |
| `usuarios_pedidos` | `UsuarioPedido` | [1382](../database.py#L1382) |
| `vendedor_bookmark` | `VendedorBookmark` | [2528](../database.py#L2528) |
| `web_producto_imagen` | `WebProductoImagen` | [3101](../database.py#L3101) |
| `web_rubros_publicados` | `WebRubroPublicado` | [3079](../database.py#L3079) |

## Rutas

### `routes/admin.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/admin` | GET | [`admin_index`](../routes/admin.py#L47) |
| `/admin/alarmas` | GET | [`admin_alarmas`](../routes/admin.py#L257) |
| `/admin/cleanup-inactivos` | GET/POST | [`admin_cleanup_inactivos`](../routes/admin.py#L737) |
| `/admin/cron-log` | GET | [`admin_cron_log`](../routes/admin.py#L403) |
| `/admin/health` | GET | [`admin_health`](../routes/admin.py#L65) |
| `/admin/panel` | GET | [`admin_panel`](../routes/admin.py#L819) |
| `/admin/panel/comandos` | POST | [`admin_panel_encolar`](../routes/admin.py#L831) |
| `/admin/panel/comandos/recientes` | GET | [`admin_panel_recientes`](../routes/admin.py#L858) |
| `/admin/reset-datos` | GET/POST | [`admin_reset_datos`](../routes/admin.py#L763) |
| `/admin/seed-proveedores` | GET/POST | [`admin_seed_proveedores`](../routes/admin.py#L555) |
| `/api/admin/actualizar` | POST | [`api_admin_actualizar`](../routes/admin.py#L970) |
| `/api/admin/alarmas` | GET | [`api_admin_alarmas`](../routes/admin.py#L389) |
| `/api/admin/alarmas/probar-telegram` | POST | [`api_alarmas_probar_telegram`](../routes/admin.py#L501) |
| `/api/admin/migrar/backfill-codigos-barra` | POST | [`api_migrar_backfill_codigos_barra`](../routes/admin.py#L571) |
| `/api/admin/migrar/bridge-productos-observer` | POST | [`api_migrar_bridge_productos_observer`](../routes/admin.py#L614) |
| `/api/admin/popular-productos-desde-obs` | POST | [`api_admin_popular_productos_desde_obs`](../routes/admin.py#L594) |
| `/api/cron-log` | POST | [`api_cron_log_externo`](../routes/admin.py#L459) |
| `/api/cron-log/purgar` | POST | [`api_cron_log_purgar`](../routes/admin.py#L487) |
| `/api/cron/limpiar-home-card-clicks` | POST | [`api_cron_limpiar_home_card_clicks`](../routes/admin.py#L693) |
| `/api/cron/notificar-alarmas` | POST | [`api_cron_notificar_alarmas`](../routes/admin.py#L515) |
| `/api/cron/recalcular-os-clientes` | POST | [`api_cron_recalcular_os_clientes`](../routes/admin.py#L659) |
| `/api/dockerpanel-info` | GET | [`api_dockerpanel_info`](../routes/admin.py#L753) |
| `/api/obs/recalcular-os-clientes` | POST | [`api_recalcular_os_clientes`](../routes/admin.py#L639) |
| `/api/panel/comandos/<int:cmd_id>/resultado` | POST | [`api_panel_resultado`](../routes/admin.py#L944) |
| `/api/panel/comandos/proximo` | GET | [`api_panel_proximo`](../routes/admin.py#L890) |
| `/api/pedidos-nuevo/scope` | GET | [`api_pedidos_nuevo_scope`](../routes/admin.py#L290) |
| `/pedidos-nuevo` | GET | [`pedidos_nuevo`](../routes/admin.py#L272) |

### `routes/api_keys_admin.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/admin/api-keys` | GET | [`admin_api_keys`](../routes/api_keys_admin.py#L36) |
| `/admin/api-keys/<int:kid>/delete` | POST | [`admin_api_keys_delete`](../routes/api_keys_admin.py#L109) |
| `/admin/api-keys/<int:kid>/toggle` | POST | [`admin_api_keys_toggle`](../routes/api_keys_admin.py#L96) |
| `/admin/api-keys/crear` | POST | [`admin_api_keys_crear`](../routes/api_keys_admin.py#L63) |

### `routes/api_publica.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/publica/obras-sociales` | GET | [`api_publica_obras_sociales`](../routes/api_publica.py#L178) |
| `/api/publica/obras-sociales/<int:observer_id>/planes` | GET | [`api_publica_planes`](../routes/api_publica.py#L193) |
| `/api/publica/paciente/<int:observer_id>` | GET | [`api_publica_paciente`](../routes/api_publica.py#L227) |
| `/api/publica/paciente/<int:observer_id>/compras` | GET | [`api_publica_paciente_compras`](../routes/api_publica.py#L288) |
| `/api/publica/paciente/buscar` | GET | [`api_publica_paciente_buscar`](../routes/api_publica.py#L250) |
| `/api/publica/pami/afiliado-por-dni` | GET | [`api_publica_pami_afiliado_por_dni`](../routes/api_publica.py#L465) |
| `/api/publica/pami/afiliado/<numero>/cronicos-sugeridos` | GET | [`api_publica_pami_cronicos_sugeridos`](../routes/api_publica.py#L569) |
| `/api/publica/pami/afiliados-por-dnis` | POST | [`api_publica_pami_afiliados_por_dnis`](../routes/api_publica.py#L483) |
| `/api/publica/panel/comandos/<int:cmd_id>` | GET | [`api_publica_panel_comando_estado`](../routes/api_publica.py#L396) |
| `/api/publica/panel/stock` | POST | [`api_publica_panel_stock_encolar`](../routes/api_publica.py#L375) |
| `/api/publica/ping` | GET | [`api_publica_ping`](../routes/api_publica.py#L91) |
| `/api/publica/producto/<int:observer_id>` | GET | [`api_publica_producto`](../routes/api_publica.py#L97) |
| `/api/publica/producto/buscar` | GET | [`api_publica_producto_buscar`](../routes/api_publica.py#L122) |
| `/api/publica/stock/<int:observer_id>` | GET | [`api_publica_stock_snapshot`](../routes/api_publica.py#L347) |

### `routes/atencion.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/atencion` | GET | [`atencion_panel`](../routes/atencion.py#L37) |
| `/atencion/<int:conv_id>/cerrar` | POST | [`atencion_cerrar`](../routes/atencion.py#L822) |
| `/atencion/<int:conv_id>/cerrar-transaccion` | POST | [`atencion_cerrar_transaccion`](../routes/atencion.py#L523) |
| `/atencion/<int:conv_id>/crear-cliente` | POST | [`atencion_crear_cliente`](../routes/atencion.py#L213) |
| `/atencion/<int:conv_id>/desvincular-cliente` | POST | [`atencion_desvincular_cliente`](../routes/atencion.py#L264) |
| `/atencion/<int:conv_id>/devolver-cola` | POST | [`atencion_devolver_cola`](../routes/atencion.py#L178) |
| `/atencion/<int:conv_id>/domicilio` | POST | [`atencion_domicilio_crear`](../routes/atencion.py#L236) |
| `/atencion/<int:conv_id>/ficha-notas` | POST | [`atencion_guardar_notas`](../routes/atencion.py#L792) |
| `/atencion/<int:conv_id>/ofrecer` | POST | [`atencion_ofrecer`](../routes/atencion.py#L944) |
| `/atencion/<int:conv_id>/pago` | GET | [`atencion_pago_get`](../routes/atencion.py#L278) |
| `/atencion/<int:conv_id>/pago` | POST | [`atencion_pago_set`](../routes/atencion.py#L299) |
| `/atencion/<int:conv_id>/reset-testing` | POST | [`atencion_reset_testing`](../routes/atencion.py#L835) |
| `/atencion/<int:conv_id>/responder` | POST | [`atencion_responder`](../routes/atencion.py#L798) |
| `/atencion/<int:conv_id>/retirar` | POST | [`atencion_marcar_retirado`](../routes/atencion.py#L85) |
| `/atencion/<int:conv_id>/ticket` | POST | [`atencion_crear_ticket`](../routes/atencion.py#L331) |
| `/atencion/<int:conv_id>/tomar` | POST | [`atencion_tomar`](../routes/atencion.py#L150) |
| `/atencion/<int:conv_id>/transferir` | POST | [`atencion_transferir`](../routes/atencion.py#L166) |
| `/atencion/<int:conv_id>/vincular-cliente` | POST | [`atencion_vincular_cliente`](../routes/atencion.py#L204) |
| `/atencion/api/<int:conv_id>/cliente` | GET | [`atencion_cliente`](../routes/atencion.py#L185) |
| `/atencion/api/<int:conv_id>/domicilios` | GET | [`atencion_domicilios`](../routes/atencion.py#L231) |
| `/atencion/api/<int:conv_id>/mensajes` | GET | [`atencion_mensajes`](../routes/atencion.py#L99) |
| `/atencion/api/cerrados-hoy` | GET | [`atencion_cerrados_hoy`](../routes/atencion.py#L341) |
| `/atencion/api/ciudades` | GET | [`atencion_ciudades`](../routes/atencion.py#L224) |
| `/atencion/api/clientes/<int:observer_id>/obra-social` | GET/POST | [`atencion_os_cliente`](../routes/atencion.py#L998) |
| `/atencion/api/clientes/buscar` | GET | [`atencion_clientes_buscar`](../routes/atencion.py#L190) |
| `/atencion/api/conversaciones` | GET | [`atencion_conversaciones`](../routes/atencion.py#L79) |
| `/atencion/api/despachos-clinica` | GET | [`atencion_despachos_clinica`](../routes/atencion.py#L420) |
| `/atencion/api/despachos-clinica/paciente/<int:paciente_id>/observer` | POST | [`atencion_despachos_set_observer`](../routes/atencion.py#L491) |
| `/atencion/api/obras-sociales` | GET | [`atencion_obras_sociales_lista`](../routes/atencion.py#L966) |
| `/atencion/api/operadores` | GET | [`atencion_operadores`](../routes/atencion.py#L112) |
| `/atencion/api/pedido/<int:pedido_id>/hidratar` | GET | [`atencion_pedido_hidratar`](../routes/atencion.py#L372) |
| `/atencion/api/precio-os` | GET | [`atencion_precio_os`](../routes/atencion.py#L1030) |
| `/atencion/api/productos/buscar` | GET | [`atencion_productos_buscar`](../routes/atencion.py#L199) |
| `/atencion/api/respuestas-rapidas` | GET | [`atencion_respuestas_rapidas`](../routes/atencion.py#L842) |
| `/atencion/api/walkin` | POST | [`atencion_api_walkin_nueva`](../routes/atencion.py#L469) |
| `/atencion/ciudades` | POST | [`atencion_ciudad_crear`](../routes/atencion.py#L253) |
| `/atencion/ciudades/<int:ciudad_id>/delete` | POST | [`atencion_ciudad_eliminar`](../routes/atencion.py#L259) |
| `/atencion/domicilio/<int:dom_id>/delete` | POST | [`atencion_domicilio_eliminar`](../routes/atencion.py#L248) |
| `/atencion/estado` | POST | [`atencion_estado`](../routes/atencion.py#L145) |
| `/atencion/heartbeat` | POST | [`atencion_heartbeat`](../routes/atencion.py#L139) |
| `/atencion/operadores/crear` | POST | [`atencion_operador_crear`](../routes/atencion.py#L117) |
| `/atencion/respuestas-rapidas` | GET | [`atencion_rr_config`](../routes/atencion.py#L853) |
| `/atencion/respuestas-rapidas` | POST | [`atencion_rr_crear`](../routes/atencion.py#L867) |
| `/atencion/respuestas-rapidas/<int:rid>/delete` | POST | [`atencion_rr_delete`](../routes/atencion.py#L904) |
| `/atencion/respuestas-rapidas/<int:rid>/edit` | POST | [`atencion_rr_edit`](../routes/atencion.py#L888) |
| `/atencion/respuestas-rapidas/<int:rid>/toggle` | POST | [`atencion_rr_toggle`](../routes/atencion.py#L916) |
| `/atencion/respuestas-rapidas/reorder` | POST | [`atencion_rr_reorder`](../routes/atencion.py#L928) |

### `routes/auth_routes.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/cambiar-password` | GET/POST | [`auth_cambiar_password`](../routes/auth_routes.py#L97) |
| `/home` | GET | [`home_operador`](../routes/auth_routes.py#L55) |
| `/login` | GET/POST | [`auth_login`](../routes/auth_routes.py#L62) |
| `/logout` | GET | [`auth_logout`](../routes/auth_routes.py#L90) |
| `/usuarios` | GET | [`usuarios_list`](../routes/auth_routes.py#L124) |
| `/usuarios/<int:user_id>/delete` | POST | [`usuarios_delete`](../routes/auth_routes.py#L223) |
| `/usuarios/<int:user_id>/editar` | POST | [`usuarios_editar`](../routes/auth_routes.py#L176) |
| `/usuarios/<int:user_id>/reset-password` | POST | [`usuarios_reset_password`](../routes/auth_routes.py#L205) |
| `/usuarios/crear` | POST | [`usuarios_crear`](../routes/auth_routes.py#L144) |

### `routes/batch.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/batch/<int:batch_id>/results` | GET | [`batch_results`](../routes/batch.py#L135) |
| `/batch/add-pdf` | POST | [`batch_add_pdf`](../routes/batch.py#L28) |
| `/batch/new` | GET | [`batch_new`](../routes/batch.py#L24) |
| `/batch/process` | POST | [`batch_process`](../routes/batch.py#L87) |

### `routes/bi.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/bi` | GET | [`bi_tablero`](../routes/bi.py#L36) |

### `routes/bot_config.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/bot-config` | GET | [`bot_config`](../routes/bot_config.py#L20) |
| `/bot-config/aplicar` | POST | [`bot_config_aplicar`](../routes/bot_config.py#L94) |
| `/bot-config/exportar` | GET | [`bot_config_exportar`](../routes/bot_config.py#L27) |
| `/bot-config/preview` | POST | [`bot_config_preview`](../routes/bot_config.py#L43) |

### `routes/caja.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/caja` | GET | [`caja_panel`](../routes/caja.py#L28) |
| `/caja/<int:ticket_id>/anular` | POST | [`caja_anular`](../routes/caja.py#L71) |
| `/caja/<int:ticket_id>/cobrar` | POST | [`caja_cobrar`](../routes/caja.py#L60) |
| `/caja/<int:ticket_id>/entregar` | POST | [`caja_entregar`](../routes/caja.py#L66) |
| `/caja/<int:ticket_id>/enviar-reparto` | POST | [`caja_enviar_reparto`](../routes/caja.py#L76) |
| `/caja/api/bandeja/<name>` | GET | [`caja_bandeja`](../routes/caja.py#L39) |
| `/caja/api/formas-pago` | GET | [`caja_formas_pago`](../routes/caja.py#L55) |
| `/caja/api/tickets` | GET | [`caja_tickets`](../routes/caja.py#L33) |
| `/caja/export` | GET | [`caja_export`](../routes/caja.py#L83) |
| `/caja/formas-pago` | POST | [`caja_forma_crear`](../routes/caja.py#L133) |
| `/caja/formas-pago/<int:forma_id>/delete` | POST | [`caja_forma_eliminar`](../routes/caja.py#L140) |
| `/caja/pedido/<int:pedido_id>/cobrar` | POST | [`caja_pedido_cobrar`](../routes/caja.py#L46) |

### `routes/claims.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/claims` | POST | [`api_create_claim`](../routes/claims.py#L53) |
| `/claim/<int:claim_id>` | GET | [`view_claim`](../routes/claims.py#L16) |
| `/claim/<int:claim_id>/complete` | POST | [`complete_claim_route`](../routes/claims.py#L45) |
| `/claim/<int:claim_id>/pdf` | GET | [`claim_pdf`](../routes/claims.py#L77) |
| `/claim/create` | POST | [`create_claim_route`](../routes/claims.py#L26) |
| `/claims` | GET | [`claims_list`](../routes/claims.py#L68) |

### `routes/clientes.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/clientes` | POST | [`api_clientes_crear`](../routes/clientes.py#L791) |
| `/api/clientes/<int:cid>` | POST | [`api_clientes_editar`](../routes/clientes.py#L811) |
| `/api/clientes/buscar` | GET | [`api_clientes_buscar`](../routes/clientes.py#L742) |
| `/api/clientes/domicilios/<int:dom_id>/geo` | POST | [`api_clientes_domicilio_set_geo`](../routes/clientes.py#L862) |
| `/api/clientes/ficha` | GET | [`api_clientes_ficha`](../routes/clientes.py#L751) |
| `/api/clientes/geocodificar` | GET | [`api_clientes_geocodificar`](../routes/clientes.py#L838) |
| `/api/clientes/observer/<int:oid>/domicilios` | GET | [`api_clientes_domicilios_observer`](../routes/clientes.py#L830) |
| `/api/clientes/separar-direccion` | POST | [`api_clientes_separar_direccion`](../routes/clientes.py#L850) |
| `/cliente/<int:cliente_id>/producto/<int:producto_id>/comportamiento` | GET | [`cliente_producto_comportamiento`](../routes/clientes.py#L508) |
| `/clientes` | GET | [`clientes_list`](../routes/clientes.py#L34) |
| `/clientes/<int:observer_id>` | GET | [`cliente_detail`](../routes/clientes.py#L316) |
| `/clientes/<int:observer_id>/borrar-extension` | POST | [`cliente_borrar_extension`](../routes/clientes.py#L725) |
| `/clientes/<int:observer_id>/edit` | POST | [`cliente_edit`](../routes/clientes.py#L692) |
| `/clientes/stats` | GET | [`clientes_stats`](../routes/clientes.py#L254) |
| `/intelligence/recurrentes` | GET | [`intelligence_recurrentes`](../routes/clientes.py#L341) |

### `routes/comparativa_ventas.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/admin/comparativa-ventas` | GET | [`comparativa_ventas`](../routes/comparativa_ventas.py#L11) |
| `/admin/comparativa-ventas/data` | GET | [`comparativa_ventas_data`](../routes/comparativa_ventas.py#L16) |

### `routes/compartido.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/compartido/push` | POST | [`api_compartido_push`](../routes/compartido.py#L42) |
| `/compartido` | GET | [`compartido_index`](../routes/compartido.py#L66) |
| `/compartido/descartar` | POST | [`compartido_descartar`](../routes/compartido.py#L122) |
| `/compartido/importar` | POST | [`compartido_importar`](../routes/compartido.py#L102) |

### `routes/compras_dia.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/drogueria/<int:prov_id>/pedidos-emitidos` | GET | [`api_drogueria_pedidos_emitidos`](../routes/compras_dia.py#L586) |
| `/api/lab-drog/asignar-bulk` | POST | [`api_lab_drog_asignar_bulk`](../routes/compras_dia.py#L2150) |
| `/api/lab-drog/toggle` | POST | [`api_lab_drog_toggle`](../routes/compras_dia.py#L2123) |
| `/api/matriz/drog-config` | POST | [`api_matriz_drog_config`](../routes/compras_dia.py#L2104) |
| `/api/matriz/drog-visible` | POST | [`api_matriz_drog_visible`](../routes/compras_dia.py#L2091) |
| `/api/pedido-emitido/<int:pedido_id>` | DELETE | [`api_pedido_emitido_borrar`](../routes/compras_dia.py#L652) |
| `/api/pedido-emitido/<int:pedido_id>/export-plantilla` | GET | [`api_pedido_emitido_export_plantilla`](../routes/compras_dia.py#L2561) |
| `/api/pedido-emitido/<int:pedido_id>/export-xls` | GET | [`api_pedido_emitido_export_xls`](../routes/compras_dia.py#L2786) |
| `/api/pedido-emitido/<int:pedido_id>/importar-xls` | POST | [`api_pedido_importar_xls`](../routes/compras_dia.py#L2493) |
| `/api/pedido-emitido/<int:pedido_id>/mapear-ean` | POST | [`api_pedido_mapear_ean`](../routes/compras_dia.py#L2424) |
| `/api/pedido-emitido/<int:pedido_id>/recepcion` | POST | [`api_pedido_recepcion`](../routes/compras_dia.py#L2385) |
| `/api/pedidos-emitidos/todos` | GET | [`api_pedidos_emitidos_todos`](../routes/compras_dia.py#L610) |
| `/api/pedidos/dia/buscar-producto` | GET | [`api_compras_dia_buscar_producto`](../routes/compras_dia.py#L1827) |
| `/api/pedidos/dia/countdown` | GET | [`api_compras_dia_countdown`](../routes/compras_dia.py#L666) |
| `/api/pedidos/dia/emitir` | POST | [`api_compras_dia_emitir`](../routes/compras_dia.py#L2189) |
| `/api/pedidos/dia/horarios/<int:proveedor_id>` | GET/POST/DELETE | [`api_horarios_crud`](../routes/compras_dia.py#L688) |
| `/api/producto/<int:prod_id>/excluir` | POST | [`api_producto_excluir`](../routes/compras_dia.py#L2900) |
| `/api/producto/<int:prod_id>/reactivar` | POST | [`api_producto_reactivar`](../routes/compras_dia.py#L2921) |
| `/api/usuarios-pedidos` | GET/POST | [`api_usuarios_pedidos`](../routes/compras_dia.py#L2859) |
| `/api/usuarios-pedidos/<int:uid>` | DELETE | [`api_usuarios_pedidos_borrar`](../routes/compras_dia.py#L2889) |
| `/compras/armar/exportar-minimos` | GET | [`compras_armar_exportar_minimos`](../routes/compras_dia.py#L1635) |
| `/compras/laboratorio` | GET | [`compras_laboratorio`](../routes/compras_dia.py#L375) |
| `/compras/laboratorio/<int:obs_lab_id>/comprar-modulos` | POST | [`compras_laboratorio_comprar_modulos`](../routes/compras_dia.py#L465) |
| `/compras/labs-drogerias` | GET | [`labs_drogerias_matriz`](../routes/compras_dia.py#L2024) |
| `/compras/multi-lab` | GET | [`compras_multi_lab`](../routes/compras_dia.py#L1590) |
| `/pedidos-emitidos` | GET | [`pedidos_emitidos_list`](../routes/compras_dia.py#L2270) |
| `/pedidos-emitidos/<int:pedido_id>` | GET | [`pedido_emitido_detalle`](../routes/compras_dia.py#L2320) |
| `/pedidos/dia` | GET | [`compras_dia`](../routes/compras_dia.py#L129) |
| `/pedidos/dia/armar` | GET | [`compras_dia_armar`](../routes/compras_dia.py#L747) |

### `routes/compras_rapido.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/compras/conflictos` | POST | [`api_compras_conflictos`](../routes/compras_rapido.py#L477) |
| `/compras/rapido` | GET | [`compras_rapido`](../routes/compras_rapido.py#L38) |
| `/compras/rapido/crear-pedidos` | POST | [`compras_rapido_crear_pedidos`](../routes/compras_rapido.py#L390) |

### `routes/compras_transfers.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/compras/transfer-grupo/toggle` | POST | [`api_compras_transfer_grupo_toggle`](../routes/compras_transfers.py#L174) |
| `/api/compras/transfer/<int:oferta_id>/renovar` | POST | [`api_compras_transfer_renovar`](../routes/compras_transfers.py#L155) |
| `/api/compras/transfer/<int:oferta_id>/toggle` | POST | [`api_compras_transfer_toggle`](../routes/compras_transfers.py#L143) |
| `/compras/transfers` | GET | [`compras_transfers`](../routes/compras_transfers.py#L24) |

### `routes/consulta_droga.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/consulta-droga/buscar` | GET | [`api_consulta_droga_buscar`](../routes/consulta_droga.py#L31) |
| `/consulta-droga` | GET | [`consulta_droga`](../routes/consulta_droga.py#L24) |
| `/consulta-droga/<int:droga_id>` | GET | [`consulta_droga_detalle`](../routes/consulta_droga.py#L47) |

### `routes/consulta_lab.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/consulta-lab` | GET | [`consulta_lab`](../routes/consulta_lab.py#L26) |
| `/consulta-lab/<int:lab_id>` | GET | [`consulta_lab_detalle`](../routes/consulta_lab.py#L40) |
| `/consulta-lab/buscar` | POST | [`consulta_lab_buscar`](../routes/consulta_lab.py#L31) |

### `routes/consulta_medico.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/consulta-medico/buscar` | GET | [`api_consulta_medico_buscar`](../routes/consulta_medico.py#L146) |
| `/consulta-medico` | GET | [`consulta_medico`](../routes/consulta_medico.py#L25) |
| `/consulta-medico/<int:medico_id>` | GET | [`consulta_medico_detalle`](../routes/consulta_medico.py#L30) |

### `routes/consulta_os.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/consulta-os/buscar` | GET | [`api_consulta_os_buscar`](../routes/consulta_os.py#L168) |
| `/consulta-os` | GET | [`consulta_os`](../routes/consulta_os.py#L27) |
| `/consulta-os/<int:os_id>` | GET | [`consulta_os_detalle`](../routes/consulta_os.py#L32) |

### `routes/consulta_paciente.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/consulta-paciente/<int:cliente_id>` | GET | [`consulta_paciente_detalle`](../routes/consulta_paciente.py#L24) |

### `routes/consulta_producto.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/consulta-producto/buscar-desc` | GET | [`api_consulta_producto_buscar_desc`](../routes/consulta_producto.py#L135) |
| `/consulta-producto` | GET | [`consulta_producto`](../routes/consulta_producto.py#L24) |
| `/consulta-producto/<ean>` | GET | [`consulta_producto_detalle`](../routes/consulta_producto.py#L48) |
| `/consulta-producto/buscar` | POST | [`consulta_producto_buscar`](../routes/consulta_producto.py#L30) |

### `routes/consulta_producto_stats.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/consulta-producto-stats/<int:observer_id>` | GET | [`consulta_producto_stats`](../routes/consulta_producto_stats.py#L28) |

### `routes/contabilidad.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/contabilidad` | GET | [`contabilidad_index`](../routes/contabilidad.py#L32) |
| `/contabilidad/formas-pago` | GET | [`contabilidad_formas_pago`](../routes/contabilidad.py#L105) |
| `/contabilidad/formas-pago/<int:cuenta_id>/movimientos` | GET | [`contabilidad_forma_pago_movimientos`](../routes/contabilidad.py#L150) |
| `/contabilidad/formas-pago/guardar` | POST | [`contabilidad_forma_pago_guardar`](../routes/contabilidad.py#L120) |
| `/contabilidad/pagos` | GET | [`contabilidad_pagos`](../routes/contabilidad.py#L190) |
| `/contabilidad/pagos/<int:pago_id>/delete` | POST | [`contabilidad_pago_delete`](../routes/contabilidad.py#L293) |
| `/contabilidad/pagos/guardar` | POST | [`contabilidad_pago_guardar`](../routes/contabilidad.py#L224) |
| `/contabilidad/pagos/nuevo` | GET | [`contabilidad_pago_nuevo`](../routes/contabilidad.py#L206) |
| `/contabilidad/proveedores` | GET | [`contabilidad_proveedores`](../routes/contabilidad.py#L37) |
| `/contabilidad/proveedores/guardar` | POST | [`contabilidad_proveedor_guardar`](../routes/contabilidad.py#L75) |

### `routes/converter.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/converter` | GET | [`converter_index`](../routes/converter.py#L184) |
| `/converter/<token>/analizar` | POST | [`converter_analizar`](../routes/converter.py#L251) |
| `/converter/<token>/auto` | GET | [`converter_auto`](../routes/converter.py#L503) |
| `/converter/<token>/auto-import` | POST | [`converter_auto_import`](../routes/converter.py#L439) |
| `/converter/<token>/delete` | POST | [`converter_delete`](../routes/converter.py#L561) |
| `/converter/<token>/detectar` | GET | [`converter_detectar`](../routes/converter.py#L317) |
| `/converter/<token>/enviar-a-proceso` | POST | [`converter_enviar_a_proceso`](../routes/converter.py#L942) |
| `/converter/<token>/export` | POST | [`converter_export`](../routes/converter.py#L991) |
| `/converter/<token>/extraer-json` | POST | [`converter_extraer_json`](../routes/converter.py#L663) |
| `/converter/<token>/guardar-factura` | POST | [`converter_guardar_factura`](../routes/converter.py#L924) |
| `/converter/<token>/guardar-parser` | POST | [`converter_guardar_parser`](../routes/converter.py#L863) |
| `/converter/<token>/importar-json` | POST | [`converter_importar_json`](../routes/converter.py#L712) |
| `/converter/<token>/infer` | POST | [`converter_infer`](../routes/converter.py#L802) |
| `/converter/<token>/pick` | GET | [`converter_pick`](../routes/converter.py#L747) |
| `/converter/<token>/reocr-vision` | POST | [`converter_reocr_vision`](../routes/converter.py#L590) |
| `/converter/<token>/verify` | GET | [`converter_verify`](../routes/converter.py#L363) |
| `/converter/<token>/verify/import` | POST | [`converter_verify_import`](../routes/converter.py#L423) |
| `/converter/check-duplicate` | GET | [`converter_check_duplicate`](../routes/converter.py#L205) |
| `/converter/delete-bulk` | POST | [`converter_delete_bulk`](../routes/converter.py#L571) |
| `/converter/upload` | POST | [`converter_upload`](../routes/converter.py#L224) |

### `routes/core.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/` | GET | [`index`](../routes/core.py#L14) |
| `/admin/backup` | POST | [`admin_backup`](../routes/core.py#L169) |
| `/admin/console` | GET | [`admin_console`](../routes/core.py#L142) |
| `/admin/dashboard` | GET | [`admin_console`](../routes/core.py#L142) |
| `/health` | GET | [`health`](../routes/core.py#L199) |
| `/health_web` | GET | [`health`](../routes/core.py#L199) |
| `/ingresos` | GET | [`ingresos`](../routes/core.py#L88) |
| `/ping` | GET | [`ping`](../routes/core.py#L213) |
| `/settings` | GET | [`settings`](../routes/core.py#L97) |
| `/settings` | POST | [`settings_save`](../routes/core.py#L101) |

### `routes/cronograma.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/cronograma` | GET | [`cronograma_list`](../routes/cronograma.py#L199) |
| `/cronograma/<int:cron_id>/delete` | POST | [`cronograma_delete`](../routes/cronograma.py#L561) |
| `/cronograma/<int:cron_id>/editar` | GET | [`cronograma_editar`](../routes/cronograma.py#L420) |
| `/cronograma/<int:cron_id>/toggle` | POST | [`cronograma_toggle`](../routes/cronograma.py#L550) |
| `/cronograma/config` | GET | [`cronograma_config`](../routes/cronograma.py#L350) |
| `/cronograma/nuevo` | GET | [`cronograma_nuevo`](../routes/cronograma.py#L406) |
| `/cronograma/save` | POST | [`cronograma_save`](../routes/cronograma.py#L463) |

### `routes/cuentas.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/comprobantes/importar` | GET/POST | [`comprobantes_importar`](../routes/cuentas.py#L158) |
| `/cuentas-corrientes` | GET | [`cuentas_corrientes`](../routes/cuentas.py#L134) |
| `/provider/<int:provider_id>/cuenta-corriente/<int:mov_id>/delete` | POST | [`cuenta_corriente_delete`](../routes/cuentas.py#L313) |
| `/provider/<int:provider_id>/cuenta-corriente/<int:mov_id>/edit-obs` | POST | [`cuenta_corriente_edit_obs`](../routes/cuentas.py#L347) |
| `/provider/<int:provider_id>/cuenta-corriente/add` | POST | [`cuenta_corriente_add`](../routes/cuentas.py#L277) |
| `/provider/<int:provider_id>/cuenta-corriente/conciliar` | POST | [`cuenta_corriente_conciliar`](../routes/cuentas.py#L327) |

### `routes/dashboard.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/dashboard` | GET | [`dashboard`](../routes/dashboard.py#L11) |
| `/dashboard/help` | GET | [`dashboard_help`](../routes/dashboard.py#L184) |
| `/dashboard/recalcular` | POST | [`dashboard_recalcular`](../routes/dashboard.py#L172) |

### `routes/descuentos_base.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/mejor-descuento/<int:observer_id>` | GET | [`api_mejor_descuento`](../routes/descuentos_base.py#L53) |
| `/descuentos-base` | GET | [`descuentos_base_lista`](../routes/descuentos_base.py#L22) |
| `/descuentos-base/celda` | POST | [`descuentos_base_set`](../routes/descuentos_base.py#L88) |

### `routes/devoluciones.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/rend` | GET | [`rend_alias`](../routes/devoluciones.py#L207) |
| `/rend-recetas` | GET | [`devoluciones_list`](../routes/devoluciones.py#L766) |
| `/rend-recetas/<int:id>/auditar` | POST | [`devolucion_auditar`](../routes/devoluciones.py#L2185) |
| `/rend-recetas/<int:id>/eliminar` | POST | [`devolucion_eliminar`](../routes/devoluciones.py#L2224) |
| `/rend-recetas/<int:id>/estado` | POST | [`devolucion_cambiar_estado`](../routes/devoluciones.py#L1781) |
| `/rend-recetas/<int:id>/marcar-rendida` | POST | [`marcar_rendida_vendedor`](../routes/devoluciones.py#L1843) |
| `/rend-recetas/<int:id>/set-motivo-vendedor` | POST | [`set_motivo_vendedor`](../routes/devoluciones.py#L1822) |
| `/rend-recetas/<int:id>/timeline` | GET | [`devolucion_timeline`](../routes/devoluciones.py#L2025) |
| `/rend-recetas/asignar-huerfanas` | POST | [`devoluciones_asignar_huerfanas`](../routes/devoluciones.py#L656) |
| `/rend-recetas/buscar` | GET/POST | [`devoluciones_buscar`](../routes/devoluciones.py#L1035) |
| `/rend-recetas/config-grupos` | GET/POST | [`rendicion_grupos`](../routes/devoluciones.py#L2297) |
| `/rend-recetas/config-grupos/<int:gid>/agregar-os` | POST | [`rendicion_grupo_agregar_os`](../routes/devoluciones.py#L2334) |
| `/rend-recetas/config-grupos/<int:gid>/editar` | POST | [`rendicion_grupo_editar`](../routes/devoluciones.py#L2396) |
| `/rend-recetas/config-grupos/<int:gid>/eliminar` | POST | [`rendicion_grupo_eliminar`](../routes/devoluciones.py#L2423) |
| `/rend-recetas/config-grupos/<int:gid>/quitar-os/<int:rgo_id>` | POST | [`rendicion_grupo_quitar_os`](../routes/devoluciones.py#L2385) |
| `/rend-recetas/dedup` | GET | [`devoluciones_dedup_vista`](../routes/devoluciones.py#L610) |
| `/rend-recetas/dedup/aplicar` | POST | [`devoluciones_dedup_aplicar`](../routes/devoluciones.py#L622) |
| `/rend-recetas/export.xlsx` | GET | [`devoluciones_export_xlsx`](../routes/devoluciones.py#L2084) |
| `/rend-recetas/filtros-os` | GET/POST | [`devoluciones_filtros_os`](../routes/devoluciones.py#L2238) |
| `/rend-recetas/filtros-os/<int:id>/eliminar` | POST | [`devoluciones_filtro_os_eliminar`](../routes/devoluciones.py#L2279) |
| `/rend-recetas/guardar` | POST | [`devoluciones_guardar`](../routes/devoluciones.py#L1469) |
| `/rend-recetas/lotes` | GET | [`rendicion_lotes_list`](../routes/devoluciones.py#L350) |
| `/rend-recetas/lotes/<int:id>/cerrar` | POST | [`rendicion_lote_cerrar`](../routes/devoluciones.py#L568) |
| `/rend-recetas/lotes/<int:id>/eliminar` | POST | [`rendicion_lote_eliminar`](../routes/devoluciones.py#L723) |
| `/rend-recetas/lotes/<int:id>/entregada` | POST | [`rendicion_lote_toggle_entregada`](../routes/devoluciones.py#L686) |
| `/rend-recetas/lotes/<int:id>/reabrir` | POST | [`rendicion_lote_reabrir`](../routes/devoluciones.py#L747) |
| `/rend-recetas/lotes/<int:id>/recibo.pdf` | GET | [`rendicion_lote_recibo_pdf`](../routes/devoluciones.py#L467) |
| `/rend-recetas/lotes/crear` | POST | [`rendicion_lote_crear`](../routes/devoluciones.py#L403) |
| `/rend-recetas/motivos` | GET/POST | [`devoluciones_motivos`](../routes/devoluciones.py#L2441) |
| `/rend-recetas/motivos/<int:id>/bloquea-rendida` | POST | [`devoluciones_motivo_bloquea_rendida`](../routes/devoluciones.py#L2491) |
| `/rend-recetas/motivos/<int:id>/eliminar` | POST | [`devoluciones_motivo_eliminar`](../routes/devoluciones.py#L2503) |
| `/rend-recetas/motivos/<int:id>/toggle` | POST | [`devoluciones_motivo_toggle`](../routes/devoluciones.py#L2467) |
| `/rend-recetas/motivos/<int:id>/uso-rol` | POST | [`devoluciones_motivo_uso_rol`](../routes/devoluciones.py#L2477) |
| `/rend-recetas/por-vendedor` | GET | [`devoluciones_por_vendedor`](../routes/devoluciones.py#L215) |
| `/rend-recetas/rendir-os` | GET | [`rendir_os`](../routes/devoluciones.py#L1864) |
| `/rend-recetas/rendir-os/export.pdf` | GET | [`rendir_os_export_pdf`](../routes/devoluciones.py#L1974) |
| `/rend-recetas/rendir-os/export.xlsx` | GET | [`rendir_os_export_xlsx`](../routes/devoluciones.py#L1940) |
| `/rend-recetas/rendir-os/marcar` | POST | [`rendir_os_marcar`](../routes/devoluciones.py#L1905) |

### `routes/docs_pendientes.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/notifications` | GET | [`api_notifications`](../routes/docs_pendientes.py#L133) |
| `/api/product/<path:barcode>/chart` | GET | [`api_product_chart`](../routes/docs_pendientes.py#L151) |
| `/docs-pendientes` | GET | [`docs_pendientes`](../routes/docs_pendientes.py#L15) |
| `/docs-pendientes/<int:doc_id>/delete` | POST | [`docs_pendientes_delete`](../routes/docs_pendientes.py#L119) |
| `/docs-pendientes/<int:doc_id>/procesar` | GET | [`docs_pendientes_procesar`](../routes/docs_pendientes.py#L63) |
| `/docs-pendientes/upload` | POST | [`docs_pendientes_upload`](../routes/docs_pendientes.py#L25) |
| `/docs-pendientes/upload-api` | POST | [`docs_pendientes_upload_api`](../routes/docs_pendientes.py#L79) |

### `routes/envio.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/config/envio` | GET | [`envio_panel`](../routes/envio.py#L37) |
| `/config/envio/api/cotizar` | GET | [`envio_cotizar`](../routes/envio.py#L51) |
| `/config/envio/api/tarifas` | GET | [`envio_tarifas`](../routes/envio.py#L44) |
| `/config/envio/geolocalizar` | POST | [`envio_config_geo`](../routes/envio.py#L85) |
| `/config/envio/save` | POST | [`envio_config_guardar`](../routes/envio.py#L67) |
| `/config/envio/tramo` | POST | [`envio_tramo_guardar`](../routes/envio.py#L101) |
| `/config/envio/tramo/<int:tid>/delete` | POST | [`envio_tramo_eliminar`](../routes/envio.py#L109) |
| `/config/envio/zona` | POST | [`envio_zona_guardar`](../routes/envio.py#L116) |
| `/config/envio/zona/<int:zid>/delete` | POST | [`envio_zona_eliminar`](../routes/envio.py#L128) |
| `/config/envio/zona/<int:zid>/geolocalizar` | POST | [`envio_zona_geo`](../routes/envio.py#L94) |
| `/envio` | GET | [`envio_legacy_panel_redirect`](../routes/envio.py#L136) |

### `routes/estacionalidad.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/estacionalidad/droga/<int:droga_id>` | GET | [`api_estacionalidad_droga`](../routes/estacionalidad.py#L383) |
| `/api/estacionalidad/droga/<int:droga_id>/aplicar` | POST | [`api_estacionalidad_aplicar_productos`](../routes/estacionalidad.py#L556) |
| `/api/estacionalidad/droga/<int:droga_id>/desvincular` | POST | [`api_estacionalidad_desvincular_productos`](../routes/estacionalidad.py#L594) |
| `/api/estacionalidad/droga/<int:droga_id>/escenarios` | GET | [`api_escenarios_listar`](../routes/estacionalidad.py#L415) |
| `/api/estacionalidad/droga/<int:droga_id>/escenarios` | POST | [`api_escenarios_crear_o_actualizar`](../routes/estacionalidad.py#L429) |
| `/api/estacionalidad/droga/<int:droga_id>/escenarios/<int:esc_id>` | DELETE | [`api_escenarios_eliminar`](../routes/estacionalidad.py#L478) |
| `/api/estacionalidad/droga/<int:droga_id>/escenarios/<int:esc_id>/default` | POST | [`api_escenarios_marcar_default`](../routes/estacionalidad.py#L491) |
| `/api/estacionalidad/droga/<int:droga_id>/productos` | GET | [`api_estacionalidad_droga_productos`](../routes/estacionalidad.py#L507) |
| `/informes/estacionalidad-drogas` | GET | [`informe_estacionalidad_drogas`](../routes/estacionalidad.py#L188) |

### `routes/filtro_drogueria.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/FILTRO-DROGUERIA` | GET | [`filtro_drogueria`](../routes/filtro_drogueria.py#L54) |
| `/FILTRO_DROGUERIA` | GET | [`filtro_drogueria`](../routes/filtro_drogueria.py#L54) |
| `/filtro-drogueria` | GET | [`filtro_drogueria`](../routes/filtro_drogueria.py#L54) |
| `/filtro_drogueria` | GET | [`filtro_drogueria`](../routes/filtro_drogueria.py#L54) |
| `/filtro_drogueria/generar` | POST | [`filtro_drogueria_generar`](../routes/filtro_drogueria.py#L69) |

### `routes/filtro_drogueria_archivo.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/filtro-drogueria/archivo` | GET | [`filtro_drogueria_archivo`](../routes/filtro_drogueria_archivo.py#L309) |
| `/filtro-drogueria/archivo/parsear` | POST | [`filtro_drogueria_archivo_parsear`](../routes/filtro_drogueria_archivo.py#L315) |
| `/filtro-drogueria/archivo/separar` | POST | [`filtro_drogueria_archivo_separar`](../routes/filtro_drogueria_archivo.py#L336) |
| `/filtro_drogueria/archivo` | GET | [`filtro_drogueria_archivo`](../routes/filtro_drogueria_archivo.py#L309) |
| `/filtro_drogueria/archivo/parsear` | POST | [`filtro_drogueria_archivo_parsear`](../routes/filtro_drogueria_archivo.py#L315) |
| `/filtro_drogueria/archivo/separar` | POST | [`filtro_drogueria_archivo_separar`](../routes/filtro_drogueria_archivo.py#L336) |

### `routes/flujo_fondos.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/flujo/aplicar-dto-default` | POST | [`flujo_aplicar_dto_default`](../routes/flujo_fondos.py#L189) |
| `/api/flujo/cronograma-precarga` | GET | [`api_flujo_cronograma_precarga`](../routes/flujo_fondos.py#L202) |
| `/finanzas/flujo` | GET | [`flujo_fondos`](../routes/flujo_fondos.py#L37) |

### `routes/help.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/help/` | GET | [`api_help`](../routes/help.py#L48) |
| `/api/help/<path:section>` | GET | [`api_help`](../routes/help.py#L48) |
| `/api/help/_index` | GET | [`api_help_index`](../routes/help.py#L31) |

### `routes/home_cards.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/configuracion/personalizar-home` | GET/POST | [`personalizar_home`](../routes/home_cards.py#L31) |
| `/go/<card_id>` | GET | [`home_card_go`](../routes/home_cards.py#L14) |

### `routes/inferencia.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/inferir-columnas` | POST | [`api_inferir_columnas`](../routes/inferencia.py#L20) |
| `/api/inferir/factura-completa` | POST | [`api_inferir_factura_completa`](../routes/inferencia.py#L116) |
| `/api/inferir/fila-factura` | POST | [`api_inferir_fila_factura`](../routes/inferencia.py#L64) |
| `/api/inferir/fila-totales` | POST | [`api_inferir_fila_totales`](../routes/inferencia.py#L89) |
| `/api/inferir/relaciones` | POST | [`api_inferir_relaciones`](../routes/inferencia.py#L134) |
| `/api/inferir/tipo-valor` | POST | [`api_inferir_tipo_valor`](../routes/inferencia.py#L52) |

### `routes/informes.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/informes/buscar-droga` | GET | [`api_buscar_droga`](../routes/informes.py#L2823) |
| `/api/informes/buscar-lab` | GET | [`api_informes_buscar_lab`](../routes/informes.py#L2536) |
| `/api/informes/buscar-os` | GET | [`api_informes_buscar_os`](../routes/informes.py#L2519) |
| `/api/informes/buscar-producto-obs` | GET | [`api_informes_buscar_producto_obs`](../routes/informes.py#L2552) |
| `/api/informes/ventas-multi/detalle` | GET | [`api_ventas_multi_detalle`](../routes/informes.py#L2260) |
| `/api/informes/ventas-multi/historico-droga-medico` | GET | [`api_ventas_multi_hist_droga_medico`](../routes/informes.py#L2420) |
| `/api/observer-product/<int:observer_id>/chart` | GET | [`api_observer_product_chart`](../routes/informes.py#L2569) |
| `/api/observer-product/<int:observer_id>/chart-mes` | GET | [`api_observer_product_chart_mes`](../routes/informes.py#L2606) |
| `/api/observer-product/<int:observer_id>/ingresos-mes` | GET | [`api_observer_product_ingresos_mes`](../routes/informes.py#L2655) |
| `/api/observer-product/<int:observer_id>/stock-snapshot` | GET | [`api_observer_product_stock_snapshot`](../routes/informes.py#L2792) |
| `/api/stock/snapshot-diario` | POST | [`api_stock_snapshot_diario`](../routes/informes.py#L2756) |
| `/informes` | GET | [`informes_index`](../routes/informes.py#L125) |
| `/informes/analisis-ia/ultimo` | GET | [`informe_analisis_ia_ultimo`](../routes/informes.py#L769) |
| `/informes/bajo-minimo` | GET | [`informe_bajo_minimo`](../routes/informes.py#L1521) |
| `/informes/cadencias-lab` | GET | [`informe_cadencias_lab`](../routes/informes.py#L660) |
| `/informes/cadencias-resumen` | GET | [`informe_cadencias_resumen`](../routes/informes.py#L698) |
| `/informes/cadencias-resumen/analizar` | POST | [`informe_cadencias_resumen_analizar`](../routes/informes.py#L726) |
| `/informes/cadencias-resumen/recalcular` | POST | [`informe_cadencias_resumen_recalcular`](../routes/informes.py#L709) |
| `/informes/comparativa-drogas` | GET | [`informe_comparativa_drogas`](../routes/informes.py#L928) |
| `/informes/correcciones-minimos` | GET | [`informe_correcciones_minimos`](../routes/informes.py#L1349) |
| `/informes/cronicos-pami` | GET | [`informe_cronicos_pami`](../routes/informes.py#L280) |
| `/informes/cronicos-pami/afiliado` | GET | [`informe_cronicos_pami_afiliado`](../routes/informes.py#L428) |
| `/informes/drogas-sin-alternativa` | GET | [`informe_drogas_sin_alternativa`](../routes/informes.py#L1251) |
| `/informes/eventos-sla` | GET | [`informes_eventos_sla`](../routes/informes.py#L131) |
| `/informes/lab-cobertura-moleculas` | GET | [`informe_lab_cobertura_moleculas`](../routes/informes.py#L993) |
| `/informes/lab-cobertura-moleculas/analizar` | POST | [`informe_lab_cobertura_moleculas_analizar`](../routes/informes.py#L1000) |
| `/informes/lab-gap-marcas` | GET | [`informe_lab_gap_marcas`](../routes/informes.py#L815) |
| `/informes/lab-gap-marcas/analizar` | POST | [`informe_lab_gap_marcas_analizar`](../routes/informes.py#L882) |
| `/informes/lab-gap-marcas/recopilar` | POST | [`informe_lab_gap_marcas_recopilar`](../routes/informes.py#L828) |
| `/informes/lab-ranking-nacional` | GET | [`informe_lab_ranking_nacional`](../routes/informes.py#L943) |
| `/informes/lab-ranking-nacional/analizar` | POST | [`informe_lab_ranking_nacional_analizar`](../routes/informes.py#L950) |
| `/informes/labs-por-droga` | GET | [`informe_labs_por_droga`](../routes/informes.py#L1043) |
| `/informes/ofertas-activas` | GET | [`informe_ofertas_activas`](../routes/informes.py#L2851) |
| `/informes/ofertas-activas/borrar-grupo` | POST | [`informe_grupo_borrar`](../routes/informes.py#L3290) |
| `/informes/ofertas-activas/borrar-grupos-bulk` | POST | [`informe_grupos_borrar_bulk`](../routes/informes.py#L3320) |
| `/informes/ofertas-activas/borrar-modulo/<int:modulo_id>` | POST | [`informe_modulo_borrar`](../routes/informes.py#L3355) |
| `/informes/ofertas-activas/grupo/toggle-activa` | POST | [`informes_ofertas_grupo_toggle_activa`](../routes/informes.py#L2936) |
| `/informes/ofertas-activas/pull-render-bulk` | POST | [`informe_pull_render_bulk`](../routes/informes.py#L3145) |
| `/informes/ofertas-activas/queue/borrar` | POST | [`informes_ofertas_queue_borrar`](../routes/informes.py#L3052) |
| `/informes/ofertas-activas/queue/preview` | GET | [`informes_ofertas_queue_preview`](../routes/informes.py#L3032) |
| `/informes/ofertas-activas/sospechosas/borrar` | POST | [`informes_ofertas_sospechosas_borrar`](../routes/informes.py#L3012) |
| `/informes/ofertas-activas/sospechosas/preview` | GET | [`informes_ofertas_sospechosas_preview`](../routes/informes.py#L2987) |
| `/informes/ofertas-activas/sync-render-bulk` | POST | [`informe_sync_render_bulk`](../routes/informes.py#L3064) |
| `/informes/presentaciones-por-droga` | GET | [`informe_presentaciones_por_droga`](../routes/informes.py#L1151) |
| `/informes/ventas-comparativa` | GET | [`informe_ventas_comparativa`](../routes/informes.py#L185) |
| `/informes/ventas-droga-anual` | GET | [`informe_ventas_droga_anual`](../routes/informes.py#L236) |
| `/informes/ventas-multi` | GET | [`informe_ventas_multi`](../routes/informes.py#L1638) |
| `/informes/ventas-multi/export.xlsx` | GET | [`informe_ventas_multi_export`](../routes/informes.py#L2000) |
| `/informes/ventas-producto-anual` | GET | [`informe_ventas_producto_anual`](../routes/informes.py#L214) |
| `/informes/ventas-vendedor` | GET | [`informes_ventas_vendedor`](../routes/informes.py#L161) |

### `routes/invoices.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/invoice/<int:invoice_id>/differences` | GET | [`invoice_differences`](../routes/invoices.py#L682) |
| `/api/upload` | POST | [`upload_files_api`](../routes/invoices.py#L278) |
| `/invoice/<int:invoice_id>/apply-mapping` | POST | [`apply_mapping`](../routes/invoices.py#L1010) |
| `/invoice/<int:invoice_id>/auto-table` | POST | [`auto_table`](../routes/invoices.py#L311) |
| `/invoice/<int:invoice_id>/compare` | GET | [`compare_view`](../routes/invoices.py#L931) |
| `/invoice/<int:invoice_id>/differences/export` | GET | [`invoice_differences_export`](../routes/invoices.py#L899) |
| `/invoice/<int:invoice_id>/erp-upload` | POST | [`invoice_erp_upload`](../routes/invoices.py#L972) |
| `/invoice/<int:invoice_id>/header` | POST | [`update_invoice_header`](../routes/invoices.py#L698) |
| `/invoice/<int:invoice_id>/items` | GET | [`invoice_items`](../routes/invoices.py#L775) |
| `/invoice/<int:invoice_id>/items/export` | GET | [`invoice_items_export`](../routes/invoices.py#L865) |
| `/invoice/<int:invoice_id>/manual-items` | GET/POST | [`manual_items`](../routes/invoices.py#L619) |
| `/invoice/<int:invoice_id>/map-columns` | GET/POST | [`map_columns`](../routes/invoices.py#L352) |
| `/invoice/<int:invoice_id>/parse-helper` | GET | [`parse_helper`](../routes/invoices.py#L295) |
| `/invoice/<int:invoice_id>/pick-fields` | GET | [`pick_fields`](../routes/invoices.py#L742) |
| `/invoice/<int:invoice_id>/pick-fields` | POST | [`pick_fields_save`](../routes/invoices.py#L760) |
| `/invoice/<int:invoice_id>/pick-items` | GET | [`pick_items`](../routes/invoices.py#L467) |
| `/invoice/<int:invoice_id>/pick-items/infer` | POST | [`pick_items_infer`](../routes/invoices.py#L483) |
| `/invoice/<int:invoice_id>/pick-items/save` | POST | [`pick_items_save`](../routes/invoices.py#L523) |
| `/invoice/<int:invoice_id>/refresh-numero` | POST | [`invoice_refresh_numero`](../routes/invoices.py#L831) |
| `/results/<int:invoice_id>` | GET | [`show_results`](../routes/invoices.py#L720) |
| `/upload` | POST | [`upload_files`](../routes/invoices.py#L203) |
| `/uploads/pdf/<path:filename>` | GET | [`serve_invoice_pdf`](../routes/invoices.py#L199) |

### `routes/kellerhoff.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/kellerhoff/catalogo/buscar` | GET | [`kellerhoff_catalogo_buscar`](../routes/kellerhoff.py#L457) |
| `/api/producto/kellerhoff-equivalencia` | POST | [`kellerhoff_equivalencia_guardar`](../routes/kellerhoff.py#L477) |
| `/kellerhoff/catalogo` | GET | [`kellerhoff_catalogo`](../routes/kellerhoff.py#L271) |
| `/kellerhoff/catalogo/cobertura` | GET | [`kellerhoff_catalogo_cobertura`](../routes/kellerhoff.py#L384) |
| `/kellerhoff/catalogo/importar` | POST | [`kellerhoff_catalogo_importar`](../routes/kellerhoff.py#L359) |
| `/kellerhoff/equivalencias` | GET | [`kellerhoff_equivalencias_list`](../routes/kellerhoff.py#L278) |
| `/kellerhoff/equivalencias/<int:eid>/eliminar` | POST | [`kellerhoff_equivalencia_eliminar`](../routes/kellerhoff.py#L348) |
| `/kellerhoff/equivalencias/recalcular` | POST | [`kellerhoff_equivalencias_recalcular`](../routes/kellerhoff.py#L443) |

### `routes/laboratorios.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/laboratorio/<int:lab_id>/descuento-base` | PATCH | [`api_lab_descuento_base`](../routes/laboratorios.py#L769) |
| `/api/laboratorio/<int:lab_id>/ofertas-minimo` | GET | [`api_ofertas_minimo_get`](../routes/laboratorios.py#L827) |
| `/api/laboratorio/<int:lab_id>/ofertas-minimo` | POST | [`api_ofertas_minimo_save`](../routes/laboratorios.py#L849) |
| `/api/laboratorio/<int:lab_id>/parser-ofertas` | DELETE | [`api_parser_ofertas_delete`](../routes/laboratorios.py#L931) |
| `/api/laboratorio/<int:lab_id>/parser-ofertas` | GET | [`api_parser_ofertas_get`](../routes/laboratorios.py#L880) |
| `/api/laboratorio/<int:lab_id>/parser-ofertas` | POST | [`api_parser_ofertas_save`](../routes/laboratorios.py#L897) |
| `/api/laboratorio/<int:lab_id>/usa-packs` | POST | [`laboratorio_toggle_packs`](../routes/laboratorios.py#L77) |
| `/api/ofertas/from-server` | GET | [`api_ofertas_from_server`](../routes/laboratorios.py#L1114) |
| `/api/ofertas/preview` | POST | [`api_ofertas_preview`](../routes/laboratorios.py#L264) |
| `/api/ofertas/preview-con-minimo` | POST | [`api_ofertas_preview_con_minimo`](../routes/laboratorios.py#L285) |
| `/api/ofertas/sync-from-local` | POST | [`api_ofertas_sync_from_local`](../routes/laboratorios.py#L1011) |
| `/laboratorio/<int:lab_id>/delete` | POST | [`laboratorio_delete`](../routes/laboratorios.py#L125) |
| `/laboratorio/<int:lab_id>/edit` | POST | [`laboratorio_edit`](../routes/laboratorios.py#L103) |
| `/laboratorio/<int:lab_id>/equivalencias` | GET | [`lab_equivalencias`](../routes/laboratorios.py#L361) |
| `/laboratorio/<int:lab_id>/equivalencias/<int:eq_id>/borrar` | POST | [`lab_equivalencia_borrar`](../routes/laboratorios.py#L384) |
| `/laboratorio/<int:lab_id>/equivalencias/semillar` | POST | [`lab_equivalencias_semillar`](../routes/laboratorios.py#L394) |
| `/laboratorio/<int:lab_id>/export-template` | GET/POST | [`laboratorio_export_template`](../routes/laboratorios.py#L1277) |
| `/laboratorio/<int:lab_id>/ofertas-minimo` | GET | [`lab_ofertas_minimo`](../routes/laboratorios.py#L307) |
| `/laboratorio/<int:lab_id>/ofertas-minimo/<int:oferta_id>/borrar` | POST | [`lab_oferta_minima_borrar`](../routes/laboratorios.py#L758) |
| `/laboratorio/<int:lab_id>/ofertas-minimo/<int:oferta_id>/editar` | PATCH | [`lab_oferta_minima_editar`](../routes/laboratorios.py#L791) |
| `/laboratorio/<int:lab_id>/ofertas-minimo/borrar-todas` | POST | [`lab_ofertas_minimo_borrar_todas`](../routes/laboratorios.py#L750) |
| `/laboratorio/<int:lab_id>/ofertas-minimo/pull-render` | POST | [`lab_ofertas_minimo_pull_render`](../routes/laboratorios.py#L1171) |
| `/laboratorio/<int:lab_id>/ofertas-minimo/sync-render` | POST | [`lab_ofertas_minimo_sync_render`](../routes/laboratorios.py#L956) |
| `/laboratorio/<int:lab_id>/pack-equivalencias` | GET | [`lab_pack_equivalencias`](../routes/laboratorios.py#L476) |
| `/laboratorio/<int:lab_id>/pack-equivalencias/<int:eq_id>/borrar` | POST | [`lab_pack_equivalencia_borrar`](../routes/laboratorios.py#L688) |
| `/laboratorio/<int:lab_id>/pack-equivalencias/crear` | POST | [`lab_pack_equivalencia_crear`](../routes/laboratorios.py#L643) |
| `/laboratorio/<int:lab_id>/pack-equivalencias/edit` | POST | [`lab_pack_equivalencia_edit`](../routes/laboratorios.py#L717) |
| `/laboratorio/<int:lab_id>/pack-equivalencias/upload` | POST | [`lab_pack_equivalencias_upload`](../routes/laboratorios.py#L501) |
| `/laboratorio/<int:lab_id>/pedido` | GET | [`laboratorio_pedido`](../routes/laboratorios.py#L92) |
| `/laboratorio/create` | POST | [`laboratorio_create`](../routes/laboratorios.py#L58) |
| `/laboratorios` | GET | [`laboratorios_list`](../routes/laboratorios.py#L34) |
| `/laboratorios/activos` | GET/POST | [`laboratorios_activos`](../routes/laboratorios.py#L238) |
| `/laboratorios/sync-observer` | POST | [`laboratorios_sync_observer`](../routes/laboratorios.py#L160) |

### `routes/memoria_no_resueltos.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/bot/no-resueltos` | GET | [`memoria_panel`](../routes/memoria_no_resueltos.py#L70) |
| `/bot/no-resueltos/api/lista` | GET | [`memoria_lista`](../routes/memoria_no_resueltos.py#L79) |
| `/bot/no-resueltos/api/resumen` | GET | [`memoria_resumen`](../routes/memoria_no_resueltos.py#L90) |
| `/bot/no-resueltos/export.xlsx` | GET | [`memoria_export`](../routes/memoria_no_resueltos.py#L98) |

### `routes/modulo_packs.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/packs/buscar-unidad` | GET | [`api_packs_buscar_unidad`](../routes/modulo_packs.py#L261) |
| `/modulo-pack/<int:pack_id>/assign` | POST | [`modulo_pack_assign`](../routes/modulo_packs.py#L996) |
| `/modulo-pack/<int:pack_id>/delete` | POST | [`modulo_pack_delete`](../routes/modulo_packs.py#L1090) |
| `/modulo-pack/<int:pack_id>/update` | POST | [`modulo_pack_update`](../routes/modulo_packs.py#L1046) |
| `/modulo-pack/add` | POST | [`modulo_pack_add`](../routes/modulo_packs.py#L1012) |
| `/modulo-packs` | GET | [`modulo_packs_list`](../routes/modulo_packs.py#L16) |
| `/modulo-packs/activos` | GET | [`modulo_packs_activos`](../routes/modulo_packs.py#L848) |
| `/modulo-packs/import-confirmar` | POST | [`modulo_packs_import_confirmar`](../routes/modulo_packs.py#L717) |
| `/modulo-packs/import-preview` | POST | [`modulo_packs_import_preview`](../routes/modulo_packs.py#L640) |
| `/modulo-packs/importar` | POST | [`modulo_packs_importar`](../routes/modulo_packs.py#L308) |
| `/modulo-packs/plantilla` | GET | [`modulo_packs_plantilla`](../routes/modulo_packs.py#L191) |
| `/modulo-packs/producto/marcar-pack` | POST | [`modulo_packs_producto_marcar`](../routes/modulo_packs.py#L695) |
| `/modulo-packs/vista` | GET | [`modulo_packs_vista`](../routes/modulo_packs.py#L182) |
| `/modulo/<int:modulo_id>/delete` | POST | [`modulo_delete`](../routes/modulo_packs.py#L983) |
| `/modulo/<int:modulo_id>/toggle-activo` | POST | [`modulo_toggle_activo`](../routes/modulo_packs.py#L935) |
| `/modulo/add` | POST | [`modulo_add`](../routes/modulo_packs.py#L960) |
| `/modulos/delete-by-lista` | POST | [`modulos_delete_by_lista`](../routes/modulo_packs.py#L825) |

### `routes/modulos_import.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/modulos/import-guardar` | POST | [`api_modulos_import_guardar`](../routes/modulos_import.py#L483) |
| `/api/modulos/import-preview` | POST | [`api_modulos_import_preview`](../routes/modulos_import.py#L180) |
| `/api/modulos/import-validar` | POST | [`api_modulos_import_validar`](../routes/modulos_import.py#L256) |
| `/modulos/import` | GET | [`modulos_import_page`](../routes/modulos_import.py#L166) |

### `routes/obras_sociales.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/obras-sociales/antiguedad` | GET | [`api_os_antiguedad`](../routes/obras_sociales.py#L1359) |
| `/api/obras-sociales/data-status` | GET | [`api_os_data_status`](../routes/obras_sociales.py#L849) |
| `/api/obras-sociales/historico/<tipo>/<int:id_obj>` | GET | [`api_os_historico`](../routes/obras_sociales.py#L870) |
| `/api/obras-sociales/medicos` | GET | [`api_os_medicos`](../routes/obras_sociales.py#L1072) |
| `/api/obras-sociales/pacientes` | GET | [`api_os_pacientes`](../routes/obras_sociales.py#L603) |
| `/api/obras-sociales/productos-rentabilidad` | GET | [`api_os_productos_rentabilidad`](../routes/obras_sociales.py#L1503) |
| `/api/obras-sociales/productos-sin-venta` | GET | [`api_os_productos_sin_venta`](../routes/obras_sociales.py#L426) |
| `/api/obras-sociales/rentabilidad` | GET | [`api_os_rentabilidad`](../routes/obras_sociales.py#L212) |
| `/medico/<int:medico_id>` | GET | [`medico_detalle`](../routes/obras_sociales.py#L2542) |
| `/obras-sociales` | GET | [`os_index`](../routes/obras_sociales.py#L174) |
| `/obras-sociales/antiguedad` | GET | [`os_antiguedad`](../routes/obras_sociales.py#L1351) |
| `/obras-sociales/dashboard` | GET | [`os_dashboard`](../routes/obras_sociales.py#L1672) |
| `/obras-sociales/dispensas` | GET | [`os_dispensas`](../routes/obras_sociales.py#L2395) |
| `/obras-sociales/dispensas/export.xlsx` | GET | [`os_dispensas_export`](../routes/obras_sociales.py#L2456) |
| `/obras-sociales/medicos` | GET | [`os_medicos`](../routes/obras_sociales.py#L830) |
| `/obras-sociales/pacientes` | GET | [`os_pacientes`](../routes/obras_sociales.py#L584) |
| `/obras-sociales/productos-rentabilidad` | GET | [`os_productos_rentabilidad`](../routes/obras_sociales.py#L1479) |
| `/obras-sociales/productos-sin-venta` | GET | [`os_productos_sin_venta`](../routes/obras_sociales.py#L420) |
| `/obras-sociales/rentabilidad` | GET | [`os_rentabilidad`](../routes/obras_sociales.py#L182) |

### `routes/obras_sociales_catalogo.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/obras-sociales/catalogo` | GET | [`obras_sociales_catalogo`](../routes/obras_sociales_catalogo.py#L15) |
| `/obras-sociales/catalogo/<int:observer_id>` | GET | [`obra_social_catalogo_detail`](../routes/obras_sociales_catalogo.py#L79) |

### `routes/observer.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/diagnose-eans` | GET | [`api_diagnose_eans`](../routes/observer.py#L812) |
| `/api/droga/<int:droga_id>/comparar-labs` | GET | [`api_droga_comparar_labs`](../routes/observer.py#L570) |
| `/api/droga/<int:droga_id>/productos` | GET | [`api_droga_productos`](../routes/observer.py#L469) |
| `/api/droga/<int:droga_id>/ventas-mensuales` | GET | [`api_droga_ventas_mensuales`](../routes/observer.py#L419) |
| `/api/mv/refresh/<view_name>` | POST | [`api_mv_refresh`](../routes/observer.py#L756) |
| `/api/mv/status` | GET | [`api_mv_status`](../routes/observer.py#L778) |
| `/api/sync-status` | GET | [`api_sync_status`](../routes/observer.py#L786) |
| `/estadisticas/drogas` | GET | [`estadisticas_drogas`](../routes/observer.py#L265) |
| `/obs/producto/<int:observer_id>/descripcion` | POST | [`obs_producto_descripcion`](../routes/observer.py#L49) |
| `/obs/productos` | GET | [`obs_productos`](../routes/observer.py#L73) |
| `/observer/analizar` | GET/POST | [`observer_analizar`](../routes/observer.py#L968) |
| `/observer/factura/<int:invoice_id>/recepciones` | GET | [`observer_recepciones_factura`](../routes/observer.py#L1240) |
| `/observer/factura/<int:invoice_id>/sync` | POST | [`observer_sync_factura`](../routes/observer.py#L1259) |
| `/observer/pedido-rapido` | GET/POST | [`observer_pedido_rapido`](../routes/observer.py#L1072) |
| `/observer/schema` | GET | [`observer_schema`](../routes/observer.py#L894) |
| `/observer/sql` | GET/POST | [`observer_sql`](../routes/observer.py#L927) |
| `/observer/status` | GET | [`observer_status`](../routes/observer.py#L958) |

### `routes/observer_sync.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/admin/fraccionado-master/run` | POST | [`fraccionado_master_run`](../routes/observer_sync.py#L543) |
| `/admin/observer-config` | POST | [`observer_config_save`](../routes/observer_sync.py#L618) |
| `/admin/observer-match-productos` | POST | [`observer_match_productos`](../routes/observer_sync.py#L524) |
| `/admin/observer-push-render` | POST | [`observer_push_render`](../routes/observer_sync.py#L701) |
| `/admin/observer-sync` | GET | [`observer_sync_panel`](../routes/observer_sync.py#L388) |
| `/admin/observer-sync/<entidad>` | POST | [`observer_sync_run`](../routes/observer_sync.py#L445) |
| `/admin/observer/diagnostico` | GET | [`observer_diagnostico`](../routes/observer_sync.py#L609) |
| `/admin/push-cadencias` | POST | [`push_cadencias`](../routes/observer_sync.py#L669) |
| `/admin/push-productos-master` | POST | [`push_productos_master`](../routes/observer_sync.py#L635) |
| `/admin/sync-audit` | GET | [`sync_audit_panel`](../routes/observer_sync.py#L311) |
| `/api/auto-sync` | POST | [`api_auto_sync`](../routes/observer_sync.py#L738) |
| `/api/auto-sync/status` | GET | [`api_auto_sync_status`](../routes/observer_sync.py#L781) |
| `/producto/<int:producto_id>/desvincular` | POST | [`producto_desvincular`](../routes/observer_sync.py#L727) |
| `/producto/<int:producto_id>/vincular/<int:observer_id>` | POST | [`producto_vincular`](../routes/observer_sync.py#L595) |
| `/productos/sin-vincular` | GET | [`productos_sin_vincular`](../routes/observer_sync.py#L567) |

### `routes/ofertas_bot.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/ofertas-bot` | GET | [`ofertas_bot`](../routes/ofertas_bot.py#L14) |
| `/ofertas-bot/api/<int:oid>` | DELETE | [`ofertas_bot_delete`](../routes/ofertas_bot.py#L127) |
| `/ofertas-bot/api/<int:oid>/toggle` | POST | [`ofertas_bot_toggle`](../routes/ofertas_bot.py#L117) |
| `/ofertas-bot/api/cargadas` | GET | [`ofertas_bot_cargadas`](../routes/ofertas_bot.py#L106) |
| `/ofertas-bot/api/drogas` | GET | [`ofertas_bot_drogas`](../routes/ofertas_bot.py#L30) |
| `/ofertas-bot/api/guardar` | POST | [`ofertas_bot_guardar`](../routes/ofertas_bot.py#L75) |
| `/ofertas-bot/api/laboratorios` | GET | [`ofertas_bot_labs`](../routes/ofertas_bot.py#L19) |
| `/ofertas-bot/api/productos` | GET | [`ofertas_bot_productos`](../routes/ofertas_bot.py#L48) |

### `routes/ofertas_import.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/ofertas/import-candidatos` | POST | [`api_ofertas_import_candidatos`](../routes/ofertas_import.py#L1178) |
| `/api/ofertas/import-candidatos-bulk` | POST | [`api_ofertas_import_candidatos_bulk`](../routes/ofertas_import.py#L1036) |
| `/api/ofertas/import-guardar` | POST | [`api_ofertas_import_guardar`](../routes/ofertas_import.py#L1196) |
| `/api/ofertas/import-ia` | POST | [`api_ofertas_import_ia`](../routes/ofertas_import.py#L699) |
| `/api/ofertas/import-match-ia` | POST | [`api_ofertas_import_match_ia`](../routes/ofertas_import.py#L1069) |
| `/api/ofertas/import-preview` | POST | [`api_ofertas_import_preview`](../routes/ofertas_import.py#L661) |
| `/api/ofertas/import-validar` | POST | [`api_ofertas_import_validar`](../routes/ofertas_import.py#L745) |
| `/api/ofertas/import/lab/<int:lab_id>/productos` | GET | [`api_ofertas_lab_productos`](../routes/ofertas_import.py#L625) |
| `/ofertas/import` | GET | [`ofertas_import_page`](../routes/ofertas_import.py#L580) |

### `routes/panel.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/panel` | GET | [`panel_dueno`](../routes/panel.py#L21) |
| `/panel/api/resumen` | GET | [`panel_api_resumen`](../routes/panel.py#L28) |

### `routes/partners.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/partners/create` | POST | [`api_partners_create`](../routes/partners.py#L139) |
| `/api/partners/search` | GET | [`api_partners_search`](../routes/partners.py#L94) |
| `/api/partners/top` | GET | [`api_partners_top`](../routes/partners.py#L184) |

### `routes/pedido_prueba.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/pedido-prueba/calcular` | POST | [`api_pedido_prueba_calcular`](../routes/pedido_prueba.py#L124) |
| `/api/pedido-prueba/export-xlsx` | POST | [`api_pedido_prueba_export_xlsx`](../routes/pedido_prueba.py#L491) |
| `/api/pedido-prueba/flag/<int:producto_id>` | POST | [`api_pedido_prueba_flag`](../routes/pedido_prueba.py#L443) |
| `/api/pedido-prueba/historico/<int:producto_id>` | GET | [`api_pedido_prueba_historico`](../routes/pedido_prueba.py#L100) |
| `/pedido/prueba` | GET | [`pedido_prueba`](../routes/pedido_prueba.py#L84) |

### `routes/pedidos.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/pedido/nuevo` | GET | [`pedido_nuevo`](../routes/pedidos.py#L33) |

### `routes/pedidos_log.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/pedidos/log` | GET | [`pedidos_log`](../routes/pedidos_log.py#L28) |

### `routes/planificacion_compras.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/planificacion/compras-mes` | GET | [`planificacion_compras_mes`](../routes/planificacion_compras.py#L36) |

### `routes/plantillas.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/partner/<tipo>/<int:id>/plantillas` | GET | [`plantillas_list`](../routes/plantillas.py#L82) |
| `/partner/<tipo>/<int:id>/plantillas/<int:pid>/delete` | POST | [`plantilla_delete`](../routes/plantillas.py#L184) |
| `/partner/<tipo>/<int:id>/plantillas/<int:pid>/duplicate` | POST | [`plantilla_duplicate`](../routes/plantillas.py#L198) |
| `/partner/<tipo>/<int:id>/plantillas/<int:pid>/save` | POST | [`plantilla_save`](../routes/plantillas.py#L152) |
| `/partner/<tipo>/<int:id>/plantillas/<pid>` | GET | [`plantilla_editor`](../routes/plantillas.py#L126) |
| `/partner/<tipo>/<int:id>/plantillas/new` | POST | [`plantilla_create`](../routes/plantillas.py#L99) |

### `routes/procesos.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/consulta-stock` | GET | [`consulta_stock`](../routes/procesos.py#L134) |
| `/consulta-stock/armar-pedido` | POST | [`consulta_stock_armar_pedido`](../routes/procesos.py#L424) |
| `/consulta-stock/export-xls` | POST | [`consulta_stock_export_xls`](../routes/procesos.py#L314) |
| `/consulta-stock/iniciar` | POST | [`consulta_stock_iniciar`](../routes/procesos.py#L150) |
| `/consulta-stock/resultado/<uid>` | GET | [`consulta_stock_resultado`](../routes/procesos.py#L489) |
| `/consulta-stock/sync-stock` | POST | [`consulta_stock_sync_stock`](../routes/procesos.py#L284) |
| `/pedido/<int:pedido_id>/enviar-a-proceso` | POST | [`pedido_enviar_a_proceso`](../routes/procesos.py#L867) |
| `/proceso/<int:proceso_id>` | GET | [`proceso_detail`](../routes/procesos.py#L624) |
| `/proceso/<int:proceso_id>/cerrar` | POST | [`proceso_cerrar`](../routes/procesos.py#L834) |
| `/proceso/<int:proceso_id>/delete` | POST | [`proceso_delete`](../routes/procesos.py#L936) |
| `/proceso/<int:proceso_id>/link-factura` | POST | [`proceso_link_factura`](../routes/procesos.py#L784) |
| `/proceso/<int:proceso_id>/link-pedido` | POST | [`proceso_link_pedido`](../routes/procesos.py#L762) |
| `/proceso/<int:proceso_id>/notas` | POST | [`proceso_notas`](../routes/procesos.py#L821) |
| `/proceso/<int:proceso_id>/paso/<paso>` | POST | [`proceso_marcar_paso`](../routes/procesos.py#L725) |
| `/proceso/<int:proceso_id>/paso/<paso>/undo` | POST | [`proceso_desmarcar_paso`](../routes/procesos.py#L740) |
| `/proceso/<int:proceso_id>/reabrir` | POST | [`proceso_reabrir`](../routes/procesos.py#L847) |
| `/proceso/<int:proceso_id>/snapshot-analisis` | POST | [`proceso_snapshot_analisis`](../routes/procesos.py#L803) |
| `/procesos` | GET | [`procesos_list`](../routes/procesos.py#L87) |
| `/procesos/crear` | POST | [`proceso_crear`](../routes/procesos.py#L571) |

### `routes/producto_flags.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/producto-nombre` | GET | [`api_producto_nombre`](../routes/producto_flags.py#L286) |
| `/api/producto/config-bulk` | POST | [`api_producto_config_bulk`](../routes/producto_flags.py#L480) |
| `/api/producto/configurados` | GET | [`api_producto_configurados`](../routes/producto_flags.py#L441) |
| `/api/producto/oferta` | POST | [`api_producto_oferta_guardar`](../routes/producto_flags.py#L392) |
| `/api/producto/presentacion` | GET | [`api_producto_presentacion`](../routes/producto_flags.py#L296) |
| `/api/producto/presentacion` | POST | [`api_producto_presentacion_guardar`](../routes/producto_flags.py#L353) |
| `/api/producto/presentacion-bulk` | POST | [`api_producto_presentacion_bulk`](../routes/producto_flags.py#L563) |
| `/productos/flags` | GET | [`producto_flags_list`](../routes/producto_flags.py#L165) |
| `/productos/flags/<int:flag_id>/eliminar` | POST | [`producto_flags_eliminar`](../routes/producto_flags.py#L275) |
| `/productos/flags/asignar` | POST | [`producto_flags_asignar`](../routes/producto_flags.py#L226) |
| `/productos/presentaciones` | GET | [`productos_presentaciones`](../routes/producto_flags.py#L84) |

### `routes/productos.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/catalogacion/backfill` | POST | [`api_catalogacion_backfill`](../routes/productos.py#L957) |
| `/api/catalogacion/stats` | GET | [`api_catalogacion_stats`](../routes/productos.py#L976) |
| `/api/match-dimensional` | GET | [`api_match_dimensional`](../routes/productos.py#L897) |
| `/api/precios/<ean>` | GET | [`api_precios_historico`](../routes/productos.py#L1139) |
| `/api/producto-resolver` | GET | [`api_producto_resolver`](../routes/productos.py#L1083) |
| `/api/producto/<int:prod_id>/atributos` | GET/POST | [`api_producto_atributos`](../routes/productos.py#L1008) |
| `/api/producto/<int:prod_id>/codigos` | GET/POST | [`api_producto_codigos`](../routes/productos.py#L790) |
| `/api/producto/<int:prod_id>/codigos/<int:cb_id>` | DELETE/PATCH | [`api_producto_codigo_modificar`](../routes/productos.py#L842) |
| `/api/producto/<int:prod_id>/recatalogar` | POST | [`api_producto_recatalogar`](../routes/productos.py#L868) |
| `/api/productos` | GET | [`api_productos`](../routes/productos.py#L160) |
| `/catalogacion` | GET | [`catalogacion_panel`](../routes/productos.py#L708) |
| `/precios/<ean>` | GET | [`precios_historico`](../routes/productos.py#L1066) |
| `/producto/<int:prod_id>` | GET | [`producto_detalle`](../routes/productos.py#L712) |
| `/producto/<int:prod_id>/delete` | POST | [`producto_delete`](../routes/productos.py#L696) |
| `/producto/<int:prod_id>/edit` | POST | [`producto_edit`](../routes/productos.py#L553) |
| `/producto/<int:prod_id>/fusionar/<int:target_id>` | POST | [`producto_fusionar`](../routes/productos.py#L98) |
| `/producto/<int:prod_id>/laboratorio` | POST | [`producto_set_laboratorio`](../routes/productos.py#L543) |
| `/producto/<int:prod_id>/marcar-verificado` | POST | [`producto_marcar_verificado`](../routes/productos.py#L133) |
| `/producto/create` | POST | [`producto_create`](../routes/productos.py#L670) |
| `/producto/edit-by-barcode` | POST | [`producto_edit_by_barcode`](../routes/productos.py#L593) |
| `/producto/materializar/<int:observer_id>` | POST | [`producto_materializar`](../routes/productos.py#L463) |
| `/producto/nuevo` | GET/POST | [`producto_nuevo`](../routes/productos.py#L622) |
| `/productos` | GET | [`productos_list`](../routes/productos.py#L144) |
| `/productos/repo-alertas` | GET | [`productos_repo_alertas`](../routes/productos.py#L15) |
| `/productos/verificar-nuevos` | GET | [`productos_verificar_nuevos`](../routes/productos.py#L57) |

### `routes/productos_pendientes.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/productos/pendientes-revision/buscar-catalogo` | GET | [`api_buscar_catalogo_pend`](../routes/productos_pendientes.py#L744) |
| `/productos/pendientes-revision` | GET | [`productos_pendientes_revision`](../routes/productos_pendientes.py#L300) |
| `/productos/pendientes-revision/<int:item_id>/aplicar-ia` | POST | [`pendiente_aplicar_ia`](../routes/productos_pendientes.py#L959) |
| `/productos/pendientes-revision/<int:item_id>/crear-nuevo` | POST | [`pendiente_crear_nuevo`](../routes/productos_pendientes.py#L574) |
| `/productos/pendientes-revision/<int:item_id>/descartar` | POST | [`pendiente_descartar`](../routes/productos_pendientes.py#L729) |
| `/productos/pendientes-revision/<int:item_id>/vincular` | POST | [`pendiente_vincular`](../routes/productos_pendientes.py#L635) |
| `/productos/pendientes-revision/analizar-ia` | POST | [`pendiente_analizar_ia`](../routes/productos_pendientes.py#L844) |
| `/productos/pendientes-revision/aplicar-ia-bulk` | POST | [`pendiente_aplicar_ia_bulk`](../routes/productos_pendientes.py#L1053) |
| `/productos/pendientes-revision/asignar-contexto` | POST | [`pendiente_asignar_contexto`](../routes/productos_pendientes.py#L1190) |
| `/productos/pendientes-revision/borrar-seleccionados` | POST | [`pendiente_borrar_seleccionados`](../routes/productos_pendientes.py#L1158) |
| `/productos/pendientes-revision/bulk-vincular` | POST | [`pendiente_bulk_vincular`](../routes/productos_pendientes.py#L469) |
| `/productos/pendientes-revision/estimar-costo-ia` | GET | [`pendiente_estimar_costo_ia`](../routes/productos_pendientes.py#L813) |
| `/productos/pendientes-revision/reaplicar-ofertas` | POST | [`pendiente_reaplicar_ofertas`](../routes/productos_pendientes.py#L1246) |

### `routes/providers.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/invoice/probe-create` | POST | [`invoice_probe_create`](../routes/providers.py#L127) |
| `/api/proveedor/<int:provider_id>/proximo-cierre` | GET | [`api_proximo_cierre`](../routes/providers.py#L757) |
| `/api/proveedores/drog-activas` | GET | [`api_drog_activas`](../routes/providers.py#L27) |
| `/api/provider/<int:provider_id>/activa-ped` | PATCH | [`api_provider_activa_ped`](../routes/providers.py#L42) |
| `/api/provider/<int:provider_id>/descuento-sin-transfer` | PATCH | [`api_provider_descuento_sin_transfer`](../routes/providers.py#L56) |
| `/api/provider/<int:provider_id>/folder-file/stage` | POST | [`provider_folder_file_stage`](../routes/providers.py#L233) |
| `/api/provider/<int:provider_id>/folder-files` | GET | [`provider_folder_files`](../routes/providers.py#L584) |
| `/api/provider/<int:provider_id>/invoices` | GET | [`api_provider_invoices`](../routes/providers.py#L77) |
| `/invoice/<int:invoice_id>/delete` | POST | [`delete_invoice`](../routes/providers.py#L491) |
| `/provider/<int:provider_id>/delete` | POST | [`provider_delete`](../routes/providers.py#L454) |
| `/provider/<int:provider_id>/edit` | POST | [`provider_edit`](../routes/providers.py#L422) |
| `/provider/<int:provider_id>/horarios` | GET/POST | [`provider_horarios`](../routes/providers.py#L709) |
| `/provider/<int:provider_id>/invoices` | GET | [`provider_invoices`](../routes/providers.py#L478) |
| `/provider/<int:provider_id>/mappings` | GET | [`provider_mappings`](../routes/providers.py#L526) |
| `/provider/<int:provider_id>/mappings/<int:mapping_id>/delete` | POST | [`delete_mapping`](../routes/providers.py#L538) |
| `/provider/<int:provider_id>/mappings/delete-all` | POST | [`delete_all_mappings`](../routes/providers.py#L574) |
| `/provider/<int:provider_id>/parser-preview` | POST | [`provider_parser_preview`](../routes/providers.py#L344) |
| `/provider/<int:provider_id>/parser-preview-saved` | POST | [`provider_parser_preview_saved`](../routes/providers.py#L259) |
| `/provider/<int:provider_id>/parser-preview/export` | POST | [`provider_parser_preview_export`](../routes/providers.py#L206) |
| `/provider/<int:provider_id>/plantilla` | GET/POST | [`provider_plantilla`](../routes/providers.py#L612) |
| `/provider/create` | POST | [`provider_create_manual`](../routes/providers.py#L389) |
| `/provider/create-from-peek` | POST | [`provider_create_from_peek`](../routes/providers.py#L174) |
| `/provider/peek` | POST | [`provider_peek`](../routes/providers.py#L102) |
| `/providers` | GET | [`providers_list`](../routes/providers.py#L297) |
| `/providers/activos` | GET/POST | [`providers_activos`](../routes/providers.py#L547) |

### `routes/purchase.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/pedido/<int:pedido_id>/indicadores` | GET | [`api_pedido_indicadores`](../routes/purchase.py#L1061) |
| `/api/pedido/<int:pedido_id>/vincular-observer` | POST | [`api_pedido_vincular_observer`](../routes/purchase.py#L1359) |
| `/order/<int:pedido_id>` | GET | [`order_detail`](../routes/purchase.py#L1586) |
| `/order/<int:pedido_id>/analizar-ia` | POST | [`order_analizar_ia`](../routes/purchase.py#L2360) |
| `/order/<int:pedido_id>/canal` | POST | [`order_set_canal`](../routes/purchase.py#L2422) |
| `/order/<int:pedido_id>/clear-state` | POST | [`order_clear_state`](../routes/purchase.py#L2034) |
| `/order/<int:pedido_id>/confirmar` | POST | [`order_confirmar`](../routes/purchase.py#L2000) |
| `/order/<int:pedido_id>/delete` | POST | [`order_delete`](../routes/purchase.py#L1414) |
| `/order/<int:pedido_id>/export-plantilla/<int:plantilla_id>` | POST | [`order_export_plantilla_unified`](../routes/purchase.py#L2476) |
| `/order/<int:pedido_id>/export-prov-plantilla` | POST | [`order_export_prov_plantilla`](../routes/purchase.py#L2287) |
| `/order/<int:pedido_id>/export/<fmt>` | GET | [`order_export_file`](../routes/purchase.py#L1428) |
| `/order/<int:pedido_id>/export/<step>/<fmt>` | POST | [`order_export`](../routes/purchase.py#L2606) |
| `/order/<int:pedido_id>/export/plantilla` | POST | [`order_export_plantilla`](../routes/purchase.py#L2218) |
| `/order/<int:pedido_id>/modules-template` | GET | [`order_modules_template`](../routes/purchase.py#L2091) |
| `/order/<int:pedido_id>/mostrar-hasta` | POST | [`order_mostrar_hasta`](../routes/purchase.py#L1386) |
| `/order/<int:pedido_id>/parse-modules` | POST | [`order_parse_modules`](../routes/purchase.py#L2158) |
| `/order/<int:pedido_id>/save-module-matches` | POST | [`order_save_module_matches`](../routes/purchase.py#L2050) |
| `/order/<int:pedido_id>/save-packs` | POST | [`order_save_packs`](../routes/purchase.py#L2185) |
| `/order/<int:pedido_id>/save-state` | POST | [`order_save_state`](../routes/purchase.py#L2015) |
| `/orders` | GET | [`orders_list`](../routes/purchase.py#L932) |
| `/purchase` | GET | [`purchase_index`](../routes/purchase.py#L324) |
| `/purchase/analyze` | POST | [`purchase_analyze`](../routes/purchase.py#L330) |
| `/purchase/batch` | POST | [`purchase_batch`](../routes/purchase.py#L391) |
| `/purchase/export/<uid>/<fmt>` | POST | [`purchase_export`](../routes/purchase.py#L499) |
| `/purchase/processed` | GET | [`purchase_processed`](../routes/purchase.py#L363) |
| `/purchase/results/<uid>` | GET | [`purchase_results`](../routes/purchase.py#L431) |
| `/purchase/save-order/<uid>` | POST | [`purchase_save_order`](../routes/purchase.py#L702) |
| `/purchase/suggest` | GET | [`purchase_suggest`](../routes/purchase.py#L793) |
| `/purchase/suggest/create-order` | POST | [`purchase_suggest_create_order`](../routes/purchase.py#L870) |

### `routes/reparto.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/pedido/obs-presets` | GET | [`api_pedido_obs_presets`](../routes/reparto.py#L534) |
| `/api/pedido/obs-presets` | POST | [`api_pedido_obs_presets_crear`](../routes/reparto.py#L547) |
| `/api/reparto/alertas-cadetes` | GET | [`api_reparto_alertas_cadetes`](../routes/reparto.py#L1607) |
| `/api/reparto/chat/dm/<int:conv_id>/mensajes` | GET | [`api_reparto_chat_dm_mensajes`](../routes/reparto.py#L1470) |
| `/api/reparto/chat/dm/<int:conv_id>/responder` | POST | [`api_reparto_chat_dm_responder`](../routes/reparto.py#L1528) |
| `/api/reparto/chat/dm/<int:conv_id>/vincular-cadete` | POST | [`api_reparto_chat_dm_vincular`](../routes/reparto.py#L1569) |
| `/api/reparto/chat/grupo/mensajes` | GET | [`api_reparto_chat_grupo_mensajes`](../routes/reparto.py#L1444) |
| `/api/reparto/chat/grupo/responder` | POST | [`api_reparto_chat_grupo_responder`](../routes/reparto.py#L1496) |
| `/api/reparto/chat/resumen` | GET | [`api_reparto_chat_resumen`](../routes/reparto.py#L1396) |
| `/api/reparto/pedido/<int:pid>/actualizar` | POST | [`reparto_actualizar_pedido`](../routes/reparto.py#L2280) |
| `/cadetes` | GET | [`cadetes_panel`](../routes/reparto.py#L381) |
| `/cadetes` | POST | [`cadetes_guardar`](../routes/reparto.py#L408) |
| `/cadetes/<int:cid>/delete` | POST | [`cadetes_eliminar`](../routes/reparto.py#L460) |
| `/cadetes/api` | GET | [`cadetes_api`](../routes/reparto.py#L388) |
| `/reparto` | GET | [`reparto_panel`](../routes/reparto.py#L478) |
| `/reparto/api` | GET | [`reparto_api`](../routes/reparto.py#L499) |
| `/reparto/armado` | GET | [`reparto_armado`](../routes/reparto.py#L486) |
| `/reparto/cadete/<int:cid>/liquidar` | POST | [`reparto_liquidar_cadete`](../routes/reparto.py#L1803) |
| `/reparto/cadete/<token>` | GET | [`cadete_vista`](../routes/reparto.py#L2121) |
| `/reparto/cadete/<token>/api` | GET | [`cadete_api`](../routes/reparto.py#L2133) |
| `/reparto/cadete/<token>/pedido/<int:pid>/cobrar` | POST | [`cadete_cobrar`](../routes/reparto.py#L2177) |
| `/reparto/cadete/<token>/pedido/<int:pid>/entregar` | POST | [`cadete_entregar`](../routes/reparto.py#L2160) |
| `/reparto/pedido` | POST | [`reparto_crear_pedido`](../routes/reparto.py#L571) |
| `/reparto/pedido/<int:pid>/asignar` | POST | [`reparto_asignar`](../routes/reparto.py#L1754) |
| `/reparto/pedido/<int:pid>/cobrar` | POST | [`reparto_cobrar`](../routes/reparto.py#L1789) |
| `/reparto/pedido/<int:pid>/delete` | POST | [`reparto_eliminar`](../routes/reparto.py#L1849) |
| `/reparto/pedido/<int:pid>/estado` | POST | [`reparto_estado`](../routes/reparto.py#L1768) |
| `/reparto/pedido/<int:pid>/liquidar` | POST | [`reparto_liquidar_pedido`](../routes/reparto.py#L1832) |
| `/reparto/pedido/<int:pid>/publicar` | POST | [`reparto_pedido_publicar`](../routes/reparto.py#L1668) |
| `/reparto/pedido/<int:pid>/ticket` | GET | [`reparto_ticket_data`](../routes/reparto.py#L2029) |
| `/reparto/pedido/<int:pid>/ticket-pdf` | GET | [`reparto_ticket_pdf`](../routes/reparto.py#L1865) |
| `/reparto/planilla` | GET | [`reparto_planilla`](../routes/reparto.py#L2195) |
| `/reparto/ruta/<int:rid>/export` | GET | [`reparto_export`](../routes/reparto.py#L2101) |
| `/reparto/ruta/<int:rid>/optimizar` | POST | [`reparto_optimizar`](../routes/reparto.py#L2082) |
| `/rutas` | GET | [`rutas_panel`](../routes/reparto.py#L308) |
| `/rutas` | POST | [`rutas_guardar`](../routes/reparto.py#L330) |
| `/rutas/<int:rid>/delete` | POST | [`rutas_eliminar`](../routes/reparto.py#L367) |
| `/rutas/api` | GET | [`rutas_api`](../routes/reparto.py#L315) |
| `/rutas/cargar-distritos` | POST | [`rutas_cargar_distritos`](../routes/reparto.py#L360) |
| `/telegram/cadetes/setup-webhook` | POST | [`reparto_telegram_setup_webhook`](../routes/reparto.py#L1360) |
| `/telegram/cadetes/webhook` | POST | [`reparto_telegram_cadetes_webhook`](../routes/reparto.py#L808) |
| `/whatsapp/grupo/setup-webhook` | POST | [`reparto_whatsapp_setup_webhook`](../routes/reparto.py#L795) |
| `/whatsapp/grupo/webhook` | POST | [`reparto_whatsapp_grupo_webhook`](../routes/reparto.py#L677) |

### `routes/sucursales.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/sucursales` | GET | [`sucursales_list`](../routes/sucursales.py#L18) |
| `/sucursales/<int:sid>/delete` | POST | [`sucursales_delete`](../routes/sucursales.py#L65) |
| `/sucursales/guardar` | POST | [`sucursales_guardar`](../routes/sucursales.py#L32) |

### `routes/tienda_admin.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/admin/tienda/config` | GET/POST | [`admin_tienda_config`](../routes/tienda_admin.py#L56) |
| `/admin/tienda/imagenes` | GET | [`admin_tienda_imagenes`](../routes/tienda_admin.py#L120) |
| `/admin/tienda/imagenes/<int:oid>/delete` | POST | [`admin_tienda_imagenes_delete`](../routes/tienda_admin.py#L224) |
| `/admin/tienda/imagenes/<int:oid>/toggle-destacado` | POST | [`admin_tienda_imagenes_toggle_destacado`](../routes/tienda_admin.py#L209) |
| `/admin/tienda/imagenes/upload` | POST | [`admin_tienda_imagenes_upload`](../routes/tienda_admin.py#L155) |
| `/admin/tienda/rubros` | GET/POST | [`admin_tienda_rubros`](../routes/tienda_admin.py#L72) |
| `/uploads/tienda/<path:filename>` | GET | [`tienda_upload_file`](../routes/tienda_admin.py#L48) |

### `routes/tienda_publica.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/tienda` | GET | [`tienda_home`](../routes/tienda_publica.py#L152) |
| `/tienda/catalogo` | GET | [`tienda_catalogo`](../routes/tienda_publica.py#L175) |
| `/tienda/pedir` | GET | [`tienda_pedir`](../routes/tienda_publica.py#L217) |
| `/tienda/producto/<int:oid>` | GET | [`tienda_producto`](../routes/tienda_publica.py#L200) |

### `routes/tipos_pedido.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/config/tipos-pedido` | GET | [`tipos_pedido_list`](../routes/tipos_pedido.py#L128) |
| `/config/tipos-pedido/<slug>/edit` | GET/POST | [`tipos_pedido_edit`](../routes/tipos_pedido.py#L140) |
| `/config/tipos-pedido/<slug>/probar` | POST | [`tipos_pedido_probar`](../routes/tipos_pedido.py#L213) |
| `/config/tipos-pedido/<slug>/restaurar` | POST | [`tipos_pedido_restaurar`](../routes/tipos_pedido.py#L268) |
| `/config/tipos-pedido/<slug>/toggle` | POST | [`tipos_pedido_toggle`](../routes/tipos_pedido.py#L201) |
| `/config/tipos-pedido/sim-producto` | GET | [`tipos_pedido_sim_producto`](../routes/tipos_pedido.py#L236) |

### `routes/transferencias.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/transferencias` | GET | [`transferencias`](../routes/transferencias.py#L26) |
| `/transferencias/export` | GET | [`transferencias_export`](../routes/transferencias.py#L34) |

### `routes/vademecum.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/api/vademecum/detail` | GET | [`api_vademecum_detail`](../routes/vademecum.py#L29) |
| `/api/vademecum/save` | POST | [`api_vademecum_save`](../routes/vademecum.py#L42) |
| `/api/vademecum/search` | GET | [`api_vademecum_search`](../routes/vademecum.py#L16) |
| `/vademecum` | GET | [`vademecum_index`](../routes/vademecum.py#L12) |

### `routes/whatsapp.py`

| Ruta | Métodos | Función |
|---|---|---|
| `/whatsapp/reenganche` | GET | [`whatsapp_reenganche`](../routes/whatsapp.py#L57) |
| `/whatsapp/webhook` | GET | [`whatsapp_webhook_get`](../routes/whatsapp.py#L24) |
| `/whatsapp/webhook` | POST | [`whatsapp_webhook_post`](../routes/whatsapp.py#L34) |

## Services

| Módulo | Qué hace |
|---|---|
| [`cadencias_analisis.py`](../services/cadencias_analisis.py) | Análisis en prosa del snapshot de cadencias por laboratorio (Claude). |
| [`calculo_pedido.py`](../services/calculo_pedido.py) | Motor unificado de cálculo de cantidad a pedir por tipo de pedido. |
| [`comparativa_ventas.py`](../services/comparativa_ventas.py) | Comparativa de ventas semanales entre sucursales (Pieri vs Badia). |
| [`compartido_sync.py`](../services/compartido_sync.py) | Sync peer-to-peer de archivos compartidos (sin hub). |
| [`cuenta_corriente.py`](../services/cuenta_corriente.py) | Cálculo único de movimientos y saldo de la cuenta corriente de proveedores. |
| [`dashboard_snapshot.py`](../services/dashboard_snapshot.py) | Refresco del snapshot product_analytics para el dashboard. |
| [`descuentos.py`](../services/descuentos.py) | Lógica de descuentos para el flujo de compra rápida. |
| [`eventos_sla.py`](../services/eventos_sla.py) | Helper para registrar eventos SLA (Diego 2026-06-22). |
| [`factura_ia.py`](../services/factura_ia.py) | Extracción de facturas de droguerías a JSON estructurado vía Claude (Vision). |
| [`farmacia.py`](../services/farmacia.py) | Resolución de la farmacia operativa (ObServer `id_farmacia`). |
| [`flags.py`](../services/flags.py) | Source of truth de la presentación de flags (comportamientos excepcionales). |
| [`horarios.py`](../services/horarios.py) | Helper para horarios de reparto por droguería. |
| [`informes_bot.py`](../services/informes_bot.py) | Informes proactivos vía Telegram (mismo bot que el asistente). |
| [`llm_matcher.py`](../services/llm_matcher.py) | LLM matcher para items en queue de pendientes de revisión. |
| [`mercado_drogas.py`](../services/mercado_drogas.py) | Mapa de mercado por droga — materializa (en memoria) la inteligencia de |
| [`modulos_ia.py`](../services/modulos_ia.py) | Extracción de módulos de descuento (packs de laboratorio) a JSON estructurado |
| [`ofertas_ia.py`](../services/ofertas_ia.py) | Extracción de catálogos de ofertas a JSON estructurado vía Claude. |
| [`os_inferida.py`](../services/os_inferida.py) | Consultas de OS inferida por cliente y precio estimado con cobertura OS. |
| [`pedido_analisis.py`](../services/pedido_analisis.py) | Análisis IA del resumen final de un pedido (Claude Haiku 4.5). |
| [`pedido_estacional.py`](../services/pedido_estacional.py) | Calculo de sugerido con ajuste estacional para /pedido/prueba. |
| [`producto_metrics.py`](../services/producto_metrics.py) | Source of truth UNICO de las metricas de venta/stock de un producto. |
| [`referencia_ia.py`](../services/referencia_ia.py) | Análisis IA de los informes de referencia de mercado (portfolio líder vs |
| [`referencia_websearch.py`](../services/referencia_websearch.py) | Recopilación de marcas estrella de un laboratorio vía web search de Claude. |
| [`reparto.py`](../services/reparto.py) | Asignación de pedidos a rutas de reparto (v1: cuadrantes N/S/E/O). |
| [`reparto_sla_cron.py`](../services/reparto_sla_cron.py) | Cron interno para los SLA del flujo de reparto. |
| [`transferencias.py`](../services/transferencias.py) | Análisis de transferencias entre sucursales (comparador N-way por par). |
| [`ventas_comparativa.py`](../services/ventas_comparativa.py) | Comparación de ventas año contra año, agregada por mes. |
| [`ventas_vendedor.py`](../services/ventas_vendedor.py) | Estadísticas de ventas por vendedor (operador del POS). |

## Parsers

| Módulo | Qué hace |
|---|---|
| [`20_de_junio.py`](../parsers/20_de_junio.py) | Parser para: Droguería 20 de Junio |
| [`bernabo_ofertas.py`](../parsers/bernabo_ofertas.py) | Parser para Excel de ofertas Bernabó (Venta Directa / Venta Directa Enero). |
| [`droguer_a_kellerhoff_s_a.py`](../parsers/droguer_a_kellerhoff_s_a.py) | Parser auto-generado para: DROGUERÍA KELLERHOFF S.A |
| [`laboratorios_bernabo_s_a.py`](../parsers/laboratorios_bernabo_s_a.py) | Parser para: LABORATORIOS BERNABO S.A. |
| [`modulos_xlsx.py`](../parsers/modulos_xlsx.py) | Parser para Excel de módulos de laboratorio. |
| [`ofertas_xlsx.py`](../parsers/ofertas_xlsx.py) | Parser genérico de Excel de ofertas. |
| [`pharmos.py`](../parsers/pharmos.py) | Parser para: PHARMOS S.A. |
| [`sales_history.py`](../parsers/sales_history.py) | Parser para informe 'Evolución de ventas por producto' de ObServer Gestión. |
| [`sales_history_html.py`](../parsers/sales_history_html.py) | Parser para: rptEvolucionDeVentas.html (reporte HTML del ERP) |
| [`sales_history_xls.py`](../parsers/sales_history_xls.py) | Parser para informe 'Evolución de ventas por producto' de ObServer Gestión (formato Excel). |
| [`vademecum.py`](../parsers/vademecum.py) | Scraper para PR Vademécum Argentina (ar.prvademecum.com). |

