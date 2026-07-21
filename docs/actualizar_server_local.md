# Actualizar el server local (192.168.1.220) — runbook

Cómo pasar código nuevo de GitHub al server de la farmacia, desde cero: VPN,
login, deploy y verificación. Escrito el 2026-07-21 después de hacerlo a mano
la primera vez.

**Resumen de una línea**: conectás la VPN → `ssh diego@192.168.1.220` → `su -`
→ `cd /root/appfarmweb` → `./actualizar.sh`.

| Dato | Valor |
|---|---|
| Server | `debian13-IA` · Debian 13 · `192.168.1.220` |
| Repo | `/root/appfarmweb` ⚠ bajo `/root`, **todo deploy necesita root** |
| App | `http://192.168.1.220:5000` |
| Portainer | `https://192.168.1.220:9443` |
| Usuario SSH | `diego` (con password, no hay key) |
| Containers | `appfarmweb-web-1`, `appfarmweb-db-1`, `appfarmweb-observer_db-1`, `portainer` |

---

## 1. Conectar la VPN

Abrir **OpenVPN GUI** y conectar. Después verificar desde PowerShell:

```powershell
Test-NetConnection 192.168.1.220 -Port 5000
```

Tiene que decir `TcpTestSucceeded : True`, y en `InterfaceAlias` va a figurar
`OpenVPN TAP-Windows6` — eso confirma que sale por el túnel.

> **No uses `ping` a secas.** El ICMP suele estar filtrado y te da un falso
> negativo con la VPN funcionando perfecto. Siempre `Test-NetConnection -Port`.

Si querés chequear los tres puertos de una:

```powershell
5000,22,9443 | % { "$_ = $((Test-NetConnection 192.168.1.220 -Port $_ -WarningAction SilentlyContinue).TcpTestSucceeded)" }
```

## 2. Entrar por SSH

```powershell
ssh diego@192.168.1.220
```

Pide la password de `diego`. **No hay key instalada** para este usuario, así
que no se puede automatizar todavía (ver *Mejoras pendientes* abajo).

## 3. Pasar a root

```bash
su -
```

Pide la password de **root** (distinta a la de `diego`).

> **Por qué es obligatorio**: este Debian **no tiene `sudo` instalado**, y
> `diego` **no está en el grupo `docker`**, así que sin root ni siquiera podés
> correr `docker ps`. Encima el repo vive en `/root/appfarmweb`, que `diego` ni
> puede leer. Los tres motivos apuntan a lo mismo: para deployar, root.

## 4. Actualizar

```bash
cd /root/appfarmweb
./actualizar.sh
```

El script hace todo solo:

1. `git pull --ff-only` — falla claro si el server tiene commits locales
2. Si no hubo cambios, corta ahí con *"Ya estabas al día"*
3. Si cambió `requirements.txt` o el `Dockerfile` → `docker compose up -d --build`
4. Si no → `docker compose restart` (segundos)
5. Espera 8 s, muestra `docker compose ps`
6. Health check contra `/health`

Salida esperada al final:

```
  http://localhost:5000/health → HTTP 200
✓ Actualizado a <commit> y respondiendo OK.
```

Si termina en otra cosa, el script sale con código 1 y te dice qué mirar.

### Migraciones de base de datos

**No hay paso manual.** `docker-compose.yml` setea
`RUN_INIT_DB_ON_STARTUP: "1"` en el servicio `web`, así que `init_db()` corre
en cada arranque y las migraciones inline se aplican solas con el restart.

> Ojo con esto: **por default en el código, `init_db` NO corre al arrancar**
> (`app.py` lo gatea por esa env var, ver `docs/lecciones_deploy_render.md`).
> Es la config de *este* server la que lo activa. En Render el flujo es otro.

## 5. Verificar

El health check del script ya cubre lo básico. Para confirmar que el cambio
concreto llegó, entrá por el navegador a `http://192.168.1.220:5000` y probá la
pantalla que tocaste.

Ver el commit en que quedó el server:

```bash
git log --oneline -1
```

Logs si algo se ve raro:

```bash
docker compose logs web --tail=50
```

---

## Problemas conocidos

**`sudo: orden no encontrada`** — no está instalado. Usá `su -`.

**`permission denied ... /var/run/docker.sock`** como `diego`** — no está en el
grupo `docker`. Necesitás `su -`.

**`su: Fallo de autenticación`** — es la password de **root**, no la de `diego`.

**El script aborta en el `restart`** — tiene `set -e`, así que corta en el
primer error. Si ya pasó el `git pull` y el restart de `web`, **el deploy está
hecho igual**; lo que falló es un paso posterior. Verificá con el health check
a mano:

```bash
curl -fsS -o /dev/null -w "health: %{http_code}\n" http://localhost:5000/health
```

**El script hace `restart web bot`, pero el bot no corre.** Hoy no da error
(Compose lo ignora). Si algún día levanta el container del bot y tenés el bot
corriendo también en tu PC, Telegram tira `Conflict: terminated by other
getUpdates`. En ese caso, restart solo del web:

```bash
git pull --ff-only && docker compose restart web
```

**`git status` muestra archivos modificados** — hay ediciones hechas a mano en
el server. `git pull --ff-only` igual pasa si el pull no toca esos archivos,
pero miralas antes (`git diff <archivo>`): o se commitean, o se descartan.
Si quedan sueltas, el día que un pull toque ese archivo da conflicto.

## Volver atrás

El deploy es un fast-forward, así que revertir es moverse al commit anterior:

```bash
cd /root/appfarmweb
git log --oneline -5          # elegir el commit previo
git checkout <commit>         # deja el repo en detached HEAD
docker compose restart web
```

Para volver a la punta: `git checkout main`.

> **La data no se toca en ningún caso**: vive en los volúmenes de Postgres
> (`pgdata`, `observer_pgdata`), que son independientes del código. Hay backup
> diario a las 03:00 en `/root/backups/` con rotación de 14 días.

## Mejoras pendientes (evitan el `su -` de cada vez)

Ninguna es urgente, las tres se hacen una sola vez con root:

- [ ] **Copiar SSH key** para entrar sin password y poder automatizar:
  ```powershell
  type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh diego@192.168.1.220 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
  ```
- [ ] **`usermod -aG docker diego`** — maneja docker sin root (toma efecto al
  reloguear). Tené en cuenta que estar en el grupo `docker` equivale a root en
  la práctica: en un server de una persona está bien, pero que sea una decisión.
- [ ] **Mover el repo a `/opt/appfarmweb`** con permisos de grupo. Mientras
  viva en `/root`, el deploy va a necesitar root aunque hagas las dos de arriba.
