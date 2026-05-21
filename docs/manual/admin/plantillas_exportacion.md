# Admin: Plantillas de exportación

Cuando exportás un pedido a archivo (XLSX/TXT) para mandárselo al laboratorio o droguería, cada uno pide su **formato custom**: columnas en orden específico, código en posiciones precisas, etc. Las plantillas de exportación te permiten configurar ese formato sin tocar código.

## Dos sistemas separados (intencionalmente NO unificados)

| | Laboratorio | Droguería / Proveedor |
|---|---|---|
| Modelo | `ExportTemplate` (1 fila por lab) | `PlantillaExportacion` + `PlantillaCampo` |
| Formato | XLSX (columnas custom) | TXT ancho fijo (col_inicio, longitud, alineación, relleno) |
| Config UI | `/laboratorio/<id>/export-template` | `/provider/<id>/plantilla` |
| Export desde pedido | `/order/<id>/export/plantilla` | `/order/<id>/export-prov-plantilla` |

¿Por qué no unificarlos?
- **Labs**: piden XLSX porque son más flexibles (Excel abierto), las columnas pueden cambiar pedido a pedido.
- **Droguerías**: piden TXT con ancho fijo porque sus sistemas legacy lo procesan automáticamente con parsers que esperan posiciones byte-exactas. Son contratos rígidos.

## Cuál se usa según el pedido

El sistema lee el **canal de compra** del pedido (paso 4 del wizard, ver [Análisis vía droguería](../flujos/02_analizar_drogueria.md)):

- `canal='laboratorio'` → muestra solo el botón **"Plantilla laboratorio"** y exporta XLSX usando `ExportTemplate` del lab.
- `canal='drogueria'` → muestra solo el botón **"Plantilla droguería"** y exporta TXT usando `PlantillaExportacion` del proveedor.
- Si la plantilla correspondiente no existe → no aparece el botón.

## Configurar plantilla de laboratorio (XLSX)

URL: `/laboratorio/<id>/export-template`.

Pantalla con drag-and-drop para definir:
- **Columnas** que querés exportar (de la lista de `EXPORT_FIELDS`).
- **Orden** en que aparecen.
- **Encabezado custom** (texto libre para la primera fila del XLSX).

Campos disponibles (`EXPORT_FIELDS` en `routes/laboratorios.py`):
- `ean` / `codigo_barra`
- `nombre` / `descripcion`
- `total` / `cantidad`
- `cant_modulo`, `cant_oferta_min`, `cant_nodeal`
- `precio` / `precio_pvp`
- `erp_qty`, `rotacion`, `avg_monthly`

## Configurar plantilla de droguería (TXT fijo)

URL: `/provider/<id>/plantilla`.

Plantilla = colección de campos. Cada campo (`PlantillaCampo`) tiene:
- **Nombre del campo** (uno de `CAMPOS_SISTEMA` definidos en `database.py`).
- **Posición de inicio** (col_inicio): byte donde empieza.
- **Longitud**: cuántos caracteres ocupa.
- **Alineación**: izquierda / derecha.
- **Relleno**: caracter para rellenar (espacios, ceros, etc.).
- **Valor fijo** (opcional): texto literal en lugar de un campo dinámico.

Ejemplo para Kellerhoff:
```
Pos 1-13:  EAN del producto         (alineado izq, relleno espacios)
Pos 14-19: Cantidad                 (alineado der, relleno ceros)
Pos 20-30: Espacio en blanco
Pos 31-50: Descripción truncada     (alineado izq, relleno espacios)
```

Al exportar, el sistema arma cada línea byte-exacta según las posiciones.

## Copiar plantilla entre proveedores

Si tenés Kellerhoff con plantilla armada y querés que Pharmos use la misma estructura:
- En `/provider/<pharmos>/plantilla`, botón "Copiar de otra plantilla" → seleccionar Kellerhoff.

## Botones desde el pedido

En el paso 4 del wizard (`/order/<id>` resumen) o desde la pantalla de pedido, según el canal seteado, aparecen botones específicos:

- "Exportar plantilla [lab]" — exporta XLSX usando ExportTemplate del lab.
- "Exportar plantilla [drog]" — exporta TXT usando PlantillaExportacion del proveedor.
- "XLSX genérico" — si no querés usar plantilla custom.
- "PDF" — siempre disponible, formato propio del sistema.

## Términos importantes

- [Canal de compra](../glosario.md#canal-de-compra)
- [Pedido](../glosario.md#pedido)
