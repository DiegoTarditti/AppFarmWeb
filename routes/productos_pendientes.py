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
import os
from datetime import datetime  # noqa: F401  # usado por now_ar fallback en handlers

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


def _filtrar_items_por_contexto(items, contexto_token):
    """Filtra ProductoPendienteRevision por contexto = (drogueria, fecha_dia).

    Token format: 'DROG_ID-YYYY-MM-DD' (DROG_ID=0 si drogueria es null).
    Si contexto_token es vacío/None → devuelve items sin filtrar.

    Iteración en Python porque oferta_data_json es Text — castear a json en
    PostgreSQL es feo y para <2000 items no aporta.
    """
    if not contexto_token:
        return list(items)
    out = []
    for it in items:
        if not it.oferta_data_json:
            continue
        try:
            od = json.loads(it.oferta_data_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(od, dict):
            continue
        drog_id = od.get('drogueria_id') or 0
        fecha_dia = (it.fecha_creacion.strftime('%Y-%m-%d')
                     if it.fecha_creacion else 'sin-fecha')
        if f'{drog_id}-{fecha_dia}' == contexto_token:
            out.append(it)
    return out


def _guardar_equiv_desde_queue(session, item, producto):
    """Hook post-vinculación: guarda equivalencia para que el matcher la use
    en próximos imports. Lee lab/drog del oferta_data del item.

    Idempotente — si la equivalencia ya existe, refresca.
    No bloquea: si falla, loggea warning.
    """
    from producto_matcher import guardar_equivalencia
    if not item or not producto:
        return
    lab_id = None
    drog_id = None
    codigo_supplier = None
    if item.oferta_data_json:
        try:
            od = json.loads(item.oferta_data_json) or {}
            lab_id = od.get('laboratorio_id')
            drog_id = od.get('drogueria_id')
            codigo_supplier = od.get('codigo_supplier')
        except (json.JSONDecodeError, TypeError):
            pass
    if not lab_id and not drog_id:
        return
    try:
        guardar_equivalencia(
            session,
            producto_id=producto.id,
            descripcion=item.descripcion_supplier,
            codigo_supplier=codigo_supplier,
            laboratorio_id=lab_id,
            drogueria_id=drog_id,
        )
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            'guardar_equivalencia falló item %s: %s', item.id, e)


def _resolver_laboratorio_id(session, producto):
    """Resuelve el laboratorio_id de un Producto, con fallback vía ObServer.

    Cascada:
    1. Si `producto.laboratorio_id` ya está → devolverlo.
    2. Si `producto.observer_id` → buscar ObsProducto, sacar `laboratorio_observer`,
       mapear a `Laboratorio` (FK `observer_id`). Si encuentra, BACKFILLEA
       `producto.laboratorio_id` (mejora el producto para futuros usos).
    3. Si no se puede resolver, None.

    Se usa para cerrar el loop de queue → oferta cuando el producto vinculado
    no tenía lab seteado (típico cuando se creó desde ObsProducto sin mapear).
    """
    if producto.laboratorio_id:
        return producto.laboratorio_id
    obs_id = getattr(producto, 'observer_id', None)
    if not obs_id:
        return None
    from database import Laboratorio, ObsProducto
    op = session.get(ObsProducto, obs_id)
    if not op or not op.laboratorio_observer:
        return None
    lab = (session.query(Laboratorio)
           .filter(Laboratorio.observer_id == op.laboratorio_observer)
           .first())
    if not lab:
        return None
    # Backfill: el producto se enriquece para que la próxima oferta encuentre
    # el lab sin tener que volver a hacer el lookup ObServer.
    producto.laboratorio_id = lab.id
    return lab.id


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

    # OfertaMinimo permite (lab, drog) con cualquiera de los dos NULL:
    # - lab=X, drog=NULL → oferta directa del laboratorio.
    # - lab=X, drog=Y → oferta de un lab vía una droguería específica.
    # - lab=NULL, drog=Y → oferta "vía droguería" (lo más común en ofertas
    #   de catálogo de drogueria como Ciafarma, donde cada producto puede
    #   ser de un lab distinto y el descuento aplica al global).
    # Si oferta_data no trae lab pero sí drog → respetar (vía drogueria).
    # Solo si NO hay ni lab ni drog → skip (no podemos clasificar la oferta).
    lab_id = od.get('laboratorio_id')
    drog_id = od.get('drogueria_id')
    if not lab_id and not drog_id:
        return 'oferta no aplicada (sin laboratorio_id ni drogueria_id)'

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
        from database import Provider
        estado = (request.args.get('estado') or 'pendiente').strip()
        archivo = (request.args.get('archivo') or '').strip()
        q_busqueda = (request.args.get('q') or '').strip()
        contexto_token = (request.args.get('contexto') or '').strip()

        with get_db() as session:
            # ── Computar contextos (oferta_data) sobre todos los PENDIENTES ──
            # Cada contexto = (drogueria_id, fecha_creacion::día) extraído del
            # snapshot oferta_data_json. La filosofía: cada tanda del queue
            # corresponde a UNA oferta de UNA droguería en UNA fecha. El user
            # elige el contexto y todos los acciones caen ahí.
            pend_items_raw = (session.query(ProductoPendienteRevision)
                              .filter(ProductoPendienteRevision.estado == 'pendiente',
                                      ProductoPendienteRevision.oferta_data_json.isnot(None))
                              .all())
            contextos_map = {}
            for it in pend_items_raw:
                try:
                    od = json.loads(it.oferta_data_json)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(od, dict):
                    continue
                drog_id = od.get('drogueria_id')
                fecha_dia = (it.fecha_creacion.strftime('%Y-%m-%d')
                             if it.fecha_creacion else 'sin-fecha')
                key = f'{drog_id or 0}-{fecha_dia}'
                if key not in contextos_map:
                    contextos_map[key] = {
                        'token': key,
                        'drogueria_id': drog_id,
                        'drogueria_nombre': None,
                        'fecha_dia': fecha_dia,
                        'count': 0,
                        'count_sin_analizar': 0,
                        'archivo_origen': it.archivo_origen,
                        'sample_od': od,
                    }
                contextos_map[key]['count'] += 1
                if it.llm_analizado_en is None:
                    contextos_map[key]['count_sin_analizar'] += 1
            # Resolver razon_social de drogueria en bloque
            drog_ids = {c['drogueria_id'] for c in contextos_map.values() if c['drogueria_id']}
            if drog_ids:
                provs = (session.query(Provider)
                         .filter(Provider.id.in_(drog_ids))
                         .all())
                prov_map = {p.id: p.razon_social for p in provs}
                for c in contextos_map.values():
                    if c['drogueria_id']:
                        c['drogueria_nombre'] = prov_map.get(c['drogueria_id'], '?')
            contextos_list = sorted(contextos_map.values(),
                                    key=lambda c: (-c['count'], c['fecha_dia']))
            contexto_activo = next((c for c in contextos_list
                                    if c['token'] == contexto_token), None)

            # Lista de drogueria/proveedores activos para el form de
            # "asignar contexto" (backfill de items legacy sin drog_id).
            droguerias_disponibles = (session.query(Provider.id, Provider.razon_social)
                                      .order_by(Provider.razon_social)
                                      .all())

            # ── Query principal con filtros ──
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
            # Si hay contexto activo, traemos hasta 2000 para luego filtrar
            # en Python (SQL sobre Text JSON es caro). Caso típico < 500 items.
            items = q.limit(2000 if contexto_activo else 500).all()

            # Filtrar por contexto activo en Python (matchea drogueria+día).
            if contexto_activo:
                target_drog = contexto_activo['drogueria_id']
                target_dia = contexto_activo['fecha_dia']
                filtered = []
                for it in items:
                    if not it.oferta_data_json:
                        continue
                    try:
                        od = json.loads(it.oferta_data_json)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not isinstance(od, dict):
                        continue
                    it_drog = od.get('drogueria_id')
                    it_dia = (it.fecha_creacion.strftime('%Y-%m-%d')
                              if it.fecha_creacion else 'sin-fecha')
                    if it_drog == target_drog and it_dia == target_dia:
                        filtered.append(it)
                items = filtered[:500]

            # Conteos por estado para badges
            counts = dict(session.query(
                ProductoPendienteRevision.estado,
                func.count(ProductoPendienteRevision.id),
            ).group_by(ProductoPendienteRevision.estado).all())
            archivos = [r[0] for r in session.query(
                ProductoPendienteRevision.archivo_origen
            ).distinct().filter(ProductoPendienteRevision.archivo_origen.isnot(None)).all()]
            # Conteo de items pendientes que aún no fueron analizados por IA.
            # Si hay contexto activo, scopear el count a ese contexto.
            if contexto_activo:
                llm_sin_analizar = contexto_activo['count_sin_analizar']
            else:
                llm_sin_analizar = session.query(func.count(ProductoPendienteRevision.id)).filter(
                    ProductoPendienteRevision.estado == 'pendiente',
                    ProductoPendienteRevision.llm_analizado_en.is_(None),
                ).scalar() or 0

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
                    'llm_analizado_en': it.llm_analizado_en,
                    'llm_pick_producto_id': it.llm_pick_producto_id,
                    'llm_pick_observer_id': it.llm_pick_observer_id,
                    'llm_confidence': it.llm_confidence,
                    'llm_reasoning': it.llm_reasoning,
                    'llm_action': it.llm_action,
                    'llm_modelo_usado': it.llm_modelo_usado,
                })

        return render_template('productos_pendientes_revision.html',
                               items=items_data,
                               counts=counts,
                               archivos=archivos,
                               llm_sin_analizar=llm_sin_analizar,
                               llm_disponible=bool(os.environ.get('ANTHROPIC_API_KEY')),
                               filtro_estado=estado,
                               filtro_archivo=archivo,
                               filtro_q=q_busqueda,
                               contextos=contextos_list,
                               contexto_activo=contexto_activo,
                               droguerias_disponibles=droguerias_disponibles)

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
        contexto_token = (request.form.get('contexto') or '').strip()

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
            items = _filtrar_items_por_contexto(q.all(), contexto_token)

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
                    _guardar_equiv_desde_queue(session, it, prod)
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
            _guardar_equiv_desde_queue(session, it, target_prod)
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
            _guardar_equiv_desde_queue(session, it, prod)
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

    @app.route('/productos/pendientes-revision/estimar-costo-ia')
    @login_required
    def pendiente_estimar_costo_ia():
        """Estimación pre-batch del costo de analizar N items con LLM. No llama a la API."""
        from services.llm_matcher import estimar_costo_batch
        try:
            limit = int(request.args.get('limit', '50'))
        except (ValueError, TypeError):
            limit = 50
        contexto_token = (request.args.get('contexto') or '').strip()
        with get_db() as session:
            q = session.query(ProductoPendienteRevision).filter(
                ProductoPendienteRevision.estado == 'pendiente',
                ProductoPendienteRevision.llm_analizado_en.is_(None),
            )
            if contexto_token:
                # Filter en Python por contexto (oferta_data en Text JSON).
                raw = q.limit(2000).all()
                n_disponibles = len(_filtrar_items_por_contexto(raw, contexto_token))
            else:
                n_disponibles = q.count()
        n_a_procesar = min(limit, n_disponibles)
        est = estimar_costo_batch(n_a_procesar)
        return jsonify({
            'ok': True,
            'pendientes_sin_analizar': n_disponibles,
            'a_procesar': n_a_procesar,
            **est,
            'api_key_seteada': bool(os.environ.get('ANTHROPIC_API_KEY')),
        })

    @app.route('/productos/pendientes-revision/analizar-ia', methods=['POST'])
    @login_required
    def pendiente_analizar_ia():
        """Itera N pendientes sin analizar (`llm_analizado_en IS NULL`) y los manda
        al LLM. Persiste sugerencia + usage. Form fields:
          - limit (int, default 5): cuántos analizar en este batch.
          - forzar_reanalisis (bool, default false): re-analiza items ya analizados.
        """
        from services.llm_matcher import analizar_pendiente
        try:
            limit = int(request.form.get('limit', '5'))
        except (ValueError, TypeError):
            limit = 5
        limit = max(1, min(500, limit))
        forzar = (request.form.get('forzar_reanalisis') or '').lower() in ('1', 'true', 'on')

        if not os.environ.get('ANTHROPIC_API_KEY'):
            flash('ANTHROPIC_API_KEY no está seteada en el entorno. '
                  'Configurarla en Render → Environment.', 'error')
            return redirect(url_for('productos_pendientes_revision'))

        contexto_token = (request.form.get('contexto') or '').strip()
        analizados = 0
        sugieren_vincular = 0
        sugieren_descartar = 0
        sugieren_crear = 0
        ambiguos = 0
        errores = 0
        costo_total = 0.0

        with get_db() as session:
            q = session.query(ProductoPendienteRevision).filter(
                ProductoPendienteRevision.estado == 'pendiente',
            )
            if not forzar:
                q = q.filter(ProductoPendienteRevision.llm_analizado_en.is_(None))
            q = q.order_by(ProductoPendienteRevision.veces_aparecido.desc(),
                           ProductoPendienteRevision.fecha_creacion.desc())
            # Si hay contexto, traemos hasta 2000 y filtramos en Python.
            raw_items = q.limit(2000 if contexto_token else limit).all()
            items = _filtrar_items_por_contexto(raw_items, contexto_token)[:limit]

            for it in items:
                cands = []
                if it.top_candidatos_json:
                    try:
                        cands = json.loads(it.top_candidatos_json) or []
                    except (json.JSONDecodeError, TypeError):
                        cands = []
                resultado = analizar_pendiente(
                    it.descripcion_supplier, it.supplier_nombre, cands,
                )
                if not resultado.get('ok'):
                    errores += 1
                    import logging
                    logging.getLogger(__name__).warning(
                        'analizar_pendiente item %s: %s', it.id, resultado.get('error'))
                    # Abortar el batch ante errores fatales (no tiene sentido
                    # reintentar 230 veces el mismo error).
                    err = resultado.get('error', '')
                    err_lower = err.lower()
                    fatal = (
                        err.startswith(('auth:', 'connection:'))
                        or 'credit balance' in err_lower
                        or 'billing' in err_lower
                        or 'rate limit' in err_lower
                        or 'overloaded' in err_lower
                    )
                    if fatal:
                        flash(f'Error fatal: {err}. Batch abortado en item {it.id}.',
                              'error')
                        break
                    continue

                analizados += 1
                action = resultado['action']
                pick_idx = resultado.get('pick_idx')
                # Resolver pick_idx → producto_id / observer_id (1-based → cands[idx-1])
                pick_prod_id = None
                pick_obs_id = None
                if pick_idx and 1 <= pick_idx <= len(cands):
                    cand = cands[pick_idx - 1]
                    pick_prod_id = cand.get('producto_id')
                    pick_obs_id = cand.get('observer_id')

                it.llm_analizado_en = now_ar()
                it.llm_pick_producto_id = pick_prod_id
                it.llm_pick_observer_id = pick_obs_id
                it.llm_confidence = resultado['confidence']
                it.llm_reasoning = resultado['reasoning']
                it.llm_action = action
                it.llm_modelo_usado = resultado.get('modelo')
                costo_total += resultado.get('costo_usd', 0.0) or 0.0

                if action == 'vincular':
                    sugieren_vincular += 1
                elif action == 'descartar':
                    sugieren_descartar += 1
                elif action == 'crear_nuevo':
                    sugieren_crear += 1
                else:  # ambiguo
                    ambiguos += 1
            session.commit()

        msg = (f'🤖 IA: {analizados} analizados · '
               f'{sugieren_vincular} sugieren vincular · '
               f'{sugieren_descartar} descartar · '
               f'{sugieren_crear} crear nuevo · '
               f'{ambiguos} ambiguos · '
               f'costo ~${costo_total:.4f} USD')
        if errores:
            msg += f' · {errores} errores'
        flash(msg, 'success' if errores == 0 else 'warning')
        return redirect(url_for('productos_pendientes_revision'))

    @app.route('/productos/pendientes-revision/<int:item_id>/aplicar-ia', methods=['POST'])
    @login_required
    def pendiente_aplicar_ia(item_id):
        """Aplica la sugerencia del LLM. Solo automatiza action='vincular' o 'descartar'.
        Para 'crear_nuevo' redirige al modal manual (necesita EAN del operador).
        Para 'ambiguo' rechaza con flash explicativo.
        """
        from database import ObsCodigoBarras, ObsProducto
        usuario = getattr(current_user, 'username', None) or '?'
        oferta_msg = None
        with get_db() as session:
            it = session.get(ProductoPendienteRevision, item_id)
            if not it or it.estado != 'pendiente':
                flash('Item no encontrado o ya resuelto.', 'error')
                return redirect(url_for('productos_pendientes_revision'))
            if not it.llm_analizado_en or not it.llm_action:
                flash('Item no fue analizado por IA todavía.', 'warning')
                return redirect(url_for('productos_pendientes_revision'))

            action = it.llm_action
            if action == 'descartar':
                it.estado = 'descartado'
                it.usuario_resuelve = f'IA→{usuario}'
                it.fecha_resolucion = now_ar()
                session.commit()
                flash(f'Item descartado por sugerencia IA ({it.llm_confidence:.0%}): '
                      f'{it.llm_reasoning}', 'success')
                return redirect(url_for('productos_pendientes_revision'))

            if action == 'crear_nuevo':
                flash('La IA sugiere crear un producto nuevo. Hacé click en '
                      '"+ Crear nuevo" para completar el EAN manualmente.', 'info')
                return redirect(url_for('productos_pendientes_revision'))

            if action == 'ambiguo':
                flash(f'La IA marcó este item como ambiguo (conf {it.llm_confidence:.0%}). '
                      f'Resolvé manualmente. Razón: {it.llm_reasoning}', 'warning')
                return redirect(url_for('productos_pendientes_revision'))

            # action == 'vincular' — necesitamos al menos un pick FK
            prod = None
            if it.llm_pick_producto_id:
                prod = session.get(Producto, it.llm_pick_producto_id)
            if not prod and it.llm_pick_observer_id:
                obs_id = it.llm_pick_observer_id
                prod = session.query(Producto).filter(Producto.observer_id == obs_id).first()
                if not prod:
                    op = session.get(ObsProducto, obs_id)
                    if op:
                        cb = (session.query(ObsCodigoBarras)
                              .filter(ObsCodigoBarras.producto_observer == obs_id,
                                      ObsCodigoBarras.fecha_baja.is_(None),
                                      ObsCodigoBarras.orden == 1).first())
                        codigo_barra = (cb.codigo_barras.strip() if cb and cb.codigo_barras
                                        else (op.codigo_alfabeta or '').strip() or None)
                        if codigo_barra:
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
                flash('La sugerencia IA apunta a un producto sin EAN ni alfabeta. '
                      'Resolvé manualmente.', 'warning')
                return redirect(url_for('productos_pendientes_revision'))

            it.estado = 'vinculado'
            it.producto_vinculado_id = prod.id
            it.usuario_resuelve = f'IA→{usuario}'
            it.fecha_resolucion = now_ar()
            session.flush()
            oferta_msg = _aplicar_oferta_a_producto(session, it, prod, usuario)
            _guardar_equiv_desde_queue(session, it, prod)
            session.commit()
            target_desc = prod.descripcion

        full_msg = (f'🤖 Vinculado a "{target_desc}" por sugerencia IA '
                    f'({it.llm_confidence:.0%}).')
        if oferta_msg:
            full_msg += f' {oferta_msg}.'
        flash(full_msg, 'success')
        return redirect(url_for('productos_pendientes_revision'))

    @app.route('/productos/pendientes-revision/aplicar-ia-bulk', methods=['POST'])
    @login_required
    def pendiente_aplicar_ia_bulk():
        """Aplica todas las sugerencias IA con confidence >= min_conf. Soporta
        action='vincular' y action='descartar'. Form: min_conf (float, default 0.9).
        """
        from database import ObsCodigoBarras, ObsProducto
        try:
            min_conf = float(request.form.get('min_conf', '0.9'))
        except (ValueError, TypeError):
            min_conf = 0.9
        min_conf = max(0.0, min(1.0, min_conf))
        contexto_token = (request.form.get('contexto') or '').strip()

        usuario = getattr(current_user, 'username', None) or '?'
        vinculados = 0
        descartados = 0
        ofertas_aplicadas = 0
        errores = 0

        with get_db() as session:
            q = session.query(ProductoPendienteRevision).filter(
                ProductoPendienteRevision.estado == 'pendiente',
                ProductoPendienteRevision.llm_analizado_en.isnot(None),
                ProductoPendienteRevision.llm_confidence >= min_conf,
                ProductoPendienteRevision.llm_action.in_(['vincular', 'descartar']),
            )
            items = _filtrar_items_por_contexto(q.all(), contexto_token)

            for it in items:
                try:
                    if it.llm_action == 'descartar':
                        it.estado = 'descartado'
                        it.usuario_resuelve = f'IA→{usuario}'
                        it.fecha_resolucion = now_ar()
                        descartados += 1
                        continue

                    # vincular
                    prod = None
                    if it.llm_pick_producto_id:
                        prod = session.get(Producto, it.llm_pick_producto_id)
                    if not prod and it.llm_pick_observer_id:
                        obs_id = it.llm_pick_observer_id
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
                    it.usuario_resuelve = f'IA→{usuario}'
                    it.fecha_resolucion = now_ar()
                    session.flush()
                    oferta_msg = _aplicar_oferta_a_producto(session, it, prod, usuario)
                    _guardar_equiv_desde_queue(session, it, prod)
                    if oferta_msg and ('creada' in oferta_msg or 'refrescada' in oferta_msg):
                        ofertas_aplicadas += 1
                    vinculados += 1
                except Exception as e:  # noqa: BLE001
                    errores += 1
                    import logging
                    logging.getLogger(__name__).warning(
                        'aplicar-ia-bulk item %s: %s', it.id, e)
            session.commit()

        msg = (f'🤖 Bulk IA (conf ≥ {min_conf:.0%}): '
               f'{vinculados} vinculados · {descartados} descartados · '
               f'{ofertas_aplicadas} ofertas aplicadas')
        if errores:
            msg += f' · {errores} errores'
        flash(msg, 'success' if errores == 0 else 'warning')
        return redirect(url_for('productos_pendientes_revision'))

    @app.route('/productos/pendientes-revision/borrar-seleccionados', methods=['POST'])
    @login_required
    def pendiente_borrar_seleccionados():
        """Borra PERMANENTEMENTE los items seleccionados. Acepta lista de
        IDs vía form field repetido 'item_ids'. Restablece a queue solo si
        el mismo item vuelve a aparecer en futuros imports (idempotente: si
        viene una vez al mes, se vuelve a encolar; si nunca más, queda
        descartado).
        """
        raw_ids = request.form.getlist('item_ids')
        ids = []
        for x in raw_ids:
            try:
                ids.append(int(x))
            except (TypeError, ValueError):
                pass
        if not ids:
            flash('No se recibieron items para borrar.', 'warning')
            return redirect(url_for('productos_pendientes_revision'))

        with get_db() as session:
            deleted = (session.query(ProductoPendienteRevision)
                       .filter(ProductoPendienteRevision.id.in_(ids))
                       .delete(synchronize_session=False))
            session.commit()
        flash(f'🗑 {deleted} items borrados permanentemente.', 'success')
        # Preservar el contexto en el redirect si venía en form.
        ctx = (request.form.get('contexto') or '').strip()
        if ctx:
            return redirect(url_for('productos_pendientes_revision', contexto=ctx))
        return redirect(url_for('productos_pendientes_revision'))

    @app.route('/productos/pendientes-revision/asignar-contexto', methods=['POST'])
    @login_required
    def pendiente_asignar_contexto():
        """Backfill de `drogueria_id` (y opcionalmente `laboratorio_id`) en el
        oferta_data_json de items pendientes que no lo tenían. Usado para
        agrupar items legacy bajo un contexto de droguería.

        Form fields:
          - contexto_actual (str, opcional): token de contexto a actualizar
            (típicamente 'Sin droguería · YYYY-MM-DD' → '0-YYYY-MM-DD').
          - drogueria_id (int): nuevo drog_id a asignar.
          - laboratorio_id (int, opcional): nuevo lab_id si se quiere setear.
        """
        contexto_actual = (request.form.get('contexto_actual') or '').strip()
        try:
            new_drog_id = int(request.form.get('drogueria_id') or 0) or None
        except (ValueError, TypeError):
            new_drog_id = None
        try:
            new_lab_id = int(request.form.get('laboratorio_id') or 0) or None
        except (ValueError, TypeError):
            new_lab_id = None
        if not new_drog_id and not new_lab_id:
            flash('Indicar al menos drogueria_id o laboratorio_id.', 'error')
            return redirect(url_for('productos_pendientes_revision'))

        actualizados = 0
        with get_db() as session:
            q = session.query(ProductoPendienteRevision).filter(
                ProductoPendienteRevision.estado == 'pendiente',
                ProductoPendienteRevision.oferta_data_json.isnot(None),
            )
            raw_items = q.all()
            target_items = (_filtrar_items_por_contexto(raw_items, contexto_actual)
                            if contexto_actual else raw_items)
            for it in target_items:
                try:
                    od = json.loads(it.oferta_data_json) or {}
                except (json.JSONDecodeError, TypeError):
                    continue
                changed = False
                if new_drog_id and not od.get('drogueria_id'):
                    od['drogueria_id'] = new_drog_id
                    changed = True
                if new_lab_id and not od.get('laboratorio_id'):
                    od['laboratorio_id'] = new_lab_id
                    changed = True
                if changed:
                    it.oferta_data_json = json.dumps(od, ensure_ascii=False, default=str)
                    actualizados += 1
            session.commit()

        flash(f'✓ {actualizados} items actualizados con contexto '
              f'(drog={new_drog_id}, lab={new_lab_id}).', 'success')
        return redirect(url_for('productos_pendientes_revision'))

    @app.route('/productos/pendientes-revision/reaplicar-ofertas', methods=['POST'])
    @login_required
    def pendiente_reaplicar_ofertas():
        """Re-procesa la aplicación de oferta sobre items ya resueltos
        (vinculado/agregado) que tienen `oferta_data_json` guardado.

        Útil cuando antes la oferta no se aplicaba por falta de laboratorio_id
        (bug previo) o cuando se mejoró la lógica de _aplicar_oferta_a_producto.
        Es idempotente: si ya existe OfertaMinimo coincidente, la refresca.
        """
        usuario = getattr(current_user, 'username', None) or '?'
        creadas = 0
        refrescadas = 0
        sin_lab = 0
        otros_skip = 0

        with get_db() as session:
            items = (session.query(ProductoPendienteRevision)
                     .filter(ProductoPendienteRevision.estado.in_(['vinculado', 'agregado']),
                             ProductoPendienteRevision.oferta_data_json.isnot(None))
                     .all())
            for it in items:
                pid = it.producto_vinculado_id or it.producto_creado_id
                if not pid:
                    otros_skip += 1
                    continue
                prod = session.get(Producto, pid)
                if not prod:
                    otros_skip += 1
                    continue
                try:
                    msg = _aplicar_oferta_a_producto(session, it, prod, usuario)
                except Exception:  # noqa: BLE001
                    import logging
                    logging.getLogger(__name__).exception(
                        'reaplicar-ofertas item %s falló', it.id)
                    otros_skip += 1
                    continue
                if not msg:
                    otros_skip += 1
                elif 'creada' in msg:
                    creadas += 1
                elif 'refrescada' in msg:
                    refrescadas += 1
                elif 'sin laboratorio_id' in msg:
                    sin_lab += 1
                else:
                    otros_skip += 1
            session.commit()

        msg = (f'♻️ Re-aplicar ofertas: {creadas} creadas · '
               f'{refrescadas} refrescadas · {sin_lab} sin lab resoluble · '
               f'{otros_skip} skip')
        flash(msg, 'success' if (creadas + refrescadas) > 0 else 'warning')
        return redirect(url_for('productos_pendientes_revision'))
