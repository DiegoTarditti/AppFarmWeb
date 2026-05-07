# Pedido al equipo de Observer

Resumen ejecutivo de lo que necesitamos del lado de Observer para avanzar con
las features que tenemos planeadas. Tras explorar `ObServerGestion.*` (255
tablas en `Gestion.*` + `Generales.*`), descubrimos que **casi todo lo que
imaginábamos pedir ya existe**. El bloqueo es de **acceso / permisos** y
**documentación**, no de funcionalidad faltante.

---

## 1. Acceso de lectura a `ObServerGestion.*`

Hoy accedemos solo vía la vista `DW.*` (29 tablas — versión denormalizada).
Necesitamos credenciales **read-only** sobre los schemas `Gestion` y
`Generales` para cubrir features de cierre de caja diario, kardex completo,
cuenta corriente clientes, recetas/OS avanzado, histórico de precios y
compras a proveedores.

## 2. Documentación de `FechaModificacion` / `RowVersion`

Para hacer sync incremental sin bajar 4M de filas cada vez, necesitamos
saber qué tablas tienen un campo de "última modificación" confiable.
Específicamente confirmar para:

- `MovimientosStock` (4.1M filas)
- `OperacionesRenglones` (3.8M)
- `ProductosPrecios` (3.8M)
- `OperacionesPagos` (2.4M)
- `Operaciones` (2.2M)
- `RecetasRenglones` (1.1M)
- `Recetas` (842K)

## 3. Aclaración de enums / lookups clave

Mapeo de IDs a descripciones para mostrar al usuario:

- **`IdTipoOperacion`** (en `ProductosVendidos`): valores `V`, `D`, `NC`,
  ¿hay otros? ¿descripciones oficiales?
- **`IdTipoMovimientoStock`** (en `MovimientosStock`): mismo concepto.
- **`IdMotivoAjusteStock`** (FK a `MotivosAjustesStock`).
- **`IdEstadoOperacion`** y similares en cierres de caja.

## 4. Confirmación de columnas con datos sensibles (PII)

Para excluir del sync local lo que no necesitamos: nombre/DNI/teléfono/
domicilio de `Clientes` y `Medicos` cuando no afectan a las features.

## 5. (Opcional) Permiso para crear vistas custom en Observer

Si hay queries pesadas que se repiten, una vista materializada del lado
de Observer reduce mucho la carga. Alternativa: las creamos del lado
nuestro con el sync.

---

## Lo que NO hay que pedir más

Inicialmente íbamos a pedir tablas nuevas para cierre de caja, transacciones
electrónicas y 1-producto-a-N-EANs. **Todo eso ya existe** en
`ObServerGestion.*`:

| Lo que pensábamos pedir            | Tabla que ya existe                              |
|------------------------------------|--------------------------------------------------|
| Tabla de cierre de caja            | `Gestion.CajasMostradorCierres` (+ 4 hijas)      |
| Movimientos de caja                | `Gestion.CajasMostradorMovimientos` (173K filas) |
| Transacciones electrónicas         | `Gestion.CuponTarjeta` (373K) + `TarjetaCierres` |
| 1 producto → N EANs                | `Gestion.ProductosCodigosBarras` (131K)          |
| Kardex con signos                  | `Gestion.MovimientosStock` (4.1M)                |

**Conclusión:** con acceso a `ObServerGestion.*` y los puntos 2-4 de arriba
documentados, el resto es trabajo nuestro de sincronización.
