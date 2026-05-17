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

from flask import jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import func

from database import (
    EstacionalidadEscenario,
    ObsLaboratorio,
    ObsNombreDroga,
    ObsProducto,
    ObsStock,
    ObsVentaMensual,
    ProductoFlag,
    TipoPedidoConfig,
    get_db,
)
from services.pedido_estacional import (
    LIMITES,
    MESES_ES,
    calcular_sugerido_dia_actual,
    calcular_sugerido_estacional,
    obtener_escenarios_bulk,
    obtener_flags_bulk,
    obtener_precios_publicos_bulk,
    obtener_ventas_arr_bulk,
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


# _sugerido_dia_actual eliminada — ahora se usa la replica fiel
# calcular_sugerido_dia_actual de services/pedido_estacional.py que usa
# los mismos building blocks que routes/compras_dia.py (purchase_helpers.
# calcular_min_sugerido + services.calculo_pedido.calcular_a_pedir).


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
                               meses_es=MESES_ES,
                               limites=LIMITES)

    @app.route('/api/pedido-prueba/historico/<int:producto_id>')
    @login_required
    def api_pedido_prueba_historico(producto_id):
        """Serie mensual de ventas del producto por anio, para chart del drawer."""
        id_farmacia = int(os.environ.get('OBSERVER_ID_FARMACIA', '10525'))
        from collections import defaultdict
        with get_db() as session:
            rows = (session.query(
                ObsVentaMensual.anio, ObsVentaMensual.mes,
                func.sum(ObsVentaMensual.unidades).label('u'))
                .filter(ObsVentaMensual.producto_observer == producto_id,
                        ObsVentaMensual.id_farmacia == id_farmacia)
                .group_by(ObsVentaMensual.anio, ObsVentaMensual.mes)
                .order_by(ObsVentaMensual.anio, ObsVentaMensual.mes)
                .all())
        por_anio = defaultdict(lambda: [0.0] * 12)
        for r in rows:
            por_anio[r.anio][r.mes - 1] = float(r.u or 0)
        return jsonify({
            'producto_id': producto_id,
            'series': [{'anio': a, 'unidades': por_anio[a]}
                       for a in sorted(por_anio.keys())],
        })

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
        # Defaults configurables desde la cabecera. Se aplican a productos
        # SIN escenario propio (origen='auto'). Los productos con escenario
        # propio o de droga mantienen sus valores.
        try:
            lead_default = max(
                LIMITES['lead_dias_piso'],
                min(LIMITES['lead_dias_max'],
                    int(payload.get('lead_default') or LIMITES['lead_dias_default'])),
            )
        except (TypeError, ValueError):
            lead_default = LIMITES['lead_dias_default']
        try:
            cob_default = max(
                LIMITES['cob_dias_min'],
                min(LIMITES['cob_dias_max'],
                    int(payload.get('cob_default') or LIMITES['cob_dias_default'])),
            )
        except (TypeError, ValueError):
            cob_default = LIMITES['cob_dias_default']
        id_farmacia = int(os.environ.get('OBSERVER_ID_FARMACIA', '10525'))

        with get_db() as session:
            productos = (session.query(ObsProducto)
                         .filter(ObsProducto.laboratorio_observer == lab_id,
                                 ObsProducto.fecha_baja.is_(None))
                         .all())
            producto_ids = [p.observer_id for p in productos]
            u12m_map = _u12m_por_producto(session, producto_ids, id_farmacia)
            stock_map = _stock_por_producto(session, producto_ids, id_farmacia)

            # Precios PVP (precio publico). Pre-cargar todos los EANs del
            # lab en 2 queries: 1 para EANs activos + 1 para precios.
            from database import ObsCodigoBarras
            ean_rows = (session.query(
                ObsCodigoBarras.producto_observer, ObsCodigoBarras.codigo_barras)
                .filter(ObsCodigoBarras.producto_observer.in_(producto_ids),
                        ObsCodigoBarras.fecha_baja.is_(None))
                .order_by(ObsCodigoBarras.orden)
                .all()) if producto_ids else []
            eans_por_producto = {}
            for r in ean_rows:
                eans_por_producto.setdefault(r.producto_observer, []).append(
                    r.codigo_barras)
            todos_eans = list({e for eans in eans_por_producto.values() for e in eans})
            precios_map = obtener_precios_publicos_bulk(session, todos_eans)

            # Nombres de drogas
            drogas_ids = list({p.nombre_droga_observer for p in productos
                               if p.nombre_droga_observer is not None})
            drogas_map = dict(session.query(
                ObsNombreDroga.observer_id, ObsNombreDroga.descripcion)
                .filter(ObsNombreDroga.observer_id.in_(drogas_ids)).all()) if drogas_ids else {}

            # Bulk precargas para eliminar N+1 dentro del loop.
            escenarios_bulk = obtener_escenarios_bulk(session, drogas_ids, producto_ids)
            flags_bulk = obtener_flags_bulk(session, todos_eans, lab_id=lab_id)
            ventas_arr_bulk = obtener_ventas_arr_bulk(session, producto_ids, id_farmacia)

            # Frescura de datos: tomar el sync_en mas reciente de obs_stock
            # y obs_ventas_mensuales (proxies de "stock" y "ventas"
            # actualizadas). Si no hay data, devuelve None.
            stock_sync = (session.query(func.max(ObsStock.sync_en))
                          .filter(ObsStock.id_farmacia == id_farmacia,
                                  ObsStock.producto_observer.in_(producto_ids))
                          .scalar() if producto_ids else None)
            ventas_sync = (session.query(func.max(ObsVentaMensual.sync_en))
                           .filter(ObsVentaMensual.id_farmacia == id_farmacia,
                                   ObsVentaMensual.producto_observer.in_(producto_ids))
                           .scalar() if producto_ids else None)

            resultado = []
            total_dia = 0
            total_prueba = 0
            monto_dia = 0.0
            monto_prueba = 0.0
            for p in productos:
                u12m = u12m_map.get(p.observer_id, 0)
                if u12m < min_u12m:
                    continue
                st = stock_map.get(p.observer_id, {'stock': 0, 'minimo': 0})
                # Precio publico: tomar de cualquiera de los EANs del producto.
                precio_pvp = None
                for ean in eans_por_producto.get(p.observer_id, []):
                    if ean in precios_map:
                        precio_pvp = precios_map[ean]
                        break

                # Sugerido estacional (con bulks precargados → cero N+1)
                est = calcular_sugerido_estacional(
                    session, p, u12m=u12m,
                    stock_actual=st['stock'], minimo=st['minimo'],
                    lead_default=lead_default, cob_default=cob_default,
                    escenarios_bulk=escenarios_bulk,
                    eans_producto=eans_por_producto.get(p.observer_id, []),
                    flags_bulk=flags_bulk,
                    lab_id_hint=lab_id,
                )
                # Sugerido dia (REPOSICION) — replica fiel + bulk
                sug_dia = calcular_sugerido_dia_actual(
                    session, p.observer_id, id_farmacia,
                    stock_actual=st['stock'], min_actual=st['minimo'],
                    ventas_arr_bulk=ventas_arr_bulk)

                delta = (est['sugerido_final'] - (sug_dia or 0)) if sug_dia is not None else None

                # u3m: ultimos 3 meses (excluyendo mes parcial actual).
                # Aprovecho ventas_arr_bulk que ya tengo cargado.
                _arr = ventas_arr_bulk.get(p.observer_id, [0.0] * 12)
                u3m = int(sum(_arr[8:11]))  # indices 8,9,10 = 3 meses antes del actual
                resultado.append({
                    'producto_id': p.observer_id,
                    'producto_nombre': p.descripcion,
                    'droga_id': p.nombre_droga_observer,
                    'droga_nombre': drogas_map.get(p.nombre_droga_observer) or '',
                    'stock': st['stock'],
                    'minimo': st['minimo'],
                    'u12m': int(u12m),
                    'u3m': u3m,
                    'sugerido_dia': sug_dia,
                    'sugerido_prueba': est['sugerido_final'],
                    'sugerido_base_prueba': est['sugerido_base'],
                    'delta': delta,
                    'precio_pvp': precio_pvp,
                    'monto_dia': (precio_pvp * sug_dia) if (precio_pvp and sug_dia) else None,
                    'monto_prueba': (precio_pvp * est['sugerido_final']) if precio_pvp else None,
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
                if precio_pvp:
                    if sug_dia:
                        monto_dia += precio_pvp * sug_dia
                    monto_prueba += precio_pvp * est['sugerido_final']

            return jsonify({
                'lab_id': lab_id,
                'items': resultado,
                'total_dia': total_dia,
                'total_prueba': total_prueba,
                'delta_total': total_prueba - total_dia,
                'monto_dia': round(monto_dia, 2),
                'monto_prueba': round(monto_prueba, 2),
                'monto_delta': round(monto_prueba - monto_dia, 2),
                'lead_default': lead_default,
                'cob_default': cob_default,
                'stock_sync_en': stock_sync.isoformat() if stock_sync else None,
                'ventas_sync_en': ventas_sync.isoformat() if ventas_sync else None,
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
            lead = max(
                LIMITES['lead_dias_piso'],
                min(LIMITES['lead_dias_max'],
                    int(payload.get('lead_time_dias', LIMITES['lead_dias_default']))),
            )
            cob = max(
                LIMITES['cob_dias_min'],
                min(LIMITES['cob_dias_max'],
                    int(payload.get('cobertura_dias', LIMITES['cob_dias_default']))),
            )
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

    # Endpoint /save eliminado en el pivot a "solo configuración".
    # La pantalla ya no arma pedidos; los escenarios y flags se persisten
    # individualmente por el drawer (escenario-producto) y por el endpoint
    # de flag. Si en el futuro vuelve a hacer falta armar pedido desde
    # aca, ver git history en branch feat/pedido-prueba (commit ef68334).
