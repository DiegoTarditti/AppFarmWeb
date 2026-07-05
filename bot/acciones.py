"""Acciones del bot que tocan la data real de la farmacia.

Cada acción recibe el texto del usuario y devuelve un string (la respuesta).
La búsqueda vive en `bot.data` (compartida con la IA).
"""
import os

from bot import store
from bot.data import buscar_productos
from bot.ia import consulta_ia


# Diego 2026-06-22: el bot NO muestra precio al cliente (hasta nuevo aviso).
# Para reactivar: setear BOT_MOSTRAR_PRECIO=1 en el .env (sin restart del bot).
def _mostrar_precio():
    return (os.environ.get('BOT_MOSTRAR_PRECIO') or '').lower() in ('1', 'true', 'yes', 'on')


def _fmt_precio(p):
    return f'${p:,.0f}'.replace(',', '.') if p else 's/precio'


def consultar_producto(texto):
    """Botón 'Consultar Precio/Stock' (Diego 2026-06-22): simplificado a
    informar stock + derivar al operador. Ya NO se ofrece comprar/encargar
    automático ni elegir de una lista numerada — el operador toma la conv y
    cierra la venta a mano.

    Devuelve dict {texto, opciones, esperando, derivar, meta}. `meta` alimenta
    la analítica de no-resueltos: si no hay match —o todo está sin stock— es
    demanda perdida."""
    texto = (texto or '').strip()
    if len(texto) < 3:
        return {'texto': 'Escribime el nombre del producto (al menos 3 letras) 🙂',
                'meta': {'camino': 'precio', 'resuelto': True}}
    _LIMITE = 3
    # Buscamos uno más para detectar si hay demasiados resultados
    rows = buscar_productos(texto, limite=_LIMITE + 1)
    if not rows:
        # Sin matches: también derivamos al operador (puede ser una marca local
        # que no está en obs_productos, una grafía rara, etc.).
        return {
            'texto': (f'No encontré "{texto}" en el sistema. 🤔\n'
                      'Te paso con alguien del equipo para que te ayude.'),
            'derivar': True,
            'esperando': None,
            'meta': {'camino': 'precio', 'resuelto': False,
                     'motivo': 'sin_match', 'producto': texto},
        }
    if len(rows) > _LIMITE:
        # Búsqueda muy genérica → deriva sin listar.
        return {
            'texto': (f'Encontré varias opciones para "{texto}" y no quiero confundirte. 🙂\n'
                      'Te paso con alguien del equipo para que te ayuden a elegir.'),
            'derivar': True,
            'esperando': None,
            'meta': {'camino': 'precio', 'resuelto': False, 'motivo': 'muchas_opciones', 'producto': texto},
        }
    # 1-3 matches: informamos stock (y precio si BOT_MOSTRAR_PRECIO=1) y derivamos.
    hay_stock = any(r['stock'] > 0 for r in rows)
    mostrar_precio = _mostrar_precio()
    lineas = []
    for r in rows:
        disp = f'✅ {r["stock"]}u' if r['stock'] > 0 else '❌ sin stock'
        precio_part = f' — {_fmt_precio(r["precio"])}' if mostrar_precio else ''
        lineas.append(f'• {r["producto"]}{precio_part} — {disp}')
    meta = {'camino': 'precio', 'resuelto': hay_stock}
    if not hay_stock:
        meta.update({'motivo': 'sin_stock', 'producto': texto})
    # Detectar ofertas activas para los productos con stock (info para el panel).
    ids_con_stock = [r['observer_id'] for r in rows if r.get('observer_id') and r['stock'] > 0]
    ofertas_meta = store.get_ofertas_para_productos(ids_con_stock) if ids_con_stock else []
    cab = 'Esto encontré 👇' if hay_stock else 'Esto encontré (sin stock) 👇'
    pie = '\n\nTe paso con alguien del equipo para coordinar 🙂'
    return {
        'texto': cab + '\n' + '\n'.join(lineas) + pie,
        'derivar': True,
        'esperando': None,
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
            # Antes pasaba directo a `encargar`. Ahora devuelve un MARCADOR para
            # que cerebro decida: si la conv ya tiene cliente vinculado con DNI →
            # `id_confirmar_dni`; si no → `id_pedir_dni`. El producto se guarda en
            # `BotConversacion.producto_pendiente` durante los 1-3 turnos de id.
            return {
                'meta': {'camino': 'precio', 'resuelto': True,
                         'proximo_paso': 'iniciar_id',
                         'producto': f'1 {prod["producto"]}'},
            }
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


# ── Flujo de identificación post-selección de producto ──────────────────────
def identificar_por_dni(texto):
    """Recibe lo que tipeó el cliente en `id_pedir_dni`. 3 caminos:

    - skip ('no', 'pasa', 'cancelar', ...) → derivar al equipo con lo que haya.
    - match único en obs_clientes/clientes → vincular + encargar con la ficha.
    - match ambiguo o sin match → ofrecer dejar el nombre (paso opcional).
    - input no es DNI válido → pedir de nuevo una vez (cerebro reintenta).

    Devuelve dict de control: el cerebro hace el "encargar / vincular / saltar al
    siguiente nodo" según meta.proximo_paso. Usa `producto_pendiente` (guardado
    en BotConversacion al entrar a id_pedir_dni)."""
    from bot import store
    t = (texto or '').strip().lower()
    if t in ('no', 'paso', 'pasame', 'pasame al equipo', 'no, paso al equipo',
             'no paso al equipo', 'skip', 'cancelar'):
        return {
            'meta': {'camino': 'identificar', 'resuelto': True, 'proximo_paso': 'derivar_sin_id'},
        }
    digs = ''.join(ch for ch in (texto or '') if ch.isdigit())
    if not (7 <= len(digs) <= 8):
        return {
            'texto': '⚠️ El DNI tiene que ser 7 u 8 dígitos. Probá de nuevo, o decime *no* y te paso al equipo.',
            'esperando': 'identificar_por_dni',
            'meta': {'camino': 'identificar', 'resuelto': True, 'proximo_paso': 'reintentar'},
        }
    oid, ambiguo = store.match_cliente_por_dni(digs)
    if oid:
        return {
            'meta': {'camino': 'identificar', 'resuelto': True,
                     'proximo_paso': 'vincular_y_encargar', 'observer_id': oid},
        }
    return {
        'meta': {'camino': 'identificar', 'resuelto': True,
                 'proximo_paso': 'ofrecer_nombre', 'dni_input': digs,
                 'ambiguo': ambiguo},
    }


def guardar_nombre_y_encargar(texto):
    """Guarda el nombre+apellido como lead local y deriva al operador.
    El cerebro hace el create + encargar(producto_pendiente)."""
    t = (texto or '').strip()
    if len(t) < 2:
        return {
            'texto': '👤 Decime tu nombre y apellido para anotarlo.',
            'esperando': 'guardar_nombre_y_encargar',
            'meta': {'camino': 'identificar', 'resuelto': True, 'proximo_paso': 'reintentar'},
        }
    return {
        'meta': {'camino': 'identificar', 'resuelto': True,
                 'proximo_paso': 'crear_local_y_encargar', 'nombre_input': t},
    }


# Registro de acciones disponibles (lo usa el cerebro por nombre).
ACCIONES = {
    'consultar_producto': consultar_producto,
    'consulta_ia': consulta_ia,
    'encargar': encargar,
    'identificar_por_dni': identificar_por_dni,
    'guardar_nombre_y_encargar': guardar_nombre_y_encargar,
}

# Prefijo especial de esperando: no es una acción sino un estado de selección.
# cerebro.py lo intercepta antes del lookup de ACCIONES.
PREFIJO_ELEGIR = 'elegir_producto:'
