"""Sirve los .md de docs/manual/ como JSON para el drawer de ayuda contextual."""

import os

from flask import abort, jsonify
from flask_login import login_required

DOCS_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'docs', 'manual'
)


def _listar_md(root):
    """Devuelve lista de paths relativos a `root` de todos los .md ordenados."""
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if not f.endswith('.md'):
                continue
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, root).replace('\\', '/')
            out.append(rel)
    return sorted(out)


def init_app(app):

    @app.route('/api/help/_index')
    @login_required
    def api_help_index():
        """Devuelve el árbol del manual agrupado por carpeta para el drawer."""
        items = _listar_md(DOCS_ROOT)
        # Agrupar por primer segmento (raiz, flujos, pantallas, admin)
        grupos = {}
        for rel in items:
            parts = rel.split('/')
            grupo = parts[0] if len(parts) > 1 else 'raiz'
            grupos.setdefault(grupo, []).append({
                'section': rel.replace('.md', ''),
                'titulo': parts[-1].replace('.md', '').replace('_', ' '),
            })
        return jsonify({'grupos': grupos})

    @app.route('/api/help/')
    @app.route('/api/help/<path:section>')
    @login_required
    def api_help(section='README.md'):
        """Devuelve el contenido raw de un .md del manual.

        Acepta secciones con o sin la extensión .md:
            /api/help/                              → README.md
            /api/help/pantallas/indicadores_pedido  → pantallas/indicadores_pedido.md
            /api/help/glosario                      → glosario.md
        """
        if not section.endswith('.md'):
            section = section + '.md'

        # Anti path traversal: el path resuelto debe seguir bajo DOCS_ROOT.
        full_path = os.path.normpath(os.path.join(DOCS_ROOT, section))
        if not full_path.startswith(DOCS_ROOT):
            abort(404)
        if not os.path.isfile(full_path):
            return jsonify({
                'error': 'sección no encontrada',
                'section': section,
                'md': f'# Sección no disponible\n\nEl archivo `{section}` todavía no existe.\n\nVer el [índice](README.md) o el [TODO](TODO.md) para ver qué falta.',
            }), 404

        with open(full_path, 'r', encoding='utf-8') as f:
            md = f.read()
        return jsonify({'md': md, 'section': section})
