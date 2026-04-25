# Flujo: Análisis vía droguería

Caso típico: querés analizar las ventas de un laboratorio (ej. Roemmers) pero la mercadería NO entra directo de Roemmers, entra por una **droguería** (Kellerhoff, Pharmos, 20 de Junio) que distribuye varios labs.

El análisis de productos es el mismo que [Análisis de laboratorio](./01_analizar_laboratorio.md) — la diferencia está en el **paso 4 del wizard**, donde elegís el canal de compra.

## Por qué importa

- El **precio de costo** lo pone la droguería, no el laboratorio fabricante. Plazos, descuentos y mínimos también.
- La **plantilla de exportación** del pedido es distinta: cada droguería pide un formato TXT específico (ancho fijo, columnas en posiciones precisas) en vez del XLSX libre del lab.
- Los **reclamos por diferencias** se hacen contra la droguería, no contra el lab.
- En procesos de compra, esto se trackea como `tipo='drogueria'` con `partner_id` apuntando al `Provider`.

## Pasos

### 1. Análisis idéntico al de laboratorio

Hasta el paso 3 del wizard (`/order/<id>`), el flujo es exactamente el mismo:
1. Crear proceso → tipo Laboratorio (Roemmers).
2. Pantalla "cuántos días" → análisis.
3. Wizard: módulos (paso 1) → cantidades (paso 2) → ofertas con mínimo (paso 3).

### 2. Paso 4 — Canal de compra

En el paso 4 del wizard aparece la card **"Canal de compra"** con dos radios:

- ⚪ **Directo del laboratorio**
- ⚪ **Vía droguería** (al elegir esta, aparece un select con las droguerías cargadas en `/providers`).

Elegís **"Vía droguería"** + seleccionás la droguería (ej. Kellerhoff).

El sistema:
- Persiste en `pedidos.canal='drogueria'` y `pedidos.partner_id=<id_kellerhoff>`.
- Persiste timestamp en `pedidos.canal_elegido_en`.
- Auto-save en cualquier cambio del radio o del select.
- Aparece un badge en el header del paso 4 mostrando el canal elegido.

### 3. Plantilla de exportación filtrada

Si la droguería tiene una **plantilla de exportación** configurada, en el paso 4 aparece el botón "Plantilla droguería" además de "Plantilla laboratorio". El sistema filtra los botones según el canal:

- Canal = `laboratorio` → muestra solo plantilla del lab (XLSX).
- Canal = `drogueria` → muestra solo plantilla de la droguería (TXT formato fijo).

Las plantillas son sistemas separados intencionalmente (no unificados):

| | Laboratorio | Droguería |
|---|---|---|
| Modelo | `ExportTemplate` | `PlantillaExportacion` + `PlantillaCampo` |
| Formato | XLSX (columnas custom) | TXT ancho fijo |
| Config UI | `/laboratorio/<id>/export-template` | `/provider/<id>/plantilla` |
| Export | `/order/<id>/export/plantilla` | `/order/<id>/export-prov-plantilla` |

Ver [Plantillas de exportación](../admin/plantillas_exportacion.md).

### 4. Enviar a procesos

Al apretar "Enviar a Procesos" desde `/orders`:

- El sistema **lee el canal del pedido** (no pregunta de nuevo).
- Crea un `procesos_compra` con `tipo='drogueria'` y `partner_id` apuntando al `Provider`.
- En el listado de procesos aparece bajo "Droguerías" (no Laboratorios).

Si el pedido NO tiene canal seteado (saltaste el paso 4 o lo dejaste en default), al apretar "Enviar a Procesos" aparece el modal viejo preguntándote canal + droguería en ese momento.

### 5. En `/orders` — badges visuales

Cada pedido en el listado muestra un badge según su canal:
- **↗ DIRECTO** (violeta) — canal=laboratorio.
- **→ {DROGUERÍA}** (sky) — canal=drogueria + nombre del proveedor.
- (sin badge) — todavía no se decidió canal.

## Cuándo NO usar este flujo

- Si vas a comprar directo al laboratorio fabricante → usá el [flujo de análisis de laboratorio](./01_analizar_laboratorio.md) sin más, dejando el canal en "Directo".
- Si la droguería te emite la factura pero los productos vienen de varios labs distintos → usá el flujo normal de subir factura ([Subir factura](./03_subir_factura.md)) que ya maneja el caso multi-lab.

## Términos importantes

- [Canal de compra](../glosario.md#canal-de-compra)
- [Droguería](../glosario.md#droguería)
- [Laboratorio](../glosario.md#laboratorio)

## Errores comunes

**"No aparece la plantilla de droguería en el paso 4"**
La droguería seleccionada no tiene plantilla configurada. Andá a `/provider/<id>/plantilla` y configurala.

**"Quería elegir droguería pero el radio sigue en laboratorio"**
El auto-save dispara cuando cambiás el radio. Si no responde, refrescá la página y volvé a intentar — el último canal elegido queda persistido.
