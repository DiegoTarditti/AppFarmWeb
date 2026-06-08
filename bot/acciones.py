"""Acciones del bot que tocan la data real de la farmacia.

Cada acción recibe el texto del usuario y devuelve un string (la respuesta).
La búsqueda vive en `bot.data` (compartida con la IA).
"""
from bot import store
from bot.data import buscar_productos
from bot.ia import consulta_ia


def _fmt_precio(p):
    return f'${p:,.0f}'.replace(',', '.') if p else 's/precio'


def consultar_producto(texto):
    """Búsqueda directa por nombre (sin IA): precio + stock.

    Devuelve dict {texto, opciones, esperando, meta}. `meta` alimenta la analítica
    de no-resueltos: si no hay match —o todo está sin stock— es demanda perdida."""
    texto = (texto or '').strip()
    if len(texto) < 3:
        return {'texto': 'Escribime el nombre del producto (al menos 3 letras) 🙂',
                'meta': {'camino': 'precio', 'resuelto': True}}
    _LIMITE = 3
    # Buscamos uno más para detectar si hay demasiados resultados
    rows = buscar_productos(texto, limite=_LIMITE + 1)
    if not rows:
        return {'texto': (f'No encontré "{texto}" en el sistema. 😕\n'
                          'Probá con otro nombre, o escribí "menú" y elegí "Hablar con una persona".'),
                'meta': {'camino': 'precio', 'resuelto': False,
                         'motivo': 'sin_stock', 'producto': texto}}
    if len(rows) > _LIMITE:
        # Búsqueda muy genérica → deriva al operador para que ayude a precisar
        return {
            'texto': (f'Encontré varias opciones para "{texto}" y no quiero confundirte. 🙂\n'
                      'Te paso con alguien del equipo para que te ayuden a elegir.'),
            'derivar': True,
            'esperando': None,
            'meta': {'camino': 'precio', 'resuelto': False, 'motivo': 'muchas_opciones', 'producto': texto},
        }
    hay_stock = any(r['stock'] > 0 for r in rows)
    lineas = []
    for i, r in enumerate(rows, 1):
        disp = f'✅ {r["stock"]}u' if r['stock'] > 0 else '❌ sin stock'
        lineas.append(f'{i}. {r["producto"]} — {_fmt_precio(r["precio"])} — {disp}')
    meta = {'camino': 'precio', 'resuelto': hay_stock}
    if not hay_stock:
        meta.update({'motivo': 'sin_stock', 'producto': texto})
    # Detectar ofertas activas para los productos con stock
    ids_con_stock = [r['observer_id'] for r in rows if r.get('observer_id') and r['stock'] > 0]
    ofertas_meta = store.get_ofertas_para_productos(ids_con_stock) if ids_con_stock else []

    pie = ('\n\nTocá el número para pedirlo, o escribí otro producto 🙂'
           if hay_stock else '\n\nEscribí otro producto o escribí "menú".')
    return {
        'texto': 'Esto encontré 👇\n' + '\n'.join(lineas) + pie,
        'opciones': [f'{i}. {r["producto"]}'[:64] for i, r in enumerate(rows, 1)],
        'esperando': f'elegir_producto:{texto[:32]}',
        'meta': meta,
        'ofertas_meta': ofertas_meta,
    }


def seleccionar_producto(texto, query):
    """El cliente eligió un número de la lista o escribe otro producto."""
    import re
    t = texto.strip()
    rows = buscar_productos(query, limite=6)
    # Acepta "1", "2" (tipeo manual) o "1. NOMBRE..." (botón tapeado)
    m = re.match(r'^(\d+)[.\s]', t)
    if t.isdigit() or m:
        idx = int(m.group(1) if m else t) - 1
        if 0 <= idx < len(rows):
            prod = rows[idx]
            # Pasa a encargar con el producto pre-cargado (con o sin stock;
            # el operador ve el stock real y decide).
            return encargar(f'1 {prod["producto"]}')
        # número fuera de rango → re-mostrar la lista
    if len(t) >= 3 and not t.isdigit():
        # texto libre → nueva búsqueda
        return consultar_producto(t)
    # fallback: recordar las opciones disponibles
    opciones = [f'{i}. {r["producto"]}'[:64] for i, r in enumerate(rows, 1)] if rows else []
    return {
        'texto': 'Escribí el número del producto que te interesa, o el nombre de otro 🙂',
        'opciones': opciones,
        'esperando': f'elegir_producto:{query}',
        'meta': {'camino': 'precio', 'resuelto': True},
    }


def encargar(texto):
    """Toma un encargo del cliente (producto + cantidad) y lo deriva al equipo.
    Devuelve un dict de control: corta el loop y marca handoff (derivar=True)
    para que la consulta caiga en la bandeja del panel con todo el contexto."""
    texto = (texto or '').strip()
    if len(texto) < 3:
        return {'texto': 'Decime qué querés encargar: producto y cantidad 🙂\n(ej: "2 cajas de amoxidal 500")',
                'meta': {'camino': 'encargar', 'resuelto': True}}
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
        'meta': {'camino': 'encargar', 'resuelto': False, 'motivo': 'derivado'},
    }


# Registro de acciones disponibles (lo usa el cerebro por nombre).
ACCIONES = {
    'consultar_producto': consultar_producto,
    'consulta_ia': consulta_ia,
    'encargar': encargar,
}

# Prefijo especial de esperando: no es una acción sino un estado de selección.
# cerebro.py lo intercepta antes del lookup de ACCIONES.
PREFIJO_ELEGIR = 'elegir_producto:'
