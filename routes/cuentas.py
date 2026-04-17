"""Cuentas corrientes routes."""

from flask import render_template, request, redirect, url_for, flash
import database


def init_app(app):

    @app.route('/cuentas-corrientes')
    def cuentas_corrientes():
        session = database.SessionLocal()
        try:
            proveedores = session.query(database.Provider).order_by(database.Provider.razon_social).all()
            prov_list = [{'id': p.id, 'razon_social': p.razon_social} for p in proveedores]

            provider_id = request.args.get('proveedor', type=int)
            provider = None
            movimientos = []
            saldo_total = 0

            if provider_id:
                provider = session.get(database.Provider, provider_id)

            if provider:
                q_inv = session.query(database.Invoice)
                if provider.cuit:
                    q_inv = q_inv.filter(
                        (database.Invoice.proveedor_cuit == provider.cuit) |
                        (database.Invoice.proveedor_razon == provider.razon_social)
                    )
                else:
                    q_inv = q_inv.filter(database.Invoice.proveedor_razon == provider.razon_social)
                invoices = q_inv.order_by(database.Invoice.fecha).all()

                pagos_ajustes = (session.query(database.PagoAjusteCC)
                                 .filter_by(proveedor_id=provider_id)
                                 .order_by(database.PagoAjusteCC.fecha).all())

                inv_ids = [inv.id for inv in invoices]
                reclamos_map = {}
                if inv_ids:
                    for c in session.query(database.Claim).filter(database.Claim.factura_id.in_(inv_ids)).all():
                        reclamos_map[c.factura_id] = c.estado
                ingresadas = set()
                if inv_ids:
                    from sqlalchemy import distinct
                    ingresadas = {r[0] for r in session.query(distinct(database.StockDifference.factura_id))
                                  .filter(database.StockDifference.factura_id.in_(inv_ids)).all()}

                for inv in invoices:
                    signo = 1 if inv.tipo_comprobante == 'FAC' else -1
                    reclamo_est = reclamos_map.get(inv.id)
                    obs_parts = []
                    if reclamo_est:
                        obs_parts.append(f'Reclamo: {reclamo_est}')
                    movimientos.append({
                        'fecha': inv.fecha,
                        'fecha_proceso': inv.creado_en.strftime('%d/%m/%Y') if inv.creado_en else '',
                        'tipo': inv.tipo_comprobante,
                        'comprobante': inv.numero_factura or '',
                        'debe': float(abs(inv.total or 0)) if signo == 1 else 0,
                        'haber': float(abs(inv.total or 0)) if signo == -1 else 0,
                        'obs': ' · '.join(obs_parts),
                        'ingresada': inv.id in ingresadas,
                        'reclamo_estado': reclamo_est,
                        'conciliado': inv.conciliado,
                        'origen': 'factura',
                        'id': inv.id,
                    })
                for pa in pagos_ajustes:
                    es_debe = pa.tipo == 'AJUSTE_POS'
                    movimientos.append({
                        'fecha': pa.fecha,
                        'fecha_proceso': '',
                        'tipo': pa.tipo,
                        'comprobante': pa.numero_comprobante or '',
                        'debe': float(pa.monto) if es_debe else 0,
                        'haber': float(pa.monto) if not es_debe else 0,
                        'obs': pa.observaciones or '',
                        'ingresada': None,
                        'reclamo_estado': None,
                        'conciliado': pa.conciliado,
                        'origen': 'manual',
                        'id': pa.id,
                    })

                movimientos.sort(key=lambda m: (m['fecha'], m['tipo']))
                saldo = 0
                for m in movimientos:
                    saldo += m['debe'] - m['haber']
                    m['saldo'] = saldo
                saldo_total = saldo

            prov = {'id': provider.id, 'razon_social': provider.razon_social,
                    'cuit': provider.cuit or ''} if provider else None
        finally:
            session.close()

        return render_template('cuenta_corriente.html', provider=prov,
                               proveedores=prov_list, provider_id=provider_id or 0,
                               movimientos=movimientos, saldo_total=saldo_total)

    @app.route('/provider/<int:provider_id>/cuenta-corriente/add', methods=['POST'])
    def cuenta_corriente_add(provider_id):
        from datetime import datetime as _dt
        session = database.SessionLocal()
        try:
            tipo = request.form.get('tipo', '').strip()
            if tipo not in ('PAGO', 'NCR', 'AJUSTE_POS', 'AJUSTE_NEG'):
                flash('Tipo inválido.')
                return redirect(url_for('cuentas_corrientes', proveedor=provider_id))
            monto = float(request.form.get('monto', 0))
            if monto <= 0:
                flash('El monto debe ser positivo.')
                return redirect(url_for('cuentas_corrientes', proveedor=provider_id))
            fecha_str = request.form.get('fecha', '')
            fecha = _dt.strptime(fecha_str, '%Y-%m-%d').date() if fecha_str else _dt.now().date()
            pa = database.PagoAjusteCC(
                proveedor_id=provider_id,
                tipo=tipo,
                fecha=fecha,
                monto=monto,
                numero_comprobante=request.form.get('comprobante', '').strip() or None,
                observaciones=request.form.get('observaciones', '').strip() or None,
            )
            session.add(pa)
            session.commit()
            flash(f'{tipo.replace("_", " ").title()} registrado.')
        except Exception as e:
            session.rollback()
            flash(f'Error: {e}')
        finally:
            session.close()
        return redirect(url_for('cuentas_corrientes', proveedor=provider_id))

    @app.route('/provider/<int:provider_id>/cuenta-corriente/<int:mov_id>/delete', methods=['POST'])
    def cuenta_corriente_delete(provider_id, mov_id):
        session = database.SessionLocal()
        try:
            pa = session.get(database.PagoAjusteCC, mov_id)
            if pa and pa.proveedor_id == provider_id:
                session.delete(pa)
                session.commit()
                flash('Movimiento eliminado.')
        except Exception as e:
            session.rollback()
            flash(f'Error: {e}')
        finally:
            session.close()
        return redirect(url_for('cuentas_corrientes', proveedor=provider_id))

    @app.route('/provider/<int:provider_id>/cuenta-corriente/conciliar', methods=['POST'])
    def cuenta_corriente_conciliar(provider_id):
        origen = request.form.get('origen')
        mov_id = request.form.get('mov_id', type=int)
        session = database.SessionLocal()
        try:
            if origen == 'factura' and mov_id:
                obj = session.get(database.Invoice, mov_id)
            elif origen == 'manual' and mov_id:
                obj = session.get(database.PagoAjusteCC, mov_id)
            else:
                obj = None
            if obj:
                obj.conciliado = not obj.conciliado
                session.commit()
        except Exception as e:
            session.rollback()
            flash(f'Error: {e}')
        finally:
            session.close()
        return redirect(url_for('cuentas_corrientes', proveedor=provider_id))

    @app.route('/provider/<int:provider_id>/cuenta-corriente/<int:mov_id>/edit-obs', methods=['POST'])
    def cuenta_corriente_edit_obs(provider_id, mov_id):
        session = database.SessionLocal()
        try:
            pa = session.get(database.PagoAjusteCC, mov_id)
            if pa and pa.proveedor_id == provider_id:
                pa.observaciones = request.form.get('observaciones', '').strip() or None
                session.commit()
        except Exception as e:
            session.rollback()
            flash(f'Error: {e}')
        finally:
            session.close()
        return redirect(url_for('cuentas_corrientes', proveedor=provider_id))
