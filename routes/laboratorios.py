"""Laboratorio CRUD routes."""

import datetime
import json
import os
import statistics
import tempfile

from flask import flash, jsonify, redirect, render_template, request, url_for

import database
from database import ExportTemplate, Laboratorio, OfertaMinimo, Producto

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

            from database import LaboratorioDrogueria
            labs_en_matriz = set(
                r[0] for r in session.query(LaboratorioDrogueria.laboratorio_id).distinct().all()
            )
            data = [{
                'id': l.id, 'nombre': l.nombre,
                'prod_count':      prod_map.get(l.id, 0),
                'ped_count':       ped_map.get(l.nombre, 0),
                'analytics_count': analytics_map.get(l.nombre, 0),
                'en_matriz':       l.id in labs_en_matriz,
            } for l in labs]
        import observer_source
        return render_template('laboratorios.html', laboratorios=data,
                               observer_disponible=observer_source.observer_disponible())

    @app.route('/laboratorio/create', methods=['POST'])
    def laboratorio_create():
        nombre = request.form.get('nombre', '').strip()
        if not nombre:
            flash('El nombre es obligatorio.')
            return redirect(url_for('laboratorios_list'))
        with database.get_db() as session:
            from helpers import _normalizar_nombre_entidad, get_or_create_laboratorio
            # Detectar duplicado por nombre normalizado (no solo case-insensitive)
            norm_nuevo = _normalizar_nombre_entidad(nombre)
            for c in session.query(Laboratorio).all():
                if _normalizar_nombre_entidad(c.nombre) == norm_nuevo:
                    flash(f'Ya existe un laboratorio: "{c.nombre}". No se creó duplicado.')
                    return redirect(url_for('laboratorios_list'))
            get_or_create_laboratorio(session, nombre)
            session.commit()
        return redirect(url_for('laboratorios_list'))

    @app.route('/laboratorio/<int:lab_id>/edit', methods=['POST'])
    def laboratorio_edit(lab_id):
        nombre = request.form.get('nombre', '').strip()
        if not nombre:
            flash('El nombre es obligatorio.')
            return redirect(url_for('laboratorios_list'))
        with database.get_db() as session:
            from helpers import _normalizar_nombre_entidad
            lab = session.get(Laboratorio, lab_id)
            if not lab:
                return redirect(url_for('laboratorios_list'))
            # Si el nombre nuevo colisiona con OTRO lab (normalizado), avisar.
            norm_nuevo = _normalizar_nombre_entidad(nombre)
            for c in session.query(Laboratorio).filter(Laboratorio.id != lab_id).all():
                if _normalizar_nombre_entidad(c.nombre) == norm_nuevo:
                    flash(f'Ya existe otro laboratorio: "{c.nombre}". No se renombró para evitar duplicado.')
                    return redirect(url_for('laboratorios_list'))
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

        from helpers import _normalizar_nombre_entidad
        nuevos = actualizados = vinculados = duplicados = 0
        with database.get_db() as session:
            existentes_por_obs = {l.observer_id: l for l in
                                  session.query(Laboratorio)
                                  .filter(Laboratorio.observer_id.isnot(None)).all()}
            # Dedup por normalizado profundo (sin acentos, sin sufijo societario):
            # 'Roemmers' y 'Roemmers S.A.' van a la misma clave 'roemmers'.
            existentes_por_norm = {_normalizar_nombre_entidad(l.nombre): l for l in
                                   session.query(Laboratorio).all()}

            for r in remotos:
                obs_id = r['id']
                nom = r['nombre']
                if not nom:
                    continue
                nom_norm = _normalizar_nombre_entidad(nom)

                lab = existentes_por_obs.get(obs_id)
                if lab:
                    # Evitar rename que choque contra otro nombre normalizado existente
                    if lab.nombre != nom and nom_norm not in existentes_por_norm:
                        old_norm = _normalizar_nombre_entidad(lab.nombre)
                        if old_norm in existentes_por_norm:
                            del existentes_por_norm[old_norm]
                        lab.nombre = nom
                        existentes_por_norm[nom_norm] = lab
                        actualizados += 1
                    continue

                lab = existentes_por_norm.get(nom_norm)
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
                existentes_por_norm[nom_norm] = nuevo
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
        import observer_source
        return render_template('laboratorios_activos.html',
                               laboratorios=data, n_total=len(data), n_activos=n_activos,
                               observer_disponible=observer_source.observer_disponible())

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

    @app.route('/laboratorio/<int:lab_id>/ofertas-minimo', methods=['GET'])
    def lab_ofertas_minimo(lab_id):
        """Pantalla con todas las ofertas vigentes de un lab. Editable."""
        with database.get_db() as session:
            lab = session.get(Laboratorio, lab_id)
            if not lab:
                flash('Laboratorio no encontrado.', 'error')
                return redirect(url_for('laboratorios_list'))
            rows = (session.query(OfertaMinimo)
                    .filter_by(laboratorio_id=lab_id)
                    .order_by(OfertaMinimo.grupo_id.nullslast(),
                              OfertaMinimo.descripcion.nullslast(),
                              OfertaMinimo.ean).all())

            # Cabecera: resumen del lote más reciente
            dtos = [float(r.descuento_psl) for r in rows if r.descuento_psl is not None]
            rents = [float(r.rentabilidad) for r in rows if r.rentabilidad is not None]
            con_minimo = sum(1 for r in rows if r.unidades_minima and r.unidades_minima > 1)
            # Droguería y vigencia: tomamos del registro más reciente
            ultimo = max(rows, key=lambda r: r.actualizado_en or datetime.datetime.min) if rows else None
            drog = None
            if ultimo and ultimo.drogueria_id:
                from database import Provider
                drog_obj = session.get(Provider, ultimo.drogueria_id)
                drog = drog_obj.razon_social if drog_obj else None
            cabecera = {
                'drogueria': drog,
                'vigencia_desde': ultimo.vigencia_desde.strftime('%d/%m/%Y') if ultimo and ultimo.vigencia_desde else None,
                'vigencia_hasta': ultimo.vigencia_hasta.strftime('%d/%m/%Y') if ultimo and ultimo.vigencia_hasta else None,
                'observacion': ultimo.observacion if ultimo else None,
                'dto_promedio': round(statistics.mean(dtos), 1) if dtos else None,
                'dto_min': round(min(dtos), 1) if dtos else None,
                'dto_max': round(max(dtos), 1) if dtos else None,
                'rent_promedio': round(statistics.mean(rents), 1) if rents else None,
                'con_minimo': con_minimo,
                'actualizado_en': ultimo.actualizado_en.strftime('%d/%m/%Y %H:%M') if ultimo and ultimo.actualizado_en else None,
            }

            ofertas = [{
                'id': r.id,
                'ean': r.ean,
                'codigo': r.codigo or '',
                'descripcion': r.descripcion or '',
                'unidades_minima': r.unidades_minima,
                'descuento_psl': float(r.descuento_psl) if r.descuento_psl is not None else None,
                'rentabilidad': float(r.rentabilidad) if r.rentabilidad is not None else None,
                'plazo_pago': r.plazo_pago or '',
                'grupo_id': r.grupo_id,
                'actualizado_en': r.actualizado_en.strftime('%d/%m/%Y %H:%M') if r.actualizado_en else '',
            } for r in rows]
        return render_template('lab_ofertas_minimo.html',
                               lab=lab, ofertas=ofertas, total=len(ofertas),
                               cabecera=cabecera)

    @app.route('/laboratorio/<int:lab_id>/equivalencias', methods=['GET'])
    def lab_equivalencias(lab_id):
        """Equivalencias descripcion/código proveedor → producto local guardadas por imports."""
        from database import EquivalenciaProveedor
        with database.get_db() as session:
            lab = session.get(Laboratorio, lab_id)
            if not lab:
                flash('Laboratorio no encontrado.', 'error')
                return redirect(url_for('laboratorios_list'))
            rows = (session.query(EquivalenciaProveedor)
                    .filter_by(laboratorio_id=lab_id)
                    .order_by(EquivalenciaProveedor.descripcion_proveedor)
                    .all())
            equiv = [{
                'id': r.id,
                'descripcion_proveedor': r.descripcion_proveedor or '',
                'producto_id': r.producto_id,
                'producto_desc': r.producto.descripcion if r.producto else '—',
                'producto_ean': r.producto.codigo_barra if r.producto else '—',
            } for r in rows]
        return render_template('lab_equivalencias.html',
                               lab=lab, equiv=equiv, total=len(equiv))

    @app.route('/laboratorio/<int:lab_id>/equivalencias/<int:eq_id>/borrar', methods=['POST'])
    def lab_equivalencia_borrar(lab_id, eq_id):
        from database import EquivalenciaProveedor
        with database.get_db() as session:
            eq = session.get(EquivalenciaProveedor, eq_id)
            if eq and eq.laboratorio_id == lab_id:
                session.delete(eq)
                session.commit()
        return redirect(url_for('lab_equivalencias', lab_id=lab_id))

    @app.route('/laboratorio/<int:lab_id>/ofertas-minimo/borrar-todas', methods=['POST'])
    def lab_ofertas_minimo_borrar_todas(lab_id):
        with database.get_db() as session:
            n = session.query(OfertaMinimo).filter_by(laboratorio_id=lab_id).delete()
            session.commit()
        flash(f'Eliminadas {n} ofertas del laboratorio.', 'success')
        return redirect(url_for('lab_ofertas_minimo', lab_id=lab_id))

    @app.route('/laboratorio/<int:lab_id>/ofertas-minimo/<int:oferta_id>/borrar', methods=['POST'])
    def lab_oferta_minima_borrar(lab_id, oferta_id):
        with database.get_db() as session:
            o = session.get(OfertaMinimo, oferta_id)
            if o and o.laboratorio_id == lab_id:
                session.delete(o)
                session.commit()
                flash('Oferta eliminada.', 'success')
        return redirect(url_for('lab_ofertas_minimo', lab_id=lab_id))

    @app.route('/laboratorio/<int:lab_id>/ofertas-minimo/<int:oferta_id>/editar', methods=['PATCH'])
    def lab_oferta_minima_editar(lab_id, oferta_id):
        """Edita campos EAN, unidades_minima, descuento_psl de una oferta."""
        data = request.get_json(silent=True) or {}
        with database.get_db() as session:
            o = session.get(OfertaMinimo, oferta_id)
            if not o or o.laboratorio_id != lab_id:
                return jsonify({'ok': False, 'error': 'No encontrada'}), 404
            changed = False
            if 'ean' in data:
                ean = (data['ean'] or '').strip() or None
                o.ean = ean
                changed = True
            if 'unidades_minima' in data:
                try:
                    o.unidades_minima = int(data['unidades_minima']) if data['unidades_minima'] not in (None, '') else None
                except (ValueError, TypeError):
                    return jsonify({'ok': False, 'error': 'Mín. inválido'}), 400
                changed = True
            if 'descuento_psl' in data:
                try:
                    o.descuento_psl = float(data['descuento_psl']) if data['descuento_psl'] not in (None, '') else None
                except (ValueError, TypeError):
                    return jsonify({'ok': False, 'error': 'Descuento inválido'}), 400
                changed = True
            if changed:
                o.actualizado_en = datetime.datetime.now()
                session.commit()
            return jsonify({
                'ok': True,
                'ean': o.ean,
                'unidades_minima': o.unidades_minima,
                'descuento_psl': float(o.descuento_psl) if o.descuento_psl is not None else None,
                'actualizado_en': o.actualizado_en.strftime('%d/%m/%Y %H:%M') if o.actualizado_en else '',
            })

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
