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
from database import (
    Laboratorio,
    ObsLaboratorio,
    ObsProducto,
    ObsStock,
    ObsVentaMensual,
    Pedido,
    PedidoItem,
    Provider,
)
from helpers import now_ar
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

            # Auditoría: ordenar descuentos por % desc (los más impactantes
            # arriba) para el panel.
            descuentos_aplicados_lst = sorted(
                descuentos_aplicados.values(),
                key=lambda d: -float(d.get('pct') or 0),
            )

            return render_template('compras_rapido.html',
                                   productos=productos,
                                   total_productos=total_productos,
                                   sin_descuentos=sin_descuentos,
                                   drogs_referenciadas=list(drogs_referenciadas.values()),
                                   descuentos_aplicados=descuentos_aplicados_lst,
                                   descuentos_excluidos_csv=','.join(sorted(descuentos_excluidos)),
                                   umbral_match=umbral_match,
                                   venta_tipo=venta_tipo,
                                   excluir_clavos=excluir_clavos,
                                   solo_clavos=solo_clavos,
                                   labs_seleccionados=labs_seleccionados_data,
                                   labs_disponibles=labs_disponibles_data,
                                   labs_con_descuento_count=len(labs_con_descuento_ids))

    @app.route('/compras/rapido/crear-pedidos', methods=['POST'])
    @login_required
    def compras_rapido_crear_pedidos():
        """Crea N Pedidos (uno por droguería) a partir de los pre-pedidos
        armados en /compras/rapido. Devuelve los pedido_ids creados.

        Body JSON esperado:
            {
                "pre_pedidos": [
                    {
                        "drogueria_id": 12,
                        "drogueria_nombre": "Bernabó",
                        "items": [
                            {"observer_id": 1234, "codigo_barra": "7791...",
                             "nombre": "AMOXIDAL 500", "cantidad": 6,
                             "descuento_pct": 31.5}
                        ]
                    },
                    ...
                ]
            }
        """
        data = request.get_json(silent=True) or {}
        pre_pedidos = data.get('pre_pedidos') or []
        if not pre_pedidos:
            return jsonify({'ok': False, 'error': 'pre_pedidos vacío'}), 400

        creados = []
        with database.get_db() as session:
            for pp in pre_pedidos:
                drog_id = pp.get('drogueria_id')
                drog_nombre = (pp.get('drogueria_nombre') or '').strip() or 'Droguería'
                items = pp.get('items') or []
                if not drog_id or not items:
                    continue
                # Verificar que la droguería existe
                prov = session.get(Provider, int(drog_id))
                if not prov:
                    continue

                pedido_items = []
                for it in items:
                    cant = int(it.get('cantidad') or 0)
                    if cant <= 0:
                        continue
                    cb = (it.get('codigo_barra') or '').strip()
                    if not cb:
                        # Pseudo-EAN si solo tenemos observer_id
                        oid = it.get('observer_id')
                        if oid:
                            cb = f'OBS:{oid}'
                        else:
                            continue
                    pedido_items.append(PedidoItem(
                        codigo_barra=cb[:30],
                        nombre=(it.get('nombre') or '')[:200],
                        cantidad=cant,
                        precio_pvp=0,
                        subtotal=0,
                    ))

                if not pedido_items:
                    continue

                pedido = Pedido(
                    laboratorio=f'Compra rápida — {drog_nombre}',
                    farmacia='',
                    periodo=f'Compra rápida {now_ar().strftime("%Y-%m-%d")}',
                    n_days=0,
                    items=pedido_items,
                    estado='PENDIENTE',
                    canal='drogueria',
                    partner_id=int(drog_id),
                    canal_elegido_en=now_ar(),
                )
                session.add(pedido)
                session.flush()
                creados.append({
                    'pedido_id': pedido.id,
                    'drogueria_id': int(drog_id),
                    'drogueria_nombre': drog_nombre,
                    'n_items': len(pedido_items),
                })
            session.commit()

        return jsonify({'ok': True, 'pedidos': creados})

    @app.route('/api/compras/conflictos', methods=['POST'])
    @login_required
    def api_compras_conflictos():
        """Detecta si hay mejor descuento en otra droguería para los EANs dados.

        Pensado como helper para pantallas que muestran un pedido a una droguería
        específica y querés ver si convendría mover algún ítem a otra. Caso de
        uso típico: el panel "Pedidos a droguerías" estilo ObServer (referencia
        en docs/mejoras_pendientes.md → "Pantalla Pedidos a droguerías").

        Body JSON:
            {
                "items": [
                    {"ean": "7791234567890", "drogueria_actual_id": 5},
                    ...
                ]
            }

        Devuelve solo los items que TIENEN conflicto:
            {
                "ok": true,
                "conflictos": [
                    {
                        "ean": "...",
                        "drogueria_actual_id": 5,
                        "drogueria_actual_nombre": "Kellerhoff",
                        "dto_actual_pct": 31.0,
                        "mejor_drogueria_id": 12,
                        "mejor_drogueria_nombre": "Bernabó",
                        "mejor_dto_pct": 41.5,
                        "ahorro_pct": 10.5
                    },
                    ...
                ],
                "sin_conflicto": 14,    # cantidad de items que NO tienen mejor opción
                "sin_resolver": 2       # cantidad de items que no se pudo mapear a obs/lab
            }
        """
        from database import Producto

        data = request.get_json(silent=True) or {}
        items = data.get('items') or []
        if not isinstance(items, list):
            return jsonify({'ok': False, 'error': 'items debe ser una lista'}), 400
        if not items:
            return jsonify({'ok': False, 'error': 'items vacío'}), 400
        # Cota anti-DoS: payloads gigantes que harían N queries en loop.
        if len(items) > 5000:
            return jsonify({'ok': False, 'error': 'items > 5000 (límite anti-DoS)'}), 413

        conflictos = []
        sin_conflicto = 0
        sin_resolver = 0

        with database.get_db() as session:
            # 1. Resolver EAN → observer_id en bulk (1 query a productos +
            # 1 query directo a obs_productos para EANs numéricos).
            eans = [str(it.get('ean') or '').strip() for it in items]
            eans_no_vacios = [e for e in eans if e]

            ean_to_obs = {}
            if eans_no_vacios:
                # Path 1: EAN numérico que es observer_id directo (pedidos
                # nacidos en ObServer guardan IdProducto como string en cb).
                numericos = []
                for e in eans_no_vacios:
                    try:
                        numericos.append((e, int(e)))
                    except (ValueError, TypeError):
                        pass
                if numericos:
                    ids_n = [n for (_, n) in numericos]
                    existentes = {oid for (oid,) in session.query(ObsProducto.observer_id)
                                  .filter(ObsProducto.observer_id.in_(ids_n)).all()}
                    for ean, n in numericos:
                        if n in existentes:
                            ean_to_obs[ean] = n

                # Path 2: tabla productos (codigo_barra principal o alts).
                pendientes = [e for e in eans_no_vacios if e not in ean_to_obs]
                if pendientes:
                    from sqlalchemy import or_
                    conds = [Producto.codigo_barra.in_(pendientes)]
                    for col_name in ('codigo_barra_alt1', 'codigo_barra_alt2', 'codigo_barra_alt3'):
                        col = getattr(Producto, col_name, None)
                        if col is not None:
                            conds.append(col.in_(pendientes))
                    for p in session.query(Producto).filter(or_(*conds)).all():
                        if not p.observer_id:
                            continue
                        for cb in [p.codigo_barra, p.codigo_barra_alt1,
                                   p.codigo_barra_alt2, p.codigo_barra_alt3]:
                            if cb and cb in pendientes:
                                ean_to_obs[cb] = p.observer_id

            # 2. Resolver observer_id → lab_id local (Laboratorio.observer_id).
            obs_ids = list(set(ean_to_obs.values()))
            obs_to_lab_local = {}
            if obs_ids:
                # observer_id de producto → laboratorio_observer (en obs_productos)
                obs_prods = session.query(ObsProducto.observer_id,
                                          ObsProducto.laboratorio_observer)\
                    .filter(ObsProducto.observer_id.in_(obs_ids)).all()
                lab_obs_ids = {lab_obs for (_, lab_obs) in obs_prods if lab_obs}
                # laboratorio_observer → Laboratorio.id local
                lab_obs_to_local = {}
                if lab_obs_ids:
                    lab_obs_to_local = dict(session.query(
                        Laboratorio.observer_id, Laboratorio.id)
                        .filter(Laboratorio.observer_id.in_(lab_obs_ids)).all())
                for (oid, lab_obs) in obs_prods:
                    local = lab_obs_to_local.get(lab_obs) if lab_obs else None
                    if local:
                        obs_to_lab_local[oid] = local

            # 3. Pre-cargar nombres de droguería para no hacer 1 query por conflicto.
            prov_nombres = dict(session.query(Provider.id, Provider.razon_social).all())

            # 4. Recorrer items: resolver, llamar mejor_descuento, comparar.
            for it in items:
                ean = str(it.get('ean') or '').strip()
                drog_actual_id = it.get('drogueria_actual_id')
                if not ean or not drog_actual_id:
                    sin_resolver += 1
                    continue
                obs_id = ean_to_obs.get(ean)
                lab_local_id = obs_to_lab_local.get(obs_id) if obs_id else None
                if not obs_id or not lab_local_id:
                    sin_resolver += 1
                    continue

                opciones = mejor_descuento(session, obs_id, lab_local_id) or []
                if not opciones:
                    sin_resolver += 1
                    continue

                mejor = opciones[0]
                # Buscar el descuento de la droguería actual en las opciones.
                actual = next((o for o in opciones
                               if o['drogueria_id'] == drog_actual_id), None)
                dto_actual = float(actual['descuento_total_pct']) if actual else 0.0
                dto_mejor = float(mejor['descuento_total_pct'])

                if mejor['drogueria_id'] == drog_actual_id:
                    sin_conflicto += 1
                    continue

                # Hay conflicto solo si la diferencia es relevante.
                # Threshold 0.5pp para no flaggear ruido por redondeo.
                ahorro = dto_mejor - dto_actual
                if ahorro < 0.5:
                    sin_conflicto += 1
                    continue

                conflictos.append({
                    'ean': ean,
                    'drogueria_actual_id': drog_actual_id,
                    'drogueria_actual_nombre': prov_nombres.get(drog_actual_id) or '',
                    'dto_actual_pct': round(dto_actual, 2),
                    'mejor_drogueria_id': mejor['drogueria_id'],
                    'mejor_drogueria_nombre': mejor['drogueria_nombre'],
                    'mejor_dto_pct': round(dto_mejor, 2),
                    'ahorro_pct': round(ahorro, 2),
                })

        # Ordenar conflictos por ahorro DESC (los más jugosos arriba).
        conflictos.sort(key=lambda c: -c['ahorro_pct'])

        return jsonify({
            'ok': True,
            'conflictos': conflictos,
            'sin_conflicto': sin_conflicto,
            'sin_resolver': sin_resolver,
        })
