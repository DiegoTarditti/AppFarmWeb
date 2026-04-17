"""Descuentos routes: campañas, módulos, importación."""

import os
from flask import render_template, request, redirect, url_for, flash, jsonify
from werkzeug.utils import secure_filename
import database
from database import DescuentoCampana, DescuentoModulo, DescuentoModuloItem
from helpers import UPLOAD_FOLDER, get_providers


def init_app(app):

    @app.route('/descuentos')
    def descuentos_list():
        session = database.SessionLocal()
        campanas = session.query(DescuentoCampana).order_by(DescuentoCampana.creado_en.desc()).all()
        data = [{
            'id': c.id,
            'laboratorio_nombre': c.laboratorio_nombre,
            'proveedor_id': c.proveedor_id,
            'fecha': c.fecha.strftime('%d/%m/%Y') if c.fecha else '',
            'observacion': c.observacion or '',
            'n_modulos': len(c.modulos),
            'n_items': sum(len(m.items) for m in c.modulos),
            'creado_en': c.creado_en.strftime('%d/%m/%Y') if c.creado_en else '',
        } for c in campanas]
        session.close()
        return render_template('descuentos.html', campanas=data)

    @app.route('/descuentos/upload', methods=['GET', 'POST'])
    def descuento_upload():
        """GET: formulario de carga. POST: parsea xlsx y muestra preview."""
        if request.method == 'GET':
            provs = get_providers()
            return render_template('descuento_upload.html', proveedores=provs, preview=None)

        # POST: parsear archivo
        f = request.files.get('archivo')
        if not f or not f.filename:
            flash('Seleccioná un archivo.')
            return redirect(url_for('descuento_upload'))

        ext = f.filename.rsplit('.', 1)[-1].lower()
        if ext not in ('xlsx', 'xls', 'pdf'):
            flash('Solo se aceptan archivos Excel (.xlsx / .xls) o PDF.')
            return redirect(url_for('descuento_upload'))

        import tempfile, json
        from parsers.descuento_xlsx_parser import parse_descuento

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}')
        f.save(tmp.name)
        tmp.close()

        try:
            modulos = parse_descuento(tmp.name)
        except Exception as e:
            flash(f'Error al leer el archivo: {e}')
            return redirect(url_for('descuento_upload'))
        finally:
            import os as _os; _os.unlink(tmp.name)

        if not modulos:
            flash('El archivo no contiene módulos reconocibles.')
            return redirect(url_for('descuento_upload'))

        provs = get_providers()

        return render_template('descuento_upload.html',
                               proveedores=provs,
                               preview=modulos,
                               preview_json=json.dumps(modulos))

    @app.route('/descuentos/campana/guardar', methods=['POST'])
    def descuento_campana_guardar():
        """Guarda la campaña completa desde el preview."""
        import json
        from datetime import date as _date

        proveedor_id = request.form.get('proveedor_id', '').strip()
        lab_nombre = request.form.get('laboratorio_nombre', '').strip()
        fecha_str = request.form.get('fecha', '').strip()
        observacion = request.form.get('observacion', '').strip()
        preview_json = request.form.get('preview_json', '[]')

        if not lab_nombre:
            flash('Ingresá el nombre del laboratorio.')
            return redirect(url_for('descuento_upload'))

        try:
            modulos_data = json.loads(preview_json)
        except (ValueError, TypeError):
            flash('Datos inválidos, volvé a subir el archivo.')
            return redirect(url_for('descuento_upload'))

        fecha = None
        if fecha_str:
            try:
                from datetime import datetime as _dt
                fecha = _dt.strptime(fecha_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        with database.get_db() as session:
            campana = DescuentoCampana(
                proveedor_id=int(proveedor_id) if proveedor_id else None,
                laboratorio_nombre=lab_nombre,
                fecha=fecha or _date.today(),
                observacion=observacion or None,
            )
            session.add(campana)
            session.flush()

            for mod_data in modulos_data:
                modulo = DescuentoModulo(
                    campana_id=campana.id,
                    nombre=mod_data.get('nombre', 'SIN NOMBRE'),
                    activo=1,
                )
                session.add(modulo)
                session.flush()
                for it in mod_data.get('items', []):
                    session.add(DescuentoModuloItem(
                        modulo_id=modulo.id,
                        codigo_ean=str(it.get('ean', '')),
                        descripcion=it.get('descripcion', '') or None,
                        cantidad=int(it.get('cantidad', 1)),
                        descuento=float(it.get('descuento', 0)),
                        es_principal=1 if it.get('es_principal') else 0,
                    ))

            session.commit()
            campana_id = campana.id
        flash(f'Campaña guardada con {len(modulos_data)} módulos.')
        return redirect(url_for('descuento_campana', campana_id=campana_id))

    @app.route('/descuentos/campana/<int:campana_id>')
    def descuento_campana(campana_id):
        with database.get_db() as session:
            c = session.get(DescuentoCampana, campana_id)
            if not c:
                flash('Campaña no encontrada.')
                return redirect(url_for('descuentos_list'))
            campana = {
                'id': c.id,
                'laboratorio_nombre': c.laboratorio_nombre,
                'proveedor_id': c.proveedor_id,
                'proveedor_nombre': c.proveedor.razon_social if c.proveedor else None,
                'fecha': c.fecha.strftime('%d/%m/%Y') if c.fecha else '',
                'fecha_iso': c.fecha.strftime('%Y-%m-%d') if c.fecha else '',
                'observacion': c.observacion or '',
                'creado_en': c.creado_en.strftime('%d/%m/%Y') if c.creado_en else '',
            }
            modulos = [{
                'id': m.id,
                'nombre': m.nombre or '—',
                'activo': m.activo,
                'n_items': len(m.items),
            } for m in c.modulos]
        return render_template('descuento_campana.html',
                               campana=campana, modulos=modulos, proveedores=get_providers())

    @app.route('/descuentos/campana/<int:campana_id>/edit', methods=['POST'])
    def descuento_campana_edit(campana_id):
        with database.get_db() as session:
            c = session.get(DescuentoCampana, campana_id)
            if c:
                proveedor_id = request.form.get('proveedor_id', '').strip()
                lab_nombre = request.form.get('laboratorio_nombre', '').strip()
                fecha_str = request.form.get('fecha', '').strip()
                c.proveedor_id = int(proveedor_id) if proveedor_id else None
                if lab_nombre:
                    c.laboratorio_nombre = lab_nombre
                c.observacion = request.form.get('observacion', '').strip() or None
                if fecha_str:
                    try:
                        from datetime import datetime as _dt
                        c.fecha = _dt.strptime(fecha_str, '%Y-%m-%d').date()
                    except ValueError:
                        pass
                session.commit()
        return redirect(url_for('descuento_campana', campana_id=campana_id))

    @app.route('/descuentos/campana/<int:campana_id>/delete', methods=['POST'])
    def descuento_campana_delete(campana_id):
        with database.get_db() as session:
            c = session.get(DescuentoCampana, campana_id)
            if c:
                session.delete(c)
                session.commit()
        flash('Campaña eliminada.')
        return redirect(url_for('descuentos_list'))

    @app.route('/descuentos/modulo/<int:modulo_id>')
    def descuento_detalle(modulo_id):
        with database.get_db() as session:
            m = session.get(DescuentoModulo, modulo_id)
            if not m:
                flash('Módulo no encontrado.')
                return redirect(url_for('descuentos_list'))
            items = [{
                'id': it.id,
                'codigo_ean': it.codigo_ean,
                'descripcion': it.descripcion or '',
                'cantidad': it.cantidad,
                'descuento': float(it.descuento),
                'es_principal': bool(it.es_principal),
            } for it in m.items]
            modulo = {
                'id': m.id,
                'nombre': m.nombre or '—',
                'activo': m.activo,
                'campana_id': m.campana_id,
            }
        return render_template('descuento_detalle.html', modulo=modulo, items=items)

    @app.route('/descuentos/modulo/<int:modulo_id>/item', methods=['POST'])
    def descuento_add_item(modulo_id):
        with database.get_db() as session:
            m = session.get(DescuentoModulo, modulo_id)
            if not m:
                flash('Módulo no encontrado.')
                return redirect(url_for('descuentos_list'))
            try:
                ean = request.form.get('codigo_ean', '').strip()
                if not ean:
                    flash('El código EAN es obligatorio.')
                    return redirect(url_for('descuento_detalle', modulo_id=modulo_id))
                session.add(DescuentoModuloItem(
                    modulo_id=modulo_id,
                    codigo_ean=ean,
                    descripcion=request.form.get('descripcion', '').strip() or None,
                    cantidad=max(1, int(request.form.get('cantidad', 1))),
                    descuento=float(request.form.get('descuento', 0)),
                    es_principal=1 if request.form.get('es_principal') else 0,
                ))
                session.commit()
            except (ValueError, TypeError):
                flash('Datos inválidos.')
        return redirect(url_for('descuento_detalle', modulo_id=modulo_id))

    @app.route('/descuentos/modulo/<int:modulo_id>/item/<int:item_id>/delete', methods=['POST'])
    def descuento_delete_item(modulo_id, item_id):
        with database.get_db() as session:
            item = session.get(DescuentoModuloItem, item_id)
            if item and item.modulo_id == modulo_id:
                session.delete(item)
                session.commit()
        return redirect(url_for('descuento_detalle', modulo_id=modulo_id))

    @app.route('/descuentos/modulo/<int:modulo_id>/toggle', methods=['POST'])
    def descuento_toggle(modulo_id):
        session = database.SessionLocal()
        m = session.get(DescuentoModulo, modulo_id)
        campana_id = None
        if m:
            m.activo = 0 if m.activo else 1
            session.commit()
            campana_id = m.campana_id
        session.close()
        if campana_id:
            return redirect(url_for('descuento_campana', campana_id=campana_id))
        return redirect(url_for('descuentos_list'))

    @app.route('/descuentos/modulo/<int:modulo_id>/delete', methods=['POST'])
    def descuento_delete(modulo_id):
        session = database.SessionLocal()
        m = session.get(DescuentoModulo, modulo_id)
        campana_id = m.campana_id if m else None
        if m:
            session.delete(m)
            session.commit()
        session.close()
        flash('Módulo eliminado.')
        if campana_id:
            return redirect(url_for('descuento_campana', campana_id=campana_id))
        return redirect(url_for('descuentos_list'))

    @app.route('/descuentos/upload-libre', methods=['GET', 'POST'])
    def descuento_upload_libre():
        """Importación libre: tabla plana LAB | EAN | DESC | CANT | DTO%"""
        provs = get_providers()

        if request.method == 'GET':
            return render_template('descuento_upload_libre.html', proveedores=provs, preview=None)

        f = request.files.get('archivo')
        if not f or not f.filename:
            flash('Seleccioná un archivo.')
            return redirect(url_for('descuento_upload_libre'))

        ext = f.filename.rsplit('.', 1)[-1].lower()
        if ext not in ('xlsx', 'xls'):
            flash('Solo se aceptan archivos Excel (.xlsx / .xls).')
            return redirect(url_for('descuento_upload_libre'))

        import tempfile, json
        from parsers.descuento_libre_parser import parse_descuento_libre

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}')
        f.save(tmp.name)
        tmp.close()

        try:
            items = parse_descuento_libre(tmp.name)
        except Exception as e:
            flash(f'Error al leer el archivo: {e}')
            return redirect(url_for('descuento_upload_libre'))
        finally:
            import os as _os; _os.unlink(tmp.name)

        if not items:
            flash('El archivo no contiene artículos reconocibles.')
            return redirect(url_for('descuento_upload_libre'))

        return render_template('descuento_upload_libre.html',
                               proveedores=provs,
                               preview=items,
                               preview_json=json.dumps(items))

    @app.route('/descuentos/libre/guardar', methods=['POST'])
    def descuento_libre_guardar():
        """Guarda la importación libre como una campaña con un módulo por lab."""
        import json
        from datetime import datetime as _dt

        proveedor_id = request.form.get('proveedor_id') or None
        observacion  = request.form.get('observacion', '').strip()
        fecha_str    = request.form.get('fecha', '').strip()
        preview_json = request.form.get('preview_json', '[]')

        try:
            items = json.loads(preview_json)
        except (ValueError, TypeError):
            flash('Datos inválidos, volvé a subir el archivo.')
            return redirect(url_for('descuento_upload_libre'))

        fecha = None
        if fecha_str:
            try:
                fecha = _dt.strptime(fecha_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        from collections import defaultdict
        grupos = defaultdict(list)
        for it in items:
            grupos[it['lab'] or 'SIN LAB'].append(it)

        session = database.SessionLocal()
        try:
            campanas_creadas = 0
            for lab_nombre, lab_items in grupos.items():
                prov_id = int(proveedor_id) if proveedor_id else None
                campana = database.DescuentoCampana(
                    proveedor_id=prov_id,
                    laboratorio_nombre=lab_nombre,
                    fecha=fecha,
                    observacion=observacion,
                )
                session.add(campana)
                session.flush()

                modulo = database.DescuentoModulo(
                    campana_id=campana.id,
                    nombre=f'Importación {lab_nombre}',
                    codigo=None,
                    laboratorio=lab_nombre,
                    descuento_default=0,
                )
                session.add(modulo)
                session.flush()

                for it in lab_items:
                    item = database.DescuentoModuloItem(
                        modulo_id=modulo.id,
                        codigo_barra=it['ean'],
                        descripcion=it['descripcion'],
                        cantidad=it['cantidad'] or 0,
                        descuento=it['descuento'] or 0,
                        es_principal=0,
                    )
                    session.add(item)
                campanas_creadas += 1

            session.commit()
            flash(f'{campanas_creadas} campaña(s) importada(s) correctamente.')
        except Exception as e:
            session.rollback()
            flash(f'Error al guardar: {e}')
        finally:
            session.close()

        return redirect(url_for('descuentos_list'))
