# Primer uso

Lo mínimo para empezar a usar AppFarmWeb productivamente.

## Login

Entrás a `/login`. Usuarios y contraseñas se cargan desde el admin.

### Roles disponibles

| Rol | Acceso |
|---|---|
| **admin** | Todo. Único que puede ver `/admin/*` (utilidades de mantenimiento, reset, sync, cron-log). |
| **dev** | Como admin, pero pensado para el desarrollador. Mismo nivel funcional. |
| **farmacia** | Operación diaria: análisis, pedidos, facturas, reclamos. NO ve admin. |
| **remoto** | Acceso restringido para revisar desde fuera de la farmacia. Lectura mayormente. |

Cuando creás un usuario nuevo, le asignás rol y permisos granulares (ver `auth.py` para el set de permisos).

## Configuración inicial

`/settings` o "Configuración" en el sidebar.

### Datos de la farmacia
- **Nombre** — aparece en encabezado de PDFs y en home.
- **Ruta de facturas** — directorio local donde la app busca PDFs (con DockerPanel + agente).

### Umbrales de análisis de compra
Configuran cómo el sistema clasifica rotaciones y sugiere cantidades:
- **Umbral pico** (default 1.30): factor para considerar un mes "pico".
- **Umbral baja** (default 0.70): factor para considerar un mes "bajo".
- **Umbral tendencia** (default 0.20): cambio porcentual para detectar tendencia.
- **Rotación alta mínimo** (default 20.0 unidades/mes): por encima → rotación A.
- **Rotación media mínimo** (default 5.0 unidades/mes): entre 5 y 20 → M; debajo → B.

Ajustables según el tamaño/perfil de la farmacia. Default funciona bien para una farmacia mediana.

### ObServer (envs)
En el `.env` del Docker (no en la UI):
```
OBSERVER_HOST=192.168.x.x       # IP del SQL Server de ObServer
OBSERVER_PORT=1433
OBSERVER_USER=usuarioDW
OBSERVER_PASS=...
OBSERVER_DB=ObServerGestion
OBSERVER_TDSVER=7.0
OBSERVER_ID_FARMACIA=10525
```

Si no seteás `OBSERVER_HOST`, `observer_disponible()` devuelve `False` y el sync queda deshabilitado. La app sigue funcionando con datos pulleados desde Render.

## DockerPanel (en la PC de la farmacia)

Una app de escritorio en Python+tkinter (`DockerPanel/docker_panel.py`) que corre **en la PC de la farmacia** (no en la app web). Sirve para:

- **Sync ObServer** automático con cron horario.
- **Push a Render** después de cada sync.
- **Pull desde Render** para máquinas remotas.
- **Agente de PDFs** que sube facturas físicas a la app.
- **Backup / restore** de Postgres local.
- **HTTP helper** en puerto 5055 para que la app web pueda consultar archivos locales (carpetas, leer PDFs).

Ejecutás con `python DockerPanel/docker_panel.py` o el `.bat` en la carpeta. Configuras la ruta del proyecto, URL de Render, AUTO_SYNC_TOKEN, y el cron horario.

## Habilitar ObServer

Si todo está en orden:
1. La app web detecta ObServer disponible (`observer_disponible() == True`).
2. El banner de sync aparece verde si hay datos frescos.
3. Los flujos que requieren ObServer (análisis, indicadores, estadísticas por droga) funcionan.

Si no:
- Banner ámbar/rojo arriba de la pantalla.
- Card "Estadísticas al día" en `/procesos` con instrucción "corré el sync desde la PC de la farmacia".

## Diagnóstico rápido

`/admin/dashboard` (admin/dev) muestra:
- Conteos de tablas (proveedores, facturas, productos, pedidos, etc.).
- Info de deploy (commit, branch, URL).
- Estado de la conexión.

`/admin/cron-log` muestra todos los procesos automáticos que corrieron, con duración y estado.

## Términos importantes

- [DockerPanel](./glosario.md#dockerpanel)
- [ObServer](./glosario.md#observer)
