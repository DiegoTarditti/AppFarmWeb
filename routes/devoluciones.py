"""Devoluciones de recetas: cuando la OS rechaza una receta presentada,
registramos motivo, destino (a quién devolvérsela) y observaciones.

Flow:
  /devoluciones                  → listado de devoluciones registradas
  /devoluciones/buscar           → form: vendedor + nro presentación + rango fechas
  /devoluciones/buscar (POST)    → tabla con recetas que coinciden + form inline
  /devoluciones/guardar (POST)   → persiste las recetas marcadas
  /devoluciones/motivos          → ABM motivos
  /devoluciones/destinos         → ABM destinos
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

import database
import observer_source


def init_app(app):

    # ──────────────────────────────────────────────────────────────────
    # Atajo: /rend → /devoluciones/buscar (para shortcut de escritorio)
    # ──────────────────────────────────────────────────────────────────
    @app.route('/rend')
    @login_required
    def rend_alias():
        return redirect(url_for('devoluciones_buscar'))

    # ──────────────────────────────────────────────────────────────────
    # Listado
    # ──────────────────────────────────────────────────────────────────
    @app.route('/devoluciones')
    @login_required
    def devoluciones_list():
        q_presentacion = (request.args.get('presentacion') or '').strip()
        q_vendedor = (request.args.get('vendedor') or '').strip()
        q_estado = (request.args.get('estado') or '').strip()
        try:
            page = max(1, int(request.args.get('page', '1')))
        except ValueError:
            page = 1
        per_page = 50
        offset = (page - 1) * per_page

        with database.get_db() as session:
            base = session.query(database.DevolucionReceta).options(
                joinedload(database.DevolucionReceta.motivo),
                joinedload(database.DevolucionReceta.destino),
            )
            if q_presentacion:
                base = base.filter(
                    database.DevolucionReceta.nro_presentacion.ilike(f'%{q_presentacion}%')
                )
            if q_vendedor:
                base = base.filter(
                    database.DevolucionReceta.vendedor_nombre.ilike(f'%{q_vendedor}%')
                )
            if q_estado:
                base = base.filter(database.DevolucionReceta.estado == q_estado)
            total = base.count()
            devoluciones = (base.order_by(database.DevolucionReceta.creado_en.desc())
                            .offset(offset).limit(per_page).all())
            last_page = max(1, (total + per_page - 1) // per_page)

            # Conteos por estado para chips
            from sqlalchemy import func as _f
            cuentas_estado = dict(session.query(
                database.DevolucionReceta.estado,
                _f.count(database.DevolucionReceta.id)
            ).group_by(database.DevolucionReceta.estado).all())

            return render_template('devoluciones_list.html',
                                   devoluciones=devoluciones, total=total,
                                   page=page, last_page=last_page,
                                   q_presentacion=q_presentacion, q_vendedor=q_vendedor,
                                   q_estado=q_estado, cuentas_estado=cuentas_estado)

    # ──────────────────────────────────────────────────────────────────
    # Búsqueda + registro
    # ──────────────────────────────────────────────────────────────────
    @app.route('/devoluciones/buscar', methods=['GET', 'POST'])
    @login_required
    def devoluciones_buscar():
        # Catálogos: obras sociales desde la DB local sincronizada (rápido, no
        # depende de SQL Server estar online en cada búsqueda).
        with database.get_db() as session:
            obras_sociales = [{
                'id_obra_social': o.observer_id,
                'nombre': o.descripcion,
            } for o in (session.query(database.ObsObraSocial)
                        .filter(database.ObsObraSocial.fecha_baja.is_(None))
                        .order_by(database.ObsObraSocial.descripcion).all())]

        # Vendedores: van live a ObServer (no hay tabla local de OperadoresVenta)
        try:
            vendedores = observer_source.listar_vendedores(solo_habilitados=True)
            observer_ok = True
        except Exception as e:
            vendedores = []
            observer_ok = False
            flash(f'ObServer no responde: {e}', 'warning')

        hoy = date.today()

        if request.method == 'GET':
            return render_template('devoluciones_buscar.html',
                                   obras_sociales=obras_sociales,
                                   vendedores=vendedores,
                                   observer_ok=observer_ok,
                                   desde=(hoy - timedelta(days=7)).isoformat(),
                                   hasta=hoy.isoformat(),
                                   nro_presentacion='',
                                   vendedor_id='',
                                   obra_social_id='',
                                   solo_a_cargo_os=False,
                                   resultados=None,
                                   motivos=[], destinos=[])

        # POST: buscar
        vendedor_id = (request.form.get('vendedor_id') or '').strip() or None
        obra_social_id = request.form.get('obra_social_id', type=int) or None
        nro_presentacion = (request.form.get('nro_presentacion') or '').strip() or None
        desde_str = (request.form.get('desde') or '').strip()
        hasta_str = (request.form.get('hasta') or '').strip()
        solo_a_cargo_os = request.form.get('solo_a_cargo_os') == '1'

        if not vendedor_id and not obra_social_id:
            flash('Seleccioná al menos un vendedor o una obra social.', 'error')
            return redirect(url_for('devoluciones_buscar'))
        try:
            desde = datetime.strptime(desde_str, '%Y-%m-%d').date()
            hasta = datetime.strptime(hasta_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Fechas inválidas.', 'error')
            return redirect(url_for('devoluciones_buscar'))
        if desde > hasta:
            flash('"Desde" no puede ser posterior a "hasta".', 'error')
            return redirect(url_for('devoluciones_buscar'))

        try:
            resultados = observer_source.buscar_recetas(
                vendedor_uuid=vendedor_id,
                obra_social_id=obra_social_id,
                desde=desde, hasta=hasta,
                solo_a_cargo_os=solo_a_cargo_os,
            )
        except Exception as e:
            flash(f'Error consultando ObServer: {e}', 'error')
            return redirect(url_for('devoluciones_buscar'))

        # Snapshots de labels
        vendedor_nombre = (next((v['nombre'] for v in vendedores
                                 if v['id_usuario'] == vendedor_id), None)
                           if vendedor_id else None)
        os_nombre = (next((o['nombre'] for o in obras_sociales
                          if o['id_obra_social'] == obra_social_id), None)
                     if obra_social_id else None)

        # Cargar catálogos para el form inline
        with database.get_db() as session:
            motivos = (session.query(database.MotivoDevolucion)
                       .filter_by(activo=True)
                       .order_by(database.MotivoDevolucion.nombre).all())
            destinos = (session.query(database.DestinoDevolucion)
                        .filter_by(activo=True)
                        .order_by(database.DestinoDevolucion.nombre).all())
            # IDs de operaciones ya devueltas (para mostrar badge)
            ya_devueltas = set()
            ids = [r['id_operacion'] for r in resultados]
            if ids:
                ya_devueltas = {oid for (oid,) in
                                session.query(database.DevolucionReceta.id_operacion_observer)
                                .filter(database.DevolucionReceta.id_operacion_observer.in_(ids))
                                .all()}

        return render_template('devoluciones_buscar.html',
                               obras_sociales=obras_sociales,
                               vendedores=vendedores,
                               observer_ok=True,
                               desde=desde.isoformat(), hasta=hasta.isoformat(),
                               nro_presentacion=nro_presentacion or '',
                               vendedor_id=vendedor_id or '',
                               vendedor_nombre=vendedor_nombre,
                               obra_social_id=obra_social_id or '',
                               os_nombre=os_nombre,
                               solo_a_cargo_os=solo_a_cargo_os,
                               resultados=resultados,
                               motivos=motivos, destinos=destinos,
                               ya_devueltas=ya_devueltas)

    @app.route('/devoluciones/guardar', methods=['POST'])
    @login_required
    def devoluciones_guardar():
        vendedor_id = (request.form.get('vendedor_id') or '').strip() or None
        vendedor_nombre = (request.form.get('vendedor_nombre') or '').strip() or None
        nro_presentacion = (request.form.get('nro_presentacion') or '').strip() or None
        # IDs marcados como devolución
        marcados = request.form.getlist('marcar')
        if not marcados:
            flash('No marcaste ninguna receta.', 'error')
            return redirect(url_for('devoluciones_buscar'))

        creador = getattr(current_user, 'email', None) or getattr(current_user, 'id', None)

        # Cache de vendedores para resolver nombre por UUID (1 sola query a ObServer)
        vendedor_name_by_id = {}
        try:
            for v in observer_source.listar_vendedores(solo_habilitados=False):
                vendedor_name_by_id[v['id_usuario']] = v['nombre']
        except Exception:
            pass  # si ObServer no responde, guardamos solo UUID sin nombre

        n_creadas = 0
        errores = []
        with database.get_db() as session:
            for op_id_str in marcados:
                try:
                    op_id = int(op_id_str)
                except ValueError:
                    continue
                motivo_id = request.form.get(f'motivo_{op_id}', type=int)
                destino_vendedor_id = (request.form.get(f'destino_vendedor_{op_id}') or '').strip() or None
                destino_vendedor_nombre = vendedor_name_by_id.get(destino_vendedor_id) if destino_vendedor_id else None
                obs = (request.form.get(f'obs_{op_id}') or '').strip() or None
                if not motivo_id:
                    errores.append(f'Receta #{op_id}: motivo es obligatorio.')
                    continue
                # Snapshot de datos de la receta
                fop = request.form.get(f'fop_{op_id}')
                fop_dt = None
                if fop:
                    try:
                        fop_dt = datetime.fromisoformat(fop)
                    except ValueError:
                        pass
                os_nombre = (request.form.get(f'os_{op_id}') or '').strip() or None
                imp_total = request.form.get(f'imp_{op_id}')
                imp_os = request.form.get(f'imp_os_{op_id}')
                try:
                    imp_total = Decimal(imp_total) if imp_total else None
                except Exception:
                    imp_total = None
                try:
                    imp_os = Decimal(imp_os) if imp_os else None
                except Exception:
                    imp_os = None

                dev = database.DevolucionReceta(
                    nro_presentacion=nro_presentacion,
                    vendedor_observer_id=vendedor_id,
                    vendedor_nombre=vendedor_nombre,
                    id_operacion_observer=op_id,
                    fecha_operacion=fop_dt,
                    obra_social_nombre=os_nombre,
                    importe_total=imp_total,
                    importe_a_cargo_os=imp_os,
                    motivo_id=motivo_id,
                    destino_vendedor_observer_id=destino_vendedor_id,
                    destino_vendedor_nombre=destino_vendedor_nombre,
                    observaciones=obs,
                    creado_por=str(creador) if creador else None,
                )
                session.add(dev)
                n_creadas += 1
            if n_creadas:
                session.commit()

        if errores:
            for e in errores:
                flash(e, 'error')
        if n_creadas:
            flash(f'{n_creadas} devolución(es) registrada(s).', 'success')
        return redirect(url_for('devoluciones_list'))

    @app.route('/devoluciones/<int:id>/estado', methods=['POST'])
    @login_required
    def devolucion_cambiar_estado(id):
        nuevo = (request.form.get('estado') or '').strip()
        if nuevo not in ('pendiente', 'resuelta', 'descartada'):
            flash('Estado inválido.', 'error')
            return redirect(url_for('devoluciones_list'))
        nota = (request.form.get('nota_cierre') or '').strip() or None
        with database.get_db() as session:
            dev = session.get(database.DevolucionReceta, id)
            if not dev:
                flash('Devolución no encontrada.', 'error')
                return redirect(url_for('devoluciones_list'))
            dev.estado = nuevo
            dev.nota_cierre = nota
            if nuevo == 'pendiente':
                dev.cerrada_en = None
                dev.cerrada_por = None
            else:
                from datetime import datetime as _dt
                dev.cerrada_en = database.now_ar()
                dev.cerrada_por = (getattr(current_user, 'email', None)
                                   or str(getattr(current_user, 'id', '') or ''))
            session.commit()
            flash(f'Devolución #{id} → {nuevo}.', 'success')
        return redirect(url_for('devoluciones_list'))


    @app.route('/devoluciones/<int:id>/eliminar', methods=['POST'])
    @login_required
    def devolucion_eliminar(id):
        with database.get_db() as session:
            dev = session.get(database.DevolucionReceta, id)
            if dev:
                session.delete(dev)
                session.commit()
                flash('Devolución eliminada.', 'success')
        return redirect(url_for('devoluciones_list'))

    # ──────────────────────────────────────────────────────────────────
    # ABM Motivos
    # ──────────────────────────────────────────────────────────────────
    @app.route('/devoluciones/motivos', methods=['GET', 'POST'])
    @login_required
    def devoluciones_motivos():
        with database.get_db() as session:
            if request.method == 'POST':
                nombre = (request.form.get('nombre') or '').strip()
                if not nombre:
                    flash('Nombre obligatorio.', 'error')
                else:
                    existe = session.query(database.MotivoDevolucion).filter_by(nombre=nombre).first()
                    if existe:
                        flash('Ya existe un motivo con ese nombre.', 'error')
                    else:
                        session.add(database.MotivoDevolucion(nombre=nombre))
                        session.commit()
                        flash('Motivo creado.', 'success')
                return redirect(url_for('devoluciones_motivos'))
            motivos = (session.query(database.MotivoDevolucion)
                       .order_by(database.MotivoDevolucion.activo.desc(),
                                 database.MotivoDevolucion.nombre).all())
        return render_template('devoluciones_motivos.html', motivos=motivos)

    @app.route('/devoluciones/motivos/<int:id>/toggle', methods=['POST'])
    @login_required
    def devoluciones_motivo_toggle(id):
        with database.get_db() as session:
            m = session.get(database.MotivoDevolucion, id)
            if m:
                m.activo = not m.activo
                session.commit()
        return redirect(url_for('devoluciones_motivos'))

    @app.route('/devoluciones/motivos/<int:id>/eliminar', methods=['POST'])
    @login_required
    def devoluciones_motivo_eliminar(id):
        with database.get_db() as session:
            usado = (session.query(database.DevolucionReceta)
                     .filter_by(motivo_id=id).first())
            if usado:
                flash('No se puede eliminar: el motivo está en uso. Desactivalo.', 'error')
                return redirect(url_for('devoluciones_motivos'))
            m = session.get(database.MotivoDevolucion, id)
            if m:
                session.delete(m)
                session.commit()
                flash('Motivo eliminado.', 'success')
        return redirect(url_for('devoluciones_motivos'))

    # ──────────────────────────────────────────────────────────────────
    # ABM Destinos
    # ──────────────────────────────────────────────────────────────────
    @app.route('/devoluciones/destinos', methods=['GET', 'POST'])
    @login_required
    def devoluciones_destinos():
        with database.get_db() as session:
            if request.method == 'POST':
                nombre = (request.form.get('nombre') or '').strip()
                if not nombre:
                    flash('Nombre obligatorio.', 'error')
                else:
                    existe = session.query(database.DestinoDevolucion).filter_by(nombre=nombre).first()
                    if existe:
                        flash('Ya existe un destino con ese nombre.', 'error')
                    else:
                        session.add(database.DestinoDevolucion(nombre=nombre))
                        session.commit()
                        flash('Destino creado.', 'success')
                return redirect(url_for('devoluciones_destinos'))
            destinos = (session.query(database.DestinoDevolucion)
                        .order_by(database.DestinoDevolucion.activo.desc(),
                                  database.DestinoDevolucion.nombre).all())
        return render_template('devoluciones_destinos.html', destinos=destinos)

    @app.route('/devoluciones/destinos/<int:id>/toggle', methods=['POST'])
    @login_required
    def devoluciones_destino_toggle(id):
        with database.get_db() as session:
            d = session.get(database.DestinoDevolucion, id)
            if d:
                d.activo = not d.activo
                session.commit()
        return redirect(url_for('devoluciones_destinos'))

    @app.route('/devoluciones/destinos/<int:id>/eliminar', methods=['POST'])
    @login_required
    def devoluciones_destino_eliminar(id):
        with database.get_db() as session:
            usado = (session.query(database.DevolucionReceta)
                     .filter_by(destino_id=id).first())
            if usado:
                flash('No se puede eliminar: el destino está en uso. Desactivalo.', 'error')
                return redirect(url_for('devoluciones_destinos'))
            d = session.get(database.DestinoDevolucion, id)
            if d:
                session.delete(d)
                session.commit()
                flash('Destino eliminado.', 'success')
        return redirect(url_for('devoluciones_destinos'))
