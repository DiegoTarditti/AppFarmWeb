"""Asignación de pedidos a rutas de reparto (v1: cuadrantes N/S/E/O).

El cuadrante sale del ángulo (bearing) desde la farmacia hasta el domicilio.
Reusa el origen (coords de la farmacia) y el geocoder de bot.envio.
"""
import json
import math
import urllib.parse

import database
from bot import envio

# Rutas por defecto (se siembran la 1ª vez): nombre, cuadrante, color.
DEFAULT_RUTAS = [('Norte', 'N', '#2E7D5B'), ('Sur', 'S', '#B45309'),
                 ('Este', 'E', '#185FA5'), ('Oeste', 'O', '#9333EA')]


def _bearing(lat1, lng1, lat2, lng2):
    """Rumbo en grados (0=N, 90=E, 180=S, 270=O) de (1)→(2)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def cuadrante_de(lat, lng):
    """'N'|'S'|'E'|'O' según el ángulo desde la farmacia. None si falta
    origen configurado o coordenadas."""
    if lat is None or lng is None:
        return None
    cfg = envio.get_config()
    if cfg['farmacia_lat'] is None or cfg['farmacia_lng'] is None:
        return None
    b = _bearing(cfg['farmacia_lat'], cfg['farmacia_lng'], float(lat), float(lng))
    if b < 45 or b >= 315:
        return 'N'
    if b < 135:
        return 'E'
    if b < 225:
        return 'S'
    return 'O'


def coords_de_pedido(domicilio_id=None, direccion=None, localidad=None):
    """(lat, lng) de un domicilio guardado, o geocodificando la dirección dentro de
    `localidad` (clave para no confundir 'Santa Fe' calle con la provincia/ciudad).
    None si no se pudo."""
    if domicilio_id:
        with database.get_db() as s:
            d = s.get(database.DomicilioCliente, domicilio_id)
            if d:
                if d.lat is not None and d.lng is not None:
                    return (d.lat, d.lng)
                if d.direccion and not direccion:
                    direccion = d.direccion
                    localidad = localidad or d.localidad
    if direccion:
        return envio.geocodificar(direccion, localidad=localidad)
    return None


def seed_rutas_si_vacio():
    with database.get_db() as s:
        if s.query(database.RutaReparto).count() == 0:
            for i, (nombre, cuad, color) in enumerate(DEFAULT_RUTAS):
                s.add(database.RutaReparto(nombre=nombre, cuadrante=cuad,
                                           color=color, orden=i))
            s.commit()


_PRIORIDADES = ('urgente', 'normal', 'programado')


def _nn(items, origen):
    """Vecino más cercano desde `origen` sobre items con lat/lng."""
    orden, actual, rest = [], origen, list(items)
    while rest:
        nxt = min(rest, key=lambda it: envio._haversine_m(
            actual[0], actual[1], it['lat'], it['lng']))
        orden.append(nxt)
        rest.remove(nxt)
        actual = (nxt['lat'], nxt['lng'])
    return orden


def secuenciar(items, origen=None):
    """Ordena las paradas para el recorrido: primero por PRIORIDAD
    (urgente → normal → programado) y, dentro de cada grupo, por vecino más
    cercano desde la farmacia. Las sin coordenadas van al final.
    `items`: dicts con 'id','lat','lng' y opcional 'prioridad'."""
    cfg = envio.get_config()
    o = origen or ((cfg['farmacia_lat'], cfg['farmacia_lng'])
                   if cfg['farmacia_lat'] is not None else None)
    con = [it for it in items if it.get('lat') is not None and it.get('lng') is not None]
    sin = [it for it in items if it.get('lat') is None or it.get('lng') is None]
    if not o or not con:
        return con + sin
    out = []
    for prio in _PRIORIDADES:
        out += _nn([it for it in con if (it.get('prioridad') or 'normal') == prio], o)
    otras = [it for it in con if (it.get('prioridad') or 'normal') not in _PRIORIDADES]
    out += _nn(otras, o)
    return out + sin


def ruta_para_cuadrante(s, cuadrante):
    """Ruta activa cuyo criterio es ese cuadrante (None si no hay)."""
    if not cuadrante:
        return None
    return (s.query(database.RutaReparto)
            .filter(database.RutaReparto.cuadrante == cuadrante,
                    database.RutaReparto.activa.is_(True))
            .order_by(database.RutaReparto.orden).first())


def _coords_de_geojson(data):
    """Primer anillo de polígono de un GeoJSON (Feature/FeatureCollection/Polygon).
    GeoJSON viene en [lng, lat] → devolvemos [lat, lng]."""
    geom = data
    if data.get('type') == 'FeatureCollection':
        geom = None
        for f in (data.get('features') or []):
            g = (f or {}).get('geometry') or {}
            if g.get('type') in ('Polygon', 'MultiPolygon'):
                geom = g
                break
        if geom is None:
            return None
    elif data.get('type') == 'Feature':
        geom = data.get('geometry') or {}
    if geom.get('type') == 'Polygon':
        ring = (geom.get('coordinates') or [[]])[0]
    elif geom.get('type') == 'MultiPolygon':
        ring = (geom.get('coordinates') or [[[]]])[0][0]
    else:
        return None
    out = [[c[1], c[0]] for c in ring if isinstance(c, (list, tuple)) and len(c) >= 2]
    return out or None


def parse_poligono(texto):
    """Zona pegada: acepta GeoJSON (de geojson.io) o líneas 'lat, lng' (Google
    Maps). Devuelve [[lat,lng], ...] o None si hay menos de 3 puntos."""
    texto = (texto or '').strip()
    if not texto:
        return None
    if texto.startswith('{') or texto.startswith('['):   # GeoJSON
        try:
            coords = _coords_de_geojson(json.loads(texto))
            if coords and len(coords) >= 3:
                return coords
        except (ValueError, TypeError):
            pass
    pts = []
    for linea in texto.replace(';', '\n').split('\n'):
        linea = linea.strip().strip('()')
        if not linea:
            continue
        partes = linea.split(',')
        if len(partes) >= 2:
            try:
                pts.append([float(partes[0]), float(partes[1])])
            except ValueError:
                continue
    return pts if len(pts) >= 3 else None


def _punto_en_poligono(lat, lng, poly):
    """Ray casting: ¿(lat,lng) cae dentro del polígono `poly` ([[lat,lng],...])?"""
    if not poly or len(poly) < 3 or lat is None or lng is None:
        return False
    x, y = lng, lat
    inside, n, j = False, len(poly), len(poly) - 1
    for i in range(n):
        yi, xi = poly[i][0], poly[i][1]
        yj, xj = poly[j][0], poly[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def ruta_para_punto(s, lat, lng):
    """Asigna por ZONA (polígono) si hay rutas con zona definida: la 1ª cuyo
    polígono contiene el punto, o None (sin asignar) si cae fuera de todas.
    Si NINGUNA ruta tiene zona definida, cae al fallback por cuadrante."""
    rutas = (s.query(database.RutaReparto)
             .filter(database.RutaReparto.activa.is_(True))
             .order_by(database.RutaReparto.orden).all())
    con_zona = [r for r in rutas if r.poligono]
    if con_zona:
        if lat is None or lng is None:
            return None
        for r in con_zona:
            try:
                poly = json.loads(r.poligono)
            except (ValueError, TypeError):
                continue
            if _punto_en_poligono(lat, lng, poly):
                return r
        return None   # fuera de todas las zonas dibujadas → sin asignar
    return ruta_para_cuadrante(s, cuadrante_de(lat, lng))


def link_google_maps(paradas, origen=None):
    """URL de Google Maps con todas las paradas; origen y vuelta = la farmacia.
    `paradas`: lista de (lat, lng). None si no hay paradas válidas."""
    cfg = envio.get_config()
    o = origen
    if o is None and cfg['farmacia_lat'] is not None:
        o = (cfg['farmacia_lat'], cfg['farmacia_lng'])
    pts = [p for p in paradas if p and p[0] is not None and p[1] is not None]
    if not pts:
        return None

    def q(c):
        return f"{c[0]},{c[1]}"

    params = {'api': '1', 'travelmode': 'driving'}
    if o:
        params['origin'] = q(o)
        params['destination'] = q(o)            # el cadete vuelve a la farmacia
        wpts = pts
    else:
        params['destination'] = q(pts[-1])
        wpts = pts[:-1]
    if wpts:
        params['waypoints'] = '|'.join(q(c) for c in wpts)
    return 'https://www.google.com/maps/dir/?' + urllib.parse.urlencode(params, safe='|,')
