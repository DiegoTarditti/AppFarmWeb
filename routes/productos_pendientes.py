"""Queue de productos sin match — revisión diferida de imports.

Concentra decisiones que en imports (ofertas, módulos, facturas) suelen quedar
en suspenso ("este item no matcheó nada / ningún candidato me convence"). En vez
de obligar al operador a decidir en caliente durante el wizard, mandamos a este
queue y se revisa cuando hay tiempo.

Endpoints:
- GET  /productos/pendientes-revision         → listado paginado con filtros
- POST /productos/pendientes-revision/<id>/crear-nuevo
- POST /productos/pendientes-revision/<id>/vincular  (body: producto_id)
- POST /productos/pendientes-revision/<id>/descartar
- GET  /api/productos/pendientes-revision/buscar-catalogo?q=  → autocomplete

Helper público (consumido por imports):
- enqueue_pendiente(session, descripcion, supplier_id=None, supplier_nombre=None,
                    archivo_origen=None, score_top=None, top_candidatos=None)
"""
import json
from datetime import datetime

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import desc, func, or_

from database import (
    Producto,
    ProductoPendienteRevision,
    get_db,
    now_ar,
)


def enqueue_pendiente(session, descripcion, supplier_id=None, supplier_nombre=None,
                      archivo_origen=None, score_top=None, top_candidatos=None,
                      oferta_data=None):
    """Encola un item al queue de revisión.

    Si ya existe una entrada PENDIENTE con la misma (descripcion_supplier,
    supplier_id, archivo_origen), suma 1 a `veces_aparecido` y actualiza
    score_top + top_candidatos + oferta_data. Caso contrario crea una fila nueva.

    No hace commit — el caller decide cuándo commitear (suele venir dentro
    de una transacción más grande del import).

    Args:
      session: sesión SQLAlchemy abierta.
      descripcion: texto crudo del archivo (ej. "DERMAGLOS cr x 200 grs").
      supplier_id: opcional, lab/proveedor de origen.
      supplier_nombre: opcional, denormalizado para mostrar en la lista.
      archivo_origen: 'ofertas_import' | 'modulos_import' | 'factura' | etc.
      score_top: float 0-1 del mejor match de la pasada bulk; None si 0 candidatos.
      top_candidatos: lista de dicts {producto_id, descripcion, score}; serializa
        a JSON.
      oferta_data: dict con la oferta original que disparó este queue. Al
        resolver el item (vincular/crear), se aplica al producto resuelto.
        Estructura: {laboratorio_id, drogueria_id, descuento_psl,
        unidades_minima, plazo_pago, rentabilidad, vigencia_hasta, observacion}.

    Returns:
      ProductoPendienteRevision instance (nuevo o actualizado).
    """
    desc_norm = (descripcion or '').strip()
    if not desc_norm:
        return None
    # Filtro anti-ruido: rechazar headers/labels que se cuelan del parser
    # (ej. el Excel Ciafarma tiene celdas "PRODUCTOS" como section header).
    desc_lower = desc_norm.lower()
    if desc_lower in ('productos', 'producto', 'descripcion', 'descripción',
                      'item', 'items', 'ítem', 'ean', 'codigo', 'código',
                      '---', '--', '-'):
        return None
    if len(desc_lower) < 4:
        return None  # tokens muy cortos, casi siempre ruido
    existente = (session.query(ProductoPendienteRevision)
                 .filter(ProductoPendienteRevision.descripcion_supplier == desc_norm,
                         ProductoPendienteRevision.supplier_id == supplier_id,
                         ProductoPendienteRevision.archivo_origen == archivo_origen,
                         ProductoPendienteRevision.estado == 'pendiente')
                 .first())
    if existente:
        existente.veces_aparecido = (existente.veces_aparecido or 1) + 1
        if score_top is not None:
            existente.score_top_candidato = score_top
        if top_candidatos is not None:
            existente.top_candidatos_json = json.dumps(top_candidatos, ensure_ascii=False)
        if oferta_data is not None:
            existente.oferta_data_json = json.dumps(oferta_data, ensure_ascii=False, default=str)
        return existente
    nuevo = ProductoPendienteRevision(
        descripcion_supplier=desc_norm,
        supplier_id=supplier_id,
        supplier_nombre=supplier_nombre,
        archivo_origen=archivo_origen,
        score_top_candidato=score_top,
        top_candidatos_json=json.dumps(top_candidatos, ensure_ascii=False) if top_candidatos else None,
        oferta_data_json=json.dumps(oferta_data, ensure_ascii=False, default=str) if oferta_data else None,
        estado='pendiente',
    )
    session.add(nuevo)
    return nuevo


def _aplicar_oferta_a_producto(session, item, producto, usuario):
    """Cierra el loop: si el item tenía `oferta_data_json` (oferta original
    del import que lo disparó), crea/upsertea la OfertaMinimo correspondiente
    sobre el producto resuelto.

    No bloquea el flujo — si la aplicación falla, loggea warning y sigue.
    Retorna mensaje descriptivo (o None si no había oferta_data).
    """
    if not item or not item.oferta_data_json:
        return None
    try:
        from datetime import datetime as _dt

        from database import OfertaMinimo
        od = json.loads(item.oferta_data_json)
    except (json.JSONDecodeError, TypeError, ImportError):
        return None
    if not isinstance(od, dict):
        return None

    lab_id = od.get('laboratorio_id') or producto.laboratorio_id
    drog_id = od.get('drogueria_id')
    if not lab_id:
        return 'oferta no aplicada (sin laboratorio_id)'

    # Vigencia
    vig = od.get('vigencia_hasta')
    vig_date = None
    if vig:
        try:
            vig_date = _dt.strptime(str(vig)[:10], '%Y-%m-%d').date()
        except (ValueError, TypeError):
            vig_date = None

    # Buscar oferta existente por (lab, drog, producto, EAN/desc) para no duplicar
    desc_supplier = item.descripcion_supplier
    ean = (producto.codigo_barra or '').strip() or None
    q = session.query(OfertaMinimo).filter(
        OfertaMinimo.laboratorio_id == lab_id,
        OfertaMinimo.activo == True,  # noqa: E712
    )
    if drog_id:
        q = q.filter(OfertaMinimo.drogueria_id == drog_id)
    if ean:
        existente = q.filter(OfertaMinimo.ean == ean).first()
    else:
        existente = q.filter(OfertaMinimo.descripcion == desc_supplier).first()

    if existente:
        # Actualizar (refresh)
        existente.descuento_psl = od.get('descuento_psl') or existente.descuento_psl
        existente.unidades_minima = od.get('unidades_minima') or existente.unidades_minima
        existente.plazo_pago = od.get('plazo_pago') or existente.plazo_pago
        existente.rentabilidad = od.get('rentabilidad') or existente.rentabilidad
        if vig_date:
            existente.vigencia_hasta = vig_date
        return f'oferta refrescada (id {existente.id})'

    # ean es NOT NULL en OfertaMinimo. Si producto no tiene EAN, usamos
    # codigo_alfabeta (con prefijo identificador) o desc_supplier truncada.
    ean_final = ean or (producto.codigo_alfabeta and f'ALFA:{producto.codigo_alfabeta}')
    if not ean_final:
        return 'oferta no aplicada (producto sin EAN ni alfabeta)'
    nueva = OfertaMinimo(
        ean=ean_final,
        descripcion=desc_supplier,
        laboratorio_id=lab_id,
        drogueria_id=drog_id,
        descuento_psl=od.get('descuento_psl'),
        unidades_minima=od.get('unidades_minima'),
        plazo_pago=od.get('plazo_pago'),
        rentabilidad=od.get('rentabilidad'),
        vigencia_hasta=vig_date,
        observacion=od.get('observacion') or f'Aplicada desde queue por {usuario}',
        activo=True,
        actualizado_en=now_ar(),
    )
    session.add(nueva)
    session.flush()
    return f'oferta creada (id {nueva.id})'


def init_app(app):

    @app.route('/productos/pendientes-revision')
    @login_required
    def productos_pendientes_revision():
        estado = (request.args.get('estado') or 'pendiente').strip()
        archivo = (request.args.get('archivo') or '').strip()
        q_busqueda = (request.args.get('q') or '').strip()

        with get_db() as session:
            q = session.query(ProductoPendienteRevision)
            if estado != 'todos':
                q = q.filter(ProductoPendienteRevision.estado == estado)
            if archivo:
                q = q.filter(ProductoPendienteRevision.archivo_origen == archivo)
            if q_busqueda:
                like = f'%{q_busqueda}%'
                q = q.filter(or_(
                    ProductoPendienteRevision.descripcion_supplier.ilike(like),
                    ProductoPendienteRevision.supplier_nombre.ilike(like),
                ))
            q = q.order_by(desc(ProductoPendienteRevision.veces_aparecido),
                           desc(ProductoPendienteRevision.fecha_creacion))
            items = q.limit(500).all()

            # Conteos por estado para badges
            counts = dict(session.query(
                ProductoPendienteRevision.estado,
                func.count(ProductoPendienteRevision.id),
            ).group_by(ProductoPendienteRevision.estado).all())
            archivos = [r[0] for r in session.query(
                ProductoPendienteRevision.archivo_origen
            ).distinct().filter(ProductoPendienteRevision.archivo_origen.isnot(None)).all()]

            # Hidratar top_candidatos_json (parsear) para mostrar inline.
            items_data = []
            for it in items:
                cands = []
                if it.top_candidatos_json:
                    try:
                        cands = json.loads(it.top_candidatos_json)
                    except (json.JSONDecodeError, TypeError):
                        cands = []
                items_data.append({
                    'id': it.id,
                    'descripcion_supplier': it.descripcion_supplier,
                    'supplier_id': it.supplier_id,
                    'supplier_nombre': it.supplier_nombre or '—',
                    'archivo_origen': it.archivo_origen or '—',
                    'fecha_creacion': it.fecha_creacion,
                    'veces_aparecido': it.veces_aparecido or 1,
                    'score_top_candidato': it.score_top_candidato,
                    'top_candidatos': cands,
                    'estado': it.estado,
                    'producto_creado_id': it.producto_creado_id,
                    'producto_vinculado_id': it.producto_vinculado_id,
                    'usuario_resuelve': it.usuario_resuelve,
                    'fecha_resolucion': it.fecha_resolucion,
                })

        return render_template('productos_pendientes_revision.html',
                               items=items_data,
                               counts=counts,
                               archivos=archivos,
                               filtro_estado=estado,
                               filtro_archivo=archivo,
                               filtro_q=q_busqueda)

    @app.route('/productos/pendientes-revision/bulk-vincular', methods=['POST'])
    @login_required
    def pendiente_bulk_vincular():
        """Acepta en bloque los items pendientes cuyo top candidato tiene
        score >= min_score. Para cada uno: vincula al top_candidato (preferencia
        local > observer), aplica la oferta al producto resuelto.

        Body form: min_score (float 0-1, default 1.0).
        Returns: redirect con flash msg "N items resueltos, M ofertas aplicadas".
        """
        from database import ObsCodigoBarras, ObsProducto
        try:
            min_score = float(request.form.get('min_score', '1.0'))
        except (ValueError, TypeError):
            min_score = 1.0
        min_score = max(0.0, min(1.0, min_score))

        usuario = getattr(current_user, 'username', None) or '?'
        resueltos = 0
        ofertas_aplicadas = 0
        errores = 0

        with get_db() as session:
            q = session.query(ProductoPendienteRevision).filter(
                ProductoPendienteRevision.estado == 'pendiente',
                ProductoPendienteRevision.score_top_candidato >= min_score,
                ProductoPendienteRevision.top_candidatos_json.isnot(None),
            )
            items = q.all()

            for it in items:
                try:
                    raw = json.loads(it.top_candidatos_json or '[]')
                    if not raw:
                        continue
                    top = raw[0]
                    pid = top.get('producto_id')
                    obs_id = top.get('observer_id')
                    prod = None
                    if pid:
                        prod = session.get(Producto, pid)
                    if not prod and obs_id:
                        # Reusar Producto local con bridge si existe
                        prod = (session.query(Producto)
                                .filter(Producto.observer_id == obs_id).first())
                        if not prod:
                            op = session.get(ObsProducto, obs_id)
                            if not op:
                                errores += 1
                                continue
                            cb = (session.query(ObsCodigoBarras)
                                  .filter(ObsCodigoBarras.producto_observer == obs_id,
                                          ObsCodigoBarras.fecha_baja.is_(None),
                                          ObsCodigoBarras.orden == 1).first())
                            codigo_barra = (cb.codigo_barras.strip() if cb and cb.codigo_barras
                                            else (op.codigo_alfabeta or '').strip() or None)
                            if not codigo_barra:
                                errores += 1
                                continue
                            existente = (session.query(Producto)
                                         .filter(Producto.codigo_barra == codigo_barra).first())
                            if existente:
                                if not existente.observer_id:
                                    existente.observer_id = obs_id
                                prod = existente
                            else:
                                prod = Producto(
                                    codigo_barra=codigo_barra,
                                    descripcion=op.descripcion or '',
                                    observer_id=obs_id,
                                    codigo_alfabeta=op.codigo_alfabeta or None,
                                    actualizado_en=now_ar(),
                                )
                                session.add(prod)
                                session.flush()
                    if not prod:
                        errores += 1
                        continue

                    it.estado = 'vinculado'
                    it.producto_vinculado_id = prod.id
                    it.usuario_resuelve = usuario
                    it.fecha_resolucion = now_ar()
                    session.flush()
                    oferta_msg = _aplicar_oferta_a_producto(session, it, prod, usuario)
                    if oferta_msg and 'creada' in oferta_msg:
                        ofertas_aplicadas += 1
                    elif oferta_msg and 'refrescada' in oferta_msg:
                        ofertas_aplicadas += 1
                    resueltos += 1
                except Exception as e:
                    errores += 1
                    import logging
                    logging.getLogger(__name__).warning(
                        'bulk vincular item %s falló: %s', it.id, e)
            session.commit()

        msg = f'✓ {resueltos} items vinculados · {ofertas_aplicadas} ofertas aplicadas'
        if errores:
            msg += f' · {errores} errores (sin EAN ni alfabeta)'
        flash(msg, 'success' if errores == 0 else 'warning')
        return redirect(url_for('productos_pendientes_revision'))

    @app.route('/productos/pendientes-revision/<int:item_id>/crear-nuevo', methods=['POST'])
    @login_required
    def pendiente_crear_nuevo(item_id):
        descripcion = (request.form.get('descripcion') or '').strip()
        codigo_barra = (request.form.get('codigo_barra') or '').strip()
        precio_pvp = request.form.get('precio_pvp', type=float)
        oferta_msg = None
        if not descripcion:
            flash('La descripción no puede estar vacía.', 'error')
            return redirect(url_for('productos_pendientes_revision'))
        if not codigo_barra:
            flash('Falta código de barras (EAN). El catálogo lo requiere — '
                  'si no lo tenés, usá "Descartar" o pedí el EAN al supplier.', 'error')
            return redirect(url_for('productos_pendientes_revision'))

        with get_db() as session:
            it = session.get(ProductoPendienteRevision, item_id)
            if not it:
                flash('Item no encontrado.', 'error')
                return redirect(url_for('productos_pendientes_revision'))
            if it.estado != 'pendiente':
                flash(f'Item ya estaba en estado "{it.estado}".', 'warning')
                return redirect(url_for('productos_pendientes_revision'))

            # Si ya existe un producto con ese EAN → vincular en vez de duplicar.
            existente = (session.query(Producto)
                         .filter(Producto.codigo_barra == codigo_barra).first())
            if existente:
                it.estado = 'vinculado'
                it.producto_vinculado_id = existente.id
                msg_action = f'vinculado a producto existente "{existente.descripcion}" (EAN ya estaba en catálogo)'
            else:
                nuevo = Producto(
                    codigo_barra=codigo_barra,
                    descripcion=descripcion,
                    precio_pvp=precio_pvp,
                    actualizado_en=now_ar(),
                )
                session.add(nuevo)
                session.flush()  # para obtener nuevo.id
                it.estado = 'agregado'
                it.producto_creado_id = nuevo.id
                msg_action = f'creado nuevo producto "{descripcion}"'

            it.usuario_resuelve = getattr(current_user, 'username', None)
            it.fecha_resolucion = now_ar()
            session.flush()  # asegurar producto creado tiene id si era nuevo
            # Cierre del loop: aplicar la oferta original (si la había) sobre
            # el producto resuelto. La oferta queda como OfertaMinimo activa.
            target_prod = nuevo if not existente else existente
            oferta_msg = _aplicar_oferta_a_producto(
                session, it, target_prod, getattr(current_user, 'username', '?'))
            session.commit()

        full_msg = f'Item: {msg_action}.'
        if oferta_msg:
            full_msg += f' {oferta_msg}.'
        flash(full_msg, 'success')
        return redirect(url_for('productos_pendientes_revision'))

    @app.route('/productos/pendientes-revision/<int:item_id>/vincular', methods=['POST'])
    @login_required
    def pendiente_vincular(item_id):
        """Vincula a producto existente. Acepta dos orígenes:

        - origen='local' + id_local=<Producto.id>
        - origen='observer' + observer_id=<ObsProducto.observer_id>
          → si no hay Producto local con ese observer_id, lo creamos usando
          el primer EAN de obs_codigos_barras (orden=1) como codigo_barra.
          Si no hay EAN, se usa codigo_alfabeta como pseudo-EAN; si tampoco,
          se rechaza con mensaje.
        """
        from database import ObsCodigoBarras, ObsProducto
        origen = (request.form.get('origen') or 'local').strip()
        id_local = request.form.get('id_local', type=int)
        observer_id = request.form.get('observer_id', type=int)
        # Backwards compat: producto_id sin origen → tratar como local.
        if not id_local and not observer_id:
            id_local = request.form.get('producto_id', type=int)
            origen = 'local'

        oferta_msg = None
        with get_db() as session:
            it = session.get(ProductoPendienteRevision, item_id)
            if not it or it.estado != 'pendiente':
                flash('Item no encontrado o ya resuelto.', 'error')
                return redirect(url_for('productos_pendientes_revision'))

            prod = None
            if origen == 'local' and id_local:
                prod = session.get(Producto, id_local)
                if not prod:
                    flash('Producto local destino no existe.', 'error')
                    return redirect(url_for('productos_pendientes_revision'))
            elif origen == 'observer' and observer_id:
                # Reuso si ya existe Producto con ese observer_id.
                prod = (session.query(Producto)
                        .filter(Producto.observer_id == observer_id).first())
                if not prod:
                    op = session.get(ObsProducto, observer_id)
                    if not op:
                        flash('ObsProducto destino no existe.', 'error')
                        return redirect(url_for('productos_pendientes_revision'))
                    # EAN principal de obs_codigos_barras (orden 1)
                    cb = (session.query(ObsCodigoBarras)
                          .filter(ObsCodigoBarras.producto_observer == observer_id,
                                  ObsCodigoBarras.fecha_baja.is_(None),
                                  ObsCodigoBarras.orden == 1).first())
                    codigo_barra = (cb.codigo_barras.strip() if cb and cb.codigo_barras
                                    else (op.codigo_alfabeta or '').strip() or None)
                    if not codigo_barra:
                        flash('El producto Observer no tiene EAN ni código alfabeta — '
                              'usá "Crear nuevo" con un EAN manual.', 'error')
                        return redirect(url_for('productos_pendientes_revision'))
                    # Si ya existe un Producto con ese EAN (sin observer_id),
                    # adoptarlo y bridgar.
                    existente = (session.query(Producto)
                                 .filter(Producto.codigo_barra == codigo_barra).first())
                    if existente:
                        if not existente.observer_id:
                            existente.observer_id = observer_id
                        prod = existente
                    else:
                        prod = Producto(
                            codigo_barra=codigo_barra,
                            descripcion=op.descripcion or '',
                            observer_id=observer_id,
                            codigo_alfabeta=op.codigo_alfabeta or None,
                            actualizado_en=now_ar(),
                        )
                        session.add(prod)
                        session.flush()
            else:
                flash('Falta target_id u observer_id.', 'error')
                return redirect(url_for('productos_pendientes_revision'))

            it.estado = 'vinculado'
            it.producto_vinculado_id = prod.id
            it.usuario_resuelve = getattr(current_user, 'username', None)
            it.fecha_resolucion = now_ar()
            session.flush()
            # Cierre del loop: aplicar oferta original sobre el producto vinculado.
            oferta_msg = _aplicar_oferta_a_producto(
                session, it, prod, getattr(current_user, 'username', '?'))
            session.commit()
            target_desc = prod.descripcion

        full_msg = f'Item vinculado a "{target_desc}".'
        if oferta_msg:
            full_msg += f' {oferta_msg}.'
        flash(full_msg, 'success')
        return redirect(url_for('productos_pendientes_revision'))

    @app.route('/productos/pendientes-revision/<int:item_id>/descartar', methods=['POST'])
    @login_required
    def pendiente_descartar(item_id):
        with get_db() as session:
            it = session.get(ProductoPendienteRevision, item_id)
            if not it or it.estado != 'pendiente':
                flash('Item no encontrado o ya resuelto.', 'error')
                return redirect(url_for('productos_pendientes_revision'))
            it.estado = 'descartado'
            it.usuario_resuelve = getattr(current_user, 'username', None)
            it.fecha_resolucion = now_ar()
            session.commit()
        flash('Item descartado.', 'success')
        return redirect(url_for('productos_pendientes_revision'))

    @app.route('/api/productos/pendientes-revision/buscar-catalogo')
    @login_required
    def api_buscar_catalogo_pend():
        """Autocomplete sobre catálogo unificado (Producto local + ObsProducto).

        Devuelve hasta 20 resultados. Cada uno trae:
          - origen: 'local' (Producto.id) o 'observer' (ObsProducto.observer_id)
          - id_local: PK Producto si existe; None para Observer-only.
          - observer_id: PK ObsProducto si origen=='observer'.
          - descripcion, codigo_barra, codigo_alfabeta.
        Los Producto locales priorizan (van primero); luego ObsProducto.
        """
        from database import ObsProducto
        q = (request.args.get('q') or '').strip()
        if len(q) < 2:
            return jsonify({'ok': True, 'productos': []})
        like = f'%{q}%'
        out = []
        alfa_seen = set()        # codigo_alfabeta ya en `out` (Producto local)
        ean_seen = set()         # codigo_barra ya en `out` (Producto local)
        obs_id_seen = set()      # observer_id ya en `out` (Producto local con bridge)
        with get_db() as session:
            # 1) Productos locales primero
            for p in (session.query(Producto)
                      .filter(Producto.descripcion.ilike(like))
                      .order_by(Producto.descripcion).limit(20).all()):
                out.append({
                    'origen': 'local',
                    'id_local': p.id,
                    'observer_id': p.observer_id,
                    'descripcion': p.descripcion or '',
                    'codigo_barra': p.codigo_barra or '',
                    'codigo_alfabeta': p.codigo_alfabeta or '',
                })
                if p.codigo_alfabeta:
                    alfa_seen.add(p.codigo_alfabeta)
                if p.codigo_barra:
                    ean_seen.add(p.codigo_barra)
                if p.observer_id:
                    obs_id_seen.add(p.observer_id)
            # 2) Si quedó cupo, sumar ObsProducto (catálogo grande)
            #    Skipear los que ya están representados por un Producto local
            #    (mismo alfabeta, mismo observer_id, o mismo EAN).
            cupo = 20 - len(out)
            if cupo > 0:
                obs_q = (session.query(ObsProducto)
                         .filter(ObsProducto.descripcion.ilike(like),
                                 ObsProducto.fecha_baja.is_(None))
                         .order_by(ObsProducto.descripcion))
                # Pedimos un poco más por las prob de skip; max razonable.
                for op in obs_q.limit(cupo + 50).all():
                    if op.observer_id in obs_id_seen:
                        continue
                    if op.codigo_alfabeta and op.codigo_alfabeta in alfa_seen:
                        continue
                    out.append({
                        'origen': 'observer',
                        'id_local': None,
                        'observer_id': op.observer_id,
                        'descripcion': op.descripcion or '',
                        'codigo_barra': '',
                        'codigo_alfabeta': op.codigo_alfabeta or '',
                    })
                    if op.codigo_alfabeta:
                        alfa_seen.add(op.codigo_alfabeta)
                    if len(out) >= 20:
                        break
        return jsonify({'ok': True, 'productos': out})
