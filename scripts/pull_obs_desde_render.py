"""Pull selectivo: dump solo tablas obs_* desde Render → restore en local.

NO pisa otras tablas (laboratorios, productos, ofertas_minimo, etc.).
Las tablas obs_* en local se truncan y reemplazan con el contenido remoto.

Uso:
    python scripts/pull_obs_desde_render.py
"""
import os
import subprocess
import sys
import time


def _read_env():
    proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(proj_root, '.env')
    out = {}
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


TABLAS_OBS = [
    'obs_laboratorios', 'obs_rubros', 'obs_subrubros', 'obs_nombres_drogas',
    'obs_productos', 'obs_stock', 'obs_ventas_mensuales',
    'obs_grupos_clientes', 'obs_categorias_clientes',
    'obs_obras_sociales', 'obs_convenios', 'obs_planes', 'obs_clientes',
    'obs_sync_log',
]


def main():
    env = _read_env()
    render_url = os.environ.get('RENDER_DATABASE_URL') or env.get('RENDER_DATABASE_URL', '')
    if not render_url:
        print('ERROR: RENDER_DATABASE_URL no encontrada en .env ni en env')
        sys.exit(1)
    if render_url.startswith('postgres://'):
        render_url = render_url.replace('postgres://', 'postgresql://', 1)

    tmp = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'render_obs_dump.sql'))
    print('1/3 Descargando obs_* desde Render…')
    t0 = time.time()
    args = ['docker', 'run', '--rm', 'postgres:18', 'pg_dump',
            '--no-owner', '--no-privileges', '--data-only',
            '--no-comments', render_url]
    for t in TABLAS_OBS:
        args.extend(['-t', t])
    with open(tmp, 'wb') as f:
        r = subprocess.run(args, stdout=f, stderr=subprocess.PIPE, check=False)
    if r.returncode != 0:
        print('ERROR pg_dump:', r.stderr.decode('utf-8', errors='replace')[:500])
        sys.exit(2)
    size_mb = os.path.getsize(tmp) / 1024 / 1024
    print(f'   OK - {size_mb:.1f} MB en {time.time()-t0:.1f}s')

    # Filtrar comandos incompatibles con psql 15
    filtrado = tmp + '.filtered'
    with open(tmp, 'rb') as fi, open(filtrado, 'wb') as fo:
        for line in fi:
            s = line.lstrip()
            if s.startswith(b'\\restrict') or s.startswith(b'\\unrestrict'):
                continue
            if s.startswith(b'SET ') and b'transaction_timeout' in s:
                continue
            fo.write(line)
    os.replace(filtrado, tmp)

    print('2/3 Truncando tablas obs_* locales…')
    truncate_sql = 'TRUNCATE ' + ', '.join(TABLAS_OBS) + ' CASCADE;'
    r = subprocess.run(
        ['docker', 'exec', '-i', 'appfarmweb-db-1',
         'psql', '-U', 'postgres', '-d', 'farmacia', '-c', truncate_sql],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print('ERROR truncate:', r.stderr[:500])
        sys.exit(3)
    print('   OK')

    print('3/3 Restaurando en local…')
    t0 = time.time()
    with open(tmp, 'rb') as f:
        r = subprocess.run(
            ['docker', 'exec', '-i', 'appfarmweb-db-1',
             'psql', '-U', 'postgres', '-d', 'farmacia',
             '-v', 'ON_ERROR_STOP=0', '-q'],
            stdin=f, capture_output=True, text=True,
        )
    print(f'   OK en {time.time()-t0:.1f}s')
    errs = [line for line in r.stderr.split('\n') if 'ERROR' in line][:5]
    if errs:
        print('   (avisos):', errs)
    os.remove(tmp)
    print('\n✓ Listo. Verificá con:')
    print('   docker-compose exec db psql -U postgres -d farmacia -c "select count(*) from obs_productos;"')


if __name__ == '__main__':
    main()
