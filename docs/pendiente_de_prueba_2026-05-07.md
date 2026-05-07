# Pendiente de prueba — sesión 2026-05-07

Lista de todos los cambios incorporados hoy. Todos requieren validación con
data real antes de pasar a prod (PR a main).

---

## 1. Compras día — lógica del armado de pedido

### 1.1 No forzar pedir 1 cuando cobertura está cubierta
- **Antes**: `a_pedir = max(1, ideal - stock)` → siempre pedía mínimo 1.
- **Ahora**: `a_pedir = max(0, ideal - stock)` → si stock=mín y target ya cubierto, queda en 0 y se oculta.
- **Probar**: ver que productos con stock=mín y rotación normal (que antes aparecían con a_pedir=1) ahora desaparezcan del listado.
- **Archivo**: `routes/compras_dia.py` líneas ~640.

### 1.2 Ventana de rotación configurable (default 3 meses)
- **Antes**: `target_unid = ceil(u12m / 365 × target_dias)` — usaba todo el agregado de la tabla (16+ meses) y dividía por 365 ⇒ subestimaba la tasa diaria 33%.
- **Ahora**: `target_unid = ceil(u_rot / dias_rotacion × target_dias)` con `meses_rotacion=3` por default. Los 12m siguen para estacionalidad/forecast.
- **Query param**: `?meses_rot=N` (1-12).
- **Probar**: producto que rotaba mucho hace 8 meses pero ahora poco → debería aparecer con menos `a_pedir` que antes.

### 1.3 Min "corregido" automático
- Cuando `min_sugerencia in ('up','down')` con `min_sugerido > 0`, el sistema usa NUESTRO valor calculado en lugar del de Observer.
- UI: columna MÍN muestra `~~50~~ → 9 [corregido]` con tachado violeta + tooltip explicando.
- También se aplica en filas agregadas a mano vía "+ Agregar producto".
- **Probar**: producto con mín muy alto en Observer + rotación lenta → debería pedir según el sugerido (más bajo) y mostrar el badge corregido.

### 1.4 Sync stock: badge en header + botón "Sincronizar ahora"
- En `/compras/armar` modo multi-drog aparece un chip `🔄 Sync stock: 07/05 15:30 (hace 90 min)` con color según antigüedad (verde <90min, amarillo <6h, rojo >6h).
- Botón "Sincronizar ahora" dispara `/admin/observer-sync/stock` y recarga.
- **Probar**: tocar el botón con DockerPanel + Observer corriendo, debería actualizar la fecha en pantalla.

---

## 2. Filtro nuevo: Rubro (en `/compras/armar` y `/productos`)

- Dropdown para filtrar por rubro de Observer (Medicamentos, Cosmética, etc.).
- Default en armar: "Medicamentos" (compatibilidad con comportamiento previo).
- Default en productos: "Todos los rubros".
- **Probar**: cambiar el dropdown, validar que la lista se filtra correctamente.

---

## 3. Filtros y UX en `/compras/armar` (modo multi-drog)

### 3.1 Drog filter como chips verticales
- Chips de drog (`Kel`, `20J`, `Libre`) ahora apilados verticalmente, no horizontal.

### 3.2 "Libres a:" — bulk + multi-drog
- Botones de color (Kel fuchsia, 20J sky) para asignar drog a las filas Libres y multi-drog visibles.
- Confirmación con detalle de filtros activos.

### 3.3 Auto-select del primer producto al cargar
- Apenas carga la pantalla, el chart Año + Mes muestra el primer producto.

### 3.4 Fuente de barras más grande
- Labels sobre las barras de los charts: 8/9px → 11px bold.

### 3.5 Override drog por fila (picker)
- Click en celda Drog de una fila → picker para cambiar la drog elegida solo de ese renglón.

---

## 4. Informe nuevo: Correcciones de mínimos (`/informes/correcciones-minimos`)

- Productos con `min_sugerencia in ('up','down')` agrupados por lab.
- Filtros: laboratorio, tipo (subir/bajar/ambos), rubro.
- Export XLSX coloreado (rojo=subir, azul=bajar).
- **Panel de gráficos**: año + mes (mismo que armar). Click en fila carga charts.
- **Probar**: que el informe abra rápido (calc pesado), filtros funcionen, export Excel sea válido, charts se carguen al click.

---

## 5. Ofertas por droguería (multi-lab)

### 5.1 Toggle en `/ofertas/import`
- Nueva sección "Modo de carga" con 2 cards: Por laboratorio | Por droguería (multi-lab).
- En modo drog: pide droguería destino, NO pide lab.
- Backend: nuevo path en `/api/ofertas/import-guardar` con `modo='drog'`. Deduce lab por producto desde `productos.laboratorio_id`. Si no se puede, queda `lab_id=NULL`.
- Migración inline: `OfertaMinimo.laboratorio_id` ahora nullable.

### 5.2 Validación rápida en modo drog
- En modo drog, validar saltea el fuzzy match y el dimensional matching → no se cuelga con archivos grandes (290+ items).
- Solo match exacto por EAN/código en `productos`.
- **Probar**: subir archivo de 200+ EANs en modo drog, validación debería tardar segundos.

### 5.3 Filtro automático en armado por oferta
- Si la drog tiene `OfertaMinimo` activa con `drogueria_id=X` y vigencia OK, en `/compras/armar?prov=X` aparece banner.
- 3 estados según `?usar_oferta`:
  - **None** (default): banner amarillo con pregunta "¿Filtrar por oferta o ver todos?"
  - **'1'**: filtro aplicado, banner verde, lista solo de oferta.
  - **'0'**: ignorar oferta, banner gris discreto con link "Filtrar".
- **Probar**: cargar oferta multi-lab para drog X → entrar a armar para X → debería aparecer banner amarillo con la pregunta.

### 5.4 Botón "📦 Armar pedido" en proceso de drog
- En `/proceso/<id>` con `tipo='drogueria'` y estado != CERRADO, aparece botón que va a `/compras/armar?prov=PARTNER_ID`.
- **Probar**: crear proceso para drog → entrar al detail → botón debería estar visible.

---

## 6. Mejoras varias

### 6.1 Dedup de candidatos en matcher manual
- Cuando un producto aparecía 2 veces (local + observer) en el dropdown de candidatos, ahora dedup por `codigo_alfabeta` (o EAN como fallback).
- Si empata score: prioriza local (lab > global > observer).
- **Archivo**: `producto_matcher.py` función `buscar_candidatos`.
- **Probar**: pasar por el flujo de "vincular ofertas" en items sin match → ver que no aparezcan duplicados.

### 6.2 Crear droguería / laboratorio manualmente
- Form "+ Agregar" en:
  - `/providers` (todos los proveedores)
  - `/providers/activos` (activar/desactivar)
- Campos: razón social, CUIT (opcional), tipo (drogueria/laboratorio/otro).
- Idempotente por nombre normalizado.
- **Probar**: crear una drog nueva, validar que aparezca en la matriz lab×drog y en el dropdown del armado.

### 6.3 Botón "+ Agregar laboratorio" en matriz lab×drog
- En `/compras/labs-drogerias` arriba: input + botón para crear lab nuevo sin salir.
- Click ✕ por fila para borrar lab (con cascade a OfertaMinimo, DescuentoBase, etc.).

### 6.4 Sin sync no aparecen en datos
- Diagnóstico: el sync de DockerPanel a localhost está fallando hace 10 días (`autosync_last_run` viejo, `last_attempt` reciente).
- Si los datos parecen viejos, ir a `/admin/observer-sync` y disparar manual.

---

## 7. Documentación generada

- `docs/feature_appcajas_link_observer.md` — estado del enlace AppCajas↔Observer (pausado, retomar después).
- `docs/pedido_a_observer.pdf` — informe completo (10pp) para enviar al equipo Observer: pedidos generales, lista de tablas a sincronizar, especificos AppCajas, comentarios.
- `docs/pedido_a_observer.md` — versión md del PDF.
- `scripts/generar_pedido_observer_pdf.py` — script regenerable.
- `scripts/listar_droguerias_observer.py` — script CLI para listar droguerías (depende de acceso a `Gestion.*` que aún no tenemos).

---

## 8. Cosas chicas

- **Auto-select chart en `/informes/correcciones-minimos`**: panel `_grafico_dual_panel.html` se incluye en cualquier pantalla y carga el primer producto al abrir.
- **Filtro multi-token AND** en buscador de productos.
- **`/observer/sql`**: SQL playground read-only (existente, lo usamos para descubrir el schema completo `ObServerGestion.*`).

---

## 9. Optimizaciones pendientes (no hechas)

### 9.0 Limpiar filas sucias en import de ofertas antes de procesar
- **Síntoma**: archivos Excel del proveedor suelen tener "ruido" antes/dentro de la tabla:
  - Títulos arriba de los headers (`"Lista de ofertas Mayo 2026"`, fecha, lab).
  - Filas en blanco separando secciones.
  - Subtotales / totales al final (`"TOTAL"`, `"Subtotal categoría X"`).
  - Comentarios sueltos en mitad de la tabla.
  - Filas con `None` en el campo EAN/código pero con texto raro en otras columnas.
- **Hoy**: el parser incluye esas filas como items y revientan en validación o matching.
- **Fix propuesto**:
  - Detectar y descartar filas que NO tengan EAN ni código interno (skip silencioso).
  - Detectar filas tipo "TOTAL"/"SUBTOTAL"/"NOTA" en cualquier columna y filtrarlas.
  - Detectar filas con descripción excesivamente corta (<3 chars) o vacía — probablemente ruido.
  - Loggear cuántas filas se descartaron en el preview para que el usuario vea: "X items detectados, Y filas vacías/totales descartadas".
- **Archivo**: `routes/ofertas_import.py` función `_previsualizar_xlsx` y `_previsualizar_pdf`.
- **Beneficio**: archivos del proveedor pasan limpio sin que el operador tenga que editar Excel a mano antes.

### 9.1 No guardar equivalencias innecesarias en import de ofertas
- **Síntoma**: `_persistir_equivalencia()` se llama para TODOS los items que se guardan, incluso aquellos que matchearon automático por EAN exacto. Esos no necesitan equivalencia — el EAN ya identifica el producto.
- **Cuando SÍ es útil**: cuando el archivo del proveedor trae solo código interno (ej. `796`) sin EAN, y matcheamos por descripción + lab → guardar la equivalencia `código→EAN` para que la próxima vez sea match directo.
- **Cuando NO es útil**: cuando el archivo trae EAN válido y matcheó por EAN exacto. Estamos guardando equivalencias redundantes (mismo EAN ↔ mismo EAN) que solo inflan la tabla.
- **Fix**: en `routes/ofertas_import.py` líneas 1105 y 1130, agregar guard `if codigo and codigo != ean` antes de llamar a `_persistir_equivalencia`. Solo persistir cuando el código interno es DISTINTO del EAN final.
- **Beneficio**: tabla `equivalencias_proveedor` más limpia, queries más rápidas, menos basura.

---

## Test plan recomendado (orden de prioridad)

1. **Item 1.3 (mín corregido)** — alto impacto en cantidades pedidas.
2. **Item 1.2 (rotación 3 meses)** — cambia las cantidades sugeridas significativamente.
3. **Item 5.1+5.3 (ofertas multi-lab + filtro)** — feature nueva completa, testear E2E.
4. **Item 4 (informe correcciones)** — validar que el cálculo es razonable + export Excel abre OK.
5. **Item 6.2 (crear drog manual)** — operacional, debería ser rápido.
6. Resto.

---

## Reverts si rompe algo

- `1.1` (max(0)→max(1)): editar `routes/compras_dia.py` línea ~641.
- `1.2` (meses_rot): pasar `?meses_rot=12` en URL para volver al comportamiento previo.
- `1.3` (corregido): comentar el bloque `min_sugerencia in ('up','down')` y volver a `min_efectivo = min_actual`.
- `5.1` (modo drog): el toggle es opt-in, ignorar y usar siempre "Por laboratorio".
- `5.3` (banner pregunta): borrar la oferta en cuestión o setear `vigencia_hasta < hoy`.
