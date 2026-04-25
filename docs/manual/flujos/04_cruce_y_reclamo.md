# Flujo: Cruce de factura vs ERP y generar reclamo

Después de subir una factura y cruzarla con lo recibido, este flujo te guía para identificar diferencias (faltantes, sobrantes, precios distintos) y armar un reclamo formal con PDF para mandarle a la droguería.

## Cuándo usarlo

- Acabás de subir una factura y aplicaste el cruce. Ver [Subir factura](./03_subir_factura.md).
- Hay diferencias entre lo facturado y lo recibido.

## Pasos

### 1. Pantalla de diferencias

Después de aplicar el cruce, te lleva a **`/results/<invoice_id>`**.

La tabla muestra todas las diferencias detectadas, una fila por producto problemático:

| Columna | Significa |
|---|---|
| Producto | Descripción + EAN |
| Cantidad factura | Lo que dice el PDF |
| Cantidad ERP | Lo que llegó realmente |
| **Diferencia** | Cantidad factura − ERP. **Positivo = faltante**, **negativo = sobrante** |
| Precio unitario | De la factura |
| Importe diferencia | Diferencia × precio |
| Observaciones | Notas (por ejemplo "fecha de vencimiento corta") |

Sumario arriba: total de diferencias, total monetario reclamable.

### 2. Seleccionar diferencias a reclamar

Cada fila tiene un **checkbox**. Tildás las que querés incluir en el reclamo. Razones para NO tildar:
- Diferencias chicas (ej. 1 unidad de un producto barato) que no vale la pena reclamar.
- Algo que ya sabés que vino físicamente pero no se cargó al ERP (resolverás internamente).

### 3. Botón "Generar reclamo"

Al apretar:

1. El sistema crea un `Claim` con `estado='ABIERTO'`, asociado a la factura y al proveedor.
2. Por cada checkbox tildado, crea un `ClaimItem` con la diferencia.
3. Renderiza la **pantalla del reclamo** (`/claim/<id>`) que muestra el PDF formato carta.
4. **Auto-descarga el PDF** con nombre `Reclamo_N{id}_{numero_factura}.pdf`.

El PDF usa `reportlab` y tiene:
- Encabezado de tu farmacia (nombre, dirección).
- Datos del proveedor (razón social, CUIT, domicilio).
- Tabla de ítems reclamados.
- Total monetario reclamable.
- Pie con número de reclamo y fecha.

### 4. Marcar reclamo como completado

Cuando la droguería resuelve el reclamo (te entrega lo faltante o emite NCR):

- Entrá a `/claim/<id>`.
- Apretás **"Marcar como completado"**.
- El estado pasa a `COMPLETADO`.

Podés ver todos los reclamos abiertos / completados en `/claims` con filtros.

## Comportamientos especiales

### Tipo NCR (Nota de Crédito)
Si la factura es NCR (`tipo_comprobante='NCR'`), todos los montos se guardan en negativo. El cruce funciona igual pero el reporte refleja el signo.

### Mappings reusados
Cada vez que confirmás un cruce con match manual, las equivalencias se guardan en `barcode_mappings`. La próxima vez que llegue una factura del mismo proveedor con esos mismos productos, el cruce automático ya los conoce.

### Ítems sin coincidencia ERP
Si un ítem de la factura no aparece en el ERP (no matchea con nada), aparece con cantidad ERP=0 → toda la cantidad facturada queda como diferencia (faltante). Tildás si querés reclamarlo o lo dejás.

## Errores comunes

**"No se generó el PDF"**
Reportlab no pudo armar el PDF. Causas: caracteres especiales en descripciones, datos del proveedor faltantes (CUIT, domicilio). Editá el proveedor en `/providers` con todos los campos.

**"El cruce no encuentra nada"**
El parser de la factura usó un formato distinto al ERP. Por ejemplo, factura tiene EANs pero ERP usa códigos internos. Solución: hacer el match manual la primera vez, después queda guardado.

## Términos importantes

- [EAN](../glosario.md#ean)
- [Alfabeta](../glosario.md#alfabeta)

## Atajos

- El PDF se descarga automático al llegar desde "Generar reclamo". Si lo necesitás de nuevo: `/claim/<id>/pdf`.
- Listado completo de reclamos: `/claims`. Filtro por estado (abierto/completado).
