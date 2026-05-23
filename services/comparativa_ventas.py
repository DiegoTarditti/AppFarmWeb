"""Comparativa de ventas semanales entre sucursales (Pieri vs Badia).

Para un mes elegido, agrega las ventas de cada sucursal por SEMANA del mes
(sem 1 = días 1-7, sem 2 = 8-14, …) y por producto, agrupando por laboratorio.
Cruza por `codigo_alfabeta` (universal; el observer_id difiere entre instalaciones).

Devuelve ambas métricas (unidades e importe $) para que el toggle de la UI sea
client-side sin re-fetch. Solo lectura sobre ambas DBs:
Pieri vía `DATABASE_URL`, Badia vía `BADIA_DATABASE_URL` (env).
"""
import calendar
import os
from datetime import date

import sqlalchemy as sa

# Ventas del mes agregadas por (alfabeta, semana). La semana sale de
# (dia-1)//7 → 0..4. Filtro por fecha_estadistica (indexado).
_SQL_VENTAS = sa.text("""
    SELECT op.codigo_alfabeta            AS alfa,
           ((d.dia - 1) / 7)             AS semana,
           SUM(d.cantidad)               AS u,
           SUM(COALESCE(d.importe, 0))   AS m
    FROM obs_ventas_detalle d
    JOIN obs_productos op ON op.observer_id = d.producto_observer
    WHERE d.fecha_estadistica >= :d1 AND d.fecha_estadistica < :d2
      AND op.codigo_alfabeta IS NOT NULL
    GROUP BY op.codigo_alfabeta, ((d.dia - 1) / 7)
""")

# Lab + descripción por alfabeta, solo para los que vendieron en el mes.
_SQL_MAPS = sa.text("""
    SELECT op.codigo_alfabeta, MIN(l.descripcion), MIN(op.descripcion)
    FROM obs_productos op
    LEFT JOIN obs_laboratorios l ON l.observer_id = op.laboratorio_observer
    WHERE op.codigo_alfabeta IN :alfas
    GROUP BY op.codigo_alfabeta
""").bindparams(sa.bindparam('alfas', expanding=True))


def _engine(url, render=False):
    if url.startswith('postgresql://'):
        url = url.replace('postgresql://', 'postgresql+psycopg2://', 1)
    args = {'connect_timeout': 30}
    if render or 'render.com' in url:
        args['sslmode'] = 'require'
    return sa.create_engine(url, connect_args=args)


def _pull_db(url, d1, d2, n_sem, render=False):
    """Devuelve (ventas, lab_map, desc_map). ventas = {alfa: {sem: (u, m)}}."""
    with _engine(url, render=render).connect() as c:
        ventas = {}
        for alfa, sem, u, m in c.execute(_SQL_VENTAS, {'d1': d1, 'd2': d2}).fetchall():
            s = min(int(sem), n_sem - 1)
            uu, mm = ventas.setdefault(alfa, {}).get(s, (0.0, 0.0))
            ventas[alfa][s] = (uu + float(u or 0), mm + float(m or 0))
        alfas = list(ventas.keys())
        lab_map, desc_map = {}, {}
        if alfas:
            for a, ln, ds in c.execute(_SQL_MAPS, {'alfas': alfas}).fetchall():
                lab_map[a] = ln or 'Sin laboratorio'
                desc_map[a] = ds
    return ventas, lab_map, desc_map


def meses_disponibles(n=12):
    """Últimos `n` meses (incluido el actual) como [{'key': 202605, 'label': 'Mayo 2026'}]."""
    nombres = ['', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
               'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
    hoy = date.today()
    y, m = hoy.year, hoy.month
    out = []
    for _ in range(n):
        out.append({'key': y * 100 + m, 'label': f'{nombres[m]} {y}'})
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return out


def analizar_semanal(mes_key, local_url=None, badia_url=None):
    local_url = local_url or os.environ.get('DATABASE_URL', '')
    badia_url = badia_url or os.environ.get('BADIA_DATABASE_URL', '')
    if not local_url:
        return {'ok': False, 'error': 'DATABASE_URL no configurada.'}
    if not badia_url:
        return {'ok': False, 'error': 'BADIA_DATABASE_URL no configurada en el entorno (.env / Render).'}

    anio, mes = mes_key // 100, mes_key % 100
    if not (1 <= mes <= 12):
        return {'ok': False, 'error': f'Mes inválido: {mes_key}'}
    n_days = calendar.monthrange(anio, mes)[1]
    n_sem = min(5, (n_days + 6) // 7)
    d1 = date(anio, mes, 1)
    d2 = date(anio + (1 if mes == 12 else 0), 1 if mes == 12 else mes + 1, 1)

    try:
        p, p_lab, p_desc = _pull_db(local_url, d1, d2, n_sem)
        b, b_lab, b_desc = _pull_db(badia_url, d1, d2, n_sem, render=True)
    except Exception as e:
        return {'ok': False, 'error': f'No pude leer alguna DB: {e}'}

    def lab_of(a):
        return p_lab.get(a) or b_lab.get(a) or 'Sin laboratorio'

    def desc_of(a):
        return p_desc.get(a) or b_desc.get(a) or str(a)

    labs = {}
    tot = {'p_sem_u': [0.0] * n_sem, 'p_sem_m': [0.0] * n_sem,
           'b_sem_u': [0.0] * n_sem, 'b_sem_m': [0.0] * n_sem}

    for a in (set(p) | set(b)):
        ln = lab_of(a)
        L = labs.setdefault(ln, {
            'lab': ln, 'productos': [],
            'p_sem_u': [0.0] * n_sem, 'p_sem_m': [0.0] * n_sem,
            'b_sem_u': [0.0] * n_sem, 'b_sem_m': [0.0] * n_sem,
        })
        psem, bsem = p.get(a, {}), b.get(a, {})
        pu = pm = bu = bm = 0.0
        for s in range(n_sem):
            up, mp = psem.get(s, (0.0, 0.0))
            ub, mb = bsem.get(s, (0.0, 0.0))
            L['p_sem_u'][s] += up; L['p_sem_m'][s] += mp
            L['b_sem_u'][s] += ub; L['b_sem_m'][s] += mb
            tot['p_sem_u'][s] += up; tot['p_sem_m'][s] += mp
            tot['b_sem_u'][s] += ub; tot['b_sem_m'][s] += mb
            pu += up; pm += mp; bu += ub; bm += mb
        if not (pu or pm or bu or bm):
            continue
        L['productos'].append({
            'alfa': a, 'desc': desc_of(a),
            'p_u': round(pu, 1), 'p_m': round(pm),
            'b_u': round(bu, 1), 'b_m': round(bm),
        })

    out_labs = []
    for L in labs.values():
        if not L['productos']:
            continue
        L['productos'].sort(key=lambda x: -(abs(x['p_u']) + abs(x['b_u'])))
        L['p_u'] = round(sum(L['p_sem_u']), 1)
        L['p_m'] = round(sum(L['p_sem_m']))
        L['b_u'] = round(sum(L['b_sem_u']), 1)
        L['b_m'] = round(sum(L['b_sem_m']))
        for k in ('p_sem_u', 'b_sem_u'):
            L[k] = [round(x, 1) for x in L[k]]
        for k in ('p_sem_m', 'b_sem_m'):
            L[k] = [round(x) for x in L[k]]
        out_labs.append(L)
    out_labs.sort(key=lambda L: -(abs(L['p_u']) + abs(L['b_u'])))

    return {
        'ok': True,
        'mes_key': mes_key,
        'n_semanas': n_sem,
        'n_dias': n_days,
        'labs': out_labs,
        'total': {
            'p_sem_u': [round(x, 1) for x in tot['p_sem_u']],
            'p_sem_m': [round(x) for x in tot['p_sem_m']],
            'b_sem_u': [round(x, 1) for x in tot['b_sem_u']],
            'b_sem_m': [round(x) for x in tot['b_sem_m']],
            'p_u': round(sum(tot['p_sem_u']), 1), 'p_m': round(sum(tot['p_sem_m'])),
            'b_u': round(sum(tot['b_sem_u']), 1), 'b_m': round(sum(tot['b_sem_m'])),
        },
    }
