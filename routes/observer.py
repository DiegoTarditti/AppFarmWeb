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
        if not observer_source.observer_disponible():
            flash('ObServer no está disponible. Usá el análisis por archivo mientras tanto.', 'error')
            return redirect(url_for('purchase_index'))

        if request.method == 'GET':
            labs = observer_source.get_laboratorios_disponibles()
            return render_template('observer_analizar.html', laboratorios=labs, n_days_default=35)

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
