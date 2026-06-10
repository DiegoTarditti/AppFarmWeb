"""Rutas de autenticación y gestión de usuarios."""

import json
from datetime import datetime

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

import database
from auth import (
    MODULOS,
    NIVELES,
    PERFILES,
    PERFILES_PATHS_COMUNES,
    botones_home,
    es_operador,
    hash_password,
    perfiles_de_usuario,
    permisos_default_rol,
    prefijos_permitidos,
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
    from auth import migrar_roles_a_perfiles
    migrar_roles_a_perfiles()

    # ── Guard unificado de operadores ────────────────────────────────────
    # Un operador (rol='operador') solo puede navegar a los prefijos de SUS
    # perfiles (Usuario.perfiles_json) + los paths comunes. Cualquier otro path
    # lo manda al home. Reemplaza los 5 guards single-rol anteriores.
    @app.before_request
    def _restrict_operador():
        if not current_user.is_authenticated:
            return None
        if not es_operador(current_user):
            return None   # admin/farmacia/dev/remoto: acceso normal (con sidebar)
        p = request.path or '/'
        if any(p.startswith(c) for c in PERFILES_PATHS_COMUNES):
            return None
        if any(p.startswith(pref) for pref in prefijos_permitidos(current_user)):
            return None
        return redirect(url_for('home_operador'))

    @app.route('/home')
    @login_required
    def home_operador():
        """Home standalone (sin sidebar) con un botón por perfil del operador."""
        return render_template('home_operador.html',
                               botones=botones_home(current_user),
                               nombre=current_user.nombre_completo or current_user.username)

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
            # Operadores → home con botones por perfil. El resto → index (sidebar).
            if user.rol == 'operador':
                return redirect(request.args.get('next') or url_for('home_operador'))
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
            if current_user.rol == 'operador':
                return redirect(url_for('home_operador'))
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
                               roles=['farmacia', 'dev', 'remoto', 'admin', 'pedidos', 'auditor', 'rendicion', 'operador', 'cajero'])

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
PERMISOS_ROLES = {'farmacia', 'dev', 'remoto', 'admin', 'operador',
                  # legacy (se migran a 'operador' al arranque; se aceptan por compat)
                  'pedidos', 'auditor', 'rendicion', 'cajero'}
