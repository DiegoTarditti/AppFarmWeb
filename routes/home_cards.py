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
                    flash('Se muestran todas las cards de nuevo.', 'success')
                    return redirect(url_for('personalizar_home'))
                # Guardar — solo visibilidad (ocultar/mostrar). El orden lo maneja
                # el sistema (auto por clicks dentro de cada banda); el color custom
                # se discontinuó al pasar al diseño por bandas (2026-05-24).
                ocultos = request.form.getlist('ocultos')
                ocultos = [x for x in ocultos if x in hc.ACCIONES_HOME_BY_ID]
                prefs = {'modo': 'auto', 'orden': [], 'colores': {}, 'ocultos': ocultos}
                u.preferencias_home_json = json.dumps(prefs, ensure_ascii=False)
                session.commit()
                flash('Listo, se actualizaron las cards visibles.', 'success')
                return redirect(url_for('personalizar_home'))

            # GET
            cards, modo = hc.resolve_cards_para_usuario(session, current_user.id)
        return render_template('personalizar_home.html', cards=cards, modo=modo)
