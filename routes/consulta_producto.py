"""Consulta de un medicamento por EAN/troquel.

Pantalla mobile-first para escanear (cámara o pistola) un troquel y ver
nombre, lab, stock, ventas, gráfico histórico. NO requiere elegir lab/drog
previo — el EAN resuelve directo contra el catálogo local + bridge ObServer.

Endpoints:
  GET /consulta-producto                       → form de entrada (input + cámara + búsqueda descripción)
  POST /consulta-producto/buscar               → recibe EAN, resuelve, redirect al detail
  GET /consulta-producto/<ean>                 → renderiza resultado con KPIs + chart
  GET /api/consulta-producto/buscar-desc?q=…   → autocomplete por descripción (multi-token AND)
"""
from flask import flash, jsonify, redirect, render_template, request, url_for

import database
from helpers import _find_producto


def init_app(app):

    @app.route('/consulta-producto')
    def consulta_producto():
        """Pantalla entrada: input grande + botón cámara para escanear EAN."""
        return render_template('consulta_producto.html')

    @app.route('/consulta-producto/buscar', methods=['POST'])
    def consulta_producto_buscar():
        """Recibe el EAN del form (input manual o cámara), resuelve contra
        catálogo local + bridge ObServer, redirige al detalle si encuentra.
        Si no encuentra, vuelve al form con flash.
        """
        ean = (request.form.get('ean') or '').strip()
        if not ean:
            flash('Ingresá un código de barras.', 'error')
            return redirect(url_for('consulta_producto'))
        # Validación mínima: solo dígitos, 8-14 chars (EAN-13 es lo común;
        # EAN-8 / GTIN-14 también aparecen).
        if not ean.isdigit() or len(ean) < 8 or len(ean) > 14:
            flash(f'Código inválido: "{ean}". Debe ser 8-14 dígitos.', 'error')
            return redirect(url_for('consulta_producto'))
        return redirect(url_for('consulta_producto_detalle', ean=ean))

    @app.route('/consulta-producto/<ean>')
    def consulta_producto_detalle(ean):
        """Resultado: resuelve EAN → producto + observer_id, pasa contexto al
        template. El chart histórico se llena vía fetch a /api/product/<ean>/chart
        (mismo endpoint que usan otras pantallas — single source of truth).
        """
        ean = (ean or '').strip()
        info = {'ean': ean, 'encontrado': False}
        with database.get_db() as session:
            prod = _find_producto(session, ean)
            if prod:
                # Producto local encontrado.
                info.update({
                    'encontrado': True,
                    'producto_id': prod.id,
                    'observer_id': prod.observer_id,
                    'descripcion': prod.descripcion or '',
                    'codigo_barra': prod.codigo_barra,
                    'precio_pvp': float(prod.precio_pvp) if prod.precio_pvp else None,
                    'monodroga': prod.monodroga or '',
                    'presentacion': prod.presentacion or '',
                    'laboratorio_id': prod.laboratorio_id,
                    'no_pedir': bool(prod.no_pedir),
                })
                if prod.laboratorio_id:
                    lab = session.get(database.Laboratorio, prod.laboratorio_id)
                    info['laboratorio'] = lab.nombre if lab else ''
                else:
                    info['laboratorio'] = ''
            else:
                # Fallback Observer: el producto puede estar en obs_productos
                # sin bridge a productos local. Resolvemos por EAN en
                # obs_codigos_barras → obs_productos. La info es read-only y
                # /api/product/<ean>/chart maneja igual el caso.
                obs_id = None
                if ean.isdigit():
                    try: obs_id = int(ean)
                    except (ValueError, TypeError): pass
                if obs_id is not None and not session.get(database.ObsProducto, obs_id):
                    obs_id = None
                if obs_id is None:
                    row = (session.query(database.ObsCodigoBarras.producto_observer)
                           .filter(database.ObsCodigoBarras.codigo_barras == ean,
                                   database.ObsCodigoBarras.fecha_baja.is_(None))
                           .first())
                    if row:
                        obs_id = row[0]
                if obs_id:
                    op = session.get(database.ObsProducto, obs_id)
                    if op:
                        info.update({
                            'encontrado': True,
                            'fuente_observer_only': True,
                            'observer_id': op.observer_id,
                            'descripcion': op.descripcion or '',
                            'codigo_barra': ean,
                            'precio_pvp': None,
                            'monodroga': '',
                            'presentacion': '',
                            'no_pedir': False,
                        })
                        # Nombre del lab si está en obs_laboratorios.
                        info['laboratorio'] = ''
                        if op.laboratorio_observer:
                            ol = session.get(database.ObsLaboratorio, op.laboratorio_observer)
                            if ol: info['laboratorio'] = ol.descripcion or ''
        return render_template('consulta_producto_resultado.html', info=info)

    @app.route('/api/consulta-producto/buscar-desc')
    def api_consulta_producto_buscar_desc():
        """Autocomplete por descripción tokenizada.

        Query: ?q=amox 500
        - Cada token (separado por espacio) debe matchear con ILIKE en
          Producto.descripcion (AND lógico, orden libre — convención del proyecto).
        - Mínimo 2 chars totales para evitar resultados gigantes.
        - Devuelve top 20 con EAN, descripcion, laboratorio.
        - Filtra productos sin EAN (no se pueden consultar después).
        - Si no hay matches locales, hace fallback a ObsProducto.
        """
        q = (request.args.get('q') or '').strip()
        if len(q) < 2:
            return jsonify({'items': []})
        tokens = [t for t in q.split() if t]
        if not tokens:
            return jsonify({'items': []})

        with database.get_db() as session:
            # 1. Productos locales (tienen EAN y bridge a Observer).
            base = (session.query(database.Producto)
                    .filter(database.Producto.codigo_barra.isnot(None),
                            database.Producto.codigo_barra != ''))
            for t in tokens:
                base = base.filter(database.Producto.descripcion.ilike(f'%{t}%'))
            results = base.order_by(database.Producto.descripcion).limit(20).all()

            # Pre-cargar nombres de lab en una sola query.
            lab_ids = {p.laboratorio_id for p in results if p.laboratorio_id}
            lab_map = {}
            if lab_ids:
                for lab in (session.query(database.Laboratorio)
                            .filter(database.Laboratorio.id.in_(lab_ids)).all()):
                    lab_map[lab.id] = lab.nombre

            items = [{
                'ean':         p.codigo_barra,
                'descripcion': p.descripcion or '',
                'laboratorio': lab_map.get(p.laboratorio_id, '') if p.laboratorio_id else '',
                'fuente':      'local',
            } for p in results]

            # 2. Fallback a ObsProducto si los locales son pocos.
            # Usamos como "EAN" el observer_id (la ruta /consulta-producto/<ean>
            # lo resuelve via int().
            if len(items) < 10:
                eans_locales = {it['ean'] for it in items}
                obs_q = (session.query(database.ObsProducto)
                         .filter(database.ObsProducto.fecha_baja.is_(None)))
                for t in tokens:
                    obs_q = obs_q.filter(database.ObsProducto.descripcion.ilike(f'%{t}%'))
                obs_results = obs_q.order_by(database.ObsProducto.descripcion).limit(20).all()

                # Pre-cargar nombres de lab observer.
                lab_obs_ids = {o.laboratorio_observer for o in obs_results
                               if o.laboratorio_observer}
                lab_obs_map = {}
                if lab_obs_ids:
                    for ol in (session.query(database.ObsLaboratorio)
                               .filter(database.ObsLaboratorio.observer_id.in_(lab_obs_ids)).all()):
                        lab_obs_map[ol.observer_id] = ol.descripcion or ''

                # Resolver EAN via obs_codigos_barras para los que tengan.
                obs_ids_buscar = [o.observer_id for o in obs_results]
                obs_to_ean = {}
                if obs_ids_buscar:
                    for row in (session.query(database.ObsCodigoBarras.producto_observer,
                                              database.ObsCodigoBarras.codigo_barras)
                                .filter(database.ObsCodigoBarras.producto_observer.in_(obs_ids_buscar),
                                        database.ObsCodigoBarras.fecha_baja.is_(None),
                                        database.ObsCodigoBarras.orden == 1).all()):
                        if row[1] and row[1].strip():
                            obs_to_ean[row[0]] = row[1].strip()

                for o in obs_results:
                    ean = obs_to_ean.get(o.observer_id)
                    if not ean:
                        # Sin EAN físico: usamos observer_id como pseudo-EAN.
                        # La ruta detalle lo resuelve via int(ean).
                        ean = str(o.observer_id)
                    if ean in eans_locales:
                        continue  # ya estaba en locales
                    items.append({
                        'ean':         ean,
                        'descripcion': o.descripcion or '',
                        'laboratorio': lab_obs_map.get(o.laboratorio_observer, ''),
                        'fuente':      'observer',
                    })
                    if len(items) >= 30:
                        break

        return jsonify({'items': items[:30]})
