"""Pantalla "Compra del día" — punto de entrada del flujo de pedidos a Kel/20j.

Muestra la matriz semanal de horarios de reparto de cada droguería + countdown
live al próximo cierre. Desde acá se entra al armado del pedido.

Empleados pueden editar la tabla de horarios; descuentos quedan fuera del scope
de este rol (ver decisión de roles).
"""
from datetime import datetime

from flask import jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from sqlalchemy import text

import database
from database import Provider, ProveedorHorarioReparto, get_db
from services.horarios import horarios_por_dia, proximo_cierre


DIAS_LABELS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']


def init_app(app):

    @app.route('/compras/dia')
    @login_required
    def compras_dia():
        with get_db() as session:
            # Drogerías candidatas: las que tengan al menos 1 horario cargado.
            prov_ids = [r[0] for r in session.query(ProveedorHorarioReparto.proveedor_id)
                        .distinct().all()]
            provs = (session.query(Provider)
                     .filter(Provider.id.in_(prov_ids), Provider.activo.is_(True))
                     .order_by(Provider.razon_social).all())
            proveedores = []
            for p in provs:
                matriz = horarios_por_dia(session, p.id)  # {0: ['07:10', ...], ...}
                # ordenado por dia_semana
                matriz_ordenada = [matriz.get(d, []) for d in range(7)]
                cierre = proximo_cierre(session, p.id)
                proveedores.append({
                    'id': p.id,
                    'nombre': p.razon_social,
                    'horarios_por_dia': matriz_ordenada,
                    'proximo_cierre': cierre,
                })
        return render_template('compras_dia.html',
                               proveedores=proveedores,
                               dias=DIAS_LABELS)

    @app.route('/api/compras/dia/countdown')
    @login_required
    def api_compras_dia_countdown():
        """Devuelve el próximo cierre + segundos faltantes para cada drog activa.
        Lo consume el JS del front para refrescar el countdown sin recargar la página.
        """
        ahora = datetime.now()
        out = {}
        with get_db() as session:
            prov_ids = [r[0] for r in session.query(ProveedorHorarioReparto.proveedor_id)
                        .distinct().all()]
            for pid in prov_ids:
                cierre = proximo_cierre(session, pid, ahora=ahora)
                if cierre:
                    out[pid] = {
                        'fecha': cierre['fecha'].isoformat(),
                        'falta_segundos': cierre['falta_segundos'],
                        'hora_str': cierre['hora_str'],
                    }
        return jsonify({'ok': True, 'now': ahora.isoformat(), 'cierres': out})

    @app.route('/api/compras/dia/horarios/<int:proveedor_id>', methods=['GET', 'POST', 'DELETE'])
    @login_required
    def api_horarios_crud(proveedor_id):
        """CRUD básico para horarios de un proveedor.

        GET    → lista los horarios.
        POST   → agrega un slot. body: {dia_semana: 0-6, hora: 'HH:MM'}.
        DELETE → borra slot. body: {id: <slot_id>}.
        """
        with get_db() as session:
            p = session.get(Provider, proveedor_id)
            if not p:
                return jsonify({'ok': False, 'error': 'Proveedor no encontrado'}), 404

            if request.method == 'GET':
                rows = (session.query(ProveedorHorarioReparto)
                        .filter_by(proveedor_id=proveedor_id, activo=True)
                        .order_by(ProveedorHorarioReparto.dia_semana,
                                  ProveedorHorarioReparto.hora).all())
                return jsonify({'ok': True, 'horarios': [{
                    'id': r.id,
                    'dia_semana': r.dia_semana,
                    'hora': r.hora,
                } for r in rows]})

            if request.method == 'POST':
                data = request.get_json(silent=True) or {}
                try:
                    dia = int(data.get('dia_semana', -1))
                    hora = (data.get('hora') or '').strip()
                except (ValueError, TypeError):
                    return jsonify({'ok': False, 'error': 'Datos inválidos'}), 400
                if dia < 0 or dia > 6 or len(hora) != 5 or hora[2] != ':':
                    return jsonify({'ok': False, 'error': 'Día (0-6) y hora HH:MM requeridos'}), 400
                # Idempotente: si ya existe ese slot, no rompe.
                ya = (session.query(ProveedorHorarioReparto.id)
                      .filter_by(proveedor_id=proveedor_id, dia_semana=dia, hora=hora)
                      .first())
                if ya:
                    return jsonify({'ok': True, 'id': ya[0], 'duplicado': True})
                row = ProveedorHorarioReparto(
                    proveedor_id=proveedor_id, dia_semana=dia, hora=hora, activo=True
                )
                session.add(row)
                session.commit()
                return jsonify({'ok': True, 'id': row.id})

            # DELETE
            data = request.get_json(silent=True) or {}
            try:
                slot_id = int(data.get('id') or 0)
            except (ValueError, TypeError):
                return jsonify({'ok': False, 'error': 'id inválido'}), 400
            row = session.get(ProveedorHorarioReparto, slot_id)
            if row and row.proveedor_id == proveedor_id:
                session.delete(row)
                session.commit()
            return jsonify({'ok': True})

    @app.route('/compras/dia/armar')
    @login_required
    def compras_dia_armar():
        """Armado del pedido para una droguería específica.

        Simplificado: solo bajo mínimo en obs_stock + rubro Medicamentos + venta 12m.
        Descuentos por lab/proveedor se evalúan en una fase posterior.
        """
        from sqlalchemy import func
        from database import (
            ObsLaboratorio, ObsProducto, ObsStock, ObsVentaMensual, Producto,
        )

        prov_id = request.args.get('prov', type=int)
        if not prov_id:
            return redirect(url_for('compras_dia'))

        with get_db() as session:
            prov = session.get(Provider, prov_id)
            if not prov:
                return redirect(url_for('compras_dia'))

            # Universo: bajo mínimo en obs_stock + rubro Medicamentos (12).
            stock_q = (session.query(
                ObsStock.producto_observer.label('pid'),
                func.sum(ObsStock.stock_actual).label('stock'),
                func.sum(ObsStock.minimo).label('minimo'),
            ).filter(ObsStock.minimo.isnot(None), ObsStock.minimo > 0)
              .group_by(ObsStock.producto_observer).subquery())

            # Ventas 12m por producto.
            v12_q = (session.query(
                ObsVentaMensual.producto_observer.label('pid'),
                func.sum(ObsVentaMensual.unidades).label('u12m'),
            ).group_by(ObsVentaMensual.producto_observer).subquery())

            # Cleanup temporal de exclusión.
            session.execute(text("""
                UPDATE productos p SET excluido_armado_actual = FALSE
                WHERE p.excluido_armado_actual = TRUE
                  AND p.observer_id IN (
                    SELECT s.producto_observer
                      FROM obs_stock s
                     WHERE s.minimo IS NOT NULL AND s.stock_actual > s.minimo
                  )
            """))
            session.commit()

            base = (session.query(
                ObsProducto.observer_id.label('pid'),
                ObsProducto.descripcion.label('desc'),
                ObsLaboratorio.observer_id.label('lab_obs_id'),
                ObsLaboratorio.descripcion.label('lab_nombre'),
                stock_q.c.stock,
                stock_q.c.minimo,
                func.coalesce(v12_q.c.u12m, 0).label('u12m'),
            )
            .join(stock_q, stock_q.c.pid == ObsProducto.observer_id)
            .outerjoin(ObsLaboratorio,
                       ObsLaboratorio.observer_id == ObsProducto.laboratorio_observer)
            .outerjoin(v12_q, v12_q.c.pid == ObsProducto.observer_id)
            .filter(ObsProducto.fecha_baja.is_(None))
            .filter(stock_q.c.stock < stock_q.c.minimo)
            .filter(ObsProducto.subrubro_observer.isnot(None))  # filtro suave: que tenga subrubro
            ).all()

            # Filtro rubro=Medicamentos (12). Lo aplicamos en Python por simplicidad
            # (los rubros viven en obs_subrubros.rubro_observer).
            from database import ObsSubrubro
            subrubro_a_rubro = dict(session.query(ObsSubrubro.observer_id,
                                                   ObsSubrubro.rubro_observer).all())
            obs_pids = [r.pid for r in base]
            sub_de_prod = {}
            if obs_pids:
                rows = (session.query(ObsProducto.observer_id,
                                       ObsProducto.subrubro_observer)
                        .filter(ObsProducto.observer_id.in_(obs_pids)).all())
                sub_de_prod = dict(rows)

            # Resolver Producto local + flags excluido / no_pedir.
            local_por_obs = {}
            if obs_pids:
                rows = (session.query(Producto.observer_id, Producto.id,
                                       Producto.excluido_armado_actual,
                                       Producto.no_pedir, Producto.laboratorio_id)
                        .filter(Producto.observer_id.in_(obs_pids)).all())
                local_por_obs = {r[0]: {
                    'id': r[1], 'excluido': r[2], 'no_pedir': r[3],
                    'lab_local_id': r[4],
                } for r in rows}

            items = []
            for r in base:
                # Filtro rubro Medicamentos
                sub_id = sub_de_prod.get(r.pid)
                if subrubro_a_rubro.get(sub_id) != 12:
                    continue
                local = local_por_obs.get(r.pid)
                if local and (local['excluido'] or local['no_pedir']):
                    continue
                items.append({
                    'pid': r.pid,
                    'producto_id_local': local['id'] if local else None,
                    'desc': r.desc,
                    'lab_nombre': r.lab_nombre or '—',
                    'stock': int(r.stock or 0),
                    'minimo': int(r.minimo or 0),
                    'u12m': int(r.u12m or 0),
                    'a_pedir': max(0, int(r.minimo or 0) - int(r.stock or 0)),
                })
            items.sort(key=lambda x: x['desc'].lower())

            cierre = proximo_cierre(session, prov_id)

            return render_template('compras_dia_armar.html',
                                   prov=prov, items=items,
                                   total_items=len(items),
                                   cierre=cierre)

    @app.route('/api/producto/<int:prod_id>/excluir', methods=['POST'])
    @login_required
    def api_producto_excluir(prod_id):
        """Body: {modo: 'temporal'|'permanente'}.
        - temporal: excluido_armado_actual=TRUE (se autodescactiva cuando stock>min).
        - permanente: no_pedir=TRUE (manual reactivación).
        """
        from database import Producto
        data = request.get_json(silent=True) or {}
        modo = (data.get('modo') or 'temporal').strip()
        with get_db() as session:
            p = session.get(Producto, prod_id)
            if not p:
                return jsonify({'ok': False, 'error': 'Producto no encontrado'}), 404
            if modo == 'permanente':
                p.no_pedir = True
            else:
                p.excluido_armado_actual = True
            session.commit()
        return jsonify({'ok': True})
