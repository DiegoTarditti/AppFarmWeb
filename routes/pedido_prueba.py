"""Pedido prueba — pantalla de planificacion grande con estacionalidad.

A diferencia de /pedido/dia (reposicion tactica con u3m/90), aca usamos
u12m/365 como base "neutra" + indice estacional del mes objetivo +
cobertura en dias. Pensado para pedidos grandes anticipados.

Flujo: usuario elige un laboratorio → pantalla popula con todos los
productos del lab con ventas en 12m → cada producto muestra:
- Sugerido del dia (calculado con la matriz REPOSICION en vivo)
- Sugerido prueba (este modulo, con escenario aplicable)
- Diferencia
- Flag activo si aplica
- Drawer al click con desglose + sliders para editar escenario producto.
"""

import json
import os
from datetime import datetime as _dt

from flask import jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import desc, func

from database import (
    EstacionalidadEscenario,
    ObsLaboratorio,
    ObsNombreDroga,
    ObsProducto,
    ObsStock,
    ObsVentaMensual,
    Pedido,
    PedidoItem,
    ProductoFlag,
    TipoPedidoConfig,
    get_db,
)
from services.pedido_estacional import (
    MESES_ES,
    calcular_sugerido_estacional,
)


def _u12m_por_producto(session, producto_ids, id_farmacia):
    """Suma u12m por producto en un solo query."""
    if not producto_ids:
        return {}
    rows = (session.query(
        ObsVentaMensual.producto_observer,
        func.sum(ObsVentaMensual.unidades).label('u12m'),
    )
    .filter(ObsVentaMensual.producto_observer.in_(producto_ids),
            ObsVentaMensual.id_farmacia == id_farmacia)
    .group_by(ObsVentaMensual.producto_observer)
    .all())
    return {r.producto_observer: float(r.u12m or 0) for r in rows}


def _stock_por_producto(session, producto_ids, id_farmacia):
    if not producto_ids:
        return {}
    rows = (session.query(ObsStock.producto_observer, ObsStock.stock_actual,
                          ObsStock.minimo)
            .filter(ObsStock.producto_observer.in_(producto_ids),
                    ObsStock.id_farmacia == id_farmacia)
            .all())
    return {r.producto_observer: {'stock': int(r.stock_actual or 0),
                                  'minimo': int(r.minimo or 0)} for r in rows}


def _sugerido_dia_actual(session, producto, u12m, stock_actual, minimo):
    """Replica simplificada de la logica de /pedido/dia con la matriz
    REPOSICION: usa daily_rate basado en u3m (no u12m). Devuelve int.
    Si no se puede calcular, devuelve None."""
    try:
        from services.calculo_pedido import calcular_a_pedir, cargar_config

        cfg = cargar_config('REPOSICION') or {}
        # u3m: ventas ultimos 3 meses → daily_rate aprox.
        from datetime import date as _date
        hoy = _date.today()
        # 3 meses atras: aproximacion por año/mes.
        anio_3m, mes_3m = hoy.year, hoy.month - 3
        while mes_3m <= 0:
            mes_3m += 12
            anio_3m -= 1
        u3m = (session.query(func.sum(ObsVentaMensual.unidades))
               .filter(ObsVentaMensual.producto_observer == producto.observer_id,
                       (ObsVentaMensual.anio * 100 + ObsVentaMensual.mes)
                       >= (anio_3m * 100 + mes_3m))
               .scalar()) or 0
        daily_rate = float(u3m) / 90.0
        result = calcular_a_pedir(cfg, {
            'daily_rate': daily_rate,
            'min_efectivo': minimo,
            'factor_h': 1.0,
            'cubrir_dias': 30,
            'stock_actual': stock_actual,
            'cantidad_reposicion_fija': None,
            'pack_quantity': None,
            'u12m': u12m,
            'sin_mov': u12m == 0,
        })
        return int(result.get('a_pedir', 0))
    except Exception:
        return None


def init_app(app):

    @app.route('/pedido/prueba')
    @login_required
    def pedido_prueba():
        """Pantalla principal. Arranca con selector de lab vacio."""
        with get_db() as session:
            labs = (session.query(ObsLaboratorio.observer_id,
                                  ObsLaboratorio.descripcion)
                    .order_by(ObsLaboratorio.descripcion)
                    .all())
            labs_list = [{'id': l.observer_id, 'nombre': l.descripcion}
                         for l in labs]
        return render_template('pedido_prueba.html',
                               labs=labs_list,
                               meses_es=MESES_ES)

    @app.route('/api/pedido-prueba/calcular', methods=['POST'])
    @login_required
    def api_pedido_prueba_calcular():
        """Devuelve productos del lab + sugeridos (prueba y dia) + flag."""
        payload = request.get_json(silent=True) or {}
        try:
            lab_id = int(payload.get('lab_id') or 0)
        except (TypeError, ValueError):
            return jsonify({'error': 'lab_id invalido'}), 400
        if not lab_id:
            return jsonify({'error': 'lab_id requerido'}), 400

        min_u12m = max(0, float(payload.get('min_u12m') or 0))
        id_farmacia = int(os.environ.get('OBSERVER_ID_FARMACIA', '10525'))

        with get_db() as session:
            productos = (session.query(ObsProducto)
                         .filter(ObsProducto.laboratorio_observer == lab_id,
                                 ObsProducto.fecha_baja.is_(None))
                         .all())
            producto_ids = [p.observer_id for p in productos]
            u12m_map = _u12m_por_producto(session, producto_ids, id_farmacia)
            stock_map = _stock_por_producto(session, producto_ids, id_farmacia)

            # Nombres de drogas
            drogas_ids = list({p.nombre_droga_observer for p in productos
                               if p.nombre_droga_observer is not None})
            drogas_map = dict(session.query(
                ObsNombreDroga.observer_id, ObsNombreDroga.descripcion)
                .filter(ObsNombreDroga.observer_id.in_(drogas_ids)).all()) if drogas_ids else {}

            resultado = []
            total_dia = 0
            total_prueba = 0
            for p in productos:
                u12m = u12m_map.get(p.observer_id, 0)
                if u12m < min_u12m:
                    continue
                st = stock_map.get(p.observer_id, {'stock': 0, 'minimo': 0})

                # Sugerido estacional
                est = calcular_sugerido_estacional(
                    session, p, u12m=u12m,
                    stock_actual=st['stock'], minimo=st['minimo'],
                )
                # Sugerido dia (REPOSICION)
                sug_dia = _sugerido_dia_actual(
                    session, p, u12m, st['stock'], st['minimo'])

                delta = (est['sugerido_final'] - (sug_dia or 0)) if sug_dia is not None else None

                resultado.append({
                    'producto_id': p.observer_id,
                    'producto_nombre': p.descripcion,
                    'droga_id': p.nombre_droga_observer,
                    'droga_nombre': drogas_map.get(p.nombre_droga_observer) or '',
                    'stock': st['stock'],
                    'minimo': st['minimo'],
                    'u12m': int(u12m),
                    'sugerido_dia': sug_dia,
                    'sugerido_prueba': est['sugerido_final'],
                    'sugerido_base_prueba': est['sugerido_base'],
                    'delta': delta,
                    'origen_escenario': est['origen_escenario'],
                    'escenario_nombre': est['escenario_nombre'],
                    'indices': est['indices'],
                    'lead_dias': est['lead_dias'],
                    'cobertura_dias': est['cobertura_dias'],
                    'mes_objetivo': est['mes_objetivo'],
                    'mes_objetivo_label': est['mes_objetivo_label'],
                    'indice_aplicado': est['indice_aplicado'],
                    'ritmo_diario': est['ritmo_diario'],
                    'demanda_proyectada': est['demanda_proyectada'],
                    'flag': est['flag'],
                    'razon': est['razon'],
                })
                total_prueba += est['sugerido_final']
                if sug_dia is not None:
                    total_dia += sug_dia

            return jsonify({
                'lab_id': lab_id,
                'items': resultado,
                'total_dia': total_dia,
                'total_prueba': total_prueba,
                'delta_total': total_prueba - total_dia,
            })

    @app.route('/api/pedido-prueba/escenario-producto/<int:producto_id>',
               methods=['POST'])
    @login_required
    def api_escenario_producto(producto_id):
        """Crea/actualiza el escenario 'Generico' de un producto puntual.

        Body: { indices: [12], lead_time_dias, cobertura_dias }.
        Devuelve el calculo recalculado del producto.
        """
        payload = request.get_json(silent=True) or {}
        indices = payload.get('indices')
        if not isinstance(indices, list) or len(indices) != 12:
            return jsonify({'error': 'Se esperan 12 indices'}), 400
        try:
            indices = [max(0.0, float(v)) for v in indices]
            lead = max(0, min(180, int(payload.get('lead_time_dias', 0))))
            cob = max(1, min(365, int(payload.get('cobertura_dias', 30))))
        except (TypeError, ValueError):
            return jsonify({'error': 'parametros invalidos'}), 400

        id_farmacia = int(os.environ.get('OBSERVER_ID_FARMACIA', '10525'))

        with get_db() as session:
            producto = (session.query(ObsProducto)
                        .filter_by(observer_id=producto_id).first())
            if not producto:
                return jsonify({'error': 'producto inexistente'}), 404
            droga_id = producto.nombre_droga_observer
            if not droga_id:
                return jsonify({'error': 'producto sin droga'}), 400

            esc = (session.query(EstacionalidadEscenario)
                   .filter_by(droga_id=droga_id, producto_id=producto_id,
                              nombre='Generico')
                   .first())
            if esc:
                esc.indices_json = json.dumps(indices)
                esc.lead_time_dias = lead
                esc.cobertura_dias = cob
            else:
                esc = EstacionalidadEscenario(
                    droga_id=droga_id, producto_id=producto_id,
                    nombre='Generico',
                    indices_json=json.dumps(indices),
                    lead_time_dias=lead, cobertura_dias=cob,
                    es_default=True,
                    creado_por=getattr(current_user, 'username', None),
                )
                session.add(esc)
            session.commit()

            # Recalcular para devolver datos frescos
            u12m_map = _u12m_por_producto(session, [producto_id], id_farmacia)
            stock_map = _stock_por_producto(session, [producto_id], id_farmacia)
            u12m = u12m_map.get(producto_id, 0)
            st = stock_map.get(producto_id, {'stock': 0, 'minimo': 0})
            est = calcular_sugerido_estacional(
                session, producto, u12m=u12m,
                stock_actual=st['stock'], minimo=st['minimo'],
            )
            return jsonify({
                'ok': True,
                'sugerido_prueba': est['sugerido_final'],
                'origen_escenario': est['origen_escenario'],
                'indices': est['indices'],
                'lead_dias': est['lead_dias'],
                'cobertura_dias': est['cobertura_dias'],
                'razon': est['razon'],
            })

    @app.route('/api/pedido-prueba/flag/<int:producto_id>', methods=['POST'])
    @login_required
    def api_pedido_prueba_flag(producto_id):
        """Aplica o quita un flag a un producto puntual via EAN principal.

        Body: { action: 'apply'|'remove', flag_slug: 'DISCONTINUADO'|... }
        """
        payload = request.get_json(silent=True) or {}
        action = payload.get('action')
        slug = payload.get('flag_slug')
        if action not in ('apply', 'remove') or not slug:
            return jsonify({'error': 'action y flag_slug requeridos'}), 400

        with get_db() as session:
            producto = (session.query(ObsProducto)
                        .filter_by(observer_id=producto_id).first())
            if not producto:
                return jsonify({'error': 'producto inexistente'}), 404

            # Validar tipo de flag
            tipo = (session.query(TipoPedidoConfig)
                    .filter_by(slug=slug, categoria='flag').first())
            if not tipo:
                return jsonify({'error': f'flag {slug} inexistente'}), 404

            # EAN principal
            from services.pedido_estacional import obtener_eans_producto
            eans = obtener_eans_producto(session, producto_id)
            if not eans:
                return jsonify({'error': 'producto sin EAN'}), 400
            ean = eans[0]

            if action == 'apply':
                # Crear si no existe
                existente = (session.query(ProductoFlag)
                             .filter_by(flag_slug=slug, ean=ean).first())
                if not existente:
                    session.add(ProductoFlag(
                        flag_slug=slug, ean=ean, laboratorio_id=None,
                    ))
                    session.commit()
            else:  # remove
                (session.query(ProductoFlag)
                 .filter_by(flag_slug=slug, ean=ean).delete())
                session.commit()

            return jsonify({'ok': True})

    @app.route('/api/pedido-prueba/save', methods=['POST'])
    @login_required
    def api_pedido_prueba_save():
        """Guarda como Pedido con origen='prueba' + PedidoItems."""
        payload = request.get_json(silent=True) or {}
        try:
            lab_id = int(payload.get('lab_id') or 0)
        except (TypeError, ValueError):
            return jsonify({'error': 'lab_id invalido'}), 400
        items = payload.get('items') or []
        if not lab_id or not items:
            return jsonify({'error': 'lab_id e items requeridos'}), 400

        with get_db() as session:
            lab = (session.query(ObsLaboratorio)
                   .filter_by(observer_id=lab_id).first())
            lab_nombre = lab.descripcion if lab else f'Lab#{lab_id}'

            pedido = Pedido(
                laboratorio=lab_nombre,
                farmacia='',
                periodo='planificacion',
                n_days=30,
                origen='prueba',
                creado_en=_dt.utcnow(),
            )
            session.add(pedido)
            session.flush()

            total_unidades = 0
            for it in items:
                cantidad = int(it.get('cantidad') or 0)
                if cantidad <= 0:
                    continue
                session.add(PedidoItem(
                    pedido_id=pedido.id,
                    codigo_barra=str(it.get('ean') or ''),
                    nombre=str(it.get('nombre') or ''),
                    cantidad=cantidad,
                    precio_pvp=float(it.get('precio') or 0),
                    subtotal=float(it.get('precio') or 0) * cantidad,
                ))
                total_unidades += cantidad

            session.commit()
            return jsonify({
                'ok': True,
                'pedido_id': pedido.id,
                'total_unidades': total_unidades,
            })
