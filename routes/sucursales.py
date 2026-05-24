"""Admin: registro de sucursales del grupo (DBs) para /transferencias.

Cada sucursal guarda su URL interna (dentro de Render) y externa (desde afuera).
La instancia compara su DB local (DATABASE_URL) contra las otras del registro.
"""
import os

from flask import flash, redirect, render_template, request, url_for

import database
from auth import requiere_permiso
from database import Sucursal, get_db


def init_app(app):
    @app.route('/sucursales')
    @requiere_permiso('usuarios', 'admin')
    def sucursales_list():
        with get_db() as s:
            rows = s.query(Sucursal).order_by(Sucursal.nombre).all()
            sucs = [{
                'id': r.id, 'slug': r.slug, 'nombre': r.nombre,
                'app_name': r.app_name, 'db_name': r.db_name,
                'url_interna': r.url_interna or '', 'url_externa': r.url_externa or '',
                'activa': r.activa,
            } for r in rows]
        return render_template('sucursales.html', sucursales=sucs,
                               local_env=os.environ.get('SUCURSAL_LOCAL', ''))

    @app.route('/sucursales/guardar', methods=['POST'])
    @requiere_permiso('usuarios', 'admin')
    def sucursales_guardar():
        f = request.form
        sid = (f.get('id') or '').strip()
        slug = (f.get('slug') or '').strip().lower()
        nombre = (f.get('nombre') or '').strip()
        if not slug or not nombre:
            flash('Slug y nombre son obligatorios.', 'error')
            return redirect(url_for('sucursales_list'))
        with get_db() as s:
            if sid:
                row = s.get(Sucursal, int(sid))
                if not row:
                    flash('Sucursal no encontrada.', 'error')
                    return redirect(url_for('sucursales_list'))
            else:
                if s.query(Sucursal).filter_by(slug=slug).first():
                    flash(f'Ya existe una sucursal con slug "{slug}".', 'error')
                    return redirect(url_for('sucursales_list'))
                row = Sucursal(slug=slug)
                s.add(row)
            row.slug = slug
            row.nombre = nombre
            row.app_name = (f.get('app_name') or '').strip() or None
            row.db_name = (f.get('db_name') or '').strip() or None
            row.url_interna = (f.get('url_interna') or '').strip() or None
            row.url_externa = (f.get('url_externa') or '').strip() or None
            row.activa = bool(f.get('activa'))
            row.actualizado_en = database.now_ar()
            s.commit()
        flash(f'Sucursal "{nombre}" guardada.', 'success')
        return redirect(url_for('sucursales_list'))

    @app.route('/sucursales/<int:sid>/delete', methods=['POST'])
    @requiere_permiso('usuarios', 'admin')
    def sucursales_delete(sid):
        with get_db() as s:
            row = s.get(Sucursal, sid)
            if row:
                nombre = row.nombre
                s.delete(row)
                s.commit()
                flash(f'Sucursal "{nombre}" eliminada.', 'success')
        return redirect(url_for('sucursales_list'))
