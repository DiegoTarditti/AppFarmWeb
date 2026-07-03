"""Módulo de contabilidad (standalone, gateado por perfil 'contabilidad').

Landing con menú propio que agrupa Proveedores, Cuentas corrientes,
Importar comprobantes ARCA y (próximamente) Rubros, Pagos y Libro de IVA.
"""

from collections import defaultdict
from datetime import datetime as _dt

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required

import database
from routes.cuentas import _solo_digitos

CONDICIONES_IVA = ['Responsable Inscripto', 'Monotributo', 'Exento',
                   'Consumidor Final', 'No Categorizado']

# Tipos de cuenta/forma de pago de contabilidad (para importar y conciliar).
TIPOS_CUENTA_PAGO = [
    ('banco', 'Banco'),
    ('mercadopago', 'MercadoPago'),
    ('efectivo', 'Efectivo'),
    ('otro', 'Otro'),
]


def init_app(app):

    @app.route('/contabilidad')
    @login_required
    def contabilidad_index():
        return render_template('contabilidad_index.html')

    @app.route('/contabilidad/proveedores')
    @login_required
    def contabilidad_proveedores():
        q = (request.args.get('q') or '').strip().lower()
        with database.get_db() as session:
            provs = (session.query(database.Provider)
                     .filter(database.Provider.activo == True)  # noqa: E712
                     .order_by(database.Provider.razon_social).all())

            # Facturas/NC indexadas por CUIT (dígitos) y por razón, para cruzar
            # con cada proveedor (mismo criterio que la cuenta corriente).
            by_cuit = defaultdict(list)
            by_razon = defaultdict(list)
            for iid, cuit, razon, total, fecha in session.query(
                    database.Invoice.id, database.Invoice.proveedor_cuit,
                    database.Invoice.proveedor_razon, database.Invoice.total,
                    database.Invoice.fecha).all():
                rec = (iid, float(total or 0), fecha)
                if cuit:
                    by_cuit[_solo_digitos(cuit)].append(rec)
                if razon:
                    by_razon[razon].append(rec)

            # Pagos / ajustes por proveedor_id.
            pagos = defaultdict(list)
            for pid, tipo, monto, fecha in session.query(
                    database.PagoAjusteCC.proveedor_id, database.PagoAjusteCC.tipo,
                    database.PagoAjusteCC.monto, database.PagoAjusteCC.fecha).all():
                pagos[pid].append((tipo, float(monto or 0), fecha))

            # Pagos estructurados (módulo Pagos) → haber.
            pagos_estr = defaultdict(list)
            for pid, monto, fecha in session.query(
                    database.Pago.proveedor_id, database.Pago.monto,
                    database.Pago.fecha).all():
                pagos_estr[pid].append((float(monto or 0), fecha))

            data = []
            for p in provs:
                # Cruce de facturas (dedup por id: cuit OR razón).
                vistos = {}
                for rec in by_cuit.get(_solo_digitos(p.cuit), []):
                    vistos[rec[0]] = rec
                for rec in by_razon.get(p.razon_social, []):
                    vistos[rec[0]] = rec
                saldo = sum(r[1] for r in vistos.values())  # total ya viene con signo (NC negativo)
                n_comp = len(vistos)
                fechas = [r[2] for r in vistos.values() if r[2]]

                for tipo, monto, fecha in pagos.get(p.id, []):
                    saldo += monto if tipo == 'AJUSTE_POS' else -monto
                    if fecha:
                        fechas.append(fecha)
                for monto, fecha in pagos_estr.get(p.id, []):
                    saldo -= monto
                    if fecha:
                        fechas.append(fecha)

                ultimo = max(fechas) if fechas else None
                data.append({
                    'id': p.id,
                    'razon_social': p.razon_social,
                    'cuit': p.cuit or '',
                    'condicion_iva': p.condicion_iva or '',
                    'tipo': p.tipo or '',
                    'domicilio': p.domicilio or '',
                    'n_comprobantes': n_comp,
                    'saldo': saldo,
                    'ultimo_mov': ultimo.strftime('%d/%m/%Y') if ultimo else '',
                })

            if q:
                data = [d for d in data
                        if q in d['razon_social'].lower() or q in d['cuit'].lower()]

        return render_template('contabilidad_proveedores.html',
                               proveedores=data, q=request.args.get('q', ''),
                               condiciones_iva=CONDICIONES_IVA)

    @app.route('/contabilidad/proveedores/guardar', methods=['POST'])
    @login_required
    def contabilidad_proveedor_guardar():
        pid = request.form.get('id', type=int)
        razon = (request.form.get('razon_social') or '').strip()
        if not razon:
            flash('La razón social es obligatoria.')
            return redirect(url_for('contabilidad_proveedores'))
        with database.get_db() as session:
            try:
                if pid:
                    p = session.get(database.Provider, pid)
                    if not p:
                        flash('Proveedor no encontrado.')
                        return redirect(url_for('contabilidad_proveedores'))
                else:
                    p = database.Provider(tipo='proveedor', activo=True)
                    session.add(p)
                p.razon_social = razon[:100]
                p.cuit = (request.form.get('cuit') or '').strip()[:20] or None
                p.domicilio = (request.form.get('domicilio') or '').strip()[:200] or None
                p.condicion_iva = (request.form.get('condicion_iva') or '').strip()[:30] or None
                session.commit()
                flash('Proveedor guardado.' if pid else 'Proveedor creado.')
            except Exception as e:
                session.rollback()
                flash(f'Error: {e}')
        return redirect(url_for('contabilidad_proveedores'))

    # ── Formas de pago (cuentas de banco / MercadoPago / efectivo) ──────────
    @app.route('/contabilidad/formas-pago')
    @login_required
    def contabilidad_formas_pago():
        with database.get_db() as session:
            cuentas = (session.query(database.CuentaPago)
                       .order_by(database.CuentaPago.activo.desc(),
                                 database.CuentaPago.nombre).all())
            data = [{'id': c.id, 'nombre': c.nombre, 'tipo': c.tipo,
                     'nro_cuenta': c.nro_cuenta or '', 'activo': c.activo}
                    for c in cuentas]
        tipos_lbl = dict(TIPOS_CUENTA_PAGO)
        return render_template('contabilidad_formas_pago.html',
                               cuentas=data, tipos=TIPOS_CUENTA_PAGO,
                               tipos_lbl=tipos_lbl)

    @app.route('/contabilidad/formas-pago/guardar', methods=['POST'])
    @login_required
    def contabilidad_forma_pago_guardar():
        cid = request.form.get('id', type=int)
        nombre = (request.form.get('nombre') or '').strip()
        if not nombre:
            flash('El nombre es obligatorio.')
            return redirect(url_for('contabilidad_formas_pago'))
        with database.get_db() as session:
            try:
                if cid:
                    c = session.get(database.CuentaPago, cid)
                    if not c:
                        flash('Cuenta no encontrada.')
                        return redirect(url_for('contabilidad_formas_pago'))
                else:
                    c = database.CuentaPago()
                    session.add(c)
                c.nombre = nombre[:80]
                tipo = (request.form.get('tipo') or 'banco').strip()
                c.tipo = tipo if tipo in dict(TIPOS_CUENTA_PAGO) else 'banco'
                c.nro_cuenta = (request.form.get('nro_cuenta') or '').strip()[:60] or None
                c.activo = request.form.get('activo') == 'on'
                session.commit()
                flash('Forma de pago guardada.' if cid else 'Forma de pago creada.')
            except Exception as e:
                session.rollback()
                flash(f'Error: {e}')
        return redirect(url_for('contabilidad_formas_pago'))

    @app.route('/contabilidad/formas-pago/<int:cuenta_id>/movimientos')
    @login_required
    def contabilidad_forma_pago_movimientos(cuenta_id):
        with database.get_db() as session:
            c = session.get(database.CuentaPago, cuenta_id)
            if not c:
                flash('Cuenta no encontrada.')
                return redirect(url_for('contabilidad_formas_pago'))
            cuenta = {'id': c.id, 'nombre': c.nombre, 'tipo': c.tipo,
                      'nro_cuenta': c.nro_cuenta or ''}
        return render_template('contabilidad_movimientos.html', cuenta=cuenta)

    # ── Pagos a proveedores (un pago cancela N facturas, sale de una CuentaPago) ─
    def _facturas_pendientes(session, provider):
        """Facturas FAC del proveedor con saldo pendiente (total - ya aplicado)."""
        q = session.query(database.Invoice).filter(database.Invoice.tipo_comprobante == 'FAC')
        if provider.cuit:
            q = q.filter((database.Invoice.proveedor_cuit == provider.cuit) |
                         (database.Invoice.proveedor_razon == provider.razon_social))
        else:
            q = q.filter(database.Invoice.proveedor_razon == provider.razon_social)
        invoices = q.order_by(database.Invoice.fecha).all()
        inv_ids = [i.id for i in invoices]
        aplicado = defaultdict(float)
        if inv_ids:
            for fid, m in session.query(database.PagoAplicacion.factura_id,
                                        database.PagoAplicacion.monto).filter(
                                        database.PagoAplicacion.factura_id.in_(inv_ids)).all():
                aplicado[fid] += float(m or 0)
        out = []
        for inv in invoices:
            total = abs(float(inv.total or 0))
            pend = round(total - aplicado.get(inv.id, 0.0), 2)
            if pend > 0.005:
                out.append({'id': inv.id, 'numero': inv.numero_factura or '',
                            'fecha': inv.fecha.strftime('%d/%m/%Y') if inv.fecha else '',
                            'total': total, 'pendiente': pend,
                            'conforme': inv.conforme_pago})
        return out

    @app.route('/contabilidad/pagos')
    @login_required
    def contabilidad_pagos():
        with database.get_db() as session:
            pagos = (session.query(database.Pago)
                     .order_by(database.Pago.fecha.desc(), database.Pago.id.desc()).all())
            prov_nombre = {p.id: p.razon_social for p in session.query(database.Provider).all()}
            cuenta_nombre = {c.id: c.nombre for c in session.query(database.CuentaPago).all()}
            data = [{'id': pg.id,
                     'fecha': pg.fecha.strftime('%d/%m/%Y') if pg.fecha else '',
                     'proveedor': prov_nombre.get(pg.proveedor_id, '—'),
                     'cuenta': cuenta_nombre.get(pg.cuenta_pago_id, '—'),
                     'monto': float(pg.monto or 0), 'nro': pg.nro_comprobante or '',
                     'n_facturas': len(pg.aplicaciones)} for pg in pagos]
        return render_template('contabilidad_pagos.html', pagos=data)

    @app.route('/contabilidad/pagos/nuevo')
    @login_required
    def contabilidad_pago_nuevo():
        proveedor_id = request.args.get('proveedor', type=int)
        with database.get_db() as session:
            proveedores = [{'id': p.id, 'razon_social': p.razon_social}
                           for p in session.query(database.Provider).filter_by(activo=True)
                           .order_by(database.Provider.razon_social).all()]
            provider = session.get(database.Provider, proveedor_id) if proveedor_id else None
            facturas = _facturas_pendientes(session, provider) if provider else []
            cuentas = [{'id': c.id, 'nombre': c.nombre} for c in
                       session.query(database.CuentaPago).filter_by(activo=True)
                       .order_by(database.CuentaPago.nombre).all()]
            prov = {'id': provider.id, 'razon_social': provider.razon_social} if provider else None
        return render_template('contabilidad_pago_nuevo.html', proveedores=proveedores,
                               provider=prov, facturas=facturas, cuentas=cuentas,
                               hoy=_dt.now().strftime('%Y-%m-%d'))

    @app.route('/contabilidad/pagos/guardar', methods=['POST'])
    @login_required
    def contabilidad_pago_guardar():
        proveedor_id = request.form.get('proveedor_id', type=int)
        cuenta_pago_id = request.form.get('cuenta_pago_id', type=int)
        fecha_str = request.form.get('fecha', '')
        nro = (request.form.get('nro_comprobante') or '').strip() or None
        obs = (request.form.get('observaciones') or '').strip() or None
        with database.get_db() as session:
            try:
                provider = session.get(database.Provider, proveedor_id) if proveedor_id else None
                if not provider:
                    flash('Proveedor inválido.')
                    return redirect(url_for('contabilidad_pago_nuevo'))
                fecha = (_dt.strptime(fecha_str, '%Y-%m-%d').date()
                         if fecha_str else _dt.now().date())
                # Aplicaciones: campos factura_<id> con monto > 0.
                apps = []
                for k, v in request.form.items():
                    if k.startswith('factura_') and (v or '').strip():
                        try:
                            fid = int(k.split('_', 1)[1])
                            m = round(float(str(v).replace(',', '.')), 2)
                        except ValueError:
                            continue
                        if m > 0:
                            apps.append((fid, m))
                suma_apps = round(sum(m for _, m in apps), 2)
                monto_total = request.form.get('monto', type=float)
                monto_total = round(monto_total, 2) if monto_total else suma_apps
                if monto_total <= 0:
                    flash('El pago necesita un monto o al menos una factura aplicada.')
                    return redirect(url_for('contabilidad_pago_nuevo', proveedor=proveedor_id))
                if suma_apps - monto_total > 0.005:
                    flash('La suma aplicada a facturas supera el monto del pago.')
                    return redirect(url_for('contabilidad_pago_nuevo', proveedor=proveedor_id))

                pago = database.Pago(proveedor_id=proveedor_id,
                                     cuenta_pago_id=cuenta_pago_id or None,
                                     fecha=fecha, monto=monto_total,
                                     nro_comprobante=nro, observaciones=obs)
                session.add(pago)
                session.flush()
                for fid, m in apps:
                    session.add(database.PagoAplicacion(pago_id=pago.id, factura_id=fid, monto=m))
                session.flush()
                cuenta = session.get(database.CuentaPago, cuenta_pago_id) if cuenta_pago_id else None
                cuenta_nom = cuenta.nombre if cuenta else None
                for fid, _m in apps:
                    inv = session.get(database.Invoice, fid)
                    if not inv:
                        continue
                    aplicado = sum(float(a.monto or 0) for a in
                                   session.query(database.PagoAplicacion).filter_by(factura_id=fid).all())
                    if aplicado >= abs(float(inv.total or 0)) - 0.005:
                        inv.pagado = True
                        inv.fecha_pago = fecha
                        if cuenta_nom:
                            inv.forma_pago = cuenta_nom[:40]
                        if nro:
                            inv.nro_comprobante_pago = nro[:40]
                session.commit()
                flash(f'Pago registrado (${monto_total:,.2f}).')
            except Exception as e:
                session.rollback()
                flash(f'Error: {e}')
                return redirect(url_for('contabilidad_pago_nuevo', proveedor=proveedor_id))
        return redirect(url_for('contabilidad_pagos'))

    @app.route('/contabilidad/pagos/<int:pago_id>/delete', methods=['POST'])
    @login_required
    def contabilidad_pago_delete(pago_id):
        with database.get_db() as session:
            try:
                pago = session.get(database.Pago, pago_id)
                if pago:
                    fids = [a.factura_id for a in pago.aplicaciones]
                    session.delete(pago)   # cascade borra aplicaciones
                    session.flush()
                    for fid in fids:
                        inv = session.get(database.Invoice, fid)
                        if not inv:
                            continue
                        aplicado = sum(float(a.monto or 0) for a in
                                       session.query(database.PagoAplicacion).filter_by(factura_id=fid).all())
                        if aplicado < abs(float(inv.total or 0)) - 0.005:
                            inv.pagado = False
                            inv.fecha_pago = None
                    session.commit()
                    flash('Pago eliminado.')
            except Exception as e:
                session.rollback()
                flash(f'Error: {e}')
        return redirect(url_for('contabilidad_pagos'))
