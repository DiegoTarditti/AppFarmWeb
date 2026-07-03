"""Panel admin de la tienda pública.

Diego 2026-06-24: la tienda pública (`/tienda`, `/producto/<oid>`, etc.) se
alimenta de dos decisiones del operador:
  - Qué rubros/subrubros de Observer se publican (tabla web_rubros_publicados).
  - Qué foto tiene cada producto (tabla web_producto_imagen, archivo en
    UPLOAD_FOLDER/tienda/<observer_id>.<ext>).

Este módulo NO tiene rutas públicas — solo el panel admin. Las rutas públicas
viven en routes/tienda_publica.py (Sprint 2).
"""

import os

from flask import (
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from sqlalchemy import or_

import database

_IMG_EXTS = {'jpg', 'jpeg', 'png', 'webp'}
_SUBDIR = 'tienda'   # bajo UPLOAD_FOLDER


def _get_config(session):
    """Devuelve el singleton de Config (siempre id=1). Lo crea si falta."""
    cfg = session.get(database.Config, 1)
    if cfg is None:
        cfg = database.Config(id=1, farmacia_nombre='Farmacia')
        session.add(cfg)
        session.flush()
    return cfg


def _ext_ok(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in _IMG_EXTS


def init_app(app):

    @app.route('/uploads/tienda/<path:filename>')
    def tienda_upload_file(filename):
        """Sirve las imágenes de productos de la tienda. Path relativo al
        UPLOAD_FOLDER/tienda/. En Sprint 2 se hará pública esta ruta también
        (para que la tienda pueda mostrarlas sin login)."""
        subdir = os.path.join(app.config['UPLOAD_FOLDER'], _SUBDIR)
        return send_from_directory(subdir, filename)

    @app.route('/admin/tienda/config', methods=['GET', 'POST'])
    def admin_tienda_config():
        with database.get_db() as s:
            cfg = _get_config(s)
            if request.method == 'POST':
                cfg.tienda_activa = bool(request.form.get('tienda_activa'))
                cfg.tienda_whatsapp_numero = (request.form.get('tienda_whatsapp_numero') or '').strip() or None
                cfg.tienda_titulo = (request.form.get('tienda_titulo') or '').strip() or None
                cfg.tienda_hero_texto = (request.form.get('tienda_hero_texto') or '').strip() or None
                cfg.tienda_direccion = (request.form.get('tienda_direccion') or '').strip() or None
                cfg.tienda_horarios = (request.form.get('tienda_horarios') or '').strip() or None
                s.commit()
                flash('Configuración guardada.')
                return redirect(url_for('admin_tienda_config'))
            return render_template('admin_tienda_config.html', cfg=cfg)

    @app.route('/admin/tienda/rubros', methods=['GET', 'POST'])
    def admin_tienda_rubros():
        with database.get_db() as s:
            if request.method == 'POST':
                # Lee todos los checkboxes marcados. Cada uno viene como
                # 'pub:<rubro_id>' (todo el rubro) o 'pub:<rubro_id>:<subrubro_id>'
                # (solo ese subrubro). Reemplazo total: borro todo y reinserto.
                seleccion = set()
                for key in request.form.keys():
                    if not key.startswith('pub:'):
                        continue
                    partes = key.split(':')
                    if len(partes) == 2 and partes[1].isdigit():
                        seleccion.add((int(partes[1]), None))
                    elif len(partes) == 3 and partes[1].isdigit() and partes[2].isdigit():
                        seleccion.add((int(partes[1]), int(partes[2])))
                # Reemplazo total: la tabla es chica y el reemplazo es simple.
                s.query(database.WebRubroPublicado).delete()
                s.flush()
                for rubro_id, subrubro_id in seleccion:
                    s.add(database.WebRubroPublicado(
                        rubro_observer_id=rubro_id,
                        subrubro_observer_id=subrubro_id,
                        activo=True,
                    ))
                s.commit()
                flash(f'{len(seleccion)} entrada(s) guardada(s).')
                return redirect(url_for('admin_tienda_rubros'))

            # GET: listado agrupado de rubros con sus subrubros y flags publicados.
            rubros = (s.query(database.ObsRubro)
                      .order_by(database.ObsRubro.descripcion).all())
            subs_por_rubro = {}
            for sub in (s.query(database.ObsSubrubro)
                        .order_by(database.ObsSubrubro.descripcion).all()):
                subs_por_rubro.setdefault(sub.rubro_observer, []).append(sub)
            publicados = s.query(database.WebRubroPublicado).all()
            pub_rubros_all = {p.rubro_observer_id for p in publicados if p.subrubro_observer_id is None}
            pub_subs = {(p.rubro_observer_id, p.subrubro_observer_id)
                        for p in publicados if p.subrubro_observer_id is not None}
            return render_template(
                'admin_tienda_rubros.html',
                rubros=rubros,
                subs_por_rubro=subs_por_rubro,
                pub_rubros_all=pub_rubros_all,
                pub_subs=pub_subs,
            )

    @app.route('/admin/tienda/imagenes', methods=['GET'])
    def admin_tienda_imagenes():
        """Buscador de productos + subida de imagen. Muestra los últimos 40
        productos con imagen ya cargada, y permite buscar por descripción /
        código de barras / observer_id."""
        q = (request.args.get('q') or '').strip()
        with database.get_db() as s:
            base = (s.query(database.ObsProducto)
                    .filter(database.ObsProducto.fecha_baja.is_(None),
                            database.ObsProducto.es_habilitado_venta.is_(True)))
            if q:
                like = f'%{q}%'
                if q.isdigit():
                    base = base.filter(or_(
                        database.ObsProducto.observer_id == int(q),
                        database.ObsProducto.codigo_alfabeta == q,
                        database.ObsProducto.descripcion.ilike(like),
                    ))
                else:
                    base = base.filter(database.ObsProducto.descripcion.ilike(like))
                productos = base.order_by(database.ObsProducto.descripcion).limit(40).all()
            else:
                # Sin búsqueda: mostrar solo los que ya tienen imagen (para editar/reemplazar).
                oids_con_img = [r[0] for r in s.query(database.WebProductoImagen.observer_id).all()]
                if oids_con_img:
                    productos = (base.filter(database.ObsProducto.observer_id.in_(oids_con_img))
                                 .order_by(database.ObsProducto.descripcion).limit(40).all())
                else:
                    productos = []
            imgs = {i.observer_id: i for i in s.query(database.WebProductoImagen).all()}
            return render_template(
                'admin_tienda_imagenes.html',
                q=q, productos=productos, imgs=imgs,
            )

    @app.route('/admin/tienda/imagenes/upload', methods=['POST'])
    def admin_tienda_imagenes_upload():
        """Sube una imagen para un producto. `observer_id` viene en el form."""
        try:
            oid = int(request.form.get('observer_id') or 0)
        except ValueError:
            oid = 0
        if not oid:
            flash('Producto inválido.')
            return redirect(url_for('admin_tienda_imagenes'))
        f = request.files.get('imagen')
        if not f or not f.filename:
            flash('Elegí un archivo.')
            return redirect(url_for('admin_tienda_imagenes'))
        if not _ext_ok(f.filename):
            flash('Formato no soportado. Usá jpg, png o webp.')
            return redirect(url_for('admin_tienda_imagenes'))

        # Guardar el archivo como <oid>.<ext> en UPLOAD_FOLDER/tienda/
        ext = f.filename.rsplit('.', 1)[1].lower()
        subdir = os.path.join(app.config['UPLOAD_FOLDER'], _SUBDIR)
        os.makedirs(subdir, exist_ok=True)
        fname = f'{oid}.{ext}'
        dst = os.path.join(subdir, fname)
        # Borrar variantes de otras extensiones que pudieran existir
        for old_ext in _IMG_EXTS:
            old = os.path.join(subdir, f'{oid}.{old_ext}')
            if old != dst and os.path.exists(old):
                try:
                    os.remove(old)
                except OSError:
                    pass
        f.save(dst)

        with database.get_db() as s:
            img = s.get(database.WebProductoImagen, oid)
            rel_path = f'{_SUBDIR}/{fname}'
            usuario = None
            try:
                from flask_login import current_user
                usuario = getattr(current_user, 'usuario', None) or getattr(current_user, 'email', None)
            except Exception:
                pass
            if img is None:
                s.add(database.WebProductoImagen(
                    observer_id=oid, ruta_archivo=rel_path, subido_por=usuario))
            else:
                img.ruta_archivo = rel_path
                img.subido_en = database.now_ar()
                img.subido_por = usuario
            s.commit()
        flash('Imagen guardada.')
        return redirect(url_for('admin_tienda_imagenes', q=request.form.get('q') or ''))

    @app.route('/admin/tienda/imagenes/<int:oid>/toggle-destacado', methods=['POST'])
    def admin_tienda_imagenes_toggle_destacado(oid):
        """Alterna el flag destacado del producto. Solo funciona si ya tiene
        imagen (los destacados sin foto no tienen sentido en la landing).
        Diego 2026-06-24."""
        with database.get_db() as s:
            img = s.get(database.WebProductoImagen, oid)
            if img is None:
                flash('Este producto no tiene imagen cargada — subí una foto primero para destacarlo.')
                return redirect(url_for('admin_tienda_imagenes'))
            img.destacado = not img.destacado
            s.commit()
            flash('Marcado como destacado.' if img.destacado else 'Ya no es destacado.')
        return redirect(url_for('admin_tienda_imagenes', q=request.args.get('q') or ''))

    @app.route('/admin/tienda/imagenes/<int:oid>/delete', methods=['POST'])
    def admin_tienda_imagenes_delete(oid):
        with database.get_db() as s:
            img = s.get(database.WebProductoImagen, oid)
            if img is None:
                flash('No había imagen cargada.')
                return redirect(url_for('admin_tienda_imagenes'))
            # Borrar archivo físico también
            path = os.path.join(app.config['UPLOAD_FOLDER'], img.ruta_archivo)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            s.delete(img)
            s.commit()
        flash('Imagen eliminada.')
        return redirect(url_for('admin_tienda_imagenes', q=request.args.get('q') or ''))
