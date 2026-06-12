"""Autenticación, roles y permisos.

- Usa Flask-Login para manejar sesiones.
- Define permisos default por rol.
- Expone decorators para proteger rutas y chequear permisos.
"""

import json
from functools import wraps

from flask import abort, flash, redirect, url_for
from flask_login import LoginManager, current_user, login_required
from werkzeug.security import check_password_hash, generate_password_hash

import database
from database import Usuario

# ── Permisos por rol ─────────────────────────────────────────────────────────
# Cada módulo puede tener nivel: 'ver', 'editar', 'admin' (jerárquicos).
# admin > editar > ver > (sin acceso = no listado)
MODULOS = [
    'facturas', 'stock', 'cta_cte', 'config', 'pedidos',
    'procesos', 'productos', 'analisis', 'reclamos', 'usuarios',
    'laboratorios', 'proveedores', 'obras_sociales', 'dashboard',
    'devoluciones',
]

NIVELES = ['ver', 'editar', 'admin']

PERMISOS_POR_ROL = {
    'admin': {m: 'admin' for m in MODULOS},
    'dev':   {m: 'admin' for m in MODULOS},
    'farmacia': {
        'facturas': 'editar', 'stock': 'editar', 'pedidos': 'editar',
        'procesos': 'editar', 'productos': 'ver', 'analisis': 'editar',
        'reclamos': 'editar', 'laboratorios': 'ver', 'proveedores': 'ver',
        'obras_sociales': 'editar', 'dashboard': 'ver', 'cta_cte': 'ver',
    },
    'remoto': {
        'dashboard': 'ver', 'procesos': 'ver', 'pedidos': 'ver',
        'facturas': 'ver', 'productos': 'ver',
    },
    # Rol acotado a /compras/dia (armado de pedidos a droguerías).
    'pedidos': {
        'pedidos': 'editar',
    },
    # Rol acotado a /devoluciones/* (registro de devoluciones por rendición).
    'rendicion': {
        'devoluciones': 'editar',
    },
}


# ── Perfiles de operador ─────────────────────────────────────────────────────
# Modelo nuevo: un operador (rol='operador') tiene una LISTA de perfiles
# (Usuario.perfiles_json). Cada perfil define su botón en el home y los prefijos
# de path que habilita. Algunos heredan acceso a otra área (ej. caja).
# Única fuente de verdad: de acá salen el home, el gating y los checks de /usuarios.
PERFILES = {
    'rendicion': {
        'label': 'Rendición de Recetas', 'icono': '📋',
        'url': '/rend-recetas?perfil=rendicion',
        'prefijos': ['/rend-recetas', '/devoluciones'],
    },
    'chat_clientes': {
        'label': 'Chat Clientes', 'icono': '💬',
        'url': '/atencion',
        # hereda caja; '/api/clientes' y '/config/envio' los usa el cliente_picker
        # embebido en /atencion (buscar cliente, ficha, cotizar envío).
        'prefijos': ['/atencion', '/caja', '/api/clientes', '/config/envio'],
    },
    'pedido_manual': {
        'label': 'Pedido Manual', 'icono': '🛒',
        'url': '/pedido/nuevo',
        # hereda caja; '/api/clientes' y '/config/envio' los usa el cliente_picker
        # embebido en /pedido/nuevo (buscar cliente, ficha, cotizar envío).
        'prefijos': ['/pedido/', '/reparto', '/api/reparto', '/caja',
                     '/api/clientes', '/config/envio'],
    },
    'planilla_envios': {
        'label': 'Planilla Envíos', 'icono': '🛵',
        'url': '/reparto/planilla',
        # '/api/clientes' y '/config/envio' los usa el cliente_picker si edita
        # un pedido desde la planilla; el cotizador también.
        'prefijos': ['/reparto', '/api/reparto', '/api/clientes', '/config/envio'],
    },
    'filtro_drogueria': {
        'label': 'Filtro Droguería', 'icono': '⊞',
        'url': '/filtro-drogueria',
        'prefijos': ['/filtro-drogueria', '/filtro_drogueria'],
    },
    'audit_recetas': {
        'label': 'Auditoría Recetas', 'icono': '✅',
        'url': '/rend-recetas/por-vendedor?perfil=auditor',
        'prefijos': ['/rend-recetas', '/devoluciones'],
    },
    'pedidos_drog': {
        'label': 'Pedidos a Droguerías', 'icono': '📦',
        'url': '/compras/dia',
        'prefijos': ['/compras/', '/pedidos/', '/api/compras/', '/api/pedidos/',
                     '/pedidos-emitidos', '/api/pedido-emitido/', '/api/producto/',
                     '/api/observer-product/', '/api/lab-drog/'],
    },
}

# Paths comunes a todo operador (siempre permitidos).
PERFILES_PATHS_COMUNES = ('/home', '/login', '/logout', '/cambiar-password',
                          '/health', '/static/', '/api/notifications',
                          '/api/sync-status', '/api/presencia')

# Migración de roles viejos (uno por usuario) → lista de perfiles equivalente.
# IMPORTANTE: NO incluir 'operador' acá. 'operador' es el rol canónico actual
# de todos los operadores; si entra en este dict, migrar_roles_a_perfiles() lo
# pisa en cada restart con ["chat_clientes"] y borra los perfiles reales.
ROL_LEGACY_A_PERFILES = {
    'rendicion': ['rendicion'],
    'auditor': ['audit_recetas'],
    'cajero': ['chat_clientes'],   # caja se hereda desde chat_clientes
    'pedidos': ['pedidos_drog'],
}


def perfiles_de_usuario(user):
    """Lista de slugs de perfil del usuario (desde Usuario.perfiles_json)."""
    if not user or not getattr(user, 'is_authenticated', False):
        return []
    try:
        data = json.loads(user.perfiles_json or '[]')
    except (json.JSONDecodeError, TypeError):
        return []
    return [p for p in data if p in PERFILES]


def tiene_perfil(user, slug):
    return slug in perfiles_de_usuario(user)


def es_operador(user):
    """True si el usuario es de tipo operador (no admin/farmacia/dev/remoto)."""
    return getattr(user, 'rol', None) == 'operador'


def prefijos_permitidos(user):
    """Unión de los prefijos de todos los perfiles del usuario."""
    pref = set()
    for slug in perfiles_de_usuario(user):
        pref.update(PERFILES[slug]['prefijos'])
    return pref


def _filtro_drogueria_url():
    """URL del botón 'Filtro Droguería' según la sucursal local.

    Badia (y cualquier farmacia sin las vistas DW.Pedidos expuestas) usa
    el filtro por archivo subido; Pieri y similares usan el filtro por SQL.
    Resuelve via services.transferencias._local_slug (tabla `sucursales`).
    Si no se puede determinar, default al filtro por archivo (más conservador:
    siempre funciona, no depende del DW).
    """
    try:
        from services.transferencias import _local_slug, listar_sucursales
        slug = _local_slug(listar_sucursales())
    except Exception:
        slug = None
    # Sucursales con SQL Pedidos funcional → SQL. Resto → archivo.
    return '/filtro-drogueria' if slug == 'pieri' else '/filtro-drogueria/archivo'


def botones_home(user):
    """Botones del home para el usuario: [{slug, label, icono, url}] en el orden
    del registro PERFILES."""
    mis = set(perfiles_de_usuario(user))
    out = []
    for s in PERFILES:
        if s not in mis:
            continue
        url = PERFILES[s]['url']
        if s == 'filtro_drogueria':
            url = _filtro_drogueria_url()
        out.append({'slug': s, 'label': PERFILES[s]['label'],
                    'icono': PERFILES[s]['icono'], 'url': url})
    return out


def migrar_roles_a_perfiles():
    """One-time: convierte los roles single-screen viejos a rol='operador' +
    perfiles_json. Idempotente (tras convertir, esos roles ya no existen)."""
    with database.get_db() as session:
        legacy = session.query(Usuario).filter(
            Usuario.rol.in_(list(ROL_LEGACY_A_PERFILES))).all()
        for u in legacy:
            u.perfiles_json = json.dumps(ROL_LEGACY_A_PERFILES.get(u.rol, []))
            u.rol = 'operador'
        if legacy:
            session.commit()
        return len(legacy)


login_manager = LoginManager()
login_manager.login_view = 'auth_login'
login_manager.login_message = 'Iniciá sesión para continuar.'
login_manager.login_message_category = 'warning'


@login_manager.user_loader
def _load_user(user_id):
    with database.get_db() as session:
        return session.get(Usuario, int(user_id))


def permisos_default_rol(rol):
    """Devuelve el dict de permisos default para un rol."""
    return dict(PERMISOS_POR_ROL.get(rol, {}))


def nivel_permiso(user, modulo):
    """Nivel que tiene el usuario sobre un módulo: 'admin'|'editar'|'ver'|None."""
    if not user or not user.is_authenticated:
        return None
    if user.rol in ('admin', 'dev'):
        return 'admin'
    try:
        perms = json.loads(user.permisos_json or '{}')
    except (json.JSONDecodeError, TypeError):
        perms = {}
    return perms.get(modulo)


def tiene_permiso(user, modulo, nivel_requerido='ver'):
    """True si user puede actuar sobre modulo con al menos nivel_requerido."""
    actual = nivel_permiso(user, modulo)
    if actual is None:
        return False
    return NIVELES.index(actual) >= NIVELES.index(nivel_requerido)


def requiere_permiso(modulo, nivel='ver'):
    """Decorator: protege una ruta con un permiso específico."""
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            if not tiene_permiso(current_user, modulo, nivel):
                flash(f'No tenés permiso {nivel} sobre {modulo}.', 'error')
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return deco


def hash_password(plain):
    return generate_password_hash(plain)


def verificar_password(user, plain):
    return check_password_hash(user.password_hash, plain)


def seed_admin_si_falta():
    """Crea usuario admin/cambiar123 si la tabla está vacía.
    Llamar desde init_db después de crear la tabla."""
    from sqlalchemy.exc import IntegrityError
    with database.get_db() as session:
        existe = session.query(Usuario).first()
        if existe:
            return
        admin = Usuario(
            username='admin',
            email=None,
            password_hash=hash_password('cambiar123'),
            nombre_completo='Administrador',
            rol='admin',
            permisos_json=json.dumps(permisos_default_rol('admin')),
            activo=True,
            debe_cambiar_password=True,
        )
        session.add(admin)
        try:
            session.commit()
        except IntegrityError:
            # Otro worker ganó la carrera — el admin ya existe.
            session.rollback()


def init_auth(app):
    login_manager.init_app(app)
    # Exponer helpers en templates
    app.jinja_env.globals['tiene_permiso'] = lambda mod, niv='ver': tiene_permiso(current_user, mod, niv)
    app.jinja_env.globals['nivel_permiso'] = lambda mod: nivel_permiso(current_user, mod)
