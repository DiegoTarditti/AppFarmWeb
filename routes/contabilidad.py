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
