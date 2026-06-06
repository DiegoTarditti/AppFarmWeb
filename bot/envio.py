"""Cotización de envío de pedidos al cliente.

Modelo híbrido (Fase 1):
- Una zona NOMBRADA (refinería, centro, Roldán…) tiene tarifa fija y PISA al
  cálculo por distancia.
- Si no matchea ninguna zona, se cobra por TRAMO de cuadras.

Las cuadras las provee el operador (Fase 1) o el bot desde la ubicación
(Fase 2). lat/lng/radio_km de las zonas quedan NULL hasta Fase 2 (pin → círculo).
"""
import unicodedata

import database

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
    """Para el panel: tramos (ordenados por distancia) + zonas. Siembra si vacío."""
    seed_si_vacio()
    with database.get_db() as s:
        tramos = (s.query(database.EnvioTramo)
                  .order_by(database.EnvioTramo.hasta_cuadras).all())
        zonas = (s.query(database.EnvioZona)
                 .order_by(database.EnvioZona.orden, database.EnvioZona.nombre).all())
        return {
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
        z.lat, z.lng, z.radio_km = lat, lng, radio_km
        s.commit()
        return {'ok': True, 'id': z.id}


def eliminar_zona(zona_id):
    with database.get_db() as s:
        z = s.get(database.EnvioZona, zona_id)
        if z:
            s.delete(z)
            s.commit()
        return {'ok': True}
