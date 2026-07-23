"""Cuentas corrientes routes."""

import csv
import io
import os
import unicodedata
from datetime import datetime as _dt

from flask import flash, redirect, render_template, request, url_for

import database
from helpers import get_providers
from services.cuenta_corriente import movimientos_proveedor

# Códigos AFIP de Nota de Crédito → restan (haber). El resto (facturas, ND) suma (debe).
_ARCA_NC = {3, 8, 13, 53, 110, 112, 113, 114, 119, 203, 208, 213}


def _norm_hdr(s):
    """Normaliza un encabezado: sin acentos, minúsculas, espacios colapsados."""
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode()
    return ' '.join(s.lower().split())


def _num_ar(s):
    """'1.234,56' → 1234.56 · '5786,24' → 5786.24 · '' → None."""
    s = (s or '').strip()
    if not s:
        return None
    s = s.replace('.', '').replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None


def _solo_digitos(s):
    return ''.join(ch for ch in (s or '') if ch.isdigit())


def _fmt_cuit(d):
    d = _solo_digitos(d)
    return f'{d[:2]}-{d[2:10]}-{d[10]}' if len(d) == 11 else d


def _parse_fecha(s):
    s = (s or '').strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return _dt.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _col_cuit_receptor(hdr):
    """Índice de la columna con el CUIT del RECEPTOR (nosotros). None si no está.

    Es el dato que dice de qué farmacia son los comprobantes: el emisor es la
    droguería y es el mismo para Badía que para Pieristei. ARCA no usa un
    nombre estable entre exports ('Nro. Doc. Receptor', 'CUIT Receptor'…), así
    que se matchea por forma en vez de por literal. Se descartan
    'Denominación Receptor' (el nombre, no el número) y 'Tipo Doc. Receptor'
    (el tipo de documento: 80 = CUIT).
    """
    for h, i in hdr.items():
        if 'receptor' not in h or 'denominacion' in h or 'tipo' in h:
            continue
        if any(k in h for k in ('cuit', 'doc', 'nro', 'numero')):
            return i
    return None


def _leer_comprobantes_arca(raw_bytes):
    """Parsea el CSV 'Mis Comprobantes' de ARCA. Devuelve (filas, error|None)."""
    try:
        txt = raw_bytes.decode('utf-8-sig')
    except UnicodeDecodeError:
        txt = raw_bytes.decode('latin-1')
    muestra = txt[:2000]
    delim = ';' if muestra.count(';') >= muestra.count(',') else ','
    rows = list(csv.reader(io.StringIO(txt), delimiter=delim))
    if not rows:
        return [], 'El archivo está vacío.'
    hdr = {_norm_hdr(h): i for i, h in enumerate(rows[0])}
    if 'fecha de emision' not in hdr:
        return [], ('No parece un export de "Mis Comprobantes" de ARCA '
                    '(falta la columna Fecha de Emisión).')

    i_receptor = _col_cuit_receptor(hdr)
    if i_receptor is None:
        return [], ('El CSV no trae la columna con el CUIT del receptor, así que no se '
                    'puede verificar de qué farmacia son los comprobantes. Bajá de ARCA '
                    'el export completo de "Mis Comprobantes → Recibidos".')

    def g(row, key):
        i = hdr.get(key)
        return row[i].strip() if (i is not None and i < len(row)) else ''

    out = []
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        out.append({
            'fecha': _parse_fecha(g(row, 'fecha de emision')),
            'tipo_cod': int(_solo_digitos(g(row, 'tipo de comprobante')) or 0),
            'pto_venta': _solo_digitos(g(row, 'punto de venta')),
            'numero': _solo_digitos(g(row, 'numero desde')),
            'cae': g(row, 'cod. autorizacion'),
            'cuit_emisor': g(row, 'nro. doc. emisor'),
            'cuit_receptor': _solo_digitos(
                row[i_receptor] if i_receptor < len(row) else ''),
            'denom_emisor': g(row, 'denominacion emisor'),
            'moneda': g(row, 'moneda') or 'PES',
            'tipo_cambio': _num_ar(g(row, 'tipo cambio')),
            'neto_gravado': _num_ar(g(row, 'imp. neto gravado total')),
            'neto_no_gravado': _num_ar(g(row, 'imp. neto no gravado')),
            'exento': _num_ar(g(row, 'imp. op. exentas')),
            'iva_25': _num_ar(g(row, 'iva 2,5%')),
            'iva_5': _num_ar(g(row, 'iva 5%')),
            'iva_105': _num_ar(g(row, 'iva 10,5%')),
            'iva_21': _num_ar(g(row, 'iva 21%')),
            'iva_27': _num_ar(g(row, 'iva 27%')),
            'total_iva': _num_ar(g(row, 'total iva')),
            'otros': _num_ar(g(row, 'otros tributos')),
            'total': _num_ar(g(row, 'imp. total')),
        })
    return out, None


def init_app(app):

    @app.route('/cuentas-corrientes')
    def cuentas_corrientes():
        with database.get_db() as session:
            prov_list = get_providers()

            provider_id = request.args.get('proveedor', type=int)
            provider = session.get(database.Provider, provider_id) if provider_id else None

            movimientos = []
            saldo_total = 0
            total_prefac = 0
            if provider:
                movimientos, resumen = movimientos_proveedor(session, provider)
                saldo_total = resumen['saldo']
                total_prefac = resumen['total_prefac']

            prov = {'id': provider.id, 'razon_social': provider.razon_social,
                    'cuit': provider.cuit or ''} if provider else None

            return render_template('cuenta_corriente.html', provider=prov,
                                   proveedores=prov_list, provider_id=provider_id or 0,
                                   movimientos=movimientos, saldo_total=saldo_total,
                                   total_prefac=total_prefac)

    @app.route('/comprobantes/importar', methods=['GET', 'POST'])
    def comprobantes_importar():
        if request.method == 'GET':
            return render_template('comprobantes_importar.html', resumen=None)

        archivo = request.files.get('archivo')
        if not archivo or not archivo.filename:
            flash('Subí el archivo CSV de "Mis Comprobantes" de ARCA.')
            return render_template('comprobantes_importar.html', resumen=None)

        filas, err = _leer_comprobantes_arca(archivo.read())
        if err:
            flash(err)
            return render_template('comprobantes_importar.html', resumen=None)

        # Guard de farmacia: el CSV tiene que ser de NUESTRO CUIT. `facturas` no
        # guarda a qué farmacia pertenece cada comprobante, así que un CSV ajeno
        # entra sin dejar rastro y se mezcla con el propio — pasó el 2026-07-21
        # con el export de Pieristei (9.724 comprobantes). Falla cerrado: si no
        # se puede verificar, no se importa nada.
        cuit_propio = _solo_digitos(os.environ.get('FARMACIA_CUIT', ''))
        if len(cuit_propio) != 11:
            flash('Falta configurar FARMACIA_CUIT (CUIT de esta farmacia, 11 dígitos) '
                  'en el entorno. Sin eso no se puede verificar de quién son los '
                  'comprobantes y el import queda bloqueado.')
            return render_template('comprobantes_importar.html', resumen=None)

        ajenos = sorted({f['cuit_receptor'] for f in filas
                         if f['cuit_receptor'] and f['cuit_receptor'] != cuit_propio})
        sin_receptor = sum(1 for f in filas if not f['cuit_receptor'])
        if ajenos or sin_receptor:
            detalle = (f"figuran a nombre de {', '.join(_fmt_cuit(c) for c in ajenos[:5])}"
                       if ajenos else f'{sin_receptor} filas no traen el CUIT del receptor')
            flash(f'No se importó nada: el CSV no es de esta farmacia '
                  f'({_fmt_cuit(cuit_propio)}) — {detalle}. Verificá con qué CUIT '
                  f'entraste a ARCA antes de bajar "Mis Comprobantes".')
            return render_template('comprobantes_importar.html', resumen=None)

        n_imp = n_dup = n_skip = 0
        provs_creados = []
        with database.get_db() as session:
            try:
                # Proveedores por CUIT (solo dígitos) para el match del emisor.
                prov_por_cuit = {}
                for p in session.query(database.Provider).all():
                    d = _solo_digitos(p.cuit)
                    if d:
                        prov_por_cuit[d] = p

                # Claves ya importadas (anti-duplicado): (cuit, tipo_cod, numero).
                existentes = set()
                for r in (session.query(database.Invoice.proveedor_cuit,
                                        database.Invoice.arca_tipo_codigo,
                                        database.Invoice.numero_factura)
                          .filter(database.Invoice.arca_tipo_codigo.isnot(None)).all()):
                    existentes.add((_solo_digitos(r[0]), r[1], r[2]))

                for f in filas:
                    if not f['fecha'] or not f['tipo_cod'] or not _solo_digitos(f['cuit_emisor']):
                        n_skip += 1
                        continue
                    cuit_d = _solo_digitos(f['cuit_emisor'])
                    numero_fmt = f"{(f['pto_venta'] or '0').zfill(5)}-{(f['numero'] or '0').zfill(8)}"
                    clave = (cuit_d, f['tipo_cod'], numero_fmt)
                    if clave in existentes:
                        n_dup += 1
                        continue

                    prov = prov_por_cuit.get(cuit_d)
                    if not prov:
                        prov = database.Provider(
                            razon_social=(f['denom_emisor'] or f'Proveedor {_fmt_cuit(cuit_d)}')[:100],
                            cuit=_fmt_cuit(cuit_d), tipo='proveedor', activo=True)
                        session.add(prov)
                        session.flush()
                        prov_por_cuit[cuit_d] = prov
                        provs_creados.append(prov.razon_social)

                    es_nc = f['tipo_cod'] in _ARCA_NC
                    signo = -1 if es_nc else 1

                    def _s(v, signo=signo):
                        return (signo * v) if v is not None else None

                    inv = database.Invoice(
                        numero_factura=numero_fmt,
                        fecha=f['fecha'],
                        proveedor_razon=prov.razon_social,
                        proveedor_cuit=prov.cuit,
                        tipo_comprobante='NCR' if es_nc else 'FAC',
                        origen='arca',
                        arca_tipo_codigo=f['tipo_cod'],
                        punto_venta=f['pto_venta'] or None,
                        cae=f['cae'] or None,
                        moneda=f['moneda'] or 'PES',
                        tipo_cambio=f['tipo_cambio'],
                        total=_s(f['total']),
                        monto_gravado=_s(f['neto_gravado']),
                        neto_no_gravado=_s(f['neto_no_gravado']),
                        monto_exento=_s(f['exento']),
                        iva_25=_s(f['iva_25']), iva_5=_s(f['iva_5']),
                        iva_105=_s(f['iva_105']), iva_21=_s(f['iva_21']),
                        iva_27=_s(f['iva_27']), total_iva=_s(f['total_iva']),
                        otros=_s(f['otros']),
                    )
                    session.add(inv)
                    existentes.add(clave)
                    n_imp += 1

                session.commit()
            except Exception as e:
                session.rollback()
                flash(f'Error importando: {e}')
                return render_template('comprobantes_importar.html', resumen=None)

        resumen = {'importados': n_imp, 'duplicados': n_dup, 'omitidos': n_skip,
                   'provs_creados': provs_creados, 'total_filas': len(filas)}
        return render_template('comprobantes_importar.html', resumen=resumen)

    @app.route('/provider/<int:provider_id>/cuenta-corriente/add', methods=['POST'])
    def cuenta_corriente_add(provider_id):
        from datetime import datetime as _dt
        with database.get_db() as session:
            try:
                tipo = request.form.get('tipo', '').strip()
                # Solo ajustes: un pago se carga por /cuentas-corrientes/pagos, que
                # lo imputa a facturas y descuenta de una cuenta. Si se pudiera
                # cargar también acá, el mismo pago entraba dos veces al saldo.
                # Las filas PAGO/NCR viejas se siguen leyendo en el extracto.
                if tipo not in ('AJUSTE_POS', 'AJUSTE_NEG'):
                    flash('Tipo inválido. Los pagos se registran desde Pagos, '
                          'que los imputa a facturas.')
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
        return redirect(url_for('cuentas_corrientes', proveedor=provider_id))

    @app.route('/provider/<int:provider_id>/cuenta-corriente/<int:mov_id>/delete', methods=['POST'])
    def cuenta_corriente_delete(provider_id, mov_id):
        with database.get_db() as session:
            try:
                pa = session.get(database.PagoAjusteCC, mov_id)
                if pa and pa.proveedor_id == provider_id:
                    session.delete(pa)
                    session.commit()
                    flash('Movimiento eliminado.')
            except Exception as e:
                session.rollback()
                flash(f'Error: {e}')
        return redirect(url_for('cuentas_corrientes', proveedor=provider_id))

    @app.route('/provider/<int:provider_id>/cuenta-corriente/conciliar', methods=['POST'])
    def cuenta_corriente_conciliar(provider_id):
        origen = request.form.get('origen')
        mov_id = request.form.get('mov_id', type=int)
        with database.get_db() as session:
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
        return redirect(url_for('cuentas_corrientes', proveedor=provider_id))

    @app.route('/provider/<int:provider_id>/cuenta-corriente/<int:mov_id>/edit-obs', methods=['POST'])
    def cuenta_corriente_edit_obs(provider_id, mov_id):
        with database.get_db() as session:
            try:
                pa = session.get(database.PagoAjusteCC, mov_id)
                if pa and pa.proveedor_id == provider_id:
                    pa.observaciones = request.form.get('observaciones', '').strip() or None
                    session.commit()
            except Exception as e:
                session.rollback()
                flash(f'Error: {e}')
        return redirect(url_for('cuentas_corrientes', proveedor=provider_id))
