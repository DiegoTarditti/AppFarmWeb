"""Consulta de compras por artículo.

Busca sobre TODOS los ítems de factura importados (InvoiceItem + Invoice): un
producto (por descripción o EAN) en un rango de fechas → cada compra con fecha,
nº de factura, proveedor, cantidad, precio unitario, %Dto e importe. Sirve para
ver el histórico de precios/descuentos de un producto entre droguerías.

Ingreso ObServer: solo se muestra para facturas de Kellerhoff que ya pasaron
por "Verificar ingresos" en su resumen semanal (ver services/kellerhoff_resumen)
— ahí sí se conoce el remito, que es contra lo que ObServer registra la
recepción (ver docs/controles_kellerhoff.md). El resto de los proveedores no
tiene remito capturado en `Invoice`, así que no hay contra qué cruzar; buscar
en vivo por número de factura da una tasa de acierto muy baja (medido:
la mayoría de las recepciones quedan bajo el remito, no la factura).
"""
from __future__ import annotations

from datetime import date as _date
from datetime import datetime as _dt

from flask import Response, abort, jsonify, render_template, request
from flask_login import login_required
from sqlalchemy import or_

import database
from database import Invoice, InvoiceItem, ObsCodigoBarras, ObsLaboratorio, ObsProducto, get_db

_LIMITE = 500


def _parse_fecha(s):
    s = (s or '').strip()
    if not s:
        return None
    try:
        return _dt.strptime(s, '%Y-%m-%d').date()
    except ValueError:
        return None


def _consultar(session, q, desde, hasta, lab_id):
    """(filas, resumen) de las compras que matchean. filas=[] si no hay término."""
    if not q:
        return [], None
    query = (session.query(InvoiceItem, Invoice)
             .join(Invoice, InvoiceItem.factura_id == Invoice.id))
    # Cada palabra tiene que aparecer en la descripción (o el EAN):
    # "optamox duo" matchea "OPTAMOX DUO 1g…".
    for palabra in q.split():
        like = f'%{palabra}%'
        query = query.filter(or_(InvoiceItem.descripcion.ilike(like),
                                 InvoiceItem.codigo_barra.ilike(like)))
    if desde:
        query = query.filter(Invoice.fecha >= desde)
    if hasta:
        query = query.filter(Invoice.fecha <= hasta)
    if lab_id:
        # Antes acá había un filtro por proveedor. Se sacó porque el detalle de
        # factura existe casi sólo para Kellerhoff (los demás proveedores tienen
        # cabecera pero ningún renglón), así que filtrar por proveedor no separa
        # nada: siempre es el mismo. Lo que sí sirve es el laboratorio.
        #
        # El laboratorio no está en el renglón de factura: se llega por el EAN,
        # vía el catálogo de ObServer.
        query = query.filter(InvoiceItem.codigo_barra.in_(
            session.query(ObsCodigoBarras.codigo_barras)
            .join(ObsProducto,
                  ObsProducto.observer_id == ObsCodigoBarras.producto_observer)
            .filter(ObsProducto.laboratorio_observer == lab_id,
                    ObsCodigoBarras.fecha_baja.is_(None))
        ))
    rows = (query.order_by(Invoice.fecha.desc(),
                           InvoiceItem.descripcion).limit(_LIMITE).all())

    # Ingreso ObServer: lo que ya haya quedado de correr "Verificar ingresos"
    # en el resumen de Kellerhoff (ver docstring del módulo) — una query bulk,
    # no una por fila.
    from database import ResumenProveedorItem
    fids = {inv.id for _it, inv in rows}
    ingreso_por_factura = {}
    if fids:
        for it_r in (session.query(ResumenProveedorItem)
                     .filter(ResumenProveedorItem.factura_id.in_(fids)).all()):
            ingreso_por_factura[it_r.factura_id] = {
                'numero_remito': it_r.numero_remito or '',
                'ingreso_verificado': it_r.ingreso_verificado,
            }

    # Diferencias del cruce, por (factura, EAN). ResumenProveedorItem.ingreso_verificado
    # es de la FACTURA ("el remito tiene alguna recepción"), y esta pantalla lista
    # ARTÍCULOS: puesto en la fila de un artículo dice algo que no sabe. Caso real
    # medido el 2026-09-03: la factura 00046-00317100 tiene recepción (1 MODIALEX),
    # así que su renglón de OBETIDE 1,7 MG mostraba ✓ — y el cruce dice facturado 2 /
    # ingresó 0, verificado contra DW.Recepciones (ninguna recepción de ese producto
    # ese día salvo las 16 de OTRA factura). Un faltante de $301.338,70 con tilde de
    # recibido.
    from database import StockDifference
    difs_por_item = {}
    if fids:
        for d in (session.query(StockDifference)
                  .filter(StockDifference.factura_id.in_(fids)).all()):
            difs_por_item[(d.factura_id, (d.codigo_barra or '').strip())] = d

    filas = []
    tot_u = 0
    tot_imp = 0.0
    dtos = []
    for it, inv in rows:
        dto = float(it.dto) if it.dto is not None else None
        ing = ingreso_por_factura.get(inv.id)
        # Estado de ingreso DE ESTE RENGLÓN. `erp_carga_id` es lo que marca que la
        # factura se cruzó alguna vez; sin eso no hay nada que afirmar (la ausencia
        # de diferencias sería un falso ✓: no hay diferencias porque no se comparó).
        dif = difs_por_item.get((inv.id, (it.codigo_barra or '').strip()))
        if inv.erp_carga_id is None:
            ingreso_item = None
        else:
            ingreso_item = dif is None
        filas.append({
            'invoice_id': inv.id,
            'fecha': inv.fecha,
            'numero': inv.numero_factura,
            'tipo': inv.tipo_comprobante or 'FAC',
            'proveedor': inv.proveedor_razon or '—',
            'codigo_barra': it.codigo_barra or '',
            'descripcion': it.descripcion or '',
            'cantidad': it.cantidad or 0,
            'precio_unitario': float(it.precio_unitario) if it.precio_unitario is not None else None,
            'dto': dto,
            'importe': float(it.importe) if it.importe is not None else None,
            'numero_remito': ing['numero_remito'] if ing else '',
            # De la FACTURA — se sigue exponiendo, pero solo para el tooltip.
            'ingreso_factura': ing['ingreso_verificado'] if ing else None,
            # De ESTE renglón: True cruzó sin diferencia, False difiere, None sin cruzar.
            'ingreso_verificado': ingreso_item,
            'ingreso_cant_erp': dif.cantidad_erp if dif is not None else None,
            'ingreso_obs': dif.observaciones if dif is not None else None,
        })
        tot_u += it.cantidad or 0
        tot_imp += float(it.importe or 0)
        if dto is not None and dto > 0:
            dtos.append(dto)
    resumen = {
        'n': len(filas), 'unidades': tot_u, 'importe': tot_imp,
        'dto_min': min(dtos) if dtos else None,
        'dto_max': max(dtos) if dtos else None,
        'dto_prom': (sum(dtos) / len(dtos)) if dtos else None,
        'truncado': len(filas) >= _LIMITE,
    }
    return filas, resumen


def _laboratorios_con_compras(session):
    """Sólo los laboratorios que aparecen en alguna factura.

    La lista completa son más de mil y el 95% nunca se compró: un desplegable
    así no se usa, se sufre.
    """
    return (session.query(ObsLaboratorio.observer_id, ObsLaboratorio.descripcion)
            .join(ObsProducto,
                  ObsProducto.laboratorio_observer == ObsLaboratorio.observer_id)
            .join(ObsCodigoBarras,
                  ObsCodigoBarras.producto_observer == ObsProducto.observer_id)
            .join(InvoiceItem,
                  InvoiceItem.codigo_barra == ObsCodigoBarras.codigo_barras)
            .filter(ObsLaboratorio.fecha_baja.is_(None))
            .distinct()
            .order_by(ObsLaboratorio.descripcion)
            .all())


def _xlsx(filas, q) -> bytes:
    import io

    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Compras'
    cols = [('Fecha', 12), ('Factura', 16), ('Tipo', 7), ('Proveedor', 28),
            ('EAN', 16), ('Producto', 42), ('Cant.', 8), ('P. Unitario', 13),
            ('% Dto', 8), ('Importe', 14), ('Remito', 16), ('Ingreso', 12)]
    ws.append([c[0] for c in cols])
    for i, (_t, w) in enumerate(cols, start=1):
        cell = ws.cell(row=1, column=i)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='1C1C1E')
        cell.alignment = Alignment(horizontal='center')
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    # Mismo criterio que la columna de la pantalla: es del RENGLÓN, no de la factura.
    _ing_txt = {True: 'OK', False: 'DIFIERE', None: 'SIN CRUZAR'}
    for f in filas:
        ws.append([
            f['fecha'].strftime('%d/%m/%Y') if f['fecha'] else '',
            f['numero'], f['tipo'], f['proveedor'], f['codigo_barra'],
            f['descripcion'], f['cantidad'],
            f['precio_unitario'], f['dto'], f['importe'],
            f.get('numero_remito', ''), _ing_txt[f.get('ingreso_verificado')],
        ])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def init_app(app):

    @app.route('/compras/consulta')
    @login_required
    def consulta_compras():
        q = (request.args.get('q') or '').strip()
        desde = _parse_fecha(request.args.get('desde'))
        hasta = _parse_fecha(request.args.get('hasta'))
        lab_id = (request.args.get('laboratorio') or '').strip()
        lab_id = int(lab_id) if lab_id.isdigit() else None
        with get_db() as session:
            laboratorios = _laboratorios_con_compras(session)
            filas, resumen = _consultar(session, q, desde, hasta, lab_id)
        return render_template('consulta_compras.html', q=q,
                               desde=request.args.get('desde') or '',
                               hasta=request.args.get('hasta') or '',
                               lab_id=lab_id, laboratorios=laboratorios,
                               filas=filas, resumen=resumen, hoy=_date.today())

    @app.route('/api/compras/consulta/monroe', methods=['POST'])
    @login_required
    def consulta_compras_monroe():
        """Precio de Monroe HOY para los EAN que están en pantalla.

        No decide nada: devuelve el número para que la tabla lo muestre al lado
        de lo que se pagó. Comparar una compra de hace tres meses contra el
        precio de hoy infla la diferencia, por eso la columna se titula
        "Monroe hoy" y no "diferencia real".
        """
        from services.contraste_monroe import cotizar_monroe
        eans = (request.get_json(silent=True) or {}).get('eans') or []
        eans = [str(e).strip() for e in eans if str(e).strip()][:500]
        if not eans:
            return jsonify({'ok': True, 'precios': {}})
        return jsonify({'ok': True, **cotizar_monroe(eans)})

    @app.route('/compras/consulta/export.xlsx')
    @login_required
    def consulta_compras_export():
        q = (request.args.get('q') or '').strip()
        if not q:
            abort(400, description='Falta el término de búsqueda.')
        desde = _parse_fecha(request.args.get('desde'))
        hasta = _parse_fecha(request.args.get('hasta'))
        lab_id = (request.args.get('laboratorio') or '').strip()
        lab_id = int(lab_id) if lab_id.isdigit() else None
        with get_db() as session:
            filas, _ = _consultar(session, q, desde, hasta, lab_id)
        contenido = _xlsx(filas, q)
        slug = ''.join(c if c.isalnum() else '-' for c in q)[:30] or 'compras'
        nombre = f'Compras-{slug}-{_date.today():%Y-%m-%d}.xlsx'
        return Response(
            contenido,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': f'attachment; filename="{nombre}"'})
