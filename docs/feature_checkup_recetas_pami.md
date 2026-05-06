# Feature pendiente — Chequeo de recetas PAMI/OS para liquidación

## Origen

Cuando termina el período de liquidación, PAMI (y otras OS) generan un **Listado de Recetas Pendientes**: el detalle de las recetas autorizadas online que la farmacia tiene que liquidar.

La farmacia tiene tres "vistas" del mismo dato:

1. **Listado oficial PAMI/OS** — PDF/CSV que baja del portal de la OS. Es la
   "verdad" de lo que se va a liquidar.
2. **Recetas físicas** — el papel impreso por farmacia con códigos de barras
   (OPF, NumeroReceta, NumeroAutorizacionExterno). Tiene que estar firmado por
   afiliado.
3. **Observer.Gestion.Recetas** — lo que quedó cargado en el sistema.

El chequeo de liquidación necesita que **los tres coincidan**. Si una receta:
- Está en (1) y (2) pero no en (3) → no se cargó en el sistema.
- Está en (2) y (3) pero no en (1) → la OS no la reconoce — riesgo de no cobrar.
- Está en (1) y (3) pero no en (2) → la farmacia no tiene el papel firmado.
- Anulada en (3) pero figura en (1) → conflicto, hay que rectificar.

## Estado actual

- ✅ **Cruce (2) ↔ (3)**: implementado en `/recetas/scan` (escaneo de recetas
  físicas y match contra Observer.Gestion.Recetas vía SQL playground).
- ⏳ **Cruce (1) ↔ (3)**: pendiente. Hay que poder importar el listado oficial
  PAMI y matchear contra Observer.

## Estructura del listado oficial PAMI (referencia)

Archivo: `Listado de Recetas Pendientes` (PDF de ejemplo recibido el 2026-05-06).

**Cabecera:**
- Financiador: PAMI
- Farmacia: BADIA
- Prestador: 909209785
- Fecha de Proceso: 05/05/2026
- Periodo: Del 01/04/2026 al 05/05/2026
- Cobertura: GENERAL ONLINE

**Columnas del detalle:**
| Columna           | Tipo    | Match en Observer                   |
|-------------------|---------|--------------------------------------|
| Beneficiario      | varchar | `Recetas.NumeroAfiliado` (16 dig)   |
| Fecha             | date    | `Recetas.FechaDeVenta`              |
| Hora              | time    | `Recetas.FechaDeVenta` (parte hora) |
| N° Referencia     | varchar | `Recetas.NumeroAutorizacionExterno` (20 dig) |
| Importe Neto      | money   | `Recetas.TotalReceta`               |
| A Cargo Entidad   | money   | `Recetas.TotalACargoOS`             |
| N° Receta         | varchar | `Recetas.NumeroReceta` (13 dig)     |

**Tamaño típico:** ~100-150 recetas por listado (un período mensual).

## Workflow propuesto del feature `/recetas/checkup`

1. Operador sube el PDF (o copia/pega el texto).
2. Sistema parsea las filas y obtiene la lista de N° Referencia.
3. Cruza contra Observer.Gestion.Recetas:
   - Match por `NumeroAutorizacionExterno` (la referencia es exact match).
   - Verifica que `TotalReceta` y `TotalACargoOS` coincidan dentro de tolerancia.
   - Verifica `Anulada=0` y `Autorizada=1`.
4. Reporta:
   - **OK**: receta en sistema y montos coinciden.
   - **Faltante en Observer**: receta en listado pero no en sistema → cargar.
   - **Anulada en Observer**: pelearla con OS o sacarla del listado.
   - **Diferencia de montos**: rectificar antes de presentar.
   - **Extra en Observer**: cargada pero no en listado oficial → ¿no se autorizó?
5. (Opcional) Cruzar también contra el escaneado físico ya hecho — listas las 3.

## Implementación

- Frontend: parsear PDF con `pdfplumber` o `tabula-py` (tabla bien estructurada).
  Alternativa: pedir al usuario que copie/pegue el contenido.
- Backend: query a Observer en bloque por `NumeroAutorizacionExterno IN (...)`.
- UI: tabla con badges de estado (OK / FALTANTE / ANULADA / DIFF).
- Export: lista de recetas a "investigar" para enviar a PAMI o cargar.

## Datos de ejemplo extraídos del PDF (primeras 5 filas)

| Beneficiario        | Fecha      | Hora     | N° Referencia          | Importe Neto | A Cargo Entidad | N° Receta     |
|---------------------|------------|----------|------------------------|--------------|-----------------|---------------|
| 4114092577810500    | 27/04/2026 | 09:30:43 | 20260427093043468200   | 90.365,65    | 90.365,65       | 8263127062012 |
| 4106580356340500    | 27/04/2026 | 09:43:12 | 20260427094312038700   | 64.942,81    | 32.471,40       | 8262904988880 |
| 4115037925870400    | 27/04/2026 | 10:31:42 | 20260427103142136500   | 12.419,17    | 12.419,17       | 8262874100763 |
| 4115037925870400    | 27/04/2026 | 10:31:47 | 20260427103147403200   | 39.750,93    | 15.900,37       | 8262860499369 |
| 4115037925870400    | 27/04/2026 | 10:33:37 | 20260427103337640500   | 9.883,80     | 5.930,28        | 8262903763969 |

**Observación:** La columna `N° Referencia` empieza con la fecha en formato
`YYYYMMDDhhmmss…` — coincide con el formato de `NumeroAutorizacionExterno` de
Observer y con el barcode del ticket impreso. Eso lo confirma como la PK
para cruce.
