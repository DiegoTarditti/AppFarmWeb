"""Pantalla de ofertas para el bot: carga manual de descuento % o 2x1 por producto."""
import os

from flask import jsonify, render_template, request
from flask_login import current_user, login_required

import database


def init_app(app):

    @app.route('/ofertas-bot')
    @login_required
    def ofertas_bot():
        return render_template('ofertas_bot.html')

    @app.route('/ofertas-bot/api/laboratorios')
    @login_required
    def ofertas_bot_labs():
        with database.get_db() as s:
            labs = (s.query(database.ObsLaboratorio)
                    .filter(database.ObsLaboratorio.fecha_baja.is_(None))
                    .order_by(database.ObsLaboratorio.descripcion).all())
            return jsonify({'laboratorios': [
                {'observer_id': l.observer_id, 'descripcion': l.descripcion}
                for l in labs]})

    @app.route('/ofertas-bot/api/drogas')
    @login_required
    def ofertas_bot_drogas():
        lab_id = request.args.get('lab_id', type=int)
        if not lab_id:
            return jsonify({'drogas': []})
        with database.get_db() as s:
            rows = s.execute(database.text(
                "SELECT DISTINCT nd.observer_id, nd.descripcion "
                "FROM obs_productos op "
                "JOIN obs_nombres_drogas nd ON nd.observer_id = op.nombre_droga_observer "
                "WHERE op.laboratorio_observer = :lab_id AND op.fecha_baja IS NULL "
                "ORDER BY nd.descripcion"
            ), {'lab_id': lab_id}).fetchall()
            return jsonify({'drogas': [
                {'observer_id': r.observer_id, 'descripcion': r.descripcion}
                for r in rows]})

    @app.route('/ofertas-bot/api/productos')
    @login_required
    def ofertas_bot_productos():
        droga_id = request.args.get('droga_id', type=int)
        if not droga_id:
            return jsonify({'productos': []})
        id_farmacia = int(os.environ.get('OBSERVER_ID_FARMACIA', '10525'))
        with database.get_db() as s:
            # Productos de esa droga con stock, marcando los que ya tienen oferta
            rows = s.execute(database.text(
                "SELECT op.observer_id, op.descripcion, "
                "       COALESCE(os.stock_actual, 0) AS stock, "
                "       CASE WHEN ob.id IS NOT NULL THEN 1 ELSE 0 END AS tiene_oferta "
                "FROM obs_productos op "
                "JOIN obs_stock os ON os.producto_observer = op.observer_id "
                "    AND os.id_farmacia = :fid "
                "LEFT JOIN ofertas_bot ob ON ob.observer_id = op.observer_id "
                "    AND ob.activo = true "
                "WHERE op.nombre_droga_observer = :droga_id "
                "  AND op.fecha_baja IS NULL "
                "ORDER BY os.stock_actual > 0 DESC, op.descripcion"
            ), {'droga_id': droga_id, 'fid': id_farmacia}).fetchall()
            return jsonify({'productos': [
                {'observer_id': r.observer_id, 'descripcion': r.descripcion,
                 'stock': r.stock, 'tiene_oferta': bool(r.tiene_oferta)}
                for r in rows]})

    @app.route('/ofertas-bot/api/guardar', methods=['POST'])
    @login_required
    def ofertas_bot_guardar():
        """Body: {observer_ids: [int], tipo: 'descuento_pct'|'2x1', valor: float|null}.
        Upsert por observer_id: si ya existe, actualiza tipo/valor y activa=True."""
        b = request.json or {}
        ids = b.get('observer_ids') or []
        tipo = (b.get('tipo') or '').strip()
        if tipo not in ('descuento_pct', '2x1'):
            return jsonify({'ok': False, 'error': 'tipo inválido'}), 400
        if not ids:
            return jsonify({'ok': False, 'error': 'falta observer_ids'}), 400
        valor = b.get('valor')
        if tipo == 'descuento_pct' and valor is None:
            return jsonify({'ok': False, 'error': 'falta valor %'}), 400
        with database.get_db() as s:
            for oid in ids:
                obs = s.query(database.ObsProducto).get(oid)
                desc = obs.descripcion if obs else f'Producto #{oid}'
                existente = s.query(database.OfertaBot).filter_by(observer_id=oid).first()
                if existente:
                    existente.tipo = tipo
                    existente.valor = valor if tipo == 'descuento_pct' else None
                    existente.activo = True
                else:
                    s.add(database.OfertaBot(
                        observer_id=oid, descripcion=desc,
                        tipo=tipo, valor=valor if tipo == 'descuento_pct' else None))
            s.commit()
        return jsonify({'ok': True})

    @app.route('/ofertas-bot/api/cargadas')
    @login_required
    def ofertas_bot_cargadas():
        with database.get_db() as s:
            ofertas = (s.query(database.OfertaBot)
                       .order_by(database.OfertaBot.creado_en.desc()).all())
            return jsonify({'ofertas': [
                {'id': o.id, 'observer_id': o.observer_id, 'descripcion': o.descripcion,
                 'tipo': o.tipo, 'valor': float(o.valor) if o.valor else None,
                 'activo': o.activo} for o in ofertas]})

    @app.route('/ofertas-bot/api/<int:oid>/toggle', methods=['POST'])
    @login_required
    def ofertas_bot_toggle(oid):
        with database.get_db() as s:
            o = s.get(database.OfertaBot, oid)
            if o:
                o.activo = not o.activo
                s.commit()
            return jsonify({'ok': True, 'activo': o.activo if o else False})

    @app.route('/ofertas-bot/api/<int:oid>', methods=['DELETE'])
    @login_required
    def ofertas_bot_delete(oid):
        with database.get_db() as s:
            o = s.get(database.OfertaBot, oid)
            if o:
                s.delete(o)
                s.commit()
            return jsonify({'ok': True})