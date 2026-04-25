# Admin: Reset de datos

> ⚠ STATUS: PENDIENTE — operación destructiva

**Ruta**: `/admin/reset-datos`

## Para qué sirve

Limpiar selectivamente los datos transaccionales (pedidos, facturas, reclamos, análisis) sin tocar configuración (proveedores, laboratorios, plantillas, productos master).

## Cuándo usarlo

- Después de pruebas iniciales / migración.
- Si datos de prueba ensucian las estadísticas reales.

## Cómo

Pantalla con checkboxes por tipo de dato:
- Procesos de compra
- Pedidos guardados
- Facturas y diferencias
- Reclamos
- Análisis sesiones
- Ventas históricas (obs_ventas_mensuales) ⚠

⚠ NO TOCAR el chequeo de obs_ventas_mensuales si no estás 100% seguro — borrar requiere re-sync con ObServer (5+ minutos en farmacia).

## Backup previo

Antes de cualquier reset masivo, considerar correr `pg_dump` desde DockerPanel.

## Acceso

Solo roles `admin` y `dev`.
