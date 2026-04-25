"""Route registration — imports each module and calls its init_app(app)."""

from routes import (
    admin,
    auth_routes,
    batch,
    claims,
    clientes,
    converter,
    core,
    cuentas,
    dashboard,
    docs_pendientes,
    home_cards,
    invoices,
    laboratorios,
    modulo_packs,
    obras_sociales,
    obras_sociales_catalogo,
    observer,
    observer_sync,
    partners,
    plantillas,
    procesos,
    productos,
    providers,
    purchase,
    vademecum,
)

_modules = [
    auth_routes,
    observer,
    observer_sync,
    admin,
    home_cards,
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
    dashboard,
    docs_pendientes,
    batch,
    vademecum,
    obras_sociales,
    obras_sociales_catalogo,
    clientes,
    procesos,
    partners,
    plantillas,
]


def register_routes(app):
    for mod in _modules:
        mod.init_app(app)
