# Tailscale + VSCode Remote SSH — guía paso a paso

Editar y operar la PC de la farmacia desde tu laptop como si fuese local. Sin abrir
puertos al mundo, sin IP pública. Tailscale arma una "VPN privada" entre tus
dispositivos (gratis hasta 100 nodos en la cuenta personal), y VSCode con la
extensión Remote-SSH abre una ventana donde el editor corre local pero el
filesystem, terminal, debugger y extensiones corren en la farmacia.

> **Glosario rápido**
> - **Laptop** = tu PC de oficina/casa desde donde editás.
> - **Farmacia** = la PC `Informatica` que tiene `c:\AppFarmWeb` y Docker.
> - **Tailnet** = tu red privada Tailscale (ej. `tu-nombre.ts.net`).

---

## Pre-requisitos

- [ ] Cuenta de Tailscale (te logueás con Google/Microsoft/GitHub — no hace falta crear nada).
- [ ] Acceso de administrador a la PC de la farmacia (para instalar OpenSSH Server).
- [ ] VSCode en tu laptop con la extensión **Remote - SSH** de Microsoft.

---

## Parte 1 — En la PC farmacia (una sola vez)

### 1.1 Instalar Tailscale

1. Ir a [tailscale.com/download](https://tailscale.com/download) → descargar el installer Windows.
2. Instalar (next, next, finish).
3. Aparece ícono en la systray (esquina inferior derecha) → click derecho → **Log in...**
4. Te abre el browser → loguearte con la **misma cuenta** que vas a usar en la laptop.
5. Aceptar conexión del device.
6. Click derecho en el ícono → **Status** → anotar:
   - **Tu hostname Tailscale**: algo como `farmacia.tu-tailnet.ts.net`
   - **Tu IP Tailscale**: `100.x.y.z` (también sirve, más estable que el hostname)

### 1.2 Habilitar OpenSSH Server (Windows lo trae built-in)

1. **Settings** → **Apps** → **Optional features** → **Add a feature** → buscar
   `OpenSSH Server` → tildar → Install (1-2 min).
2. Win+R → `services.msc` → buscar `OpenSSH SSH Server`:
   - Botón derecho → **Properties**:
     - Startup type: `Automatic`
     - Service status: `Start`
   - Apply.
3. Verificar que está escuchando: en una terminal PowerShell:
   ```powershell
   Get-Service sshd
   Test-NetConnection -ComputerName localhost -Port 22
   ```
   Debe decir `Status: Running` y `TcpTestSucceeded: True`.

### 1.3 Configurar tu user para SSH

Asumiendo que el user de la farmacia es `Informatica`:

1. Abrir PowerShell como ese user (NO como admin) y ejecutar:
   ```powershell
   New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.ssh" | Out-Null
   New-Item -ItemType File -Force -Path "$env:USERPROFILE\.ssh\authorized_keys" | Out-Null
   ```
2. Permisos restrictivos al archivo (Windows OpenSSH es estricto):
   ```powershell
   icacls "$env:USERPROFILE\.ssh\authorized_keys" /inheritance:r /grant:r "${env:USERNAME}:F"
   ```

> Por ahora dejá `authorized_keys` vacío. Lo llenás en la **Parte 2.3** con tu clave pública desde la laptop.

### 1.4 Firewall (Windows debería autoabrirlo)

Si SSH no responde desde la laptop, agregar regla manual:
```powershell
New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
```

---

## Parte 2 — En tu laptop (una sola vez)

### 2.1 Instalar Tailscale

Mismo procedimiento que en farmacia: descargar, instalar, loguearte con la **misma cuenta**.

Al loguearte, en el systray click derecho → **Status** → debería listar la farmacia
en "Other devices". Si la ves, las dos máquinas se ven entre sí. ✅

### 2.2 Generar SSH key (si no tenés una)

En PowerShell o bash de tu laptop:

```bash
ssh-keygen -t ed25519 -C "diego@laptop"
```

Apretar Enter para aceptar default (`~/.ssh/id_ed25519`). Passphrase opcional pero
recomendada (te la pide cada vez que conectás, o usás `ssh-agent` para cachearla).

Te genera dos archivos:
- `~/.ssh/id_ed25519` — privada, **nunca compartir**
- `~/.ssh/id_ed25519.pub` — pública, esta es la que va a la farmacia

### 2.3 Copiar la clave pública a la farmacia

**Opción A — desde la laptop (más fácil):**

```bash
type %USERPROFILE%\.ssh\id_ed25519.pub | ssh Informatica@farmacia.tu-tailnet.ts.net "cat >> .ssh/authorized_keys"
```

(En PowerShell `type` ya funciona; en bash de Git for Windows usás `cat`.)

> Te pide la password de Windows del user `Informatica`. Después de esto, ya no la pide más.

**Opción B — manual:**

1. En tu laptop: abrir `~/.ssh/id_ed25519.pub` con bloc de notas, copiar todo el contenido.
2. En la farmacia: abrir `C:\Users\Informatica\.ssh\authorized_keys` con bloc de notas.
3. Pegar la línea (debe quedar en una sola línea, empezando con `ssh-ed25519 ...`).
4. Guardar.

### 2.4 Configurar `~/.ssh/config`

Crear o editar `~/.ssh/config` (en Windows: `%USERPROFILE%\.ssh\config`):

```sshconfig
Host farmacia
    HostName farmacia.tu-tailnet.ts.net
    User Informatica
    IdentityFile ~/.ssh/id_ed25519
    ServerAliveInterval 60
    ServerAliveCountMax 3
```

> Reemplazar `farmacia.tu-tailnet.ts.net` con el hostname real que anotaste en 1.1. Si
> el hostname es problemático, usar la IP `100.x.y.z` directamente.

### 2.5 Probar SSH desde la terminal

```bash
ssh farmacia
```

Debería abrirte una shell remota. Si pide password en lugar de aceptar la key,
revisar que el contenido de `authorized_keys` esté bien (una sola línea, sin saltos
de línea Windows raros — usar `notepad++` o equivalente).

Para salir: `exit`.

---

## Parte 3 — VSCode Remote-SSH

### 3.1 Instalar la extensión

En VSCode:
- `Ctrl+Shift+X` → buscar `Remote - SSH` (de Microsoft) → Install.

### 3.2 Conectar

1. `Ctrl+Shift+P` → escribir `Remote-SSH: Connect to Host...` → enter.
2. Selecciona `farmacia` de la lista (que sale del `~/.ssh/config`).
3. Te pregunta el OS remoto: **Windows**.
4. Espera 1-2 min la primera vez (descarga VSCode Server en la farmacia, ~50 MB).
5. Al terminar, abajo a la izquierda te dice `SSH: farmacia` en verde. Estás conectado.

### 3.3 Abrir el repo

- `File → Open Folder` → `C:\AppFarmWeb` → Open.
- Te pregunta si confiás en los autores → Yes.
- Listo. Editás como local.

### 3.4 Terminal integrada (corre en la farmacia)

`Ctrl+ñ` (o `Ctrl+\``) abre terminal en la farmacia. Probás:

```powershell
git status
docker-compose ps
```

Te muestra el estado real de la farmacia.

### 3.5 Port forwarding automático

Si en la terminal arrancás algo en `localhost:5000`, VSCode automáticamente lo
forwardea a tu laptop — abrís `http://localhost:5000` en el browser de tu laptop y
te conectás al server de la farmacia.

Manual: pestaña `PORTS` (al lado de `TERMINAL`) → `Forward a Port`.

---

## Workflow típico después del setup

```bash
# Desde tu laptop, abrís VSCode → Connect to Host: farmacia
# Abrís terminal integrada (Ctrl+ñ)
git pull
docker-compose restart web
docker-compose logs -f web
# Editás .py / .html → Ctrl+S → tomó el cambio
# Para deploy a Render: git push (corre desde la farmacia, autenticado con su SSH key/PAT)
```

Indistinguible de estar físicamente en la farmacia.

---

## Troubleshooting

### "Permission denied (publickey)" al conectar
- Revisar permisos de `authorized_keys` en farmacia (paso 1.3).
- Verificar que el contenido de la key pública esté COMPLETO y en una sola línea.
- En la farmacia, ver el log: Event Viewer → Applications and Services Logs →
  OpenSSH → Operational. Te dice qué falló específicamente.

### SSH conecta pero VSCode tarda eternidad
- Primera conexión descarga ~50 MB de VSCode Server. Esperá 5 min.
- Si pasa más: en la farmacia, borrar `C:\Users\Informatica\.vscode-server` y reintentar.

### Tailscale: no veo la farmacia desde la laptop
- Verificar que en ambas estás logueado con la **misma cuenta**.
- Click derecho ícono Tailscale → Status — la farmacia debe aparecer "Connected".
- En la farmacia: cmd `tailscale status` muestra el estado.

### El hostname `*.ts.net` no resuelve
- Tailscale MagicDNS puede estar off. Usar la IP `100.x.y.z` directamente en
  `~/.ssh/config`.
- Reactivar MagicDNS en [login.tailscale.com](https://login.tailscale.com) → DNS.

### Caí offline / Tailscale dropea
- `ServerAliveInterval 60` en `~/.ssh/config` (ya está) hace que VSCode reintente
  cada minuto. Generalmente reconecta solo al volver internet.

---

## Lo que NO reemplaza

Tailscale + VSCode Remote te resuelve "trabajar como si estuviera en la farmacia".
Pero el **panel remoto en Render** sigue siendo útil cuando:

- Estás desde el celular y querés un quick deploy.
- La PC farmacia es de un cliente y no querés instalar Tailscale ahí (su red, no tuya).
- Querés audit trail histórico de "quién deployó qué cuándo".

Los dos sistemas conviven sin pisarse.
