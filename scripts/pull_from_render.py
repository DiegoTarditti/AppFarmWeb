"""Trae un snapshot fresco de la DB de Render y lo restaura en el Postgres local.

Uso:
    python scripts/pull_from_render.py

Qué hace:
    1. Lee RENDER_DATABASE_URL del .env (o del env actual).
    2. docker run --rm postgres:17 pg_dump del remoto → archivo local temporal.
    3. Trunca el schema public del Postgres local (appfarmweb-db-1).
    4. Restaura el dump con psql dentro del contenedor local.
    5. Borra el archivo temporal.

Preservás:
    - Tu .env, tu código, tu contenedor web.
    - El sync del schema: init_db correrá de nuevo al próximo restart y aplicará
      migraciones que agregues localmente sin pisar la data.

NO preservás:
    - Cambios locales sin commit pusheados a la DB — se pierden al restaurar.
"""
import os
import subprocess
import sys
import time


def _read_env(path=None):
    """Lee .env sin dependencias externas. Busca primero en CWD, después en la raíz del proyecto."""
    if path is None:
        # Raíz del proyecto: un nivel arriba de scripts/
        proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidatos = [os.path.join(os.getcwd(), '.env'),
                      os.path.join(proj_root, '.env')]
    else:
        candidatos = [path]
    out = {}
    for p in candidatos:
        if os.path.exists(p):
            with open(p, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    k, v = line.split('=', 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
            break
    return out


def main():
    env_file = _read_env()
    render_url = os.environ.get('RENDER_DATABASE_URL') or env_file.get('RENDER_DATABASE_URL', '')
    if not render_url:
        print('ERROR: RENDER_DATABASE_URL no encontrada (ni en env ni en .env)')
        sys.exit(1)

    if render_url.startswith('postgres://'):
        render_url = render_url.replace('postgres://', 'postgresql://', 1)

    tmp_dump = os.path.abspath('render_dump_pull.sql')
    print(f'1/3 Descargando dump de Render...')
    t0 = time.time()
    with open(tmp_dump, 'wb') as f:
        # pg_dump 18 para que matchee con Render (18.3). Plain SQL es
        # forward-compatible con psql 17 local.
        r = subprocess.run(
            ['docker', 'run', '--rm', 'postgres:18', 'pg_dump',
             '--no-owner', '--no-privileges', '--clean', '--if-exists',
             '--no-comments', render_url],
            stdout=f, stderr=subprocess.PIPE, check=False,
        )
    if r.returncode != 0:
        print(f'   ERROR pg_dump: {r.stderr.decode("utf-8", errors="replace")[:500]}')
        if os.path.exists(tmp_dump):
            os.remove(tmp_dump)
        sys.exit(2)
    size_mb = os.path.getsize(tmp_dump) / 1024 / 1024
    print(f'   OK - {size_mb:.1f} MB en {time.time() - t0:.1f}s')

    print('2/3 Reseteando Postgres local...')
    r = subprocess.run(
        ['docker', 'exec', 'appfarmweb-db-1', 'psql', '-U', 'postgres', '-d', 'farmacia',
         '-c', 'DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public; '
               'GRANT ALL ON SCHEMA public TO postgres; GRANT ALL ON SCHEMA public TO public;'],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f'   ERROR: {r.stderr[:500]}')
        sys.exit(3)
    print('   OK')

    print('3/3 Restaurando dump en local...')
    t0 = time.time()
    # Copiar al contenedor y restaurar (docker exec no admite stdin binario confiable en Windows)
    subprocess.run(['docker', 'cp', tmp_dump, 'appfarmweb-db-1:/tmp/render_dump_pull.sql'], check=True)
    r = subprocess.run(
        ['docker', 'exec', 'appfarmweb-db-1', 'psql', '-U', 'postgres', '-d', 'farmacia',
         '-f', '/tmp/render_dump_pull.sql', '-q', '--single-transaction'],
        capture_output=True, text=True,
    )
    # psql suele devolver 0 aun con warnings; mostramos últimas líneas
    last_lines = (r.stderr or r.stdout).strip().splitlines()[-10:]
    if last_lines:
        for L in last_lines:
            print(f'   {L}')
    if r.returncode != 0:
        print(f'   ERROR restore (code {r.returncode})')
        sys.exit(4)
    print(f'   OK en {time.time() - t0:.1f}s')

    # Limpieza
    try: os.remove(tmp_dump)
    except OSError: pass
    subprocess.run(
        ['docker', 'exec', 'appfarmweb-db-1', 'rm', '-f', '/tmp/render_dump_pull.sql'],
        capture_output=True,
    )

    # Recomendar restart del web para que init_db aplique migraciones nuevas
    print('\nListo. Reiniciá el contenedor web para aplicar migraciones locales:')
    print('   docker-compose restart web')


if __name__ == '__main__':
    main()
