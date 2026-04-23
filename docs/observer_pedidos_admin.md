# Pedidos al admin de SQL Server (ObServer)

Lista de cosas que pueden necesitarse para que la integración con ObServer funcione bien. No todo es urgente — priorizadas.

**Conexión actual (OK)**
- Host: `192.168.1.137` · Puerto TCP: `54572` (dinámico) · Instancia: `SERVER-1\BADIA`
- Base: `ObServerGestion` · Usuario: `usuarioDW` (read-only al esquema DW)
- SQL Server 2014 (v12.0.4100.1) · TDS 7.0 requerido

---

## 🔴 Importante (bloqueante o cuasi-bloqueante)

### 1. Fijar el puerto TCP de la instancia BADIA
Hoy BADIA escucha en el puerto dinámico **54572**. Cada vez que se reinicia el servicio SQL Server, ese puerto puede cambiar → nuestras apps pierden conexión.

**Pedir**: en **SQL Server Configuration Manager** → **Protocols for BADIA** → **TCP/IP** → Properties → pestaña **IP Addresses** → **IPAll**:
- **TCP Dynamic Ports** = vacío
- **TCP Port** = `1433` (o el que quieran, pero fijo)

Reiniciar el servicio SQL Server (BADIA) y abrir el puerto en el firewall de Windows del server:
```
netsh advfirewall firewall add rule name="SQL BADIA" dir=in action=allow protocol=TCP localport=1433
```

### 2. Identificador externo de producto (EAN / código de barra)
En las 29 vistas `DW.*` **no hay columna con EAN**. Los únicos identificadores externos visibles son:
- `DW.Productos.CodigoAlfabeta` (varchar 10) — código Alfabeta/Kairos
- `DW.Productos.Troquel` (int)

**Preguntar**: ¿existe alguna vista o tabla (aunque sea fuera de DW) que tenga la correspondencia `IdProducto` ↔ `EAN / código de barra`? Si la respuesta es sí → expónelo como una vista nueva `DW.ProductosCodigos` (o similar) con al menos `IdProducto, CodigoBarra`.

Si no existe esa tabla en ObServer, es un fix que tenemos que resolver del lado nuestro (por nombre + laboratorio), pero tenerlo nos ahorraría errores de matching.

---

## 🟡 Útiles (no bloqueantes)

### 3. Índice / vista orientada al análisis de ventas por lab + período
`DW.ProductosVendidos` tiene 2.9 millones de filas y 68 columnas. Para el análisis de compra necesitamos filtrar por `IdLaboratorio` + rango de fechas. Si las consultas son lentas, pedir:
- Confirmar si `DW.ProductosVendidos` tiene índice sobre `(FechaEstadistica, IdProducto)` o similar.
- Si no, sugerir crear una vista agregada `DW.VentasMensualesPorLab` con: `IdLaboratorio, IdProducto, Año, Mes, UnidadesVendidas, ImporteTotal` — sería ideal para nuestro caso.

### 4. Vista de ingresos/recepciones de mercadería

Confirmado: **el usuario `usuarioDW` no ve ninguna vista con nombre `Ingreso*`, `Compra*`, `Recepcion*`, `Movimiento*`, etc.** en todo el server. Los datos sí existen (el ERP genera el reporte `rptIngresoEgresoMercaderia.xlsx` con todas las columnas necesarias), solo hay que exponerlos.

**Pedido concreto**: crear una vista `DW.IngresosMercaderia` (o `DW.MovimientosMercaderia` si querés incluir también devoluciones/egresos en el mismo schema) con las columnas que el reporte del ERP ya calcula:

| Columna sugerida | Qué contiene | Ya existe en el reporte |
|---|---|---|
| `IdIngreso` | PK interna | nro de orden "Ingreso" de la col 2 |
| `IdFarmacia` | filtro por sucursal | — |
| `FechaIngreso` | fecha de la operación | — (cabecera del reporte) |
| `NumeroComprobante` | ej. `A001317854274` | sí (fila 14) |
| `ProveedorCuit` | ej. `30-53975649-0` | sí (fila 12) |
| `ProveedorNombre` | ej. `Kellerhoff` | sí (fila 11) |
| `IdProducto` | FK a DW.Productos | — (el reporte usa descripción) |
| `CodigoBarra` | EAN | sí (fila 33) |
| `CantidadPedida` | "Pedido" | sí |
| `CantidadRecibida` | "Recibido" | sí |
| `CantidadEnFalta` | "En falta" | sí |
| `CantidadDevuelta` | "Devueltas" | sí |
| `CantidadNoEntregada` | "No entr." | sí |
| `CantidadNoFacturada` | "No fact." | sí |
| `PrecioCosto` | "Costo" | sí |
| `PrecioVenta` | "Precio" | sí |
| `StockAlMomento` | stock después del ingreso | sí |
| `Lote` | (si el sistema lo registra) | — |
| `Vencimiento` | (si el sistema lo registra) | — |

Con esto, dejamos de subir el Excel ERP y el cruce factura↔ingreso se hace 100% automático desde ObServer. Hoy es un paso manual.

### 5. Permisos de lectura a vistas adicionales si hiciera falta
El usuario `usuarioDW` solo ve el esquema `DW`. Si en el futuro queremos leer algo que esté fuera de ese esquema (ej. ingresos de mercadería que no se expusieron), pedir GRANT SELECT a esa vista específica.

---

## 🟢 Opcionales (solo si se vuelve relevante)

### 6. Acceso remoto desde Render
La app corre en Render (cloud). Hoy solo podemos usar ObServer desde la LAN de la farmacia. Para que Render llegue al SQL Server hay dos opciones:
- **Tailscale** en el server (más simple, ya existe el setup para la farmacia).
- **VPN IPsec / túnel SSH** — más complejo.

**Pedir (solo si/cuando se quiera usar desde Render)**: instalar Tailscale en el server y agregar la máquina al tailnet de la farmacia.

### 7. IdFarmacia de la sucursal
Las vistas usan `IdFarmacia` (p. ej. `10525` en los samples). **Pedir**: confirmar que `10525` es efectivamente el ID de la farmacia principal (y si hay múltiples sucursales, listarlas).

---

## 📋 Estado actual

- [x] Conexión pymssql funcionando (TDS 7.0)
- [x] 29 vistas `DW.*` accesibles, read-only
- [x] Schema de `DW.Productos`, `DW.Laboratorios`, `DW.StockFarmaciasProductos`, `DW.ProductosVendidos`, `DW.ProductosHistorico` explorado
- [ ] Puerto TCP fijo (pedido #1)
- [ ] Mapeo EAN ↔ IdProducto (pedido #2)
- [ ] Fase 1 sync de laboratorios — pendiente de desarrollo
