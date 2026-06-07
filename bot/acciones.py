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


def encargar(texto):
    """Toma un encargo del cliente (producto + cantidad) y lo deriva al equipo.
    Devuelve un dict de control: corta el loop y marca handoff (derivar=True)
    para que la consulta caiga en la bandeja del panel con todo el contexto."""
    texto = (texto or '').strip()
    if len(texto) < 3:
        return 'Decime qué querés encargar: producto y cantidad 🙂\n(ej: "2 cajas de amoxidal 500")'
    # Si tenemos algo parecido en stock, lo adelantamos (sin prometer nada).
    extra = ''
    if any(r['stock'] > 0 for r in buscar_productos(texto, limite=3)):
        extra = '\n\nParece que tenemos algo parecido en stock; el equipo te confirma.'
    return {
        'texto': ('📝 ¡Anotado! Tu encargo:\n'
                  f'"{texto}"\n\n'
                  'Lo paso al equipo para que te confirmen precio y disponibilidad. '
                  'Te responden por acá en un rato 🙂\n'
                  '🛵 Si lo querés con envío a domicilio, avisales y lo coordinamos.' + extra),
        'esperando': None,   # corta el loop de captura
        'derivar': True,     # → cae en la bandeja del operador
    }


# Registro de acciones disponibles (lo usa el cerebro por nombre).
ACCIONES = {
    'consultar_producto': consultar_producto,
    'consulta_ia': consulta_ia,
    'encargar': encargar,
}
