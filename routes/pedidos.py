"""Pedidos — pantalla de alta manual (`/pedido/nuevo`).

Conceptualmente es entrada al flujo de pedido, no de reparto. Se separó de
`routes/reparto.py` para que `reparto.py` quede solo con lo del reparto del
día (mapa, rutas, planilla, vista cadete).

Las APIs que consume esta pantalla (buscar-cliente, ficha cliente, domicilios,
geocodificar, separar-direccion, crear pedido) todavía viven en `reparto.py`
bajo el prefijo `/reparto/api/*` porque también las consume el mapa. Próximo
paso: mover esas APIs a `/api/clientes/*` junto con el componente reusable
`cliente_picker` (ver docs/mejoras_pendientes.md y docs/flujo_pedido_despacho.md).
"""
from flask import render_template
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
        return render_template('pedido_nuevo.html')
