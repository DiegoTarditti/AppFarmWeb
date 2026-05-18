"""Producto routes: list, CRUD, API + análisis histórico de precios."""

from flask import jsonify, redirect, render_template, request, url_for
from flask_login import login_required

import database
from database import Laboratorio, Producto, ProductoPrecioHist
from helpers import _find_producto


def init_app(app):

    @app.route('/productos/verificar-nuevos')
    @login_required
    def productos_verificar_nuevos():
        """Pantalla de deduplicación: productos creados desde importación de ofertas
        cruzados contra el catálogo completo para detectar duplicados."""
        import producto_matcher as pm
        with database.get_db() as session:
            nuevos = (session.query(Producto)
                      .filter(Producto.fuente_creacion == 'oferta_import')
                      .order_by(Producto.descripcion)
                      .all())
            if not nuevos:
                return render_template('productos_verificar.html',
                                       filas=[], total=0)

            items = [{'idx': p.id,
                      'descripcion': p.descripcion or '',
                      'ean': p.codigo_barra,
                      'codigo': p.codigo_barra}
                     for p in nuevos]

            bulk = pm.buscar_candidatos_bulk(
                items, top=5, threshold_min=0.55, session=session,
            )

            filas = []
            for p in nuevos:
                cands_raw = bulk.get(p.id, [])
                # Excluir el producto mismo de sus propios candidatos
                cands = [c for c in cands_raw if c.get('producto_id') != p.id]
                filas.append({
                    'id': p.id,
                    'ean': p.codigo_barra,
                    'descripcion': p.descripcion or '',
                    'laboratorio': p.laboratorio.nombre if p.laboratorio else '—',
                    'candidatos': cands,
                })

        return render_template('productos_verificar.html',
                               filas=filas, total=len(filas))

    @app.route('/producto/<int:prod_id>/fusionar/<int:target_id>', methods=['POST'])
    @login_required
    def producto_fusionar(prod_id, target_id):
        """Fusiona prod_id (duplicado) dentro de target_id (el producto real).

        Pasos:
        1. Agrega el EAN del duplicado como alt barcode del target.
        2. Actualiza OfertaMinimo con ean=duplicado → ean=target.
        3. Elimina el producto duplicado.
        """
        from helpers import _add_alt_barcode
        with database.get_db() as session:
            dup = session.get(Producto, prod_id)
            target = session.get(Producto, target_id)
            if not dup or not target:
                return jsonify({'error': 'Producto no encontrado'}), 404

            dup_ean = dup.codigo_barra
            target_ean = target.codigo_barra

            # 1. EAN del duplicado como alt del target
            if dup_ean and dup_ean != target_ean:
                _add_alt_barcode(session, target_ean, dup_ean)

            # 2. OfertaMinimo: redirigir EAN del dup → target
            from database import OfertaMinimo
            for oferta in session.query(OfertaMinimo).filter_by(ean=dup_ean).all():
                oferta.ean = target_ean

            # 3. Eliminar duplicado
            session.delete(dup)
            session.commit()

        return jsonify({'ok': True, 'fusionado': prod_id, 'en': target_id})

    @app.route('/producto/<int:prod_id>/marcar-verificado', methods=['POST'])
    @login_required
    def producto_marcar_verificado(prod_id):
        """Limpia la marca fuente_creacion — el producto es válido, no es duplicado."""
        with database.get_db() as session:
            prod = session.get(Producto, prod_id)
            if not prod:
                return jsonify({'error': 'No encontrado'}), 404
            prod.fuente_creacion = None
            session.commit()
        return jsonify({'ok': True})

    @app.route('/productos')
    def productos_list():
        from database import ObsLaboratorio, ObsRubro
        with database.get_db() as session:
            # Labs del catalogo grande (ObServer): observer_id como valor del
            # dropdown — alineado con el filtro de /api/productos.
            labs = [{'id': l.observer_id, 'nombre': l.descripcion}
                    for l in session.query(ObsLaboratorio)
                                     .filter(ObsLaboratorio.fecha_baja.is_(None))
                                     .order_by(ObsLaboratorio.descripcion).all()]
            rubros = [{'id': r.observer_id, 'nombre': r.descripcion}
                      for r in session.query(ObsRubro)
                                       .order_by(ObsRubro.descripcion).all()]
            return render_template('productos.html',
                                    laboratorios=labs, rubros=rubros)

    @app.route('/api/productos')
    def api_productos():
        """Lista el catalogo completo: ObsProducto LEFT JOIN Producto.

        Base = obs_productos (espejo ObServer, ~122k filas activas). Cuando
        existe master local lo overlay: precio_pvp, alts manuales, es_pack,
        atributos. Cuando no existe, el row es read-only en la UI (boton
        'Crear local' lo materializa).
        """
        from collections import defaultdict

        from sqlalchemy import func, or_

        from database import (
            ErpStock,
            ObsCodigoBarras,
            ObsLaboratorio,
            ObsProducto,
            ObsStock,
            ObsSubrubro,
            ProductoAtributo,
            ProductoCodigoBarra,
        )

        q = (request.args.get('q') or '').strip()
        lab = (request.args.get('lab') or '').strip()
        rubro = (request.args.get('rubro') or '').strip()
        only_alt = request.args.get('only_alt') in ('1', 'true')
        only_pack = request.args.get('only_pack') in ('1', 'true')
        # con_ean=1: solo productos con al menos un EAN (en obs_codigos_barras
        # o en master local). Usado por el autocomplete de /productos/flags —
        # sin EAN no se puede asignar flag, no tiene sentido mostrarlos.
        con_ean = request.args.get('con_ean') in ('1', 'true')
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
            base = (session.query(ObsProducto, Producto)
                    .outerjoin(Producto,
                               Producto.observer_id == ObsProducto.observer_id)
                    .filter(ObsProducto.fecha_baja.is_(None)))

            if q:
                from helpers import multi_token_filter
                clausula = multi_token_filter(q, ObsProducto.descripcion)
                if clausula is not None:
                    primer_token = q.split()[0] if q.split() else ''
                    if primer_token:
                        # Match adicional por EAN: en obs_codigos_barras o en
                        # producto_codigos_barra (alts manuales del master).
                        sub_obs = (session.query(ObsCodigoBarras.producto_observer)
                                   .filter(ObsCodigoBarras.codigo_barras.ilike(f'%{primer_token}%'))
                                   .filter(ObsCodigoBarras.fecha_baja.is_(None))
                                   .subquery())
                        sub_loc_obs = (session.query(Producto.observer_id)
                                       .join(ProductoCodigoBarra,
                                             ProductoCodigoBarra.producto_id == Producto.id)
                                       .filter(ProductoCodigoBarra.codigo_barra.ilike(f'%{primer_token}%'))
                                       .filter(Producto.observer_id.isnot(None))
                                       .subquery())
                        base = base.filter(or_(
                            clausula,
                            ObsProducto.observer_id.in_(sub_obs),
                            ObsProducto.observer_id.in_(sub_loc_obs),
                        ))
                    else:
                        base = base.filter(clausula)

            if lab == '__none__':
                base = base.filter(ObsProducto.laboratorio_observer.is_(None))
            elif lab:
                try:
                    base = base.filter(ObsProducto.laboratorio_observer == int(lab))
                except ValueError:
                    pass

            if only_alt:
                # Productos con >=2 EAN en obs_codigos_barras (principal + alts)
                # o que tienen alts manuales en producto_codigos_barra.
                sub_obs = (session.query(ObsCodigoBarras.producto_observer)
                           .filter(ObsCodigoBarras.fecha_baja.is_(None))
                           .filter(ObsCodigoBarras.orden > 1)
                           .subquery())
                sub_loc = (session.query(Producto.observer_id)
                           .join(ProductoCodigoBarra,
                                 ProductoCodigoBarra.producto_id == Producto.id)
                           .filter(ProductoCodigoBarra.es_principal.is_(False))
                           .filter(Producto.observer_id.isnot(None))
                           .subquery())
                base = base.filter(or_(
                    ObsProducto.observer_id.in_(sub_obs),
                    ObsProducto.observer_id.in_(sub_loc),
                ))

            if only_pack:
                # es_pack es campo del master. Requiere que exista master.
                base = base.filter(Producto.es_pack == 1)

            if con_ean:
                # Producto tiene EAN si: tiene entrada en obs_codigos_barras
                # activa, o tiene un Producto master con codigo_barra no vacio.
                sub_obs = (session.query(ObsCodigoBarras.producto_observer)
                           .filter(ObsCodigoBarras.fecha_baja.is_(None))
                           .subquery())
                base = base.filter(or_(
                    ObsProducto.observer_id.in_(sub_obs),
                    Producto.codigo_barra.isnot(None),
                ))

            if rubro:
                try:
                    rubro_id = int(rubro)
                    sub_ids = (session.query(ObsSubrubro.observer_id)
                               .filter(ObsSubrubro.rubro_observer == rubro_id).subquery())
                    base = base.filter(ObsProducto.subrubro_observer.in_(sub_ids))
                except (ValueError, TypeError):
                    pass

            if venta_tipo:
                if venta_tipo == 'libre':
                    tvc_vals = ['L']
                elif venta_tipo == 'receta':
                    tvc_vals = ['R', 'A']
                elif venta_tipo == 'controlado':
                    tvc_vals = ['1', '2', '3', '4', '5', '6', '7', '8']
                else:
                    tvc_vals = []
                if tvc_vals:
                    base = base.filter(ObsProducto.id_tipo_venta_control.in_(tvc_vals))

            total = base.count()
            rows = (base.order_by(ObsProducto.descripcion)
                        .limit(limit).offset(offset).all())

            obs_ids = [obs.observer_id for obs, _ in rows]
            local_ids = [loc.id for _, loc in rows if loc]

            # EANs por obs_id: principal (orden=1) + alts (orden>1).
            ean_principal_por_obs = {}
            ean_alts_por_obs = defaultdict(list)
            if obs_ids:
                cb_rows = (session.query(ObsCodigoBarras.producto_observer,
                                         ObsCodigoBarras.codigo_barras,
                                         ObsCodigoBarras.orden)
                           .filter(ObsCodigoBarras.producto_observer.in_(obs_ids))
                           .filter(ObsCodigoBarras.fecha_baja.is_(None))
                           .order_by(ObsCodigoBarras.producto_observer,
                                     ObsCodigoBarras.orden).all())
                for oid, cb, orden in cb_rows:
                    if oid not in ean_principal_por_obs:
                        ean_principal_por_obs[oid] = cb
                    else:
                        ean_alts_por_obs[oid].append(cb)

            # Alts del master (sobreescriben a obs_codigos_barras cuando hay master).
            alts_master_por_prod = defaultdict(list)
            if local_ids:
                cb_loc = (session.query(ProductoCodigoBarra.producto_id,
                                        ProductoCodigoBarra.codigo_barra)
                          .filter(ProductoCodigoBarra.producto_id.in_(local_ids))
                          .filter(ProductoCodigoBarra.es_principal.is_(False))
                          .all())
                for pid, cb in cb_loc:
                    alts_master_por_prod[pid].append(cb)

            # Labs ObServer (nombre por observer_id).
            labs_obs = {}
            lab_obs_ids = list({obs.laboratorio_observer for obs, _ in rows
                                if obs.laboratorio_observer})
            if lab_obs_ids:
                rows_lo = (session.query(ObsLaboratorio.observer_id,
                                          ObsLaboratorio.descripcion)
                            .filter(ObsLaboratorio.observer_id.in_(lab_obs_ids)).all())
                labs_obs = dict(rows_lo)

            # Stock ObServer (sum por producto, todas las farmacias).
            stock_obs_map = {}
            if obs_ids:
                rows_so = (session.query(ObsStock.producto_observer,
                                          func.sum(ObsStock.stock_actual))
                           .filter(ObsStock.producto_observer.in_(obs_ids))
                           .group_by(ObsStock.producto_observer).all())
                stock_obs_map = dict(rows_so)

            # Stock ERP local: por codigo_barra (master o EAN principal de obs).
            cb_para_erp = set()
            for obs, loc in rows:
                cb_principal = (loc.codigo_barra if loc else None) or \
                               ean_principal_por_obs.get(obs.observer_id)
                if cb_principal:
                    cb_para_erp.add(cb_principal)
            stock_erp_map = {}
            if cb_para_erp:
                rows_se = (session.query(ErpStock.codigo_barra, ErpStock.cantidad)
                           .filter(ErpStock.codigo_barra.in_(list(cb_para_erp))).all())
                stock_erp_map = dict(rows_se)

            # Atributos del master.
            atributos_map = {}
            if local_ids:
                atrs = (session.query(ProductoAtributo)
                        .filter(ProductoAtributo.producto_id.in_(local_ids)).all())
                for a in atrs:
                    atributos_map[a.producto_id] = {
                        'monodroga': a.monodroga_display,
                        'concentracion_mg': float(a.concentracion_mg) if a.concentracion_mg else None,
                        'concentracion_unidad': a.concentracion_unidad,
                        'forma_farma': a.forma_farma,
                        'cantidad_envase': float(a.cantidad_envase) if a.cantidad_envase else None,
                        'fuente': a.fuente,
                        'confianza': a.confianza,
                    }

            # Mapping local Laboratorio (para mostrar nombre del lab elegido en
            # el dropdown de cambio de laboratorio en cada row del master).
            local_labs_map = {}
            if local_ids:
                local_lab_ids = list({loc.laboratorio_id for _, loc in rows
                                      if loc and loc.laboratorio_id})
                if local_lab_ids:
                    rows_ll = (session.query(Laboratorio.id, Laboratorio.nombre)
                                .filter(Laboratorio.id.in_(local_lab_ids)).all())
                    local_labs_map = dict(rows_ll)

            tvc_map = {}
            droga_map = {}
            for obs, _ in rows:
                tvc_map[obs.observer_id] = obs.id_tipo_venta_control
                if obs.nombre_droga_observer:
                    droga_map[obs.observer_id] = obs.nombre_droga_observer

            def _tvc_label(t):
                if not t:
                    return ''
                if t == 'L':
                    return 'Libre'
                if t in ('R', 'A'):
                    return 'Receta'
                if t in ('1', '2', '3', '4', '5', '6', '7', '8'):
                    return f'Ctrl·{t}'
                return t

            data = []
            for obs, loc in rows:
                cb_principal = (loc.codigo_barra if loc else None) or \
                               ean_principal_por_obs.get(obs.observer_id) or ''
                alts = (alts_master_por_prod[loc.id] if loc
                        else ean_alts_por_obs.get(obs.observer_id, []))
                lab_nombre = ''
                if loc and loc.laboratorio_id:
                    lab_nombre = local_labs_map.get(loc.laboratorio_id, '')
                if not lab_nombre and obs.laboratorio_observer:
                    lab_nombre = labs_obs.get(obs.laboratorio_observer, '')
                data.append({
                    'id': loc.id if loc else None,
                    'observer_id': obs.observer_id,
                    'codigo_barra': cb_principal,
                    'descripcion': (loc.descripcion if loc else obs.descripcion) or '',
                    'alts': alts,
                    'precio_pvp': float(loc.precio_pvp) if loc and loc.precio_pvp else None,
                    'laboratorio_id': (loc.laboratorio_id if loc else None) or '',
                    'laboratorio_observer': obs.laboratorio_observer,
                    'laboratorio_nombre': lab_nombre,
                    'actualizado_en': loc.actualizado_en.strftime('%d/%m/%Y')
                                      if loc and loc.actualizado_en else '',
                    'ultima_compra': loc.ultima_compra.strftime('%d/%m/%Y')
                                     if loc and loc.ultima_compra else '',
                    'es_pack': (loc.es_pack if loc else 0) or 0,
                    'cantidad_reposicion_fija': loc.cantidad_reposicion_fija if loc else None,
                    'tvc': obs.id_tipo_venta_control or '',
                    'tvc_label': _tvc_label(obs.id_tipo_venta_control),
                    'droga_id': obs.nombre_droga_observer,
                    'stock_erp': stock_erp_map.get(cb_principal),
                    'stock_obs': stock_obs_map.get(obs.observer_id),
                    'atributos': atributos_map.get(loc.id) if loc else None,
                    'tiene_master': loc is not None,
                })
            return jsonify({'data': data, 'total': total, 'limit': limit, 'offset': offset})

    @app.route('/producto/materializar/<int:observer_id>', methods=['POST'])
    @login_required
    def producto_materializar(observer_id):
        """Crea un Producto master local a partir de un ObsProducto.

        EAN principal: el de orden=1 en obs_codigos_barras. Si no hay EANs en
        ObServer, se usa el observer_id como placeholder (mejor que fallar —
        el usuario lo edita despues).

        Laboratorio: matchea Laboratorio.observer_id == obs.laboratorio_observer.
        Si no existe local, se intenta crear con el nombre de ObsLaboratorio.
        """
        from database import (
            Laboratorio,
            ObsCodigoBarras,
            ObsLaboratorio,
            ObsProducto,
        )

        with database.get_db() as session:
            obs = session.get(ObsProducto, observer_id)
            if not obs:
                return jsonify({'ok': False,
                                'error': f'observer_id {observer_id} no existe'}), 404

            # Idempotente: si ya existe master, devolverlo.
            existente = (session.query(Producto)
                         .filter_by(observer_id=observer_id).first())
            if existente:
                return jsonify({'ok': True, 'id': existente.id,
                                'msg': 'ya existia'})

            # EAN principal desde obs_codigos_barras.
            ean_row = (session.query(ObsCodigoBarras.codigo_barras)
                       .filter_by(producto_observer=observer_id)
                       .filter(ObsCodigoBarras.fecha_baja.is_(None))
                       .order_by(ObsCodigoBarras.orden)
                       .first())
            ean = ean_row[0] if ean_row else f'OBS-{observer_id}'

            # Si por casualidad ese EAN ya esta tomado por otro producto local,
            # bail con error claro en vez de violar UNIQUE.
            colision = (session.query(Producto)
                        .filter_by(codigo_barra=ean).first())
            if colision:
                return jsonify({
                    'ok': False,
                    'error': f'EAN {ean} ya esta asignado al producto master '
                             f'#{colision.id}. Vinculalo desde /catalogacion.',
                }), 409

            # Resolver laboratorio local por observer_id (o crearlo si falta).
            lab_id_local = None
            if obs.laboratorio_observer:
                lab = (session.query(Laboratorio)
                       .filter_by(observer_id=obs.laboratorio_observer).first())
                if not lab:
                    obs_lab = session.get(ObsLaboratorio, obs.laboratorio_observer)
                    if obs_lab:
                        lab = Laboratorio(
                            nombre=obs_lab.descripcion,
                            observer_id=obs.laboratorio_observer,
                            activo=True,
                        )
                        session.add(lab)
                        session.flush()
                if lab:
                    lab_id_local = lab.id

            prod = Producto(
                codigo_barra=ean,
                descripcion=obs.descripcion,
                observer_id=observer_id,
                laboratorio_id=lab_id_local,
                codigo_alfabeta=obs.codigo_alfabeta,
                fuente_creacion='materializar_obs',
            )
            session.add(prod)
            session.commit()
            return jsonify({'ok': True, 'id': prod.id})

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
        # alt1/2/3 quitados — los alternativos ahora se editan vía
        # /producto/<id>/codigos (POST a producto_codigos_barra)
        allowed = {'descripcion', 'codigo_barra', 'precio_pvp', 'es_pack',
                   'cantidad_reposicion_fija'}
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
            elif field == 'cantidad_reposicion_fija':
                if not value:
                    prod.cantidad_reposicion_fija = None
                else:
                    try:
                        n = int(value)
                        if n < 0:
                            return {'error': 'Debe ser >= 0'}, 400
                        prod.cantidad_reposicion_fija = n if n > 0 else None
                    except ValueError:
                        return {'error': 'Cantidad inválida'}, 400
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

    @app.route('/producto/nuevo', methods=['GET', 'POST'])
    @login_required
    def producto_nuevo():
        """Crea un producto nuevo y redirige a su ficha completa.

        GET ?ean=...&desc=...&lab_id=... — si hay EAN crea directamente.
        Sin EAN muestra un form para ingresar EAN + laboratorio.
        POST — mismo flujo desde el form.
        """
        if request.method == 'POST':
            ean = (request.form.get('ean') or '').strip()
            desc = (request.form.get('desc') or '').strip()
            lab_id_raw = request.form.get('lab_id') or ''
        else:
            ean = (request.args.get('ean') or '').strip()
            desc = (request.args.get('desc') or '').strip()
            lab_id_raw = request.args.get('lab_id') or ''

        try:
            lab_id = int(lab_id_raw) if lab_id_raw else None
        except ValueError:
            lab_id = None

        if not ean:
            with database.get_db() as session:
                labs = (session.query(Laboratorio)
                        .filter(Laboratorio.activo == True)  # noqa: E712
                        .order_by(Laboratorio.nombre).all())
                labs_data = [{'id': l.id, 'nombre': l.nombre} for l in labs]
            return render_template('producto_nuevo.html', desc=desc,
                                   lab_id_sel=lab_id, laboratorios=labs_data)

        with database.get_db() as session:
            existing = session.query(Producto).filter_by(codigo_barra=ean).first()
            if existing:
                return redirect(url_for('producto_detalle', prod_id=existing.id))
            prod = Producto(
                codigo_barra=ean,
                descripcion=desc or None,
                laboratorio_id=lab_id,
                fuente_creacion='oferta_import',
            )
            session.add(prod)
            session.commit()
            prod_id = prod.id
        return redirect(url_for('producto_detalle', prod_id=prod_id))

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

    @app.route('/producto/<int:prod_id>')
    def producto_detalle(prod_id):
        """Ficha del producto: identificación + atributos + historial precios + ventas."""
        from database import ProductoAtributo
        with database.get_db() as session:
            prod = session.get(Producto, prod_id)
            if not prod:
                return render_template('producto_detalle.html', producto=None), 404
            atr = session.get(ProductoAtributo, prod_id)
            obs = None
            if prod.observer_id:
                from database import ObsNombreDroga, ObsProducto
                op = session.get(ObsProducto, prod.observer_id)
                if op:
                    obs_droga = session.get(ObsNombreDroga, op.nombre_droga_observer) if op.nombre_droga_observer else None
                    obs = {
                        'observer_id': op.observer_id,
                        'descripcion': op.descripcion,
                        'cantidad_envase': float(op.cantidad_envase) if op.cantidad_envase else None,
                        'troquel': op.troquel,
                        'codigo_alfabeta': op.codigo_alfabeta,
                        'tvc': op.id_tipo_venta_control,
                        'cadena_frio': op.requiere_cadena_frio,
                        'baja': op.fecha_baja,
                        'monodroga': obs_droga.descripcion if obs_droga else None,
                    }
            # EANs alts desde producto_codigos_barra (1-a-N)
            from database import ProductoCodigoBarra
            alts_lista = [
                cb for cb, in (session.query(ProductoCodigoBarra.codigo_barra)
                                .filter_by(producto_id=prod.id, es_principal=False)
                                .all()) if cb
            ]
            producto = {
                'id': prod.id,
                'codigo_barra': prod.codigo_barra,
                'descripcion': prod.descripcion or '',
                'alts': alts_lista,
                'precio_pvp': float(prod.precio_pvp) if prod.precio_pvp else None,
                'es_pack': bool(prod.es_pack),
                'laboratorio': prod.laboratorio.nombre if prod.laboratorio else None,
                'codigo_alfabeta': prod.codigo_alfabeta,
                'observer_id': prod.observer_id,
                'ultima_compra': prod.ultima_compra.strftime('%d/%m/%Y') if prod.ultima_compra else None,
                'actualizado_en': prod.actualizado_en.strftime('%d/%m/%Y') if prod.actualizado_en else None,
                'monodroga_legacy': prod.monodroga,
                'presentacion_legacy': prod.presentacion,
                'accion_terapeutica_legacy': prod.accion_terapeutica,
                'fuente_creacion': prod.fuente_creacion,
            }
            atributos = None
            if atr:
                atributos = {
                    'monodroga_display': atr.monodroga_display,
                    'concentracion_mg': float(atr.concentracion_mg) if atr.concentracion_mg else None,
                    'concentracion_unidad': atr.concentracion_unidad,
                    'forma_farma': atr.forma_farma,
                    'cantidad_envase': float(atr.cantidad_envase) if atr.cantidad_envase else None,
                    'via_admin': atr.via_admin,
                    'fuente': atr.fuente,
                    'confianza': atr.confianza,
                    'extraido_en': atr.extraido_en.strftime('%d/%m/%Y %H:%M') if atr.extraido_en else None,
                }
        return render_template('producto_detalle.html',
                               producto=producto, atributos=atributos, obs=obs)

    @app.route('/api/producto/<int:prod_id>/codigos', methods=['GET', 'POST'])
    @login_required
    def api_producto_codigos(prod_id):
        """GET: lista todos los EANs del producto (tabla 1-a-N + obs).
        POST: agrega un EAN nuevo. Body: {codigo_barra, fuente?}."""
        from database import ObsCodigoBarras, ProductoCodigoBarra
        with database.get_db() as session:
            prod = session.get(Producto, prod_id)
            if not prod:
                return jsonify({'error': 'Producto no encontrado'}), 404
            if request.method == 'GET':
                locales = (session.query(ProductoCodigoBarra)
                           .filter_by(producto_id=prod_id)
                           .order_by(ProductoCodigoBarra.es_principal.desc(),
                                     ProductoCodigoBarra.id).all())
                obs_eans = []
                if prod.observer_id:
                    obs_eans = (session.query(ObsCodigoBarras)
                                .filter_by(producto_observer=prod.observer_id)
                                .order_by(ObsCodigoBarras.orden).all())
                return jsonify({
                    'locales': [{
                        'id': c.id,
                        'codigo_barra': c.codigo_barra,
                        'es_principal': c.es_principal,
                        'fuente': c.fuente,
                        'factura_id': c.factura_id,
                        'creado_en': c.creado_en.strftime('%d/%m/%Y') if c.creado_en else None,
                    } for c in locales],
                    'observer': [{
                        'codigo_barra': o.codigo_barras,
                        'orden': o.orden,
                        'baja': o.fecha_baja.strftime('%d/%m/%Y') if o.fecha_baja else None,
                    } for o in obs_eans],
                })
            data = request.get_json(silent=True) or {}
            cb = (data.get('codigo_barra') or '').strip()
            if not cb:
                return jsonify({'error': 'codigo_barra requerido'}), 400
            ya = (session.query(ProductoCodigoBarra.id)
                  .filter_by(producto_id=prod_id, codigo_barra=cb).first())
            if ya:
                return jsonify({'error': 'Ese código ya está cargado para este producto'}), 409
            session.add(ProductoCodigoBarra(
                producto_id=prod_id,
                codigo_barra=cb,
                es_principal=False,
                fuente=(data.get('fuente') or 'manual').strip(),
            ))
            session.commit()
            return jsonify({'ok': True})

    @app.route('/api/producto/<int:prod_id>/codigos/<int:cb_id>', methods=['DELETE', 'PATCH'])
    @login_required
    def api_producto_codigo_modificar(prod_id, cb_id):
        """DELETE: borra. PATCH: marca como principal (desmarca al resto)."""
        from database import ProductoCodigoBarra
        with database.get_db() as session:
            cb = session.get(ProductoCodigoBarra, cb_id)
            if not cb or cb.producto_id != prod_id:
                return jsonify({'error': 'No encontrado'}), 404
            if request.method == 'DELETE':
                if cb.es_principal:
                    return jsonify({'error': 'No podés borrar el principal. Marcá otro como principal primero.'}), 400
                session.delete(cb)
                session.commit()
                return jsonify({'ok': True})
            # PATCH: marcar como principal
            (session.query(ProductoCodigoBarra)
             .filter_by(producto_id=prod_id)
             .update({'es_principal': False}))
            cb.es_principal = True
            # Sincronizar productos.codigo_barra con el nuevo principal (compat).
            prod = session.get(Producto, prod_id)
            if prod:
                prod.codigo_barra = cb.codigo_barra
            session.commit()
            return jsonify({'ok': True})

    @app.route('/api/producto/<int:prod_id>/recatalogar', methods=['POST'])
    def api_producto_recatalogar(prod_id):
        """Re-extrae atributos de UN producto (usa obs + regex). No pisa fuente='manual'."""
        from catalogacion import upsert_atributos
        with database.get_db() as session:
            prod = session.get(Producto, prod_id)
            if not prod:
                return jsonify({'error': 'No encontrado'}), 404
            try:
                atr = upsert_atributos(prod, session, force=True)
                session.commit()
                if not atr:
                    return jsonify({'ok': True, 'atributos': None,
                                    'mensaje': 'No se pudo extraer nada de la descripción'})
                return jsonify({'ok': True, 'atributos': {
                    'monodroga_display': atr.monodroga_display,
                    'concentracion_mg': float(atr.concentracion_mg) if atr.concentracion_mg else None,
                    'concentracion_unidad': atr.concentracion_unidad,
                    'forma_farma': atr.forma_farma,
                    'cantidad_envase': float(atr.cantidad_envase) if atr.cantidad_envase else None,
                    'via_admin': atr.via_admin,
                    'fuente': atr.fuente,
                    'confianza': atr.confianza,
                }})
            except Exception as e:
                session.rollback()
                return jsonify({'error': str(e)}), 500

    @app.route('/api/match-dimensional', methods=['GET'])
    @login_required
    def api_match_dimensional():
        """Busca candidatos para un EAN/descripción que NO matchea ninguna fuente.

        Query params:
          - ean (opcional): EAN del producto/oferta no resuelto.
          - desc (opcional): descripción libre. Si viene, se extraen atributos.
          - droga, conc_mg, conc_unit, forma, cantidad: si pasás los atributos directos.

        Devuelve top 10 candidatos rankeados por score (5+ = probable, 7+ = casi seguro).
        """
        from catalogacion import match_dimensional_candidatos
        from helpers import _find_producto

        ean = (request.args.get('ean') or '').strip()
        desc = (request.args.get('desc') or '').strip()
        droga = (request.args.get('droga') or '').strip().lower() or None
        conc_mg = request.args.get('conc_mg')
        conc_unit = (request.args.get('conc_unit') or '').strip().upper() or None
        forma = (request.args.get('forma') or '').strip().upper() or None
        cantidad = request.args.get('cantidad')

        with database.get_db() as session:
            # Si llegó EAN, primero chequear si matchea (en cuyo caso no hace falta dimensional)
            if ean:
                prod = _find_producto(session, ean)
                if prod:
                    return jsonify({
                        'matched_directly': True,
                        'producto': {
                            'id': prod.id,
                            'codigo_barra': prod.codigo_barra,
                            'descripcion': prod.descripcion,
                        },
                    })

            try:
                conc_mg_f = float(conc_mg) if conc_mg else None
                cantidad_f = float(cantidad) if cantidad else None
            except ValueError:
                return jsonify({'error': 'Parámetros numéricos inválidos'}), 400

            candidatos = match_dimensional_candidatos(
                session,
                descripcion=desc or None,
                monodroga_norm=droga,
                concentracion_mg=conc_mg_f,
                concentracion_unidad=conc_unit,
                forma_farma=forma,
                cantidad_envase=cantidad_f,
                limit=10,
            )
            return jsonify({
                'matched_directly': False,
                'ean': ean or None,
                'desc': desc or None,
                'candidatos': candidatos,
            })

    @app.route('/api/catalogacion/backfill', methods=['POST'])
    @login_required
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
        from catalogacion import _normalizar_droga
        from database import ProductoAtributo
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
                prod.monodroga = m or None
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
            # Colectar todos los EANs equivalentes (principal + 1-a-N) del producto.
            eans = {ean}
            prod = _find_producto(session, ean)
            if prod:
                if prod.codigo_barra:
                    eans.add(prod.codigo_barra)
                from database import ProductoCodigoBarra
                for cb, in (session.query(ProductoCodigoBarra.codigo_barra)
                            .filter_by(producto_id=prod.id).all()):
                    if cb:
                        eans.add(cb)

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
