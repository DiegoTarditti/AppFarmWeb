"""Catálogo Kellerhoff: import del CSV + reporte de cobertura.

Fase 1 de la feature de equivalencias (ver docs/kellerhoff_equivalencias.md).
Acá solo: importar el catálogo que exporta Kellerhoff y reportar cuántos de
nuestros productos matchean (por EAN directo y por el puente Alfabeta/Troquel).
El matching automático y la resolución manual son fases posteriores.
"""
import csv
import io
from decimal import Decimal, InvalidOperation

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from database import KellerhoffCatalogo, KellerhoffEquivalencia, ObsCodigoBarras, ObsProducto, Producto, get_db, now_ar

# Columnas esperadas del CSV de Kellerhoff (header exacto).
_COLS = ('Tipo', 'Producto', 'AlfaBeta', 'Troquel', 'CodBarraPrinc',
         'Laboratorio', 'Precio', 'Neto', 'CadenaFrio', 'RequiereVale',
         'Trazable', 'CodKellerhoff')


def _precio_ar(s):
    """'46.163,92' o '46163,92' → Decimal. Vacío/None → None."""
    s = (s or '').strip()
    if not s:
        return None
    try:
        return Decimal(s.replace('.', '').replace(',', '.'))
    except (InvalidOperation, ValueError):
        return None


def _flag(s):
    return (s or '').strip() == '1'


def _clean(s):
    return (s or '').strip() or None


def _parse_kellerhoff_csv(text):
    """Parsea el texto del CSV (delimiter ';'). Devuelve (rows, errores).

    `rows`: lista de dicts listos para KellerhoffCatalogo (dedup por codigo_kellerhoff,
    último gana). `errores`: lista de strings con problemas de formato.
    """
    errores = []
    reader = csv.DictReader(io.StringIO(text), delimiter=';')
    faltan = [c for c in _COLS if c not in (reader.fieldnames or [])]
    if faltan:
        errores.append('Faltan columnas: ' + ', '.join(faltan))
        return [], errores

    by_cod = {}
    for i, r in enumerate(reader, start=2):
        cod = (r.get('CodKellerhoff') or '').strip()
        if not cod:
            errores.append(f'Fila {i}: sin CodKellerhoff (se ignora)')
            continue
        by_cod[cod] = {
            'codigo_kellerhoff': cod,
            'tipo':          _clean(r.get('Tipo')),
            'descripcion':   (r.get('Producto') or '').strip()[:200] or None,
            'alfabeta':      _clean(r.get('AlfaBeta')) if (r.get('AlfaBeta') or '').strip() not in ('', '0') else None,
            'troquel':       _clean(r.get('Troquel')) if (r.get('Troquel') or '').strip() not in ('', '0') else None,
            'ean':           _clean(r.get('CodBarraPrinc')),
            'laboratorio':   (r.get('Laboratorio') or '').strip()[:120] or None,
            'precio':        _precio_ar(r.get('Precio')),
            'neto':          _flag(r.get('Neto')),
            'cadena_frio':   _flag(r.get('CadenaFrio')),
            'requiere_vale': _flag(r.get('RequiereVale')),
            'trazable':      _flag(r.get('Trazable')),
            'importado_en':  now_ar(),
        }
    return list(by_cod.values()), errores


def _importar_catalogo(session, rows):
    """Reemplaza por completo kellerhoff_catalogo con `rows`. Devuelve count."""
    session.query(KellerhoffCatalogo).delete()
    session.flush()
    session.bulk_insert_mappings(KellerhoffCatalogo, rows)
    session.commit()
    return len(rows)


def _resumen_catalogo(session):
    """Stats baratas del catálogo cargado."""
    total = session.query(KellerhoffCatalogo).count()
    if not total:
        return None
    con_ean = (session.query(KellerhoffCatalogo)
               .filter(KellerhoffCatalogo.ean.isnot(None)).count())
    ult = (session.query(KellerhoffCatalogo.importado_en)
           .order_by(KellerhoffCatalogo.importado_en.desc()).first())
    equiv = session.query(KellerhoffEquivalencia).count()
    equiv_manual = (session.query(KellerhoffEquivalencia)
                    .filter_by(revisado=True).count())
    return {
        'total': total,
        'con_ean': con_ean,
        'sin_ean': total - con_ean,
        'importado_en': ult[0].strftime('%d/%m/%Y %H:%M') if ult and ult[0] else '—',
        'equivalencias': equiv,
        'equivalencias_manual': equiv_manual,
    }


def _tok_nombre(s):
    """Primer token normalizado del nombre (para guarda de coherencia)."""
    import unicodedata
    s = ''.join(c for c in unicodedata.normalize('NFKD', (s or '').upper())
                if not unicodedata.combining(c))
    parts = s.replace('-', ' ').replace('.', ' ').split()
    return parts[0] if parts else ''


def _recalcular_equivalencias(session):
    """Matching automático SOLO por EAN alternativo + guarda de nombre.

    Para un producto cuyo EAN principal NO está en el catálogo de Kellerhoff:
    si OTRO EAN del mismo producto ObServer SÍ está (candidato único) y el primer
    token del nombre coincide, guarda nuestro EAN principal → codigo_kellerhoff.
    De ahí el export deriva el EAN de Kellerhoff (CodBarraPrinc) para mandar el
    EAN que ellos reconocen.

    Alfabeta/Troquel NO se usan: ~31% de falsos positivos (ej. LACTATO→DAXAS) y
    Kellerhoff pediría el producto equivocado. Regla: preferir falso negativo.
    No pisa revisado=True. Idempotente.
    """
    from collections import defaultdict

    cat_by_ean = defaultdict(list)  # ean del catálogo → [(codkel, descripcion)]
    for codkel, ean, desc in session.query(
            KellerhoffCatalogo.codigo_kellerhoff, KellerhoffCatalogo.ean,
            KellerhoffCatalogo.descripcion):
        if ean:
            cat_by_ean[ean].append((codkel, desc or ''))

    obs_eans = defaultdict(list)
    for oid, ean, orden in session.query(
            ObsCodigoBarras.producto_observer, ObsCodigoBarras.codigo_barras,
            ObsCodigoBarras.orden).filter(ObsCodigoBarras.fecha_baja.is_(None)):
        if ean:
            obs_eans[oid].append((orden if orden is not None else 999, ean))

    existing = {e.ean: e for e in session.query(KellerhoffEquivalencia).all()}
    st = {'nuevas': 0, 'actualizadas': 0, 'ambiguas': 0,
          'incoherentes': 0, 'sin_candidato': 0, 'ya_directo': 0}

    for oid, desc in session.query(ObsProducto.observer_id, ObsProducto.descripcion):
        eans = obs_eans.get(oid)
        if not eans:
            continue
        eans.sort()
        principal = eans[0][1]
        if principal in cat_by_ean:
            st['ya_directo'] += 1
            continue
        # Candidatos: filas del catálogo cuyo EAN es un EAN alternativo nuestro.
        cands = []
        for (_, e) in eans:
            cands.extend(cat_by_ean.get(e, []))
        if not cands:
            st['sin_candidato'] += 1
            continue
        codkels = {c for c, _ in cands}
        if len(codkels) != 1:
            st['ambiguas'] += 1
            continue
        codkel = next(iter(codkels))
        # Guarda de nombre: descarta matches de producto distinto.
        if _tok_nombre(desc) != _tok_nombre(cands[0][1]):
            st['incoherentes'] += 1
            continue

        ex = existing.get(principal)
        if ex is not None and ex is not True:
            if ex.revisado:
                continue  # no pisar resolución manual
            if ex.codigo_kellerhoff == codkel and ex.metodo == 'ean_alt':
                continue
            ex.codigo_kellerhoff, ex.metodo, ex.confianza = codkel, 'ean_alt', 'ALTA'
            st['actualizadas'] += 1
        else:
            nuevo = KellerhoffEquivalencia(
                ean=principal, codigo_kellerhoff=codkel, metodo='ean_alt',
                confianza='ALTA', revisado=False, creado_por='auto')
            session.add(nuevo)
            existing[principal] = nuevo
            st['nuevas'] += 1

    session.commit()
    return st


def ean_export_de_producto(session, prod):
    """EAN que el export del pedido emite para este producto: el principal de
    ObServer (orden mínimo, activo). Fallback: el codigo_barra del master."""
    if prod.observer_id:
        row = (session.query(ObsCodigoBarras.codigo_barras)
               .filter(ObsCodigoBarras.producto_observer == prod.observer_id,
                       ObsCodigoBarras.fecha_baja.is_(None))
               .order_by(ObsCodigoBarras.orden.asc()).first())
        if row and row[0]:
            return row[0]
    return prod.codigo_barra


# Sentinel para "Kellerhoff no lo trae" (codigo_kellerhoff es NOT NULL).
KEL_NO_DISPONIBLE = 'NO_DISPONIBLE'


def estado_equivalencia(session, ean):
    """Estado de resolución del EAN contra Kellerhoff (para la ficha)."""
    if not session.query(KellerhoffCatalogo.codigo_kellerhoff).first():
        return {'estado': 'sin_catalogo', 'ean': ean}
    cat = session.query(KellerhoffCatalogo).filter_by(ean=ean).first()
    if cat:
        return {'estado': 'directo', 'ean': ean,
                'codigo': cat.codigo_kellerhoff, 'desc': cat.descripcion}
    eq = session.query(KellerhoffEquivalencia).filter_by(ean=ean).first()
    if eq:
        if eq.codigo_kellerhoff == KEL_NO_DISPONIBLE:
            return {'estado': 'no_disponible', 'ean': ean, 'revisado': eq.revisado}
        cat = (session.query(KellerhoffCatalogo)
               .filter_by(codigo_kellerhoff=eq.codigo_kellerhoff).first())
        return {'estado': 'equivalencia', 'ean': ean, 'codigo': eq.codigo_kellerhoff,
                'metodo': eq.metodo, 'revisado': eq.revisado,
                'desc': cat.descripcion if cat else ''}
    return {'estado': 'sin_resolver', 'ean': ean}


def corregir_eans(session, eans):
    """{ean_nuestro: ean_a_mandar}. Kellerhoff importa por EAN (no por su código
    interno). Si nuestro EAN ya está en su catálogo, se manda igual. Si NO está
    pero hay equivalencia, se manda el EAN de Kellerhoff (CodBarraPrinc) de ese
    producto — el que ellos reconocen. Si no hay arreglo, se manda el nuestro
    (falla igual que antes, no peor). Nunca vacío."""
    eans = list({e for e in eans if e})
    out = {e: e for e in eans}  # default: el mismo EAN
    if not eans:
        return out
    cat_eans = {e for (e,) in session.query(KellerhoffCatalogo.ean).filter(
        KellerhoffCatalogo.ean.in_(eans))}
    faltan = [e for e in eans if e not in cat_eans]
    if not faltan:
        return out
    eq = {x.ean: x.codigo_kellerhoff for x in session.query(KellerhoffEquivalencia).filter(
        KellerhoffEquivalencia.ean.in_(faltan))}
    codkels = [c for c in eq.values() if c and c != KEL_NO_DISPONIBLE]
    ck2ean = {}
    if codkels:
        for ck, cean in session.query(
                KellerhoffCatalogo.codigo_kellerhoff, KellerhoffCatalogo.ean).filter(
                KellerhoffCatalogo.codigo_kellerhoff.in_(codkels)):
            if cean:
                ck2ean[ck] = cean
    for e in faltan:
        cean = ck2ean.get(eq.get(e))
        if cean:
            out[e] = cean
    return out


def init_app(app):

    @app.route('/kellerhoff/catalogo')
    @login_required
    def kellerhoff_catalogo():
        with get_db() as session:
            resumen = _resumen_catalogo(session)
        return render_template('kellerhoff_catalogo.html', resumen=resumen)

    @app.route('/kellerhoff/equivalencias')
    @login_required
    def kellerhoff_equivalencias_list():
        """Lista de equivalencias EAN nuestro → producto Kellerhoff (distinta del
        barcode_mappings de facturas). Resuelve nombres para que se entienda."""
        with get_db() as session:
            eqs = (session.query(KellerhoffEquivalencia)
                   .order_by(KellerhoffEquivalencia.revisado.desc(),
                             KellerhoffEquivalencia.id.desc()).all())
            eans = [e.ean for e in eqs]
            ean_oid = {}
            if eans:
                for cb, oid in session.query(
                        ObsCodigoBarras.codigo_barras, ObsCodigoBarras.producto_observer).filter(
                        ObsCodigoBarras.codigo_barras.in_(eans),
                        ObsCodigoBarras.fecha_baja.is_(None)):
                    if cb not in ean_oid:
                        ean_oid[cb] = oid
            obs_desc = {}
            oids = list(set(ean_oid.values()))
            if oids:
                for oid, desc in session.query(
                        ObsProducto.observer_id, ObsProducto.descripcion).filter(
                        ObsProducto.observer_id.in_(oids)):
                    obs_desc[oid] = desc
            codkels = [e.codigo_kellerhoff for e in eqs
                       if e.codigo_kellerhoff != KEL_NO_DISPONIBLE]
            cat = {}
            if codkels:
                for ck, desc, cean in session.query(
                        KellerhoffCatalogo.codigo_kellerhoff, KellerhoffCatalogo.descripcion,
                        KellerhoffCatalogo.ean).filter(
                        KellerhoffCatalogo.codigo_kellerhoff.in_(codkels)):
                    cat[ck] = (desc or '', cean or '')
            filas = []
            for e in eqs:
                nd = obs_desc.get(ean_oid.get(e.ean), '') or '—'
                if e.codigo_kellerhoff == KEL_NO_DISPONIBLE:
                    kd, kean = 'Kellerhoff no lo trae', ''
                else:
                    kd, kean = cat.get(e.codigo_kellerhoff, ('(no está en el catálogo)', ''))
                filas.append({
                    'tipo': 'pedido', 'id': e.id, 'ean': e.ean, 'nombre': nd,
                    'metodo': e.metodo or '', 'revisado': e.revisado,
                    'codigo': e.codigo_kellerhoff, 'kel_desc': kd, 'kel_ean': kean,
                    'no_disponible': e.codigo_kellerhoff == KEL_NO_DISPONIBLE,
                })

            # Mappings de FACTURA de Kellerhoff (otra tabla, otro propósito).
            from database import BarcodeMapping, Provider
            kel_prov = (session.query(Provider)
                        .filter(Provider.razon_social.ilike('%keller%')).first())
            kel_prov_id = kel_prov.id if kel_prov else None
            facturas = []
            if kel_prov_id:
                facturas = (session.query(BarcodeMapping)
                            .filter_by(proveedor_id=kel_prov_id)
                            .order_by(BarcodeMapping.creado_en.desc()).all())
            for m in facturas:
                filas.append({
                    'tipo': 'factura', 'id': m.id, 'ean': m.codigo_barra_factura,
                    'nombre': '—', 'metodo': 'factura', 'revisado': False,
                    'codigo': '', 'kel_desc': 'EAN en ERP', 'kel_ean': m.codigo_barra_erp,
                    'no_disponible': False,
                })
            manuales = sum(1 for f in filas if f['revisado'])
        return render_template('kellerhoff_equivalencias.html',
                               filas=filas, total=len(filas), manuales=manuales,
                               kel_prov_id=kel_prov_id)

    @app.route('/kellerhoff/equivalencias/<int:eid>/eliminar', methods=['POST'])
    @login_required
    def kellerhoff_equivalencia_eliminar(eid):
        with get_db() as session:
            e = session.get(KellerhoffEquivalencia, eid)
            if e:
                session.delete(e)
                session.commit()
                flash('Equivalencia eliminada.')
        return redirect(url_for('kellerhoff_equivalencias_list'))

    @app.route('/kellerhoff/catalogo/importar', methods=['POST'])
    @login_required
    def kellerhoff_catalogo_importar():
        f = request.files.get('archivo')
        if not f or not f.filename:
            flash('Subí el CSV de Kellerhoff.', 'error')
            return redirect(url_for('kellerhoff_catalogo'))
        raw = f.read()
        # El CSV viene con BOM (utf-8-sig); fallback latin-1 por las dudas.
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('latin-1')
        rows, errores = _parse_kellerhoff_csv(text)
        if not rows:
            flash('No se pudo importar: ' + ('; '.join(errores) or 'CSV vacío'), 'error')
            return redirect(url_for('kellerhoff_catalogo'))
        with get_db() as session:
            n = _importar_catalogo(session, rows)
        msg = f'Catálogo Kellerhoff importado: {n} productos.'
        if errores:
            msg += f' ({len(errores)} filas con observaciones)'
        flash(msg)
        return redirect(url_for('kellerhoff_catalogo'))

    @app.route('/kellerhoff/catalogo/cobertura')
    @login_required
    def kellerhoff_catalogo_cobertura():
        """Reporte on-demand sobre los productos ObServer: cuántos resuelven con
        Kellerhoff por EAN directo (su EAN principal está en el catálogo) y
        cuántos más son corregibles por EAN alternativo del mismo producto."""
        from collections import defaultdict
        with get_db() as session:
            if not session.query(KellerhoffCatalogo).first():
                return jsonify({'ok': False, 'error': 'No hay catálogo cargado.'})

            cat_ean = {e for (e,) in session.query(KellerhoffCatalogo.ean) if e}
            obs_eans = defaultdict(list)
            for oid, ean, orden in session.query(
                    ObsCodigoBarras.producto_observer, ObsCodigoBarras.codigo_barras,
                    ObsCodigoBarras.orden).filter(ObsCodigoBarras.fecha_baja.is_(None)):
                if ean:
                    obs_eans[oid].append((orden if orden is not None else 999, ean))

            directo = corregible = sin = 0
            total = 0
            for oid, eans in obs_eans.items():
                total += 1
                eans.sort()
                principal = eans[0][1]
                if principal in cat_ean:
                    directo += 1
                elif any(e in cat_ean for (_, e) in eans):
                    corregible += 1
                else:
                    sin += 1

        return jsonify({
            'ok': True,
            'total': total,
            'por_ean': directo,
            'por_ean_alt': corregible,
            'sin_match': sin,
            'resueltos': directo + corregible,
        })

    @app.route('/kellerhoff/equivalencias/recalcular', methods=['POST'])
    @login_required
    def kellerhoff_equivalencias_recalcular():
        """Corre el matching automático (EAN-alt + guarda de nombre) y persiste."""
        with get_db() as session:
            if not session.query(KellerhoffCatalogo).first():
                return jsonify({'ok': False, 'error': 'No hay catálogo cargado.'})
            stats = _recalcular_equivalencias(session)
            total = session.query(KellerhoffEquivalencia).count()
            manuales = (session.query(KellerhoffEquivalencia)
                        .filter_by(revisado=True).count())
        return jsonify({'ok': True, 'stats': stats,
                        'total_equivalencias': total, 'manuales': manuales})

    @app.route('/api/kellerhoff/catalogo/buscar')
    @login_required
    def kellerhoff_catalogo_buscar():
        """Búsqueda multi-token sobre la descripción del catálogo (para elegir el
        código a mano en la ficha de Presentación)."""
        q = request.args.get('q', '').strip()
        if len(q) < 2:
            return jsonify({'data': []})
        with get_db() as session:
            query = session.query(KellerhoffCatalogo)
            for tok in q.split():
                query = query.filter(KellerhoffCatalogo.descripcion.ilike(f'%{tok}%'))
            rows = query.order_by(KellerhoffCatalogo.descripcion).limit(15).all()
            return jsonify({'data': [{
                'codigo_kellerhoff': r.codigo_kellerhoff,
                'descripcion': r.descripcion or '',
                'ean': r.ean or '',
                'precio': float(r.precio) if r.precio is not None else None,
            } for r in rows]})

    @app.route('/api/producto/kellerhoff-equivalencia', methods=['POST'])
    @login_required
    def kellerhoff_equivalencia_guardar():
        """Resuelve a mano la equivalencia de un EAN (desde Presentación).
        Body: {ean, accion: 'asignar'|'no_disponible'|'limpiar', codigo_kellerhoff?}."""
        body = request.get_json(silent=True) or {}
        ean = str(body.get('ean', '')).strip()
        accion = body.get('accion')
        if not ean:
            return jsonify({'ok': False, 'error': 'Falta EAN'}), 400

        if accion == 'asignar':
            codkel = str(body.get('codigo_kellerhoff', '')).strip()
            if not codkel:
                return jsonify({'ok': False, 'error': 'Elegí un producto del catálogo.'}), 400
        elif accion == 'no_disponible':
            codkel = KEL_NO_DISPONIBLE
        elif accion != 'limpiar':
            return jsonify({'ok': False, 'error': 'Acción inválida.'}), 400

        creado_por = (getattr(current_user, 'email', None)
                      or str(getattr(current_user, 'id', '')))
        with get_db() as session:
            eq = session.query(KellerhoffEquivalencia).filter_by(ean=ean).first()
            if accion == 'limpiar':
                if eq:
                    session.delete(eq)
                    session.commit()
            else:
                if eq:
                    eq.codigo_kellerhoff, eq.metodo = codkel, 'manual'
                    eq.confianza, eq.revisado, eq.creado_por = 'ALTA', True, creado_por
                else:
                    session.add(KellerhoffEquivalencia(
                        ean=ean, codigo_kellerhoff=codkel, metodo='manual',
                        confianza='ALTA', revisado=True, creado_por=creado_por))
                session.commit()
            estado = estado_equivalencia(session, ean)
        return jsonify({'ok': True, 'kellerhoff': estado})
