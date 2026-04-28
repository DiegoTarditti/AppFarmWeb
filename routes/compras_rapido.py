"""Compra rápida multi-droguería — pantalla principal.

Flujo:
1. Toma productos bajo mínimo (de /informes/bajo-minimo).
2. Para cada producto, calcula la mejor droguería por descuento total.
3. Muestra tabla editable: usuario ajusta cantidad y droguería.
4. Al final: pre-pedidos agrupados por droguería con plantillas para exportar.
"""
import os
from datetime import date, datetime

from flask import jsonify, render_template, request
from flask_login import login_required
from sqlalchemy import func

import database
from database import Laboratorio, ObsLaboratorio, ObsProducto, ObsStock, ObsVentaMensual, Provider
from services.descuentos import mejor_descuento


def init_app(app):

    @app.route('/compras/rapido')
    @login_required
    def compras_rapido():
        """Página principal del flujo de compra rápida."""
        from datetime import datetime as _dt

        from database import DescuentoBase

        # Parámetros
        try:
            umbral_match = max(50, min(100, int(request.args.get('umbral', 75))))
        except (ValueError, TypeError):
            umbral_match = 75

        venta_tipo = (request.args.get('venta_tipo') or '').strip()
        solo_clavos = request.args.get('solo_clavos') == '1'
        excluir_clavos = request.args.get('excluir_clavos', '1') == '1'
        # Auditoría: descuentos desmarcados por el usuario (CSV de IDs)
        excluir_dto = (request.args.get('excluir_dto') or '').strip()
        descuentos_excluidos = set(t for t in excluir_dto.split(',') if t.strip()) if excluir_dto else set()

        # Ámbito: lista de laboratorios seleccionados (por id local).
        # Si vacío en GET inicial, proponer los que TIENEN descuentos configurados.
        labs_param = (request.args.get('labs') or '').strip()
        labs_seleccionados_ids = set()
        if labs_param:
            for x in labs_param.split(','):
                try:
                    labs_seleccionados_ids.add(int(x.strip()))
                except (ValueError, TypeError):
                    pass

        id_farmacia = int(os.environ.get('OBSERVER_ID_FARMACIA', '10525'))
        hoy = _dt.now()

        # Ventana 12 meses para calcular ventas
        meses_12 = []
        y, m = hoy.year, hoy.month
        for _ in range(12):
            meses_12.append((y, m))
            m -= 1
            if m == 0:
                m, y = 12, y - 1
        desde_12m = meses_12[-1][0] * 100 + meses_12[-1][1]
        hasta_12m = hoy.year * 100 + hoy.month
        # Ventana 3 meses recientes (excluyendo el mes actual incompleto)
        meses_3 = meses_12[1:4]  # los 3 más recientes después del actual
        desde_3m = meses_3[-1][0] * 100 + meses_3[-1][1]
        hasta_3m = meses_3[0][0] * 100 + meses_3[0][1]

        with database.get_db() as session:
            # 1. Productos bajo mínimo en stock
            stock_q = (session.query(
                            ObsStock.producto_observer.label('pid'),
                            func.sum(ObsStock.stock_actual).label('stock'),
                            func.sum(ObsStock.minimo).label('minimo'),
                            func.sum(ObsStock.maximo).label('maximo'),
                       )
                       .filter(ObsStock.id_farmacia == id_farmacia,
                               ObsStock.minimo.isnot(None),
                               ObsStock.minimo > 0)
                       .group_by(ObsStock.producto_observer)
                       .subquery())

            ventas_sub = (session.query(
                              ObsVentaMensual.producto_observer.label('pid'),
                              func.sum(ObsVentaMensual.unidades).label('u12m'),
                          )
                          .filter(ObsVentaMensual.id_farmacia == id_farmacia,
                                  (ObsVentaMensual.anio * 100 + ObsVentaMensual.mes).between(desde_12m, hasta_12m))
                          .group_by(ObsVentaMensual.producto_observer)
                          .subquery())

            ventas_3m_sub = (session.query(
                                 ObsVentaMensual.producto_observer.label('pid'),
                                 func.sum(ObsVentaMensual.unidades).label('u3m'),
                             )
                             .filter(ObsVentaMensual.id_farmacia == id_farmacia,
                                     (ObsVentaMensual.anio * 100 + ObsVentaMensual.mes).between(desde_3m, hasta_3m))
                             .group_by(ObsVentaMensual.producto_observer)
                             .subquery())

            q = (session.query(
                    ObsProducto.observer_id.label('pid'),
                    ObsProducto.descripcion.label('desc'),
                    ObsProducto.id_tipo_venta_control.label('tvc'),
                    ObsProducto.laboratorio_observer.label('lab_obs'),
                    ObsLaboratorio.descripcion.label('lab_nombre'),
                    stock_q.c.stock,
                    stock_q.c.minimo,
                    stock_q.c.maximo,
                    func.coalesce(ventas_sub.c.u12m, 0).label('u12m'),
                    func.coalesce(ventas_3m_sub.c.u3m, 0).label('u3m'),
                 )
                 .join(stock_q, stock_q.c.pid == ObsProducto.observer_id)
                 .outerjoin(ObsLaboratorio,
                            ObsLaboratorio.observer_id == ObsProducto.laboratorio_observer)
                 .outerjoin(ventas_sub, ventas_sub.c.pid == ObsProducto.observer_id)
                 .outerjoin(ventas_3m_sub, ventas_3m_sub.c.pid == ObsProducto.observer_id)
                 .filter(ObsProducto.fecha_baja.is_(None))
                 .filter(stock_q.c.stock < stock_q.c.minimo)
                 .order_by(ObsProducto.descripcion))  # alfabético siempre

            # Filtros
            if venta_tipo == 'libre':
                q = q.filter(ObsProducto.id_tipo_venta_control == 'L')
            elif venta_tipo == 'receta':
                q = q.filter(ObsProducto.id_tipo_venta_control.in_(['R', 'A']))
            elif venta_tipo == 'controlado':
                q = q.filter(ObsProducto.id_tipo_venta_control.in_(['1','2','3','4','5','6','7','8']))

            # Map lab_observer → Laboratorio.id local
            lab_local_map = dict(
                session.query(Laboratorio.observer_id, Laboratorio.id)
                .filter(Laboratorio.observer_id.isnot(None)).all()
            )

            # Labs sugeridos = los que tienen al menos 1 descuento base activo
            labs_con_descuento_ids = {
                lid for (lid,) in session.query(DescuentoBase.laboratorio_id)
                .filter(DescuentoBase.activo == True).distinct().all()  # noqa: E712
            }
            # Si el usuario no eligió nada en URL → sugerir los con descuento
            if not labs_seleccionados_ids:
                labs_seleccionados_ids = set(labs_con_descuento_ids)

            # Lista completa de labs disponibles (activos) para el selector
            labs_disponibles = (session.query(Laboratorio)
                                .filter(Laboratorio.activo == True)  # noqa: E712
                                .order_by(Laboratorio.nombre).all())
            labs_disponibles_data = [
                {'id': l.id, 'nombre': l.nombre,
                 'tiene_dto': l.id in labs_con_descuento_ids,
                 'seleccionado': l.id in labs_seleccionados_ids}
                for l in labs_disponibles
            ]
            # Map lab_local_id → nombre (para chips)
            labs_seleccionados_data = [
                {'id': l.id, 'nombre': l.nombre,
                 'tiene_dto': l.id in labs_con_descuento_ids}
                for l in labs_disponibles if l.id in labs_seleccionados_ids
            ]

            # Set de observer_ids de labs seleccionados (para filtro rápido)
            obs_ids_seleccionados = {
                obs_id for obs_id, lid in lab_local_map.items()
                if lid in labs_seleccionados_ids
            }

            productos = []
            for r in q.all():
                # Filtro por ámbito de labs (si está seleccionado el lab del producto)
                if r.lab_obs not in obs_ids_seleccionados:
                    continue
                u3m = float(r.u3m or 0)
                u12m = float(r.u12m or 0)
                # "clavo" = sin movimiento 60d (aproximado: u3m == 0)
                es_clavo = u3m == 0
                if excluir_clavos and es_clavo and not solo_clavos:
                    continue
                if solo_clavos and not es_clavo:
                    continue

                stock = int(r.stock or 0)
                minimo = int(r.minimo or 0)
                maximo = int(r.maximo) if r.maximo is not None else None
                # Cantidad sugerida: max(1, maximo - stock) si hay máximo, sino (minimo - stock)
                if maximo and maximo > stock:
                    sugerido = max(1, maximo - stock)
                else:
                    sugerido = max(1, minimo - stock)

                # Mejor descuento — necesita lab_id local
                lab_id_local = lab_local_map.get(r.lab_obs)
                opciones = []
                if lab_id_local:
                    opciones = mejor_descuento(session, r.pid, lab_id_local,
                                                cantidad=sugerido,
                                                descuentos_excluidos=descuentos_excluidos)

                # Mejor opción
                mejor = opciones[0] if opciones else None

                productos.append({
                    'observer_id':       r.pid,
                    'descripcion':       r.desc,
                    'tvc':               (r.tvc or '').strip(),
                    'lab_observer':      r.lab_obs,
                    'lab_nombre':        r.lab_nombre or '—',
                    'lab_id_local':      lab_id_local,
                    'stock':             stock,
                    'minimo':            minimo,
                    'maximo':            maximo,
                    'faltan':            max(0, minimo - stock),
                    'u3m':               u3m,
                    'u12m':              u12m,
                    'es_clavo':          es_clavo,
                    'sugerido':          sugerido,
                    'opciones':          opciones,  # lista ordenada por mejor descuento
                    'mejor_drog_id':     mejor['drogueria_id'] if mejor else None,
                    'mejor_drog_nombre': mejor['drogueria_nombre'] if mejor else None,
                    'mejor_dto_pct':     mejor['descuento_total_pct'] if mejor else None,
                })

            # Stats globales
            total_productos = len(productos)
            sin_descuentos = sum(1 for p in productos if not p['opciones'])
            clavos_ocultos = 0

            # Drogerías únicas presentes en las opciones (para el footer)
            drogs_referenciadas = {}
            # Descuentos únicos aplicados en el cálculo (para panel auditoría)
            descuentos_aplicados = {}  # id → {label, pct, contar...}
            for p in productos:
                for o in p['opciones']:
                    drogs_referenciadas[o['drogueria_id']] = {
                        'id':            o['drogueria_id'],
                        'nombre':        o['drogueria_nombre'],
                        'compra_minima': o['compra_minima'],
                    }
                    for d in o['desglose']:
                        did = d.get('id')
                        if not did:
                            continue
                        if did not in descuentos_aplicados:
                            label = ''
                            if d['nivel'] == 'base':
                                label = f'Base — {o["drogueria_nombre"]}'
                            elif d['nivel'] in ('transfer', 'oferta_producto', 'oferta c/min'):
                                label = d.get('fuente') or 'Transfer'
                            else:
                                label = f'{d["nivel"]} — {o["drogueria_nombre"]}'
                            descuentos_aplicados[did] = {
                                'id':         did,
                                'nivel':      d['nivel'],
                                'label':      label,
                                'pct':        d['pct'],
                                'usado_en':   0,
                                'plazo':      d.get('plazo', ''),
                                'vigencia':   d.get('vigencia_hasta', ''),
                                'excluido':   did in descuentos_excluidos,
                            }
                        descuentos_aplicados[did]['usado_en'] += 1

            return render_template('compras_rapido.html',
                                   productos=productos,
                                   total_productos=total_productos,
                                   sin_descuentos=sin_descuentos,
                                   drogs_referenciadas=list(drogs_referenciadas.values()),
                                   umbral_match=umbral_match,
                                   venta_tipo=venta_tipo,
                                   excluir_clavos=excluir_clavos,
                                   solo_clavos=solo_clavos,
                                   labs_seleccionados=labs_seleccionados_data,
                                   labs_disponibles=labs_disponibles_data,
                                   labs_con_descuento_count=len(labs_con_descuento_ids))
