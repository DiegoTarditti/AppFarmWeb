# panel-server.ps1 — atajo directo por SSH a los comandos del panel remoto,
# sin pasar por Render. Útil cuando ya tenés acceso SSH al server (misma red,
# o clave ya cargada) y no querés esperar el polling de /admin/panel.
#
# No mantiene su propia lista de comandos: en cada corrida le pide al server
# `python3 /root/panel_remoto_worker.py --list` y arma el menú con eso. Si se
# agrega o saca un comando de WHITELIST (scripts/panel_remoto_worker.py), este
# script lo refleja solo, sin tocar nada acá.
#
# Uso:
#   .\scripts\panel-server.ps1                    # menú interactivo
#   .\scripts\panel-server.ps1 actualizar-applabo  # corre uno directo
#   .\scripts\panel-server.ps1 -Comando logs-cajas
#
# Requiere: la clave SSH ya autorizada en el server (ver docs/runbook si hace
# falta generar una nueva) y el cliente ssh de Windows (ssh.exe, ya viene con
# Windows 10/11).

param(
    [Parameter(Position = 0)]
    [string]$Comando,
    [string]$Servidor = "root@192.168.1.220",
    [string]$Clave = "$HOME\.ssh\id_ed25519_applabo"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Clave)) {
    Write-Host "No encuentro la clave SSH en $Clave" -ForegroundColor Red
    Write-Host "Pasá otra con -Clave <ruta>, o generá una y agregala a authorized_keys del server." -ForegroundColor Yellow
    exit 1
}

function Invoke-Remoto {
    param([string]$RemoteCmd)
    & ssh -i $Clave -o BatchMode=yes -o ConnectTimeout=8 $Servidor $RemoteCmd
}

Write-Host "Consultando comandos disponibles en $Servidor..." -ForegroundColor DarkGray
$listaRaw = Invoke-Remoto "python3 /root/panel_remoto_worker.py --list"
if ($LASTEXITCODE -ne 0 -or -not $listaRaw) {
    Write-Host "No se pudo conectar al server o listar comandos (exit $LASTEXITCODE)." -ForegroundColor Red
    exit 1
}
$comandos = $listaRaw -split "`r?`n" | Where-Object { $_.Trim() -ne "" }

if (-not $Comando) {
    Write-Host ""
    Write-Host "== Panel de comandos remotos — $Servidor ==" -ForegroundColor Cyan
    for ($i = 0; $i -lt $comandos.Count; $i++) {
        Write-Host ("  {0,2}) {1}" -f ($i + 1), $comandos[$i])
    }
    Write-Host ""
    $sel = Read-Host "Elegí un número (o Ctrl+C para salir)"
    $idx = 0
    if (-not [int]::TryParse($sel, [ref]$idx) -or $idx -lt 1 -or $idx -gt $comandos.Count) {
        Write-Host "Opción inválida." -ForegroundColor Red
        exit 1
    }
    $Comando = $comandos[$idx - 1]
}

if ($comandos -notcontains $Comando) {
    Write-Host "`"$Comando`" no está en la lista del server. Comandos disponibles:" -ForegroundColor Red
    $comandos | ForEach-Object { Write-Host "  - $_" }
    exit 1
}

Write-Host ""
Write-Host "▶ Ejecutando `"$Comando`" en $Servidor..." -ForegroundColor Yellow
Invoke-Remoto "python3 /root/panel_remoto_worker.py $Comando"
$exitCode = $LASTEXITCODE

Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "✔ OK" -ForegroundColor Green
} else {
    Write-Host "✗ ERROR (exit $exitCode)" -ForegroundColor Red
}
exit $exitCode
