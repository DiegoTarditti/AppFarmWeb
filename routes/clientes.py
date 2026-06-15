"""Listado, detalle y ABM local de clientes (espejo DW.Clientes + extensión local).

También expone la API JSON `/api/clientes/*` usada por el componente
`cliente_picker` (ver static/js/cliente_picker.js), por templates/reparto.html
y por tests/test_reparto.py.
"""

from datetime import datetime

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func as _f
from sqlalchemy import or_

import database
from auth import tiene_perfil

_ROLES_OK = ('admin', 'dev', 'farmacia')
# Perfiles que usan las APIs de /api/clientes/* — el cliente_picker está embebido en
# /pedido/nuevo, /atencion (chat clientes) y en /reparto/planilla.
_PERFILES_OK = ('pedido_manual', 'chat_clientes', 'planilla_envios')


def _api_ok():
    if getattr(current_user, 'rol', None) in _ROLES_OK:
        return True
    return any(tiene_perfil(current_user, p) for p in _PERFILES_OK)


def init_app(app):

    @app.route('/clientes')
    @login_required
    def clientes_list():
        q = (request.args.get('q') or '').strip()
        grupo_id = request.args.get('grupo_id', type=int)
        localidad = (request.args.get('localidad') or '').strip()
        os_id = request.args.get('os_id', type=int)
        try:
            page = max(1, int(request.args.get('page', '1')))
        except ValueError:
            page = 1
        per_page = 50
        offset = (page - 1) * per_page

        with database.get_db() as session:
            base = session.query(database.ObsCliente)
            if q:
                from helpers import multi_token_filter
                # Match exacto por DNI si el query es solo numérico (atajo).
                if q.isdigit():
                    base = base.filter(or_(
                        database.ObsCliente.documento_numero == int(q),
                        database.ObsCliente.telefono.ilike(f'%{q}%'),
                    ))
                else:
                    clausula = multi_token_filter(q,
                        database.ObsCliente.apellido_nombre,
                        database.ObsCliente.telefono,
                        database.ObsCliente.domicilio_direccion)
                    if clausula is not None:
                        base = base.filter(clausula)
            if grupo_id:
                base = base.filter(database.ObsCliente.grupo_observer == grupo_id)
            if localidad:
                base = base.filter(database.ObsCliente.localidad.ilike(f'%{localidad}%'))
            # Filtro por OS principal inferida (cliente_os_inferida)
            if os_id is not None:
                if os_id == 0:
                    # 0 = "sin OS principal" (ningún match en cliente_os_inferida con OS)
                    sub = (session.query(database.ClienteOsInferida.cliente_observer)
                           .filter(database.ClienteOsInferida.obra_social_observer.isnot(None))
                           .subquery())
                    base = base.filter(~database.ObsCliente.observer_id.in_(sub))
                else:
                    sub = (session.query(database.ClienteOsInferida.cliente_observer)
                           .filter(database.ClienteOsInferida.obra_social_observer == os_id)
                           .subquery())
                    base = base.filter(database.ObsCliente.observer_id.in_(sub))

            total = base.count()
            clientes_raw = (base.order_by(database.ObsCliente.apellido_nombre)
                            .offset(offset).limit(per_page).all())

            obs_ids = [c.observer_id for c in clientes_raw]

            # Nombres de grupos
            grupos_map = dict(
                session.query(database.ObsGrupoCliente.observer_id,
                              database.ObsGrupoCliente.descripcion).all()
            )

            # Detectar cuáles tienen extensión local (Cliente) ya cargada
            con_extension = set()
            obs_to_cli = {}   # observer_id → cliente_id local (Cliente.id)
            if obs_ids:
                for cli_id, obs_id in (session.query(database.Cliente.id, database.Cliente.observer_id)
                                       .filter(database.Cliente.observer_id.in_(obs_ids)).all()):
                    con_extension.add(obs_id)
                    obs_to_cli[obs_id] = cli_id

            # Geo del último domicilio con coords + último chat + último pedido
            # por cliente local. Se renderean como columnas extra (Diego 2026-06-15).
            cli_ids = list(obs_to_cli.values())
            geo_map = {}            # cli_id → (lat, lng)
            ultima_conv_map = {}    # cli_id → conv_id (canal != 'manual')
            ultimo_pedido_map = {}  # cli_id → (pedido_id, fecha)
            if cli_ids:
                # Último DomicilioCliente con lat/lng por cliente_id.
                from sqlalchemy import func as __f
                dom_rows = (session.query(database.DomicilioCliente.cliente_id,
                                          database.DomicilioCliente.lat,
                                          database.DomicilioCliente.lng,
                                          database.DomicilioCliente.geo_actualizado_en)
                            .filter(database.DomicilioCliente.cliente_id.in_(cli_ids),
                                    database.DomicilioCliente.lat.isnot(None),
                                    database.DomicilioCliente.lng.isnot(None))
                            .order_by(database.DomicilioCliente.cliente_id,
                                      database.DomicilioCliente.geo_actualizado_en.desc().nullslast())
                            .all())
                for cid, lat, lng, _ in dom_rows:
                    if cid not in geo_map:
                        geo_map[cid] = (float(lat), float(lng))
                # Última BotConversacion no-manual por cliente_id.
                conv_rows = (session.query(database.BotConversacion.cliente_id,
                                           database.BotConversacion.id,
                                           database.BotConversacion.ultimo_en)
                             .filter(database.BotConversacion.cliente_id.in_(cli_ids),
                                     database.BotConversacion.canal != 'manual')
                             .order_by(database.BotConversacion.cliente_id,
                                       database.BotConversacion.ultimo_en.desc().nullslast())
                             .all())
                for cid, conv_id, _ in conv_rows:
                    if cid not in ultima_conv_map:
                        ultima_conv_map[cid] = conv_id
                # Último PedidoReparto por cliente_id.
                ped_rows = (session.query(database.PedidoReparto.cliente_id,
                                          database.PedidoReparto.id,
                                          database.PedidoReparto.fecha)
                            .filter(database.PedidoReparto.cliente_id.in_(cli_ids))
                            .order_by(database.PedidoReparto.cliente_id,
                                      database.PedidoReparto.fecha.desc(),
                                      database.PedidoReparto.id.desc())
                            .all())
                for cid, pid, fecha in ped_rows:
                    if cid not in ultimo_pedido_map:
                        ultimo_pedido_map[cid] = (pid, fecha)

            # OS principal inferida por cliente (con confianza)
            os_inferida_map = {}
            if obs_ids:
                rows = (session.query(database.ClienteOsInferida.cliente_observer,
                                      database.ClienteOsInferida.obra_social_observer,
                                      database.ClienteOsInferida.confianza_pct)
                        .filter(database.ClienteOsInferida.cliente_observer.in_(obs_ids))
                        .filter(database.ClienteOsInferida.obra_social_observer.isnot(None))
                        .all())
                os_ids_para_nombrar = {r[1] for r in rows}
                os_nombres = dict(session.query(database.ObsObraSocial.observer_id,
                                                database.ObsObraSocial.descripcion)
                                  .filter(database.ObsObraSocial.observer_id.in_(os_ids_para_nombrar)).all()) if os_ids_para_nombrar else {}
                for cli, os_obs, conf in rows:
                    os_inferida_map[cli] = {
                        'os_id': os_obs,
                        'nombre': os_nombres.get(os_obs, f'OS#{os_obs}'),
                        'confianza': float(conf) if conf is not None else None,
                    }

            # Lista de grupos para el filtro
            grupos = (session.query(database.ObsGrupoCliente)
                      .filter(database.ObsGrupoCliente.fecha_baja.is_(None))
                      .order_by(database.ObsGrupoCliente.descripcion).all())

            # Lista de OS con clientes inferidos (para dropdown del filtro)
            from sqlalchemy import func as _func
            os_con_clientes = (session.query(database.ObsObraSocial.observer_id,
                                              database.ObsObraSocial.descripcion,
                                              _func.count(database.ClienteOsInferida.cliente_observer))
                                .join(database.ClienteOsInferida,
                                      database.ClienteOsInferida.obra_social_observer == database.ObsObraSocial.observer_id)
                                .filter(database.ObsObraSocial.fecha_baja.is_(None))
                                .group_by(database.ObsObraSocial.observer_id, database.ObsObraSocial.descripcion)
                                .order_by(_func.count(database.ClienteOsInferida.cliente_observer).desc())
                                .all())

            clientes = []
            for c in clientes_raw:
                cli_id = obs_to_cli.get(c.observer_id)
                geo = geo_map.get(cli_id) if cli_id else None
                ult_pedido = ultimo_pedido_map.get(cli_id) if cli_id else None
                clientes.append({
                    'observer_id': c.observer_id,
                    'cliente_id': cli_id,
                    'apellido_nombre': c.apellido_nombre,
                    'documento': (f'{c.documento_tipo} {c.documento_numero}'
                                   if c.documento_numero else ''),
                    'telefono': c.telefono or '',
                    'localidad': c.localidad or '',
                    'direccion': c.domicilio_direccion or '',
                    'grupo': grupos_map.get(c.grupo_observer, ''),
                    'tiene_extension': c.observer_id in con_extension,
                    'os_inferida': os_inferida_map.get(c.observer_id),
                    'geo': {'lat': geo[0], 'lng': geo[1]} if geo else None,
                    'ultima_conv_id': ultima_conv_map.get(cli_id) if cli_id else None,
                    'ultimo_pedido_id': ult_pedido[0] if ult_pedido else None,
                    'ultimo_pedido_fecha': ult_pedido[1].strftime('%Y-%m-%d') if ult_pedido else None,
                })

            last_page = max(1, (total + per_page - 1) // per_page)
            return render_template('clientes_list.html',
                                   clientes=clientes,
                                   total=total,
                                   grupos=[{'observer_id': g.observer_id,
                                            'descripcion': g.descripcion} for g in grupos],
                                   obras_sociales=[{'os_id': r[0], 'nombre': r[1], 'n_clientes': r[2]}
                                                    for r in os_con_clientes],
                                   q=q, grupo_id=grupo_id, localidad=localidad, os_id=os_id,
                                   page=page, last_page=last_page)

    @app.route('/clientes/stats')
    @login_required
    def clientes_stats():
        """Dashboard demográfico de clientes: distribuciones por grupo, categoría,
        localidad, provincia + conteo de extensiones locales cargadas."""
        with database.get_db() as session:
            total = session.query(database.ObsCliente).count()

            grupos_map = dict(session.query(database.ObsGrupoCliente.observer_id,
                                            database.ObsGrupoCliente.descripcion).all())
            cats_map = dict(session.query(database.ObsCategoriaCliente.observer_id,
                                          database.ObsCategoriaCliente.descripcion).all())

            por_grupo = (session.query(database.ObsCliente.grupo_observer,
                                       _f.count(database.ObsCliente.observer_id))
                         .group_by(database.ObsCliente.grupo_observer)
                         .order_by(_f.count(database.ObsCliente.observer_id).desc()).all())
            por_grupo = [{'label': grupos_map.get(g) or '(sin grupo)', 'count': int(n)}
                         for g, n in por_grupo]

            por_cat = (session.query(database.ObsCliente.categoria_observer,
                                     _f.count(database.ObsCliente.observer_id))
                       .group_by(database.ObsCliente.categoria_observer)
                       .order_by(_f.count(database.ObsCliente.observer_id).desc()).all())
            por_cat = [{'label': cats_map.get(c) or '(sin categoría)', 'count': int(n)}
                       for c, n in por_cat]

            por_loc = (session.query(database.ObsCliente.localidad,
                                     _f.count(database.ObsCliente.observer_id))
                       .filter(database.ObsCliente.localidad.isnot(None),
                               database.ObsCliente.localidad != '')
                       .group_by(database.ObsCliente.localidad)
                       .order_by(_f.count(database.ObsCliente.observer_id).desc())
                       .limit(15).all())
            por_loc = [{'label': l or '—', 'count': int(n)} for l, n in por_loc]

            por_prov = (session.query(database.ObsCliente.provincia,
                                      _f.count(database.ObsCliente.observer_id))
                        .filter(database.ObsCliente.provincia.isnot(None),
                                database.ObsCliente.provincia != '')
                        .group_by(database.ObsCliente.provincia)
                        .order_by(_f.count(database.ObsCliente.observer_id).desc()).all())
            por_prov = [{'label': p or '—', 'count': int(n)} for p, n in por_prov]

            # Extensión local
            n_ext = session.query(database.Cliente).count()
            n_wa = session.query(database.Cliente).filter(database.Cliente.whatsapp.isnot(None),
                                                          database.Cliente.whatsapp != '').count()
            n_mail = session.query(database.Cliente).filter(database.Cliente.email.isnot(None),
                                                            database.Cliente.email != '').count()
            n_notas = session.query(database.Cliente).filter(database.Cliente.notas.isnot(None),
                                                             database.Cliente.notas != '').count()
            n_tags = session.query(database.Cliente).filter(database.Cliente.tags.isnot(None),
                                                            database.Cliente.tags != '').count()

        return render_template('clientes_stats.html',
                               total=total,
                               por_grupo=por_grupo, por_cat=por_cat,
                               por_loc=por_loc, por_prov=por_prov,
                               n_ext=n_ext, n_wa=n_wa, n_mail=n_mail,
                               n_notas=n_notas, n_tags=n_tags)

    @app.route('/clientes/<int:observer_id>')
    @login_required
    def cliente_detail(observer_id):
        with database.get_db() as session:
            obs = session.get(database.ObsCliente, observer_id)
            if not obs:
                flash('Cliente no encontrado.', 'error')
                return redirect(url_for('clientes_list'))

            grupo = (session.get(database.ObsGrupoCliente, obs.grupo_observer)
                     if obs.grupo_observer else None)
            categoria = (session.get(database.ObsCategoriaCliente, obs.categoria_observer)
                         if obs.categoria_observer else None)
            ext = session.query(database.Cliente).filter_by(observer_id=observer_id).first()
            # Ubicaciones que el cliente compartió (pin del bot) — antes solo las veía
            # el bot; ahora viven colgadas de cliente_id y se muestran acá.
            from bot import store as _store
            domicilios = _store.listar_domicilios_de_cliente(observer_id=observer_id)

            return render_template('cliente_detail.html',
                                   obs=obs,
                                   grupo=grupo.descripcion if grupo else None,
                                   categoria=categoria.descripcion if categoria else None,
                                   ext=ext, domicilios=domicilios)

    @app.route('/intelligence/recurrentes')
    @login_required
    def intelligence_recurrentes():
        """Identifica combos cliente×producto con patrón de compra recurrente.
        Es la pantalla "predictiva": muestra qué clientes vienen pronto a buscar
        qué medicamento, qué clientes están atrasados, y los productos con más
        base de clientes habituales.
        """
        import statistics
        from datetime import date as _date
        from datetime import timedelta

        from database import ObsCliente, ObsProducto, ObsVentaDetalle

        # Filtros del query
        min_compras = max(2, int(request.args.get('min_compras', 3)))
        max_cv = float(request.args.get('max_cv', 0.5))  # 0.5 = semi-regular o mejor
        dias_max_ultima = int(request.args.get('dias_max_ultima', 180))
        producto_q = (request.args.get('producto') or '').strip().lower()
        cliente_q = (request.args.get('cliente') or '').strip().lower()
        orden = request.args.get('orden', 'urgencia')  # urgencia | freq | n
        limit = max(20, min(int(request.args.get('limit', 300)), 1000))

        hoy = _date.today()
        desde = hoy - timedelta(days=dias_max_ultima * 2)  # margen

        with database.get_db() as session:
            # Query agregado por (cliente, producto): n, min/max fecha
            base = (session.query(
                        ObsVentaDetalle.cliente_observer.label('cli'),
                        ObsVentaDetalle.producto_observer.label('prod'),
                        _f.count(ObsVentaDetalle.id_producto_vendido).label('n'),
                        _f.min(ObsVentaDetalle.fecha_operacion).label('primera'),
                        _f.max(ObsVentaDetalle.fecha_operacion).label('ultima'),
                        _f.sum(ObsVentaDetalle.cantidad).label('cant_total'),
                        _f.sum(ObsVentaDetalle.importe).label('importe_total'),
                    )
                    .filter(
                        ObsVentaDetalle.cliente_observer.isnot(None),
                        ObsVentaDetalle.fecha_operacion >= desde,
                        # NO filtrar tipo_operacion: devoluciones vienen con
                        # cantidad/importe<0, sum() neto descuenta solas.
                        # Ver helpers.ventas_periodo_filter para la regla.
                    )
                    .group_by(ObsVentaDetalle.cliente_observer,
                              ObsVentaDetalle.producto_observer)
                    .having(_f.count(ObsVentaDetalle.id_producto_vendido) >= min_compras)
                    .all())
            # Filtrar última compra dentro de la ventana de actividad
            base = [r for r in base
                    if r.ultima and (hoy - r.ultima.date()).days <= dias_max_ultima]
            if not base:
                return render_template('intelligence_recurrentes.html',
                    rows=[], total=0, filtros={
                        'min_compras': min_compras, 'max_cv': max_cv,
                        'dias_max_ultima': dias_max_ultima,
                        'producto_q': producto_q, 'cliente_q': cliente_q,
                        'orden': orden, 'limit': limit,
                    })

            # Para cada combo, traer las fechas para calcular CV/freq
            # (1 query en bloque, filtrada por los pares).
            par_keys = {(r.cli, r.prod) for r in base}
            cli_ids = list({r.cli for r in base})
            prod_ids = list({r.prod for r in base})
            todas = (session.query(
                        ObsVentaDetalle.cliente_observer,
                        ObsVentaDetalle.producto_observer,
                        ObsVentaDetalle.fecha_operacion,
                    )
                    .filter(
                        ObsVentaDetalle.cliente_observer.in_(cli_ids),
                        ObsVentaDetalle.producto_observer.in_(prod_ids),
                        ObsVentaDetalle.fecha_operacion >= desde,
                        # NO filtrar tipo_operacion (sum neto descuenta dev.).
                    )
                    .order_by(ObsVentaDetalle.cliente_observer,
                              ObsVentaDetalle.producto_observer,
                              ObsVentaDetalle.fecha_operacion).all())
            from collections import defaultdict
            fechas_por_par = defaultdict(list)
            for cli, prod, fop in todas:
                if (cli, prod) in par_keys and fop:
                    fechas_por_par[(cli, prod)].append(fop)

            # Resolver nombres
            cli_map = {c.observer_id: c.apellido_nombre
                       for c in session.query(ObsCliente).filter(ObsCliente.observer_id.in_(cli_ids)).all()}
            prod_map = {p.observer_id: p.descripcion
                        for p in session.query(ObsProducto).filter(ObsProducto.observer_id.in_(prod_ids)).all()}

            # Procesar cada combo
            rows = []
            for r in base:
                fechas = fechas_por_par.get((r.cli, r.prod), [])
                if len(fechas) < 2:
                    continue
                deltas = [(fechas[i] - fechas[i-1]).days
                          for i in range(1, len(fechas)) if fechas[i] != fechas[i-1]]
                if not deltas:
                    continue
                media = statistics.mean(deltas)
                desv = statistics.pstdev(deltas) if len(deltas) > 1 else 0
                if media <= 0:
                    continue
                cv = desv / media
                if cv > max_cv:
                    continue
                proxima = fechas[-1].date() + timedelta(days=int(round(media)))
                dias_para = (proxima - hoy).days
                cli_nombre = cli_map.get(r.cli, f'#{r.cli}')
                prod_nombre = prod_map.get(r.prod, f'#{r.prod}')
                # Filtros de búsqueda
                if producto_q and producto_q not in (prod_nombre or '').lower():
                    continue
                if cliente_q and cliente_q not in (cli_nombre or '').lower():
                    continue
                rows.append({
                    'cli_id': r.cli,
                    'prod_id': r.prod,
                    'cli_nombre': cli_nombre,
                    'prod_nombre': prod_nombre,
                    'n': r.n,
                    'cant_total': float(r.cant_total or 0),
                    'importe_total': float(r.importe_total or 0),
                    'primera': r.primera.date() if r.primera else None,
                    'ultima': r.ultima.date() if r.ultima else None,
                    'dias_freq': round(media, 0),
                    'cv': round(cv, 2),
                    'proxima': proxima,
                    'dias_para': dias_para,
                    'estado': ('atrasada' if dias_para < -2
                               else 'hoy' if -2 <= dias_para <= 2
                               else 'pronto' if dias_para <= 7
                               else 'futura'),
                })
            # Ordenar
            if orden == 'freq':
                rows.sort(key=lambda x: x['dias_freq'])
            elif orden == 'n':
                rows.sort(key=lambda x: -x['n'])
            else:  # urgencia
                rows.sort(key=lambda x: (x['dias_para'], x['cv']))
            total = len(rows)
            rows = rows[:limit]

            # Stats de productos: top productos por cantidad de clientes recurrentes
            prod_stats_map = defaultdict(lambda: {'clientes': 0, 'compras': 0, 'unidades': 0, 'importe': 0})
            for r in rows:
                ps = prod_stats_map[(r['prod_id'], r['prod_nombre'])]
                ps['clientes'] += 1
                ps['compras'] += r['n']
                ps['unidades'] += r['cant_total']
                ps['importe'] += r['importe_total']
            top_productos = sorted([
                {'id': k[0], 'nombre': k[1], **v} for k, v in prod_stats_map.items()
            ], key=lambda x: -x['clientes'])[:20]

            return render_template('intelligence_recurrentes.html',
                rows=rows, total=total, top_productos=top_productos,
                filtros={
                    'min_compras': min_compras, 'max_cv': max_cv,
                    'dias_max_ultima': dias_max_ultima,
                    'producto_q': producto_q, 'cliente_q': cliente_q,
                    'orden': orden, 'limit': limit,
                })

    @app.route('/cliente/<int:cliente_id>/producto/<int:producto_id>/comportamiento')
    @login_required
    def cliente_producto_comportamiento(cliente_id, producto_id):
        """Análisis predictivo del comportamiento de un cliente con un producto.

        Calcula frecuencia, patrón, próxima compra estimada, médicos habituales,
        OS principal, co-compras frecuentes (otros productos en la misma operación).
        """
        import statistics
        from collections import Counter
        from datetime import timedelta

        from database import (
            ObsCliente,
            ObsMedico,
            ObsObraSocial,
            ObsPlan,
            ObsProducto,
            ObsVentaDetalle,
        )

        with database.get_db() as session:
            cliente = session.get(ObsCliente, cliente_id)
            producto = session.get(ObsProducto, producto_id)
            if not cliente or not producto:
                flash('Cliente o producto no encontrado.', 'error')
                return redirect(url_for('clientes_list'))

            # Ventas (tipo='V' o NULL legacy) del producto al cliente
            ventas = (session.query(ObsVentaDetalle)
                      .filter(
                          ObsVentaDetalle.cliente_observer == cliente_id,
                          ObsVentaDetalle.producto_observer == producto_id,
                          # NO filtrar tipo_operacion (sum neto). Las devoluciones
                          # quedan separadas en `anomalias` abajo (tipo IN D/NC).
                      )
                      .order_by(ObsVentaDetalle.fecha_operacion)
                      .all())
            # Devoluciones / NC (anomalías)
            anomalias = (session.query(ObsVentaDetalle)
                         .filter(
                             ObsVentaDetalle.cliente_observer == cliente_id,
                             ObsVentaDetalle.producto_observer == producto_id,
                             ObsVentaDetalle.tipo_operacion.in_(('D', 'NC')),
                         )
                         .order_by(ObsVentaDetalle.fecha_operacion).all())

            # Stats temporales
            fechas = [v.fecha_operacion for v in ventas if v.fecha_operacion]
            n_compras = len(fechas)
            stats = {
                'n_compras': n_compras,
                'primera': fechas[0].date() if fechas else None,
                'ultima': fechas[-1].date() if fechas else None,
                'dias_entre_promedio': None,
                'dias_entre_std': None,
                'patron': '—',
                'proxima_estimada': None,
                'dias_para_proxima': None,
            }
            if n_compras >= 2:
                deltas = [(fechas[i] - fechas[i-1]).days
                          for i in range(1, n_compras) if fechas[i] != fechas[i-1]]
                if deltas:
                    media = statistics.mean(deltas)
                    desv = statistics.pstdev(deltas) if len(deltas) > 1 else 0
                    stats['dias_entre_promedio'] = round(media, 1)
                    stats['dias_entre_std'] = round(desv, 1)
                    cv = (desv / media) if media else 0
                    if cv < 0.25:
                        stats['patron'] = 'regular (crónico/recurrente)'
                    elif cv < 0.6:
                        stats['patron'] = 'semi-regular'
                    else:
                        stats['patron'] = 'esporádico (irregular)'
                    proxima = fechas[-1].date() + timedelta(days=int(round(media)))
                    stats['proxima_estimada'] = proxima
                    from datetime import date as _date
                    hoy = _date.today()
                    stats['dias_para_proxima'] = (proxima - hoy).days
            elif n_compras == 1:
                stats['patron'] = 'compra única'

            # Cantidad
            cants = [float(v.cantidad or 0) for v in ventas]
            cant_stats = {
                'tipica': statistics.median(cants) if cants else 0,
                'min': min(cants) if cants else 0,
                'max': max(cants) if cants else 0,
                'total': sum(cants),
            }

            # Importes (suma)
            gasto_total = float(sum(v.importe or 0 for v in ventas))
            ahorro_os = float(sum(v.importe_a_cargo_os or 0 for v in ventas))
            efectivo = float(sum(v.importe_efectivo or 0 for v in ventas))
            tarjeta = float(sum(v.importe_tarjeta or 0 for v in ventas))
            cta_cte = float(sum(v.importe_cuenta_corriente or 0 for v in ventas))
            cheque = float(sum(v.importe_cheque or 0 for v in ventas))

            # Médico habitual
            medicos_count = Counter(v.medico_observer for v in ventas if v.medico_observer)
            medicos_top = []
            if medicos_count:
                ids_top = [mid for mid, _ in medicos_count.most_common(3)]
                medicos_map = {m.observer_id: m.descripcion for m in
                               session.query(ObsMedico).filter(ObsMedico.observer_id.in_(ids_top)).all()}
                for mid, n in medicos_count.most_common(3):
                    medicos_top.append({
                        'id': mid,
                        'nombre': medicos_map.get(mid, f'#{mid}'),
                        'n': n,
                    })

            # OS / Plan principal
            os_count = Counter(v.obra_social_observer for v in ventas if v.obra_social_observer)
            os_top = []
            if os_count:
                ids_top = [oid for oid, _ in os_count.most_common(3)]
                os_map = {o.observer_id: o.descripcion for o in
                          session.query(ObsObraSocial).filter(ObsObraSocial.observer_id.in_(ids_top)).all()}
                for oid, n in os_count.most_common(3):
                    os_top.append({
                        'id': oid,
                        'nombre': os_map.get(oid, f'#{oid}'),
                        'n': n,
                        'pct': round(100 * n / n_compras, 1) if n_compras else 0,
                    })

            n_particulares = sum(1 for v in ventas if v.es_venta_particular)

            # Co-compras: otros productos en la misma operación
            cocompra = []
            ops_ids = list({v.id_operacion for v in ventas if v.id_operacion})
            if ops_ids:
                otros = (session.query(ObsVentaDetalle.producto_observer,
                                        _f.count(ObsVentaDetalle.id_producto_vendido).label('n'),
                                        _f.sum(ObsVentaDetalle.cantidad).label('cant_total'))
                         .filter(ObsVentaDetalle.id_operacion.in_(ops_ids),
                                 ObsVentaDetalle.producto_observer != producto_id,
                                 # NO filtrar tipo_operacion (sum neto).
                                 )
                         .group_by(ObsVentaDetalle.producto_observer)
                         .order_by(_f.count(ObsVentaDetalle.id_producto_vendido).desc())
                         .limit(15).all())
                if otros:
                    pids = [r.producto_observer for r in otros]
                    prod_map = {p.observer_id: p.descripcion for p in
                                session.query(ObsProducto)
                                .filter(ObsProducto.observer_id.in_(pids)).all()}
                    for r in otros:
                        cocompra.append({
                            'id': r.producto_observer,
                            'nombre': prod_map.get(r.producto_observer, f'#{r.producto_observer}'),
                            'n': r.n,
                            'pct': round(100 * r.n / n_compras, 1) if n_compras else 0,
                            'cant_total': float(r.cant_total or 0),
                        })

            # Serie temporal para el chart
            timeline = []
            for v in ventas:
                if v.fecha_operacion:
                    timeline.append({
                        'fecha': v.fecha_operacion.strftime('%Y-%m-%d'),
                        'cantidad': float(v.cantidad or 0),
                        'importe': float(v.importe or 0),
                        'medico': v.medico_observer,
                        'os': v.obra_social_observer,
                    })

            return render_template(
                'cliente_producto_comportamiento.html',
                cliente=cliente, producto=producto,
                stats=stats, cant_stats=cant_stats,
                gasto_total=gasto_total, ahorro_os=ahorro_os,
                pago={'efectivo': efectivo, 'tarjeta': tarjeta,
                      'cta_cte': cta_cte, 'cheque': cheque},
                medicos_top=medicos_top, os_top=os_top,
                n_particulares=n_particulares,
                cocompra=cocompra, timeline=timeline,
                anomalias=len(anomalias),
            )

    @app.route('/clientes/<int:observer_id>/edit', methods=['POST'])
    @login_required
    def cliente_edit(observer_id):
        """ABM de la extensión local (Cliente). Crea o actualiza."""
        with database.get_db() as session:
            obs = session.get(database.ObsCliente, observer_id)
            if not obs:
                flash('Cliente no encontrado.', 'error')
                return redirect(url_for('clientes_list'))

            ext = session.query(database.Cliente).filter_by(observer_id=observer_id).first()
            if not ext:
                ext = database.Cliente(observer_id=observer_id)
                session.add(ext)

            ext.notas = (request.form.get('notas') or '').strip() or None
            ext.tags = (request.form.get('tags') or '').strip() or None
            ext.whatsapp = (request.form.get('whatsapp') or '').strip() or None
            ext.email = (request.form.get('email') or '').strip() or None
            fn = (request.form.get('fecha_nacimiento') or '').strip()
            if fn:
                try:
                    ext.fecha_nacimiento = datetime.strptime(fn, '%Y-%m-%d').date()
                except ValueError:
                    flash('Fecha de nacimiento inválida.', 'warning')
                    ext.fecha_nacimiento = None
            else:
                ext.fecha_nacimiento = None

            session.commit()
            flash('Datos locales actualizados.', 'success')
            return redirect(url_for('cliente_detail', observer_id=observer_id))

    @app.route('/clientes/<int:observer_id>/borrar-extension', methods=['POST'])
    @login_required
    def cliente_borrar_extension(observer_id):
        """Borra la extensión local (sin tocar el cliente del ObServer)."""
        with database.get_db() as session:
            ext = session.query(database.Cliente).filter_by(observer_id=observer_id).first()
            if ext:
                session.delete(ext)
                session.commit()
                flash('Datos locales eliminados.', 'success')
        return redirect(url_for('cliente_detail', observer_id=observer_id))

    # ── API JSON usada por cliente_picker (static/js/cliente_picker.js) ─────
    # Movido desde routes/reparto.py el 2026-06-10 (commit f0eca4e). Los paths
    # viejos `/reparto/api/*` y `/reparto/cliente*` se borraron el mismo día
    # (commit ed7c0fb) tras migrar todos los callers.

    @app.route('/api/clientes/buscar')
    @login_required
    def api_clientes_buscar():
        if not _api_ok():
            return jsonify({'error': 'sin permiso'}), 403
        from bot import store
        return jsonify({'clientes': store.buscar_clientes_unificado(
            request.args.get('q', ''), limite=12)})

    @app.route('/api/clientes/ficha')
    @login_required
    def api_clientes_ficha():
        """Ficha de un cliente para precarga del form.
        Acepta ?cliente_id= o ?observer_id= (resuelve con get_or_create)."""
        if not _api_ok():
            return jsonify({'error': 'sin permiso'}), 403
        from bot import store
        cliente_id = request.args.get('cliente_id', type=int)
        observer_id = request.args.get('observer_id', type=int)
        if not cliente_id and not observer_id:
            return jsonify({'error': 'falta cliente_id o observer_id'}), 400
        with database.get_db() as s:
            if not cliente_id and observer_id:
                cliente_id = database.get_or_create_cliente(
                    s, observer_id=observer_id, creado_por=current_user.id)
                s.commit()
            ficha = store._ficha_de_cliente(s, cliente_id)
            if ficha:
                ficha['domicilios'] = store.listar_domicilios_de_cliente(
                    cliente_id=cliente_id)
                c = s.get(database.Cliente, cliente_id)
                if c:
                    ficha['raw'] = {
                        'nombre': c.nombre or '', 'apellido': c.apellido or '',
                        'dni': c.dni or '', 'telefono': c.telefono or '',
                        'domicilio': c.domicilio or '', 'ciudad': c.ciudad or '',
                    }
            return jsonify(ficha or {'error': 'no encontrado'}), 200 if ficha else 404

    @app.route('/api/clientes', methods=['POST'])
    @login_required
    def api_clientes_crear():
        """Alta de un cliente nuevo (lead puro, sin ObServer)."""
        if not _api_ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        b = request.json or {}
        lead = {}
        for k in ('nombre', 'apellido', 'dni', 'telefono', 'domicilio', 'ciudad'):
            v = (b.get(k) or '').strip()
            if v:
                lead[k] = v
        if not lead:
            return jsonify({'ok': False, 'error': 'sin datos'}), 400
        with database.get_db() as s:
            cid = database.get_or_create_cliente(
                s, lead=lead, creado_por=current_user.id)
            s.commit()
        return jsonify({'ok': True, 'cliente_id': cid})

    @app.route('/api/clientes/<int:cid>', methods=['POST'])
    @login_required
    def api_clientes_editar(cid):
        """Edita campos de la fila Clientes (NUNCA obs_clientes)."""
        if not _api_ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        b = request.json or {}
        with database.get_db() as s:
            c = s.get(database.Cliente, cid)
            if not c:
                return jsonify({'ok': False, 'error': 'no existe'}), 404
            for k in ('nombre', 'apellido', 'dni', 'telefono', 'domicilio',
                       'ciudad', 'notas'):
                if k in b:
                    setattr(c, k, (b[k] or '').strip() or None)
            c.actualizado_en = database.now_ar()
            s.commit()
        return jsonify({'ok': True})

    @app.route('/api/clientes/observer/<int:oid>/domicilios')
    @login_required
    def api_clientes_domicilios_observer(oid):
        if not _api_ok():
            return jsonify({'error': 'sin permiso'}), 403
        from bot import store
        return jsonify({'domicilios': store.listar_domicilios_de_cliente(observer_id=oid)})

    @app.route('/api/clientes/geocodificar')
    @login_required
    def api_clientes_geocodificar():
        if not _api_ok():
            return jsonify({'error': 'sin permiso'}), 403
        q = (request.args.get('q') or '').strip()
        loc = (request.args.get('loc') or '').strip() or None
        if len(q) < 3:
            return jsonify({'sugerencias': []})
        from bot import envio as _envio
        return jsonify({'sugerencias': _envio.geocodificar_sugerencias(q, localidad=loc)})

    @app.route('/api/clientes/separar-direccion', methods=['POST'])
    @login_required
    def api_clientes_separar_direccion():
        """Server-side parser: separa 'calle+número' de
        'piso / depto / referencia' para no duplicar lógica en JS."""
        if not _api_ok():
            return jsonify({'error': 'sin permiso'}), 403
        b = request.json or {}
        texto = (b.get('texto') or b.get('direccion') or '').strip()
        from bot.direcciones import separar_direccion
        return jsonify(separar_direccion(texto))

    @app.route('/api/clientes/domicilios/<int:dom_id>/geo', methods=['POST'])
    @login_required
    def api_clientes_domicilio_set_geo(dom_id):
        if not _api_ok():
            return jsonify({'ok': False, 'error': 'sin permiso'}), 403
        b = request.json or {}
        try:
            lat = float(b['lat'])
            lng = float(b['lng'])
        except (KeyError, TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'lat/lng inválidos'}), 400
        with database.get_db() as s:
            d = s.get(database.DomicilioCliente, dom_id)
            if not d:
                return jsonify({'ok': False, 'error': 'domicilio no existe'}), 404
            d.lat, d.lng = lat, lng
            d.geo_actualizado_en = database.now_ar()
            s.commit()
            return jsonify({'ok': True, 'lat': lat, 'lng': lng,
                            'geo_actualizado_en': d.geo_actualizado_en.isoformat()})
