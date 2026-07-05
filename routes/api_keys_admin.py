"""Panel admin para emitir y revocar API keys de la API pública.

- Solo accesible por admins de AppFarmWeb.
- Al crear una key: se muestra el token en claro UNA sola vez (después queda hasheado).
- Revocar: soft-delete (activo=False), mantiene el histórico de uso.
"""
import hashlib
import secrets

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import desc

import database


def _hash_key(clave):
    return hashlib.sha256(clave.encode('utf-8')).hexdigest()


def _generar_key():
    """Genera un token seguro. Formato: 'apf_' + 40 chars random. El prefix
    'apf_' identifica que es una key de AppFarmWeb Público."""
    token = 'apf_' + secrets.token_urlsafe(30)
    return token


def _es_admin():
    return (getattr(current_user, 'rol', None) or '').lower() == 'admin'


def init_app(app):

    @app.route('/admin/api-keys')
    @login_required
    def admin_api_keys():
        if not _es_admin():
            flash('Solo admins pueden gestionar API keys.', 'error')
            return redirect(url_for('index'))
        with database.get_db() as s:
            rows = (s.query(database.ApiKey)
                    .order_by(desc(database.ApiKey.creado_en)).all())
            keys = [{
                'id': k.id,
                'cliente_nombre': k.cliente_nombre,
                'prefix': k.prefix,
                'activo': k.activo,
                'cuota_diaria': k.cuota_diaria,
                'usos_hoy': k.usos_hoy or 0,
                'total_usos': k.total_usos or 0,
                'ultimo_uso': k.ultimo_uso,
                'ultimo_ip': k.ultimo_ip,
                'creado_en': k.creado_en,
                'notas': k.notas or '',
            } for k in rows]
        # Token recién creado (viene por query string, se muestra 1 vez)
        token_nuevo = request.args.get('token', '')
        return render_template('admin_api_keys.html',
                               keys=keys, token_nuevo=token_nuevo)

    @app.route('/admin/api-keys/crear', methods=['POST'])
    @login_required
    def admin_api_keys_crear():
        if not _es_admin():
            return redirect(url_for('index'))
        cliente = (request.form.get('cliente_nombre') or '').strip()
        cuota_raw = (request.form.get('cuota_diaria') or '').strip()
        notas = (request.form.get('notas') or '').strip() or None
        if not cliente:
            flash('Falta el nombre del cliente.', 'error')
            return redirect(url_for('admin_api_keys'))
        cuota = None
        if cuota_raw:
            try:
                cuota = max(1, int(cuota_raw))
            except ValueError:
                cuota = None
        token = _generar_key()
        with database.get_db() as s:
            ak = database.ApiKey(
                cliente_nombre=cliente,
                key_hash=_hash_key(token),
                prefix=token[:8],
                activo=True,
                cuota_diaria=cuota,
                notas=notas,
                creado_por=current_user.id,
            )
            s.add(ak)
            s.commit()
        flash(f'Key creada para "{cliente}". Copiala AHORA — no se muestra otra vez.', 'success')
        return redirect(url_for('admin_api_keys', token=token))

    @app.route('/admin/api-keys/debug-hashes')
    @login_required
    def admin_api_keys_debug_hashes():
        """DEBUG: devuelve el hash guardado por cada key para comparar con
        lo que hashea el cliente. Solo admin. Sacar cuando termine el diagnostico."""
        if not _es_admin():
            return jsonify({'error': 'admin only'}), 403
        with database.get_db() as s:
            rows = s.query(database.ApiKey).all()
            return jsonify({'keys': [
                {'id': k.id, 'cliente': k.cliente_nombre, 'prefix': k.prefix,
                 'activo': k.activo,
                 'hash_inicio': (k.key_hash or '')[:20],
                 'hash_largo': len(k.key_hash or '')}
                for k in rows]})

    @app.route('/admin/api-keys/<int:kid>/toggle', methods=['POST'])
    @login_required
    def admin_api_keys_toggle(kid):
        if not _es_admin():
            return redirect(url_for('index'))
        with database.get_db() as s:
            ak = s.get(database.ApiKey, kid)
            if ak:
                ak.activo = not ak.activo
                s.commit()
                flash(f'Key {"activada" if ak.activo else "revocada"}.', 'success')
        return redirect(url_for('admin_api_keys'))

    @app.route('/admin/api-keys/<int:kid>/delete', methods=['POST'])
    @login_required
    def admin_api_keys_delete(kid):
        if not _es_admin():
            return redirect(url_for('index'))
        with database.get_db() as s:
            ak = s.get(database.ApiKey, kid)
            if ak:
                cliente = ak.cliente_nombre
                s.delete(ak)
                s.commit()
                flash(f'Key de "{cliente}" eliminada.', 'success')
        return redirect(url_for('admin_api_keys'))
