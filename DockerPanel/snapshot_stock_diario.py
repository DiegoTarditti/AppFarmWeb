"""Snapshot diario de obs_stock — standalone, para Windows Task Scheduler.

Corre 1 INSERT idempotente que copia obs_stock entero como fotografía del día.
Diseñado para ejecutarse vía Task Scheduler de Windows (o cron en Linux) a una
hora fija (ej. 22:00), independientemente de que el DockerPanel esté abierto.

USO:
    python snapshot_stock_diario.py [project_dir]
    # project_dir = directorio del repo con docker-compose.yml
    # (si no se pasa, usa el directorio actual)

WINDOWS TASK SCHEDULER (una vez por instalación):
    1) Abrir Task Scheduler → Create Task
    2) General: nombre 'Snapshot Stock Farmacia'; Run whether logged on or not
    3) Triggers: Daily, start at 22:00
    4) Actions: Start a program
       Program: C:\\Path\\To\\python.exe
       Arguments: snapshot_stock_diario.py
       Start in: E:\\AppFarmWeb\\DockerPanel
    5) Conditions: Wake the computer to run this task (recomendado)

EXIT CODES:
    0 → snapshot creado o ya existía hoy (todo OK)
    1 → error (ver stderr)
"""
import subprocess
import sys
from pathlib import Path


def snapshot_stock(project_dir: Path) -> int:
    if not (project_dir / 'docker-compose.yml').is_file():
        print(f'ERROR: no se encontró docker-compose.yml en {project_dir}',
              file=sys.stderr)
        return 1

    sql = (
        "INSERT INTO obs_stock_snapshot_diario "
        "(fecha, id_farmacia, producto_observer, stock_actual) "
        "SELECT CURRENT_DATE, id_farmacia, producto_observer, stock_actual "
        "FROM obs_stock "
        "ON CONFLICT (fecha, id_farmacia, producto_observer) DO NOTHING"
    )
    try:
        r = subprocess.run(
            ['docker-compose', 'exec', '-T', 'db', 'psql',
             '-U', 'postgres', 'farmacia', '-c', sql],
            cwd=str(project_dir), timeout=120, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if r.returncode == 0:
            out = r.stdout.decode('utf-8', 'replace').strip()
            print(f'OK: {out}')
            return 0
        err = r.stderr.decode('utf-8', 'replace')[:500]
        print(f'ERROR: psql rc={r.returncode}: {err}', file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print('ERROR: timeout (>120s)', file=sys.stderr)
        return 1
    except FileNotFoundError:
        print('ERROR: docker-compose no encontrado en PATH', file=sys.stderr)
        return 1
    except Exception as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    arg_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    sys.exit(snapshot_stock(arg_dir.resolve()))
