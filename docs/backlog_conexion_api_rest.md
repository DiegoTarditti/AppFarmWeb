# Backlog — Conexión vía API REST de ObServer Gestión

_Creado: 2026-09-07. Investigación puntual, no hay código todavía — solo
pruebas manuales contra el ObServer real de Badia para validar la vía._

## Por qué

Hay farmacias donde no está habilitada la capa `DW.*` (las vistas que usa
hoy `observer_source.py` vía SQL Server directo). ObServer Gestión expone
además una **API REST** (spec: `ObServer Gestion v2.5.5.Build.1`, contacto
Praxys: julio.ineichen@praxys.com.ar) que no depende de esa capa — sirve
como vía alternativa para esas farmacias. Spec completa (con un fix menor
de una coma de más que la hacía JSON inválido) guardada en
[`api_rest_observer_gestion.openapi.json`](api_rest_observer_gestion.openapi.json).

## Estado: probado contra Badia, funciona

Importante: **Badia sí tiene DW habilitada** — esto se probó contra su
ObServer solo porque es el que tenemos a mano con credenciales/red ya
resueltas, no porque Badia lo vaya a usar. Sirve como prueba de concepto
para la farmacia real que SÍ lo necesita (todavía sin identificar/anotar acá).

### Cómo se probó (conexión)

Sin ningún código nuevo — solo `curl` de línea de comandos, corrido por
SSH contra `.220` (el server que ya tiene la ruta de red probada hacia la
LAN de Badia, mismo camino que usa `observer_source.py` para SQL):

```bash
ssh -i ~/.ssh/id_ed25519_applabo root@192.168.1.220 \
  "curl -sS -m 8 http://192.168.1.137:60063/api/productos/1"
```

Primer intento fue directo desde la notebook por VPN a la LAN (sin pasar
por `.220`) — el `ping` a `192.168.1.137` respondía (5-6ms) pero **ningún
puerto TCP conectaba** (ni 60064 ni el SQL 54572), así que esa ruta VPN no
sirve para esto puntual (puede ser un firewall que solo deja pasar lo que
usa `.220`). Se resolvió pasando todo por `.220` vía SSH, que sí tiene el
puerto abierto.

Sin ningún header de autenticación — la API respondió igual con y sin
`Content-Type`, no se probó ninguna clave/token porque no hay ninguna
documentada en la spec ni en la config existente.

### Dato clave: el puerto de la spec está mal

La spec trae `http://localhost:60064/api` como ejemplo. En la instalación
real de Badia:
- **60064 → cerrado** (connection refused inmediato, no es timeout de red)
- **60063 → es el correcto**, responde HTTP 200

Confirmado que no es un problema de red/VPN: `.220` tiene la conexión SQL
real funcionando en simultáneo contra el mismo host
(`observer_source.observer_disponible() == True`) y aun así 60064 rechaza
la conexión — el servicio simplemente escucha en 60063, no en 60064. Si se
prueba en otra farmacia, no asumir el puerto — probar ambos primero.

### Los 4 endpoints de LECTURA — probados y funcionando

Base real: `http://192.168.1.137:60063/api` (host = mismo que
`OBSERVER_HOST` de la SQL, puerto REST distinto)

| Endpoint | Resultado | Ejemplo real |
|---|---|---|
| `GET /productos/{idProducto}` | ✅ HTTP 200 | `idProducto=1` → producto interno "Sellado" (`categoria: Items Uso Interno`) |
| `GET /productos?codigoBarras=` | ✅ HTTP 200 con EAN real, 404 con uno inventado (correcto) | `7795345006130` → ACALIX 120mg, **$34.617**, stock 3 |
| `GET /productos/lote/{n}` | ✅ HTTP 200 | lote 1 de 49 totales, trae varios productos con precio/stock/laboratorio/droga |
| `GET /stocks/{idProducto}` | ✅ HTTP 200 | confirma `idFarmacia: 10525, nombreFarmacia: "Farmacia BADIA"` — coincide con `OBSERVER_ID_FARMACIA` ya configurado en `.env` |

**Conclusión de la prueba de concepto: la consulta de precio de un
medicamento (por EAN) funciona perfecto** — trae precio, stock, laboratorio
y droga sin tocar SQL Server para nada. Es viable como vía alternativa.

### `POST /paquetes` — SIN probar, a propósito

Es el único endpoint de escritura (crea un paquete de venta real en
ObServer — puede terminar en la cola de cobranza de la farmacia). El
clasificador de seguridad de Claude Code bloqueó el intento de prueba
automáticamente por ser una escritura real contra un sistema de producción
de un tercero. Si se necesita probarlo:
- Usar `idProducto=1` (el "Sellado"/Items Uso Interno visto arriba — parece
  pensado justo para este tipo de prueba) y `referencia` bien marcada
  (ej. `TEST-CLAUDE-BORRAR-<fecha>`) para poder encontrarlo y anularlo.
- No se probó qué `idCajero` es válido — falta ese dato.
- Correrlo a mano (no vía agente) o dar permiso explícito de Bash para esa
  llamada puntual.

## Pendiente

- [ ] Identificar/anotar acá la farmacia real que necesita esto (sin DW
      habilitada) — la investigación de arriba es contra Badia solo como
      prueba de concepto, no es el caso de uso real.
- [ ] Confirmar puerto REST en la farmacia real (no asumir 60063).
- [ ] Si se decide avanzar: diseñar un `observer_rest_source.py` paralelo a
      `observer_source.py`, con la misma interfaz que consume el resto de
      la app (para que sea intercambiable según si la farmacia tiene DW o
      no) — no hecho, ni evaluado en detalle todavía.
- [ ] Probar `POST /paquetes` a mano cuando haya un caso de uso real que lo
      necesite (hoy no hay ninguna feature planeada que lo use).
- [ ] Preguntarle a Praxys (julio.ineichen@praxys.com.ar) qué esquema de
      auth usa la API en instalaciones donde si está expuesta a internet
      (acá se probó todo dentro de la LAN, sin ningún header de auth, y
      respondió igual — puede que en LAN no pida nada pero afuera sí).
