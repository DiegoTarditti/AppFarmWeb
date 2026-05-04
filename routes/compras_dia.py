"""Pantalla "Compra del día" — punto de entrada del flujo de pedidos a Kel/20j.

Muestra la matriz semanal de horarios de reparto de cada droguería + countdown
live al próximo cierre. Desde acá se entra al armado del pedido.

Empleados pueden editar la tabla de horarios; descuentos quedan fuera del scope
de este rol (ver decisión de roles).
"""
from datetime import datetime

from flask import jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import text

import database
from database import ProveedorHorarioReparto, Provider, get_db
from services.horarios import horarios_por_dia, proximo_cierre

DIAS_LABELS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']


def _recalc_item_canonico(it):
    """Canónico = COALESCE(confirmada_obs, revisada_op, 0). Setea estado."""
    if it.cantidad_confirmada_obs is not None:
        rec = it.cantidad_confirmada_obs
    elif it.cantidad_revisada_op is not None:
        rec = it.cantidad_revisada_op
    else:
        rec = 0
    it.cantidad_recibida = max(0, rec)
    if it.cantidad_revisada_op is None and it.cantidad_confirmada_obs is None:
        it.estado = 'PENDIENTE'
    elif it.cantidad_recibida <= 0 and it.cantidad_pedida > 0:
        it.estado = 'NO_VINO'
    else:
        it.estado = 'RECIBIDO'


def _recalc_pedido(p):
    estados = [i.estado for i in p.items]
    if all(e != 'PENDIENTE' for e in estados):
        p.estado = 'CERRADO'
    elif any(e in ('RECIBIDO', 'NO_VINO') for e in estados):
        p.estado = 'RECIBIDO_PARCIAL'
    else:
        p.estado = 'ABIERTO'


def init_app(app):

    @app.route('/pedidos/dia')
    @login_required
    def compras_dia():
        with get_db() as session:
            # Drogerías candidatas: las que tengan al menos 1 horario cargado.
            prov_ids = [r[0] for r in session.query(ProveedorHorarioReparto.proveedor_id)
                        .distinct().all()]
            from sqlalchemy import case as _case
            from database import PedidoEmitido
            provs = (session.query(Provider)
                     .filter(Provider.id.in_(prov_ids), Provider.activo.is_(True))
                     .order_by(
                         _case((Provider.matriz_orden.isnot(None), Provider.matriz_orden), else_=9999),
                         Provider.razon_social)
                     .all())
            # Conteo de pedidos activos (no CERRADO) por droguería para el badge
            from sqlalchemy import func
            pedidos_activos = dict(
                session.query(PedidoEmitido.drogueria_id, func.count(PedidoEmitido.id))
                .filter(PedidoEmitido.estado != 'CERRADO')
                .group_by(PedidoEmitido.drogueria_id)
                .all()
            )
            # Pre-fetch plantillas de pedido por droguería (default primero, sino la primera)
            import json as _json
            from database import Plantilla as _Plantilla
            _plant_rows = (session.query(_Plantilla)
                           .filter(_Plantilla.entidad_tipo == 'drogueria',
                                   _Plantilla.tipo_doc == 'pedido')
                           .order_by(_Plantilla.entidad_id,
                                     _Plantilla.es_default.desc(),
                                     _Plantilla.id)
                           .all())
            _plant_map = {}  # drogueria_id → {nombre, formato, n_campos}
            for _pl in _plant_rows:
                if _pl.entidad_id in _plant_map:
                    continue
                try:
                    _cfg = _json.loads(_pl.config_json or '{}')
                    _cols = _cfg.get('columnas') or _cfg.get('campos') or []
                    _n = len(_cols)
                except Exception:
                    _n = 0
                _plant_map[_pl.entidad_id] = {
                    'nombre': _pl.nombre,
                    'formato': _pl.formato.upper(),
                    'n_campos': _n,
                    'es_default': bool(_pl.es_default),
                }

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
                    'pedidos_activos': pedidos_activos.get(p.id, 0),
                    'plantilla': _plant_map.get(p.id),
                })
            # Drogerías activas que NO tienen horarios todavía — para el dropdown
            # "agregar nueva al flujo".
            drogerias_sin_horarios = (
                session.query(Provider)
                .filter(Provider.tipo == 'drogueria',
                        Provider.activo.is_(True),
                        ~Provider.id.in_(prov_ids if prov_ids else [-1]))
                .order_by(Provider.razon_social).all()
            )
            sin_horarios = [{'id': p.id, 'nombre': p.razon_social}
                            for p in drogerias_sin_horarios]
        return render_template('compras_dia.html',
                               proveedores=proveedores,
                               sin_horarios=sin_horarios,
                               dias=DIAS_LABELS)

    @app.route('/api/drogueria/<int:prov_id>/pedidos-emitidos')
    @login_required
    def api_drogueria_pedidos_emitidos(prov_id):
        from database import PedidoEmitido
        with get_db() as session:
            pedidos = (session.query(PedidoEmitido)
                       .filter_by(drogueria_id=prov_id)
                       .order_by(PedidoEmitido.fecha.desc())
                       .all())
            data = []
            for p in pedidos:
                data.append({
                    'id': p.id,
                    'fecha': p.fecha.strftime('%d/%m/%Y %H:%M') if p.fecha else '—',
                    'estado': p.estado,
                    'total_items': p.total_items,
                    'recibido_por': p.recibido_por,
                    'cargado_por': p.cargado_por,
                    'tiene_factura': False,
                })
        return jsonify({'ok': True, 'pedidos': data})

    @app.route('/api/pedido-emitido/<int:pedido_id>', methods=['DELETE'])
    @login_required
    def api_pedido_emitido_borrar(pedido_id):
        if getattr(current_user, 'rol', None) not in ('dev', 'admin'):
            return jsonify({'ok': False, 'error': 'Sin permisos'}), 403
        from database import PedidoEmitido
        with get_db() as session:
            p = session.get(PedidoEmitido, pedido_id)
            if not p:
                return jsonify({'ok': False, 'error': 'No encontrado'}), 404
            session.delete(p)
            session.commit()
        return jsonify({'ok': True})

    @app.route('/api/pedidos/dia/countdown')
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

    @app.route('/api/pedidos/dia/horarios/<int:proveedor_id>', methods=['GET', 'POST', 'DELETE'])
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

    @app.route('/pedidos/dia/armar')
    @login_required
    def compras_dia_armar():
        """Armado del pedido para una droguería específica.

        Simplificado: solo bajo mínimo en obs_stock + rubro Medicamentos + venta 12m.
        Descuentos por lab/proveedor se evalúan en una fase posterior.
        """
        from sqlalchemy import func

        from database import (
            Laboratorio,
            LaboratorioDrogueria,
            ObsLaboratorio,
            ObsProducto,
            ObsStock,
            ObsVentaDetalle,
            ObsVentaMensual,
            PedidoEmitido,
            PedidoEmitidoItem,
            Producto,
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

            # Ventas 12m por producto (agregado para tabla).
            v12_q = (session.query(
                ObsVentaMensual.producto_observer.label('pid'),
                func.sum(ObsVentaMensual.unidades).label('u12m'),
            ).group_by(ObsVentaMensual.producto_observer).subquery())

            # Ventas ayer y última semana por producto.
            from datetime import date as _date
            from datetime import timedelta as _td
            hoy_d = _date.today()
            _ayer = hoy_d - _td(days=1)
            _semana = hoy_d - _td(days=7)
            _det_rows = session.query(
                ObsVentaDetalle.producto_observer,
                ObsVentaDetalle.fecha_estadistica,
                func.sum(ObsVentaDetalle.cantidad).label('cant'),
            ).filter(ObsVentaDetalle.fecha_estadistica >= _semana)\
             .group_by(ObsVentaDetalle.producto_observer,
                       ObsVentaDetalle.fecha_estadistica).all()
            v24h_rows = {}
            v7d_rows  = {}
            for pid_d, fec, cant in _det_rows:
                v7d_rows[pid_d] = v7d_rows.get(pid_d, 0) + int(cant or 0)
                if fec >= _ayer:
                    v24h_rows[pid_d] = v24h_rows.get(pid_d, 0) + int(cant or 0)

            # Ventas detalladas por mes para alimentar purchase_engine.
            # Construimos el array de 12 meses ventas[0..11] terminando en el mes actual.
            end_month = hoy_d.month
            start_month = ((end_month - 11 - 1) % 12) + 1  # mes-11 (1..12)
            start_year = hoy_d.year if start_month <= end_month else hoy_d.year - 1

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
                ObsProducto.id_tipo_venta_control.label('tvc'),
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

            # Cargar ventas mes-a-mes para los productos en juego.
            ventas_por_pid = {pid: [0]*12 for pid in [r.pid for r in base]}
            if ventas_por_pid:
                rows_vm = (session.query(ObsVentaMensual.producto_observer,
                                          ObsVentaMensual.anio,
                                          ObsVentaMensual.mes,
                                          func.sum(ObsVentaMensual.unidades))
                           .filter(ObsVentaMensual.producto_observer.in_(list(ventas_por_pid.keys())))
                           .group_by(ObsVentaMensual.producto_observer,
                                     ObsVentaMensual.anio, ObsVentaMensual.mes)
                           .all())
                for pid_v, anio, mes, uds in rows_vm:
                    # Slot 0..11: ventas[0]=start_month, ventas[11]=end_month
                    offset = (anio - start_year) * 12 + (mes - start_month)
                    if 0 <= offset <= 11 and pid_v in ventas_por_pid:
                        ventas_por_pid[pid_v][offset] += int(uds or 0)

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

            # Labs cubiertos por esta droguería (LaboratorioDrogueria).
            labs_cubiertos = set(
                r[0] for r in session.query(LaboratorioDrogueria.laboratorio_id)
                .filter(LaboratorioDrogueria.drogueria_id == prov_id).all()
            )
            # Map lab observer → lab local id (por observer_id si está linkeado).
            lab_obs_to_local = dict(
                session.query(Laboratorio.observer_id, Laboratorio.id)
                .filter(Laboratorio.observer_id.isnot(None)).all()
            )
            # Fallback por nombre normalizado: obs_lab_id → local lab id.
            # Permite usar la matriz aunque los labs no tengan observer_id.
            from database import ObsLaboratorio
            from helpers import _normalizar_nombre_entidad as _norm_lab
            obs_lab_norm = {
                r[0]: _norm_lab(r[1])
                for r in session.query(ObsLaboratorio.observer_id,
                                       ObsLaboratorio.descripcion).all()
            }
            local_lab_por_norm = {
                _norm_lab(l.nombre): l.id
                for l in session.query(Laboratorio).all()
            }

            # EANs desde obs_codigos_barras (orden 1 = principal)
            from database import ObsCodigoBarras, OfertaMinimo
            all_pids = [r.pid for r in base]
            eans_armar = {}  # observer_id → ean principal
            if all_pids:
                for ecb in (session.query(ObsCodigoBarras.producto_observer,
                                          ObsCodigoBarras.codigo_barras)
                            .filter(ObsCodigoBarras.producto_observer.in_(all_pids),
                                    ObsCodigoBarras.fecha_baja.is_(None),
                                    ObsCodigoBarras.orden == 1)
                            .all()):
                    eans_armar[ecb.producto_observer] = ecb.codigo_barras

            # Ofertas activas por EAN (TRF): mayor descuento si hay varias.
            all_eans_set = {v for v in eans_armar.values() if v}
            ofertas_por_ean = {}
            if all_eans_set:
                for of in (session.query(OfertaMinimo)
                           .filter(OfertaMinimo.ean.in_(all_eans_set),
                                   OfertaMinimo.activo.is_(True))
                           .all()):
                    um  = int(of.unidades_minima or 1)
                    dto = float(of.descuento_psl or 0)
                    prev = ofertas_por_ean.get(of.ean)
                    if not prev or dto > prev['oferta_dto']:
                        ofertas_por_ean[of.ean] = {'oferta_dto': dto, 'oferta_min': um}

            items = []
            for r in base:
                # Filtro rubro Medicamentos
                sub_id = sub_de_prod.get(r.pid)
                if subrubro_a_rubro.get(sub_id) != 12:
                    continue
                local = local_por_obs.get(r.pid)
                if local and (local['excluido'] or local['no_pedir']):
                    continue
                lab_local_id = (local['lab_local_id'] if local else None) \
                                or lab_obs_to_local.get(r.lab_obs_id) \
                                or local_lab_por_norm.get(obs_lab_norm.get(r.lab_obs_id, ''))
                cubre_lab = lab_local_id in labs_cubiertos
                from purchase_engine import (
                    AVG_DAYS_PER_MONTH,
                    analyze_product,
                    start_month_idx_from_period,
                    tipo_producto,
                )
                u12m_int = int(r.u12m or 0)
                u24h_val = int(v24h_rows.get(r.pid, 0) or 0)
                u7d_val  = int(v7d_rows.get(r.pid, 0) or 0)
                min_actual = int(r.minimo or 0)
                stock_actual = int(r.stock or 0)
                ventas_arr = ventas_por_pid.get(r.pid, [0]*12)

                # Tipo C (crónico) vs N (normal).
                tipo = tipo_producto(ventas_arr)
                # Forecast a 7 días con estacionalidad + prorrateo + slope amortiguado si C.
                sidx = start_month_idx_from_period(start_month, end_month)
                _qty, fcst7, slope, _peak, _low, _comment, sin_mov = analyze_product(
                    ventas_arr, stock_actual, n_days=7, start_month_idx=sidx,
                    data_start_month=start_month, end_month=end_month, tipo=tipo,
                )
                import math
                min_sugerido = int(math.ceil(fcst7)) if fcst7 > 0 else 0
                # Promedio mensual con prorrateo si aplica (igual que analyze_purchase).
                from purchase_engine import FULL_MONTHS, _prorate_partial
                _pp = _prorate_partial(ventas_arr, end_month)
                if _pp is not None:
                    avg_m = (sum(ventas_arr[:FULL_MONTHS]) + _pp) / (FULL_MONTHS + 1)
                else:
                    avg_m = sum(ventas_arr[:FULL_MONTHS]) / FULL_MONTHS if FULL_MONTHS else 0
                avg_diario = avg_m / AVG_DAYS_PER_MONTH if avg_m else 0
                cobertura_d = (round(min_actual / avg_diario)
                               if avg_diario > 0 and min_actual > 0 else None)
                # Sugerencia up/down/ok comparando contra el forecast 7d.
                if u12m_int == 0 or sin_mov:
                    min_sugerencia = None
                elif min_actual == 0 or min_actual < min_sugerido * 0.6:
                    min_sugerencia = 'up'
                elif min_sugerido > 0 and min_actual > min_sugerido * 2.0:
                    min_sugerencia = 'down'
                else:
                    min_sugerencia = 'ok'

                items.append({
                    'pid': r.pid,
                    'producto_id_local': local['id'] if local else None,
                    'desc': r.desc,
                    'lab_nombre': r.lab_nombre or '—',
                    'lab_local_id': lab_local_id,
                    'tvc': r.tvc,
                    'tipo': tipo,  # 'C' crónico, 'N' normal
                    'stock': stock_actual,
                    'minimo': min_actual,
                    'min_sugerido': min_sugerido,
                    'min_sugerencia': min_sugerencia,
                    'cobertura_d': cobertura_d,
                    'u24h': u24h_val,
                    'u7d': u7d_val,
                    'a_pedir': max(0, min_actual - stock_actual),
                    'avg_diario': round(avg_diario, 3),
                    'cubre_lab': cubre_lab,
                    'ean': eans_armar.get(r.pid, ''),
                    **(ofertas_por_ean.get(eans_armar.get(r.pid, ''), {'oferta_dto': None, 'oferta_min': None})),
                })
            items.sort(key=lambda x: (not x['cubre_lab'], x['desc'].lower()))

            cierre = proximo_cierre(session, prov_id)

            # Pendientes de pedidos anteriores a esta drog: pedida > recibida.
            # Incluye estado=NO_VINO y RECIBIDO_PARCIAL (recibida < pedida).
            pendientes_anteriores = []
            ped_rows = (session.query(PedidoEmitidoItem, PedidoEmitido)
                        .join(PedidoEmitido,
                              PedidoEmitido.id == PedidoEmitidoItem.pedido_id)
                        .filter(PedidoEmitido.drogueria_id == prov_id,
                                PedidoEmitidoItem.estado.in_(('NO_VINO', 'RECIBIDO')),
                                PedidoEmitidoItem.cantidad_recibida < PedidoEmitidoItem.cantidad_pedida)
                        .order_by(PedidoEmitido.fecha.desc())
                        .all())
            for it, ped in ped_rows:
                pendiente = max(0, (it.cantidad_pedida or 0) - (it.cantidad_recibida or 0))
                if pendiente <= 0:
                    continue
                lab_id_pend = (lab_obs_to_local.get(it.observer_id)
                               or local_lab_por_norm.get(_norm_lab(it.lab_nombre or '')))
                pendientes_anteriores.append({
                    'item_id': it.id,
                    'pedido_id': ped.id,
                    'pedido_fecha': ped.fecha,
                    'pid': it.observer_id,
                    'producto_id_local': it.producto_id_local,
                    'desc': it.descripcion,
                    'lab_nombre': it.lab_nombre or '—',
                    'pendiente': pendiente,
                    'cubre_lab': lab_id_pend in labs_cubiertos if lab_id_pend else False,
                })

            return render_template('compras_dia_armar.html',
                                   prov=prov, items=items,
                                   total_items=len(items),
                                   cubre=sum(1 for i in items if i['cubre_lab']),
                                   pendientes=sum(1 for i in items if not i['cubre_lab']),
                                   cierre=cierre,
                                   pendientes_anteriores=pendientes_anteriores)

    @app.route('/api/pedidos/dia/buscar-producto')
    @login_required
    def api_compras_dia_buscar_producto():
        """Busca producto ObServer por descripción tokenizada (AND).
        Devuelve datos listos para agregar como fila al armado: stock, mínimo,
        u12m, lab, cubre_lab según la droguería pasada por ?prov.
        """
        from sqlalchemy import and_, func

        from database import (
            Laboratorio,
            LaboratorioDrogueria,
            ObsLaboratorio,
            ObsProducto,
            ObsStock,
            ObsVentaDetalle,
            Producto,
        )
        q = (request.args.get('q') or '').strip()
        prov_id = request.args.get('prov', type=int)
        if len(q) < 2 or not prov_id:
            return jsonify({'ok': True, 'items': []})

        # Tokenizar y armar filtros AND sobre descripcion (case-insensitive).
        tokens = [t for t in q.split() if t]
        with get_db() as session:
            query = (session.query(ObsProducto.observer_id,
                                    ObsProducto.descripcion,
                                    ObsLaboratorio.observer_id.label('lab_obs_id'),
                                    ObsLaboratorio.descripcion.label('lab_nombre'))
                     .outerjoin(ObsLaboratorio,
                                ObsLaboratorio.observer_id == ObsProducto.laboratorio_observer)
                     .filter(ObsProducto.fecha_baja.is_(None)))
            for tok in tokens:
                query = query.filter(ObsProducto.descripcion.ilike(f'%{tok}%'))
            base = query.order_by(ObsProducto.descripcion).limit(20).all()

            obs_ids = [r.observer_id for r in base]
            if not obs_ids:
                return jsonify({'ok': True, 'items': []})

            stock_rows = dict(session.query(
                ObsStock.producto_observer,
                func.sum(ObsStock.stock_actual)
            ).filter(ObsStock.producto_observer.in_(obs_ids))
             .group_by(ObsStock.producto_observer).all())
            min_rows = dict(session.query(
                ObsStock.producto_observer,
                func.sum(ObsStock.minimo)
            ).filter(ObsStock.producto_observer.in_(obs_ids),
                     ObsStock.minimo.isnot(None))
             .group_by(ObsStock.producto_observer).all())
            from datetime import date as _date2
            from datetime import timedelta as _td2
            hoy2   = _date2.today()
            _ayer2 = hoy2 - _td2(days=1)
            _sem2  = hoy2 - _td2(days=7)
            _det2  = session.query(
                ObsVentaDetalle.producto_observer,
                ObsVentaDetalle.fecha_estadistica,
                func.sum(ObsVentaDetalle.cantidad).label('cant'),
            ).filter(ObsVentaDetalle.producto_observer.in_(obs_ids),
                     ObsVentaDetalle.fecha_estadistica >= _sem2)\
             .group_by(ObsVentaDetalle.producto_observer,
                       ObsVentaDetalle.fecha_estadistica).all()
            v24h_rows2 = {}
            v7d_rows2  = {}
            for pid_d, fec, cant in _det2:
                v7d_rows2[pid_d] = v7d_rows2.get(pid_d, 0) + int(cant or 0)
                if fec >= _ayer2:
                    v24h_rows2[pid_d] = v24h_rows2.get(pid_d, 0) + int(cant or 0)

            local_rows = {r[0]: r for r in (
                session.query(Producto.observer_id, Producto.id, Producto.laboratorio_id)
                .filter(Producto.observer_id.in_(obs_ids)).all())}
            lab_obs_to_local = dict(session.query(
                Laboratorio.observer_id, Laboratorio.id
            ).filter(Laboratorio.observer_id.isnot(None)).all())
            from helpers import _normalizar_nombre_entidad as _norm_lab
            local_lab_por_norm = {
                _norm_lab(l.nombre): l.id
                for l in session.query(Laboratorio).all()
            }
            obs_lab_norm = {
                r[0]: _norm_lab(r[1])
                for r in session.query(ObsLaboratorio.observer_id,
                                       ObsLaboratorio.descripcion).all()
            }
            labs_cubiertos = set(r[0] for r in session.query(
                LaboratorioDrogueria.laboratorio_id
            ).filter(LaboratorioDrogueria.drogueria_id == prov_id).all())

            eans_buscar = {}
            if obs_ids:
                for ecb in (session.query(ObsCodigoBarras.producto_observer,
                                          ObsCodigoBarras.codigo_barras)
                            .filter(ObsCodigoBarras.producto_observer.in_(obs_ids),
                                    ObsCodigoBarras.fecha_baja.is_(None),
                                    ObsCodigoBarras.orden == 1)
                            .all()):
                    eans_buscar[ecb.producto_observer] = ecb.codigo_barras

            from database import OfertaMinimo as _OM
            eans_buscar_set = {v for v in eans_buscar.values() if v}
            ofertas_buscar = {}
            if eans_buscar_set:
                for of in (session.query(_OM)
                           .filter(_OM.ean.in_(eans_buscar_set), _OM.activo.is_(True))
                           .all()):
                    um  = int(of.unidades_minima or 1)
                    dto = float(of.descuento_psl or 0)
                    prev = ofertas_buscar.get(of.ean)
                    if not prev or dto > prev['oferta_dto']:
                        ofertas_buscar[of.ean] = {'oferta_dto': dto, 'oferta_min': um}

            items = []
            for r in base:
                local = local_rows.get(r.observer_id)
                lab_local_id = ((local[2] if local else None)
                                or lab_obs_to_local.get(r.lab_obs_id)
                                or local_lab_por_norm.get(obs_lab_norm.get(r.lab_obs_id, '')))
                stock = int(stock_rows.get(r.observer_id, 0) or 0)
                minimo = int(min_rows.get(r.observer_id, 0) or 0)
                ean_b = eans_buscar.get(r.observer_id, '')
                items.append({
                    'pid': r.observer_id,
                    'producto_id_local': local[1] if local else None,
                    'desc': r.descripcion,
                    'lab_nombre': r.lab_nombre or '—',
                    'stock': stock,
                    'minimo': minimo,
                    'u24h': int(v24h_rows2.get(r.observer_id, 0) or 0),
                    'u7d':  int(v7d_rows2.get(r.observer_id, 0) or 0),
                    'a_pedir': max(0, minimo - stock) if minimo else 1,
                    'cubre_lab': lab_local_id in labs_cubiertos,
                    'ean': ean_b,
                    **(ofertas_buscar.get(ean_b, {'oferta_dto': None, 'oferta_min': None})),
                })
            return jsonify({'ok': True, 'items': items})

    @app.route('/compras/labs-drogerias')
    @login_required
    def labs_drogerias_matriz():
        """Matriz lab × droguería: marca por qué drogerías va cada laboratorio.
        Persiste en LaboratorioDrogueria (tabla simple sin descuento).
        """
        from database import Laboratorio, LaboratorioDrogueria, OfertaMinimo
        from sqlalchemy import func
        with get_db() as session:
            labs_q = (session.query(Laboratorio)
                      .filter(Laboratorio.activo.is_(True))
                      .order_by(Laboratorio.nombre).all())
            labs = labs_q
            from sqlalchemy import case
            todas_drogs = (session.query(Provider)
                           .filter(Provider.tipo == 'drogueria')
                           .order_by(
                               case((Provider.matriz_orden.isnot(None), Provider.matriz_orden), else_=9999),
                               Provider.razon_social)
                           .all())
            drogs = [d for d in todas_drogs if d.activo and d.matriz_visible]
            existentes = set(
                (r.laboratorio_id, r.drogueria_id)
                for r in session.query(LaboratorioDrogueria).all()
            )
            # Ofertas importadas por lab: última fecha de actualización
            ofertas_lab = {}
            for r in (session.query(
                    OfertaMinimo.laboratorio_id,
                    func.count(OfertaMinimo.id).label('cnt'),
                    func.max(OfertaMinimo.actualizado_en).label('ultima'))
                    .filter(OfertaMinimo.activo.is_(True))
                    .group_by(OfertaMinimo.laboratorio_id)
                    .all()):
                fecha = r.ultima.date() if r.ultima else None
                ofertas_lab[r.laboratorio_id] = {
                    'count': r.cnt,
                    'fecha': fecha.strftime('%d/%m/%y') if fecha else '',
                }
            labs_data = [{'id': l.id, 'nombre': l.nombre} for l in labs]
            drogs_data = [{'id': d.id, 'nombre': d.razon_social} for d in drogs]
            todas_drogs_data = [{'id': d.id, 'nombre': d.razon_social,
                                  'visible': d.matriz_visible, 'orden': d.matriz_orden,
                                  'activo': d.activo} for d in todas_drogs]
            matriz = {}  # {lab_id: set(drog_ids)}
            for (lab_id, drog_id) in existentes:
                matriz.setdefault(lab_id, set()).add(drog_id)
        return render_template('labs_drogerias.html',
                               labs=labs_data, drogs=drogs_data,
                               todas_drogs=todas_drogs_data,
                               matriz={k: list(v) for k, v in matriz.items()},
                               ofertas_lab=ofertas_lab)

    @app.route('/api/matriz/drog-visible', methods=['POST'])
    @login_required
    def api_matriz_drog_visible():
        """Body: {drog_id, visible}"""
        data = request.get_json(silent=True) or {}
        with get_db() as session:
            drog = session.get(Provider, int(data.get('drog_id') or 0))
            if not drog:
                return jsonify({'ok': False}), 404
            drog.matriz_visible = bool(data.get('visible'))
            session.commit()
        return jsonify({'ok': True})

    @app.route('/api/matriz/drog-config', methods=['POST'])
    @login_required
    def api_matriz_drog_config():
        """Body: [{id, visible, orden}] — actualiza visibilidad y orden de droguerías en la matriz."""
        items = request.get_json(silent=True) or []
        with get_db() as session:
            for item in items:
                try:
                    drog_id = int(item.get('id') or 0)
                except (TypeError, ValueError):
                    continue
                d = session.get(Provider, drog_id)
                if not d:
                    continue
                d.matriz_visible = bool(item.get('visible', True))
                d.matriz_orden   = item.get('orden')
            session.commit()
        return jsonify({'ok': True})

    @app.route('/api/lab-drog/toggle', methods=['POST'])
    @login_required
    def api_lab_drog_toggle():
        """Body: {laboratorio_id, drogueria_id, activo: bool}.
        Crea/borra el match. Idempotente."""
        from database import LaboratorioDrogueria
        data = request.get_json(silent=True) or {}
        try:
            lab_id  = int(data.get('laboratorio_id') or 0)
            drog_id = int(data.get('drogueria_id') or 0)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'IDs inválidos'}), 400
        if not lab_id or not drog_id:
            return jsonify({'ok': False, 'error': 'lab y drog requeridos'}), 400
        activo = bool(data.get('activo'))
        with get_db() as session:
            row = (session.query(LaboratorioDrogueria)
                   .filter_by(laboratorio_id=lab_id, drogueria_id=drog_id).first())
            if activo and not row:
                session.add(LaboratorioDrogueria(
                    laboratorio_id=lab_id, drogueria_id=drog_id))
                session.commit()
            elif not activo and row:
                session.delete(row)
                session.commit()
        return jsonify({'ok': True})

    @app.route('/api/pedidos/dia/emitir', methods=['POST'])
    @login_required
    def api_compras_dia_emitir():
        """Snapshot del armado a PedidoEmitido + PedidoEmitidoItem.
        Body: {prov_id, items: [{observer_id, producto_id_local, descripcion,
                                  lab_nombre, cantidad}], observacion?}
        """
        from database import PedidoEmitido, PedidoEmitidoItem, Producto
        data = request.get_json(silent=True) or {}
        try:
            prov_id = int(data.get('prov_id') or 0)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'prov_id inválido'}), 400
        items = data.get('items') or []
        if not prov_id or not items:
            return jsonify({'ok': False, 'error': 'Faltan datos'}), 400
        with get_db() as session:
            # Pre-cargar observer_ids de los productos locales para auto-bridging
            prod_ids = [int(it['producto_id_local']) for it in items
                        if it.get('producto_id_local') and not it.get('observer_id')]
            obs_by_prod = {}
            if prod_ids:
                obs_by_prod = {
                    r.id: r.observer_id
                    for r in session.query(Producto.id, Producto.observer_id)
                                    .filter(Producto.id.in_(prod_ids),
                                            Producto.observer_id.isnot(None)).all()
                }
            ped = PedidoEmitido(
                drogueria_id=prov_id,
                usuario=getattr(current_user, 'username', None),
                emitido_por=(data.get('emitido_por') or '').strip() or None,
                total_items=len(items),
                total_unidades=sum(int(it.get('cantidad') or 0) for it in items),
                observacion=(data.get('observacion') or None),
            )
            session.add(ped)
            session.flush()
            for it in items:
                cant = int(it.get('cantidad') or 0)
                if cant <= 0:
                    continue
                prod_id_local = it.get('producto_id_local') or None
                observer_id = (it.get('observer_id')
                               or obs_by_prod.get(int(prod_id_local) if prod_id_local else 0)
                               or None)
                _dto = it.get('oferta_dto')
                _min = it.get('oferta_min')
                session.add(PedidoEmitidoItem(
                    pedido_id=ped.id,
                    observer_id=observer_id,
                    producto_id_local=prod_id_local,
                    descripcion=it.get('descripcion') or '',
                    lab_nombre=it.get('lab_nombre') or None,
                    cantidad_pedida=cant,
                    cantidad_recibida=0,
                    estado='PENDIENTE',
                    oferta_dto=float(_dto) if _dto is not None else None,
                    oferta_min=int(_min) if _min is not None else None,
                ))
            session.commit()
            return jsonify({'ok': True, 'pedido_id': ped.id})

    @app.route('/pedidos-emitidos')
    @login_required
    def pedidos_emitidos_list():
        from datetime import timedelta

        from database import PedidoEmitido
        from helpers import now_ar
        es_pedidos = getattr(current_user, 'rol', None) == 'pedidos'
        with get_db() as session:
            q = session.query(PedidoEmitido)
            if es_pedidos:
                # El rol pedidos solo ve los no cerrados de los últimos 30 días.
                desde = now_ar() - timedelta(days=30)
                q = q.filter(PedidoEmitido.estado != 'CERRADO',
                             PedidoEmitido.fecha >= desde)
            peds = q.order_by(PedidoEmitido.fecha.desc()).all()
            data = []
            for p in peds:
                items = p.items
                pendientes  = sum(1 for i in items if i.estado == 'PENDIENTE')
                no_vino     = sum(1 for i in items if i.estado == 'NO_VINO')
                recibidos   = sum(1 for i in items if i.estado == 'RECIBIDO')
                # Etapa Recepción: un operador firmó la recepción
                etapa_recep  = bool(p.recibido_por)
                # Etapa Carga: un operador firmó la carga del XLS Observer
                etapa_carga  = bool(p.cargado_por)
                # Etapa Factura: TODO — por ahora siempre False
                etapa_factura = False
                data.append({
                    'id': p.id,
                    'fecha': p.fecha,
                    'drog': p.drogueria.razon_social if p.drogueria else '—',
                    'drog_id': p.drogueria_id,
                    'total_items': p.total_items,
                    'total_unidades': p.total_unidades,
                    'estado': p.estado,
                    'usuario': p.usuario or '—',
                    'emitido_por': p.emitido_por or '—',
                    'recibido_por': p.recibido_por or None,
                    'cargado_por': p.cargado_por or None,
                    'pendientes': pendientes,
                    'no_vino': no_vino,
                    'recibidos': recibidos,
                    'etapa_recep': etapa_recep,
                    'etapa_carga': etapa_carga,
                    'etapa_factura': etapa_factura,
                })
        return render_template('pedidos_emitidos_list.html', pedidos=data)

    @app.route('/pedidos-emitidos/<int:pedido_id>')
    @login_required
    def pedido_emitido_detalle(pedido_id):
        from database import PedidoEmitido
        with get_db() as session:
            p = session.get(PedidoEmitido, pedido_id)
            if not p:
                return redirect(url_for('pedidos_emitidos_list'))
            items = sorted(p.items, key=lambda i: (i.descripcion or '').lower())
            from database import ObsCodigoBarras, Producto, ProductoCodigoBarra
            # EANs desde ObServer (fuente principal — obs_codigos_barras)
            obs_ids = [i.observer_id for i in items if i.observer_id]
            obs_eans_map = {}  # observer_id → [ean1, ean2, ...] ordered by orden
            if obs_ids:
                for r in (session.query(ObsCodigoBarras.producto_observer,
                                        ObsCodigoBarras.codigo_barras,
                                        ObsCodigoBarras.orden)
                          .filter(ObsCodigoBarras.producto_observer.in_(obs_ids),
                                  ObsCodigoBarras.fecha_baja.is_(None))
                          .order_by(ObsCodigoBarras.producto_observer,
                                    ObsCodigoBarras.orden)
                          .all()):
                    obs_eans_map.setdefault(r.producto_observer, []).append(r.codigo_barras)
            # EANs desde productos locales (fallback / EANs extra mapeados por scanner)
            prod_ids = [i.producto_id_local for i in items if i.producto_id_local]
            local_eans_map = {}   # producto_id_local → [ean, ...]
            if prod_ids:
                for r in session.query(Producto.id, Producto.codigo_barra)\
                                .filter(Producto.id.in_(prod_ids)).all():
                    if r.codigo_barra:
                        local_eans_map.setdefault(r.id, []).append(r.codigo_barra)
                for r in session.query(ProductoCodigoBarra.producto_id,
                                       ProductoCodigoBarra.codigo_barra)\
                                .filter(ProductoCodigoBarra.producto_id.in_(prod_ids)).all():
                    if r.codigo_barra and r.codigo_barra not in local_eans_map.get(r.producto_id, []):
                        local_eans_map.setdefault(r.producto_id, []).append(r.codigo_barra)
            ped_data = {
                'id': p.id, 'fecha': p.fecha, 'estado': p.estado,
                'drog': p.drogueria.razon_social if p.drogueria else '—',
                'usuario': p.usuario or '—', 'observacion': p.observacion or '',
                'total_items': p.total_items, 'total_unidades': p.total_unidades,
                'recibido_por': p.recibido_por,
                'cargado_por': p.cargado_por,
                'etapa_recep': bool(p.recibido_por),
                'etapa_carga': bool(p.cargado_por),
                'etapa_factura': False,  # TODO: vincular con Invoice
                'items': [{
                    'id': i.id, 'observer_id': i.observer_id,
                    'producto_id_local': i.producto_id_local,
                    'ean': (obs_eans_map.get(i.observer_id) or local_eans_map.get(i.producto_id_local) or [''])[0],
                    'eans': obs_eans_map.get(i.observer_id) or local_eans_map.get(i.producto_id_local) or [],
                    'descripcion': i.descripcion, 'lab': i.lab_nombre or '—',
                    'pedida': i.cantidad_pedida,
                    'revisada': i.cantidad_revisada_op,
                    'confirmada': i.cantidad_confirmada_obs,
                    'factura': None,   # TODO: cruzar con InvoiceItem por droguería + fecha
                    'recibida': i.cantidad_recibida,
                    'estado': i.estado,
                } for i in items],
            }
        es_pedidos = getattr(current_user, 'rol', None) == 'pedidos'
        solo_lectura = es_pedidos and ped_data['estado'] == 'CERRADO'
        return render_template('pedido_emitido_detalle.html',
                               pedido=ped_data, solo_lectura=solo_lectura)

    @app.route('/api/pedido-emitido/<int:pedido_id>/recepcion', methods=['POST'])
    @login_required
    def api_pedido_recepcion(pedido_id):
        """Primera revisión manual del operador.

        Body: {items: [{id, revisada}]}. `revisada` es la cantidad que entró
        según el operador (default = cantidad_pedida; si "no entró" → 0).
        Si la confirmación de Observer ya está cargada, NO la pisa.
        """
        from database import PedidoEmitido, PedidoEmitidoItem
        from helpers import now_ar
        data = request.get_json(silent=True) or {}
        items_in = {int(it['id']): it for it in (data.get('items') or []) if it.get('id')}
        with get_db() as session:
            p = session.get(PedidoEmitido, pedido_id)
            if not p:
                return jsonify({'ok': False, 'error': 'Pedido no encontrado'}), 404
            if (getattr(current_user, 'rol', None) == 'pedidos'
                    and p.estado == 'CERRADO'):
                return jsonify({'ok': False, 'error': 'Pedido cerrado, solo lectura'}), 403
            ahora = now_ar()
            for it in p.items:
                if it.id not in items_in:
                    continue
                payload = items_in[it.id]
                try:
                    rev = int(payload.get('revisada') or 0)
                except (TypeError, ValueError):
                    rev = 0
                it.cantidad_revisada_op = max(0, rev)
                it.revisada_en = ahora
                _recalc_item_canonico(it)
            _recalc_pedido(p)
            recibido_por = (data.get('recibido_por') or '').strip()
            if recibido_por and not p.recibido_por:
                p.recibido_por = recibido_por
            session.commit()
        return jsonify({'ok': True})

    @app.route('/api/pedido-emitido/<int:pedido_id>/mapear-ean', methods=['POST'])
    @login_required
    def api_pedido_mapear_ean(pedido_id):
        """Guarda la equivalencia EAN escaneado → producto local.

        Body: {ean, producto_id_local?N, observer_id?N}
        Si solo viene observer_id, busca/crea el Producto local correspondiente.
        Inserta en producto_codigos_barra con fuente='scanner_recep'.
        También actualiza pedido_emitido_item.producto_id_local para todas las
        filas de este pedido con el mismo observer_id.
        """
        from database import ObsCodigoBarras, ObsProducto, PedidoEmitidoItem, Producto, ProductoCodigoBarra
        data = request.get_json(silent=True) or {}
        ean = (data.get('ean') or '').strip()
        try:
            prod_id = int(data.get('producto_id_local') or 0)
        except (TypeError, ValueError):
            prod_id = 0
        try:
            obs_id = int(data.get('observer_id') or 0)
        except (TypeError, ValueError):
            obs_id = 0
        if not ean:
            return jsonify({'ok': False, 'error': 'Faltan datos'}), 400
        with get_db() as session:
            if not prod_id and obs_id:
                # Buscar o crear Producto local vinculado a este observer_id
                prod = session.query(Producto).filter_by(observer_id=obs_id).first()
                if not prod:
                    obs = session.get(ObsProducto, obs_id)
                    if not obs:
                        return jsonify({'ok': False, 'error': 'Producto observer no encontrado'}), 404
                    # EAN principal desde obs_codigos_barras (orden 1)
                    ean_principal_row = (session.query(ObsCodigoBarras.codigo_barras)
                                         .filter_by(producto_observer=obs_id)
                                         .filter(ObsCodigoBarras.fecha_baja.is_(None))
                                         .order_by(ObsCodigoBarras.orden)
                                         .first())
                    ean_principal = ean_principal_row[0] if ean_principal_row else ean
                    prod = Producto(
                        codigo_barra=ean_principal,
                        descripcion=obs.descripcion,
                        observer_id=obs_id,
                    )
                    session.add(prod)
                    session.flush()
                prod_id = prod.id
                # Vincular todas las filas del pedido con este observer_id
                (session.query(PedidoEmitidoItem)
                 .filter_by(pedido_id=pedido_id, observer_id=obs_id)
                 .update({'producto_id_local': prod_id}))
            elif not prod_id:
                return jsonify({'ok': False, 'error': 'Faltan datos'}), 400
            else:
                prod = session.get(Producto, prod_id)
                if not prod:
                    return jsonify({'ok': False, 'error': 'Producto no encontrado'}), 404
            existe = session.query(ProductoCodigoBarra)\
                            .filter_by(producto_id=prod_id, codigo_barra=ean).first()
            if not existe:
                session.add(ProductoCodigoBarra(
                    producto_id=prod_id,
                    codigo_barra=ean,
                    es_principal=False,
                    fuente='scanner_recep',
                ))
            session.commit()
        return jsonify({'ok': True, 'ean': ean, 'producto_id_local': prod_id})

    @app.route('/api/pedido-emitido/<int:pedido_id>/importar-xls', methods=['POST'])
    @login_required
    def api_pedido_importar_xls(pedido_id):
        """Importa XLS de ingreso Observer (mismo formato que ERP upload).

        Cruza por EAN → descripción normalizada. Llena cantidad_confirmada_obs
        en cada ítem matcheado. Guarda cargado_por desde el form.
        """
        import os
        import tempfile

        from data_extract import parse_erp_excel
        from database import PedidoEmitido
        from helpers import now_ar

        f = request.files.get('xls')
        cargado_por = (request.form.get('cargado_por') or '').strip() or None
        if not f:
            return jsonify({'ok': False, 'error': 'Falta el archivo XLS'}), 400

        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
            f.save(tmp.name)
            tmp_path = tmp.name

        try:
            erp_items = parse_erp_excel(tmp_path)
        except Exception as e:
            return jsonify({'ok': False, 'error': f'No se pudo leer el XLS: {e}'}), 400
        finally:
            os.unlink(tmp_path)

        # Índice por EAN y por descripción normalizada
        def _norm(s):
            return ' '.join((s or '').lower().split())

        erp_by_ean  = {}
        erp_by_desc = {}
        for row in erp_items:
            ean = str(row.get('codigo_barra') or '').strip()
            if ean and ean != 'nan':
                erp_by_ean[ean] = row
            desc = _norm(row.get('descripcion', ''))
            if desc:
                erp_by_desc[desc] = row

        with get_db() as session:
            p = session.get(PedidoEmitido, pedido_id)
            if not p:
                return jsonify({'ok': False, 'error': 'Pedido no encontrado'}), 404
            ahora = now_ar()
            matched = 0
            for it in p.items:
                ean = str(it.observer_id or '')
                row = erp_by_ean.get(ean) or erp_by_desc.get(_norm(it.descripcion))
                if row is None:
                    continue
                it.cantidad_confirmada_obs = max(0, int(row.get('cantidad') or 0))
                it.confirmada_en = ahora
                _recalc_item_canonico(it)
                matched += 1
            if cargado_por and not p.cargado_por:
                p.cargado_por = cargado_por
            _recalc_pedido(p)
            session.commit()

        return jsonify({'ok': True, 'matched': matched, 'total': len(erp_items)})

    # ── Export plantilla desde PedidoEmitido ──────────────────────────────
    @app.route('/api/pedido-emitido/<int:pedido_id>/export-plantilla')
    @login_required
    def api_pedido_emitido_export_plantilla(pedido_id):
        """Genera archivo con la plantilla default (tipo_doc=pedido) de la droguería."""
        import json as _json
        from io import BytesIO, StringIO

        from flask import Response, send_file

        from database import PedidoEmitido, Plantilla

        with get_db() as session:
            ped = session.get(PedidoEmitido, pedido_id)
            if not ped:
                return jsonify({'ok': False, 'error': 'Pedido no encontrado'}), 404
            plant = (session.query(Plantilla)
                     .filter_by(entidad_tipo='drogueria', entidad_id=ped.drogueria_id,
                                tipo_doc='pedido', es_default=True)
                     .order_by(Plantilla.formato.desc())   # xlsx (x) antes que txt_fijo (t)
                     .first()
                     or session.query(Plantilla)
                     .filter_by(entidad_tipo='drogueria', entidad_id=ped.drogueria_id,
                                tipo_doc='pedido')
                     .order_by(Plantilla.formato.desc())
                     .first())
            if not plant:
                return jsonify({'ok': False, 'error': 'Sin plantilla de pedido configurada para esta droguería'}), 404

            # Resolver EANs reales desde obs_codigos_barras
            obs_ids = [it.observer_id for it in ped.items if it.observer_id]
            ean_map = {}
            if obs_ids:
                from database import ObsCodigoBarras
                for oid, cb in (session.query(ObsCodigoBarras.producto_observer,
                                              ObsCodigoBarras.codigo_barras)
                                .filter(ObsCodigoBarras.producto_observer.in_(obs_ids),
                                        ObsCodigoBarras.fecha_baja.is_(None))
                                .order_by(ObsCodigoBarras.orden.asc()).all()):
                    if oid not in ean_map and cb:
                        ean_map[oid] = cb

            rows = [{
                'ean': ean_map.get(it.observer_id, it.observer_id or ''),
                'codigo_barra': ean_map.get(it.observer_id, it.observer_id or ''),
                'nombre': it.descripcion,
                'descripcion': it.descripcion,
                'cantidad': it.cantidad_pedida,
                'total': it.cantidad_pedida,
                'lab': it.lab_nombre or '',
            } for it in ped.items]

            try:
                cfg = _json.loads(plant.config_json or '{}')
            except Exception:
                cfg = {}

            import unicodedata as _ud
            _drog_raw = ped.drogueria.razon_social if ped.drogueria else 'drog'
            drog = _ud.normalize('NFKD', _drog_raw).encode('ascii', 'ignore').decode().replace(' ', '_').strip('_') or 'drog'
            from helpers import now_ar as _now
            fecha_str = _now().strftime('%Y%m%d')

            # Alias: los nombres de campo del editor de plantillas → keys del dict de fila
            _FIELD_ALIAS = {
                'codigo_barra': 'ean', 'ean': 'ean',
                'descripcion': 'nombre', 'nombre': 'nombre',
                'cantidad': 'cantidad', 'total': 'cantidad',
                'laboratorio': 'lab', 'lab': 'lab',
            }

            from database import CAMPOS_SISTEMA as _CAMPOS
            _CAMPO_LABEL = dict(_CAMPOS)

            if plant.formato == 'xlsx':
                import openpyxl
                wb = openpyxl.Workbook()
                ws = wb.active
                cols = [c if isinstance(c, str) else c.get('field', '') for c in cfg.get('columnas', [])]
                if not cols:
                    cols = ['codigo_barra', 'descripcion', 'cantidad']
                headers = [_CAMPO_LABEL.get(c, c) for c in cols]
                ws.append(headers)
                for row in rows:
                    ws.append([row.get(_FIELD_ALIAS.get(c, c), '') for c in cols])
                buf = BytesIO()
                wb.save(buf)
                buf.seek(0)
                fname = f'Pedido_{drog}_{fecha_str}.xlsx'
                return send_file(buf, as_attachment=True, download_name=fname,
                                 mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

            # txt_fijo
            campos = sorted(cfg.get('campos', []), key=lambda c: c.get('col_inicio', 0))
            if not campos:
                return jsonify({'ok': False, 'error': 'Plantilla sin campos'}), 400
            line_len = max(c['col_inicio'] + c['longitud'] for c in campos)
            lines = []
            for row in rows:
                line = bytearray(b' ' * line_len)
                for c in campos:
                    cs = c.get('campo_sistema', '')
                    val = str(row.get('ean', '')) if cs == 'codigo_barra' else \
                          str(row.get('nombre', '')) if cs == 'descripcion' else \
                          str(int(row.get('cantidad', 0) or 0)) if cs == 'cantidad' else \
                          (c.get('valor_fijo') or '') if cs == 'fijo' else ''
                    pad = (c.get('relleno') or ' ')[0].encode()
                    lng = c['longitud']
                    ali = c.get('alineacion', 'L')
                    val_b = val.encode('latin-1', errors='replace')[:lng]
                    if ali == 'R':
                        val_b = val_b.rjust(lng, pad)
                    else:
                        val_b = val_b.ljust(lng, pad)
                    line[c['col_inicio']:c['col_inicio'] + lng] = val_b
                lines.append(line.decode('latin-1'))
            ext = plant.formato.replace('txt_fijo', 'txt')
            fname = f'Pedido_{drog}_{fecha_str}.{ext}'
            content = '\r\n'.join(lines)
            return Response(content, mimetype='text/plain',
                            headers={'Content-Disposition': f'attachment; filename="{fname}"'})

    # ── Usuarios de pedidos (operadores) ──────────────────────────────────
    @app.route('/api/usuarios-pedidos', methods=['GET', 'POST'])
    @login_required
    def api_usuarios_pedidos():
        from database import UsuarioPedido
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            nombre = (data.get('nombre') or '').strip()
            if not nombre:
                return jsonify({'ok': False, 'error': 'Nombre requerido'}), 400
            with get_db() as session:
                existente = session.query(UsuarioPedido).filter_by(nombre=nombre).first()
                if existente:
                    if existente.activo:
                        return jsonify({'ok': False, 'error': 'Ya existe ese nombre'}), 400
                    existente.activo = True
                    session.commit()
                    return jsonify({'ok': True, 'id': existente.id, 'nombre': existente.nombre})
                u = UsuarioPedido(nombre=nombre)
                session.add(u)
                try:
                    session.commit()
                except Exception:
                    session.rollback()
                    return jsonify({'ok': False, 'error': 'Ya existe ese nombre'}), 400
                return jsonify({'ok': True, 'id': u.id, 'nombre': u.nombre})
        with get_db() as session:
            users = session.query(UsuarioPedido).filter_by(activo=True)\
                           .order_by(UsuarioPedido.nombre).all()
            return jsonify({'ok': True, 'users': [{'id': u.id, 'nombre': u.nombre} for u in users]})

    @app.route('/api/usuarios-pedidos/<int:uid>', methods=['DELETE'])
    @login_required
    def api_usuarios_pedidos_borrar(uid):
        from database import UsuarioPedido
        with get_db() as session:
            u = session.get(UsuarioPedido, uid)
            if u:
                u.activo = False
                session.commit()
        return jsonify({'ok': True})

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
