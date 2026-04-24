"""Procesos de compra: ciclo análisis → pedido → factura → cruce → reclamo → cierre."""

import json
import os
from datetime import datetime
from helpers import now_ar
from flask import render_template, request, redirect, url_for, flash, jsonify
import database
from database import ProcesoCompra, Pedido, Invoice, Claim, Laboratorio, Provider, AnalisisSesion


# ── Configuración de pasos ──────────────────────────────────────────────────
PASOS = ['analisis', 'pedido', 'factura', 'cruce', 'reclamo']

ESTADO_POR_PASO = {
    'analisis': 'BORRADOR',
    'pedido':   'PEDIDO',
    'factura':  'FACTURADO',
    'cruce':    'INGRESADO',
    'reclamo':  'INGRESADO',   # reclamo no cambia el estado principal
}

ESTADOS_ORDEN = ['BORRADOR', 'PEDIDO', 'FACTURADO', 'INGRESADO', 'CERRADO']


def _estado_index(est):
    try:
        return ESTADOS_ORDEN.index(est or 'BORRADOR')
    except ValueError:
        return 0


def _bump_estado(proc, nuevo):
    if _estado_index(nuevo) > _estado_index(proc.estado):
        proc.estado = nuevo


def _inferir_hecho(proc, session):
    """Decide si cada paso está hecho combinando timestamps + asociaciones reales.

    Esto es lo que hace 'auto-marcado': el usuario no necesita clickear 'Marcar
    hecho' si ya subió factura / creó pedido / hizo cruce.
    """
    from database import StockDifference
    tiene_diff_cruce = False
    if proc.factura_id:
        tiene_diff_cruce = session.query(StockDifference).filter_by(
            factura_id=proc.factura_id).first() is not None
    return {
        'analisis': bool(proc.analisis_hecho_en) or proc.analisis_sesion_id is not None,
        'pedido':   bool(proc.pedido_hecho_en)   or proc.pedido_id is not None,
        'factura':  bool(proc.factura_hecha_en)  or proc.factura_id is not None,
        'cruce':    bool(proc.cruce_hecho_en)    or tiene_diff_cruce,
        'reclamo':  bool(proc.reclamo_hecho_en)  or proc.reclamo_id is not None,
    }


def _serialize_list(proc, session=None):
    hechos_dict = _inferir_hecho(proc, session) if session else None
    pasos = []
    for p in PASOS:
        campo = 'factura_hecha_en' if p == 'factura' else f'{p}_hecho_en'
        ts = getattr(proc, campo, None)
        hecho = hechos_dict[p] if hechos_dict is not None else bool(ts)
        pasos.append({'paso': p, 'hecho': hecho,
                      'fecha': ts.strftime('%d/%m/%Y') if ts else None})
    return {
        'id': proc.id, 'tipo': proc.tipo,
        'partner_nombre': proc.partner_nombre, 'estado': proc.estado,
        'creado_en': proc.creado_en.strftime('%d/%m/%Y') if proc.creado_en else '',
        'actualizado_en': proc.actualizado_en.strftime('%d/%m/%Y') if proc.actualizado_en else '',
        'pedido_id': proc.pedido_id, 'factura_id': proc.factura_id, 'reclamo_id': proc.reclamo_id,
        'analisis_periodo': proc.analisis_periodo or '',
        'pasos': pasos,
    }


def _paso_field(paso):
    return 'factura_hecha_en' if paso == 'factura' else f'{paso}_hecho_en'


def init_app(app):

    @app.route('/procesos')
    def procesos_list():
        estado_filter = (request.args.get('estado') or '').strip().upper()
        tipo_filter = (request.args.get('tipo') or '').strip()
        q_text = (request.args.get('q') or '').strip().lower()

        with database.get_db() as session:
            base = session.query(ProcesoCompra)
            if estado_filter in ESTADOS_ORDEN:
                base = base.filter(ProcesoCompra.estado == estado_filter)
            if tipo_filter in ('laboratorio', 'drogueria'):
                base = base.filter(ProcesoCompra.tipo == tipo_filter)
            procs = base.order_by(ProcesoCompra.actualizado_en.desc()).all()
            if q_text:
                procs = [p for p in procs if q_text in (p.partner_nombre or '').lower()]
            data = [_serialize_list(p, session) for p in procs]

            # Conteos para filtros
            counts = {est: 0 for est in ESTADOS_ORDEN}
            for p in session.query(ProcesoCompra.estado).all():
                est = p[0] or 'BORRADOR'
                if est in counts:
                    counts[est] += 1

            laboratorios = [{'id': l.id, 'nombre': l.nombre}
                            for l in session.query(Laboratorio).order_by(Laboratorio.nombre).all()]
            proveedores = [{'id': p.id, 'nombre': p.razon_social}
                           for p in session.query(Provider).order_by(Provider.razon_social).all()]

            import observer_source
            estado_ventas = observer_source.estado_ventas_mensuales(session)
            # Para habilitar el flujo "ir directo a análisis" basta con tener datos
            # locales en obs_ventas_mensuales (no hace falta conexión SQL Server).
            observer_disponible = observer_source.observer_analisis_disponible()

        return render_template('procesos_list.html', procesos=data, counts=counts,
                               estado_filter=estado_filter, tipo_filter=tipo_filter,
                               q_text=q_text, total=len(data),
                               laboratorios=laboratorios, proveedores=proveedores,
                               estados=ESTADOS_ORDEN,
                               estado_ventas=estado_ventas,
                               observer_disponible=observer_disponible)

    @app.route('/procesos/crear', methods=['POST'])
    def proceso_crear():
        tipo = (request.form.get('tipo') or '').strip()
        partner_id = request.form.get('partner_id') or None
        partner_nombre = (request.form.get('partner_nombre') or '').strip()
        periodo = (request.form.get('periodo') or '').strip()

        if tipo not in ('laboratorio', 'drogueria', 'proveedor'):
            flash('Tipo inválido.')
            return redirect(url_for('procesos_list'))
        if not partner_nombre:
            # Resolver desde partner_id si viene
            with database.get_db() as session:
                if partner_id:
                    if tipo == 'laboratorio':
                        lab = session.get(Laboratorio, int(partner_id))
                        partner_nombre = lab.nombre if lab else ''
                    else:
                        # 'drogueria' o 'proveedor' → ambos viven en la tabla Provider
                        prov = session.get(Provider, int(partner_id))
                        partner_nombre = prov.razon_social if prov else ''
        if not partner_nombre:
            flash('Seleccioná un partner.')
            return redirect(url_for('procesos_list'))

        with database.get_db() as session:
            proc = ProcesoCompra(
                tipo=tipo,
                partner_id=int(partner_id) if partner_id else None,
                partner_nombre=partner_nombre,
                analisis_periodo=periodo or None,
                estado='BORRADOR',
            )
            session.add(proc)
            session.commit()
            pid = proc.id

        # Para laboratorio: arrancar directo en Analizar Ventas con el lab precargado.
        # Para droguería/proveedor: ir al detail (el flujo arranca desde factura).
        if tipo == 'laboratorio':
            import observer_source
            if observer_source.observer_analisis_disponible():
                return redirect(url_for('observer_analizar',
                                        lab=partner_nombre, proceso=pid))
        return redirect(url_for('proceso_detail', proceso_id=pid))

    @app.route('/proceso/<int:proceso_id>')
    def proceso_detail(proceso_id):
        with database.get_db() as session:
            proc = session.get(ProcesoCompra, proceso_id)
            if not proc:
                flash('Proceso no encontrado.')
                return redirect(url_for('procesos_list'))

            pedido = session.get(Pedido, proc.pedido_id) if proc.pedido_id else None
            invoice = session.get(Invoice, proc.factura_id) if proc.factura_id else None
            reclamo = session.get(Claim, proc.reclamo_id) if proc.reclamo_id else None
            sesion_id = proc.analisis_sesion_id or (pedido.analisis_sesion_id if pedido else None)
            sesion = session.get(AnalisisSesion, sesion_id) if sesion_id else None

            analisis_pasos = {}
            if proc.analisis_pasos_json:
                try:
                    analisis_pasos = json.loads(proc.analisis_pasos_json)
                except (ValueError, TypeError):
                    analisis_pasos = {}

            # Para sugerir links: pedidos/facturas del mismo partner sin proceso
            pedidos_libres = []
            if not proc.pedido_id:
                query_p = session.query(Pedido).filter(
                    Pedido.laboratorio.ilike(f'%{proc.partner_nombre}%')
                ).order_by(Pedido.creado_en.desc()).limit(10).all()
                usados = {p.pedido_id for p in session.query(ProcesoCompra).filter(
                    ProcesoCompra.pedido_id.isnot(None)).all()}
                pedidos_libres = [{'id': p.id, 'periodo': p.periodo,
                                   'creado_en': p.creado_en.strftime('%d/%m/%Y') if p.creado_en else ''}
                                  for p in query_p if p.id not in usados]

            facturas_libres = []
            if not proc.factura_id and proc.tipo == 'drogueria':
                query_f = session.query(Invoice).filter(
                    Invoice.proveedor_razon.ilike(f'%{proc.partner_nombre}%')
                ).order_by(Invoice.fecha.desc()).limit(10).all()
                usados_f = {p.factura_id for p in session.query(ProcesoCompra).filter(
                    ProcesoCompra.factura_id.isnot(None)).all()}
                facturas_libres = [{'id': f.id, 'numero': f.numero_factura,
                                    'fecha': f.fecha.strftime('%d/%m/%Y') if f.fecha else '',
                                    'total': float(f.total or 0)}
                                   for f in query_f if f.id not in usados_f]

            hechos = _inferir_hecho(proc, session)

            data = {
                'id': proc.id, 'tipo': proc.tipo, 'partner_nombre': proc.partner_nombre,
                'partner_id': proc.partner_id, 'estado': proc.estado,
                'analisis_periodo': proc.analisis_periodo or '',
                'notas': proc.notas or '',
                'creado_en': proc.creado_en.strftime('%d/%m/%Y %H:%M') if proc.creado_en else '',
                'analisis_hecho_en': proc.analisis_hecho_en,
                'pedido_hecho_en': proc.pedido_hecho_en,
                'factura_hecha_en': proc.factura_hecha_en,
                'cruce_hecho_en': proc.cruce_hecho_en,
                'reclamo_hecho_en': proc.reclamo_hecho_en,
                'cerrado_en': proc.cerrado_en,
                # Flags auto-calculados (verdadero si el paso está implícitamente hecho
                # por tener el pedido/factura/etc asociado, aunque no haya timestamp)
                'hechos': hechos,
                'pedido': {
                    'id': pedido.id, 'periodo': pedido.periodo,
                    'laboratorio': pedido.laboratorio,
                    'n_items': len(pedido.items),
                    'total': float(sum(float(i.subtotal or 0) for i in pedido.items)),
                    'creado_en': pedido.creado_en.strftime('%d/%m/%Y') if pedido.creado_en else '',
                } if pedido else None,
                'invoice': {
                    'id': invoice.id, 'numero': invoice.numero_factura,
                    'fecha': invoice.fecha.strftime('%d/%m/%Y') if invoice.fecha else '',
                    'total': float(invoice.total or 0),
                    'tipo_comprobante': invoice.tipo_comprobante or 'FAC',
                    'pdf_filename': invoice.pdf_filename or '',
                    'pdf_disponible': bool(invoice.pdf_filename and os.path.exists(
                        os.path.join(os.path.dirname(os.path.dirname(__file__)), 'uploads', invoice.pdf_filename)
                    )),
                } if invoice else None,
                'reclamo': {
                    'id': reclamo.id, 'numero_factura': reclamo.numero_factura,
                    'estado': reclamo.estado,
                    'fecha': reclamo.fecha.strftime('%d/%m/%Y') if reclamo.fecha else '',
                } if reclamo else None,
                'analisis_pasos': analisis_pasos,
                'analisis_sesion': {
                    'id': sesion.id,
                    'laboratorio': sesion.laboratorio_nombre,
                    'periodo': sesion.periodo or '',
                    'farmacia': sesion.farmacia or '',
                    'n_days': sesion.n_days,
                    'fuente': sesion.fuente or 'pdf',
                    'n_productos': sesion.n_productos,
                    'creado_en': sesion.creado_en.strftime('%d/%m/%Y %H:%M') if sesion.creado_en else '',
                } if sesion else None,
            }

        return render_template('proceso_detail.html', proc=data,
                               pedidos_libres=pedidos_libres,
                               facturas_libres=facturas_libres)

    @app.route('/proceso/<int:proceso_id>/paso/<paso>', methods=['POST'])
    def proceso_marcar_paso(proceso_id, paso):
        if paso not in PASOS:
            return jsonify({'error': 'Paso inválido'}), 400
        with database.get_db() as session:
            proc = session.get(ProcesoCompra, proceso_id)
            if not proc:
                return jsonify({'error': 'No encontrado'}), 404
            field = _paso_field(paso)
            setattr(proc, field, now_ar())
            _bump_estado(proc, ESTADO_POR_PASO[paso])
            proc.actualizado_en = now_ar()
            session.commit()
        return redirect(url_for('proceso_detail', proceso_id=proceso_id))

    @app.route('/proceso/<int:proceso_id>/paso/<paso>/undo', methods=['POST'])
    def proceso_desmarcar_paso(proceso_id, paso):
        if paso not in PASOS:
            return jsonify({'error': 'Paso inválido'}), 400
        with database.get_db() as session:
            proc = session.get(ProcesoCompra, proceso_id)
            if not proc:
                return jsonify({'error': 'No encontrado'}), 404
            setattr(proc, _paso_field(paso), None)
            # Recalcular estado
            if proc.cruce_hecho_en:
                proc.estado = 'INGRESADO'
            elif proc.factura_hecha_en:
                proc.estado = 'FACTURADO'
            elif proc.pedido_hecho_en:
                proc.estado = 'PEDIDO'
            else:
                proc.estado = 'BORRADOR'
            proc.actualizado_en = now_ar()
            session.commit()
        return redirect(url_for('proceso_detail', proceso_id=proceso_id))

    @app.route('/proceso/<int:proceso_id>/link-pedido', methods=['POST'])
    def proceso_link_pedido(proceso_id):
        pedido_id = request.form.get('pedido_id')
        with database.get_db() as session:
            proc = session.get(ProcesoCompra, proceso_id)
            if not proc:
                flash('Proceso no encontrado.')
                return redirect(url_for('procesos_list'))
            if pedido_id:
                proc.pedido_id = int(pedido_id)
                if not proc.pedido_hecho_en:
                    proc.pedido_hecho_en = now_ar()
                _bump_estado(proc, 'PEDIDO')
                pedido = session.get(Pedido, int(pedido_id))
                if pedido and pedido.analisis_sesion_id and not proc.analisis_sesion_id:
                    proc.analisis_sesion_id = pedido.analisis_sesion_id
            else:
                proc.pedido_id = None
            proc.actualizado_en = now_ar()
            session.commit()
        return redirect(url_for('proceso_detail', proceso_id=proceso_id))

    @app.route('/proceso/<int:proceso_id>/link-factura', methods=['POST'])
    def proceso_link_factura(proceso_id):
        factura_id = request.form.get('factura_id')
        with database.get_db() as session:
            proc = session.get(ProcesoCompra, proceso_id)
            if not proc:
                flash('Proceso no encontrado.')
                return redirect(url_for('procesos_list'))
            if factura_id:
                proc.factura_id = int(factura_id)
                if not proc.factura_hecha_en:
                    proc.factura_hecha_en = now_ar()
                _bump_estado(proc, 'FACTURADO')
            else:
                proc.factura_id = None
            proc.actualizado_en = now_ar()
            session.commit()
        return redirect(url_for('proceso_detail', proceso_id=proceso_id))

    @app.route('/proceso/<int:proceso_id>/snapshot-analisis', methods=['POST'])
    def proceso_snapshot_analisis(proceso_id):
        """Guarda qué sub-pasos del análisis se usaron. Body JSON:
        {modulos:{hecho:true, cant:N}, ofertas:{...}, ofertas_min:{...}, sin_deal:{...}}
        """
        body = request.get_json(silent=True) or {}
        with database.get_db() as session:
            proc = session.get(ProcesoCompra, proceso_id)
            if not proc:
                return jsonify({'error': 'No encontrado'}), 404
            proc.analisis_pasos_json = json.dumps(body)
            if not proc.analisis_hecho_en:
                proc.analisis_hecho_en = now_ar()
            _bump_estado(proc, 'BORRADOR')  # no sube estado, solo marca
            proc.actualizado_en = now_ar()
            session.commit()
            return jsonify({'ok': True})

    @app.route('/proceso/<int:proceso_id>/notas', methods=['POST'])
    def proceso_notas(proceso_id):
        notas = (request.form.get('notas') or '').strip()
        with database.get_db() as session:
            proc = session.get(ProcesoCompra, proceso_id)
            if not proc:
                flash('Proceso no encontrado.')
                return redirect(url_for('procesos_list'))
            proc.notas = notas or None
            proc.actualizado_en = now_ar()
            session.commit()
        return redirect(url_for('proceso_detail', proceso_id=proceso_id))

    @app.route('/proceso/<int:proceso_id>/cerrar', methods=['POST'])
    def proceso_cerrar(proceso_id):
        with database.get_db() as session:
            proc = session.get(ProcesoCompra, proceso_id)
            if not proc:
                flash('Proceso no encontrado.')
                return redirect(url_for('procesos_list'))
            proc.cerrado_en = now_ar()
            proc.estado = 'CERRADO'
            proc.actualizado_en = now_ar()
            session.commit()
        return redirect(url_for('proceso_detail', proceso_id=proceso_id))

    @app.route('/proceso/<int:proceso_id>/reabrir', methods=['POST'])
    def proceso_reabrir(proceso_id):
        with database.get_db() as session:
            proc = session.get(ProcesoCompra, proceso_id)
            if not proc:
                flash('Proceso no encontrado.')
                return redirect(url_for('procesos_list'))
            proc.cerrado_en = None
            if proc.cruce_hecho_en:
                proc.estado = 'INGRESADO'
            elif proc.factura_hecha_en:
                proc.estado = 'FACTURADO'
            elif proc.pedido_hecho_en:
                proc.estado = 'PEDIDO'
            else:
                proc.estado = 'BORRADOR'
            proc.actualizado_en = now_ar()
            session.commit()
        return redirect(url_for('proceso_detail', proceso_id=proceso_id))

    @app.route('/pedido/<int:pedido_id>/enviar-a-proceso', methods=['POST'])
    def pedido_enviar_a_proceso(pedido_id):
        """Crea un ProcesoCompra desde un Pedido guardado.
        Si el pedido ya tiene canal+partner_id decidido (desde el paso 4 del análisis),
        los usa directamente. Si no, acepta canal y drogueria_id del form.
        """
        with database.get_db() as session:
            pedido = session.get(Pedido, pedido_id)
            if not pedido:
                flash('Pedido no encontrado.')
                return redirect(url_for('orders_list'))

            existente = session.query(ProcesoCompra).filter_by(pedido_id=pedido.id).first()
            if existente:
                flash(f'Este pedido ya está en el proceso #{existente.id}.')
                return redirect(url_for('proceso_detail', proceso_id=existente.id))

            # Preferir lo que el pedido ya tiene guardado (paso 4 del análisis)
            if pedido.canal:
                canal = pedido.canal
                drog_id = pedido.partner_id if canal == 'drogueria' else None
            else:
                # Fallback al form tradicional (orders_list)
                canal = (request.form.get('canal') or 'laboratorio').strip()
                drog_id = request.form.get('drogueria_id') or None

            if canal == 'drogueria':
                if not drog_id:
                    flash('Elegí la droguería por la que va a entrar el pedido.')
                    return redirect(url_for('orders_list'))
                prov = session.get(Provider, int(drog_id))
                if not prov:
                    flash('Droguería no encontrada.')
                    return redirect(url_for('orders_list'))
                partner_nombre = prov.razon_social
                partner_id = prov.id
                tipo = 'drogueria'
                notas = f'Pedido generado para laboratorio: {pedido.laboratorio}'
            else:
                lab = session.query(Laboratorio).filter_by(nombre=pedido.laboratorio).first()
                partner_nombre = pedido.laboratorio or '—'
                partner_id = lab.id if lab else None
                tipo = 'laboratorio'
                notas = None

            # Si el pedido no tenía canal persistido, lo guardamos ahora para congruencia
            if not pedido.canal:
                pedido.canal = canal
                pedido.partner_id = partner_id
                pedido.canal_elegido_en = now_ar()

            proc = ProcesoCompra(
                tipo=tipo,
                partner_id=partner_id,
                partner_nombre=partner_nombre,
                analisis_periodo=pedido.periodo or None,
                pedido_id=pedido.id,
                analisis_sesion_id=pedido.analisis_sesion_id,
                analisis_hecho_en=pedido.creado_en or now_ar(),
                estado='BORRADOR',
                notas=notas,
            )
            session.add(proc)
            pedido.estado = 'ENVIADO'
            session.commit()
            pid = proc.id
        flash('Pedido enviado a Procesos.')
        return redirect(url_for('proceso_detail', proceso_id=pid))

    @app.route('/proceso/<int:proceso_id>/delete', methods=['POST'])
    def proceso_delete(proceso_id):
        with database.get_db() as session:
            proc = session.get(ProcesoCompra, proceso_id)
            if proc:
                session.delete(proc)
                session.commit()
        return redirect(url_for('procesos_list'))
