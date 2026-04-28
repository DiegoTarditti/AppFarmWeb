"""Obras Sociales — dashboard y listado de dispensas.

⚠ AÚN MOCK DATA. Plan de migración a datos reales:
1. Sincronizar `DW.ProductosVendidos` detalle → tabla local `obs_ventas_detalle`
   (ya está confirmado el schema, ver `c:/AppSeguimiento/10-obras-sociales.md`).
2. Crear vista local `v_dispensas_os` que junte ventas + cliente + OS + plan + médico.
3. Reemplazar `_mock_dispensas()` con queries reales contra esa vista.
La UI no debería cambiar — solo cambia la fuente de datos.
"""

import random
from datetime import date, datetime, timedelta

from flask import render_template, request

# ── Mock data ─────────────────────────────────────────────────────────

_MEDICOS = [
    ('12345', 'Dr. Juan Pérez'),
    ('23456', 'Dra. María González'),
    ('34567', 'Dr. Carlos Rodríguez'),
    ('45678', 'Dra. Laura Fernández'),
    ('56789', 'Dr. Roberto Martínez'),
    ('67890', 'Dra. Ana Sánchez'),
    ('78901', 'Dr. Diego López'),
]

_MEDICAMENTOS = [
    ('7791234567890', 'ATORVASTATINA 20MG x 30 COMP', 2450.00),
    ('7791234567891', 'METFORMINA 850MG x 30 COMP', 890.00),
    ('7791234567892', 'ENALAPRIL 10MG x 30 COMP', 560.00),
    ('7791234567893', 'LOSARTAN 50MG x 30 COMP', 1200.00),
    ('7791234567894', 'OMEPRAZOL 20MG x 14 CAP', 780.00),
    ('7791234567895', 'LEVOTIROXINA 100MCG x 50 COMP', 1850.00),
    ('7791234567896', 'AMLODIPINA 5MG x 30 COMP', 920.00),
    ('7791234567897', 'INSULINA GLARGINA 100UI/ML FCO', 48500.00),
    ('7791234567898', 'ACIDO ACETILSALICILICO 100MG x 30', 340.00),
    ('7791234567899', 'CLOPIDOGREL 75MG x 28 COMP', 3200.00),
    ('7791234567900', 'SALBUTAMOL AEROSOL 200 DOSIS', 2100.00),
    ('7791234567901', 'PARACETAMOL 500MG x 20 COMP', 280.00),
]

_AFILIADOS_PAMI = [
    ('150234567/00', '20-12345678-9', 'GÓMEZ, MARIA ELENA'),
    ('150234568/01', '27-23456789-0', 'LÓPEZ, ROBERTO'),
    ('150234569/00', '20-34567890-1', 'FERNÁNDEZ, CARLOS'),
    ('150234570/02', '27-45678901-2', 'MARTÍNEZ, SUSANA'),
    ('150234571/00', '20-56789012-3', 'GONZÁLEZ, JUAN'),
    ('150234572/01', '27-67890123-4', 'RODRÍGUEZ, ANA'),
]

_AFILIADOS_IAPOS = [
    ('A-1234567', '20-11223344-5', 'PÉREZ, LUIS'),
    ('A-2345678', '27-22334455-6', 'SÁNCHEZ, MARTA'),
    ('A-3456789', '20-33445566-7', 'DÍAZ, PABLO'),
    ('A-4567890', '27-44556677-8', 'RAMÍREZ, SILVIA'),
    ('A-5678901', '20-55667788-9', 'TORRES, MIGUEL'),
]

_PLANES = {
    'PAMI': ['Básico', 'PROFE', 'Convenio', 'Oncológico'],
    'IAPOS': ['Ambulatorio', 'Insulina', 'Crónicos', 'PMO'],
}

_MOCK_CACHE = None


def _mock_dispensas():
    """Genera 350 dispensas mockeadas distribuidas en los últimos 45 días."""
    global _MOCK_CACHE
    if _MOCK_CACHE is not None:
        return _MOCK_CACHE

    rng = random.Random(42)
    rows = []
    hoy = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(1, 351):
        os_code = rng.choices(['PAMI', 'IAPOS'], weights=[0.65, 0.35])[0]
        afil = rng.choice(_AFILIADOS_PAMI if os_code == 'PAMI' else _AFILIADOS_IAPOS)
        med = rng.choice(_MEDICOS)
        plan = rng.choice(_PLANES[os_code])
        tipo_receta = rng.choices(['electronica', 'manual', 'OPF'], weights=[0.80, 0.10, 0.10])[0]
        n_items = rng.choices([1, 2, 3, 4], weights=[0.55, 0.28, 0.12, 0.05])[0]
        dias_atras = rng.randint(0, 44)
        hora = rng.randint(8, 20)
        minuto = rng.randint(0, 59)
        fecha_venta = hoy - timedelta(days=dias_atras) + timedelta(hours=hora, minutes=minuto)
        fecha_emision = fecha_venta - timedelta(days=rng.randint(0, 25))
        ticket = f'{os_code[0]}{rng.randint(100000, 999999)}'
        items = []
        importe_os_total = 0.0
        importe_pac_total = 0.0
        for _ in range(n_items):
            troquel, desc, pvp = rng.choice(_MEDICAMENTOS)
            cant = rng.choices([1, 2, 3], weights=[0.75, 0.20, 0.05])[0]
            cobertura = rng.choice([40, 50, 60, 70, 100])
            importe_total = pvp * cant
            importe_os = round(importe_total * cobertura / 100, 2)
            importe_pac = round(importe_total - importe_os, 2)
            importe_os_total += importe_os
            importe_pac_total += importe_pac
            items.append({
                'troquel': troquel,
                'descripcion': desc,
                'cantidad': cant,
                'pvp_unitario': pvp,
                'cobertura_pct': cobertura,
                'importe_os': importe_os,
                'importe_paciente': importe_pac,
            })
        rows.append({
            'dispensa_id': i,
            'receta_id': f'R-{i:05d}',
            'fecha_venta': fecha_venta,
            'os_codigo': os_code,
            'os_plan': plan,
            'ticket_validacion': ticket,
            'afiliado_nro': afil[0],
            'afiliado_dni': afil[1],
            'afiliado_nombre': afil[2],
            'medico_matricula': med[0],
            'medico_nombre': med[1],
            'receta_fecha_emision': fecha_emision,
            'receta_tipo': tipo_receta,
            'items': items,
            'importe_os': round(importe_os_total, 2),
            'importe_paciente': round(importe_pac_total, 2),
            'importe_total': round(importe_os_total + importe_pac_total, 2),
            'lote_nro': None,
            'estado': 'pendiente',
        })
    _MOCK_CACHE = rows
    return rows


# ── Rutas ─────────────────────────────────────────────────────────────

def init_app(app):

    @app.route('/obras-sociales')
    def os_dashboard():
        """Dashboard de OS — datos reales desde obs_ventas_detalle.

        Usa categorías 'PAMI'/'IAPOS'/'OTROS' detectando por la descripción de
        la OS. Una "receta" = grupo de líneas con el mismo (id_operacion,
        cliente, medico) en el mismo día (DW.Recetas no expone esto, lo
        reconstruimos).
        """
        from sqlalchemy import case, distinct
        from sqlalchemy import func as _f

        import database

        hoy = datetime.now().date()
        primer_dia = hoy.replace(day=1)
        desde_30d = hoy - timedelta(days=29)

        os_filter = (request.args.get('os') or '').upper()
        if os_filter not in ('PAMI', 'IAPOS'):
            os_filter = ''

        with database.get_db() as session:
            ObsVD = database.ObsVentaDetalle

            # Mapear cada OS observada a categoría (PAMI/IAPOS/OTROS).
            os_obs_ids = [r[0] for r in session.query(distinct(ObsVD.obra_social_observer))
                          .filter(ObsVD.obra_social_observer.isnot(None)).all()]
            os_descrs = dict(session.query(database.ObsObraSocial.observer_id,
                                            database.ObsObraSocial.descripcion).all())

            def categoria(os_id):
                if os_id is None:
                    return None
                desc = (os_descrs.get(os_id) or '').upper()
                if 'PAMI' in desc:
                    return 'PAMI'
                if 'IAPOS' in desc:
                    return 'IAPOS'
                return 'OTROS'

            os_to_cat = {oid: categoria(oid) for oid in os_obs_ids}
            pami_ids = {oid for oid, c in os_to_cat.items() if c == 'PAMI'}
            iapos_ids = {oid for oid, c in os_to_cat.items() if c == 'IAPOS'}

            # CASE expr para SQL
            cat_expr = case(
                (ObsVD.obra_social_observer.in_(list(pami_ids) or [-1]), 'PAMI'),
                (ObsVD.obra_social_observer.in_(list(iapos_ids) or [-1]), 'IAPOS'),
                else_='OTROS',
            )

            # Filtro base: solo ventas con OS (no particulares).
            base_filters = [
                ObsVD.obra_social_observer.isnot(None),
                ObsVD.fecha_estadistica >= primer_dia,
            ]
            if os_filter == 'PAMI':
                base_filters.append(ObsVD.obra_social_observer.in_(list(pami_ids) or [-1]))
            elif os_filter == 'IAPOS':
                base_filters.append(ObsVD.obra_social_observer.in_(list(iapos_ids) or [-1]))

            # Card "del mes corriente": agrupar por receta (id_operacion + medico + cliente + fecha).
            # Para contar recetas únicas y sumar importes.
            recetas_q = (session.query(
                            _f.count(distinct(_f.concat(
                                ObsVD.id_operacion, '|',
                                _f.coalesce(ObsVD.cliente_observer, 0), '|',
                                _f.coalesce(ObsVD.medico_observer, 0), '|',
                                ObsVD.fecha_estadistica,
                            ))).label('recetas'),
                            _f.coalesce(_f.sum(ObsVD.importe_a_cargo_os), 0).label('importe_os'),
                            _f.coalesce(_f.sum(_f.coalesce(ObsVD.importe, 0)
                                                - _f.coalesce(ObsVD.importe_a_cargo_os, 0)), 0).label('importe_pac'),
                         )
                         .filter(*base_filters))
            row = recetas_q.first()
            mes_recetas = int(row[0] or 0) if row else 0
            mes_os_total = float(row[1] or 0) if row else 0
            mes_pac_total = float(row[2] or 0) if row else 0
            mes_ticket_prom = ((mes_os_total + mes_pac_total) / mes_recetas) if mes_recetas else 0

            # Breakdown por categoría (sin filtro os_filter, mostramos las 2 grandes).
            por_os = {}
            for cat_code, ids in (('PAMI', pami_ids), ('IAPOS', iapos_ids)):
                if not ids:
                    por_os[cat_code] = {'recetas': 0, 'importe_os': 0, 'importe_paciente': 0}
                    continue
                r = (session.query(
                        _f.count(distinct(_f.concat(
                            ObsVD.id_operacion, '|',
                            _f.coalesce(ObsVD.cliente_observer, 0), '|',
                            _f.coalesce(ObsVD.medico_observer, 0), '|',
                            ObsVD.fecha_estadistica,
                        ))),
                        _f.coalesce(_f.sum(ObsVD.importe_a_cargo_os), 0),
                        _f.coalesce(_f.sum(_f.coalesce(ObsVD.importe, 0)
                                            - _f.coalesce(ObsVD.importe_a_cargo_os, 0)), 0),
                     )
                     .filter(ObsVD.obra_social_observer.in_(list(ids)),
                             ObsVD.fecha_estadistica >= primer_dia)
                     .first())
                por_os[cat_code] = {
                    'recetas': int(r[0] or 0),
                    'importe_os': float(r[1] or 0),
                    'importe_paciente': float(r[2] or 0),
                }

            # Serie diaria últimos 30 días por categoría (solo PAMI e IAPOS).
            serie_dict = {}
            for i in range(30):
                d = hoy - timedelta(days=29 - i)
                serie_dict[d.isoformat()] = {'PAMI': 0.0, 'IAPOS': 0.0}
            serie_q = (session.query(
                            ObsVD.fecha_estadistica,
                            cat_expr.label('cat'),
                            _f.sum(ObsVD.importe_a_cargo_os),
                       )
                       .filter(ObsVD.obra_social_observer.isnot(None),
                               ObsVD.fecha_estadistica >= desde_30d)
                       .group_by(ObsVD.fecha_estadistica, cat_expr))
            for fecha, cat, imp in serie_q.all():
                if not fecha:
                    continue
                key = fecha.isoformat() if hasattr(fecha, 'isoformat') else str(fecha)
                if key in serie_dict and cat in ('PAMI', 'IAPOS'):
                    serie_dict[key][cat] += float(imp or 0)
            serie_list = [{'fecha': k, 'PAMI': round(v['PAMI'], 2), 'IAPOS': round(v['IAPOS'], 2)}
                          for k, v in serie_dict.items()]

            # Top médicos del mes (filtrado por OS si aplica).
            med_q = (session.query(
                        ObsVD.medico_observer,
                        _f.count(distinct(ObsVD.id_operacion)).label('recetas'),
                        _f.coalesce(_f.sum(ObsVD.importe_a_cargo_os), 0).label('importe'),
                     )
                     .filter(*base_filters)
                     .filter(ObsVD.medico_observer.isnot(None))
                     .group_by(ObsVD.medico_observer)
                     .order_by(_f.sum(ObsVD.importe_a_cargo_os).desc())
                     .limit(10)).all()
            med_ids = [r[0] for r in med_q if r[0]]
            med_nombres = dict(session.query(database.ObsMedico.observer_id,
                                              database.ObsMedico.medico)
                               .filter(database.ObsMedico.observer_id.in_(med_ids)).all()) if med_ids else {}
            top_medicos = [{
                'matricula': str(m_id),
                'nombre': med_nombres.get(m_id, f'Médico #{m_id}'),
                'recetas': int(rec or 0),
                'importe': float(imp or 0),
            } for (m_id, rec, imp) in med_q]

            # Top productos del mes (filtrado por OS si aplica).
            prod_q = (session.query(
                        ObsVD.producto_observer,
                        _f.coalesce(_f.sum(ObsVD.cantidad), 0).label('unidades'),
                        _f.coalesce(_f.sum(ObsVD.importe_a_cargo_os), 0).label('importe'),
                      )
                      .filter(*base_filters)
                      .group_by(ObsVD.producto_observer)
                      .order_by(_f.sum(ObsVD.importe_a_cargo_os).desc())
                      .limit(10)).all()
            prod_ids = [r[0] for r in prod_q]
            prod_descrs = dict(session.query(database.ObsProducto.observer_id,
                                              database.ObsProducto.descripcion)
                               .filter(database.ObsProducto.observer_id.in_(prod_ids)).all()) if prod_ids else {}
            top_productos = [{
                'descripcion': prod_descrs.get(pid, f'Producto #{pid}'),
                'unidades': int(uds or 0),
                'importe': float(imp or 0),
            } for (pid, uds, imp) in prod_q]

            # "Lotes abiertos": no tenemos lote real en DW. Como aproximación:
            # recetas del mes que aún no fueron presentadas (asumimos todas
            # están "abiertas" hasta que tengamos el dato de lote).
            lotes = []
            for cat_code in ('PAMI', 'IAPOS'):
                info = por_os.get(cat_code, {})
                if info.get('recetas', 0) > 0:
                    lotes.append({
                        'os_codigo': cat_code,
                        'cantidad_recetas': info['recetas'],
                        'importe_os': info['importe_os'],
                        'fecha_apertura': primer_dia,
                    })

        return render_template('os_dashboard.html',
                               os_filter=os_filter,
                               mes_recetas=mes_recetas,
                               mes_os_total=mes_os_total,
                               mes_pac_total=mes_pac_total,
                               mes_ticket_prom=mes_ticket_prom,
                               por_os=por_os,
                               serie=serie_list,
                               top_medicos=top_medicos,
                               top_productos=top_productos,
                               lotes=lotes)

    @app.route('/obras-sociales/dispensas')
    def os_dispensas():
        data = _mock_dispensas()

        os_filter = (request.args.get('os') or '').upper()
        plan_filter = (request.args.get('plan') or '').strip()
        tipo_filter = (request.args.get('tipo') or '').strip()
        q = (request.args.get('q') or '').strip().lower()
        desde = request.args.get('desde') or ''
        hasta = request.args.get('hasta') or ''

        def match(d):
            if os_filter in ('PAMI', 'IAPOS') and d['os_codigo'] != os_filter:
                return False
            if plan_filter and d['os_plan'] != plan_filter:
                return False
            if tipo_filter and d['receta_tipo'] != tipo_filter:
                return False
            if desde:
                try:
                    if d['fecha_venta'].date() < date.fromisoformat(desde):
                        return False
                except ValueError:
                    pass
            if hasta:
                try:
                    if d['fecha_venta'].date() > date.fromisoformat(hasta):
                        return False
                except ValueError:
                    pass
            if q:
                hay = f"{d['afiliado_nombre']} {d['afiliado_nro']} {d['afiliado_dni']} {d['medico_nombre']} {d['medico_matricula']} {d['ticket_validacion']}".lower()
                if q not in hay:
                    return False
            return True

        filtered = [d for d in data if match(d)]
        filtered.sort(key=lambda x: x['fecha_venta'], reverse=True)

        total_recetas = len(filtered)
        total_os = sum(d['importe_os'] for d in filtered)
        total_pac = sum(d['importe_paciente'] for d in filtered)

        return render_template('os_dispensas.html',
                               dispensas=filtered[:500],
                               total_recetas=total_recetas,
                               total_os=total_os,
                               total_pac=total_pac,
                               os_filter=os_filter,
                               plan_filter=plan_filter,
                               tipo_filter=tipo_filter,
                               q_text=q,
                               desde=desde,
                               hasta=hasta,
                               planes_pami=_PLANES['PAMI'],
                               planes_iapos=_PLANES['IAPOS'])
