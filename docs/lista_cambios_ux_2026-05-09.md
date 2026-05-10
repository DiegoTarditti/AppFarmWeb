# Lista de cambios UX — sesión 2026-05-09

Iteración visual con Diego: migración de purchase_suggest, rediseño de
botones, charts theme-aware, refinamientos en compras_dia_armar.

| # | Cambio | Hecho | Probado |
|---|--------|:-----:|:-------:|
| 1 | `purchase_suggest.html` migrado a `extends base.html` + theme-emerald | ✅ | ⬜ |
| 2 | `base.html` — `text-[#6b7280]` + `text-[#9ca3af]` agregados a mute overrides (arregla topbar title) | ✅ | ⬜ |
| 3 | `base.html` — nueva clase `.btn-icon` (icon-only ghost button + variant `-danger`) | ✅ | ✅ |
| 4 | `compras_dia.html` — botón "Matriz lab × drog" → `btn-primary` (naranja CTA) | ✅ | ⬜ |
| 5 | `compras_dia_armar.html` — quitado `opacity:.7` en `.text-[#bbb]` interno tabla ("Sin Producto local" legible) | ✅ | ⬜ |
| 6 | `compras_dia_armar.html` — Drog: Kel/Pha/Libre → toggle buttons (`.toggle-drog`) | ✅ | ⬜ |
| 7 | `compras_dia_armar.html` — Libres a: → gradient naranja unificado (sin colores per-sigla) | ✅ | ⬜ |
| 8 | `compras_dia_armar.html` — "Sincronizar ahora" → gradient naranja siempre | ✅ | ⬜ |
| 9 | `compras_dia_armar.html` — header filtros 2 cols (prod+lab / rubro+checks) | ✅ | ⬜ |
| 10 | `compras_dia_armar.html` — modal operador input `bg-white` (fix blanco s/blanco) | ✅ | ⬜ |
| 11 | `compras_dia_armar.html` — badge "Libre" en filas → mint translúcido visible (clase `.libre-base`) | ✅ | ⬜ |
| 12 | `orders_list.html` — fila acciones por pedido rediseñada con jerarquía (Analizar único primary, resto secondary, exports icon-only) | ✅ | ✅ |
| 13 | `orders_list.html` — filtro estado activo en gradient naranja, inactivos ghost | ✅ | ⬜ |
| 14 | `purchase_results.html` — XLSX + PDF → `btn-secondary` + icono std | ✅ | ⬜ |
| 15 | `converter_auto.html` — XLSX → `btn-secondary` + icono std | ✅ | ⬜ |
| 16 | `converter_pick.html` — XLSX → `btn-secondary` + icono std (JS innerHTML adaptado) | ✅ | ⬜ |
| 17 | `informe_correcciones_minimos.html` — XLSX → `btn-secondary` + icono std | ✅ | ⬜ |
| 18 | `informe_ventas_multi.html` — XLSX → `btn-secondary` + icono std | ✅ | ⬜ |
| 19 | `order_detail.html` — 3 XLSX + 3 PDF + Plantilla ▾ → `btn-secondary` + iconos std | ✅ | ⬜ |
| 20 | `_grafico_historico.html` — paleta theme-aware (single panel modal) | ✅ | ✅ |
| 21 | `_grafico_dual_panel.html` — paleta theme-aware (AÑO + MES) | ✅ | ✅ |

## Backlog tocado

- Item "compras_dia_armar header layout 2 col" del backlog (anotado y resuelto).
- Memoria nueva: `feedback_hide_sidebar_mobile.md` (regla de cuándo usar `hide_sidebar = True`).

## Qué falta para tildar "Probado"

Diego tiene que abrir cada pantalla y validar visualmente:

- `/purchase/suggest?calcular=1` — items 1
- Header topbar de cualquier pantalla con theme-emerald (ver "Armar pedido" arriba a la izq legible) — item 2
- `/compras/dia` — items 4
- `/compras/dia/armar` — items 5, 6, 7, 8, 9, 10, 11
- `/orders` — item 13 (12 ya OK), 19 (gráficos abiertos desde acciones)
- `/purchase/results/<uid>` — item 14
- `/converter/auto/<token>` — item 15
- `/converter/<token>/pick` — item 16
- `/informes/correcciones-minimos` — items 17, 21 (light theme)
- `/informe-ventas-multi` — item 18
- `/order/<id>` — item 19
- `/orders` modal histórico — item 20
- `/compras/dia/armar` modal histórico — item 21

## Commits de la sesión (rama `feat/migracion-pantallas-emerald`)

```
8600057 feat(ui): charts theme-aware
7a551d8 feat(ui): refinamientos visuales compras_dia y compras_dia_armar
f8b3982 feat(ui): rediseño orders_list + estandarización XLSX/PDF
46050ea feat(ui): migrar purchase_suggest.html al theme-emerald
```
