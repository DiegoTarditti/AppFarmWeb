"""Rutas que consumen directamente la DB de ObServer (modo online).

Habilitadas solo para roles/usuarios con acceso online. No requieren subir archivos.
"""

import os
import uuid
import json
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
import database
from database import Pedido, PedidoItem, AnalisisSesion
from purchase_engine import analyze_purchase
from helpers import PURCHASE_FOLDER, get_config, _upsert_producto, now_ar
from auth import tiene_permiso
import observer_source


def _user_tiene_observer(user):
    """Decide si el usuario accede a ObServer. Por ahora: rol farmacia, dev o admin."""
    if not user or not user.is_authenticated:
        return False
    return user.rol in ('farmacia', 'dev', 'admin')


def init_app(app):

    @app.route('/obs/productos')
    @login_required
    def obs_productos():
        """Catálogo completo ObServer (122k) con ventas + stock + laboratorio + monodroga.
        Solo lectura, paginado, sin tocar la tabla `productos` local."""
        from sqlalchemy import func as _f, or_ as _or
        from datetime import datetime
        q = (request.args.get('q') or '').strip()
        lab_id = request.args.get('lab_id', type=int)
        try:
            page = max(1, int(request.args.get('page', '1')))
        except ValueError:
            page = 1
        per_page = 50
        offset = (page - 1) * per_page

        id_farmacia = int(os.environ.get('OBSERVER_ID_FARMACIA', '10525'))

        with database.get_db() as session:
            base = (session.query(database.ObsProducto)
                    .filter(database.ObsProducto.fecha_baja.is_(None)))
            if q:
                like = f'%{q}%'
                base = base.filter(_or(
                    database.ObsProducto.descripcion.ilike(like),
                    database.ObsProducto.codigo_alfabeta.ilike(like),
                ))
            if lab_id:
                base = base.filter(database.ObsProducto.laboratorio_observer == lab_id)

            total = base.count()
            productos = (base.order_by(database.ObsProducto.descripcion)
                         .offset(offset).limit(per_page).all())

            obs_ids = [p.observer_id for p in productos]

            # Resolver laboratorios y drogas en batch
            labs_map = dict(
                session.query(database.ObsLaboratorio.observer_id,
                              database.ObsLaboratorio.descripcion).all()
            )
            drogas_map = dict(
                session.query(database.ObsNombreDroga.observer_id,
                              database.ObsNombreDroga.descripcion).all()
            )

            # Stock actual
            stock_map = {}
            if obs_ids:
                for po, sa in (session.query(
                        database.ObsStock.producto_observer,
                        database.ObsStock.stock_actual)
                        .filter(database.ObsStock.id_farmacia == id_farmacia,
                                database.ObsStock.producto_observer.in_(obs_ids)).all()):
                    stock_map[po] = int(sa or 0)

            # Agregados de ventas: total 3m y 12m
            ventas_3m = ventas_12m = {}
            if obs_ids:
                hoy = datetime.now()
                # Mes actual y 2 atrás = 3m; 11 atrás = 12m (ym como int yyyymm)
                def _ym_hace(n):
                    y, m = hoy.year, hoy.month - n
                    while m <= 0:
                        m += 12
                        y -= 1
                    return y * 100 + m
                desde_3m = _ym_hace(2)
                desde_12m = _ym_hace(11)
                hasta = hoy.year * 100 + hoy.month

                rows = (session.query(
                        database.ObsVentaMensual.producto_observer,
                        _f.sum(database.ObsVentaMensual.unidades).label('u'))
                        .filter(database.ObsVentaMensual.id_farmacia == id_farmacia,
                                database.ObsVentaMensual.producto_observer.in_(obs_ids),
                                (database.ObsVentaMensual.anio * 100 +
                                 database.ObsVentaMensual.mes).between(desde_3m, hasta))
                        .group_by(database.ObsVentaMensual.producto_observer).all())
                ventas_3m = {po: float(u or 0) for po, u in rows}

                rows = (session.query(
                        database.ObsVentaMensual.producto_observer,
                        _f.sum(database.ObsVentaMensual.unidades).label('u'))
                        .filter(database.ObsVentaMensual.id_farmacia == id_farmacia,
                                database.ObsVentaMensual.producto_observer.in_(obs_ids),
                                (database.ObsVentaMensual.anio * 100 +
                                 database.ObsVentaMensual.mes).between(desde_12m, hasta))
                        .group_by(database.ObsVentaMensual.producto_observer).all())
                ventas_12m = {po: float(u or 0) for po, u in rows}

            # EAN del bridge local (opcional)
            ean_map = dict(
                session.query(database.Producto.observer_id,
                              database.Producto.codigo_barra)
                .filter(database.Producto.observer_id.in_(obs_ids)).all()
            ) if obs_ids else {}

            data = []
            for p in productos:
                v3 = ventas_3m.get(p.observer_id, 0)
                v12 = ventas_12m.get(p.observer_id, 0)
                data.append({
                    'observer_id':  p.observer_id,
                    'ean':          ean_map.get(p.observer_id) or '',
                    'codigo_alfabeta': p.codigo_alfabeta or '',
                    'descripcion':  p.descripcion,
                    'laboratorio':  labs_map.get(p.laboratorio_observer) or '—',
                    'monodroga':    drogas_map.get(p.nombre_droga_observer) or '',
                    'stock':        stock_map.get(p.observer_id, 0),
                    'prom_3m':      round(v3 / 3, 1),
                    'prom_12m':     round(v12 / 12, 1),
                    'total_3m':     v3,
                    'total_12m':    v12,
                })

            # Labs para el dropdown
            labs = (session.query(database.ObsLaboratorio)
                    .filter(database.ObsLaboratorio.fecha_baja.is_(None))
                    .order_by(database.ObsLaboratorio.descripcion).all())

        last_page = max(1, (total + per_page - 1) // per_page)
        return render_template('obs_productos.html',
                               productos=data, total=total,
                               page=page, last_page=last_page,
                               q=q, lab_id=lab_id, labs=labs,
                               per_page=per_page)

    @app.route('/observer/status')
    @login_required
    def observer_status():
        """Health check de la DB de ObServer."""
        return jsonify({
            'disponible': observer_source.observer_disponible(),
            'url_configurada': bool(os.environ.get('OBSERVER_DATABASE_URL')),
            'usuario_habilitado': _user_tiene_observer(current_user),
        })

    @app.route('/observer/laboratorios')
    @login_required
    def observer_laboratorios():
        """Lista de laboratorios disponibles en ObServer."""
        if not _user_tiene_observer(current_user):
            flash('Tu usuario no tiene acceso a ObServer.', 'error')
            return redirect(url_for('index'))
        if not observer_source.observer_disponible():
            flash('ObServer no está disponible.', 'error')
            return redirect(url_for('index'))
        labs = observer_source.get_laboratorios_disponibles()
        return render_template('observer_labs.html', laboratorios=labs)

    @app.route('/observer/analizar', methods=['GET', 'POST'])
    @login_required
    def observer_analizar():
        """Análisis de ventas consultando directo a ObServer (sin subir archivos)."""
        if not _user_tiene_observer(current_user):
            flash('Tu usuario no tiene acceso a ObServer.', 'error')
            return redirect(url_for('index'))
        if not observer_source.observer_analisis_disponible():
            flash('No hay datos de ventas para analizar. Corré el sync desde la PC de la farmacia.', 'error')
            return redirect(url_for('purchase_index'))

        if request.method == 'GET':
            labs = observer_source.get_laboratorios_disponibles()
            lab_preseleccionado = (request.args.get('lab') or '').strip()
            proceso_id = request.args.get('proceso', type=int)
            return render_template('observer_analizar.html', laboratorios=labs,
                                   n_days_default=35,
                                   lab_preseleccionado=lab_preseleccionado,
                                   proceso_id=proceso_id,
                                   now=datetime.now())

        # POST: ejecutar análisis
        laboratorio = (request.form.get('laboratorio') or '').strip()
        try:
            n_days = max(1, min(365, int(request.form.get('n_days', 35))))
        except (ValueError, TypeError):
            n_days = 35
        try:
            anio = int(request.form.get('anio_hasta') or datetime.now().year)
            mes = int(request.form.get('mes_hasta') or datetime.now().month)
        except (ValueError, TypeError):
            hoy = datetime.now()
            anio, mes = hoy.year, hoy.month

        if not laboratorio:
            flash('Elegí un laboratorio.', 'error')
            return redirect(url_for('observer_analizar'))

        productos = observer_source.get_ventas_laboratorio(laboratorio, anio, mes)
        if not productos:
            flash(f'Sin datos de ventas para "{laboratorio}" en ese período.', 'warning')
            return redirect(url_for('observer_analizar'))

        # Calcular start_month (el primero de los 12 meses hacia atrás)
        start_m = mes - 11
        start_y = anio
        while start_m <= 0:
            start_m += 12
            start_y -= 1
        end_m = mes

        cfg = get_config()
        results = analyze_purchase(
            productos, n_days, start_m, end_m,
            umbral_pico=cfg['umbral_pico'],
            umbral_baja=cfg['umbral_baja'],
            umbral_tendencia=cfg['umbral_tendencia'],
            rot_alta_min=cfg['rot_alta_min'],
            rot_media_min=cfg['rot_media_min'],
        )

        uid = str(uuid.uuid4())
        periodo_str = f'{start_m:02d}/{start_y} - {end_m:02d}/{anio}'
        data = {
            'uid': uid,
            'farmacia': current_user.nombre_completo or 'Farmacia',
            'laboratorio': laboratorio,
            'periodo': periodo_str,
            'start_month': start_m,
            'n_days': n_days,
            'umbral_tendencia': cfg['umbral_tendencia'],
            'rot_alta_min': cfg['rot_alta_min'],
            'rot_alta_tol': cfg['rot_alta_tol'],
            'rot_media_min': cfg['rot_media_min'],
            'rot_media_tol': cfg['rot_media_tol'],
            'rot_baja_tol': cfg['rot_baja_tol'],
            'products': results,
            'proceso_id': request.form.get('proceso_id', type=int),
        }

        # Registrar sesión con fuente='observer'
        with database.get_db() as session:
            sesion = AnalisisSesion(
                laboratorio_nombre=laboratorio,
                periodo=periodo_str,
                farmacia=data['farmacia'],
                n_days=n_days,
                fuente='observer',
                n_productos=len(results),
            )
            session.add(sesion)
            session.commit()
            data['sesion_id'] = sesion.id

        json_path = os.path.join(PURCHASE_FOLDER, f'{uid}.json')
        with open(json_path, 'w', encoding='utf-8') as jf:
            json.dump(data, jf, ensure_ascii=False)

        flash(f'Análisis de {laboratorio} completado desde ObServer.', 'success')
        return redirect(url_for('purchase_results', uid=uid))

    @app.route('/observer/factura/<int:invoice_id>/recepciones')
    @login_required
    def observer_recepciones_factura(invoice_id):
        """Devuelve las recepciones de una factura según ObServer para el cruce."""
        if not _user_tiene_observer(current_user):
            return jsonify({'ok': False, 'error': 'Sin acceso a ObServer'}), 403
        if not observer_source.observer_disponible():
            return jsonify({'ok': False, 'error': 'ObServer no disponible'}), 503
        with database.get_db() as session:
            inv = session.get(database.Invoice, invoice_id)
            if not inv:
                return jsonify({'ok': False, 'error': 'Factura no encontrada'}), 404
            items = observer_source.get_recepciones_factura(
                inv.numero_factura, inv.proveedor_cuit
            )
            return jsonify({'ok': True, 'items': items, 'count': len(items)})

    @app.route('/observer/factura/<int:invoice_id>/sync', methods=['POST'])
    @login_required
    def observer_sync_factura(invoice_id):
        """Trae las recepciones de ObServer usando el nro de comprobante indicado por el usuario.

        Por ahora el nro de comprobante de ObServer se pide manualmente al usuario
        (parámetro `comprobante`). Más adelante se resolverá de dónde sale automáticamente.
        """
        from data_extract import save_erp_to_db, compare_invoice_vs_erp, save_differences
        if not _user_tiene_observer(current_user):
            flash('Tu usuario no tiene acceso a ObServer.', 'error')
            return redirect(url_for('compare_view', invoice_id=invoice_id))
        if not observer_source.observer_disponible():
            flash('ObServer no está disponible en este momento.', 'error')
            return redirect(url_for('compare_view', invoice_id=invoice_id))

        comprobante = (request.form.get('comprobante') or '').strip()
        if not comprobante:
            flash('Ingresá el número de comprobante de recepción de ObServer.', 'error')
            return redirect(url_for('compare_view', invoice_id=invoice_id))

        with database.get_db() as session:
            inv = session.get(database.Invoice, invoice_id)
            if not inv:
                flash('Factura no encontrada.', 'error')
                return redirect(url_for('index'))
            recepciones = observer_source.get_recepciones_factura(
                comprobante, inv.proveedor_cuit
            )
            if not recepciones:
                flash(f'ObServer: sin recepciones para el comprobante "{comprobante}" '
                      f'(proveedor {inv.proveedor_cuit or "—"}).', 'warning')
                return redirect(url_for('compare_view', invoice_id=invoice_id))

            # Convertir recepciones de ObServer al formato erp_items
            erp_items = [{
                'codigo_barra': r['codigo_barra'],
                'descripcion': r['descripcion'],
                'cantidad': r['cantidad'],
                'precio_unitario': r['precio_unitario'] * r['cantidad']
                                    if r.get('precio_unitario') and r.get('cantidad') else 0,
            } for r in recepciones]

            save_erp_to_db(session, erp_items)
            differences = compare_invoice_vs_erp(session, invoice_id)
            save_differences(session, invoice_id, differences)

            flash(f'Sincronizado con ObServer (comprobante {comprobante}): '
                  f'{len(recepciones)} ítems cargados.', 'success')
        return redirect(url_for('compare_view', invoice_id=invoice_id))
