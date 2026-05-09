# Docker / WSL no arranca — log de intentos

**Inicio:** 2026-05-08
**Síntoma:** Docker Desktop no arranca el engine. `docker ps` → 500 contra `dockerDesktopLinuxEngine`. Backend (`com.docker.backend.exe`) lleva 3+ min esperando `/ping` del init control API.

## Estado observado (no cambia entre reintentos)

- Docker Desktop **4.72.0** abierto, GUI corriendo (PIDs 4052, 18992, 19320 a las 00:32).
- `com.docker.service` Stopped (Manual).
- `WSLService` Running, `vmcompute` Running, `HvHost` Stopped (Manual).
- `wsl -l -v` → "Subsistema de Windows para Linux no tiene distribuciones instaladas."
- Pipe `dockerBackendApiServer` existe; pipe `dockerDesktopLinuxEngine` existe pero falla en API → no hay backend Linux atrás.
- Distro interna `docker-desktop` ausente. Docker no la auto-recrea al arrancar.
- Logs: `e:\Users\Diego\AppData\Local\Docker\log\host\com.docker.backend.exe.log` muestra spam `engines C<-S ConnectionClosed GET /ping ... context deadline exceeded`.

## Ya probado (NO funcionó)

- [x] Reinstalar Docker Desktop
- [x] `wsl --install` / instalar Ubuntu
- [x] `wsl --update` / `wsl --shutdown`
- [x] Reset to factory defaults en Docker Desktop
- [x] Habilitar/deshabilitar Hyper-V o "Virtual Machine Platform"
- [x] Reiniciar Windows

## CAUSA RAÍZ ENCONTRADA (2026-05-08)

`systeminfo` reporta:
```
Requisitos Hyper-V: Extensiones de modo de monitor de VM: Sí
                    Se habilitó la virtualización en el firmware: No
Seguridad basada en virtualización: Estado: No habilitado
```

**Virtualización deshabilitada en BIOS/UEFI**. CPU la soporta (`VM Monitor Mode Extensions: Yes`) pero está apagada en firmware. WSL2 y Docker no pueden crear ninguna VM Linux sin esto. Explica por qué todos los fixes a nivel software (reinstalar Docker, wsl --install, reset factory, reiniciar Windows, etc.) fallaron.

## Fix BIOS (paso 1 — necesario)

1. Reboot, entrar al BIOS (F2/F10/F12/Del según fabricante).
2. Habilitar `Intel Virtualization Technology` (Intel) o `SVM Mode` (AMD). Suele estar en Advanced / CPU / Security.
3. Guardar y reiniciar.
4. Verificación post-boot: `systeminfo` debe decir `Se habilitó la virtualización en el firmware: Sí` y/o "Se detectó un hipervisor".

## Fix WSL (paso 2 — descubierto 2026-05-08, post-BIOS)

Tras habilitar virtualización en BIOS, Docker Desktop **seguía** colgado esperando el engine 12+ min. `wsl -l -v` mostraba solo Ubuntu, sin `docker-desktop`. Docker no la registra automáticamente al arrancar — pero el `ext4.vhdx` ya estaba en disco.

Solución: importar el VHD existente como distro WSL manualmente.

```powershell
# 1. Cerrar Docker Desktop completamente
Get-Process "Docker Desktop","com.docker.backend","com.docker.build" -EA SilentlyContinue | Stop-Process -Force
wsl --shutdown

# 2. Registrar la distro a partir del VHD que ya existe
wsl --import-in-place docker-desktop "C:\Users\Diego\AppData\Local\Docker\wsl\main\ext4.vhdx"

# 3. Verificar (debe aparecer docker-desktop Stopped V2)
wsl -l -v

# 4. Abrir Docker Desktop normalmente
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
```

Tras esto, `docker ps` responde en ~5s y ambas distros (`Ubuntu`, `docker-desktop`) quedan `Running`.

## Diagnósticos que confirman las causas

- WSL 2.6.3.0, kernel 6.6.87.2 instalado correctamente.
- `WSLService` Running, `vmcompute` Running — el stack software está OK.
- Sin distros instaladas: WSL las acepta crear pero después no pueden iniciar (causa BIOS).
- Backend Docker (4.72.0) loop infinito esperando init API de la VM (`/ping context deadline exceeded`).
- Post-BIOS: `vmmem` no aparecía en procesos → confirmaba que la VM `docker-desktop` no se había instanciado (causa WSL).
