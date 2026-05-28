"""Diff de schema entre dos Postgres (lectura pura, no toca data).

Compara estructura: tablas, columnas (tipo, nullable, default), índices, FKs,
constraints únicas. Pensado para detectar drift entre instancias del proyecto
antes de adoptar Alembic.

Uso:
    python scripts/schema_diff.py <URL_A> <URL_B>
    python scripts/schema_diff.py <URL_A> <URL_B> --name-a Badia --name-b Render
    python scripts/schema_diff.py <URL_A> <URL_B> --out report.md

Salida: markdown estructurado con resumen + secciones por categoría.
"""
import argparse
import sys
from collections import defaultdict

import psycopg2


def conectar(url):
    """Abre conexión Postgres. Acepta URL postgres:// o postgresql://."""
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return psycopg2.connect(url, connect_timeout=20)


# ─────────── Lectura del schema ───────────

def listar_tablas(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public' AND table_type='BASE TABLE'
            ORDER BY table_name
        """)
        return {r[0] for r in cur.fetchall()}


def columnas_por_tabla(conn, tabla):
    """Dict col_name → {type, nullable, default, char_max, num_prec, num_scale}."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, data_type, is_nullable, column_default,
                   character_maximum_length, numeric_precision, numeric_scale
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s
            ORDER BY ordinal_position
        """, (tabla,))
        out = {}
        for r in cur.fetchall():
            out[r[0]] = {
                'type': r[1], 'nullable': r[2], 'default': r[3],
                'char_max': r[4], 'num_prec': r[5], 'num_scale': r[6],
            }
        return out


def indices_por_tabla(conn, tabla):
    """Dict index_name → indexdef."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT indexname, indexdef FROM pg_indexes
            WHERE schemaname='public' AND tablename=%s
            ORDER BY indexname
        """, (tabla,))
        return {r[0]: r[1] for r in cur.fetchall()}


def fks_por_tabla(conn, tabla):
    """Dict fk_name → definicion."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT conname, pg_get_constraintdef(c.oid)
            FROM pg_constraint c
            JOIN pg_class t ON c.conrelid=t.oid
            JOIN pg_namespace n ON t.relnamespace=n.oid
            WHERE n.nspname='public' AND t.relname=%s AND c.contype='f'
            ORDER BY conname
        """, (tabla,))
        return {r[0]: r[1] for r in cur.fetchall()}


def constraints_por_tabla(conn, tabla):
    """Dict constraint_name → (tipo, definicion) — UNIQUE y CHECK (PK y FK aparte)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT conname, contype, pg_get_constraintdef(c.oid)
            FROM pg_constraint c
            JOIN pg_class t ON c.conrelid=t.oid
            JOIN pg_namespace n ON t.relnamespace=n.oid
            WHERE n.nspname='public' AND t.relname=%s AND c.contype IN ('u','c','p')
            ORDER BY conname
        """, (tabla,))
        return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


# ─────────── Comparación ───────────

def fmt_tipo(c):
    """Representación legible del tipo de una columna."""
    if c['char_max']:
        return f"{c['type']}({c['char_max']})"
    if c['num_prec'] is not None:
        if c['num_scale']:
            return f"{c['type']}({c['num_prec']},{c['num_scale']})"
        return f"{c['type']}({c['num_prec']})"
    return c['type']


def diff_columnas(cols_a, cols_b, name_a, name_b):
    """Devuelve lista de tuplas (severidad, tipo_diff, col, detalle)."""
    diffs = []
    for col in sorted(set(cols_a) | set(cols_b)):
        if col not in cols_a:
            diffs.append(('🔴', '+COL', col,
                          f'solo en {name_b} ({fmt_tipo(cols_b[col])})'))
            continue
        if col not in cols_b:
            diffs.append(('🔴', '-COL', col,
                          f'solo en {name_a} ({fmt_tipo(cols_a[col])})'))
            continue
        a, b = cols_a[col], cols_b[col]
        if (a['type'] != b['type'] or a['char_max'] != b['char_max']
                or a['num_prec'] != b['num_prec']
                or a['num_scale'] != b['num_scale']):
            diffs.append(('🔴', '≠TYPE', col,
                          f'{name_a}={fmt_tipo(a)} · {name_b}={fmt_tipo(b)}'))
        if a['nullable'] != b['nullable']:
            diffs.append(('🟡', '≠NULL', col,
                          f'{name_a}={a["nullable"]} · {name_b}={b["nullable"]}'))
        if (a['default'] or '') != (b['default'] or ''):
            diffs.append(('🟡', '≠DEFAULT', col,
                          f'{name_a}={a["default"]!r} · {name_b}={b["default"]!r}'))
    return diffs


def diff_set(set_a, set_b, name_a, name_b, label):
    """Devuelve listas (solo_a, solo_b) para un set de nombres."""
    return sorted(set_a - set_b), sorted(set_b - set_a)


def diff_dict_def(da, db, name_a, name_b, severidad='🟡'):
    """Para dicts name→def (índices, FKs, constraints) devuelve diffs."""
    diffs = []
    for k in sorted(set(da) | set(db)):
        if k not in da:
            diffs.append((severidad, '+', k, f'solo en {name_b}: {db[k]}'))
        elif k not in db:
            diffs.append((severidad, '-', k, f'solo en {name_a}: {da[k]}'))
        elif da[k] != db[k]:
            diffs.append((severidad, '≠', k,
                          f'{name_a}: {da[k]} · {name_b}: {db[k]}'))
    return diffs


# ─────────── Reporte ───────────

def reporte(conn_a, conn_b, name_a, name_b):
    """Devuelve el reporte completo como string markdown."""
    lines = [f'# Schema diff — {name_a} vs {name_b}', '']

    # === Tablas ===
    tabs_a = listar_tablas(conn_a)
    tabs_b = listar_tablas(conn_b)
    solo_a, solo_b = diff_set(tabs_a, tabs_b, name_a, name_b, 'tabla')
    comunes = sorted(tabs_a & tabs_b)

    lines += [
        '## 📊 Resumen',
        '',
        f'- **{name_a}**: {len(tabs_a)} tablas',
        f'- **{name_b}**: {len(tabs_b)} tablas',
        f'- En común: {len(comunes)}',
        f'- Solo en **{name_a}**: {len(solo_a)}',
        f'- Solo en **{name_b}**: {len(solo_b)}',
        '',
    ]

    # === Tablas ===
    lines += ['## 🗂 Tablas', '']
    if solo_a:
        lines.append(f'### Solo en {name_a} ({len(solo_a)})')
        for t in solo_a:
            lines.append(f'- `{t}`')
        lines.append('')
    if solo_b:
        lines.append(f'### Solo en {name_b} ({len(solo_b)})')
        for t in solo_b:
            lines.append(f'- `{t}`')
        lines.append('')
    if not solo_a and not solo_b:
        lines += ['✅ Las dos DBs tienen las mismas tablas.', '']

    # === Columnas (por tabla común) ===
    lines += ['## 🧱 Columnas (en tablas comunes)', '']
    tablas_con_diff_col = []
    diffs_por_tabla = {}
    for t in comunes:
        ca = columnas_por_tabla(conn_a, t)
        cb = columnas_por_tabla(conn_b, t)
        diffs = diff_columnas(ca, cb, name_a, name_b)
        if diffs:
            tablas_con_diff_col.append(t)
            diffs_por_tabla[t] = diffs

    if tablas_con_diff_col:
        lines.append(f'**{len(tablas_con_diff_col)} tablas con diferencias de columnas:**')
        lines.append('')
        for t in tablas_con_diff_col:
            lines.append(f'### `{t}`')
            for sev, kind, col, det in diffs_por_tabla[t]:
                lines.append(f'- {sev} `{kind}` **{col}** — {det}')
            lines.append('')
    else:
        lines += ['✅ Sin diferencias de columnas en tablas comunes.', '']

    # === Índices ===
    lines += ['## 🔍 Índices', '']
    tablas_con_diff_idx = []
    diffs_idx_por_tabla = {}
    for t in comunes:
        ia = indices_por_tabla(conn_a, t)
        ib = indices_por_tabla(conn_b, t)
        ddx = diff_dict_def(ia, ib, name_a, name_b, severidad='🟢')
        if ddx:
            tablas_con_diff_idx.append(t)
            diffs_idx_por_tabla[t] = ddx
    if tablas_con_diff_idx:
        lines.append(f'**{len(tablas_con_diff_idx)} tablas con diferencias de índices:**')
        lines.append('')
        for t in tablas_con_diff_idx:
            lines.append(f'### `{t}`')
            for sev, kind, idx, det in diffs_idx_por_tabla[t]:
                lines.append(f'- {sev} `{kind}` **{idx}** — {det}')
            lines.append('')
    else:
        lines += ['✅ Sin diferencias de índices.', '']

    # === Foreign Keys ===
    lines += ['## 🔗 Foreign Keys', '']
    diffs_fk_por_tabla = {}
    for t in comunes:
        fa = fks_por_tabla(conn_a, t)
        fb = fks_por_tabla(conn_b, t)
        dfk = diff_dict_def(fa, fb, name_a, name_b, severidad='🟡')
        if dfk:
            diffs_fk_por_tabla[t] = dfk
    if diffs_fk_por_tabla:
        lines.append(f'**{len(diffs_fk_por_tabla)} tablas con FKs distintas:**')
        lines.append('')
        for t in sorted(diffs_fk_por_tabla):
            lines.append(f'### `{t}`')
            for sev, kind, fk, det in diffs_fk_por_tabla[t]:
                lines.append(f'- {sev} `{kind}` **{fk}** — {det}')
            lines.append('')
    else:
        lines += ['✅ Sin diferencias de FKs.', '']

    # === Constraints (UNIQUE, CHECK, PRIMARY) ===
    lines += ['## 🛡 Constraints (UNIQUE/CHECK/PK)', '']
    diffs_ct_por_tabla = {}
    for t in comunes:
        ca = {k: v[1] for k, v in constraints_por_tabla(conn_a, t).items()}
        cb = {k: v[1] for k, v in constraints_por_tabla(conn_b, t).items()}
        dct = diff_dict_def(ca, cb, name_a, name_b, severidad='🟡')
        if dct:
            diffs_ct_por_tabla[t] = dct
    if diffs_ct_por_tabla:
        lines.append(f'**{len(diffs_ct_por_tabla)} tablas con constraints distintas:**')
        lines.append('')
        for t in sorted(diffs_ct_por_tabla):
            lines.append(f'### `{t}`')
            for sev, kind, ct, det in diffs_ct_por_tabla[t]:
                lines.append(f'- {sev} `{kind}` **{ct}** — {det}')
            lines.append('')
    else:
        lines += ['✅ Sin diferencias de constraints.', '']

    # Conteos finales
    total_col = sum(len(v) for v in diffs_por_tabla.values())
    total_idx = sum(len(v) for v in diffs_idx_por_tabla.values())
    total_fk = sum(len(v) for v in diffs_fk_por_tabla.values())
    total_ct = sum(len(v) for v in diffs_ct_por_tabla.values())

    lines += [
        '---', '',
        '## 🎯 Conteos totales de diferencias', '',
        f'- Tablas: **{len(solo_a) + len(solo_b)}**',
        f'- Columnas: **{total_col}**',
        f'- Índices: **{total_idx}**',
        f'- FKs: **{total_fk}**',
        f'- Constraints: **{total_ct}**',
        f'- **TOTAL: {len(solo_a) + len(solo_b) + total_col + total_idx + total_fk + total_ct}**',
        '',
    ]

    return '\n'.join(lines)


# ─────────── CLI ───────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('url_a', help='URL Postgres A')
    ap.add_argument('url_b', help='URL Postgres B')
    ap.add_argument('--name-a', default='A', help='Nombre para A (default: A)')
    ap.add_argument('--name-b', default='B', help='Nombre para B (default: B)')
    ap.add_argument('--out', help='Archivo de salida (default: stdout)')
    args = ap.parse_args()

    print(f'Conectando a {args.name_a}…', file=sys.stderr)
    conn_a = conectar(args.url_a)
    print(f'Conectando a {args.name_b}…', file=sys.stderr)
    conn_b = conectar(args.url_b)
    print('Generando reporte…', file=sys.stderr)
    txt = reporte(conn_a, conn_b, args.name_a, args.name_b)
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(txt)
        print(f'✓ reporte → {args.out}', file=sys.stderr)
    else:
        print(txt)
    conn_a.close()
    conn_b.close()


if __name__ == '__main__':
    main()
