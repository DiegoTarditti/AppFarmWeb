# -*- coding: utf-8 -*-
"""Genera docs/MAPA.generado.md — el indice del repo, DERIVADO del codigo.

Por que existe: el repo es grande (75 archivos de rutas, ~5800 lineas de
modelos) y encontrar "donde esta X" cuesta varios greps. Peor: un indice
escrito a mano se pudre y termina MINTIENDO (paso con el CLAUDE.md, que
afirmaba que el catalogo era uniforme entre farmacias, y con el backlog, que
listaba como pendientes cosas ya hechas).

La regla: si un dato se puede derivar del codigo, NO se escribe a mano.
Este mapa se regenera; el CLAUDE.md queda para lo que el codigo no dice
(decisiones, trampas, por que).

Uso:
    python scripts/mapa.py          # escribe docs/MAPA.generado.md
    python scripts/mapa.py --check  # falla si esta desactualizado (para CI)
"""
import ast
import io
import os
import re
import subprocess
import sys
from datetime import datetime

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SALIDA = os.path.join(RAIZ, 'docs', 'MAPA.generado.md')


def _rel(path):
    return os.path.relpath(path, RAIZ).replace('\\', '/')


def _parse(path):
    try:
        with io.open(path, encoding='utf-8') as f:
            return ast.parse(f.read(), filename=path), f
    except (SyntaxError, UnicodeDecodeError):
        return None, None


def _fuente(path):
    with io.open(path, encoding='utf-8', errors='replace') as f:
        return f.read()


# ── Rutas ─────────────────────────────────────────────────────────────────

def rutas():
    """Extrae @app.route de routes/*.py y app.py. Devuelve {archivo: [(ruta, metodos, fn, linea)]}"""
    out = {}
    archivos = [os.path.join(RAIZ, 'app.py')]
    rdir = os.path.join(RAIZ, 'routes')
    archivos += [os.path.join(rdir, f) for f in sorted(os.listdir(rdir)) if f.endswith('.py')]
    for path in archivos:
        tree, _ = _parse(path)
        if tree is None:
            continue
        encontradas = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                fn = dec.func
                nombre = getattr(fn, 'attr', None) or getattr(fn, 'id', None)
                if nombre != 'route':
                    continue
                ruta = None
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    ruta = dec.args[0].value
                metodos = ['GET']
                for kw in dec.keywords:
                    if kw.arg == 'methods' and isinstance(kw.value, (ast.List, ast.Tuple)):
                        metodos = [e.value for e in kw.value.elts if isinstance(e, ast.Constant)]
                if ruta:
                    encontradas.append((ruta, '/'.join(metodos), node.name, node.lineno))
        if encontradas:
            out[_rel(path)] = sorted(encontradas)
    return out


# ── Modelos ───────────────────────────────────────────────────────────────

def modelos():
    """Clases con __tablename__ en database.py → [(tabla, clase, linea, doc)]"""
    path = os.path.join(RAIZ, 'database.py')
    tree, _ = _parse(path)
    out = []
    if tree is None:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        tabla = None
        for st in node.body:
            if (isinstance(st, ast.Assign) and st.targets
                    and getattr(st.targets[0], 'id', None) == '__tablename__'
                    and isinstance(st.value, ast.Constant)):
                tabla = st.value.value
        if tabla:
            doc = (ast.get_docstring(node) or '').strip().split('\n')[0][:90]
            out.append((tabla, node.name, node.lineno, doc))
    return sorted(out)


# ── Syncs de ObServer ─────────────────────────────────────────────────────

VISTA_RE = re.compile(r'FROM\s+((?:DW|Gestion)\.\w+)', re.IGNORECASE)


def syncs():
    """Funciones sync_* de observer_source.py + de que vista de ObServer leen."""
    path = os.path.join(RAIZ, 'observer_source.py')
    src = _fuente(path)
    tree = ast.parse(src, filename=path)
    lineas = src.split('\n')
    out = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith('sync_'):
            continue
        cuerpo = '\n'.join(lineas[node.lineno - 1:node.end_lineno])
        vistas = sorted(set(VISTA_RE.findall(cuerpo)))
        premium = any(v.lower().startswith('gestion.') for v in vistas)
        doc = (ast.get_docstring(node) or '').strip().split('\n')[0][:80]
        out.append((node.name, vistas, premium, node.lineno, doc))
    return out


# ── Services ──────────────────────────────────────────────────────────────

def services():
    d = os.path.join(RAIZ, 'services')
    out = []
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d)):
        if not f.endswith('.py') or f == '__init__.py':
            continue
        tree, _ = _parse(os.path.join(d, f))
        doc = (ast.get_docstring(tree) or '').strip().split('\n')[0][:95] if tree else ''
        out.append((f, doc))
    return out


def parsers():
    d = os.path.join(RAIZ, 'parsers')
    out = []
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d)):
        if not f.endswith('.py') or f.startswith('_'):
            continue
        tree, _ = _parse(os.path.join(d, f))
        doc = (ast.get_docstring(tree) or '').strip().split('\n')[0][:95] if tree else ''
        out.append((f, doc))
    return out


# ── Render ────────────────────────────────────────────────────────────────

def generar():
    try:
        commit = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'],
                                         cwd=RAIZ, text=True).strip()
        rama = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                                       cwd=RAIZ, text=True).strip()
    except Exception:  # noqa: BLE001
        commit = rama = '?'

    R, M, S, SV, P = rutas(), modelos(), syncs(), services(), parsers()
    n_rutas = sum(len(v) for v in R.values())

    L = []
    L.append('# Mapa de AppFarmWeb')
    L.append('')
    L.append('> ⚠️ **GENERADO — no editar a mano.** Se regenera con `python scripts/mapa.py`.')
    L.append('> Todo acá sale del código, así que no puede quedar desactualizado sin que')
    L.append('> se note. Lo que el código NO dice (decisiones, trampas, por qué) va en')
    L.append('> [CLAUDE.md](../CLAUDE.md), no acá.')
    L.append('')
    L.append(f'Generado: {datetime.now():%Y-%m-%d %H:%M} · rama `{rama}` · commit `{commit}`')
    L.append('')
    L.append(f'**{n_rutas} rutas** en {len(R)} archivos · **{len(M)} modelos** · '
             f'**{len(S)} syncs** · **{len(SV)} services** · **{len(P)} parsers**')
    L.append('')

    # Syncs primero: es lo que más cuesta encontrar y lo más sensible.
    L.append('## Syncs de ObServer (`observer_source.py`)')
    L.append('')
    L.append('⭐ = premium (lee el schema `Gestion` → **requiere usuario SA**).')
    L.append('')
    L.append('| Función | Lee de | Línea |')
    L.append('|---|---|---|')
    for nombre, vistas, premium, ln, _doc in S:
        v = '`' + '`, `'.join(vistas) + '`' if vistas else '—'
        star = ' ⭐' if premium else ''
        L.append(f'| `{nombre}`{star} | {v} | [{ln}](../observer_source.py#L{ln}) |')
    L.append('')

    L.append('## Modelos (`database.py`)')
    L.append('')
    L.append('| Tabla | Clase | Línea |')
    L.append('|---|---|---|')
    for tabla, clase, ln, _doc in M:
        L.append(f'| `{tabla}` | `{clase}` | [{ln}](../database.py#L{ln}) |')
    L.append('')

    L.append('## Rutas')
    L.append('')
    for archivo in sorted(R):
        L.append(f'### `{archivo}`')
        L.append('')
        L.append('| Ruta | Métodos | Función |')
        L.append('|---|---|---|')
        for ruta, metodos, fn, ln in R[archivo]:
            L.append(f'| `{ruta}` | {metodos} | [`{fn}`](../{archivo}#L{ln}) |')
        L.append('')

    if SV:
        L.append('## Services')
        L.append('')
        L.append('| Módulo | Qué hace |')
        L.append('|---|---|')
        for f, doc in SV:
            L.append(f'| [`{f}`](../services/{f}) | {doc} |')
        L.append('')

    if P:
        L.append('## Parsers')
        L.append('')
        L.append('| Módulo | Qué hace |')
        L.append('|---|---|')
        for f, doc in P:
            L.append(f'| [`{f}`](../parsers/{f}) | {doc} |')
        L.append('')

    return '\n'.join(L) + '\n'


def _sin_fecha(txt):
    """El texto sin la línea de fecha/commit, para comparar en --check."""
    return '\n'.join(l for l in txt.split('\n') if not l.startswith('Generado:'))


def main():
    nuevo = generar()
    if '--check' in sys.argv:
        if not os.path.exists(SALIDA):
            print('FALTA docs/MAPA.generado.md — correr: python scripts/mapa.py')
            return 1
        viejo = io.open(SALIDA, encoding='utf-8').read()
        if _sin_fecha(viejo) != _sin_fecha(nuevo):
            print('MAPA DESACTUALIZADO — correr: python scripts/mapa.py')
            return 1
        print('mapa OK')
        return 0
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    io.open(SALIDA, 'w', encoding='utf-8', newline='').write(nuevo)
    print(f'{_rel(SALIDA)} generado')
    return 0


if __name__ == '__main__':
    sys.exit(main())
