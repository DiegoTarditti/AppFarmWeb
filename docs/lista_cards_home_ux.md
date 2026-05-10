# Lista de cards del home — estado UX

Tracking de migración visual al theme-emerald + validación visual.

| # | Card | Endpoint / Template | Modificado | Probado |
|---|------|--------------------|:----------:|:-------:|
|   | **HERO** | | | |
| 1 | Pedido de reposición (CTA principal) | `compras_dia` → `compras_dia.html` | ✅ | ⬜ |
| 2 | Ver pendientes (sub-link) | `orders_list` → `orders_list.html` | ✅ | ⬜ |
|   | **ENTIDADES** | | | |
| 3 | Laboratorios → Ver | `laboratorios_list` → `laboratorios.html` | ⬜ | ⬜ |
| 4 | Laboratorios → Procesos | `procesos_list` → `procesos_list.html` | ⬜ | ⬜ |
| 5 | Laboratorios → Packs | `modulo_packs_list` → `modulo_packs.html` | ⬜ | ⬜ |
| 6 | Droguerías → Ver | `providers_list` → `providers.html` | ✅ | ⬜ |
| 7 | Droguerías → Procesos | `procesos_list` → `procesos_list.html` | ⬜ | ⬜ |
| 8 | Droguerías → Nueva (Ingresos) | `ingresos` → `ingresos.html` | ⬜ | ⬜ |
| 9 | Otros → Ver | `providers_list` → `providers.html` | ✅ | ⬜ |
| 10 | Otros → Procesos | `procesos_list` → `procesos_list.html` | ⬜ | ⬜ |
| 11 | Otros → Reclamos | `claims_list` → `claims_list.html` | ✅ | ⬜ |
|   | **ACCIONES FRECUENTES** | | | |
| 12 | 🛒 Pedidos guardados | `orders_list` | ✅ | ⬜ |
| 13 | 📥 Control de Ingreso | `ingresos` → `ingresos.html` | ⬜ | ⬜ |
| 14 | 📊 Procesos de compra | `procesos_list` | ⬜ | ⬜ |
| 15 | ⚠️ Reclamos | `claims_list` | ✅ | ⬜ |
| 16 | 📈 Importar ofertas | `ofertas_import_page` → `ofertas_import.html` | ⬜ | ⬜ |
| 17 | 💳 Cuentas Corrientes | `cuentas_corrientes` → `cuenta_corriente.html` | ⬜ | ⬜ |
| 18 | 📈 Mis Informes | `informes_index` → `informes_index.html` | ⬜ | ⬜ |
| 19 | ⚡ Inteligencia de Negocios | `bi_tablero` → `bi_tablero.html` | ⬜ | ⬜ |
| 20 | 📦 Productos | `productos_list` → `productos.html` | ✅ | ⬜ |
| 21 | 💊 Vademécum | `vademecum_index` → `vademecum.html` | ⬜ | ⬜ |
| 22 | 👥 Clientes | `clientes_list` → `clientes_list.html` | ⬜ | ⬜ |
| 23 | 🏥 Obras Sociales | `os_index` → `os_index.html` | ⬜ | ⬜ |
| 24 | 📋 Scan recetas | `recetas_scan` → `recetas_scan.html` | ⬜ | ⬜ |
| 25 | 🔮 Compras recurrentes | `intelligence_recurrentes` → `intelligence_recurrentes.html` | ⬜ | ⬜ |
| 26 | ⚙️ Configuración | `settings` → `settings.html` | ⬜ | ⬜ |

## Resumen

- **Modificado (theme-emerald activo)**: 8 destinos únicos (compras_dia, orders_list, providers, claims_list, productos).
- **Pendiente migrar**: 14 destinos únicos (laboratorios, procesos_list, modulo_packs, ingresos, ofertas_import, cuenta_corriente, informes_index, bi_tablero, vademecum, clientes, os_index, recetas_scan, intelligence_recurrentes, settings).

## Cómo usar este doc

Diego dice **"lista de cards home"** → lo levanto.
Después dice "valido X" → tildo `Probado` para esa fila.
Si una fila marcada Modificado falla, vuelvo a abrir y ajusto.

## Pantallas migradas no en el home

Estas también están en theme-emerald pero no son cards del home (se accede desde flujos):

- `compras_dia_armar.html` (entrada desde compras_dia)
- `compras_rapido.html` (entrada desde compras_dia)
- `compras_transfers.html`
- `purchase_suggest.html` (recién migrado en esta sesión)
- `order_detail.html` (entrada desde orders_list)
- `provider_invoices.html`, `provider_mappings.html`, `invoice_items.html`, `pick_fields.html`, `compare.html`, `results.html`, `claim.html` (flujo factura/reclamo)
- `index.html` (el home mismo)
- `login.html`, `base.html` (estructura)
