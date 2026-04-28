"""Producto routes: list, CRUD, API + análisis histórico de precios."""

from flask import jsonify, render_template, request

import database
from database import Laboratorio, Producto, ProductoPrecioHist
from helpers import _find_producto


def init_app(app):

    @app.route('/productos')
    def productos_list():
        with database.get_db() as session:
            labs = [{'id': l.id, 'nombre': l.nombre}
                    for l in session.query(Laboratorio).order_by(Laboratorio.nombre).all()]
            return render_template('productos.html', laboratorios=labs)

    @app.route('/api/productos')
    def api_productos():
        from sqlalchemy import func, or_
        from sqlalchemy.orm import joinedload

        q = (request.args.get('q') or '').strip()
        lab = (request.args.get('lab') or '').strip()
        only_alt = request.args.get('only_alt') in ('1', 'true')
        only_pack = request.args.get('only_pack') in ('1', 'true')
        venta_tipo = (request.args.get('venta_tipo') or '').strip()
        try:
            limit = min(int(request.args.get('limit') or 100), 500)
        except ValueError:
            limit = 100
        try:
            offset = max(int(request.args.get('offset') or 0), 0)
        except ValueError:
            offset = 0

        with database.get_db() as session:
            base = session.query(Producto).options(joinedload(Producto.laboratorio))
            if q:
                from helpers import multi_token_filter
                clausula = multi_token_filter(q,
                    Producto.descripcion,
                    Producto.codigo_barra,
                    Producto.codigo_barra_alt1,
                    Producto.codigo_barra_alt2,
                    Producto.codigo_barra_alt3)
                if clausula is not None:
                    base = base.filter(clausula)
            if lab == '__none__':
                base = base.filter(Producto.laboratorio_id.is_(None))
            elif lab:
                try:
                    base = base.filter(Producto.laboratorio_id == int(lab))
                except ValueError:
                    pass
            if only_alt:
                base = base.filter(or_(
                    Producto.codigo_barra_alt1.isnot(None),
                    Producto.codigo_barra_alt2.isnot(None),
                    Producto.codigo_barra_alt3.isnot(None),
                ))
            if only_pack:
                base = base.filter(Producto.es_pack == 1)
            # Filtro por tipo de venta y control (vía obs_productos.id_tipo_venta_control)
            if venta_tipo:
                from database import ObsProducto
                if venta_tipo == 'libre':
                    tvc_vals = ['L']
                elif venta_tipo == 'receta':
                    tvc_vals = ['R', 'A']
                elif venta_tipo == 'controlado':
                    tvc_vals = ['1','2','3','4','5','6','7','8']
                else:
                    tvc_vals = []
                if tvc_vals:
                    sub = (session.query(ObsProducto.observer_id)
                           .filter(ObsProducto.id_tipo_venta_control.in_(tvc_vals)).subquery())
                    base = base.filter(Producto.observer_id.in_(sub))

            total = base.count()
            prods = base.order_by(Producto.descripcion).limit(limit).offset(offset).all()

            # Resolver tvc por producto en batch (para mostrar el badge en la tabla)
            from database import ObsProducto
            obs_ids = [p.observer_id for p in prods if p.observer_id]
            tvc_map = {}
            if obs_ids:
                tvc_map = dict(session.query(ObsProducto.observer_id,
                                              ObsProducto.id_tipo_venta_control)
                               .filter(ObsProducto.observer_id.in_(obs_ids)).all())

            data = [
                {
                    'id': p.id,
                    'codigo_barra': p.codigo_barra,
                    'descripcion': p.descripcion or '',
                    'alt1': p.codigo_barra_alt1 or '',
                    'alt2': p.codigo_barra_alt2 or '',
                    'alt3': p.codigo_barra_alt3 or '',
                    'precio_pvp': float(p.precio_pvp) if p.precio_pvp else None,
                    'laboratorio_id': p.laboratorio_id or '',
                    'laboratorio_nombre': p.laboratorio.nombre if p.laboratorio else '',
                    'actualizado_en': p.actualizado_en.strftime('%d/%m/%Y') if p.actualizado_en else '',
                    'es_pack': p.es_pack or 0,
                    'tvc': tvc_map.get(p.observer_id, '') if p.observer_id else '',
                }
                for p in prods
            ]
            return jsonify({'data': data, 'total': total, 'limit': limit, 'offset': offset})

    @app.route('/producto/<int:prod_id>/laboratorio', methods=['POST'])
    def producto_set_laboratorio(prod_id):
        lab_id = request.form.get('laboratorio_id') or None
        with database.get_db() as session:
            prod = session.get(Producto, prod_id)
            if prod:
                prod.laboratorio_id = int(lab_id) if lab_id else None
                session.commit()
        return ('', 204)

    @app.route('/producto/<int:prod_id>/edit', methods=['POST'])
    def producto_edit(prod_id):
        data = request.get_json(silent=True) or {}
        field = data.get('field')
        value = (data.get('value') or '').strip()
        allowed = {'descripcion', 'codigo_barra', 'codigo_barra_alt1', 'codigo_barra_alt2', 'codigo_barra_alt3', 'precio_pvp', 'es_pack'}
        if field not in allowed:
            return {'error': 'Campo no permitido'}, 400
        with database.get_db() as session:
            prod = session.get(Producto, prod_id)
            if not prod:
                return {'error': 'No encontrado'}, 404
            if field == 'precio_pvp':
                try:
                    setattr(prod, field, float(value.replace(',', '.')) if value else None)
                except ValueError:
                    return {'error': 'Precio inválido'}, 400
            elif field == 'es_pack':
                prod.es_pack = 1 if value in ('1', 'true', 'True') else 0
            else:
                setattr(prod, field, value or None)
            from datetime import datetime as _dt
            prod.actualizado_en = _dt.now().date()
            session.commit()
            return {'ok': True}

    @app.route('/producto/edit-by-barcode', methods=['POST'])
    def producto_edit_by_barcode():
        data = request.get_json(silent=True) or {}
        cb    = (data.get('codigo_barra') or '').strip()
        field = data.get('field')
        value = (data.get('value') or '').strip()
        if not cb or field not in {'descripcion', 'precio_pvp'}:
            return {'error': 'Parámetros inválidos'}, 400
        with database.get_db() as session:
            try:
                prod = _find_producto(session, cb)
                if not prod:
                    prod = Producto(codigo_barra=cb)
                    session.add(prod)
                    session.flush()
                if field == 'precio_pvp':
                    prod.precio_pvp = float(value.replace(',', '.')) if value else None
                else:
                    setattr(prod, field, value or None)
                from datetime import datetime as _dt
                prod.actualizado_en = _dt.now().date()
                session.commit()
                return {'ok': True, 'id': prod.id}
            except Exception as e:
                session.rollback()
                return {'error': str(e)}, 500

    @app.route('/producto/create', methods=['POST'])
    def producto_create():
        data = request.get_json(silent=True) or {}
        cb = (data.get('codigo_barra') or '').strip()
        if not cb:
            return {'error': 'Código de barra requerido'}, 400
        with database.get_db() as session:
            try:
                if session.query(Producto).filter_by(codigo_barra=cb).first():
                    return {'error': 'Ya existe un producto con ese código'}, 409
                prod = Producto(
                    codigo_barra=cb,
                    descripcion=(data.get('descripcion') or '').strip() or None,
                    precio_pvp=float(data['precio_pvp']) if data.get('precio_pvp') else None,
                    es_pack=1 if data.get('es_pack') else 0,
                )
                session.add(prod)
                session.commit()
                return {'ok': True, 'id': prod.id}
            except Exception as e:
                session.rollback()
                return {'error': str(e)}, 500

    @app.route('/producto/<int:prod_id>/delete', methods=['POST'])
    def producto_delete(prod_id):
        with database.get_db() as session:
            prod = session.get(Producto, prod_id)
            if not prod:
                return {'error': 'No encontrado'}, 404
            session.delete(prod)
            session.commit()
            return {'ok': True}

    # ─── Catalogación estructurada ────────────────────────────────────────────

    @app.route('/catalogacion')
    def catalogacion_panel():
        return render_template('catalogacion.html')

    @app.route('/api/catalogacion/backfill', methods=['POST'])
    def api_catalogacion_backfill():
        """Ejecuta el backfill de atributos estructurados sobre todos los productos.

        Idempotente: solo recalcula los productos cuya descripción cambió o que
        nunca se procesaron. Devuelve métricas para mostrar en el UI.
        """
        from catalogacion import backfill_todos
        try:
            n_total, n_act, n_sin = backfill_todos()
            return jsonify({
                'ok': True,
                'total': n_total,
                'actualizados': n_act,
                'sin_datos': n_sin,
            })
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 500

    @app.route('/api/catalogacion/stats')
    def api_catalogacion_stats():
        """Resumen de cobertura del catálogo estructurado."""
        from sqlalchemy import func
        from database import ProductoAtributo
        with database.get_db() as session:
            total_prods = session.query(func.count(Producto.id)).scalar()
            total_atrib = session.query(func.count(ProductoAtributo.producto_id)).scalar()
            por_fuente = dict(session.query(ProductoAtributo.fuente, func.count())
                              .group_by(ProductoAtributo.fuente).all())
            con_droga = session.query(func.count(ProductoAtributo.producto_id)) \
                               .filter(ProductoAtributo.monodroga_norm.isnot(None)).scalar()
            con_conc  = session.query(func.count(ProductoAtributo.producto_id)) \
                               .filter(ProductoAtributo.concentracion_mg.isnot(None)).scalar()
            con_forma = session.query(func.count(ProductoAtributo.producto_id)) \
                               .filter(ProductoAtributo.forma_farma.isnot(None)).scalar()
            con_cant  = session.query(func.count(ProductoAtributo.producto_id)) \
                               .filter(ProductoAtributo.cantidad_envase.isnot(None)).scalar()
            return jsonify({
                'total_productos': total_prods,
                'con_atributos': total_atrib,
                'cobertura_pct': round(total_atrib * 100.0 / total_prods, 1) if total_prods else 0,
                'por_fuente': por_fuente,
                'completitud': {
                    'monodroga': con_droga,
                    'concentracion': con_conc,
                    'forma_farma': con_forma,
                    'cantidad_envase': con_cant,
                },
            })

    @app.route('/api/producto/<int:prod_id>/atributos', methods=['GET', 'POST'])
    def api_producto_atributos(prod_id):
        """GET devuelve los atributos. POST permite editar manualmente (fuente='manual')."""
        from database import ProductoAtributo
        from catalogacion import _normalizar_droga
        with database.get_db() as session:
            prod = session.get(Producto, prod_id)
            if not prod:
                return jsonify({'error': 'Producto no encontrado'}), 404
            atr = session.get(ProductoAtributo, prod_id)
            if request.method == 'GET':
                if not atr:
                    return jsonify({'producto_id': prod_id, 'atributos': None})
                return jsonify({
                    'producto_id': prod_id,
                    'descripcion': prod.descripcion,
                    'atributos': {
                        'monodroga': atr.monodroga_display,
                        'concentracion_mg': float(atr.concentracion_mg) if atr.concentracion_mg else None,
                        'concentracion_unidad': atr.concentracion_unidad,
                        'forma_farma': atr.forma_farma,
                        'cantidad_envase': float(atr.cantidad_envase) if atr.cantidad_envase else None,
                        'via_admin': atr.via_admin,
                        'fuente': atr.fuente,
                        'confianza': atr.confianza,
                        'extraido_en': atr.extraido_en.strftime('%Y-%m-%d %H:%M') if atr.extraido_en else None,
                    },
                })
            # POST: editar manualmente
            data = request.get_json(silent=True) or {}
            if not atr:
                atr = ProductoAtributo(producto_id=prod_id)
                session.add(atr)
            for f in ['concentracion_unidad', 'forma_farma', 'via_admin']:
                if f in data:
                    setattr(atr, f, (data.get(f) or None) and str(data[f]).strip().upper())
            for f in ['concentracion_mg', 'cantidad_envase']:
                if f in data:
                    val = data.get(f)
                    if val in (None, ''):
                        setattr(atr, f, None)
                    else:
                        try:
                            setattr(atr, f, float(str(val).replace(',', '.')))
                        except ValueError:
                            return jsonify({'error': f'{f} inválido'}), 400
            if 'monodroga' in data:
                m = (data.get('monodroga') or '').strip()
                atr.monodroga_display = m or None
                atr.monodroga_norm = _normalizar_droga(m) if m else None
            atr.fuente = 'manual'
            atr.confianza = 'ALTA'
            atr.extraido_en = database.now_ar()
            session.commit()
            return jsonify({'ok': True})

    # ─── Análisis histórico de precios ───────────────────────────────────────

    @app.route('/precios/<ean>')
    def precios_historico(ean):
        """Pantalla de análisis histórico de precio para un EAN."""
        ean = (ean or '').strip()
        with database.get_db() as session:
            prod = _find_producto(session, ean)
            producto_info = None
            if prod:
                producto_info = {
                    'id': prod.id,
                    'codigo_barra': prod.codigo_barra,
                    'descripcion': prod.descripcion or '',
                    'precio_pvp': float(prod.precio_pvp) if prod.precio_pvp else None,
                    'laboratorio': prod.laboratorio.nombre if prod.laboratorio else '',
                }
        return render_template('precios_historico.html', ean=ean, producto=producto_info)

    @app.route('/api/producto-resolver')
    def api_producto_resolver():
        """Resuelve un EAN a partir del nombre del producto.

        Útil cuando un PedidoItem se creó sin código (ej. compras rápidas
        antiguas) y queremos disparar acciones que requieren EAN
        (comparar droguerías, gráfico histórico, etc.).

        Retorna: {ok, ean, descripcion, source} donde source = 'producto'
        si match por catálogo local, 'obs' si match por catálogo Observer.
        """
        from sqlalchemy import or_
        nombre = (request.args.get('nombre') or '').strip()
        if not nombre or len(nombre) < 3:
            return jsonify({'ok': False, 'error': 'Nombre vacío o muy corto'}), 400

        with database.get_db() as session:
            # 1. Match exacto en Producto local
            prod = (session.query(database.Producto)
                    .filter(database.Producto.descripcion.ilike(nombre)).first())
            if prod and prod.codigo_barra:
                return jsonify({
                    'ok': True, 'ean': prod.codigo_barra,
                    'descripcion': prod.descripcion, 'source': 'producto_exacto',
                })
            # 2. Match contains en Producto local
            prod = (session.query(database.Producto)
                    .filter(database.Producto.descripcion.ilike(f'%{nombre}%')).first())
            if prod and prod.codigo_barra:
                return jsonify({
                    'ok': True, 'ean': prod.codigo_barra,
                    'descripcion': prod.descripcion, 'source': 'producto_contains',
                })
            # 3. Match en ObsProducto + EAN principal de obs_codigos_barras
            obs = (session.query(database.ObsProducto)
                   .filter(or_(database.ObsProducto.descripcion.ilike(nombre),
                               database.ObsProducto.descripcion.ilike(f'%{nombre}%')))
                   .filter(database.ObsProducto.fecha_baja.is_(None))
                   .order_by(database.ObsProducto.descripcion).first())
            if obs:
                cb_row = (session.query(database.ObsCodigoBarras.codigo_barra)
                          .filter(database.ObsCodigoBarras.producto_observer == obs.observer_id)
                          .order_by(database.ObsCodigoBarras.orden).first())
                if cb_row:
                    return jsonify({
                        'ok': True, 'ean': cb_row[0],
                        'descripcion': obs.descripcion, 'source': 'obs',
                    })
                # Como último recurso, pseudo-EAN
                return jsonify({
                    'ok': True, 'ean': f'OBS:{obs.observer_id}',
                    'descripcion': obs.descripcion, 'source': 'obs_pseudo',
                })

        return jsonify({'ok': False, 'error': 'Producto no encontrado por nombre'}), 404

    @app.route('/api/precios/<ean>')
    def api_precios_historico(ean):
        """Devuelve la serie de precios históricos de un EAN agrupada por proveedor.
        Incluye EANs alternativos si el producto los tiene mapeados."""
        from sqlalchemy import or_
        ean = (ean or '').strip()
        if not ean:
            return jsonify({'ok': False, 'error': 'EAN vacío'}), 400

        with database.get_db() as session:
            # Colectar todos los EANs equivalentes (principal + alts) del producto.
            eans = {ean}
            prod = _find_producto(session, ean)
            if prod:
                for alt in (prod.codigo_barra, prod.codigo_barra_alt1, prod.codigo_barra_alt2, prod.codigo_barra_alt3):
                    if alt: eans.add(alt)

            rows = (session.query(ProductoPrecioHist)
                    .filter(ProductoPrecioHist.codigo_barra.in_(list(eans)))
                    .order_by(ProductoPrecioHist.fecha.asc(), ProductoPrecioHist.id.asc())
                    .all())

            # Agrupar por proveedor_razon (o proveedor_id si existe)
            series = {}
            detalle = []
            for r in rows:
                key = r.proveedor_razon or (f'Proveedor #{r.proveedor_id}' if r.proveedor_id else 'Sin proveedor')
                pu = float(r.precio_unitario) if r.precio_unitario is not None else None
                fecha_str = r.fecha.strftime('%Y-%m-%d') if r.fecha else None
                if key not in series:
                    series[key] = []
                if pu is not None and fecha_str:
                    series[key].append({'x': fecha_str, 'y': pu})
                detalle.append({
                    'id': r.id,
                    'fecha': fecha_str,
                    'proveedor': key,
                    'codigo_barra': r.codigo_barra,
                    'precio_publico': float(r.precio_publico) if r.precio_publico is not None else None,
                    'dto_pct': float(r.dto_pct) if r.dto_pct is not None else None,
                    'precio_unitario': pu,
                    'importe': float(r.importe) if r.importe is not None else None,
                    'factura_id': r.factura_id,
                    'tipo_comprobante': r.tipo_comprobante,
                })

            # Resumen por proveedor: último precio, mínimo, máximo, variación
            resumen = []
            for prov, pts in series.items():
                if not pts: continue
                precios = [p['y'] for p in pts]
                ultimo = pts[-1]
                primero = pts[0]
                variacion = None
                if primero['y'] and ultimo['y']:
                    variacion = round((ultimo['y'] - primero['y']) / primero['y'] * 100, 2)
                resumen.append({
                    'proveedor': prov,
                    'n_puntos': len(pts),
                    'primer_fecha': primero['x'],
                    'primer_precio': primero['y'],
                    'ultimo_fecha': ultimo['x'],
                    'ultimo_precio': ultimo['y'],
                    'min': min(precios),
                    'max': max(precios),
                    'variacion_pct': variacion,
                })
            resumen.sort(key=lambda r: r['ultimo_precio'])

            # Diagnóstico para mejor mensaje en el frontend cuando no hay datos.
            total_precios = session.query(ProductoPrecioHist.id).limit(1).first()
            tabla_vacia = total_precios is None
            es_pseudo_ean = ean.startswith('OBS:') or (ean.isdigit() and len(ean) <= 7)

            return jsonify({
                'ok': True,
                'ean': ean,
                'eans_equivalentes': sorted(eans),
                'producto': {
                    'codigo_barra': prod.codigo_barra,
                    'descripcion': prod.descripcion or '',
                    'precio_pvp': float(prod.precio_pvp) if prod and prod.precio_pvp else None,
                    'laboratorio': prod.laboratorio.nombre if prod and prod.laboratorio else '',
                } if prod else None,
                'series': series,
                'resumen': resumen,
                'detalle': detalle,
                'tabla_vacia': tabla_vacia,
                'es_pseudo_ean': es_pseudo_ean,
            })
