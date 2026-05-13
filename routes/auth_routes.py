"""Rutas de autenticación y gestión de usuarios."""

import json
from datetime import datetime

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

import database
from auth import (
    MODULOS,
    NIVELES,
    hash_password,
    permisos_default_rol,
    requiere_permiso,
    seed_admin_si_falta,
    seed_pedidos_si_falta,
    seed_rendicion_si_falta,
    verificar_password,
)
from database import Usuario
from helpers import now_ar


def init_app(app):
    # Garantizar admin inicial + user 'pedidos' al arranque
    seed_admin_si_falta()
    seed_pedidos_si_falta()
    seed_rendicion_si_falta()

    # ── Guard global para rol 'pedidos' ─────────────────────────────────
    # Sólo puede navegar a /compras/* y a las APIs que use ese flujo.
    # Cualquier otro path lo redirige a /compras/dia.
    _PEDIDOS_PATHS_OK = (
        '/pedidos/', '/api/pedidos/',
        '/compras/', '/api/compras/',  # legacy: rutas no-renombradas (labs-drogerias, rapido)
        '/api/producto/',
        '/api/observer-product/', '/api/lab-drog/',
        '/pedidos-emitidos', '/api/pedido-emitido/',
        '/static/',
    )
    _PEDIDOS_PATHS_OK_EXACT = {
        '/login', '/logout', '/cambiar-password', '/health',
        '/api/notifications', '/api/sync-status', '/api/dockerpanel-info',
    }

    @app.before_request
    def _restrict_rol_pedidos():
        if not current_user.is_authenticated:
            return None
        if getattr(current_user, 'rol', None) != 'pedidos':
            return None
        p = request.path or '/'
        if p in _PEDIDOS_PATHS_OK_EXACT:
            return None
        if any(p.startswith(pref) for pref in _PEDIDOS_PATHS_OK):
            return None
        return redirect(url_for('compras_dia'))

    # Rol 'rendicion': solo accede a /devoluciones/* y /rend.
    _RENDICION_PATHS_OK = ('/devoluciones/', '/static/')
    _RENDICION_PATHS_OK_EXACT = {
        '/devoluciones', '/rend',
        '/login', '/logout', '/cambiar-password', '/health',
    }

    @app.before_request
    def _restrict_rol_rendicion():
        if not current_user.is_authenticated:
            return None
        if getattr(current_user, 'rol', None) != 'rendicion':
            return None
        p = request.path or '/'
        if p in _RENDICION_PATHS_OK_EXACT:
            return None
        if any(p.startswith(pref) for pref in _RENDICION_PATHS_OK):
            return None
        return redirect(url_for('devoluciones_buscar'))

    @app.route('/login', methods=['GET', 'POST'])
    def auth_login():
        if current_user.is_authenticated:
            return redirect(url_for('index'))
        if request.method == 'POST':
            username = (request.form.get('username') or '').strip().lower()
            password = request.form.get('password') or ''
            with database.get_db() as session:
                user = session.query(Usuario).filter_by(username=username).first()
                if not user or not verificar_password(user, password):
                    flash('Usuario o contraseña incorrectos.', 'error')
                    return render_template('login.html', username=username)
                if not user.activo:
                    flash('Usuario desactivado. Contactá al administrador.', 'error')
                    return render_template('login.html', username=username)
                user.ultimo_login = now_ar()
                session.commit()
                login_user(user, remember=True)
            if user.debe_cambiar_password:
                flash('Tenés que cambiar tu contraseña antes de continuar.', 'warning')
                return redirect(url_for('auth_cambiar_password'))
            # Rol 'pedidos' tiene una sola pantalla — saltearse el index.
            if user.rol == 'pedidos':
                return redirect(request.args.get('next') or url_for('compras_dia'))
            if user.rol == 'rendicion':
                return redirect(request.args.get('next') or url_for('devoluciones_buscar'))
            return redirect(request.args.get('next') or url_for('index'))
        return render_template('login.html')

    @app.route('/logout')
    def auth_logout():
        logout_user()
        flash('Sesión cerrada.', 'info')
        return redirect(url_for('auth_login'))

    @app.route('/cambiar-password', methods=['GET', 'POST'])
    @login_required
    def auth_cambiar_password():
        if request.method == 'POST':
            actual = request.form.get('password_actual') or ''
            nueva = request.form.get('password_nueva') or ''
            confirmar = request.form.get('password_confirmar') or ''
            with database.get_db() as session:
                user = session.get(Usuario, int(current_user.get_id()))
                if not verificar_password(user, actual):
                    flash('La contraseña actual es incorrecta.', 'error')
                    return render_template('cambiar_password.html')
                if len(nueva) < 6:
                    flash('La nueva contraseña debe tener al menos 6 caracteres.', 'error')
                    return render_template('cambiar_password.html')
                if nueva != confirmar:
                    flash('Las contraseñas no coinciden.', 'error')
                    return render_template('cambiar_password.html')
                user.password_hash = hash_password(nueva)
                user.debe_cambiar_password = False
                session.commit()
            flash('Contraseña actualizada.', 'success')
            if current_user.rol == 'pedidos':
                return redirect(url_for('compras_dia'))
            if current_user.rol == 'rendicion':
                return redirect(url_for('devoluciones_buscar'))
            return redirect(url_for('index'))
        return render_template('cambiar_password.html')

    @app.route('/usuarios')
    @requiere_permiso('usuarios', 'ver')
    def usuarios_list():
        with database.get_db() as session:
            users = session.query(Usuario).order_by(Usuario.username).all()
            data = [{
                'id': u.id, 'username': u.username, 'email': u.email or '',
                'nombre_completo': u.nombre_completo or '',
                'rol': u.rol, 'activo': u.activo,
                'ultimo_login': u.ultimo_login.strftime('%d/%m/%Y %H:%M') if u.ultimo_login else '—',
                'permisos': json.loads(u.permisos_json or '{}'),
            } for u in users]
        return render_template('usuarios_list.html', usuarios=data,
                               modulos=MODULOS, niveles=NIVELES,
                               roles=['farmacia', 'dev', 'remoto', 'admin'])

    @app.route('/usuarios/crear', methods=['POST'])
    @requiere_permiso('usuarios', 'admin')
    def usuarios_crear():
        username = (request.form.get('username') or '').strip().lower()
        email = (request.form.get('email') or '').strip() or None
        nombre = (request.form.get('nombre_completo') or '').strip() or None
        rol = (request.form.get('rol') or 'remoto').strip()
        password = request.form.get('password') or ''
        if not username or not password:
            flash('Usuario y contraseña son obligatorios.', 'error')
            return redirect(url_for('usuarios_list'))
        if rol not in PERMISOS_ROLES:
            flash('Rol inválido.', 'error')
            return redirect(url_for('usuarios_list'))
        with database.get_db() as session:
            if session.query(Usuario).filter_by(username=username).first():
                flash('Ya existe un usuario con ese nombre.', 'error')
                return redirect(url_for('usuarios_list'))
            u = Usuario(
                username=username, email=email, nombre_completo=nombre,
                password_hash=hash_password(password),
                rol=rol,
                permisos_json=json.dumps(permisos_default_rol(rol)),
                activo=True, debe_cambiar_password=True,
            )
            session.add(u)
            session.commit()
        flash(f'Usuario "{username}" creado. Debe cambiar su contraseña al loguearse.', 'success')
        return redirect(url_for('usuarios_list'))

    @app.route('/usuarios/<int:user_id>/editar', methods=['POST'])
    @requiere_permiso('usuarios', 'admin')
    def usuarios_editar(user_id):
        with database.get_db() as session:
            u = session.get(Usuario, user_id)
            if not u:
                flash('Usuario no encontrado.', 'error')
                return redirect(url_for('usuarios_list'))
            u.email = (request.form.get('email') or '').strip() or None
            u.nombre_completo = (request.form.get('nombre_completo') or '').strip() or None
            nuevo_rol = (request.form.get('rol') or u.rol).strip()
            if nuevo_rol != u.rol and nuevo_rol in PERMISOS_ROLES:
                u.rol = nuevo_rol
                u.permisos_json = json.dumps(permisos_default_rol(nuevo_rol))
            u.activo = request.form.get('activo') == '1'
            # Permisos granulares: si se mandaron checkboxes
            permisos_form = {}
            for mod in MODULOS:
                nivel = (request.form.get(f'perm_{mod}') or '').strip()
                if nivel in NIVELES:
                    permisos_form[mod] = nivel
            if permisos_form:
                u.permisos_json = json.dumps(permisos_form)
            session.commit()
        flash('Usuario actualizado.', 'success')
        return redirect(url_for('usuarios_list'))

    @app.route('/usuarios/<int:user_id>/reset-password', methods=['POST'])
    @requiere_permiso('usuarios', 'admin')
    def usuarios_reset_password(user_id):
        nueva = request.form.get('password') or ''
        if len(nueva) < 6:
            flash('La contraseña debe tener al menos 6 caracteres.', 'error')
            return redirect(url_for('usuarios_list'))
        with database.get_db() as session:
            u = session.get(Usuario, user_id)
            if not u:
                flash('Usuario no encontrado.', 'error')
                return redirect(url_for('usuarios_list'))
            u.password_hash = hash_password(nueva)
            u.debe_cambiar_password = True
            session.commit()
        flash(f'Contraseña de "{u.username}" reseteada. Deberá cambiarla al loguearse.', 'success')
        return redirect(url_for('usuarios_list'))

    @app.route('/usuarios/<int:user_id>/delete', methods=['POST'])
    @requiere_permiso('usuarios', 'admin')
    def usuarios_delete(user_id):
        with database.get_db() as session:
            u = session.get(Usuario, user_id)
            if not u:
                flash('Usuario no encontrado.', 'error')
                return redirect(url_for('usuarios_list'))
            if u.username == 'admin':
                flash('No se puede eliminar al usuario admin.', 'error')
                return redirect(url_for('usuarios_list'))
            session.delete(u)
            session.commit()
        flash('Usuario eliminado.', 'info')
        return redirect(url_for('usuarios_list'))


# Lista de roles válidos usada en crear/editar
PERMISOS_ROLES = {'farmacia', 'dev', 'remoto', 'admin', 'pedidos'}
