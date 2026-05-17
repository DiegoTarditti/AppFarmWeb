"""CRUD de flags de comportamiento por producto (EAN) o laboratorio."""
import json
from datetime import date

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import database
from database import Laboratorio, Producto, ProductoFlag, TipoPedidoConfig, get_db


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
                prods = (session.query(Producto.codigo_barra, Producto.descripcion)
                         .filter(Producto.codigo_barra.in_(eans)).all())
                productos_map = {p.codigo_barra: p.descripcion for p in prods}

            flags = [_row_to_dict(r, productos_map, cfgs) for r in rows]

            labs = (session.query(Laboratorio.id, Laboratorio.nombre)
                    .filter(Laboratorio.activo.is_(True))
                    .order_by(Laboratorio.nombre).all())

        return render_template('producto_flags.html',
                               flags=flags,
                               flag_configs=cfgs,
                               filtro_slug=filtro_slug,
                               labs=labs)

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
        from flask import jsonify
        ean = request.args.get('ean', '').strip()
        if not ean:
            return jsonify({'nombre': None})
        with get_db() as session:
            prod = session.query(Producto).filter_by(codigo_barra=ean).first()
            return jsonify({'nombre': prod.descripcion if prod else None})
