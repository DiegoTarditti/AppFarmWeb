"""Compartido peer-to-peer entre farmacias del grupo (sin hub).

Cada farmacia publica datasets curados (ofertas con mínimo, equivalencias,
módulos) en SU tabla `archivos_compartidos` (INSERT local vía /api/compartido/push).
Las demás los LEEN read-only (services/compartido_sync → Sucursal.url_externa) y
los importan on-demand; lo consumido queda en `compartido_importado` (log local).

Reemplaza el viejo esquema hub HTTP (HUB_BASE_URL / HUB_TOKEN) — ya no se usan.
"""
import json

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from database import ArchivoCompartido, CompartidoImportado, Laboratorio, OfertaMinimo, get_db
from helpers import now_ar
from services import compartido_sync

_TIPOS_LABEL = {
    'oferta_minimo': 'Ofertas con mínimo',
    'modulos':       'Módulos',
    'equivalencias': 'Equivalencias de barcode',
}


def _origen_local():
    """Nombre de esta sucursal para sellar `farmacia_origen` al publicar."""
    try:
        from services.transferencias import _local_slug, listar_sucursales
        sucs = listar_sucursales()
        slug = _local_slug(sucs)
        return next((s['nombre'] for s in sucs if s['slug'] == slug), None) or slug or 'local'
    except Exception:
        import os
        return os.environ.get('OBSERVER_ID_FARMACIA', 'local')


def init_app(app):

    @app.route('/api/compartido/push', methods=['POST'])
    @login_required
    def api_compartido_push():
        """Publica un dataset curado en la tabla local (buzón de salida)."""
        data = request.get_json(silent=True) or {}
        tipo   = (data.get('tipo') or '').strip()
        nombre = (data.get('nombre') or '').strip()
        desc   = (data.get('descripcion') or '').strip() or None
        items  = data.get('items')
        destinatarios = (data.get('destinatarios') or 'todos').strip() or 'todos'
        if not tipo or not nombre or not isinstance(items, list):
            return jsonify({'ok': False, 'error': 'Faltan campos: tipo, nombre, items[]'}), 400
        with get_db() as session:
            arch = ArchivoCompartido(
                tipo=tipo, nombre=nombre, descripcion=desc,
                farmacia_origen=_origen_local(), destinatarios=destinatarios,
                json_data=json.dumps(items, ensure_ascii=False),
                n_items=len(items), creado_en=now_ar(),
            )
            session.add(arch)
            session.commit()
            compartido_sync._count_cache['ts'] = 0.0  # invalidar count de los peers
            return jsonify({'ok': True, 'id': arch.id, 'n_items': len(items)})

    @app.route('/compartido')
    @login_required
    def compartido_index():
        """Compartidos de los peers (con estado) + lo publicado por esta farmacia."""
        del_peers = compartido_sync.listar_peers()
        errores = [{'origen': it['origen_nombre'], 'error': it['error']}
                   for it in del_peers if 'error' in it]

        with get_db() as session:
            log = {(r.origen_slug, r.archivo_id): r.accion
                   for r in session.query(CompartidoImportado).all()}
            mios = [{
                'id': m.id, 'tipo': m.tipo, 'tipo_label': _TIPOS_LABEL.get(m.tipo, m.tipo),
                'nombre': m.nombre, 'descripcion': m.descripcion, 'n_items': m.n_items,
                'destinatarios': m.destinatarios,
                'creado_en': m.creado_en.strftime('%d/%m/%Y %H:%M') if m.creado_en else '',
            } for m in session.query(ArchivoCompartido).order_by(
                ArchivoCompartido.creado_en.desc()).limit(100).all()]

        items = []
        for it in del_peers:
            if 'error' in it:
                continue
            items.append({
                **it,
                'tipo_label': _TIPOS_LABEL.get(it['tipo'], it['tipo']),
                'estado': log.get((it['origen_slug'], it['id']), 'nuevo'),
                'creado_en_fmt': it['creado_en'].strftime('%d/%m/%Y %H:%M') if it.get('creado_en') else '',
            })
        items.sort(key=lambda x: x['estado'] != 'nuevo')  # nuevos primero (sort estable)

        return render_template('compartido.html',
                               items=items, errores=errores, mios=mios,
                               sin_peers=not compartido_sync.peers(),
                               tipos_label=_TIPOS_LABEL)

    @app.route('/compartido/importar', methods=['POST'])
    @login_required
    def compartido_importar():
        slug = (request.form.get('origen_slug') or '').strip()
        archivo_id = request.form.get('archivo_id', type=int)
        if not slug or not archivo_id:
            flash('Falta origen o id.', 'error')
            return redirect(url_for('compartido_index'))
        arch = compartido_sync.leer_archivo(slug, archivo_id)
        if not arch:
            flash('No se pudo leer el archivo del peer (¿offline o ya no existe?).', 'error')
            return redirect(url_for('compartido_index'))
        n = _importar_items(arch['tipo'], arch['nombre'], arch['items'])
        if n is None:
            flash(f'Tipo "{arch["tipo"]}" no soportado para importación.', 'error')
            return redirect(url_for('compartido_index'))
        _registrar(slug, archivo_id, arch['tipo'], arch['nombre'], 'importado')
        flash(f'Importados {n} registros de "{arch["nombre"]}".', 'success')
        return redirect(url_for('compartido_index'))

    @app.route('/compartido/descartar', methods=['POST'])
    @login_required
    def compartido_descartar():
        slug = (request.form.get('origen_slug') or '').strip()
        archivo_id = request.form.get('archivo_id', type=int)
        if slug and archivo_id:
            _registrar(slug, archivo_id, request.form.get('tipo', ''),
                       request.form.get('nombre', ''), 'descartado')
            flash('Marcado como descartado (no vuelve a avisar).', 'success')
        return redirect(url_for('compartido_index'))


def _registrar(slug, archivo_id, tipo, nombre, accion):
    """Upsert en el log local `compartido_importado` + invalida el count cacheado."""
    usuario = getattr(current_user, 'username', None) or getattr(current_user, 'nombre', None)
    with get_db() as session:
        ex = session.query(CompartidoImportado).filter_by(
            origen_slug=slug, archivo_id=archivo_id).first()
        if ex:
            ex.accion, ex.usuario, ex.creado_en = accion, usuario, now_ar()
            if tipo:   ex.tipo = tipo
            if nombre: ex.nombre = nombre
        else:
            session.add(CompartidoImportado(
                origen_slug=slug, archivo_id=archivo_id, tipo=tipo or None,
                nombre=nombre or None, accion=accion, usuario=usuario, creado_en=now_ar()))
        session.commit()
    compartido_sync._count_cache['ts'] = 0.0


def _importar_items(tipo, nombre, items):
    """Importa items al modelo local según tipo. n registros o None si tipo inválido."""
    if tipo == 'oferta_minimo':
        return _importar_oferta_minimo(items)
    return None


def _importar_oferta_minimo(items):
    """Upsert de OfertaMinimo por (ean, laboratorio_id, tipo_descuento)."""
    with get_db() as session:
        labs_cache = {l.nombre: l.id for l in session.query(
            Laboratorio.id, Laboratorio.nombre).all()}
        n = 0
        for it in items:
            lab_id = it.get('laboratorio_id')
            if not lab_id and it.get('laboratorio_nombre'):
                lab_id = labs_cache.get(it['laboratorio_nombre'])
            ean    = (it.get('ean') or '').strip()
            tipo_d = (it.get('tipo_descuento') or 'simple').strip()
            if not ean:
                continue
            existing = session.query(OfertaMinimo).filter_by(
                ean=ean, laboratorio_id=lab_id, tipo_descuento=tipo_d).first()
            obj = existing or OfertaMinimo(ean=ean, laboratorio_id=lab_id, tipo_descuento=tipo_d)
            if not existing:
                session.add(obj)
            obj.descripcion     = it.get('descripcion')
            obj.codigo          = it.get('codigo')
            obj.unidades_minima = it.get('unidades_minima') or 1
            obj.descuento_psl   = it.get('descuento_psl')
            obj.rentabilidad    = it.get('rentabilidad')
            obj.plazo_pago      = it.get('plazo_pago')
            obj.grupo_id        = it.get('grupo_id')
            obj.activo          = True
            obj.actualizado_en  = now_ar()
            n += 1
        session.commit()
    return n
