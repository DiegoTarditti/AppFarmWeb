# Hallazgo — `Gestion.Recetas`: la fuente completa de recetas del PDV

**Fecha**: 2026-07-11
**Contexto**: Diego mostró una vista del PDV (screenshot) con recetas por afiliado — columnas OPF, Nro. Receta, Nro. Afiliado, Matrícula Médico, Total, A Cargo OS, Trazabilidad, Plan de Venta, etc. Investigamos dónde vive esa info y agregamos un helper para consultarla.

## TL;DR

- **Tabla**: `Gestion.Recetas` (schema premium, requiere user `sa` — [ver `estrategia_db_observer.md`](estrategia_db_observer.md))
- **Granularidad**: 1 fila por receta (a diferencia de `DW.ProductosVendidos` que es 1 fila por producto vendido)
- **Función**: `buscar_recetas_por_afiliado(numero_afiliado, ...)` en `observer_source.py`
- **Requiere**: acceso al schema `Gestion` — solo Badia lo tiene. Otras farmacias (usuarioDW) obtienen `RuntimeError`.

## Qué vs qué había ya

| Tabla | Granularidad | Campos únicos | Cuándo usar |
|---|---|---|---|
| `DW.ProductosVendidos` | 1 fila por producto en operación | `IdOperador`, `Comprobante_*`, precio del renglón | Reportes de rendición contable (helpers `buscar_recetas`, `buscar_recetas_os`, `buscar_recetas_vendedor`) |
| `Gestion.Recetas` | **1 fila por receta** | **`OPF`**, `NumeroReceta`, `NombreAfiliado`, `MatriculaMedico`, `IdEstadoAsociacionTrazabilidad`, `Autorizada`, `Rendida`, `AjustePRF`, `AutorizacionTelefonica` | Consulta por afiliado, checkup de trazabilidad, control de autorización PAMI |

Complementan: la primera para rendición contable (`ACargoOS` por línea), la segunda para historial médico-legal (¿qué recetó el médico X para el afiliado Y? ¿está autorizada? ¿trazable?).

## Mapa completo — columnas de `Gestion.Recetas` que replican el screenshot

```sql
SELECT
    r.FechaDeOperacion,              -- Fecha de operación
    r.FechaAutorizacionOnLine,       -- Fecha de autorización
    r.OPF,                            -- OPF (código PAMI: '017097...' 14 dígitos)
    p.Descripcion AS Plan,            -- Plan de Venta (JOIN a DW.Planes)
    r.IdEstadoAsociacionTrazabilidad, -- Trazabilidad (ver códigos abajo)
    r.Anulada,                        -- Anulada (bit)
    r.FechaDeVenta,                   -- Fecha de venta
    r.NumeroReceta,                   -- Nro. Receta
    r.NumeroAfiliado,                 -- Nro. Afiliado (varchar, no int)
    r.MatriculaMedico,                -- Matrícula Médico
    r.TotalReceta,                    -- Total Receta
    r.TotalACargoOS                   -- A Cargo OS
  FROM Gestion.Recetas r
  LEFT JOIN DW.Planes p ON p.IdPlan = r.IdPlan
```

**Códigos de trazabilidad** (`IdEstadoAsociacionTrazabilidad`):
- `N` — No requerido
- `P` — Trazabilidad pendiente
- `A` — Asociada (trazada)
- `X` — Excluida
- otros: ver `Gestion.EstadosAsociacionTrazabilidad` si aparece un código nuevo

## Cadena para productos de una receta

Los productos no están en `Gestion.Recetas`. Vienen de:

```sql
SELECT rr.IdReceta, rr.IdProducto, pr.Producto,
       rr.Cantidad, rr.PrecioPVP,
       rr.ImporteRenglon, rr.ImporteACargoOS,
       rr.Rechazado, rr.MotivoRechazo
  FROM Gestion.RecetasRenglones rr
  LEFT JOIN DW.Productos pr ON pr.IdProducto = rr.IdProducto
 WHERE rr.IdReceta = ?
 ORDER BY rr.NumeroRenglon
```

Columnas útiles adicionales en `RecetasRenglones`:
- `DiasTratamiento`, `DosisDiaria`, `Diagnostico` — para checkup médico
- `Fraccionado` — bit, si fue vendido fraccionado
- `PorcentajeCobertura`, `MontoFijo`, `ImporteAporteConvenio` — desglose de la cobertura de la OS

## Otras tablas del schema relacionadas

Encontradas en el mismo grupo (por si son necesarias más adelante):

| Tabla | Rol |
|---|---|
| `Gestion.Recetas` | Cabecera receta (esta) |
| `Gestion.RecetasRenglones` | Ítems / productos por receta |
| `Gestion.RecetasRenglonesCajas` | Ligas a cajas físicas dispensadas |
| `Gestion.RecetasRenglonesOperacionesRenglones` | Link renglón receta ↔ producto vendido en operación (para conciliar receta vs venta) |
| `Gestion.RecetasPagos` | Desglose de pagos por receta (`TotalACargoOS`, `TotalAfiliado`, `Sellado`) |
| `Gestion.RecetasPrescripciones` | Info de la prescripción electrónica |
| `Gestion.RecetasDocumentosImpresos` | Documentos impresos (cupones, tickets) |
| `Gestion.OperacionesRecetas` | Link `IdOperacion` ↔ `IdReceta` |
| `Gestion.Prescripciones` | Prescripciones electrónicas independientes |
| `Cierre.CaratulasRecetas` | Carátulas de rendición a la OS (una vez cerrado el período) |

## Helper agregado — `buscar_recetas_por_afiliado`

Firma:

```python
buscar_recetas_por_afiliado(
    numero_afiliado,           # str o int — NumeroAfiliado exacto
    desde=None, hasta=None,    # date/datetime opcionales
    id_farmacia=None,          # int (default OBSERVER_ID_FARMACIA)
    incluir_anuladas=False,    # bool
    incluir_productos=True,    # bool — hace query extra a RecetasRenglones
    limit=500,
) -> list[dict]
```

**Ejemplo real** (afiliado del screenshot):

```python
from observer_source import buscar_recetas_por_afiliado
recetas = buscar_recetas_por_afiliado('14004503970600', limit=10)

for r in recetas:
    print(f"OPF={r['opf']} Rec={r['numero_receta']} "
          f"Total=${r['total_receta']:.2f} "
          f"Traz={r['trazabilidad_desc']}")
    for p in r['productos']:
        print(f"  · {p['cantidad']}x {p['producto']}")
```

Output real:

```
OPF=01709748488231 Rec=8263320805713 Total=$50639.05 Traz=No requerido
  · 1x AVODART 0.5 mg CAP x   30
OPF=01709745849232 Rec=8263301547441 Total=$52616.02 Traz=Trazabilidad pendiente
  · 1x EPLERONA 25 mg Rec. COM x   30
  · 1x VASOTENAL EZ 10/10 mg COM x   30
OPF=01709745843791 Rec=8263301542712 Total=$142465.47 Traz=No requerido
  · 2x NOSTER D 160/12.5/ 5 mg COM x   28
  · 1x SYNCROCOR 5 mg COM x   28
```

**Estructura del dict devuelto**:

```python
{
    'id_receta': int,
    'opf': str | None,               # None si es venta sin OPF (receta manual)
    'numero_receta': str,
    'numero_afiliado': str,
    'nombre_afiliado': str,
    'matricula_medico': str,
    'plan_id': int,
    'plan_descripcion': str,          # ej. 'Ambulatorio FLK (01/11/24)'
    'fecha_operacion': datetime,
    'fecha_autorizacion': datetime | None,
    'fecha_venta': datetime,
    'total_receta': float,
    'total_a_cargo_os': float,
    'total_afiliado': float,
    'trazabilidad_estado': str | None,   # 'N'/'P'/'A'/'X'
    'trazabilidad_desc': str | None,     # ya traducido
    'anulada': bool,
    'autorizada': bool,
    'rendida': bool,
    'productos': [
        {'id_producto': int, 'producto': str, 'cantidad': int,
         'precio_pvp': float, 'importe_renglon': float,
         'importe_a_cargo_os': float, 'rechazado': bool},
        ...
    ]
}
```

## Consideraciones

- **Requiere schema Gestion** — solo Badia (user `sa`). Otras farmacias tiran `RuntimeError`. Si querés un fallback: envolver en try/except y degradar a `buscar_recetas` que usa `DW.ProductosVendidos` (sin OPF, sin Trazabilidad).
- **Performance**: la query principal es indexada por `NumeroAfiliado` + `IdFarmacia` + `FechaDeOperacion`. Rango de 1 año típico (~500 recetas) devuelve en <1s. Para `incluir_productos=True`, agrega 1 query extra sobre `RecetasRenglones` con `IN (...)` de todas las IdReceta encontradas — sigue siendo rápido en la práctica.
- **`OPF` puede ser NULL** — recetas sin autorización online (formularios manuales, prescripciones sin OPF PAMI). Manejar con `.get('opf') or '(sin OPF)'` al mostrar.
- **`FechaAutorizacionOnLine`** también puede ser NULL en recetas manuales.

## Casos de uso potenciales

1. **Ficha del afiliado** en `/pacientes/<id>`: mostrar el histórico completo de recetas con productos, similar al PDV.
2. **Checkup PAMI**: cruzar `Gestion.Recetas` (lo dispensado) con obligaciones PAMI (medicamentos crónicos por afiliado) para detectar faltantes. Encaja con [feature_checkup_recetas_pami.md](feature_checkup_recetas_pami.md).
3. **Alerta de trazabilidad pendiente**: query filtrado por `IdEstadoAsociacionTrazabilidad = 'P'` y sin dispensa aún — recetas que necesitan resolverse antes del cierre.
4. **Auditoría por matrícula médico**: recetas de un médico específico en un rango, para detectar patrones raros.
