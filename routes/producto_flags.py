"""CRUD de flags de comportamiento por producto (EAN) o laboratorio."""
import json
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import database
from database import (
    Laboratorio,
    Producto,
    ProductoAtributo,
    ProductoCodigoBarra,
    ProductoFlag,
    TipoPedidoConfig,
    get_db,
)


def _resolver_producto_por_ean(session, ean):
    """Producto master por cualquier EAN: principal, alt1/2/3 o tabla 1-a-N."""
    prod = session.query(Producto).filter_by(codigo_barra=ean).first()
    if prod:
        return prod
    prod = (session.query(Producto)
            .filter((Producto.codigo_barra_alt1 == ean)
                    | (Producto.codigo_barra_alt2 == ean)
                    | (Producto.codigo_barra_alt3 == ean)).first())
    if prod:
        return prod
    pcb = (session.query(ProductoCodigoBarra)
           .filter_by(codigo_barra=ean).first())
    return pcb.producto if pcb else None


def _flag_configs(session):
    """Devuelve dict slug → {nombre, icono, color, permite_reemplazo, permite_vigencia}."""
    tipos = (session.query(TipoPedidoConfig)
             .filter_by(categoria='flag', activo=True)
             .order_by(TipoPedidoConfig.slug).all())
    result = {}
    for t in tipos:
        cfg = {}
        try:
            cfg = json.loads(t.config_json or '{}')
        except (ValueError, TypeError):
            pass
        result[t.slug] = {
            'nombre':            t.nombre,
            'icono':             cfg.get('icono', '📝'),
            'color':             cfg.get('color', 'gray'),
            'efecto_armado':     cfg.get('efecto_armado', 'ninguno'),
            'permite_reemplazo': bool(cfg.get('permite_reemplazo')),
            'permite_vigencia':  bool(cfg.get('permite_vigencia')),
        }
    return result


def _row_to_dict(r, productos_map, flag_configs):
    cfg = flag_configs.get(r.flag_slug, {})
    return {
        'id':            r.id,
        'flag_slug':     r.flag_slug,
        'flag_nombre':   cfg.get('nombre', r.flag_slug),
        'flag_icono':    cfg.get('icono', '📝'),
        'flag_color':    cfg.get('color', 'gray'),
        'ean':           r.ean or '',
        'prod_nombre':   productos_map.get(r.ean, '') if r.ean else '',
        'laboratorio_id': r.laboratorio_id,
        'lab_nombre':    r.laboratorio.nombre if r.laboratorio else '',
        'nota':          r.nota or '',
        'ean_reemplazo': r.ean_reemplazo or '',
        'vigente_hasta': r.vigente_hasta.isoformat() if r.vigente_hasta else '',
        'creado_en':     r.creado_en.strftime('%d/%m/%Y') if r.creado_en else '',
        'creado_por':    r.creado_por or '',
    }


def init_app(app):

    @app.route('/productos/presentaciones')
    @login_required
    def productos_presentaciones():
        """Pantalla dedicada: arriba el buscador para configurar presentación
        (fraccionado + envase + equivalencia Kellerhoff); abajo la lista de los
        productos que ya tienen presentación configurada."""
        from routes.kellerhoff import corregir_eans, ean_export_de_producto, estado_equivalencia
        with get_db() as session:
            # "Presentación configurada" = fraccionado=True (se vende suelto, se
            # pide por envase). El cantidad_envase suelto NO alcanza: está
            # auto-parseado en ~60k productos en catalogación, y un envase editado
            # a mano sin marcar fraccionado tampoco es una presentación (confunde).
            q = (session.query(Producto, ProductoAtributo)
                 .outerjoin(ProductoAtributo, ProductoAtributo.producto_id == Producto.id)
                 .filter(Producto.fraccionado.is_(True))
                 .order_by(Producto.descripcion))
            prod_rows = q.limit(2000).all()

            # EAN que el export emite por producto + su equivalencia Kellerhoff.
            export_eans = {p.id: ean_export_de_producto(session, p)
                           for p, _ in prod_rows}
            corr = corregir_eans(session, list(export_eans.values()))

            # Laboratorio: master (Producto.laboratorio) o, si no, vía ObServer
            # (observer_id → ObsProducto.laboratorio_observer → ObsLaboratorio).
            from database import ObsLaboratorio, ObsProducto
            obs_ids = [p.observer_id for p, _ in prod_rows if p.observer_id]
            lab_by_oid = {}
            if obs_ids:
                for oid, labdesc in (session.query(
                        ObsProducto.observer_id, ObsLaboratorio.descripcion)
                        .outerjoin(ObsLaboratorio,
                                   ObsLaboratorio.observer_id == ObsProducto.laboratorio_observer)
                        .filter(ObsProducto.observer_id.in_(obs_ids))):
                    lab_by_oid[oid] = labdesc or ''

            filas = []
            for prod, atr in prod_rows:
                ce = atr.cantidad_envase if (atr and atr.cantidad_envase is not None) else None
                eean = export_eans[prod.id]
                est = estado_equivalencia(session, eean)
                estado = est.get('estado')
                if estado in ('directo', 'equivalencia'):
                    kel_desc = est.get('desc') or ''
                    kel_codigo = est.get('codigo') or ''
                    kel_ean_eq = corr.get(eean) or ''
                elif estado == 'no_disponible':
                    kel_desc, kel_codigo, kel_ean_eq = 'Kellerhoff no lo trae', '', ''
                else:  # sin_resolver / sin_catalogo
                    kel_desc, kel_codigo, kel_ean_eq = '', '', ''
                filas.append({
                    'ean': prod.codigo_barra,
                    'export_ean': eean,
                    'nombre': prod.descripcion or '',
                    'lab': (prod.laboratorio.nombre if prod.laboratorio
                            else lab_by_oid.get(prod.observer_id, '')),
                    'fraccionado': bool(prod.fraccionado),
                    'cantidad_envase': int(ce) if ce is not None else None,
                    'kel_estado': estado,
                    'kel_desc': kel_desc,
                    'kel_codigo': kel_codigo,
                    'kel_ean_eq': kel_ean_eq,
                })
        return render_template('productos_presentaciones.html', filas=filas)

    @app.route('/productos/flags')
    @login_required
    def producto_flags_list():
        filtro_slug = request.args.get('slug', '')
        with get_db() as session:
            cfgs = _flag_configs(session)

            q = session.query(ProductoFlag)
            if filtro_slug:
                q = q.filter(ProductoFlag.flag_slug == filtro_slug)
            rows = q.order_by(ProductoFlag.creado_en.desc()).all()

            eans = [r.ean for r in rows if r.ean]
            productos_map = {}
            if eans:
                # 1) Master local: codigo_barra principal (UNIQUE)
                prods = (session.query(Producto.codigo_barra, Producto.descripcion)
                         .filter(Producto.codigo_barra.in_(eans)).all())
                for cb, desc in prods:
                    productos_map[cb] = desc
                # 2) Fallback ObServer: EAN → ObsCodigoBarras → ObsProducto.descripcion.
                # Necesario para flags asignados a EANs del catalogo ObServer sin
                # contraparte en Producto master.
                from database import ObsCodigoBarras, ObsProducto
                pendientes = [e for e in eans if e not in productos_map]
                if pendientes:
                    obs_rows = (session.query(ObsCodigoBarras.codigo_barras,
                                              ObsProducto.descripcion)
                                .join(ObsProducto,
                                      ObsProducto.observer_id == ObsCodigoBarras.producto_observer)
                                .filter(ObsCodigoBarras.codigo_barras.in_(pendientes))
                                .filter(ObsCodigoBarras.fecha_baja.is_(None))
                                .all())
                    for cb, desc in obs_rows:
                        if cb not in productos_map:
                            productos_map[cb] = desc

            flags = [_row_to_dict(r, productos_map, cfgs) for r in rows]

            labs = (session.query(Laboratorio.id, Laboratorio.nombre)
                    .filter(Laboratorio.activo.is_(True))
                    .order_by(Laboratorio.nombre).all())

            # Productos marcados "no pedir" al armar un pedido (Producto.no_pedir).
            # No son flags de la tabla ProductoFlag: es una columna del master que
            # se setea desde compras/dia → "Pide 0 (no reponer)".
            np_rows = (session.query(Producto.id, Producto.codigo_barra,
                                     Producto.descripcion, Laboratorio.nombre)
                       .outerjoin(Laboratorio, Laboratorio.id == Producto.laboratorio_id)
                       .filter(Producto.no_pedir.is_(True))
                       .order_by(Producto.descripcion).all())
            no_pedir_items = [{'id': r[0], 'ean': r[1], 'nombre': r[2] or '',
                               'lab': r[3] or ''} for r in np_rows]

        return render_template('producto_flags.html',
                               flags=flags,
                               flag_configs=cfgs,
                               filtro_slug=filtro_slug,
                               labs=labs,
                               no_pedir_items=no_pedir_items)

    @app.route('/productos/flags/asignar', methods=['POST'])
    @login_required
    def producto_flags_asignar():
        flag_slug      = request.form.get('flag_slug', '').strip()
        ean            = request.form.get('ean', '').strip() or None
        lab_id_raw     = request.form.get('laboratorio_id', '').strip()
        nota           = request.form.get('nota', '').strip() or None
        ean_reemplazo  = request.form.get('ean_reemplazo', '').strip() or None
        vigente_raw    = request.form.get('vigente_hasta', '').strip()

        if not flag_slug or (not ean and not lab_id_raw):
            flash('Falta seleccionar el flag y al menos un EAN o laboratorio.', 'error')
            return redirect(url_for('producto_flags_list'))

        laboratorio_id = int(lab_id_raw) if lab_id_raw.isdigit() else None

        vigente_hasta = None
        if vigente_raw:
            try:
                vigente_hasta = date.fromisoformat(vigente_raw)
            except ValueError:
                pass

        creado_por = (getattr(current_user, 'email', None)
                      or str(getattr(current_user, 'id', '')))

        with get_db() as session:
            # Evitar duplicado exacto (mismo flag + mismo EAN)
            if ean:
                existing = (session.query(ProductoFlag)
                            .filter_by(flag_slug=flag_slug, ean=ean).first())
                if existing:
                    flash(f'El EAN {ean} ya tiene el flag {flag_slug}.', 'warning')
                    return redirect(url_for('producto_flags_list'))

            pf = ProductoFlag(
                flag_slug=flag_slug,
                ean=ean,
                laboratorio_id=laboratorio_id,
                nota=nota,
                ean_reemplazo=ean_reemplazo,
                vigente_hasta=vigente_hasta,
                creado_por=creado_por,
            )
            session.add(pf)
            session.commit()
            flash(f'Flag {flag_slug} asignado{" al EAN " + ean if ean else ""}.')
        return redirect(url_for('producto_flags_list'))

    @app.route('/productos/flags/<int:flag_id>/eliminar', methods=['POST'])
    @login_required
    def producto_flags_eliminar(flag_id):
        with get_db() as session:
            pf = session.get(ProductoFlag, flag_id)
            if pf:
                session.delete(pf)
                session.commit()
                flash('Flag eliminado.')
        return redirect(url_for('producto_flags_list'))

    @app.route('/api/producto-nombre')
    @login_required
    def api_producto_nombre():
        ean = request.args.get('ean', '').strip()
        if not ean:
            return jsonify({'nombre': None})
        with get_db() as session:
            prod = session.query(Producto).filter_by(codigo_barra=ean).first()
            return jsonify({'nombre': prod.descripcion if prod else None})

    @app.route('/api/producto/presentacion')
    @login_required
    def api_producto_presentacion():
        """Datos de presentación de un producto por EAN (para la tarjeta de
        config en /productos/flags): fraccionado + cantidad de envase. El envase
        sale de ProductoAtributo (editable) con fallback al de ObServer."""
        ean = request.args.get('ean', '').strip()
        if not ean:
            return jsonify({'ok': False, 'error': 'Falta EAN'}), 400
        with get_db() as session:
            prod = _resolver_producto_por_ean(session, ean)
            if not prod:
                return jsonify({'ok': False, 'existe_master': False,
                                'error': 'Producto sin ficha master local. '
                                         'Cataloga el producto primero.'})
            atr = session.get(ProductoAtributo, prod.id)
            cant = float(atr.cantidad_envase) if (atr and atr.cantidad_envase is not None) else None
            cant_obs = None
            if prod.observer_id:
                from database import ObsProducto
                obs = session.get(ObsProducto, prod.observer_id)
                if obs and obs.cantidad_envase is not None:
                    cant_obs = float(obs.cantidad_envase)
            # Estado de equivalencia Kellerhoff (sobre el EAN que el export emite).
            from routes.kellerhoff import ean_export_de_producto, estado_equivalencia
            kel_ean = ean_export_de_producto(session, prod)
            kel = estado_equivalencia(session, kel_ean)
            return jsonify({
                'ok': True,
                'existe_master': True,
                'ean': prod.codigo_barra,
                'descripcion': prod.descripcion or '',
                'lab': prod.laboratorio.nombre if prod.laboratorio else '',
                'fraccionado': bool(prod.fraccionado),
                'cantidad_envase': cant,
                'cantidad_envase_obs': cant_obs,
                'kellerhoff': kel,
            })

    @app.route('/api/producto/presentacion', methods=['POST'])
    @login_required
    def api_producto_presentacion_guardar():
        """Guarda fraccionado (Producto) + cantidad_envase (ProductoAtributo,
        fuente=manual). Body JSON: {ean, fraccionado: bool, cantidad_envase}."""
        body = request.get_json(silent=True) or {}
        ean = str(body.get('ean', '')).strip()
        if not ean:
            return jsonify({'ok': False, 'error': 'Falta EAN'}), 400
        with get_db() as session:
            prod = _resolver_producto_por_ean(session, ean)
            if not prod:
                return jsonify({'ok': False, 'error': 'Producto sin ficha master local.'}), 404

            prod.fraccionado = bool(body.get('fraccionado'))

            # cantidad_envase → ProductoAtributo (1-a-1). Vacío = no tocar / limpiar.
            raw = body.get('cantidad_envase')
            raw = str(raw).strip() if raw is not None else ''
            atr = session.get(ProductoAtributo, prod.id)
            if raw:
                try:
                    val = Decimal(raw.replace(',', '.'))
                except (InvalidOperation, ValueError):
                    return jsonify({'ok': False, 'error': 'Cantidad de envase inválida.'}), 400
                if atr is None:
                    atr = ProductoAtributo(producto_id=prod.id)
                    session.add(atr)
                atr.cantidad_envase = val
                atr.fuente = 'manual'
            elif atr is not None and atr.cantidad_envase is not None:
                atr.cantidad_envase = None
                atr.fuente = 'manual'

            session.commit()
            return jsonify({'ok': True, 'fraccionado': prod.fraccionado,
                            'cantidad_envase': float(atr.cantidad_envase)
                            if (atr and atr.cantidad_envase is not None) else None})

    @app.route('/api/producto/presentacion-bulk', methods=['POST'])
    @login_required
    def api_producto_presentacion_bulk():
        """Marca fraccionado + cantidad_envase en VARIOS productos a la vez.

        Body: {observer_ids: [...], fraccionado: bool, cantidad_envase: num|null}.
        Envase: si viene `cantidad_envase` → común para todos; si no → el de
        ObServer de cada producto. Materializa el master si no existe.
        """
        from database import ObsProducto
        from helpers import materializar_producto
        body = request.get_json(silent=True) or {}
        obs_ids = [int(x) for x in (body.get('observer_ids') or [])
                   if str(x).strip().lstrip('-').isdigit()]
        if not obs_ids:
            return jsonify({'ok': False, 'error': 'No seleccionaste productos.'}), 400
        fraccionado = bool(body.get('fraccionado', True))
        raw = body.get('cantidad_envase')
        envase_comun = None
        if raw not in (None, ''):
            try:
                envase_comun = Decimal(str(raw).replace('.', '').replace(',', '.'))
            except (InvalidOperation, ValueError):
                return jsonify({'ok': False, 'error': 'Envase común inválido.'}), 400

        aplicados = materializados = sin_envase = errores = 0
        with get_db() as session:
            for oid in obs_ids:
                ya_existia = (session.query(Producto)
                              .filter_by(observer_id=oid).first() is not None)
                prod, err = materializar_producto(session, oid)
                if not prod:
                    errores += 1
                    continue
                if not ya_existia:
                    materializados += 1
                prod.fraccionado = fraccionado
                # Envase: común si lo mandaron; sino el de ObServer del producto.
                env = envase_comun
                if env is None:
                    obs = session.get(ObsProducto, oid)
                    env = obs.cantidad_envase if (obs and obs.cantidad_envase) else None
                if env is not None and env > 0:
                    atr = session.get(ProductoAtributo, prod.id)
                    if atr is None:
                        atr = ProductoAtributo(producto_id=prod.id)
                        session.add(atr)
                    atr.cantidad_envase = env
                    atr.fuente = 'manual'
                else:
                    sin_envase += 1
                aplicados += 1
            session.commit()
        return jsonify({'ok': True, 'aplicados': aplicados,
                        'materializados': materializados,
                        'sin_envase': sin_envase, 'errores': errores})
