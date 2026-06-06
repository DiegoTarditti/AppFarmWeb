"""Cotización de envío de pedidos al cliente.

Modelo híbrido (Fase 1):
- Una zona NOMBRADA (refinería, centro, Roldán…) tiene tarifa fija y PISA al
  cálculo por distancia.
- Si no matchea ninguna zona, se cobra por TRAMO de cuadras.

Las cuadras las provee el operador (Fase 1) o el bot desde la ubicación
(Fase 2). lat/lng/radio_km de las zonas quedan NULL hasta Fase 2 (pin → círculo).
"""
import math
import unicodedata

import requests

import database

GEOREF_URL = 'https://apis.datos.gob.ar/georef/api/direcciones'

# Grilla inicial (la tabla real de la farmacia). Se siembra la 1ª vez.
DEFAULT_TRAMOS = [(14, 2500), (24, 3000), (34, 3500), (49, 4000), (9999, 4500)]
DEFAULT_ZONAS = [('refinería', 8000), ('centro', 8000), ('Kentucky', 10000),
                 ('haras', 12000), ('Roldán', 15000)]


def _norm(s):
    """minúsculas + sin tildes, para matchear zonas con tolerancia."""
    s = (s or '').strip().lower()
    return ''.join(c for c in unicodedata.normalize('NFD', s)
                   if unicodedata.category(c) != 'Mn')


def seed_si_vacio():
    """Carga la grilla inicial si no hay nada cargado (idempotente)."""
    with database.get_db() as s:
        if (s.query(database.EnvioTramo).count() == 0
                and s.query(database.EnvioZona).count() == 0):
            for i, (hasta, monto) in enumerate(DEFAULT_TRAMOS):
                s.add(database.EnvioTramo(hasta_cuadras=hasta, monto=monto, orden=i))
            for i, (nombre, monto) in enumerate(DEFAULT_ZONAS):
                s.add(database.EnvioZona(nombre=nombre, monto=monto, orden=i))
            s.commit()


def cotizar(localidad=None, cuadras=None):
    """Devuelve {monto, fuente, detalle}. monto=None → 'a convenir'.
    Prioridad: zona nombrada (pisa) → tramo por cuadras."""
    with database.get_db() as s:
        # 1) Zona nombrada (match tolerante por nombre) — manda.
        if localidad:
            n = _norm(localidad)
            zonas = (s.query(database.EnvioZona)
                     .filter(database.EnvioZona.activa.is_(True))
                     .order_by(database.EnvioZona.orden).all())
            for z in zonas:
                zn = _norm(z.nombre)
                if zn and (zn in n or n in zn):
                    return {'monto': float(z.monto or 0), 'fuente': 'zona',
                            'detalle': z.nombre}
        # 2) Tramo por cuadras.
        if cuadras is not None and str(cuadras).strip() != '':
            try:
                c = int(float(cuadras))
            except (TypeError, ValueError):
                c = None
            if c is not None and c >= 0:
                tr = (s.query(database.EnvioTramo)
                      .filter(database.EnvioTramo.hasta_cuadras >= c)
                      .order_by(database.EnvioTramo.hasta_cuadras.asc()).first())
                if tr:
                    return {'monto': float(tr.monto or 0), 'fuente': 'tramo',
                            'detalle': f'{c} cuadras'}
    return {'monto': None, 'fuente': None, 'detalle': 'a convenir'}


def listar_tarifas():
    """Para el panel: config + tramos (ordenados) + zonas. Siembra si vacío."""
    seed_si_vacio()
    cfg = get_config()
    with database.get_db() as s:
        tramos = (s.query(database.EnvioTramo)
                  .order_by(database.EnvioTramo.hasta_cuadras).all())
        zonas = (s.query(database.EnvioZona)
                 .order_by(database.EnvioZona.orden, database.EnvioZona.nombre).all())
        return {
            'config': cfg,
            'tramos': [{'id': t.id, 'hasta_cuadras': t.hasta_cuadras,
                        'monto': float(t.monto or 0)} for t in tramos],
            'zonas': [{'id': z.id, 'nombre': z.nombre, 'monto': float(z.monto or 0),
                       'activa': z.activa, 'lat': z.lat, 'lng': z.lng,
                       'radio_km': z.radio_km} for z in zonas],
        }


# ── CRUD para el panel de config ─────────────────────────────────────────────

def guardar_tramo(tramo_id, hasta_cuadras, monto):
    try:
        hasta = int(float(hasta_cuadras))
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'cuadras inválidas'}
    with database.get_db() as s:
        if tramo_id:
            t = s.get(database.EnvioTramo, tramo_id)
            if not t:
                return {'ok': False, 'error': 'no existe'}
        else:
            t = database.EnvioTramo()
            s.add(t)
        t.hasta_cuadras = hasta
        t.monto = float(monto or 0)
        s.commit()
        return {'ok': True, 'id': t.id}


def eliminar_tramo(tramo_id):
    with database.get_db() as s:
        t = s.get(database.EnvioTramo, tramo_id)
        if t:
            s.delete(t)
            s.commit()
        return {'ok': True}


def guardar_zona(zona_id, nombre, monto, lat=None, lng=None, radio_km=None):
    nombre = (nombre or '').strip()
    if not nombre:
        return {'ok': False, 'error': 'nombre vacío'}
    with database.get_db() as s:
        if zona_id:
            z = s.get(database.EnvioZona, zona_id)
            if not z:
                return {'ok': False, 'error': 'no existe'}
        else:
            z = database.EnvioZona()
            s.add(z)
        z.nombre = nombre
        z.monto = float(monto or 0)
        # Solo tocar el círculo si vino en la llamada (no pisarlo al editar nombre/monto).
        if lat is not None:
            z.lat = _f(lat)
        if lng is not None:
            z.lng = _f(lng)
        if radio_km is not None:
            z.radio_km = _f(radio_km)
        s.commit()
        return {'ok': True, 'id': z.id}


def eliminar_zona(zona_id):
    with database.get_db() as s:
        z = s.get(database.EnvioZona, zona_id)
        if z:
            s.delete(z)
            s.commit()
        return {'ok': True}


def geolocalizar_zona(zona_id, radio_km_default=2.0):
    """Geocodifica el nombre de la zona y le setea el círculo (lat/lng + radio)."""
    with database.get_db() as s:
        z = s.get(database.EnvioZona, zona_id)
        if not z:
            return {'ok': False, 'error': 'no existe'}
        nombre, monto, radio = z.nombre, float(z.monto or 0), z.radio_km
    coords = geocodificar(nombre, localidad=nombre)
    if not coords:
        return {'ok': False, 'error': 'no pude geolocalizar la zona'}
    return guardar_zona(zona_id, nombre, monto, lat=coords[0], lng=coords[1],
                        radio_km=(radio or radio_km_default))


# ── Fase 2: cálculo automático desde coordenadas / dirección ─────────────────

def _f(v):
    """str/num/'' → float o None."""
    if v is None or str(v).strip() == '':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def get_config():
    """Config de envío (fila única, get-or-create)."""
    with database.get_db() as s:
        c = s.query(database.EnvioConfig).first()
        if not c:
            c = database.EnvioConfig()
            s.add(c)
            s.commit()
        return {'farmacia_lat': c.farmacia_lat, 'farmacia_lng': c.farmacia_lng,
                'factor_cuadras': c.factor_cuadras or 1.3,
                'metros_por_cuadra': c.metros_por_cuadra or 100}


def guardar_config(farmacia_lat=None, farmacia_lng=None,
                   factor_cuadras=None, metros_por_cuadra=None):
    """Actualiza solo los campos provistos (no pisa coords al editar el factor)."""
    with database.get_db() as s:
        c = s.query(database.EnvioConfig).first()
        if not c:
            c = database.EnvioConfig()
            s.add(c)
        if _f(farmacia_lat) is not None:
            c.farmacia_lat = _f(farmacia_lat)
        if _f(farmacia_lng) is not None:
            c.farmacia_lng = _f(farmacia_lng)
        if _f(factor_cuadras):
            c.factor_cuadras = _f(factor_cuadras)
        if _f(metros_por_cuadra):
            c.metros_por_cuadra = int(_f(metros_por_cuadra))
        c.actualizado_en = database.now_ar()
        s.commit()
        return {'ok': True}


def geolocalizar_farmacia(direccion, localidad='Rosario'):
    """Geocodifica la dirección de la farmacia y guarda sus coordenadas."""
    coords = geocodificar(direccion, localidad=localidad)
    if not coords:
        return {'ok': False, 'error': 'no pude ubicar la dirección'}
    guardar_config(farmacia_lat=coords[0], farmacia_lng=coords[1])
    return {'ok': True, 'lat': coords[0], 'lng': coords[1]}


def _haversine_m(lat1, lng1, lat2, lng2):
    """Distancia en línea recta (metros) entre dos coordenadas."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def cuadras_desde_coords(lat, lng, cfg=None):
    """Estima cuadras del cadete desde la farmacia: línea recta → cuadras × factor
    de rodeo de la grilla. None si la farmacia no tiene coordenadas configuradas."""
    cfg = cfg or get_config()
    if cfg['farmacia_lat'] is None or cfg['farmacia_lng'] is None:
        return None
    m = _haversine_m(cfg['farmacia_lat'], cfg['farmacia_lng'], lat, lng)
    return int(round((m / (cfg['metros_por_cuadra'] or 100)) * (cfg['factor_cuadras'] or 1.3)))


def cotizar_por_coords(lat, lng):
    """Cotiza desde un punto (pin de ubicación). Prioridad: zona por CÍRCULO
    (lat/lng + radio_km) → tramo por cuadras estimadas."""
    lat, lng = _f(lat), _f(lng)
    if lat is None or lng is None:
        return {'monto': None, 'fuente': None, 'detalle': 'ubicación inválida'}
    with database.get_db() as s:
        zonas = (s.query(database.EnvioZona)
                 .filter(database.EnvioZona.activa.is_(True),
                         database.EnvioZona.lat.isnot(None),
                         database.EnvioZona.lng.isnot(None),
                         database.EnvioZona.radio_km.isnot(None))
                 .order_by(database.EnvioZona.orden).all())
        for z in zonas:
            if _haversine_m(z.lat, z.lng, lat, lng) / 1000.0 <= z.radio_km:
                return {'monto': float(z.monto or 0), 'fuente': 'zona',
                        'detalle': z.nombre}
    cu = cuadras_desde_coords(lat, lng)
    if cu is None:
        return {'monto': None, 'fuente': None,
                'detalle': 'falta configurar la ubicación de la farmacia'}
    r = cotizar(cuadras=cu)
    r['cuadras'] = cu
    if r['monto'] is not None:
        r['detalle'] = f'~{cu} cuadras'
    return r


def geocodificar(direccion, provincia='santa fe', localidad=None):
    """Dirección escrita → (lat, lng) vía georef-ar (gratis, AR). None si falla."""
    direccion = (direccion or '').strip()
    if not direccion:
        return None
    try:
        params = {'direccion': direccion, 'provincia': provincia, 'max': 1}
        if localidad:
            params['localidad'] = localidad
        data = requests.get(GEOREF_URL, params=params, timeout=8).json()
        ds = data.get('direcciones') or []
        if not ds:
            return None
        u = ds[0].get('ubicacion') or {}
        lat, lon = u.get('lat'), u.get('lon')
        if lat is None or lon is None:
            return None
        return (float(lat), float(lon))
    except Exception as e:  # noqa: BLE001
        print('geocodificar error:', e)
        return None


def cotizar_por_direccion(direccion, localidad=None):
    """Dirección escrita → cotización. Atajo: si la localidad/dirección matchea
    una zona nombrada, se usa esa tarifa sin geocodificar."""
    z = cotizar(localidad=localidad or direccion)
    if z['fuente'] == 'zona':
        return z
    coords = geocodificar(direccion, localidad=localidad)
    if not coords:
        return {'monto': None, 'fuente': None,
                'detalle': 'no pude ubicar la dirección'}
    return cotizar_por_coords(*coords)
