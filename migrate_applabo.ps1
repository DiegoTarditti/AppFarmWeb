# Migra Db-Labo (Postgres standalone en Render) → farmacia-db.applabo (mismo Postgres, schema separado).
# Pasos: dump → transformar public.→applabo. (preservando datos COPY intactos) → CREATE SCHEMA → restore.
# Correr desde local: ./migrate_applabo.ps1

$src = "postgresql://db_labo_user:kWjRkb86hrOTOqc451tRyXSivnE4f8yg@dpg-d80j2o67r5hc73bmi3sg-a.oregon-postgres.render.com/db_labo"
$dst = "postgresql://database_user:OVisrG0mYcewDaNawLbXc1dZzp8imo7g@dpg-d7g1erf7f7vs73bmfijg-a.oregon-postgres.render.com/farmacia_yhvp"

if (-not (Test-Path dumps)) { New-Item -ItemType Directory dumps | Out-Null }
if (Test-Path dumps/applabo_raw.sql) { Remove-Item dumps/applabo_raw.sql }
if (Test-Path dumps/applabo_transformed.sql) { Remove-Item dumps/applabo_transformed.sql }

# 1) Dump del schema public de Db-Labo (schema + data) → archivo plano UTF-8 sin BOM
Write-Host "=== 1/4 Dump Db-Labo ===" -ForegroundColor Cyan
$dumpCmd = "pg_dump --no-owner --no-privileges --schema=public '$src' > /out/applabo_raw.sql"
docker run --rm -v "${PWD}/dumps:/out" postgres:18 sh -c $dumpCmd
if ($LASTEXITCODE -ne 0) { Write-Error "pg_dump failed"; exit 1 }
$sizeMB = [math]::Round((Get-Item dumps/applabo_raw.sql).Length / 1MB, 2)
Write-Host "    dump: $sizeMB MB" -ForegroundColor Gray

# 2) Transformar public.→applabo. SOLO fuera de bloques COPY (para no tocar data literal).
#    Streaming line-by-line para soportar dumps grandes sin cargar todo en RAM.
Write-Host "=== 2/4 Transform public → applabo ===" -ForegroundColor Cyan
$reader = [System.IO.StreamReader]::new("$PWD\dumps\applabo_raw.sql")
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$writer = [System.IO.StreamWriter]::new("$PWD\dumps\applabo_transformed.sql", $false, $utf8NoBom)
$inCopy = $false
$lines = 0
try {
    while (-not $reader.EndOfStream) {
        $line = $reader.ReadLine()
        $lines++
        if ($inCopy) {
            if ($line -eq '\.') { $inCopy = $false }
            $writer.WriteLine($line)
            continue
        }
        if ($line -match '^COPY\s+.*FROM stdin;') {
            $line = $line -replace '\bpublic\.', 'applabo.'
            $inCopy = $true
            $writer.WriteLine($line)
            continue
        }
        # Saltar refs a schema/owner public (applabo se crea aparte; owner ya quitado por --no-owner).
        if ($line -match '^(CREATE SCHEMA public|ALTER SCHEMA public|COMMENT ON SCHEMA public)') {
            $writer.WriteLine("-- skipped: $line")
            continue
        }
        $line = $line -replace '\bpublic\.', 'applabo.'
        $writer.WriteLine($line)
    }
} finally {
    $reader.Close()
    $writer.Close()
}
Write-Host "    $lines lineas procesadas" -ForegroundColor Gray

# 3) CREATE SCHEMA applabo en farmacia-db (idempotente)
Write-Host "=== 3/4 CREATE SCHEMA applabo en farmacia-db ===" -ForegroundColor Cyan
docker run --rm postgres:18 psql "$dst" -c "CREATE SCHEMA IF NOT EXISTS applabo;"
if ($LASTEXITCODE -ne 0) { Write-Error "CREATE SCHEMA failed"; exit 1 }

# 4) Restore: psql ON_ERROR_STOP para abortar al primer error.
Write-Host "=== 4/4 Restore en farmacia-db.applabo ===" -ForegroundColor Cyan
$restoreCmd = "psql -v ON_ERROR_STOP=1 '$dst' < /out/applabo_transformed.sql"
docker run --rm -v "${PWD}/dumps:/out" postgres:18 sh -c $restoreCmd
if ($LASTEXITCODE -ne 0) { Write-Error "Restore failed"; exit 1 }

Write-Host ""
Write-Host "=== OK ===" -ForegroundColor Green
Write-Host "Smoke test:" -ForegroundColor Yellow
Write-Host "  docker run --rm postgres:18 psql '$dst' -c '\dt applabo.*'"
Write-Host "  docker run --rm postgres:18 psql '$dst' -c 'SELECT schemaname, tablename, n_live_tup FROM pg_stat_user_tables WHERE schemaname=''applabo'' ORDER BY tablename;'"
