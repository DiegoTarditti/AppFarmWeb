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

## Fix pendiente (requiere reboot a BIOS)

1. Reboot, entrar al BIOS (F2/F10/F12/Del según fabricante).
2. Habilitar `Intel Virtualization Technology` (Intel) o `SVM Mode` (AMD). Suele estar en Advanced / CPU / Security.
3. Guardar y reiniciar.
4. Verificación post-boot: `systeminfo` debe decir `Se habilitó la virtualización en el firmware: Sí`.
5. Abrir Docker Desktop → debería crear la distro `docker-desktop` automáticamente y arrancar el engine.

## Diagnósticos que confirman la causa

- WSL 2.6.3.0, kernel 6.6.87.2 instalado correctamente.
- `WSLService` Running, `vmcompute` Running — el stack software está OK.
- Sin distros instaladas: WSL las acepta crear pero después no pueden iniciar.
- Backend Docker (4.72.0) loop infinito esperando init API de la VM (`/ping context deadline exceeded`).
