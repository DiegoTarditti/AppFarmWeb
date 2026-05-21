# Instalación en una farmacia nueva — checklist

> Guía para levantar la app en un servidor de una farmacia nueva. Modelo
> **una instancia por farmacia** (stack Docker propio + su ObServer). Ver el
> porqué (multi-tenant no activado) al final.

---

## 0. Decisión previa

- **Modelo B (este doc): instancia por farmacia.** Cada farmacia tiene su propia
  DB y su propio ObServer. **Es lo que el código soporta hoy.**
- Modelo A (Render compartido para varias farmacias) **NO está listo**: el
  `farmacia_id` existe (default 1) pero no se resuelve por farmacia → los datos
  de dos farmacias se mezclarían. Requiere terminar el multi-tenant antes.

---

## 1. Prerrequisitos del SERVIDOR

- [ ] Servidor que **queda prendido 24/7** (es el puente a ObServer). UPS recomendado.
- [ ] **Docker + docker-compose** instalados (Docker Desktop en Windows Server, o
      Docker Engine en Linux).
- [ ] **git** instalado.
- [ ] El server tiene **red hacia el SQL Server de ObServer** de la farmacia
      (misma LAN o ruta/firewall abiertos).
- [ ] RAM holgada para el primer sync (`ventas_detalle` = millones de filas).

## 2. Prerrequisitos de OBSERVER (pedir al de sistemas / soporte ObServer)

> **Esto es lo más difícil y NO es de la app.** Sin esto, el sync no levanta.

- [ ] El ObServer de la farmacia tiene el **datawarehouse con las vistas `DW.*`**
      (DW.Productos, DW.ProductosVendidos, DW.Stock, DW.Clientes, etc.). Si no las
      tiene, hay que pedir que las habiliten.
- [ ] Un **usuario de SQL Server con permiso de lectura sobre `DW.*`** (ej. `usuarioDW`).
- [ ] **TCP/IP habilitado** en el SQL Server + **puerto abierto** en el firewall.
- [ ] Datos a anotar:
  - `OBSERVER_HOST` (IP del SQL Server) y `OBSERVER_PORT` (típico 1433).
  - `OBSERVER_USER` / `OBSERVER_PASS`.
  - `OBSERVER_DB` (nombre de la base, ej. `ObServerGestion`).
  - **`OBSERVER_ID_FARMACIA`** ← el ID de ESTA farmacia en ObServer (¡no copiar el de otra!).
  - Versión de SQL Server (define `OBSERVER_TDSVER`: 7.0 para SQL Server 2014).

## 3. Instalar la app

```bash
git clone <repo> appfarmweb
cd appfarmweb
cp .env.example .env   # si no existe, crear .env a mano (ver abajo)
# editar .env con los datos de la farmacia
docker-compose up -d --build
```

### `.env` de ejemplo (ajustar TODO por farmacia)

```env
# Puertos host (cambiar si chocan con algo en el server)
WEB_PORT=5000
FARMACIA_DB_PORT=5433

# Seguridad — generar uno nuevo por farmacia
SECRET_KEY=<string-aleatorio-largo-y-unico>

# ObServer (SQL Server) de ESTA farmacia
OBSERVER_HOST=192.168.x.x
OBSERVER_PORT=1433
OBSERVER_USER=usuarioDW
OBSERVER_PASS=********
OBSERVER_DB=ObServerGestion
OBSERVER_TDSVER=7.0
OBSERVER_ID_FARMACIA=<ID-real-de-esta-farmacia>

# IA (matcher de productos) — opcional, tiene costo
ANTHROPIC_API_KEY=

# Solo si esta instancia pushea a una nube Render (Modelo B normal: dejar vacío)
RENDER_DATABASE_URL=
RENDER_BASE_URL=
PANEL_REMOTO_TOKEN=
```

> `RUN_INIT_DB_ON_STARTUP=1` ya está en `docker-compose.yml` → al arrancar crea el
> schema y corre migraciones solo. No hace falta init_db a mano.

## 4. Primer arranque y sync

- [ ] `docker-compose ps` → `web` y `db` en `healthy`.
- [ ] Verificar que ObServer conecta: el botón de sync no debe decir "ObServer no
      configurado". (Si `OBSERVER_HOST` está vacío o no hay red, el sync queda deshabilitado.)
- [ ] Correr el **sync completo** (DockerPanel → "Sync todo", o el botón web). El
      primero tarda (catálogo + ventas históricas).
- [ ] Entrar a `http://<IP_del_server>:<WEB_PORT>` y crear el/los usuarios.

## 5. DockerPanel (opcional pero recomendado)

- GUI local (Windows, tkinter) para sync/backup/restore y el agente de pendientes.
- Su config `agente_config.txt` va **por máquina** (no está en git) → configurarla a mano.

## 6. Verificación final

- [ ] `/productos` muestra el catálogo de la farmacia (no vacío).
- [ ] Una venta reciente aparece en `/consulta-producto` o en estadísticas.
- [ ] `OBSERVER_ID_FARMACIA` correcto (los datos son de ESTA sucursal, no de otra).

---

## Problemas frecuentes

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| Sync dice "ObServer no configurado" | `OBSERVER_HOST` vacío o `pymssql` no conecta | revisar `.env`, red/firewall, TCP del SQL Server |
| Sync conecta pero trae 0 / falla en `DW.*` | faltan las vistas DW o permisos del usuario | tarea del soporte de ObServer |
| Datos de otra sucursal | `OBSERVER_ID_FARMACIA` equivocado | poner el ID real de la farmacia |
| Login a SQL Server falla por versión | TDS version | ajustar `OBSERVER_TDSVER` (7.0 para 2014) |
| La cámara de `/consulta-producto` no abre | requiere HTTPS o localhost | usar por localhost, o reverse proxy con TLS; o tipear el EAN |
| Primer sync OOM / lentísimo | `ventas_detalle` es enorme + server flojo | más RAM, o sync por partes |
| Se apagó la máquina y dejó de sincronizar | el server es el puente a ObServer | UPS + dejar el server siempre prendido |

## Backups

- El volumen `pgdata` tiene toda la DB local. Configurar backup/restore (el
  DockerPanel lo trae) o un dump periódico de Postgres.

## Por qué una instancia por farmacia (y no Render compartido)

El `farmacia_id` existe en el schema (default 1) pero **no hay resolución por
farmacia en runtime** — todas las queries asumen `farmacia_id=1`. Hasta que se
implemente el multi-tenant real (resolver la farmacia por login y filtrar todas
las queries), **cada farmacia debe ser su propia instancia** para no mezclar
datos. Cuando se invierta en multi-tenant, se podrá unificar en un solo Render.
