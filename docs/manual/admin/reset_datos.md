# Admin: Reset de datos operativos

Limpieza selectiva de los datos transaccionales (pedidos, facturas, reclamos, análisis) sin tocar la **configuración** (proveedores, laboratorios, plantillas, productos master) ni el **espejo de ObServer** (`obs_*`).

**Acceso**: `/admin/reset-datos`. Card roja "Reset de datos operativos" en el admin.

⚠ **Operación destructiva**. Una vez confirmás, no hay deshacer.

## Cuándo usarlo

- **Después de pruebas iniciales / migración**: probaste con datos de ejemplo y querés arrancar limpio con datos reales.
- **Datos de prueba ensucian estadísticas**: querés que los gráficos de venta y los pedidos pendientes solo reflejen actividad real.
- **Empezar un nuevo período fiscal con base limpia**: raro pero pasa.

## Cómo funciona

Pantalla con **checkboxes por tipo de dato**:

- **Procesos de compra** (`procesos_compra`)
- **Pedidos guardados** (`pedidos`, `pedido_items`)
- **Facturas y diferencias** (`facturas`, `factura_items`, `stock_differences`, `erp_stock`)
- **Reclamos** (`reclamos`, `reclamo_items`)
- **Análisis sesiones** (`analisis_sesiones`)
- **Pagos / cuentas corrientes** (`pagos_ajustes_cc`)
- **Documentos pendientes** (`documentos_pendientes`)
- **Archivos físicos** (PDFs, Excel subidos)
- **Módulos de descuento** (`descuento_modulos`, `descuento_modulo_items`, `modulo_packs`)

Tildás los grupos que querés borrar y apretás **"Ejecutar reset"**. Confirmación obligatoria con texto "RESET" para evitar accidentes.

## Lo que NO toca

- **Usuarios** y sus permisos.
- **Configuración** (`Config` con farmacia_nombre, umbrales, etc.).
- **Laboratorios** y sus plantillas.
- **Proveedores** y sus plantillas.
- **Productos master** (`productos`) — aunque podés filtrarlos como dato operativo si querés, normalmente conviene mantenerlo.
- **Espejo de ObServer** (`obs_*`). Borrarlo sería suicida — habría que volver a sincronizar todo desde la farmacia (varios minutos).

## Antes de un reset masivo

Considerá:
- **Backup explícito**: correr `pg_dump` desde el DockerPanel o desde Render. Los datos no se pueden recuperar después del DELETE.
- **Filtrar primero**: si solo querés borrar los pedidos de prueba, podés ir a `/orders` y borrarlos uno a uno con el ícono 🗑.
- **Snapshot de Render**: si trabajás en Render, su filesystem tiene snapshots. Pero no es trivial restaurar uno.

## Acceso

Solo roles `admin` y `dev`. Los `farmacia` y `remoto` no pueden ejecutarlo.

## Comportamiento posterior

Después del reset, el sistema queda con:
- Configuración intacta.
- Catálogos de partners (labs, droguerías) intactos.
- Productos master intactos.
- ObServer mirror intacto (sigue funcionando análisis y catálogo).
- Cero pedidos / facturas / reclamos.

Cuando llegue el primer pedido nuevo (vía análisis), el sistema empieza a poblar las tablas operativas otra vez como si fuera la primera vez.
