"""Log unificado de pedidos emitidos — sale de PedidoEmitido (matriz drog)
+ Pedido con canal_elegido (análisis lab/drog). Tabla cronológica plana,
filtrable, con link al proceso o al pedido directo según corresponda.
"""
from datetime import date as _date, datetime, timedelta

from flask import render_template, request
from flask_login import login_required
from sqlalchemy import or_

from database import (Laboratorio, Pedido, PedidoEmitido, ProcesoCompra,
                      Provider, get_db)


def _parse_fecha(s):
    if not s:
        return None
    try:
        return _date.fromisoformat(s)
    except ValueError:
        return None


def init_app(app):

    @app.route('/pedidos/log')
    @login_required
    def pedidos_log():
        """Lista cronológica de pedidos emitidos (de cualquier flujo)."""
        # Default: últimos 30 días.
        hoy = _date.today()
        desde = _parse_fecha(request.args.get('desde')) or (hoy - timedelta(days=30))
        hasta = _parse_fecha(request.args.get('hasta')) or hoy
        tipo_filtro = (request.args.get('tipo') or '').strip().upper() or None
        # Filtros de partner: lab_id o drog_id (separados, igual que cronograma).
        lab_filtro = request.args.get('lab_id', type=int)
        drog_filtro = request.args.get('drog_id', type=int)

        with get_db() as session:
            registros = []

            # --- PedidoEmitido (modo matriz drog) → tipo REPOSICION ---
            if tipo_filtro in (None, 'REPOSICION'):
                q_pe = (session.query(PedidoEmitido)
                        .filter(PedidoEmitido.fecha >= datetime.combine(desde, datetime.min.time()),
                                PedidoEmitido.fecha < datetime.combine(hasta + timedelta(days=1),
                                                                       datetime.min.time())))
                if drog_filtro:
                    q_pe = q_pe.filter(PedidoEmitido.drogueria_id == drog_filtro)
                if lab_filtro:
                    # PedidoEmitido no tiene partner lab — si filtran por lab, no entran estos.
                    q_pe = q_pe.filter(False)
                for p in q_pe.all():
                    prov = session.get(Provider, p.drogueria_id) if p.drogueria_id else None
                    registros.append({
                        'tipo': 'REPOSICION',
                        'fuente': 'PedidoEmitido',
                        'pedido_id': p.id,
                        'partner_tipo': 'drogueria',
                        'partner_nombre': prov.razon_social if prov else f'Drog#{p.drogueria_id}',
                        'canal_nombre': None,  # drog ES el canal
                        'fecha_dt': p.fecha,
                        'estado': p.estado or '—',
                        'origen': p.origen or '',
                        'emitido_por': p.emitido_por or p.usuario or '',
                        'total_items': p.total_items or 0,
                        'total_unidades': p.total_unidades or 0,
                    })

            # --- Pedido con canal elegido (análisis lab/drog) ---
            q_p = (session.query(Pedido)
                   .filter(Pedido.canal.isnot(None),
                           Pedido.canal_elegido_en.isnot(None),
                           Pedido.canal_elegido_en >= datetime.combine(desde, datetime.min.time()),
                           Pedido.canal_elegido_en < datetime.combine(hasta + timedelta(days=1),
                                                                     datetime.min.time())))
            # Mapeo de filtro tipo → canal
            if tipo_filtro == 'COMPRA_LAB':
                q_p = q_p.filter(Pedido.canal == 'laboratorio')
            elif tipo_filtro == 'COMPRA_DROG':
                q_p = q_p.filter(Pedido.canal == 'drogueria')
            elif tipo_filtro == 'REPOSICION':
                q_p = q_p.filter(False)  # REPO solo viene de PedidoEmitido
            if lab_filtro:
                q_p = q_p.filter(Pedido.canal == 'laboratorio',
                                 Pedido.partner_id == lab_filtro)
            if drog_filtro:
                q_p = q_p.filter(Pedido.canal == 'drogueria',
                                 Pedido.partner_id == drog_filtro)
            for p in q_p.all():
                # Resolver nombre del partner según canal
                partner_nombre = p.laboratorio or ''
                if p.canal == 'drogueria' and p.partner_id:
                    prov = session.get(Provider, p.partner_id)
                    if prov:
                        partner_nombre = prov.razon_social
                elif p.canal == 'laboratorio' and p.partner_id:
                    lab = session.get(Laboratorio, p.partner_id)
                    if lab:
                        partner_nombre = lab.nombre
                tipo = 'COMPRA_LAB' if p.canal == 'laboratorio' else 'COMPRA_DROG'
                registros.append({
                    'tipo': tipo,
                    'fuente': 'Pedido',
                    'pedido_id': p.id,
                    'partner_tipo': p.canal,
                    'partner_nombre': partner_nombre,
                    'canal_nombre': None,  # un Pedido es directo o vía drog (el canal=drog ya es la drog)
                    'fecha_dt': p.canal_elegido_en,
                    'estado': p.estado or '—',
                    'origen': p.origen or '',
                    'emitido_por': '',
                    'total_items': len(p.items) if p.items else 0,
                    'total_unidades': sum((it.cantidad_pedida or 0) for it in p.items) if p.items else 0,
                })

            # Map proceso → pedido_id (mismo patrón que cronograma)
            pedido_ids = {r['pedido_id'] for r in registros if r['fuente'] == 'Pedido'}
            proceso_por_pedido = {}
            if pedido_ids:
                proceso_por_pedido = {
                    p.pedido_id: p.id
                    for p in (session.query(ProcesoCompra)
                              .filter(ProcesoCompra.pedido_id.in_(pedido_ids)).all())
                }

            # Orden cronológico descendente.
            registros.sort(key=lambda r: r['fecha_dt'] or datetime.min, reverse=True)
            for r in registros:
                r['fecha_label'] = r['fecha_dt'].strftime('%d/%m/%Y %H:%M') if r['fecha_dt'] else '—'

            # Listas para los selects de filtro (mismo set que cronograma).
            laboratorios = [{'id': l.id, 'nombre': l.nombre}
                            for l in session.query(Laboratorio).order_by(Laboratorio.nombre).all()]
            droguerias = [{'id': pr.id, 'nombre': pr.razon_social}
                          for pr in session.query(Provider)
                                           .filter(Provider.tipo == 'drogueria',
                                                   Provider.activo == True)  # noqa: E712
                                           .order_by(Provider.razon_social).all()]

        return render_template('pedidos_log.html',
                               registros=registros,
                               proceso_por_pedido=proceso_por_pedido,
                               desde=desde.isoformat(),
                               hasta=hasta.isoformat(),
                               tipo_filtro=tipo_filtro or '',
                               lab_filtro=lab_filtro,
                               drog_filtro=drog_filtro,
                               laboratorios=laboratorios,
                               droguerias=droguerias)
