"""Route registration — imports each module and calls its init_app(app)."""

from routes import (
    core,
    providers,
    laboratorios,
    invoices,
    converter,
    claims,
    purchase,
    modulo_packs,
    productos,
    cuentas,
    descuentos,
    dashboard,
    docs_pendientes,
    batch,
    vademecum,
    obras_sociales,
    procesos,
    partners,
    auth_routes,
    observer,
    admin,
)

_modules = [
    auth_routes,
    observer,
    admin,
    core,
    providers,
    laboratorios,
    invoices,
    converter,
    claims,
    purchase,
    modulo_packs,
    productos,
    cuentas,
    descuentos,
    dashboard,
    docs_pendientes,
    batch,
    vademecum,
    obras_sociales,
    procesos,
    partners,
]


def register_routes(app):
    for mod in _modules:
        mod.init_app(app)
