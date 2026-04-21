"""Plantillas unificadas para las 3 entidades (laboratorio/drogueria/proveedor).

Lee del modelo nuevo `Plantilla` y también expone (read-only) plantillas viejas:
- ExportTemplate (laboratorio, XLSX columnas) → formato 'xlsx'
- PlantillaExportacion + PlantillaCampo (proveedor, TXT ancho fijo) → formato 'txt_fijo'
"""

import json
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash, jsonify, abort
import database
from database import Plantilla, Laboratorio, Provider, ExportTemplate, PlantillaExportacion, PlantillaCampo


VALID_TIPOS = ('laboratorio', 'drogueria', 'proveedor')
VALID_FORMATOS = ('xlsx', 'txt_fijo', 'csv')
VALID_TIPOS_DOC = ('pedido', 'recepcion', 'descuento')


def _entidad_nombre(session, tipo, id_):
    """Resuelve (tipo, id) → (nombre, label_singular, label_plural) o aborta."""
    labels = {
        'laboratorio': ('Laboratorio', 'Laboratorios'),
        'drogueria':   ('Droguería',   'Droguerías'),
        'proveedor':   ('Proveedor',   'Proveedores'),
    }
    if tipo not in labels:
        abort(404)
    label, label_pl = labels[tipo]
    if tipo == 'laboratorio':
        ent = session.get(Laboratorio, id_)
        if not ent: abort(404)
        return ent.nombre, label, label_pl
    # drogueria / proveedor → ambos viven en Provider
    ent = session.get(Provider, id_)
    if not ent: abort(404)
    # si tipo solicitado no coincide con Provider.tipo, igual dejamos ver (pragmatico)
    return ent.razon_social, label, label_pl


def _plantilla_to_dict(p):
    return {
        'id': p.id,
        'nombre': p.nombre,
        'formato': p.formato,
        'tipo_doc': p.tipo_doc.capitalize(),
        'actualizada': p.actualizada_en.strftime('%Y-%m-%d') if p.actualizada_en else '—',
        'default': bool(p.es_default),
        'origen': 'nueva',
    }


def _adapter_old_lab(et):
    """ExportTemplate → dict compatible con plantillas_list."""
    return {
        'id': f'legacy-lab-{et.laboratorio_id}',
        'nombre': 'Plantilla XLSX (migración pendiente)',
        'formato': 'xlsx',
        'tipo_doc': 'Pedido',
        'actualizada': '—',
        'default': True,
        'origen': 'legacy',
    }


def _adapter_old_prov(pe):
    """PlantillaExportacion → dict compatible con plantillas_list."""
    return {
        'id': f'legacy-prov-{pe.id}',
        'nombre': pe.nombre + ' (TXT legacy)',
        'formato': 'txt_fijo',
        'tipo_doc': 'Pedido',
        'actualizada': pe.creado_en.strftime('%Y-%m-%d') if pe.creado_en else '—',
        'default': False,
        'origen': 'legacy',
    }


def init_app(app):

    @app.route('/partner/<tipo>/<int:id>/plantillas')
    def plantillas_list(tipo, id):
        if tipo not in VALID_TIPOS:
            abort(404)
        with database.get_db() as session:
            nombre, label, label_pl = _entidad_nombre(session, tipo, id)

            rows = session.query(Plantilla).filter_by(entidad_tipo=tipo, entidad_id=id)\
                                           .order_by(Plantilla.es_default.desc(), Plantilla.nombre).all()
            plantillas = [_plantilla_to_dict(p) for p in rows]

        return render_template('plantillas_list.html',
                               entidad_tipo=tipo, entidad_id=id,
                               entidad_nombre=nombre, entidad_label=label,
                               entidad_label_plural=label_pl, plantillas=plantillas)


    @app.route('/partner/<tipo>/<int:id>/plantillas/new', methods=['POST'])
    def plantilla_create(tipo, id):
        if tipo not in VALID_TIPOS:
            abort(404)
        nombre = (request.form.get('nombre') or '').strip()
        formato = request.form.get('formato', 'xlsx')
        tipo_doc = request.form.get('tipo_doc', 'pedido')
        if formato not in VALID_FORMATOS: formato = 'xlsx'
        if tipo_doc not in VALID_TIPOS_DOC: tipo_doc = 'pedido'
        if len(nombre) < 2:
            flash('El nombre debe tener al menos 2 caracteres.')
            return redirect(url_for('plantillas_list', tipo=tipo, id=id))

        with database.get_db() as session:
            _entidad_nombre(session, tipo, id)  # valida existencia
            default_config = {'xlsx': {'columnas': []},
                              'txt_fijo': {'campos': [], 'encoding': 'UTF-8', 'eol': 'LF'},
                              'csv': {'columnas': [], 'separador': ','}}[formato]
            p = Plantilla(entidad_tipo=tipo, entidad_id=id, nombre=nombre,
                          formato=formato, tipo_doc=tipo_doc,
                          config_json=json.dumps(default_config))
            session.add(p)
            session.commit()
            pid = p.id
        return redirect(url_for('plantilla_editor', tipo=tipo, id=id, pid=pid))


    @app.route('/partner/<tipo>/<int:id>/plantillas/<pid>')
    def plantilla_editor(tipo, id, pid):
        if tipo not in VALID_TIPOS:
            abort(404)
        with database.get_db() as session:
            nombre_ent, label, label_pl = _entidad_nombre(session, tipo, id)
            # pid puede ser int (Plantilla) o string legacy-xxx → por ahora solo int
            try:
                pid_int = int(pid)
            except ValueError:
                flash('Plantilla legacy — migración pendiente, editá desde la vista vieja.')
                return redirect(url_for('plantillas_list', tipo=tipo, id=id))
            p = session.get(Plantilla, pid_int)
            if not p or p.entidad_tipo != tipo or p.entidad_id != id:
                abort(404)
            config = json.loads(p.config_json or '{}')
            plantilla = {
                'id': p.id, 'nombre': p.nombre, 'formato': p.formato,
                'tipo_doc': p.tipo_doc.capitalize(), 'default': p.es_default,
                'config': config,
            }
        return render_template('plantilla_editor.html',
                               entidad_tipo=tipo, entidad_id=id,
                               entidad_nombre=nombre_ent, plantilla=plantilla)


    @app.route('/partner/<tipo>/<int:id>/plantillas/<int:pid>/save', methods=['POST'])
    def plantilla_save(tipo, id, pid):
        if tipo not in VALID_TIPOS:
            abort(404)
        body = request.get_json(silent=True) or {}
        with database.get_db() as session:
            p = session.get(Plantilla, pid)
            if not p or p.entidad_tipo != tipo or p.entidad_id != id:
                return jsonify({'error': 'no encontrada'}), 404
            if 'nombre' in body:
                nombre = (body.get('nombre') or '').strip()
                if len(nombre) >= 2: p.nombre = nombre
            if 'tipo_doc' in body and body['tipo_doc'] in VALID_TIPOS_DOC:
                p.tipo_doc = body['tipo_doc']
            if 'es_default' in body:
                want_default = bool(body['es_default'])
                if want_default:
                    # desmarcar el resto de la misma entidad+tipo_doc
                    session.query(Plantilla).filter(
                        Plantilla.entidad_tipo == tipo,
                        Plantilla.entidad_id == id,
                        Plantilla.tipo_doc == p.tipo_doc,
                        Plantilla.id != p.id,
                    ).update({'es_default': False})
                p.es_default = want_default
            if 'config' in body:
                p.config_json = json.dumps(body['config'])
            p.actualizada_en = datetime.utcnow()
            session.commit()
            return jsonify({'ok': True, 'id': p.id})


    @app.route('/partner/<tipo>/<int:id>/plantillas/<int:pid>/delete', methods=['POST'])
    def plantilla_delete(tipo, id, pid):
        if tipo not in VALID_TIPOS:
            abort(404)
        with database.get_db() as session:
            p = session.get(Plantilla, pid)
            if not p or p.entidad_tipo != tipo or p.entidad_id != id:
                abort(404)
            session.delete(p)
            session.commit()
        flash('Plantilla eliminada.')
        return redirect(url_for('plantillas_list', tipo=tipo, id=id))


    @app.route('/partner/<tipo>/<int:id>/plantillas/<int:pid>/duplicate', methods=['POST'])
    def plantilla_duplicate(tipo, id, pid):
        if tipo not in VALID_TIPOS:
            abort(404)
        with database.get_db() as session:
            p = session.get(Plantilla, pid)
            if not p or p.entidad_tipo != tipo or p.entidad_id != id:
                abort(404)
            copia = Plantilla(entidad_tipo=p.entidad_tipo, entidad_id=p.entidad_id,
                              nombre=p.nombre + ' (copia)', formato=p.formato,
                              tipo_doc=p.tipo_doc, config_json=p.config_json,
                              es_default=False)
            session.add(copia)
            session.commit()
            new_id = copia.id
        return redirect(url_for('plantilla_editor', tipo=tipo, id=id, pid=new_id))
