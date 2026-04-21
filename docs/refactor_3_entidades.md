# Refactor de Proveedores / Droguerías / Laboratorios

**Estado:** diseño pendiente de responder preguntas de dominio. NO CODEADO todavía.

## Contexto del problema

Hoy el schema tiene el concepto "proveedor" roto en dos:

- Tabla **`proveedores`** con campo `tipo` que vale `drogueria` o `laboratorio` por default. Las droguerías viven ahí. Los laboratorios **podrían** vivir ahí pero en la práctica...
- Tabla **`laboratorios`** separada, con sus propias FKs desde `productos`, `modulos`, `ofertas_minimo`, `export_templates`, `analisis_sesiones`, `pedidos` (por nombre), etc.

Resultado: "laboratorio" existe dos veces en el schema. `ProcesoCompra` resuelve el polimorfismo manualmente con `tipo + partner_id` apuntando a una u otra tabla.

## Decisión de diseño (alineada con intuición del usuario)

**3 tablas conceptualmente separadas** — no juntar todo en `proveedores`, porque un laboratorio NO es "un proveedor con flag". Tienen atributos y flujos distintos.

```
proveedores    → solo "otros" (servicios, insumos varios). NO labs ni droguerías.
droguerias     → nueva tabla (hoy están en proveedores con tipo='drogueria')
laboratorios   → ya existe, se mantiene
```

Cada una con sus atributos propios:

- **Droguería:** `parser_file`, `match_strategy`, plazos de pago, tipos de comprobante que maneja, etc.
- **Laboratorio:** de momento nada extra, pero puede tener listas de precios propias, condiciones comerciales, productos que fabrica
- **Proveedor "otro":** descripción del rubro y poco más

## Infraestructura común — polimorfismo explícito

Las relaciones comunes (facturas, cuentas corrientes, pedidos, ofertas, reclamos) usan el patrón que ya existe en `ProcesoCompra`:

```python
class Invoice:
    partner_id = Column(Integer)            # id en la tabla indicada por partner_type
    partner_type = Column(String(20))       # 'drogueria' | 'laboratorio' | 'proveedor'
```

**Ventajas:**
- Cada entidad mantiene su semántica en su tabla propia
- Las relaciones comunes no se duplican
- Ya hay precedente (procesos_compra)

**Desventajas:**
- PostgreSQL no valida FK polimórficas (hay que validar a mano)
- Queries de "dame todas las facturas de X" necesitan `partner_type` + `partner_id`

## Preguntas de dominio pendientes (CRÍTICAS antes de codear)

### 1. Pedidos: ¿a quién se le pide vs quién factura?
Hoy un pedido guarda un `laboratorio` como string. Pero en el flujo real:
- ¿Se le hace el pedido al laboratorio pero lo factura una droguería?
- Si es así, el Pedido necesita guardar **ambos**: `lab_id` (a quién pedí) Y `drogueria_id` (quién me factura).
- Esto CAMBIA completamente el modelo de Pedido vs lo que tenemos hoy.

### 2. Cuentas corrientes
- ¿Se llevan solo con droguerías? ¿También con laboratorios?
- Si ambos: ¿dos cuentas corrientes separadas por partner_type?

### 3. Facturas
- ¿Un laboratorio puede facturar directo a la farmacia, o solo las droguerías?
- Si solo droguerías: las facturas tendrían `partner_type='drogueria'` siempre.

### 4. Ofertas
- Las publica el laboratorio, pero la orden de compra va a la droguería.
- ¿Se registran por lab o por droguería?
- Si son del lab: `ofertas.laboratorio_id` (directo, no polimórfico).

## Plan de implementación (cuando haya respuestas)

**Fase 1 — Schema**
- Crear tabla `droguerias` con los campos específicos
- Dejar `proveedores` solo para "otros"
- Mantener `laboratorios` como está
- Agregar `partner_id + partner_type` a: facturas, pagos_ajustes_cc, barcode_mappings, claims, descuento_campanas, documentos_pendientes
- Migración SQL: mover filas con `tipo='drogueria'` de `proveedores` a `droguerias`, luego repuntar FKs

**Fase 2 — Modelos SQLAlchemy**
- `Provider` queda con los "otros"
- Nuevo `Drogueria` model
- Helper `get_partner(partner_type, partner_id)` que resuelve la fila concreta
- Adaptar `Invoice`, `Pedido`, etc. al nuevo esquema

**Fase 3 — Rutas y templates**
- 3 cards en el home (Proveedores, Droguerías, Laboratorios)
- Listados separados por tipo con vista común de facturas/cta cte/pedidos
- Migrar `ProcesoCompra.tipo` al nuevo `partner_type` (ya son iguales)

**Fase 4 — Cleanup**
- Eliminar el campo `tipo` viejo de `proveedores` (ya queda solo un tipo)
- Refactor del sidebar
- Actualizar el seed de proveedores (que ya mueve droguerías a su tabla correcta)

## Tareas antes pendientes que siguen vigentes

- ✅ Proveedores vacíos en DB local → hecho (hay script `seed_proveedores`)
- ⬜ Cache/fallback para usuario remoto cuando ObServer no responde
- ⬜ Filtrar sidebar completo según permisos (hoy solo Usuarios / ObServer / Admin están condicionados)
- ⬜ Nº de comprobante ObServer automático (cuando se sepa de dónde sale)
- ⬜ Probar en serio flujo "Guardar factura / Enviar a Proceso" del converter
- ⬜ Cleanup de inactivos en Render (el panel `/admin/cleanup-inactivos` está listo, falta correrlo allá)

## Siguiente paso al retomar

1. Responder las 4 preguntas de dominio
2. Ajustar el plan según respuestas
3. Arrancar por Fase 1 (schema) en rama de desarrollo
