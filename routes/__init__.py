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
)

_modules = [
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
]


def register_routes(app):
    for mod in _modules:
        mod.init_app(app)
