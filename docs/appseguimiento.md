# App Seguimiento — cómo sigo en casa

Estado al 2026-04-28 al cerrar la sesión en la oficina. Esta nota es para
arrancar la sesión siguiente sin volver a leer todo el chat.

## Cómo arrancar la app en casa

1. Levantar Docker: `docker-compose up -d`. Esperar 5-10s a que `db` esté healthy y `web` levante.
2. Restaurar el dump de datos:
   ```bash
   gunzip -c dumps/seed_pedidos_dia.sql.gz | docker-compose exec -T db psql -U postgres -d farmacia
   ```
   Si la DB está limpia (post `init_db`), entra directo. Si tiene data vieja, primero TRUNCATE — ver `dumps/README.md`.
3. Login con user `pedidos` / pass `pedidos123` (debe cambiar al primer ingreso).
   - Admin sigue siendo `admin` / `cambiar123`.
4. Pantalla principal del flujo: `/compras/dia` → "Armar pedido →" en alguna droguería.

## Lo hecho hoy (resumen)

- Rol `pedidos` con seed automático + redirect a `/compras/dia` post-login.
- Sidebar oculto + guard global que bouncea cualquier path fuera del flujo.
- Pantalla `/compras/labs-drogerias` con matriz de checkboxes lab × drog (filtro multi-token, ajax toggle).
- Limpieza de 5 labs duplicados sin ventas.
- Panel sticky con gráfico (Año rico + Mes simple) que se actualiza al click en una fila.
- 3 series en Mes: **Salidas** (verde, ventas), **Pedido** (amber, `pedido_emitido`), **Entradas** (azul, facturas).
- Filtros tokenizados: producto + lab + "Solo venta libre" + "Solo con sugerencia".
- Buscador "+ Agregar producto…" con autocomplete.
- Modelo `PedidoEmitido` + `PedidoEmitidoItem` (con campos para 2 vías de recepción: `cantidad_revisada_op` manual + `cantidad_confirmada_obs` automática).
- Pantalla `/pedidos-emitidos` (lista) y `/pedidos-emitidos/<id>` (recepción row-by-row).
- Sección "🔁 Pendientes anteriores" en el armado, con chips clickeables que reagrega productos.
- Sugerencia de mínimo (Subir/Bajar/OK) usando `purchase_engine.analyze_product` (estacionalidad + prorrateo + crónicos).
- Badge "Prom Vtas" para crónicos (CV<0.30 y vende 8/11 meses).
- Datos demo: 2 pedidos emitidos con mix RECIBIDO/NO_VINO/PENDIENTE.

## Para retocar gráficos en casa

Foco que pediste: **ver cómo queda el chart con la recepción ya hecha.**

Caminos rápidos:

1. Login pedidos → `/compras/dia/armar?prov=<id>` → click en cualquier producto del pedido demo #1 → mirar el chart de Mes (debería tener barras amber "Pedido" en el día -5 y posibles barras azules "Entradas" si hay facturas reales con ese EAN).
2. `/pedidos-emitidos/1` → modificar la revisión (ej. marcar otra como NO_VINO) → guardar → ver cómo cambian los chips del armado.
3. Crear un pedido nuevo desde `/compras/dia/armar`, marcar recepción → ver el ciclo entero.

## Pendientes (orden sugerido)

1. **Estilo del chart de Mes**: las 3 series como barras agrupadas pueden quedar muy comprimidas con 30 días. Probar:
   - Pedido y Entradas como puntos/iconos sobre la línea de Salidas.
   - O barras apiladas con colores distintos (Salidas neutro, Pedido y Entradas con highlight).
2. **Endpoint para cargar confirmación Observer** desde un export CSV/XLSX (botón "Importar ingreso" en `/pedidos-emitidos/<id>`).
3. **Plantilla específica de exportación "pedidos día"** por droguería (lo conversamos: tipo='pedidos_dia' en `PlantillaExportacion`).
4. **Botón "Aplicar sugerencias"** que persista los nuevos mínimos (cuando exista push a Observer; por ahora podría guardar local).
5. **Contador en header** "X subir · Y bajar" + ordenar por sugerencia urgente arriba.
6. **Commit + push** de todo lo de hoy (lab×drog, recepción 2-vías, gráfico mixto, sugerencias, dump). No commiteado todavía.
7. **Gráfico de médicos** (extenso, dejado para después). Pensar pantalla / chart con métricas
   por médico: top prescriptores, evolución temporal, ranking por OS, etc. Datos en `obs_ventas_detalle`
   (`medico_observer`, `medico_matricula_observer`) — joinea con `obs_medicos`. Es trabajo grande,
   abordar como feature aparte cuando los pendientes de Compra del día estén estables.

## Archivos clave que toqué

- `database.py` — modelos `LaboratorioDrogueria`, `PedidoEmitido`, `PedidoEmitidoItem` + columnas extra.
- `auth.py` + `routes/auth_routes.py` — rol `pedidos`, seed, guard global, redirect cambio password.
- `routes/compras_dia.py` — endpoints buscar, emitir, recepción, confirmación-observer; pantalla matriz; sugerencias con `purchase_engine`.
- `routes/informes.py` — endpoints `chart-mes` (3 series) y `ingresos-mes`.
- `templates/compras_dia.html` — sin "← Inicio" para rol pedidos.
- `templates/compras_dia_armar.html` — todo el armado, panel sticky, filtros, búsqueda, sugerencias, emitir.
- `templates/labs_drogerias.html` — matriz lab × drog.
- `templates/pedidos_emitidos_list.html` + `pedido_emitido_detalle.html` — flujo de recepción.
- `templates/base.html` — `_hide_chrome` para gateo de sidebar/topbar.
- `dumps/seed_pedidos_dia.sql.gz` — snapshot de datos.

## Pelusa

- El `init_db` corre con `--preload` en gunicorn (Render) — si rompe el boot por una migración inline, sospechar de eso.
- Las tablas nuevas se agregan al `zombie_names` en `database.py:1081` para evitar `pg_type` huérfanos en deploys.
- El dump excluye `obs_ventas_detalle` (200+ MB). Si querés probar el chart "Entradas" con datos reales necesitás repoblarla — desde la app local sería con el sync ObServer del DockerPanel.
