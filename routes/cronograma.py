"""Cronograma de pedidos por proveedor.

Dos vistas:

1. `/cronograma` (default) → AGENDA: timeline con pedidos ya ejecutados + futuros
   planificados, cruzando `ProveedorCronograma` (config) con `PedidoEmitido` y
   `Pedido` (ejecutados). Toggle día/semana/mes y filtro por proveedor.

2. `/cronograma/config` → CRUD de cronogramas. Una fila por (proveedor,
   tipo_pedido): cadencia, próxima fecha, horas entre pedidos (reposición).

Reglas del cruce planificado ↔ real:
- Tolerancia ±3 días para considerar un evento "cumplido": si hay un Pedido o
  PedidoEmitido al mismo proveedor en esa ventana, el evento se da por hecho.
- Evento sin match y fecha esperada < hoy → atrasado.
- Evento sin match y fecha esperada >= hoy → pendiente.
"""
from datetime import date as _date
from datetime import timedelta

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import or_

import database
from database import Laboratorio, Pedido, PedidoEmitido, ProcesoCompra, ProveedorCronograma, Provider, get_db

_TIPOS_VALIDOS = ('programado',)
_PARTNER_TIPOS_VALIDOS = ('laboratorio', 'drogueria')
_TOLERANCIA_CUMPLIDO_DIAS = 3


def _parse_fecha(s):
    if not s:
        return None
    try:
        return _date.fromisoformat(s)
    except ValueError:
        return None


def _generar_eventos_planificados(crons, desde, hasta):
    """Para cada cron activo, genera fechas esperadas dentro del rango.

    Solo `programado`: arranca en `proxima_fecha` (o hoy si no está) y suma
    `cadencia_dias`. La reposición no se programa — vive como ejecutados de
    pedido/día cruzados desde `_consultar_eventos_ejecutados`.
    """
    eventos = []
    for cron in crons:
        if not cron.activo:
            continue
        base = cron.proxima_fecha or _date.today()
        cad = cron.cadencia_dias or 0
        cur = base
        if cad > 0:
            while cur > desde:
                cur = cur - timedelta(days=cad)
            while cur < desde:
                cur = cur + timedelta(days=cad)
        while cur <= hasta:
            eventos.append({
                'cron_id': cron.id,
                'partner_tipo': cron.partner_tipo,
                'partner_id': cron.proveedor_id,
                'canal_drog_id': cron.canal_drog_id,
                'fecha': cur,
                'tipo_pedido': 'programado',
                'cadencia_dias': cad,
                'notas': cron.notas or '',
            })
            if cad <= 0:
                break
            cur = cur + timedelta(days=cad)
    return eventos


def _consultar_eventos_ejecutados(session, desde, hasta):
    """Devuelve eventos reales (pedidos emitidos / pedidos con canal) en rango.

    Cada evento tiene `(partner_tipo, partner_id)`:
      - PedidoEmitido → ('drogueria', drogueria_id)
      - Pedido con canal='drogueria' → ('drogueria', partner_id)
      - Pedido con canal='laboratorio' → ('laboratorio', partner_id)
    """
    eventos = []
    q_pe = (session.query(PedidoEmitido)
            .filter(PedidoEmitido.fecha >= desde,
                    PedidoEmitido.fecha < hasta + timedelta(days=1)))
    for p in q_pe.all():
        eventos.append({
            'fuente': 'PedidoEmitido',
            'pedido_id': p.id,
            'partner_tipo': 'drogueria',
            'partner_id': p.drogueria_id,
            'fecha': p.fecha.date() if p.fecha else None,
            'datetime': p.fecha,
            'origen': p.origen,
            'total_items': p.total_items,
            'total_unidades': p.total_unidades,
            'estado': p.estado,
        })
    q_p = (session.query(Pedido)
           .filter(Pedido.creado_en >= desde,
                   Pedido.creado_en < hasta + timedelta(days=1),
                   Pedido.canal.isnot(None),
                   Pedido.partner_id.isnot(None)))
    for p in q_p.all():
        eventos.append({
            'fuente': 'Pedido',
            'pedido_id': p.id,
            'partner_tipo': p.canal,  # 'laboratorio' | 'drogueria'
            'partner_id': p.partner_id,
            'fecha': p.creado_en.date() if p.creado_en else None,
            'datetime': p.creado_en,
            'origen': p.origen,
            'laboratorio': p.laboratorio,
            'estado': p.estado,
        })
    return eventos


def _cruzar(planificados, ejecutados):
    """Marca cada planificado con estado: cumplido | atrasado | pendiente.

    Cumplido si hay un ejecutado al MISMO (partner_tipo, partner_id) o, cuando
    el planificado es lab con canal_drog, también un ejecutado a esa drog
    (que efectivamente recibe el pedido), todo en ±_TOLERANCIA_CUMPLIDO_DIAS.
    """
    hoy = _date.today()
    by_partner = {}
    for ev in ejecutados:
        key = (ev['partner_tipo'], ev['partner_id'])
        by_partner.setdefault(key, []).append(ev)
    out = []
    for pl in planificados:
        keys = [(pl['partner_tipo'], pl['partner_id'])]
        # Si el partner es un lab con canal por droguería, los pedidos a esa
        # drog en la misma ventana cuentan como cumplimiento (es el ingreso real).
        if pl['partner_tipo'] == 'laboratorio' and pl.get('canal_drog_id'):
            keys.append(('drogueria', pl['canal_drog_id']))
        match = None
        for k in keys:
            for ej in by_partner.get(k, []):
                if not ej['fecha']:
                    continue
                delta = abs((ej['fecha'] - pl['fecha']).days)
                if delta <= _TOLERANCIA_CUMPLIDO_DIAS:
                    match = ej
                    break
            if match:
                break
        if match:
            estado = 'cumplido'
        elif pl['fecha'] < hoy:
            estado = 'atrasado'
        else:
            estado = 'pendiente'
        out.append({**pl, 'estado': estado,
                    'match_pedido_id': match['pedido_id'] if match else None,
                    'match_fuente': match['fuente'] if match else None})
    return out


def init_app(app):

    def _resolver_partner_maps(session, crons_o_eventos):
        """Devuelve dos dicts: labs_map[lab_id]={nombre} y drogs_map[drog_id]={nombre}.
        Carga ambos en una sola pasada para todos los partners y canales referenciados."""
        lab_ids, drog_ids = set(), set()
        for x in crons_o_eventos:
            tipo = getattr(x, 'partner_tipo', None) or x.get('partner_tipo')
            pid  = getattr(x, 'proveedor_id', None) or x.get('partner_id')
            canal = getattr(x, 'canal_drog_id', None) if hasattr(x, 'canal_drog_id') else x.get('canal_drog_id')
            if tipo == 'laboratorio' and pid:
                lab_ids.add(pid)
            elif tipo == 'drogueria' and pid:
                drog_ids.add(pid)
            if canal:
                drog_ids.add(canal)
        labs_map = {l.id: {'nombre': l.nombre}
                    for l in session.query(Laboratorio).filter(Laboratorio.id.in_(lab_ids)).all()
                    } if lab_ids else {}
        drogs_map = {p.id: {'nombre': p.razon_social, 'tipo': p.tipo}
                     for p in session.query(Provider).filter(Provider.id.in_(drog_ids)).all()
                     } if drog_ids else {}
        return labs_map, drogs_map

    def _partner_label(ev_or_cron, labs_map, drogs_map):
        """Devuelve nombre del partner principal según tipo."""
        tipo = ev_or_cron.get('partner_tipo')
        pid = ev_or_cron.get('partner_id')
        if tipo == 'laboratorio':
            return labs_map.get(pid, {}).get('nombre', f'Lab#{pid}')
        return drogs_map.get(pid, {}).get('nombre', f'Drog#{pid}')

    @app.route('/cronograma')
    @login_required
    def cronograma_list():
        """Vista agenda: planificados + ejecutados, filtros separados lab/drog."""
        lab_filtro  = request.args.get('lab_id',  type=int)
        drog_filtro = request.args.get('drog_id', type=int)
        vista = request.args.get('vista', 'semana')
        if vista not in ('dia', 'semana', 'mes'):
            vista = 'semana'
        ref_str = request.args.get('ref') or ''
        ref = _parse_fecha(ref_str) or _date.today()

        if vista == 'dia':
            desde = ref
            hasta = ref
            label = ref.strftime('%A %d/%m/%Y')
            prev_ref = ref - timedelta(days=1)
            next_ref = ref + timedelta(days=1)
        elif vista == 'mes':
            primero = ref.replace(day=1)
            if primero.month == 12:
                next_mes = primero.replace(year=primero.year + 1, month=1)
            else:
                next_mes = primero.replace(month=primero.month + 1)
            desde = primero
            hasta = next_mes - timedelta(days=1)
            label = primero.strftime('%B %Y').capitalize()
            prev_ref = (primero - timedelta(days=1)).replace(day=1)
            next_ref = next_mes
        else:
            lunes = ref - timedelta(days=ref.weekday())
            desde = lunes
            hasta = lunes + timedelta(days=6)
            label = f'Semana {desde.strftime("%d/%m")} – {hasta.strftime("%d/%m/%Y")}'
            prev_ref = lunes - timedelta(days=7)
            next_ref = lunes + timedelta(days=7)

        with get_db() as session:
            q_cron = session.query(ProveedorCronograma)
            if lab_filtro:
                q_cron = q_cron.filter(
                    ProveedorCronograma.partner_tipo == 'laboratorio',
                    ProveedorCronograma.proveedor_id == lab_filtro,
                )
            if drog_filtro:
                # drog en filtro: matchea si es el partner directo O el canal de un lab.
                q_cron = q_cron.filter(or_(
                    (ProveedorCronograma.partner_tipo == 'drogueria') &
                    (ProveedorCronograma.proveedor_id == drog_filtro),
                    ProveedorCronograma.canal_drog_id == drog_filtro,
                ))
            crons = q_cron.all()

            planif = _generar_eventos_planificados(crons, desde, hasta)
            ejec = _consultar_eventos_ejecutados(session, desde, hasta)
            planif_cruzados = _cruzar(planif, ejec)

            labs_map, drogs_map = _resolver_partner_maps(session, planif_cruzados + ejec)

            # Map proceso_id por pedido_id: si un Pedido tiene un ProcesoCompra
            # asociado, preferimos linkear al proceso (vista integral del ciclo
            # análisis → pedido → factura → cruce → reclamo). Solo aplica para
            # `Pedido` — los PedidoEmitido (matriz drog) no tienen proceso.
            _pedido_ids = set()
            for ev in ejec:
                if ev.get('fuente') == 'Pedido' and ev.get('pedido_id'):
                    _pedido_ids.add(ev['pedido_id'])
            for ev in planif_cruzados:
                if ev.get('match_fuente') == 'Pedido' and ev.get('match_pedido_id'):
                    _pedido_ids.add(ev['match_pedido_id'])
            proceso_por_pedido = {}
            if _pedido_ids:
                proceso_por_pedido = {p.pedido_id: p.id
                                      for p in (session.query(ProcesoCompra)
                                                .filter(ProcesoCompra.pedido_id.in_(_pedido_ids))
                                                .all())}

            dias = {}
            d = desde
            while d <= hasta:
                dias[d] = {'planificados': [], 'ejecutados': []}
                d = d + timedelta(days=1)
            for ev in planif_cruzados:
                if ev['fecha'] in dias:
                    canal_nombre = (drogs_map.get(ev['canal_drog_id'], {}).get('nombre')
                                    if ev.get('canal_drog_id') else None)
                    dias[ev['fecha']]['planificados'].append({
                        **ev,
                        'partner_nombre': _partner_label(ev, labs_map, drogs_map),
                        'canal_nombre':   canal_nombre,
                        'fecha_iso':      ev['fecha'].isoformat(),
                    })
            for ev in ejec:
                if ev['fecha'] and ev['fecha'] in dias:
                    dias[ev['fecha']]['ejecutados'].append({
                        **ev,
                        'partner_nombre': _partner_label(ev, labs_map, drogs_map),
                        'hora': ev['datetime'].strftime('%H:%M') if ev.get('datetime') else '',
                    })

            dias_list = [{'fecha': d, 'fecha_iso': d.isoformat(),
                          'fecha_label': d.strftime('%a %d/%m'),
                          'es_hoy': d == _date.today(),
                          **dias[d]}
                         for d in sorted(dias.keys())]

            laboratorios = [{'id': l.id, 'nombre': l.nombre}
                            for l in session.query(Laboratorio).order_by(Laboratorio.nombre).all()]
            droguerias = [{'id': p.id, 'nombre': p.razon_social}
                          for p in session.query(Provider)
                                          .filter(Provider.tipo == 'drogueria',
                                                  Provider.activo == True)  # noqa: E712
                                          .order_by(Provider.razon_social).all()]
            # Totales para el hero stats.
            total_cumplidos = sum(1 for e in planif_cruzados if e['estado'] == 'cumplido')
            total_pendientes = sum(1 for e in planif_cruzados if e['estado'] == 'pendiente')
            total_atrasados = sum(1 for e in planif_cruzados if e['estado'] == 'atrasado')
            total_eventos = len(planif_cruzados)
            total_ejec = len(ejec)
            # Nombre del filtro activo (para el título del hero).
            partner_filtro_nombre = None
            if lab_filtro:
                lab = session.get(Laboratorio, lab_filtro)
                if lab:
                    partner_filtro_nombre = lab.nombre
            elif drog_filtro:
                drog = session.get(Provider, drog_filtro)
                if drog:
                    partner_filtro_nombre = drog.razon_social

        return render_template('cronograma.html',
                               dias=dias_list,
                               proceso_por_pedido=proceso_por_pedido,
                               laboratorios=laboratorios,
                               droguerias=droguerias,
                               lab_filtro=lab_filtro,
                               drog_filtro=drog_filtro,
                               partner_filtro_nombre=partner_filtro_nombre,
                               total_eventos=total_eventos,
                               total_cumplidos=total_cumplidos,
                               total_pendientes=total_pendientes,
                               total_atrasados=total_atrasados,
                               total_ejec=total_ejec,
                               vista=vista,
                               label=label,
                               desde=desde, hasta=hasta,
                               ref_iso=ref.isoformat(),
                               prev_ref=prev_ref.isoformat(),
                               next_ref=next_ref.isoformat(),
                               hoy_iso=_date.today().isoformat())

    @app.route('/cronograma/config')
    @login_required
    def cronograma_config():
        lab_filtro  = request.args.get('lab_id',  type=int)
        drog_filtro = request.args.get('drog_id', type=int)
        with get_db() as session:
            q = session.query(ProveedorCronograma).filter(
                ProveedorCronograma.tipo_pedido == 'programado')
            if lab_filtro:
                q = q.filter(ProveedorCronograma.partner_tipo == 'laboratorio',
                             ProveedorCronograma.proveedor_id == lab_filtro)
            if drog_filtro:
                q = q.filter(or_(
                    (ProveedorCronograma.partner_tipo == 'drogueria') &
                    (ProveedorCronograma.proveedor_id == drog_filtro),
                    ProveedorCronograma.canal_drog_id == drog_filtro,
                ))
            crons = q.all()
            labs_map, drogs_map = _resolver_partner_maps(session, crons)
            rows = []
            for cron in crons:
                if cron.partner_tipo == 'laboratorio':
                    nombre = labs_map.get(cron.proveedor_id, {}).get('nombre', f'Lab#{cron.proveedor_id}')
                else:
                    nombre = drogs_map.get(cron.proveedor_id, {}).get('nombre', f'Drog#{cron.proveedor_id}')
                canal_nombre = (drogs_map.get(cron.canal_drog_id, {}).get('nombre')
                                if cron.canal_drog_id else None)
                rows.append({
                    'id': cron.id,
                    'partner_tipo': cron.partner_tipo,
                    'partner_id': cron.proveedor_id,
                    'partner_nombre': nombre,
                    'canal_drog_id': cron.canal_drog_id,
                    'canal_drog_nombre': canal_nombre,
                    'tipo_pedido': cron.tipo_pedido,
                    'cadencia_dias': cron.cadencia_dias,
                    'proxima_fecha': cron.proxima_fecha.isoformat() if cron.proxima_fecha else '',
                    'proxima_fecha_label': cron.proxima_fecha.strftime('%d/%m/%Y') if cron.proxima_fecha else '',
                    'activo': cron.activo,
                    'notas': cron.notas or '',
                })
            rows.sort(key=lambda r: r['partner_nombre'].lower())
            laboratorios = [{'id': l.id, 'nombre': l.nombre}
                            for l in session.query(Laboratorio).order_by(Laboratorio.nombre).all()]
            droguerias = [{'id': p.id, 'nombre': p.razon_social}
                          for p in session.query(Provider)
                                          .filter(Provider.tipo == 'drogueria',
                                                  Provider.activo == True)  # noqa: E712
                                          .order_by(Provider.razon_social).all()]
        return render_template('cronograma_config.html',
                               rows=rows,
                               laboratorios=laboratorios,
                               droguerias=droguerias,
                               lab_filtro=lab_filtro,
                               drog_filtro=drog_filtro)

    @app.route('/cronograma/nuevo')
    @login_required
    def cronograma_nuevo():
        with get_db() as session:
            droguerias = [{'id': p.id, 'nombre': p.razon_social}
                          for p in session.query(Provider)
                                          .filter(Provider.tipo == 'drogueria',
                                                  Provider.activo == True)  # noqa: E712
                                          .order_by(Provider.razon_social).all()]
        return render_template('cronograma_form.html',
                               cron=None,
                               droguerias=droguerias,
                               hoy_iso=_date.today().isoformat())

    @app.route('/cronograma/<int:cron_id>/editar')
    @login_required
    def cronograma_editar(cron_id):
        with get_db() as session:
            cron = session.get(ProveedorCronograma, cron_id)
            if not cron:
                flash('Cronograma no encontrado.', 'error')
                return redirect(url_for('cronograma_config'))
            # Resolver nombre del partner según tipo, y nombre de la drog canal.
            if cron.partner_tipo == 'laboratorio':
                lab = session.get(Laboratorio, cron.proveedor_id)
                partner_nombre = lab.nombre if lab else f'Lab#{cron.proveedor_id}'
            else:
                prov = session.get(Provider, cron.proveedor_id)
                partner_nombre = prov.razon_social if prov else f'Drog#{cron.proveedor_id}'
            canal_drog_nombre = None
            if cron.canal_drog_id:
                drog = session.get(Provider, cron.canal_drog_id)
                if drog:
                    canal_drog_nombre = drog.razon_social
            ctx = {
                'id': cron.id,
                'partner_tipo': cron.partner_tipo,
                'partner_id': cron.proveedor_id,
                'partner_nombre': partner_nombre,
                'canal_drog_id': cron.canal_drog_id,
                'canal_drog_nombre': canal_drog_nombre,
                'tipo_pedido': cron.tipo_pedido,
                'cadencia_dias': cron.cadencia_dias,
                'proxima_fecha': cron.proxima_fecha.isoformat() if cron.proxima_fecha else '',
                'notas': cron.notas or '',
                'activo': cron.activo,
            }
            droguerias = [{'id': p.id, 'nombre': p.razon_social}
                          for p in session.query(Provider)
                                          .filter(Provider.tipo == 'drogueria',
                                                  Provider.activo == True)  # noqa: E712
                                          .order_by(Provider.razon_social).all()]
        return render_template('cronograma_form.html',
                               cron=ctx,
                               droguerias=droguerias,
                               hoy_iso=_date.today().isoformat())

    @app.route('/cronograma/save', methods=['POST'])
    @login_required
    def cronograma_save():
        wants_json = request.is_json
        data = request.get_json(silent=True) or request.form

        def _err(msg, http=400):
            if wants_json:
                return jsonify({'ok': False, 'error': msg}), http
            flash(msg, 'error')
            return redirect(request.referrer or url_for('cronograma_config'))

        partner_tipo = (data.get('partner_tipo') or '').strip()
        # Compat backward: aceptamos `prov_tipo` (form viejo) → mapear a 'laboratorio'|'drogueria'.
        if not partner_tipo:
            partner_tipo = (data.get('prov_tipo') or '').strip()
        if partner_tipo not in _PARTNER_TIPOS_VALIDOS:
            return _err('partner_tipo inválido (laboratorio|drogueria)')

        try:
            partner_id = int(data.get('prov_id') or data.get('partner_id') or 0)
        except (TypeError, ValueError):
            return _err('partner_id inválido')
        if not partner_id:
            return _err('Falta partner_id')

        tipo = (data.get('tipo_pedido') or 'programado').strip()
        if tipo not in _TIPOS_VALIDOS:
            return _err('tipo_pedido inválido')

        canal_drog_id = data.get('canal_drog_id')
        try:
            canal_drog_id = int(canal_drog_id) if canal_drog_id not in (None, '', '0') else None
        except (TypeError, ValueError):
            canal_drog_id = None
        # canal_drog_id solo aplica cuando partner_tipo='laboratorio'
        if partner_tipo != 'laboratorio':
            canal_drog_id = None

        cad = data.get('cadencia_dias')
        try:
            cad = int(cad) if cad not in (None, '') else None
        except (TypeError, ValueError):
            cad = None
        proxima = _parse_fecha(data.get('proxima_fecha'))
        notas = (data.get('notas') or '').strip() or None
        activo_raw = data.get('activo')
        activo = bool(int(activo_raw)) if activo_raw in ('0', '1', 0, 1) else True

        with get_db() as session:
            # Validar que el partner exista en la tabla correcta.
            if partner_tipo == 'laboratorio':
                if not session.get(Laboratorio, partner_id):
                    return _err(f'Laboratorio #{partner_id} no encontrado', 404)
            else:
                drog = session.get(Provider, partner_id)
                if not drog or drog.tipo != 'drogueria':
                    return _err(f'Droguería #{partner_id} no encontrada', 404)
            if canal_drog_id:
                drog_canal = session.get(Provider, canal_drog_id)
                if not drog_canal or drog_canal.tipo != 'drogueria':
                    return _err(f'Droguería canal #{canal_drog_id} no encontrada', 404)

            cron = (session.query(ProveedorCronograma)
                    .filter_by(partner_tipo=partner_tipo,
                               proveedor_id=partner_id,
                               tipo_pedido=tipo).first())
            es_nuevo = cron is None
            if es_nuevo:
                cron = ProveedorCronograma(partner_tipo=partner_tipo,
                                            proveedor_id=partner_id,
                                            tipo_pedido=tipo)
                session.add(cron)
            cron.canal_drog_id = canal_drog_id
            cron.cadencia_dias = cad
            cron.proxima_fecha = proxima
            cron.horas_entre_pedidos = None  # legacy, no se usa
            cron.notas = notas
            cron.activo = activo
            session.commit()
            cron_id = cron.id

        if wants_json:
            return jsonify({'ok': True, 'id': cron_id})
        flash('Cronograma creado.' if es_nuevo else 'Cronograma actualizado.')
        return redirect(url_for('cronograma_config'))

    @app.route('/cronograma/<int:cron_id>/toggle', methods=['POST'])
    @login_required
    def cronograma_toggle(cron_id):
        with get_db() as session:
            cron = session.get(ProveedorCronograma, cron_id)
            if not cron:
                return jsonify({'ok': False, 'error': 'no encontrado'}), 404
            cron.activo = not cron.activo
            session.commit()
            return jsonify({'ok': True, 'activo': cron.activo})

    @app.route('/cronograma/<int:cron_id>/delete', methods=['POST'])
    @login_required
    def cronograma_delete(cron_id):
        with get_db() as session:
            cron = session.get(ProveedorCronograma, cron_id)
            if not cron:
                flash('Registro no encontrado.')
                return redirect(url_for('cronograma_list'))
            session.delete(cron)
            session.commit()
        flash('Cronograma eliminado.')
        return redirect(url_for('cronograma_list'))
