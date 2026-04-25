"""Rutas para tracking de cards del home + pantalla de personalización."""
import json

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

import database
import home_cards as hc


def init_app(app):

    @app.route('/go/<card_id>')
    def home_card_go(card_id):
        """Redirige a la ruta de la card y registra el click."""
        card = hc.ACCIONES_HOME_BY_ID.get(card_id)
        if not card:
            abort(404)
        if current_user.is_authenticated:
            with database.get_db() as session:
                session.add(database.HomeCardClick(
                    usuario_id=current_user.id, card_id=card_id))
                session.commit()
        try:
            target = url_for(card['endpoint'])
        except Exception:
            target = url_for('index')
        return redirect(target)

    @app.route('/configuracion/personalizar-home', methods=['GET', 'POST'])
    def personalizar_home():
        if not current_user.is_authenticated:
            return redirect(url_for('auth_login'))

        with database.get_db() as session:
            u = session.get(database.Usuario, current_user.id)
            if u is None:
                flash('Usuario no encontrado.', 'error')
                return redirect(url_for('index'))

            if request.method == 'POST':
                accion = request.form.get('accion', 'guardar')
                if accion == 'reset':
                    u.preferencias_home_json = None
                    session.commit()
                    flash('Preferencias restablecidas al default.', 'success')
                    return redirect(url_for('personalizar_home'))
                # Guardar
                modo = request.form.get('modo', 'auto')
                orden = request.form.get('orden', '').split(',')
                orden = [x.strip() for x in orden if x.strip() in hc.ACCIONES_HOME_BY_ID]
                ocultos = request.form.getlist('ocultos')
                ocultos = [x for x in ocultos if x in hc.ACCIONES_HOME_BY_ID]
                colores = {}
                for c in hc.ACCIONES_HOME:
                    color = request.form.get(f'color_{c["id"]}', '').strip()
                    if color and color.lower() != c['bg_default'].lower():
                        colores[c['id']] = color
                prefs = {'modo': modo, 'orden': orden, 'colores': colores, 'ocultos': ocultos}
                u.preferencias_home_json = json.dumps(prefs, ensure_ascii=False)
                session.commit()
                flash('Preferencias guardadas.', 'success')
                return redirect(url_for('personalizar_home'))

            # GET
            cards, modo = hc.resolve_cards_para_usuario(session, current_user.id)
        return render_template('personalizar_home.html', cards=cards, modo=modo)
