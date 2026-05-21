"""Laboratorio CRUD routes."""

import datetime
import hmac
import json
import os
import statistics
import tempfile

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

import database
from database import ExportTemplate, Laboratorio, OfertaMinimo, Producto
from helpers import now_ar, normalizar_unidades_minima

EXPORT_FIELDS = [
    ('ean',           'Código de Barra'),
    ('nombre',        'Descripción'),
    ('total',         'Cantidad'),
    ('cant_modulo',   'Cant. Módulo'),
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
        with database.get_db() as session:
            labs = (session.query(Laboratorio)
                    .filter(Laboratorio.activo == True)
                    .order_by(Laboratorio.nombre).all())

            # Los chips "N productos / N pedidos / N analytics" se sacaron: con
            # ~2000 labs esas 3 agregaciones hacían lenta la pantalla y no
            # aportaban. Solo necesitamos saber qué labs están en la matriz.
            from database import LaboratorioDrogueria
            labs_en_matriz = set(
                r[0] for r in session.query(LaboratorioDrogueria.laboratorio_id).distinct().all()
            )
            data = [{
                'id': l.id, 'nombre': l.nombre,
                'en_matriz':       l.id in labs_en_matriz,
                'usa_packs':       bool(l.usa_packs),
            } for l in labs]
        import observer_source
        return render_template('laboratorios.html', laboratorios=data,
                               observer_disponible=observer_source.observer_disponible())

    @app.route('/laboratorio/create', methods=['POST'])
    @login_required
    def laboratorio_create():
        nombre = request.form.get('nombre', '').strip()
        next_url = request.form.get('next') or request.referrer or url_for('laboratorios_list')
        if not nombre:
            flash('El nombre es obligatorio.')
            return redirect(next_url)
        with database.get_db() as session:
            from helpers import _normalizar_nombre_entidad, get_or_create_laboratorio
            # Detectar duplicado por nombre normalizado (no solo case-insensitive)
            norm_nuevo = _normalizar_nombre_entidad(nombre)
            for c in session.query(Laboratorio).all():
                if _normalizar_nombre_entidad(c.nombre) == norm_nuevo:
                    flash(f'Ya existe un laboratorio: "{c.nombre}". No se creó duplicado.')
                    return redirect(next_url)
            get_or_create_laboratorio(session, nombre)
            session.commit()
        return redirect(next_url)

    @app.route('/api/laboratorio/<int:lab_id>/usa-packs', methods=['POST'])
    @login_required
    def laboratorio_toggle_packs(lab_id):
        """Togglea Laboratorio.usa_packs (check directo en el ABM de labs).
        Body JSON: {usa_packs: bool}."""
        data = request.get_json(silent=True) or {}
        valor = bool(data.get('usa_packs'))
        with database.get_db() as session:
            lab = session.get(Laboratorio, lab_id)
            if not lab:
                return jsonify({'ok': False, 'error': 'Lab inexistente'}), 404
            lab.usa_packs = valor
            session.commit()
        return jsonify({'ok': True, 'usa_packs': valor})

    @app.route('/laboratorio/<int:lab_id>/edit', methods=['POST'])
    @login_required
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
            lab.usa_packs = request.form.get('usa_packs') == '1'
            session.commit()
        return redirect(url_for('laboratorios_list'))

    @app.route('/laboratorio/<int:lab_id>/delete', methods=['POST'])
    @login_required
    def laboratorio_delete(lab_id):
        next_url = request.form.get('next') or request.referrer or url_for('laboratorios_list')
        from database import (
            AnalisisSesion,
            DescuentoBase,
            EquivalenciaProveedor,
            ExportTemplate,
            LaboratorioDrogueria,
            Modulo,
            OfertaMinimo,
            PedidoBorrador,
        )
        try:
            with database.get_db() as session:
                lab = session.get(Laboratorio, lab_id)
                if not lab:
                    return redirect(next_url)
                # Borrar dependencias HARD (lab_id NOT NULL en estas).
                session.query(OfertaMinimo).filter_by(laboratorio_id=lab_id).delete(synchronize_session=False)
                session.query(DescuentoBase).filter_by(laboratorio_id=lab_id).delete(synchronize_session=False)
                session.query(ExportTemplate).filter_by(laboratorio_id=lab_id).delete(synchronize_session=False)
                session.query(LaboratorioDrogueria).filter_by(laboratorio_id=lab_id).delete(synchronize_session=False)
                session.query(EquivalenciaProveedor).filter_by(laboratorio_id=lab_id).delete(synchronize_session=False)
                # SET NULL en dependencias soft (lab_id nullable).
                session.query(Producto).filter_by(laboratorio_id=lab_id).update({'laboratorio_id': None}, synchronize_session=False)
                session.query(PedidoBorrador).filter_by(laboratorio_id=lab_id).update({'laboratorio_id': None}, synchronize_session=False)
                session.query(Modulo).filter_by(laboratorio_id=lab_id).update({'laboratorio_id': None}, synchronize_session=False)
                session.query(AnalisisSesion).filter_by(laboratorio_id=lab_id).update({'laboratorio_id': None}, synchronize_session=False)
                session.delete(lab)
                session.commit()
        except Exception as e:
            flash(f'No se pudo borrar el laboratorio: {e}')
        return redirect(next_url)

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

    @app.route('/laboratorio/<int:lab_id>/equivalencias/semillar', methods=['POST'])
    def lab_equivalencias_semillar(lab_id):
        """Crea equivalencias retroactivas desde OfertaMinimo existentes.

        Para cada oferta del lab busca el Producto por EAN (directo o alt) y,
        si encuentra match, inserta la EquivalenciaProveedor usando la
        descripción del archivo como clave.
        """
        from database import EquivalenciaProveedor
        from producto_matcher import normalizar_texto
        with database.get_db() as session:
            lab = session.get(Laboratorio, lab_id)
            if not lab:
                flash('Laboratorio no encontrado.', 'error')
                return redirect(url_for('laboratorios_list'))

            ofertas = session.query(OfertaMinimo).filter_by(laboratorio_id=lab_id).all()

            # Carga todos los productos de una vez para no hacer N queries.
            # EANs alternativos viven en producto_codigos_barra (1-a-N) desde
            # el refactor del 2026-05-05.
            from database import ProductoCodigoBarra as _PCB
            todos = session.query(Producto).all()
            prod_by_id = {p.id: p for p in todos}
            ean_a_prod = {}
            for p in todos:
                if p.codigo_barra:
                    ean_a_prod.setdefault(p.codigo_barra.strip(), p)
            for ean, prod_id in session.query(_PCB.codigo_barra, _PCB.producto_id).all():
                if ean and prod_id in prod_by_id:
                    e = ean.strip()
                    if e:
                        ean_a_prod.setdefault(e, prod_by_id[prod_id])

            creadas = 0
            actualizadas = 0
            sin_match = 0
            seen_norms = set()  # evita duplicados dentro del mismo batch
            for o in ofertas:
                desc_orig = (o.descripcion or '').strip()
                if not desc_orig:
                    continue
                prod = ean_a_prod.get((o.ean or '').strip())
                if not prod:
                    sin_match += 1
                    continue
                desc_norm = normalizar_texto(desc_orig)[:200]
                if not desc_norm or desc_norm in seen_norms:
                    continue
                seen_norms.add(desc_norm)
                existente = (session.query(EquivalenciaProveedor)
                             .filter_by(laboratorio_id=lab_id,
                                        descripcion_proveedor_norm=desc_norm)
                             .first())
                if existente:
                    if existente.producto_id != prod.id:
                        existente.producto_id = prod.id
                        actualizadas += 1
                else:
                    session.add(EquivalenciaProveedor(
                        laboratorio_id=lab_id,
                        descripcion_proveedor=desc_orig,
                        descripcion_proveedor_norm=desc_norm,
                        producto_id=prod.id,
                    ))
                    creadas += 1
            session.commit()
        partes = []
        if creadas:
            partes.append(f'{creadas} equivalencias creadas')
        if actualizadas:
            partes.append(f'{actualizadas} actualizadas')
        if sin_match:
            partes.append(f'{sin_match} ofertas sin match de EAN')
        flash(', '.join(partes) or 'Sin cambios.', 'success' if creadas or actualizadas else 'info')
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

    @app.route('/api/laboratorio/<int:lab_id>/descuento-base', methods=['PATCH'])
    @login_required
    def api_lab_descuento_base(lab_id):
        """Edita Laboratorio.descuento_base. Body JSON {descuento_base: 0..100|null}."""
        data = request.get_json(silent=True) or {}
        raw = data.get('descuento_base')
        dto = None
        if raw not in (None, ''):
            try:
                dto = float(raw)
                if dto < 0 or dto > 100:
                    return jsonify({'ok': False, 'error': 'Debe estar entre 0 y 100'}), 400
            except (TypeError, ValueError):
                return jsonify({'ok': False, 'error': 'Valor inválido'}), 400
        with database.get_db() as session:
            lab = session.get(Laboratorio, lab_id)
            if not lab:
                return jsonify({'ok': False, 'error': 'Laboratorio no encontrado'}), 404
            lab.descuento_base = dto
            session.commit()
        return jsonify({'ok': True, 'descuento_base': dto})

    @app.route('/laboratorio/<int:lab_id>/ofertas-minimo/<int:oferta_id>/editar', methods=['PATCH'])
    @login_required
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
                    o.unidades_minima = normalizar_unidades_minima(data['unidades_minima'])
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
                    unidades_minima = normalizar_unidades_minima(it.get('unidades_minima')),
                    descuento_psl   = it.get('descuento_psl'),
                    rentabilidad    = it.get('rentabilidad'),
                    plazo_pago      = it.get('plazo_pago'),
                    grupo_id        = it.get('grupo_id'),
                ))
            session.commit()
            return jsonify({'ok': True, 'guardados': len(items)})

    # ─── Sync local → Render ──────────────────────────────────────────────
    #
    # Flujo C: procesás ofertas en local con el wizard normal, validás
    # contra tus EANs reales, y al final "empujás" el resultado limpio a
    # Render con un click — sin re-correr el wizard ahí. Render recibe via
    # `POST /api/ofertas/sync-from-local` (más abajo) protegido por token.
    #
    # Config:
    #   Local (donde corre el push):
    #     RENDER_BASE_URL=https://farmacia-web-rj1z.onrender.com
    #     PANEL_REMOTO_TOKEN=<random-secret-largo>
    #   Render (donde recibe):
    #     PANEL_REMOTO_TOKEN=<el mismo>

    @app.route('/laboratorio/<int:lab_id>/ofertas-minimo/sync-render', methods=['POST'])
    def lab_ofertas_minimo_sync_render(lab_id):
        """Empuja las OfertaMinimo de este lab a Render via API."""
        import requests
        render_url = os.environ.get('RENDER_BASE_URL', '').rstrip('/')
        token = os.environ.get('PANEL_REMOTO_TOKEN', '')
        if not render_url or not token:
            return jsonify({
                'ok': False,
                'error': 'Sync no configurado. Setear RENDER_BASE_URL y PANEL_REMOTO_TOKEN en env local.'
            }), 400

        with database.get_db() as session:
            lab = session.get(Laboratorio, lab_id)
            if not lab:
                return jsonify({'ok': False, 'error': 'Laboratorio no encontrado'}), 404
            rows = session.query(OfertaMinimo).filter_by(laboratorio_id=lab_id).all()
            if not rows:
                return jsonify({'ok': False, 'error': 'No hay ofertas para sincronizar'}), 400
            payload = {
                'laboratorio_nombre': lab.nombre,
                'ofertas': [{
                    'ean':             r.ean,
                    'codigo':          r.codigo,
                    'descripcion':     r.descripcion,
                    'unidades_minima': r.unidades_minima,
                    'descuento_psl':   float(r.descuento_psl) if r.descuento_psl is not None else None,
                    'rentabilidad':    float(r.rentabilidad)  if r.rentabilidad  is not None else None,
                    'plazo_pago':      r.plazo_pago,
                    'grupo_id':        r.grupo_id,
                    'tipo_descuento':  r.tipo_descuento,
                    'drogueria_id':    r.drogueria_id,  # cuidado: id local — Render hace lookup por nombre o lo ignora
                    'vigencia_desde':  r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                    'vigencia_hasta':  r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                    'observacion':     r.observacion,
                } for r in rows],
            }

        try:
            r = requests.post(
                f'{render_url}/api/ofertas/sync-from-local',
                json=payload,
                headers={'X-Panel-Token': token},
                timeout=60,
            )
        except requests.exceptions.RequestException as e:
            return jsonify({'ok': False, 'error': f'No pude conectar con Render: {e}'}), 502

        if r.status_code != 200:
            return jsonify({
                'ok': False,
                'error': f'Render devolvió {r.status_code}: {r.text[:300]}',
            }), 502
        return jsonify(r.json())

    @app.route('/api/ofertas/sync-from-local', methods=['POST'])
    def api_ofertas_sync_from_local():
        """Recibe el payload del push local y upsertea las ofertas.

        Validación: X-Panel-Token debe coincidir con env PANEL_REMOTO_TOKEN.
        Upsert por (laboratorio_id, ean, drogueria_id, grupo_id):
        si existe actualiza el resto, sino crea.
        """
        token_esperado = os.environ.get('PANEL_REMOTO_TOKEN', '')
        if not token_esperado:
            return jsonify({'ok': False, 'error': 'Sync deshabilitado en este server'}), 403
        if not hmac.compare_digest(request.headers.get('X-Panel-Token', ''), token_esperado):
            return jsonify({'ok': False, 'error': 'Token inválido'}), 401

        body = request.get_json(silent=True) or {}
        lab_nombre = (body.get('laboratorio_nombre') or '').strip()
        ofertas = body.get('ofertas') or []
        if not lab_nombre or not ofertas:
            return jsonify({'ok': False, 'error': 'Faltan laboratorio_nombre u ofertas'}), 400

        from helpers import _normalizar_nombre_entidad
        creadas = 0
        actualizadas = 0
        skip_sin_ean = 0
        errores = []

        with database.get_db() as session:
            # Resolver laboratorio por nombre (normalizado). Si no existe, crearlo.
            norm = _normalizar_nombre_entidad(lab_nombre)
            lab = None
            for c in session.query(Laboratorio).all():
                if _normalizar_nombre_entidad(c.nombre) == norm:
                    lab = c
                    break
            if lab is None:
                lab = Laboratorio(nombre=lab_nombre, activo=True)
                session.add(lab)
                session.flush()

            for o in ofertas:
                ean = (o.get('ean') or '').strip()
                if not ean:
                    skip_sin_ean += 1
                    continue
                key = {
                    'laboratorio_id': lab.id,
                    'ean':            ean,
                    'grupo_id':       o.get('grupo_id'),
                    'drogueria_id':   o.get('drogueria_id'),
                }
                existente = session.query(OfertaMinimo).filter_by(**key).first()

                # Fechas vienen como ISO string ('YYYY-MM-DD') o None.
                vd = vh = None
                try:
                    if o.get('vigencia_desde'): vd = datetime.date.fromisoformat(o['vigencia_desde'])
                    if o.get('vigencia_hasta'): vh = datetime.date.fromisoformat(o['vigencia_hasta'])
                except (ValueError, TypeError) as e:
                    errores.append(f'Fechas inválidas en EAN {ean}: {e}')

                if existente:
                    existente.descripcion     = o.get('descripcion')
                    existente.codigo          = o.get('codigo')
                    existente.unidades_minima = normalizar_unidades_minima(o.get('unidades_minima'))
                    existente.descuento_psl   = o.get('descuento_psl')
                    existente.rentabilidad    = o.get('rentabilidad')
                    existente.plazo_pago      = o.get('plazo_pago')
                    existente.tipo_descuento  = o.get('tipo_descuento')
                    existente.vigencia_desde  = vd
                    existente.vigencia_hasta  = vh
                    existente.observacion     = o.get('observacion')
                    existente.actualizado_en  = now_ar()
                    actualizadas += 1
                else:
                    session.add(OfertaMinimo(
                        laboratorio_id  = lab.id,
                        ean             = ean,
                        descripcion     = o.get('descripcion'),
                        codigo          = o.get('codigo'),
                        unidades_minima = normalizar_unidades_minima(o.get('unidades_minima')),
                        descuento_psl   = o.get('descuento_psl'),
                        rentabilidad    = o.get('rentabilidad'),
                        plazo_pago      = o.get('plazo_pago'),
                        grupo_id        = o.get('grupo_id'),
                        tipo_descuento  = o.get('tipo_descuento'),
                        drogueria_id    = o.get('drogueria_id'),
                        vigencia_desde  = vd,
                        vigencia_hasta  = vh,
                        observacion     = o.get('observacion'),
                    ))
                    creadas += 1
            session.commit()

        return jsonify({
            'ok': True,
            'laboratorio': lab_nombre,
            'creadas': creadas,
            'actualizadas': actualizadas,
            'skip_sin_ean': skip_sin_ean,
            'errores': errores,
        })

    # ─── Pull Render → local ──────────────────────────────────────────────
    #
    # Inverso al sync local→Render. Sirve cuando Render es el source of
    # truth (otro user importó ahi) y querés bajar la data a localhost
    # para verla/usarla. Mismo token PANEL_REMOTO_TOKEN.

    @app.route('/api/ofertas/from-server', methods=['GET'])
    def api_ofertas_from_server():
        """Devuelve las OfertaMinimo de este server. Filtrable por lab.

        Auth: X-Panel-Token contra env PANEL_REMOTO_TOKEN.
        Query params:
            laboratorio_id (int, opcional): filtrar a un lab.
            laboratorio_nombre (str, opcional): si lab_id no se da,
                resuelve por nombre (normalizado).
        """
        token_esperado = os.environ.get('PANEL_REMOTO_TOKEN', '')
        if not token_esperado:
            return jsonify({'ok': False, 'error': 'Sync deshabilitado en este server'}), 403
        if not hmac.compare_digest(request.headers.get('X-Panel-Token', ''), token_esperado):
            return jsonify({'ok': False, 'error': 'Token inválido'}), 401

        lab_id = request.args.get('laboratorio_id', type=int)
        lab_nombre_q = (request.args.get('laboratorio_nombre') or '').strip()

        with database.get_db() as session:
            q = session.query(OfertaMinimo)
            lab = None
            if lab_id:
                lab = session.get(Laboratorio, lab_id)
                if not lab:
                    return jsonify({'ok': False, 'error': 'Laboratorio no encontrado'}), 404
                q = q.filter_by(laboratorio_id=lab.id)
            elif lab_nombre_q:
                from helpers import _normalizar_nombre_entidad
                norm = _normalizar_nombre_entidad(lab_nombre_q)
                for c in session.query(Laboratorio).all():
                    if _normalizar_nombre_entidad(c.nombre) == norm:
                        lab = c
                        break
                if not lab:
                    return jsonify({'ok': False, 'error': f'Laboratorio "{lab_nombre_q}" no encontrado'}), 404
                q = q.filter_by(laboratorio_id=lab.id)

            rows = q.all()
            return jsonify({
                'ok': True,
                'laboratorio_id': lab.id if lab else None,
                'laboratorio_nombre': lab.nombre if lab else None,
                'count': len(rows),
                'ofertas': [{
                    'ean':             r.ean,
                    'codigo':          r.codigo,
                    'descripcion':     r.descripcion,
                    'unidades_minima': r.unidades_minima,
                    'descuento_psl':   float(r.descuento_psl) if r.descuento_psl is not None else None,
                    'rentabilidad':    float(r.rentabilidad)  if r.rentabilidad  is not None else None,
                    'plazo_pago':      r.plazo_pago,
                    'grupo_id':        r.grupo_id,
                    'tipo_descuento':  r.tipo_descuento,
                    'drogueria_id':    r.drogueria_id,
                    'vigencia_desde':  r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                    'vigencia_hasta':  r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                    'observacion':     r.observacion,
                } for r in rows],
            })

    @app.route('/laboratorio/<int:lab_id>/ofertas-minimo/pull-render', methods=['POST'])
    def lab_ofertas_minimo_pull_render(lab_id):
        """Trae las ofertas de Render para este lab (por nombre) y las upsertea
        localmente. Inverso al sync. Mismo upsert key: (lab, ean, grupo, drog).
        """
        import datetime as _dt

        import requests
        render_url = os.environ.get('RENDER_BASE_URL', '').rstrip('/')
        token = os.environ.get('PANEL_REMOTO_TOKEN', '')
        if not render_url or not token:
            return jsonify({
                'ok': False,
                'error': 'Sync no configurado. Setear RENDER_BASE_URL y PANEL_REMOTO_TOKEN en env local.'
            }), 400

        with database.get_db() as session:
            lab = session.get(Laboratorio, lab_id)
            if not lab:
                return jsonify({'ok': False, 'error': 'Laboratorio no encontrado localmente'}), 404
            lab_nombre = lab.nombre

        # GET a Render filtrando por nombre del lab (los IDs no necesariamente
        # coinciden entre local y Render).
        try:
            r = requests.get(
                f'{render_url}/api/ofertas/from-server',
                params={'laboratorio_nombre': lab_nombre},
                headers={'X-Panel-Token': token},
                timeout=60,
            )
        except requests.exceptions.RequestException as e:
            return jsonify({'ok': False, 'error': f'No pude conectar con Render: {e}'}), 502

        if r.status_code != 200:
            return jsonify({
                'ok': False,
                'error': f'Render devolvió {r.status_code}: {r.text[:300]}',
            }), 502
        data = r.json()
        ofertas_remote = data.get('ofertas') or []

        creadas = 0
        actualizadas = 0
        errores = []
        with database.get_db() as session:
            for o in ofertas_remote:
                ean = (o.get('ean') or '').strip()
                if not ean:
                    continue
                key = {
                    'laboratorio_id': lab_id,
                    'ean':            ean,
                    'grupo_id':       o.get('grupo_id'),
                    'drogueria_id':   o.get('drogueria_id'),
                }
                existente = session.query(OfertaMinimo).filter_by(**key).first()
                vd = vh = None
                try:
                    if o.get('vigencia_desde'): vd = _dt.date.fromisoformat(o['vigencia_desde'])
                    if o.get('vigencia_hasta'): vh = _dt.date.fromisoformat(o['vigencia_hasta'])
                except (ValueError, TypeError) as e:
                    errores.append(f'Fechas inválidas en EAN {ean}: {e}')

                if existente:
                    existente.descripcion     = o.get('descripcion')
                    existente.codigo          = o.get('codigo')
                    existente.unidades_minima = normalizar_unidades_minima(o.get('unidades_minima'))
                    existente.descuento_psl   = o.get('descuento_psl')
                    existente.rentabilidad    = o.get('rentabilidad')
                    existente.plazo_pago      = o.get('plazo_pago')
                    existente.tipo_descuento  = o.get('tipo_descuento')
                    existente.vigencia_desde  = vd
                    existente.vigencia_hasta  = vh
                    existente.observacion     = o.get('observacion')
                    existente.actualizado_en  = now_ar()
                    actualizadas += 1
                else:
                    session.add(OfertaMinimo(
                        laboratorio_id  = lab_id,
                        ean             = ean,
                        descripcion     = o.get('descripcion'),
                        codigo          = o.get('codigo'),
                        unidades_minima = normalizar_unidades_minima(o.get('unidades_minima')),
                        descuento_psl   = o.get('descuento_psl'),
                        rentabilidad    = o.get('rentabilidad'),
                        plazo_pago      = o.get('plazo_pago'),
                        grupo_id        = o.get('grupo_id'),
                        tipo_descuento  = o.get('tipo_descuento'),
                        drogueria_id    = o.get('drogueria_id'),
                        vigencia_desde  = vd,
                        vigencia_hasta  = vh,
                        observacion     = o.get('observacion'),
                    ))
                    creadas += 1
            session.commit()

        return jsonify({
            'ok': True,
            'laboratorio': lab_nombre,
            'fetched': len(ofertas_remote),
            'creadas': creadas,
            'actualizadas': actualizadas,
            'errores': errores,
        })

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
