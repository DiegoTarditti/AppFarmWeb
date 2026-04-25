"""Reset de datos operativos agrupados por módulo.

Usado tanto desde CLI (python scripts/reset_datos.py --grupos pedidos,facturas --ejecutar)
como desde el panel admin en /admin/reset-datos.

NO toca: usuarios, configuracion, laboratorios, proveedores, tablas obs_*.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import (
    AnalisisSesion,
    BarcodeMapping,
    Claim,
    ClaimItem,
    DescuentoCampana,
    DescuentoModulo,
    DescuentoModuloItem,
    DocumentoPendiente,
    ErpStock,
    ExportTemplate,
    Invoice,
    InvoiceBatch,
    InvoiceItem,
    Modulo,
    ModuloPack,
    OfertaMinimo,
    PagoAjusteCC,
    Pedido,
    PedidoItem,
    PlantillaCampo,
    PlantillaExportacion,
    ProcesoCompra,
    ProductAnalytics,
    Producto,
    ProductoPrecioHist,
    StockDifference,
    get_db,
    init_db,
)

# Cada grupo tiene tablas en orden topológico (hijas antes que padres)
# para evitar violaciones de FK al borrar.
GRUPOS = {
    'pedidos': {
        'label': 'Pedidos y análisis',
        'descripcion': 'Pedidos guardados, sus ítems, sesiones de análisis, snapshots de analytics, histórico de precios.',
        'tablas': [
            ('PedidoItem', PedidoItem),
            ('Pedido', Pedido),
            ('AnalisisSesion', AnalisisSesion),
            ('ProductAnalytics', ProductAnalytics),
            ('ProductoPrecioHist', ProductoPrecioHist),
        ],
    },
    'facturas': {
        'label': 'Facturas, cruce y reclamos',
        'descripcion': 'Facturas, sus ítems, diferencias ERP, stock ERP cargado, reclamos, batches, mappings de códigos, docs pendientes.',
        'tablas': [
            ('ClaimItem', ClaimItem),
            ('Claim', Claim),
            ('StockDifference', StockDifference),
            ('InvoiceItem', InvoiceItem),
            ('Invoice', Invoice),
            ('InvoiceBatch', InvoiceBatch),
            ('ErpStock', ErpStock),
            ('BarcodeMapping', BarcodeMapping),
            ('DocumentoPendiente', DocumentoPendiente),
        ],
    },
    'modulos': {
        'label': 'Módulos, packs y ofertas',
        'descripcion': 'Módulos de descuento, packs, ofertas mínimas, campañas de descuento.',
        'tablas': [
            ('ModuloPack', ModuloPack),
            ('Modulo', Modulo),
            ('OfertaMinimo', OfertaMinimo),
            ('DescuentoModuloItem', DescuentoModuloItem),
            ('DescuentoModulo', DescuentoModulo),
            ('DescuentoCampana', DescuentoCampana),
        ],
    },
    'procesos': {
        'label': 'Procesos de compra',
        'descripcion': 'Procesos del ciclo análisis → pedido → factura → cruce → reclamo.',
        'tablas': [
            ('ProcesoCompra', ProcesoCompra),
        ],
    },
    'productos': {
        'label': 'Catálogo de productos',
        'descripcion': 'Catálogo local de productos. Se repuebla solo desde facturas o vinculación con ObServer.',
        'tablas': [
            ('Producto', Producto),
        ],
    },
    'plantillas': {
        'label': 'Plantillas de exportación',
        'descripcion': 'Plantillas de export por lab/proveedor y sus campos.',
        'tablas': [
            ('PlantillaCampo', PlantillaCampo),
            ('PlantillaExportacion', PlantillaExportacion),
            ('ExportTemplate', ExportTemplate),
        ],
    },
    'pagos': {
        'label': 'Pagos y ajustes CC',
        'descripcion': 'Pagos y ajustes de cuenta corriente de proveedores.',
        'tablas': [
            ('PagoAjusteCC', PagoAjusteCC),
        ],
    },
    'archivos_raiz': {
        'label': 'Archivos uploads/ (raíz)',
        'descripcion': 'PDFs y Excels sueltos subidos a la bandeja raíz.',
        'tipo': 'filesystem',
        'dirs': ['uploads'],
        'solo_raiz': True,
    },
    'archivos_converter': {
        'label': 'Archivos uploads/converter/',
        'descripcion': 'PDFs del aprendizaje de parsers + meta.json.',
        'tipo': 'filesystem',
        'dirs': ['uploads/converter'],
    },
    'archivos_purchase': {
        'label': 'Archivos uploads/purchase/',
        'descripcion': 'JSONs de caché de análisis de ventas.',
        'tipo': 'filesystem',
        'dirs': ['uploads/purchase'],
    },
}


def _base_path():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _contar_archivos(dirs, solo_raiz=False):
    total = 0
    base = _base_path()
    for d in dirs:
        abs_d = os.path.join(base, d)
        if not os.path.isdir(abs_d):
            continue
        if solo_raiz:
            for fn in os.listdir(abs_d):
                if os.path.isfile(os.path.join(abs_d, fn)):
                    total += 1
        else:
            for _r, _dd, files in os.walk(abs_d):
                total += len(files)
    return total


def _borrar_archivos(dirs, solo_raiz=False):
    borrados = 0
    base = _base_path()
    for d in dirs:
        abs_d = os.path.join(base, d)
        if not os.path.isdir(abs_d):
            continue
        if solo_raiz:
            for fn in os.listdir(abs_d):
                p = os.path.join(abs_d, fn)
                if os.path.isfile(p):
                    try:
                        os.remove(p)
                        borrados += 1
                    except OSError:
                        pass
        else:
            for root, _dd, files in os.walk(abs_d):
                for fn in files:
                    try:
                        os.remove(os.path.join(root, fn))
                        borrados += 1
                    except OSError:
                        pass
    return borrados


def calcular_dry_run():
    """Devuelve {grupo_key: {'total': int, 'detalle': [(nombre, n), ...]}}"""
    resultado = {}
    with get_db() as session:
        for key, grp in GRUPOS.items():
            if grp.get('tipo') == 'filesystem':
                total = _contar_archivos(grp['dirs'], grp.get('solo_raiz', False))
                resultado[key] = {'total': total, 'detalle': [(d, None) for d in grp['dirs']]}
            else:
                detalle = []
                total = 0
                for nombre, modelo in grp['tablas']:
                    n = session.query(modelo).count()
                    detalle.append((nombre, n))
                    total += n
                resultado[key] = {'total': total, 'detalle': detalle}
    return resultado


def ejecutar_reset(seleccion):
    """seleccion: iterable de grupo_keys. Retorna lista de líneas de log."""
    seleccion = set(seleccion)
    logs = []
    # DB primero
    with get_db() as session:
        for key in seleccion:
            grp = GRUPOS.get(key)
            if not grp or grp.get('tipo') == 'filesystem':
                continue
            for nombre, modelo in grp['tablas']:
                n = session.query(modelo).delete()
                if n:
                    logs.append(f'[{key}] {nombre}: {n} filas borradas')
        session.commit()
    # Archivos después
    for key in seleccion:
        grp = GRUPOS.get(key)
        if not grp or grp.get('tipo') != 'filesystem':
            continue
        n = _borrar_archivos(grp['dirs'], grp.get('solo_raiz', False))
        if n:
            logs.append(f'[{key}] {n} archivos eliminados de {", ".join(grp["dirs"])}')
    return logs


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Reset de datos operativos')
    parser.add_argument('--grupos', help='Lista separada por comas: ' + ','.join(GRUPOS.keys()))
    parser.add_argument('--ejecutar', action='store_true', help='Sin este flag, solo dry-run')
    args = parser.parse_args()

    init_db()

    print('\n=== Conteos actuales ===')
    dry = calcular_dry_run()
    for key, grp in GRUPOS.items():
        r = dry[key]
        print(f'\n[{key}] {grp["label"]} — total: {r["total"]}')
        for det in r['detalle']:
            if det[1] is None:
                print(f'    {det[0]}/ (archivos)')
            else:
                print(f'    {det[0]:25} {det[1]}')

    if not args.grupos:
        print('\n(no se pasó --grupos, nada para ejecutar)')
        return
    seleccion = [g.strip() for g in args.grupos.split(',') if g.strip()]
    invalidos = [g for g in seleccion if g not in GRUPOS]
    if invalidos:
        print(f'\nGrupos inválidos: {invalidos}')
        return

    if not args.ejecutar:
        print(f'\n(dry-run — se borrarían los grupos: {seleccion}. Pasá --ejecutar para confirmar)')
        return

    print(f'\n=== Ejecutando borrado de {seleccion} ===')
    logs = ejecutar_reset(seleccion)
    for line in logs:
        print('  ' + line)
    print('\n✓ Listo.')


if __name__ == '__main__':
    main()
