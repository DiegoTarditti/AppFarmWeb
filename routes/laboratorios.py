"""Laboratorio CRUD routes."""

import os
import tempfile
from flask import request, redirect, url_for, flash, render_template, jsonify
import database
import json
from database import Laboratorio, Producto, ExportTemplate, OfertaMinimo

EXPORT_FIELDS = [
    ('ean',           'Código de Barra'),
    ('nombre',        'Descripción'),
    ('total',         'Cantidad'),
    ('cant_modulo',   'Cant. Módulo'),
    ('cant_oferta',   'Cant. Oferta'),
    ('cant_oferta_min','Cant. Oferta c/Mín'),
    ('cant_nodeal',   'Sin Deal'),
    ('precio_pvp',    'Precio PVP'),
    ('erp_qty',       'Stock ERP'),
    ('rotacion',      'Rotación'),
    ('avg_monthly',   'Prom. Mensual'),
]


def init_app(app):

    @app.route('/laboratorios')
    def laboratorios_list():
        from sqlalchemy import func as _func
        with database.get_db() as session:
            labs = (session.query(Laboratorio)
                    .filter(Laboratorio.activo == True)
                    .order_by(Laboratorio.nombre).all())
            lab_ids   = [l.id     for l in labs]
            lab_names = [l.nombre for l in labs]

            prod_map = dict(
                session.query(Producto.laboratorio_id, _func.count(Producto.id))
                .filter(Producto.laboratorio_id.in_(lab_ids))
                .group_by(Producto.laboratorio_id).all()
            ) if lab_ids else {}

            ped_map = dict(
                session.query(database.Pedido.laboratorio, _func.count(database.Pedido.id))
                .filter(database.Pedido.laboratorio.in_(lab_names))
                .group_by(database.Pedido.laboratorio).all()
            ) if lab_names else {}

            analytics_map = dict(
                session.query(database.ProductAnalytics.laboratorio,
                              _func.count(database.ProductAnalytics.codigo_barra))
                .filter(database.ProductAnalytics.laboratorio.in_(lab_names))
                .group_by(database.ProductAnalytics.laboratorio).all()
            ) if lab_names else {}

            data = [{
                'id': l.id, 'nombre': l.nombre,
                'prod_count':      prod_map.get(l.id, 0),
                'ped_count':       ped_map.get(l.nombre, 0),
                'analytics_count': analytics_map.get(l.nombre, 0),
            } for l in labs]
        return render_template('laboratorios.html', laboratorios=data)

    @app.route('/laboratorio/create', methods=['POST'])
    def laboratorio_create():
        nombre = request.form.get('nombre', '').strip()
        if not nombre:
            flash('El nombre es obligatorio.')
            return redirect(url_for('laboratorios_list'))
        with database.get_db() as session:
            existing = session.query(Laboratorio).filter(Laboratorio.nombre.ilike(nombre)).first()
            if existing:
                flash(f'Ya existe un laboratorio con ese nombre.')
            else:
                session.add(Laboratorio(nombre=nombre))
                session.commit()
        return redirect(url_for('laboratorios_list'))

    @app.route('/laboratorio/<int:lab_id>/edit', methods=['POST'])
    def laboratorio_edit(lab_id):
        nombre = request.form.get('nombre', '').strip()
        if not nombre:
            flash('El nombre es obligatorio.')
            return redirect(url_for('laboratorios_list'))
        with database.get_db() as session:
            lab = session.get(Laboratorio, lab_id)
            if lab:
                lab.nombre = nombre
                session.commit()
        return redirect(url_for('laboratorios_list'))

    @app.route('/laboratorio/<int:lab_id>/delete', methods=['POST'])
    def laboratorio_delete(lab_id):
        with database.get_db() as session:
            lab = session.get(Laboratorio, lab_id)
            if lab:
                session.query(Producto).filter_by(laboratorio_id=lab_id).update({'laboratorio_id': None})
                session.delete(lab)
                session.commit()
        return redirect(url_for('laboratorios_list'))

    @app.route('/laboratorios/sync-observer', methods=['POST'])
    def laboratorios_sync_observer():
        """Vincula laboratorios locales con el espejo obs_laboratorios.

        Asume que /admin/observer-sync ya pobló obs_laboratorios desde ObServer.
        Estrategia:
        - Si existe lab local con mismo observer_id → update nombre.
        - Si no y existe lab con mismo nombre (case-insensitive) → asocia observer_id.
        - Si no existe → crea con activo=False (escondido hasta marcarlo).
        """
        labs_remotos = []
        with database.get_db() as _s_read:
            for obs in (_s_read.query(database.ObsLaboratorio)
                        .filter(database.ObsLaboratorio.fecha_baja.is_(None))
                        .order_by(database.ObsLaboratorio.descripcion).all()):
                labs_remotos.append({'id': obs.observer_id, 'nombre': obs.descripcion})

        if not labs_remotos:
            flash('El espejo obs_laboratorios está vacío. Corré el sync general primero en /admin/observer-sync.',
                  'error')
            return redirect(url_for('observer_sync_panel'))

        remotos = labs_remotos

        nuevos = actualizados = vinculados = duplicados = 0
        with database.get_db() as session:
            existentes_por_obs = {l.observer_id: l for l in
                                  session.query(Laboratorio)
                                  .filter(Laboratorio.observer_id.isnot(None)).all()}
            existentes_por_nom = {l.nombre.lower(): l for l in
                                  session.query(Laboratorio).all()}

            for r in remotos:
                obs_id = r['id']
                nom = r['nombre']
                if not nom:
                    continue

                lab = existentes_por_obs.get(obs_id)
                if lab:
                    # Evitar rename que choque contra otro nombre existente
                    if lab.nombre != nom and nom.lower() not in existentes_por_nom:
                        del existentes_por_nom[lab.nombre.lower()]
                        lab.nombre = nom
                        existentes_por_nom[nom.lower()] = lab
                        actualizados += 1
                    continue

                lab = existentes_por_nom.get(nom.lower())
                if lab:
                    # Segundo obs_id con mismo nombre → ignorar (duplicado en ObServer)
                    if lab.observer_id and lab.observer_id != obs_id:
                        duplicados += 1
                        continue
                    lab.observer_id = obs_id
                    existentes_por_obs[obs_id] = lab
                    vinculados += 1
                    continue

                # Nuevo: insertar y registrarlo para dedup en esta misma corrida
                nuevo = Laboratorio(nombre=nom, observer_id=obs_id, activo=False)
                session.add(nuevo)
                existentes_por_nom[nom.lower()] = nuevo
                existentes_por_obs[obs_id] = nuevo
                nuevos += 1

            session.commit()

        flash(f'Sync ObServer: {nuevos} nuevos (inactivos), {vinculados} vinculados, '
              f'{actualizados} renombrados, {duplicados} duplicados ignorados.')
        return redirect(url_for('laboratorios_activos'))

    @app.route('/laboratorios/activos', methods=['GET', 'POST'])
    def laboratorios_activos():
        """Pantalla admin para activar/desactivar laboratorios en bulk."""
        with database.get_db() as session:
            if request.method == 'POST':
                activos_ids = set(int(x) for x in request.form.getlist('activo_ids') if x.isdigit())
                todos = session.query(Laboratorio).all()
                cambios = 0
                for lab in todos:
                    nuevo = lab.id in activos_ids
                    if lab.activo != nuevo:
                        lab.activo = nuevo
                        cambios += 1
                session.commit()
                flash(f'{cambios} laboratorio(s) actualizado(s).')
                return redirect(url_for('laboratorios_activos'))

            labs = session.query(Laboratorio).order_by(Laboratorio.nombre).all()
            n_activos = sum(1 for l in labs if l.activo)
            data = [{'id': l.id, 'nombre': l.nombre, 'activo': bool(l.activo),
                     'observer_id': l.observer_id} for l in labs]
        return render_template('laboratorios_activos.html',
                               laboratorios=data, n_total=len(data), n_activos=n_activos)

    @app.route('/api/ofertas/preview', methods=['POST'])
    def api_ofertas_preview():
        """Preview de ofertas simples (solo EAN + descripción)."""
        from parsers.ofertas_xlsx import parse_ofertas_xlsx
        f = request.files.get('archivo')
        if not f or not f.filename:
            return jsonify({'error': 'No se recibió archivo'}), 400
        ext = f.filename.rsplit('.', 1)[-1].lower()
        if ext not in ('xlsx', 'xls'):
            return jsonify({'error': 'Solo se aceptan .xlsx / .xls'}), 400
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}')
        f.save(tmp.name); tmp.close()
        try:
            items = parse_ofertas_xlsx(tmp.name)
            return jsonify({'items': items})
        except Exception as e:
            return jsonify({'error': str(e)}), 400
        finally:
            try: os.unlink(tmp.name)
            except OSError: pass

    @app.route('/api/ofertas/preview-con-minimo', methods=['POST'])
    def api_ofertas_preview_con_minimo():
        """Preview de ofertas con cantidad mínima (formato Bernabó)."""
        from parsers.bernabo_ofertas import parse_bernabo_ofertas
        f = request.files.get('archivo')
        if not f or not f.filename:
            return jsonify({'error': 'No se recibió archivo'}), 400
        ext = f.filename.rsplit('.', 1)[-1].lower()
        if ext not in ('xlsx', 'xls'):
            return jsonify({'error': 'Solo se aceptan .xlsx / .xls'}), 400
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}')
        f.save(tmp.name); tmp.close()
        try:
            items = parse_bernabo_ofertas(tmp.name)
            grupos = len({it['grupo_id'] for it in items if it['grupo_id'] is not None})
            return jsonify({'items': items, 'grupos': grupos or None})
        except Exception as e:
            return jsonify({'error': str(e)}), 400
        finally:
            try: os.unlink(tmp.name)
            except OSError: pass

    @app.route('/api/laboratorio/<int:lab_id>/ofertas-minimo', methods=['GET'])
    def api_ofertas_minimo_get(lab_id):
        with database.get_db() as session:
            rows = session.query(OfertaMinimo).filter_by(laboratorio_id=lab_id).order_by(OfertaMinimo.grupo_id.nullslast(), OfertaMinimo.id).all()
            return jsonify({
                'items': [{
                    'ean': r.ean, 'descripcion': r.descripcion, 'codigo': r.codigo,
                    'unidades_minima': r.unidades_minima,
                    'descuento_psl': float(r.descuento_psl) if r.descuento_psl is not None else None,
                    'rentabilidad': float(r.rentabilidad) if r.rentabilidad is not None else None,
                    'plazo_pago': r.plazo_pago, 'grupo_id': r.grupo_id,
                } for r in rows],
                'count': len(rows),
            })

    @app.route('/api/laboratorio/<int:lab_id>/ofertas-minimo', methods=['POST'])
    def api_ofertas_minimo_save(lab_id):
        body = request.get_json(silent=True) or {}
        items = body.get('items', [])
        if not items:
            return jsonify({'error': 'Sin items'}), 400
        with database.get_db() as session:
            lab = session.get(Laboratorio, lab_id)
            if not lab:
                return jsonify({'error': 'Laboratorio no encontrado'}), 404
            session.query(OfertaMinimo).filter_by(laboratorio_id=lab_id).delete()
            for it in items:
                session.add(OfertaMinimo(
                    laboratorio_id  = lab_id,
                    ean             = it.get('ean', ''),
                    descripcion     = it.get('descripcion'),
                    codigo          = it.get('codigo'),
                    unidades_minima = it.get('unidades_minima'),
                    descuento_psl   = it.get('descuento_psl'),
                    rentabilidad    = it.get('rentabilidad'),
                    plazo_pago      = it.get('plazo_pago'),
                    grupo_id        = it.get('grupo_id'),
                ))
            session.commit()
            return jsonify({'ok': True, 'guardados': len(items)})

    @app.route('/laboratorio/<int:lab_id>/export-template', methods=['GET', 'POST'])
    def laboratorio_export_template(lab_id):
        with database.get_db() as session:
            lab = session.get(Laboratorio, lab_id)
            if not lab:
                flash('Laboratorio no encontrado.')
                return redirect(url_for('laboratorios_list'))

            if request.method == 'POST':
                fields  = request.form.getlist('field')
                labels  = request.form.getlist('label')
                enabled = set(request.form.getlist('enabled'))
                header  = request.form.get('custom_header', '').strip() or None
                cols = [{'field': f, 'label': l, 'enabled': f in enabled}
                        for f, l in zip(fields, labels)]
                tpl = session.get(ExportTemplate, lab_id)
                if tpl:
                    tpl.columns_json  = json.dumps(cols)
                    tpl.custom_header = header
                else:
                    session.add(ExportTemplate(
                        laboratorio_id=lab_id,
                        columns_json=json.dumps(cols),
                        custom_header=header,
                    ))
                session.commit()
                flash('Plantilla guardada.')
                return redirect(url_for('laboratorio_export_template', lab_id=lab_id))

            tpl = session.get(ExportTemplate, lab_id)
            saved = json.loads(tpl.columns_json) if tpl else []
            saved_fields = [c['field'] for c in saved if any(f == c['field'] for f, _ in EXPORT_FIELDS)]
            remaining    = [f for f, _ in EXPORT_FIELDS if f not in saved_fields]
            ordered_cols = []
            for c in saved:
                default_label = next((l for f, l in EXPORT_FIELDS if f == c['field']), c['field'])
                ordered_cols.append({'field': c['field'], 'label': c.get('label', default_label), 'enabled': c.get('enabled', True)})
            for f, l in EXPORT_FIELDS:
                if f in remaining:
                    ordered_cols.append({'field': f, 'label': l, 'enabled': False})
            return render_template('export_template.html',
                                   lab=lab, cols=ordered_cols,
                                   custom_header=tpl.custom_header if tpl else '')
