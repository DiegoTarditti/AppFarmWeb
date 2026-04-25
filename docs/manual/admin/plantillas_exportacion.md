# Admin: Plantillas de exportación

> ⚠ STATUS: PENDIENTE

## Tipos de plantilla

Hay dos sistemas separados (intencionalmente NO unificados):

| | Laboratorio | Proveedor / Droguería |
|---|---|---|
| Modelo | `ExportTemplate` | `PlantillaExportacion` + `PlantillaCampo` |
| Formato | XLSX (columnas custom) | TXT ancho fijo |
| Config UI | `/laboratorio/<id>/export-template` | `/provider/<id>/plantilla` |
| Export | `/order/<id>/export/plantilla` | `/order/<id>/export-prov-plantilla` |

## Cuándo se usa cada una

- **Lab**: cuando el pedido sale directo al laboratorio fabricante (Excel con columnas que el lab pide).
- **Proveedor**: cuando el pedido entra vía droguería y la droguería pide formato TXT con campos en posiciones fijas.

El sistema elige automáticamente según el [canal de compra](../glosario.md#canal-de-compra) seteado en el paso 4 del análisis.
