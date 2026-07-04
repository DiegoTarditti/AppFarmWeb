"""Tienda pública — rutas sin login para el catálogo online.

Diego 2026-06-24. Sprint 2 del proyecto tienda:
  - GET  /tienda                 — vitrina + filtros + carrito (drawer JS)
  - GET  /tienda/producto/<oid>  — detalle
  - GET  /tienda/pedir           — arma mensaje WhatsApp con carrito y redirect
  - GET  /uploads/tienda/<file>  — sirve imágenes (declarada en tienda_admin.py
                                    pero el endpoint queda público via whitelist)

Reglas de qué se muestra:
  1. Producto publicado (rubro/subrubro en web_rubros_publicados con activo=True).
  2. Con stock > 0 en la farmacia activa (OBSERVER_ID_FARMACIA env).
  3. Venta libre (id_tipo_venta_control = 'L').
  4. No dado de baja.
  5. Habilitado para venta.

Cache: la lista se cachea 5 min en memoria — la tienda pública no puede pegarle
a Postgres en cada page load con miles de visitantes.
"""

import os
import time
import urllib.parse

from flask import (
    abort,
    make_response,
    redirect,
    render_template,
    request,
)
from sqlalchemy import and_, or_

import database

# Cache en memoria del listado. TTL corto para que si el operador cambia rubros
# o imágenes se refleje rápido, pero suficiente para amortizar cargas concurrentes.
_CACHE = {'ts': 0, 'productos': None, 'rubros': None}
_CACHE_TTL_SEG = 300


def _get_config(session):
    return session.get(database.Config, 1)


def _farmacia_id():
    return int(os.environ.get('OBSERVER_ID_FARMACIA', '10525'))


def _cargar_productos(session):
    """Devuelve (lista_productos, lista_rubros) desde la DB. Aplica filtros
    de publicación (web_rubros_publicados), venta libre, stock > 0, no baja."""
    fid = _farmacia_id()
    pubs = session.query(database.WebRubroPublicado).filter_by(activo=True).all()
    if not pubs:
        return [], []

    rubros_full = {p.rubro_observer_id for p in pubs if p.subrubro_observer_id is None}
    subrubros_ok = {p.subrubro_observer_id for p in pubs if p.subrubro_observer_id is not None}

    # Query base: productos venta libre + habilitados + no de baja.
    P = database.ObsProducto
    Sub = database.ObsSubrubro
    Stock = database.ObsStock

    q = (session.query(
            P.observer_id, P.descripcion, P.precio_lista,
            P.cantidad_envase, P.es_fraccionable,
            Sub.observer_id.label('subrubro_id'),
            Sub.rubro_observer.label('rubro_id'),
            Stock.stock_actual,
         )
         .join(Sub, Sub.observer_id == P.subrubro_observer)
         .join(Stock, and_(
             Stock.producto_observer == P.observer_id,
             Stock.id_farmacia == fid,
         ))
         .filter(P.fecha_baja.is_(None))
         .filter(P.es_habilitado_venta.is_(True))
         .filter(P.id_tipo_venta_control == 'L')
         .filter(Stock.stock_actual > 0))

    # Filtro publicación: (rubro completo) OR (subrubro específico)
    filtro_pub = []
    if rubros_full:
        filtro_pub.append(Sub.rubro_observer.in_(rubros_full))
    if subrubros_ok:
        filtro_pub.append(Sub.observer_id.in_(subrubros_ok))
    if not filtro_pub:
        return [], []
    q = q.filter(or_(*filtro_pub))

    # Cargar imágenes en un dict aparte (más simple que outer join)
    imgs = {i.observer_id: i.ruta_archivo
            for i in session.query(database.WebProductoImagen).all()}

    productos = []
    for row in q.order_by(P.descripcion).all():
        productos.append({
            'oid': row.observer_id,
            'nombre': row.descripcion,
            'precio': float(row.precio_lista) if row.precio_lista is not None else None,
            'stock': int(row.stock_actual or 0),
            'imagen_ruta': imgs.get(row.observer_id),  # None si no tiene foto
            'subrubro_id': row.subrubro_id,
            'rubro_id': row.rubro_id,
            'cantidad_envase': float(row.cantidad_envase) if row.cantidad_envase else None,
            'fraccionable': bool(row.es_fraccionable),
        })

    # Lista de rubros para el filtro (solo los que tienen productos publicados)
    rubro_ids = {p['rubro_id'] for p in productos}
    rubros_activos = (session.query(database.ObsRubro)
                      .filter(database.ObsRubro.observer_id.in_(rubro_ids))
                      .order_by(database.ObsRubro.descripcion).all()) if rubro_ids else []
    rubros_list = [{'id': r.observer_id, 'nombre': r.descripcion} for r in rubros_activos]

    return productos, rubros_list


def productos_para_web(session, force=False):
    """Interfaz cacheada. TTL 5 min. `force=True` bypassa el cache."""
    ahora = time.time()
    if not force and _CACHE['productos'] is not None and (ahora - _CACHE['ts']) < _CACHE_TTL_SEG:
        return _CACHE['productos'], _CACHE['rubros']
    productos, rubros = _cargar_productos(session)
    _CACHE['productos'] = productos
    _CACHE['rubros'] = rubros
    _CACHE['ts'] = ahora
    return productos, rubros


def _check_tienda_activa(session):
    """Kill switch: si tienda_activa=False → 404 en todas las rutas públicas."""
    cfg = _get_config(session)
    if not cfg or not cfg.tienda_activa:
        abort(404)
    return cfg


def _wa_url(cfg, texto):
    """Construye una URL wa.me con el texto pre-armado. Fallback a # si no
    hay número configurado (mejor que romper la landing con 500)."""
    if not cfg or not cfg.tienda_whatsapp_numero:
        return '#'
    return f"https://wa.me/{cfg.tienda_whatsapp_numero}?text={urllib.parse.quote(texto)}"


def init_app(app):

    @app.route('/tienda')
    def tienda_home():
        """Landing / home de la tienda. Hero + categorías + destacados +
        franjas de confianza + CTA a WhatsApp. Diego 2026-06-24."""
        with database.get_db() as s:
            cfg = _check_tienda_activa(s)
            productos, rubros = productos_para_web(s)
            # Destacados: los que tienen imagen + flag destacado=True (max 3).
            # Los seleccionamos del listado ya filtrado por publicación y stock.
            oids_destacados = {i.observer_id for i in
                               s.query(database.WebProductoImagen)
                                .filter_by(destacado=True).all()}
            destacados = [p for p in productos if p['oid'] in oids_destacados][:3]
            return render_template(
                'tienda_landing.html',
                cfg=cfg,
                rubros=rubros[:4],   # max 4 categorías en la landing
                destacados=destacados,
                wa_pedido=_wa_url(cfg, 'Hola! Quiero hacer un pedido.'),
                wa_general=_wa_url(cfg, 'Hola! Quiero hacer una consulta.'),
                wa_receta=_wa_url(cfg, 'Hola! Te mando una receta para consultar precio y stock.'),
            )

    @app.route('/tienda/catalogo')
    def tienda_catalogo():
        """Catálogo con filtros + carrito. Diego 2026-06-24."""
        with database.get_db() as s:
            cfg = _check_tienda_activa(s)
            productos, rubros = productos_para_web(s)

            q = (request.args.get('q') or '').strip()
            rubro_sel = request.args.get('rubro', type=int)

            filtrados = productos
            if rubro_sel:
                filtrados = [p for p in filtrados if p['rubro_id'] == rubro_sel]
            if q:
                tokens = q.lower().split()
                filtrados = [p for p in filtrados
                             if all(t in p['nombre'].lower() for t in tokens)]

            return render_template(
                'tienda_catalogo.html',
                cfg=cfg, productos=filtrados, rubros=rubros,
                q=q, rubro_sel=rubro_sel,
                total_sin_filtro=len(productos),
            )

    @app.route('/tienda/producto/<int:oid>')
    def tienda_producto(oid):
        with database.get_db() as s:
            cfg = _check_tienda_activa(s)
            productos, _ = productos_para_web(s)
            producto = next((p for p in productos if p['oid'] == oid), None)
            if producto is None:
                abort(404)
            # Similares del mismo subrubro (max 4)
            similares = [p for p in productos
                         if p['subrubro_id'] == producto['subrubro_id']
                         and p['oid'] != oid][:4]
            return render_template(
                'tienda_producto.html',
                cfg=cfg, producto=producto, similares=similares,
            )

    @app.route('/tienda/pedir')
    def tienda_pedir():
        """Arma el mensaje de WhatsApp con el pedido y redirige a wa.me.
        Los items vienen como query string: items=oid1:cant1,oid2:cant2,..."""
        with database.get_db() as s:
            cfg = _check_tienda_activa(s)
            if not cfg.tienda_whatsapp_numero:
                abort(500, 'Número de WhatsApp no configurado en /admin/tienda/config')
            items_raw = (request.args.get('items') or '').strip()
            if not items_raw:
                return redirect('/tienda')
            productos, _ = productos_para_web(s)
            prod_por_oid = {p['oid']: p for p in productos}

            lineas = []
            total = 0.0
            for chunk in items_raw.split(','):
                if ':' not in chunk:
                    continue
                oid_str, cant_str = chunk.split(':', 1)
                try:
                    oid = int(oid_str)
                    cant = int(cant_str)
                except ValueError:
                    continue
                p = prod_por_oid.get(oid)
                if p is None or cant <= 0:
                    continue
                subtotal = (p['precio'] or 0) * cant
                total += subtotal
                cant_txt = f' x{cant}' if cant > 1 else ''
                precio_txt = (f' — ${subtotal:,.0f}'.replace(',', '.')
                              if p['precio'] else '')
                lineas.append(f"• {p['nombre']}{cant_txt}{precio_txt}")

            if not lineas:
                return redirect('/tienda')

            mensaje = 'Hola! Quisiera pedir:\n' + '\n'.join(lineas)
            if total > 0:
                mensaje += f"\n\nTotal aprox: ${total:,.0f}".replace(',', '.')
            mensaje += "\n\nDomicilio: [completar]\nForma de pago: [completar]"

            url = (f"https://wa.me/{cfg.tienda_whatsapp_numero}?"
                   f"text={urllib.parse.quote(mensaje)}")
            return redirect(url)
