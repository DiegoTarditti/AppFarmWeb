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


def _recalcular_equivalencias(session):
    """Matching automático nuestro EAN → codigo_kellerhoff por el puente
    Alfabeta → Troquel, SOLO para productos que no resuelven por EAN directo.

    - Solo persiste candidatos NO ambiguos (1 solo codigo_kellerhoff) → confianza ALTA.
    - NO pisa equivalencias con revisado=True (resueltas a mano).
    - El match por nombre queda para resolución manual (Fase 3), por seguridad.
    Devuelve stats. La equivalencia se clava sobre el EAN principal del producto
    (el que el export del pedido emite: ObsCodigoBarras orden mínimo, activo).
    """
    from collections import defaultdict

    by_ean = defaultdict(set)
    by_alfa = defaultdict(set)
    by_troq = defaultdict(set)
    for codkel, ean, alfa, troq in session.query(
            KellerhoffCatalogo.codigo_kellerhoff, KellerhoffCatalogo.ean,
            KellerhoffCatalogo.alfabeta, KellerhoffCatalogo.troquel):
        if ean:
            by_ean[ean].add(codkel)
        if alfa:
            by_alfa[alfa].add(codkel)
        if troq:
            by_troq[troq].add(codkel)

    # EANs activos por producto ObServer (orden mínimo = principal).
    obs_eans = defaultdict(list)
    for oid, ean, orden in session.query(
            ObsCodigoBarras.producto_observer, ObsCodigoBarras.codigo_barras,
            ObsCodigoBarras.orden).filter(ObsCodigoBarras.fecha_baja.is_(None)):
        if ean:
            obs_eans[oid].append((orden if orden is not None else 999, ean))

    existing = {e.ean: e for e in session.query(KellerhoffEquivalencia).all()}
    st = {'nuevas': 0, 'actualizadas': 0, 'ambiguas': 0,
          'sin_candidato': 0, 'ya_directo': 0}

    def _unico(candidatos):
        """1 solo codkel → ese; varios → 'AMBIGUO'; ninguno → None."""
        if not candidatos:
            return None
        return next(iter(candidatos)) if len(candidatos) == 1 else 'AMBIGUO'

    for oid, alfa, troq in session.query(
            ObsProducto.observer_id, ObsProducto.codigo_alfabeta, ObsProducto.troquel):
        eans = obs_eans.get(oid)
        if not eans:
            continue
        eans.sort()
        principal = eans[0][1]
        # El export emite el EAN principal. Si ESE está en el catálogo, Kellerhoff
        # resuelve directo → no hace falta equivalencia.
        if principal in by_ean:
            st['ya_directo'] += 1
            continue

        # Cascada: EAN alternativo (otro EAN del mismo producto que sí está en el
        # catálogo) → Alfabeta → Troquel. Cada paso exige candidato único.
        codkel = metodo = None
        for fuente, key in (
                ('ean_alt', None),  # especial: une todos los alts
                ('alfabeta', alfa),
                ('troquel', str(troq) if troq else None)):
            if fuente == 'ean_alt':
                cands = set()
                for (_, e) in eans:
                    cands |= by_ean.get(e, set())
            elif fuente == 'alfabeta':
                cands = by_alfa.get(key, set()) if key else set()
            else:
                cands = by_troq.get(key, set()) if key else set()
            u = _unico(cands)
            if u == 'AMBIGUO':
                metodo = 'AMBIGUO'
                break
            if u:
                codkel, metodo = u, fuente
                break

        if metodo == 'AMBIGUO':
            st['ambiguas'] += 1
            continue
        if not codkel:
            st['sin_candidato'] += 1
            continue

        ex = existing.get(principal)
        if ex is not None and ex is not True:
            if ex.revisado:
                continue  # no pisar resolución manual
            if ex.codigo_kellerhoff == codkel and ex.metodo == metodo:
                continue
            ex.codigo_kellerhoff, ex.metodo, ex.confianza = codkel, metodo, 'ALTA'
            st['actualizadas'] += 1
        else:
            nuevo = KellerhoffEquivalencia(
                ean=principal, codigo_kellerhoff=codkel, metodo=metodo,
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


def resolver_codigos(session, eans):
    """{ean: codigo_kellerhoff} para exportar el pedido. Cascada: catálogo
    directo por EAN → equivalencia guardada. Sin resolver o 'no lo trae' → ''."""
    eans = [e for e in {e for e in eans if e}]
    if not eans:
        return {}
    out = {}
    for ean, codkel in session.query(
            KellerhoffCatalogo.ean, KellerhoffCatalogo.codigo_kellerhoff).filter(
            KellerhoffCatalogo.ean.in_(eans)):
        if ean and ean not in out:
            out[ean] = codkel
    faltan = [e for e in eans if e not in out]
    if faltan:
        for ean, codkel in session.query(
                KellerhoffEquivalencia.ean, KellerhoffEquivalencia.codigo_kellerhoff).filter(
                KellerhoffEquivalencia.ean.in_(faltan)):
            out[ean] = '' if codkel == KEL_NO_DISPONIBLE else codkel
    return out


def init_app(app):

    @app.route('/kellerhoff/catalogo')
    @login_required
    def kellerhoff_catalogo():
        with get_db() as session:
            resumen = _resumen_catalogo(session)
        return render_template('kellerhoff_catalogo.html', resumen=resumen)

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
        """Reporte on-demand: de nuestros productos, cuántos resuelven contra el
        catálogo por EAN directo y cuántos más se rescatarían por Alfabeta/Troquel."""
        with get_db() as session:
            if not session.query(KellerhoffCatalogo).first():
                return jsonify({'ok': False, 'error': 'No hay catálogo cargado.'})

            # Índices del catálogo.
            cat_ean = set()
            cat_alfa = set()
            cat_troq = set()
            for ean, alfa, troq in session.query(
                    KellerhoffCatalogo.ean, KellerhoffCatalogo.alfabeta,
                    KellerhoffCatalogo.troquel):
                if ean:
                    cat_ean.add(ean)
                if alfa:
                    cat_alfa.add(alfa)
                if troq:
                    cat_troq.add(troq)

            # EAN → (alfabeta, troquel) de NUESTROS productos vía ObServer.
            obs_attrs = {}
            for oid, alfa, troq in session.query(
                    ObsProducto.observer_id, ObsProducto.codigo_alfabeta,
                    ObsProducto.troquel):
                obs_attrs[oid] = (alfa, str(troq) if troq else None)
            ean_to_obs = {}
            for ean, oid in session.query(ObsCodigoBarras.codigo_barras,
                                          ObsCodigoBarras.producto_observer):
                if ean and ean not in ean_to_obs:
                    ean_to_obs[ean] = oid

            por_ean = por_alfa = por_troq = sin = 0
            total = 0
            for (cb, a1, a2, a3) in session.query(
                    Producto.codigo_barra, Producto.codigo_barra_alt1,
                    Producto.codigo_barra_alt2, Producto.codigo_barra_alt3):
                total += 1
                eans = [e for e in (cb, a1, a2, a3) if e]
                if any(e in cat_ean for e in eans):
                    por_ean += 1
                    continue
                # puente alfabeta/troquel via obs
                alfa = troq = None
                for e in eans:
                    oid = ean_to_obs.get(e)
                    if oid and oid in obs_attrs:
                        alfa, troq = obs_attrs[oid]
                        break
                if alfa and alfa in cat_alfa:
                    por_alfa += 1
                elif troq and troq in cat_troq:
                    por_troq += 1
                else:
                    sin += 1

        return jsonify({
            'ok': True,
            'total': total,
            'por_ean': por_ean,
            'por_alfabeta': por_alfa,
            'por_troquel': por_troq,
            'sin_match': sin,
            'resueltos': por_ean + por_alfa + por_troq,
        })

    @app.route('/kellerhoff/equivalencias/recalcular', methods=['POST'])
    @login_required
    def kellerhoff_equivalencias_recalcular():
        """Corre el matching automático (Alfabeta→Troquel) y persiste equivalencias."""
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
