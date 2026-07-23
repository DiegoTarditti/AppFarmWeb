"""Pantalla de configuración de contenido del bot (plantilla editable)."""
import json as _json
import os as _os

from flask import Response, jsonify, render_template, request
from flask_login import current_user, login_required

from bot.flujo import SECCIONES, get_flujo


def init_app(app):

    def _solo_admin():
        if current_user.rol not in ('admin', 'dev'):
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        return None

    @app.route('/bot-config')
    @login_required
    def bot_config():
        err = _solo_admin()
        if err: return err
        return render_template('bot_plantilla.html')

    @app.route('/bot-config/exportar')
    @login_required
    def bot_config_exportar():
        err = _solo_admin()
        if err: return err
        flujo = get_flujo()
        partes = []
        for sec_nombre, nodo_id in SECCIONES.items():
            nodo = flujo.get(nodo_id, {})
            mensaje = nodo.get('mensaje', '')
            partes.append(f'# {sec_nombre}\n{mensaje}\n')
        contenido = '\n'.join(partes)
        return Response(contenido, mimetype='text/plain',
                        headers={'Content-Disposition':
                                 'attachment; filename=farmacia_bot_plantilla.txt'})

    @app.route('/bot-config/preview', methods=['POST'])
    @login_required
    def bot_config_preview():
        err = _solo_admin()
        if err: return err
        archivo = request.files.get('archivo')
        if not archivo:
            return jsonify({'ok': False, 'error': 'falta archivo'}), 400
        texto = archivo.read().decode('utf-8', errors='replace')
        lineas = texto.split('\n')
        secciones_subidas = {}
        actual = None
        acumulado = []
        for linea in lineas:
            rstripped = linea.rstrip()
            if rstripped.startswith('# ') and rstripped.count('#') == 1:
                if actual is not None:
                    secciones_subidas[actual] = '\n'.join(acumulado).strip()
                actual = rstripped[2:].strip()
                acumulado = []
            elif actual is not None:
                acumulado.append(linea)
        if actual is not None:
            secciones_subidas[actual] = '\n'.join(acumulado).strip()

        flujo = get_flujo()
        cambios = []
        sin_cambios = []
        desconocidas = []

        for sec_nombre, nodo_id in SECCIONES.items():
            subido = secciones_subidas.pop(sec_nombre, None)
            if subido is None:
                sin_cambios.append(sec_nombre)
                continue
            viejo = flujo.get(nodo_id, {}).get('mensaje', '')
            if subido.strip() != viejo.strip():
                cambios.append({
                    'seccion': sec_nombre, 'nodo': nodo_id,
                    'viejo': viejo, 'nuevo': subido.strip(),
                })
            else:
                sin_cambios.append(sec_nombre)
        for k in secciones_subidas:
            desconocidas.append(k)

        return jsonify({
            'cambios': cambios, 'sin_cambios': sin_cambios,
            'desconocidas': desconocidas,
        })

    @app.route('/bot-config/aplicar', methods=['POST'])
    @login_required
    def bot_config_aplicar():
        err = _solo_admin()
        if err: return err
        overrides = (request.json or {}).get('overrides') or {}
        validos = set(SECCIONES.values())
        clean = {k: v for k, v in overrides.items() if k in validos}
        if not clean:
            return jsonify({'ok': False, 'error': 'sin cambios válidos'}), 400
        path = _os.path.join(_os.path.dirname(__file__), '..', 'bot', 'flujo_data.json')
        existente = {}
        try:
            if _os.path.exists(path):
                existente = _json.loads(open(path, encoding='utf-8').read())
        except Exception:
            existente = {}
        existente.update(clean)
        with open(path, 'w', encoding='utf-8') as f:
            _json.dump(existente, f, ensure_ascii=False, indent=2)
        return jsonify({'ok': True})