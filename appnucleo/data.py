"""Capa de datos de AppNúcleo — fan-out read-only a las farmacias del grupo.

Lee `product_analytics` de cada farmacia (tabla chica, pre-agregada por su propio
sync: stock + precio + ventas 12m + lab + rubro) y arma los agregados del grupo
EN MEMORIA. Nunca toca `obs_ventas_detalle` crudo → liviano y rápido.

Registro de farmacias: env var `NUCLEO_FARMACIAS` (JSON):
    [{"slug":"badia","nombre":"Badia","url":"postgresql://..."} , ...]
Si no está seteada → modo DEMO con datos sintéticos (para ver la UI sin creds).

Caché en memoria con TTL para no machacar las DBs remotas en cada request.
Cada farmacia se consulta con try/except: una caída no rompe el dashboard.
"""
import json
import os
import random
import time
from collections import defaultdict
from datetime import date

import sqlalchemy as sa

_CACHE = {}            # {'grupo': (ts, data)}
_TTL = 300             # 5 min

_PA_SQL = sa.text("""
    SELECT codigo_barra, descripcion, laboratorio, rubro, stock,
           avg_monthly, rotacion, sin_mov_60d, precio_pvp, ventas_json,
           actualizado_en
    FROM product_analytics
""")


# ── Config ──────────────────────────────────────────────────────────────────

def farmacias_config():
    """Registro de farmacias [{slug, nombre, url}].

    Precedencia: env NUCLEO_FARMACIAS (override manual) → tabla `sucursales`
    (las activas con url_externa) → [] (modo DEMO).
    """
    raw = (os.environ.get('NUCLEO_FARMACIAS') or '').strip()
    if raw:
        try:
            cfg = [f for f in json.loads(raw) if f.get('url') and f.get('slug')]
            if cfg:
                return cfg
        except (ValueError, TypeError):
            pass
    return _farmacias_desde_sucursales()


def _farmacias_desde_sucursales():
    """Lee el registro desde la tabla `sucursales` de NUCLEO_REGISTRO_URL
    (o, en su defecto, DATABASE_URL). Una sola fuente de verdad, ya poblada
    con las url_externa (de Render, funcionan desde local y desde Render).
    Devuelve [] si no hay URL de registro o la consulta falla."""
    reg_url = (os.environ.get('NUCLEO_REGISTRO_URL')
               or os.environ.get('DATABASE_URL') or '').strip()
    if not reg_url:
        return []
    try:
        eng = _engine(reg_url)
        out = []
        with eng.connect() as c:
            for r in c.execute(sa.text(
                    "SELECT slug, nombre, url_externa FROM sucursales "
                    "WHERE activa = true AND url_externa IS NOT NULL "
                    "ORDER BY slug")):
                out.append({'slug': r.slug, 'nombre': r.nombre or r.slug,
                            'url': r.url_externa})
        eng.dispose()
        return out
    except Exception:  # noqa: BLE001 — sin registro accesible → DEMO, no romper
        return []


def _engine(url):
    if url.startswith('postgresql://'):
        url = url.replace('postgresql://', 'postgresql+psycopg2://', 1)
    args = {'connect_timeout': 20}
    if 'render.com' in url:
        args['sslmode'] = 'require'
    return sa.create_engine(url, connect_args=args, pool_pre_ping=True)


# ── Carga (fan-out) ─────────────────────────────────────────────────────────

def _norm_ventas(raw):
    """ventas_json → lista de 12 ints (slot 11 = mes actual)."""
    try:
        v = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        v = []
    if not isinstance(v, list):
        v = []
    v = [int(x or 0) for x in v]
    return (v + [0] * 12)[:12]


def _pull_farmacia(cfg):
    """Lee product_analytics de UNA farmacia. Devuelve dict con rows o error."""
    out = {'slug': cfg['slug'], 'nombre': cfg.get('nombre', cfg['slug']),
           'ok': False, 'error': None, 'actualizado': None, 'rows': []}
    try:
        eng = _engine(cfg['url'])
        rows = []
        ultima = None
        with eng.connect() as c:
            for r in c.execute(_PA_SQL):
                rows.append({
                    'codigo_barra': r.codigo_barra,
                    'descripcion': r.descripcion or '',
                    'laboratorio': (r.laboratorio or 'Sin laboratorio').strip() or 'Sin laboratorio',
                    'rubro': r.rubro,
                    'stock': int(r.stock or 0),
                    'avg_monthly': float(r.avg_monthly or 0),
                    'rotacion': r.rotacion if r.rotacion in ('A', 'M', 'B') else None,
                    'sin_mov': int(r.sin_mov_60d or 0),
                    'pvp': float(r.precio_pvp or 0),
                    'ventas': _norm_ventas(r.ventas_json),
                })
                if r.actualizado_en and (ultima is None or r.actualizado_en > ultima):
                    ultima = r.actualizado_en
        eng.dispose()
        out.update(ok=True, rows=rows,
                   actualizado=ultima.isoformat() if ultima else None)
    except Exception as e:  # noqa: BLE001 — una farmacia caída no debe romper el grupo
        out['error'] = str(e)[:200]
    return out


def cargar_grupo(force=False):
    """Fan-out a todas las farmacias (cacheado). Modo DEMO si no hay config."""
    if not force:
        hit = _CACHE.get('grupo')
        if hit and (time.time() - hit[0]) < _TTL:
            return hit[1]
    cfgs = farmacias_config()
    if not cfgs:
        data = {'demo': True, 'farmacias': _demo_grupo()}
    else:
        data = {'demo': False, 'farmacias': [_pull_farmacia(c) for c in cfgs]}
    _CACHE['grupo'] = (time.time(), data)
    return data


# ── Agregaciones (operan sobre la estructura cargada) ────────────────────────

def kpis(grupo):
    """Totales del grupo + breakdown por farmacia. Ventas valorizadas = u·pvp."""
    tot = {'ventas_val': 0.0, 'unidades': 0, 'stock_val': 0.0, 'sin_mov': 0, 'skus': 0}
    por_far = []
    for f in grupo['farmacias']:
        v = u = sv = sm = 0.0
        n = 0
        for r in f['rows']:
            u12 = sum(r['ventas'])
            v += u12 * r['pvp']
            u += u12
            sv += r['stock'] * r['pvp']
            sm += r['sin_mov']
            n += 1
        por_far.append({
            'slug': f['slug'], 'nombre': f['nombre'], 'ok': f['ok'],
            'error': f.get('error'), 'actualizado': f.get('actualizado'),
            'ventas_val': round(v, 2), 'unidades': int(u),
            'stock_val': round(sv, 2), 'sin_mov': int(sm), 'skus': n,
        })
        tot['ventas_val'] += v
        tot['unidades'] += u
        tot['stock_val'] += sv
        tot['sin_mov'] += sm
        tot['skus'] += n
    tot = {k: (round(v, 2) if isinstance(v, float) else int(v)) for k, v in tot.items()}
    return tot, por_far


def tendencia(grupo):
    """Serie 12m de ventas valorizadas ($), una por farmacia. Hero chart."""
    series = []
    for f in grupo['farmacias']:
        meses = [0.0] * 12
        for r in f['rows']:
            pvp = r['pvp']
            for i, u in enumerate(r['ventas']):
                meses[i] += u * pvp
        series.append({'slug': f['slug'], 'nombre': f['nombre'],
                       'data': [round(x, 2) for x in meses]})
    return series


def top_laboratorios(grupo, n=10):
    agg = defaultdict(float)
    for f in grupo['farmacias']:
        for r in f['rows']:
            agg[r['laboratorio']] += sum(r['ventas']) * r['pvp']
    items = sorted(agg.items(), key=lambda kv: -kv[1])[:n]
    return [{'lab': k, 'ventas_val': round(v, 2)} for k, v in items]


def top_laboratorios_por_farmacia(grupo, n=10):
    """Top-N labs por $ del grupo, con el $ desglosado por farmacia.
    Para barra horizontal apilada. {'labs':[...ordenados...], 'slugs':[...],
    'nombres':{}, 'data':{slug:[$ por lab alineado a labs]}}."""
    slugs = [f['slug'] for f in grupo['farmacias']]
    nombres = {f['slug']: f['nombre'] for f in grupo['farmacias']}
    by_lab_far = defaultdict(lambda: defaultdict(float))
    tot = defaultdict(float)
    for f in grupo['farmacias']:
        for r in f['rows']:
            val = sum(r['ventas']) * r['pvp']
            if val:
                by_lab_far[r['laboratorio']][f['slug']] += val
                tot[r['laboratorio']] += val
    labs = sorted(tot, key=lambda l: -tot[l])[:n]
    data = {s: [round(by_lab_far[l].get(s, 0.0), 2) for l in labs] for s in slugs}
    return {'labs': labs, 'slugs': slugs, 'nombres': nombres, 'data': data}


def rotacion_dist(grupo):
    """Conteo de SKUs por rotación A/M/B (sin = sin clasificar)."""
    c = {'A': 0, 'M': 0, 'B': 0, 'sin': 0}
    for f in grupo['farmacias']:
        for r in f['rows']:
            c[r['rotacion'] or 'sin'] += 1
    return c


def heatmap_cobertura(grupo, n=12):
    """Matriz top-N labs × farmacias con $ 12m por celda. Mapa de calor.
    {'labs':[...], 'slugs':[...], 'nombres':{}, 'celdas':{lab:{slug:$}},
     'max':$, 'tot_lab':{lab:$}, 'presentes':{lab:int}}."""
    slugs = [f['slug'] for f in grupo['farmacias']]
    nombres = {f['slug']: f['nombre'] for f in grupo['farmacias']}
    by_lab_far = defaultdict(lambda: defaultdict(float))
    tot_lab = defaultdict(float)
    for f in grupo['farmacias']:
        for r in f['rows']:
            val = sum(r['ventas']) * r['pvp']
            if val:
                by_lab_far[r['laboratorio']][f['slug']] += val
                tot_lab[r['laboratorio']] += val
    labs = sorted(tot_lab, key=lambda l: -tot_lab[l])[:n]
    celdas = {l: {s: round(by_lab_far[l].get(s, 0.0), 2) for s in slugs} for l in labs}
    mx = max((by_lab_far[l].get(s, 0.0) for l in labs for s in slugs), default=0.0)
    presentes = {l: sum(1 for s in slugs if by_lab_far[l].get(s, 0.0) > 0) for l in labs}
    return {'labs': labs, 'slugs': slugs, 'nombres': nombres, 'celdas': celdas,
            'max': round(mx, 2), 'tot_lab': {l: round(tot_lab[l], 2) for l in labs},
            'presentes': presentes}


def detalle_por_farmacia(grupo, n_labs=6):
    """Por farmacia: serie 12m ($), top labs propios, rotación. Para drill-down."""
    out = {}
    for f in grupo['farmacias']:
        meses = [0.0] * 12
        labs = defaultdict(float)
        rot = {'A': 0, 'M': 0, 'B': 0, 'sin': 0}
        for r in f['rows']:
            pvp = r['pvp']
            for i, u in enumerate(r['ventas']):
                meses[i] += u * pvp
            labs[r['laboratorio']] += sum(r['ventas']) * pvp
            rot[r['rotacion'] or 'sin'] += 1
        top = sorted(labs.items(), key=lambda kv: -kv[1])[:n_labs]
        out[f['slug']] = {
            'nombre': f['nombre'],
            'serie': [round(x, 2) for x in meses],
            'top_labs': [{'lab': k, 'ventas_val': round(v, 2)} for k, v in top],
            'rotacion': rot,
        }
    return out


def meses_labels(hoy=None):
    """12 etiquetas 'MMM' terminando en el mes actual (slot 11)."""
    hoy = hoy or date.today()
    nombres = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun',
               'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
    out = []
    y, m = hoy.year, hoy.month
    for back in range(11, -1, -1):
        mm = m - back
        yy = y
        while mm <= 0:
            mm += 12
            yy -= 1
        out.append(f"{nombres[mm - 1]} {str(yy)[2:]}")
    return out


# ── Ventas-multi (pivot por farmacia) ────────────────────────────────────────

_GROUP_KEYS = {
    'laboratorio': lambda r: r['laboratorio'],
    'producto': lambda r: r['descripcion'] or r['codigo_barra'],
    'rubro': lambda r: r['rubro'] or 'Sin rubro',
}


def ventas_multi(grupo, group_by='laboratorio', q='', rubro='', limit=300):
    """Pivot: filas = dimensión group_by, columnas = farmacias + total.
    Cada celda: (unidades 12m, $ 12m). Filtros: q (texto), rubro (==)."""
    if group_by not in _GROUP_KEYS:
        group_by = 'laboratorio'
    keyf = _GROUP_KEYS[group_by]
    slugs = [f['slug'] for f in grupo['farmacias']]
    nombres = {f['slug']: f['nombre'] for f in grupo['farmacias']}
    q = (q or '').strip().lower()
    rubro = (rubro or '').strip().lower()

    # key -> {'label':..., 'far':{slug:[u,$]}, 'tot':[u,$]}
    filas = {}
    for f in grupo['farmacias']:
        for r in f['rows']:
            if q and q not in (r['descripcion'] or '').lower() and q not in r['laboratorio'].lower():
                continue
            if rubro and rubro not in (r['rubro'] or '').lower():
                continue
            u = sum(r['ventas'])
            val = u * r['pvp']
            if u == 0 and val == 0:
                continue
            k = keyf(r)
            fila = filas.get(k)
            if fila is None:
                fila = {'label': k, 'far': {s: [0, 0.0] for s in slugs}, 'tot': [0, 0.0]}
                filas[k] = fila
            fila['far'][f['slug']][0] += u
            fila['far'][f['slug']][1] += val
            fila['tot'][0] += u
            fila['tot'][1] += val

    ordenadas = sorted(filas.values(), key=lambda x: -x['tot'][1])[:limit]
    for fila in ordenadas:
        fila['tot'][1] = round(fila['tot'][1], 2)
        for s in slugs:
            fila['far'][s][1] = round(fila['far'][s][1], 2)
    return {'slugs': slugs, 'nombres': nombres, 'group_by': group_by,
            'filas': ordenadas, 'total_filas': len(filas)}


# ── DEMO (sin creds) ─────────────────────────────────────────────────────────

_DEMO_FARMS = [('badia', 'Badia'), ('pieri', 'Pieri'),
               ('grassi', 'Grassi'), ('cappone', 'Cappone')]
_DEMO_LABS = ['Bayer', 'Roemmers', 'Bagó', 'Elea', 'Gador', 'Raffo',
              'Casasco', 'Montpellier', 'Phoenix', 'Sanofi', 'Pfizer']
_DEMO_RUBROS = ['Medicamentos', 'Perfumería', 'Accesorios']


def _demo_grupo():
    rnd = random.Random(7)
    escala = {'badia': 1.0, 'pieri': 0.6, 'grassi': 0.38, 'cappone': 0.24}
    out = []
    for slug, nombre in _DEMO_FARMS:
        rows = []
        for i in range(160):
            base = rnd.randint(2, 80)
            trend = rnd.uniform(0.9, 1.15)
            ventas = []
            v = base * escala[slug]
            for _ in range(12):
                v = max(0, v * trend * rnd.uniform(0.8, 1.2))
                ventas.append(int(v))
            rows.append({
                'codigo_barra': f'77{slug[:1]}{i:05d}',
                'descripcion': f'PRODUCTO DEMO {i:03d}',
                'laboratorio': rnd.choice(_DEMO_LABS),
                'rubro': rnd.choice(_DEMO_RUBROS) if i % 7 else 'Medicamentos',
                'stock': rnd.randint(0, 120),
                'avg_monthly': round(sum(ventas) / 12, 2),
                'rotacion': rnd.choice(['A', 'M', 'B', None]),
                'sin_mov': 1 if rnd.random() < 0.12 else 0,
                'pvp': round(rnd.uniform(800, 28000), 2),
                'ventas': ventas,
            })
        out.append({'slug': slug, 'nombre': nombre, 'ok': True, 'error': None,
                    'actualizado': date.today().isoformat(), 'rows': rows})
    return out
