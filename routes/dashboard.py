"""Dashboard routes."""

from flask import jsonify, render_template, request

import database


def init_app(app):

    @app.route('/dashboard')
    def dashboard():
        from datetime import date as _date

        from sqlalchemy import case as _case
        from sqlalchemy import func as _func
        try:
            n_days = max(1, min(365, int(request.args.get('n_days', 10))))
        except (ValueError, TypeError):
            n_days = 10
        lab_filter = (request.args.get('laboratorio') or '').strip()
        rot_filter = (request.args.get('rotacion') or '').strip().upper()
        if rot_filter not in ('A', 'M', 'B'):
            rot_filter = ''
        q_text = (request.args.get('q') or '').strip()
        only_sin_mov = request.args.get('sin_mov') == '1'
        # Filtro de rubro para las stats. Default Medicamentos: saca servicios y
        # accesorios que ensucian todo (Sellado de Recetas, Costo Receta/Cupón,
        # Retira en Farmacia, M.Farmacia…). 'todos' = sin filtrar.
        rubro_cat = (request.args.get('rubro_cat') or 'Medicamentos').strip()

        with database.get_db() as session:
            PA = database.ProductAnalytics

            def _rub(query):
                if rubro_cat and rubro_cat != 'todos':
                    return query.filter(PA.rubro == rubro_cat)
                return query

            cobertura_expr = _case(
                (PA.avg_monthly == 0, None),
                else_=(PA.stock * 30.0 / PA.avg_monthly)
            )
            q = _rub(session.query(PA, cobertura_expr.label('cobertura')))
            if lab_filter:
                q = q.filter(PA.laboratorio == lab_filter)
            if rot_filter:
                q = q.filter(PA.rotacion == rot_filter)
            if q_text:
                like = f'%{q_text}%'
                q = q.filter((PA.descripcion.ilike(like)) | (PA.codigo_barra.ilike(like)))
            if only_sin_mov:
                q_alerts = q.filter(PA.sin_mov_60d == 1).order_by(PA.descripcion.asc())
            else:
                q_alerts = q.filter(PA.avg_monthly > 0, PA.stock * 30.0 / PA.avg_monthly < n_days)
                q_alerts = q_alerts.order_by(cobertura_expr.asc())
            alerts = q_alerts.limit(200).all()

            labs = [row[0] for row in _rub(session.query(PA.laboratorio))
                    .filter(PA.laboratorio.isnot(None))
                    .distinct().order_by(PA.laboratorio).all()]
            # Rubros disponibles para el selector (con productos en el snapshot).
            rubros_cat = [r for (r,) in session.query(PA.rubro)
                          .filter(PA.rubro.isnot(None))
                          .distinct().order_by(PA.rubro).all()]

            total_products = _rub(session.query(_func.count(PA.codigo_barra))).scalar() or 0
            alerts_count = _rub(session.query(_func.count(PA.codigo_barra)).filter(
                PA.avg_monthly > 0, PA.stock * 30.0 / PA.avg_monthly < n_days
            )).scalar() or 0
            sin_mov_count = _rub(session.query(_func.count(PA.codigo_barra)).filter(
                PA.sin_mov_60d == 1
            )).scalar() or 0
            claims_open = session.query(_func.count(database.Claim.id)).filter(
                database.Claim.estado == 'ABIERTO'
            ).scalar() or 0
            first_of_month = _date.today().replace(day=1)
            invoices_month = session.query(_func.count(database.Invoice.id)).filter(
                database.Invoice.fecha >= first_of_month
            ).scalar() or 0

            codigos = [pa.codigo_barra for pa, _ in alerts]
            ultima_compra_map = {}
            if codigos:
                rows = session.query(database.Producto.codigo_barra, database.Producto.ultima_compra)\
                    .filter(database.Producto.codigo_barra.in_(codigos)).all()
                ultima_compra_map = {cb: fc for cb, fc in rows if fc}

            alert_rows = [{
                'codigo_barra': pa.codigo_barra,
                'descripcion': pa.descripcion,
                'laboratorio': pa.laboratorio,
                'stock': pa.stock,
                'avg_monthly': float(pa.avg_monthly or 0),
                'rotacion': pa.rotacion,
                'cobertura': round(cov, 1) if cov is not None else None,
                'precio_pvp': float(pa.precio_pvp or 0),
                'ultima_compra': ultima_compra_map.get(pa.codigo_barra),
                'sin_mov_60d': bool(pa.sin_mov_60d),
            } for pa, cov in alerts]

            ultima_act = session.query(_func.max(PA.actualizado_en)).scalar()

            base_q = _rub(session.query(PA))
            if lab_filter:
                base_q = base_q.filter(PA.laboratorio == lab_filter)
            top_qty_rows = base_q.order_by(PA.avg_monthly.desc()).limit(10).all()
            top_qty = [{
                'nombre': (p.descripcion or p.codigo_barra or '')[:40],
                'valor': float(p.avg_monthly or 0),
            } for p in top_qty_rows]

            valor_expr = PA.avg_monthly * PA.precio_pvp
            top_val_rows = base_q.order_by(valor_expr.desc()).limit(10).all()
            top_val = [{
                'nombre': (p.descripcion or p.codigo_barra or '')[:40],
                'valor': float(p.avg_monthly or 0) * float(p.precio_pvp or 0),
            } for p in top_val_rows]

            loss_expr = (PA.avg_monthly / 30.0) * PA.precio_pvp
            loss_rows = base_q.filter(PA.stock <= 0, PA.avg_monthly > 0)\
                .order_by(loss_expr.desc()).limit(10).all()
            top_loss = [{
                'nombre': (p.descripcion or p.codigo_barra or '')[:40],
                'valor': float(p.avg_monthly or 0) / 30.0 * float(p.precio_pvp or 0),
            } for p in loss_rows]

            capital_expr = PA.stock * PA.precio_pvp
            capital_q = _rub(session.query(_func.coalesce(_func.sum(capital_expr), 0)))
            muerto_q = _rub(session.query(_func.coalesce(_func.sum(capital_expr), 0))
                            .filter(PA.sin_mov_60d == 1))
            if lab_filter:
                capital_q = capital_q.filter(PA.laboratorio == lab_filter)
                muerto_q = muerto_q.filter(PA.laboratorio == lab_filter)
            capital_total = float(capital_q.scalar() or 0)
            stock_muerto_total = float(muerto_q.scalar() or 0)

            muerto_rows = base_q.filter(PA.sin_mov_60d == 1, PA.stock > 0)\
                .order_by(capital_expr.desc()).limit(10).all()
            top_muerto = [{
                'nombre': (p.descripcion or p.codigo_barra or '')[:40],
                'valor': float(p.stock or 0) * float(p.precio_pvp or 0),
            } for p in muerto_rows]

        return render_template('dashboard.html',
                               n_days=n_days,
                               lab_filter=lab_filter,
                               rot_filter=rot_filter,
                               q_text=q_text,
                               only_sin_mov=only_sin_mov,
                               rubro_cat=rubro_cat,
                               rubros_cat=rubros_cat,
                               labs=labs,
                               alerts=alert_rows,
                               total_products=total_products,
                               alerts_count=alerts_count,
                               sin_mov_count=sin_mov_count,
                               claims_open=claims_open,
                               invoices_month=invoices_month,
                               ultima_act=ultima_act,
                               top_qty=top_qty,
                               top_val=top_val,
                               top_loss=top_loss,
                               top_muerto=top_muerto,
                               capital_total=capital_total,
                               stock_muerto_total=stock_muerto_total)

    @app.route('/dashboard/recalcular', methods=['POST'])
    def dashboard_recalcular():
        """Refresca el snapshot product_analytics desde Observer (datos vivos).
        Reemplaza la fuente vieja (flujo /purchase, en desuso)."""
        from services.dashboard_snapshot import refrescar_product_analytics
        try:
            with database.get_db() as session:
                stats = refrescar_product_analytics(session)
            return jsonify({'ok': True, **stats})
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 500

    @app.route('/dashboard/help')
    def dashboard_help():
        return render_template('dashboard_help.html')
