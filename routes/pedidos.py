"""Pedidos — pantalla de alta manual (`/pedido/nuevo`).

DEPRECADO (refactor C, 2026-06-15): /pedido/nuevo se unificó con /atencion. La
ruta sigue existiendo como redirect a /atencion?modo=manual&new=1 para no
romper links viejos (sidebar, bookmarks, deep-links con observer_id).

El template templates/pedido_nuevo.html ya no se renderiza pero queda en el
repo como referencia hasta la etapa 4 del refactor (cleanup).
"""
from flask import redirect, request
from flask_login import current_user, login_required

from auth import tiene_perfil

_ROLES_OK = ('admin', 'dev', 'farmacia')


def _ok():
    # Roles legacy entran directo; operadores entran si tienen el perfil correspondiente.
    return (getattr(current_user, 'rol', None) in _ROLES_OK
            or tiene_perfil(current_user, 'pedido_manual'))


def init_app(app):

    @app.route('/pedido/nuevo')
    @login_required
    def pedido_nuevo():
        if not _ok():
            return 'Sin permiso', 403
        # Redirect a /atencion en modo manual. Preservamos observer_id si vino
        # como deep-link (ej. desde un botón "→ Crear pedido para este cliente").
        # El modo manual crea una BotConversacion stub y abre el panel simplificado.
        observer_id = request.args.get('observer_id')
        target = '/atencion?modo=manual&new=1'
        if observer_id:
            target += f'&observer_id={observer_id}'
        return redirect(target)
