"""Acciones del bot que tocan la data real de la farmacia.

Cada acción recibe el texto del usuario y devuelve un string (la respuesta).
La búsqueda vive en `bot.data` (compartida con la IA).
"""
from bot.data import buscar_productos
from bot.ia import consulta_ia


def _fmt_precio(p):
    return f'${p:,.0f}'.replace(',', '.') if p else 's/precio'


def consultar_producto(texto):
    """Búsqueda directa por nombre (sin IA): precio + stock."""
    texto = (texto or '').strip()
    if len(texto) < 3:
        return 'Escribime el nombre del producto (al menos 3 letras) 🙂'
    rows = buscar_productos(texto, limite=6)
    if not rows:
        return (f'No encontré "{texto}" en el sistema. 😕\n'
                'Probá con otro nombre, o escribí "menú" y elegí "Hablar con una persona".')
    lineas = []
    for r in rows:
        disp = f'✅ {r["stock"]} en stock' if r['stock'] > 0 else '❌ sin stock'
        lineas.append(f'• {r["producto"]} — {_fmt_precio(r["precio"])} — {disp}')
    return ('Esto encontré 👇\n' + '\n'.join(lineas)
            + '\n\nEscribí "menú" para volver, o el nombre de otro producto.')


# Registro de acciones disponibles (lo usa el cerebro por nombre).
ACCIONES = {
    'consultar_producto': consultar_producto,
    'consulta_ia': consulta_ia,
}
