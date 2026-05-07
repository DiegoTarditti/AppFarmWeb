# AppCajas → Observer · Estado del enlace (pausado)

Trabajo iniciado el 2026-05-07 y pausado para retomarlo después.

## Visión

Reemplazar el adapter CSV de AppCajas (`app/adapters/observer.py` con
watchdog sobre carpeta de exportación) por consultas directas a
`ObServerGestion.*`. Resultado: detección casi en tiempo real, datos más
granulares (cupón a cupón), y se elimina la dependencia del export del POS.

## Lo que ya quedó hecho en `C:\AppCajas`

- `requirements.txt` → agregado `pymssql>=2.3`.
- `.env.example` → sumadas vars `OBSERVER_HOST/PORT/USER/PASS/DB`.
- `app/config.py` → settings `observer_host/port/user/pass/db` +
  property `observer_configurado` (bool).

**Nada de esto se usa todavía** — son agregados de configuración inactivos.
Si se quiere revertir, son 3 archivos chicos.

## Próximo paso al retomar

1. Crear `app/adapters/observer_sql.py` con:
   - `get_engine()` — conexión read-only via pymssql.
   - `ping()` — `SELECT 1` para validar acceso.
   - `get_cierres_recientes(dias=7)` — devuelve cierres con cajero + puesto.
   - `get_ventas_de_cierre(cierre_id)` — devuelve `OperacionesPagos` del cierre.

2. Crear `scripts/test_observer_conn.py` — CLI: ping + lista 5 cierres
   recientes. Sirve para validar credenciales antes de tocar más código.

3. Una vez validado, escribir el adapter completo que devuelve un
   `ObserverCierreData` (mismo shape que el CSV adapter actual) — así no
   hay que tocar el `core/cierre_service.py`.

## Tablas clave para el adapter

| Dato AppCajas | Tabla Observer | Columna |
|---|---|---|
| Fecha + hora cierre | `Gestion.CajasMostradorCierres` | `FechaDesde`, `FechaHasta` |
| Cajero (nombre) | `Gestion.Cajeros` | `Descripcion` |
| Puesto (caja) | `Gestion.PuestosDeTrabajo` | `Descripcion` |
| Pagos por operación | `Gestion.OperacionesPagos` | `Importe`, `IdTipoFormaDePagoContable` |
| Lookup medio pago | `Gestion.TiposFormaDePago` | `Descripcion`, `Codigo` |
| Cupones tarjeta | `Gestion.CuponTarjeta` | `NumeroAutorizacion`, `IdTarjeta`, `Importe` |
| Cierre lote tarjetas | `Gestion.TarjetaCierres` | — |

## Datos que faltan confirmar al equipo Observer

Listados también en `docs/pedido_a_observer.pdf` (sección "Específicos
para AppCajas"):

1. **Terminal_Payway_ID por puesto** — no aparece en `PuestosDeTrabajo`
   ni en `Cajeros`. ¿Vive en alguna otra tabla? ¿O lo configuramos local?
2. **Concepto de "Turno"** (Mañana/Tarde/Noche) — `CajasMostradorCierres`
   no tiene un campo Turno explícito. Confirmar si se infiere por hora
   de `FechaDesde` o si es 1 turno = 1 sesión cajero.
3. **Detección en tiempo real del cierre** — polling vs trigger vs webhook.

## Decisiones de diseño tomadas

- AppCajas mantiene su DB SQLite local (no necesita acceso al Postgres
  de AppFarmWeb).
- El adapter SQL se conecta directo a Observer SQL Server (mismo target
  que el `observer_source.py` de AppFarmWeb).
- Los dos sistemas son independientes a nivel data — solo comparten la
  **fuente de verdad** Observer.
- Nada de los syncs de AppFarmWeb (`obs_*` en Postgres) es relevante para
  AppCajas.

## Referencias

- PDF completo del pedido: [`docs/pedido_a_observer.pdf`](pedido_a_observer.pdf)
- Schema explorer: `/observer/schema` en AppFarmWeb (si es local).
- SQL playground (validar queries antes): `/observer/sql` en AppFarmWeb.
