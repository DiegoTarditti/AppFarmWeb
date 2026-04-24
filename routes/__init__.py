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
    obras_sociales_catalogo,
    clientes,
    procesos,
    partners,
    plantillas,
    auth_routes,
    observer,
    observer_sync,
    admin,
    home_cards,
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
    descuentos,
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
