"""Seed inicial de usuarios reales de la farmacia (mayo 2026).

Datos transcriptos del listado físico "FARMACIA BADIA 2025". Idempotente:
si el username ya existe, NO lo pisa (preserva password y rol actuales).

Uso:
    docker compose exec web python scripts/seed_usuarios_farmacia.py
"""
import os
import sys

# Permitir ejecutar desde la raíz del repo.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from auth import hash_password
from database import Usuario

# (nombre_completo, username, password, rol)
USUARIOS = [
    ('ANAHI',           '07',       '07',     'farmacia'),
    ('ANDRES',          'ANDRES',   'pep',    'farmacia'),
    ('BEÑAT',           '182',      '182',    'farmacia'),
    ('CECILIA',         'cec',      'cec',    'farmacia'),
    ('DARIO',           'Dario',    '797',    'farmacia'),
    ('EDUARDO',         'ers',      '1668',   'farmacia'),
    ('ESEBAN F',        '08',       'srp',    'farmacia'),
    ('ESTEFANIA',       'est',      'est',    'farmacia'),
    ('EZEQUIEL',        '15',       '1515',   'farmacia'),
    ('GLADIS',          'GLADIS',   'gla',    'farmacia'),
    ('FLORENCIA',       'flor',     '402',    'farmacia'),
    ('FRANCO',          '37',       '37',     'farmacia'),
    ('GUILLERMO',       'guille',   '3539',   'farmacia'),
    ('JAQUELINA',       '585',      '585',    'farmacia'),
    ('JAVIER',          'javi',     '2505',   'farmacia'),
    ('KSOFT',           'KSOFT',    'K',      'farmacia'),
    ('LISANDRO',        '03',       'manu',   'farmacia'),
    ('MANUEL',          'manuel',   '6268',   'farmacia'),
    ('MARCELO',         'mar',      '125',    'farmacia'),
    ('MARIELA',         '447',      '447',    'farmacia'),
    ('MICAELA',         '23',       '27',     'farmacia'),
    ('PIEREISTEI',      'ESTEBAN',  '02',     'farmacia'),
    ('RENZO',           'renzo',    'r',      'farmacia'),
    ('SANTIAGO',        '377',      '377',    'farmacia'),
    ('SILVIA',          'SILVIA',   '84',     'farmacia'),
    ('SUPERVISOR',      'super',    's',      'farmacia'),
    ('VIRGINIA',        '216',      '216',    'farmacia'),
]


def main():
    database.init_db()
    created, skipped = [], []
    with database.get_db() as session:
        for nombre, username, password, rol in USUARIOS:
            # Normalizar a lowercase porque el login también lo hace
            # (request.form.get('username').lower()).
            username = username.lower()
            existente = session.query(Usuario).filter_by(username=username).first()
            if existente:
                skipped.append((nombre, username))
                continue
            u = Usuario(
                username=username,
                nombre_completo=nombre,
                password_hash=hash_password(password),
                rol=rol,
                activo=True,
                debe_cambiar_password=False,
                permisos_json='{}',
            )
            session.add(u)
            created.append((nombre, username, rol))
        session.commit()
    print(f'\n✓ Creados: {len(created)}')
    for n, u, r in created:
        print(f'  - {u:12s} ({n})  rol={r}')
    print(f'\n· Ya existían (no se tocaron): {len(skipped)}')
    for n, u in skipped:
        print(f'  - {u:12s} ({n})')
    print()


if __name__ == '__main__':
    main()
