"""CRUD de la matriz lab × droguería con descuentos base acordados.

Es el nivel 1 de los 5 niveles de descuento del flujo de compra rápida.
La matriz solo incluye combinaciones que tienen un descuento explícito;
las combinaciones sin descuento implican que esa droguería no opera con
ese laboratorio.
"""
from datetime import date

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

import database
from database import DescuentoBase, Laboratorio, Provider
from services.descuentos import mejor_descuento


def init_app(app):

    @app.route('/descuentos-base')
    @login_required
    def descuentos_base_lista():
        """Vista matriz: filas=labs (ordenados por uso o nombre), columnas=drogerías.
        Cada celda tiene el descuento o queda vacía para indicar 'sin acuerdo'."""
        with database.get_db() as session:
            # Solo droguerías activas con tipo='drogueria'
            drogs = (session.query(Provider)
                     .filter(Provider.tipo == 'drogueria',
                             Provider.activo == True)  # noqa: E712
                     .order_by(Provider.razon_social).all())
            # Solo labs activos (sino la lista es interminable)
            labs = (session.query(Laboratorio)
                    .filter(Laboratorio.activo == True)  # noqa: E712
                    .order_by(Laboratorio.nombre).all())
            # Mapa (lab_id, drog_id) → DescuentoBase
            descuentos = (session.query(DescuentoBase)
                          .filter(DescuentoBase.activo == True).all())  # noqa: E712
            mapa = {(d.laboratorio_id, d.drogueria_id): d for d in descuentos}
            # Filtro: solo labs que tienen al menos un descuento configurado
            # (sino sería interminable; el usuario puede agregar nuevos labs por buscador).
            mostrar_todos = request.args.get('todos') == '1'
            if not mostrar_todos:
                labs_con_dto = {d.laboratorio_id for d in descuentos}
                labs = [l for l in labs if l.id in labs_con_dto]
            return render_template('descuentos_base.html',
                                   labs=labs,
                                   drogs=drogs,
                                   mapa=mapa,
                                   mostrar_todos=mostrar_todos)

    @app.route('/api/mejor-descuento/<int:observer_id>')
    @login_required
    def api_mejor_descuento(observer_id):
        """Devuelve lista de droguerías ordenadas por mejor descuento total
        para el producto dado. Usa el laboratorio del producto en obs_productos.

        Query params:
        - cantidad: int (default 1) — para evaluar ofertas con mínimo.
        """
        try:
            cantidad = max(1, int(request.args.get('cantidad', 1)))
        except (ValueError, TypeError):
            cantidad = 1
        with database.get_db() as session:
            obs_p = session.get(database.ObsProducto, observer_id)
            if not obs_p:
                return jsonify({'ok': False, 'error': 'Producto no encontrado'}), 404
            if obs_p.laboratorio_observer is None:
                return jsonify({'ok': False, 'error': 'Producto sin laboratorio'}), 400
            # Resolver Laboratorio local desde observer_id
            lab_local = (session.query(Laboratorio)
                         .filter_by(observer_id=obs_p.laboratorio_observer).first())
            if not lab_local:
                return jsonify({'ok': False,
                                'error': f'Laboratorio {obs_p.laboratorio_observer} sin mapping local'}), 400
            opciones = mejor_descuento(session, observer_id, lab_local.id, cantidad=cantidad)
            return jsonify({
                'ok':                  True,
                'producto':            {'observer_id': obs_p.observer_id,
                                        'descripcion': obs_p.descripcion},
                'laboratorio':         {'id': lab_local.id, 'nombre': lab_local.nombre},
                'cantidad_evaluada':   cantidad,
                'opciones':            opciones,
            })

    @app.route('/descuentos-base/celda', methods=['POST'])
    @login_required
    def descuentos_base_set():
        """Crea/actualiza una celda. Si descuento_pct es 0/None, marca activo=False
        (no borra para mantener histórico)."""
        data = request.get_json(silent=True) or {}
        try:
            lab_id = int(data.get('lab_id'))
            drog_id = int(data.get('drog_id'))
        except (TypeError, ValueError):
            return jsonify({'error': 'lab_id/drog_id inválidos'}), 400
        pct_str = (str(data.get('descuento_pct') or '').replace(',', '.').strip())
        plazo = (data.get('plazo_pago') or '').strip() or None
        observacion = (data.get('observacion') or '').strip() or None
        try:
            pct = float(pct_str) if pct_str else None
        except ValueError:
            return jsonify({'error': 'descuento_pct inválido'}), 400

        with database.get_db() as session:
            d = (session.query(DescuentoBase)
                 .filter_by(laboratorio_id=lab_id, drogueria_id=drog_id).first())
            if not pct or pct <= 0:
                # Vaciar celda → desactivar (no borrar)
                if d:
                    d.activo = False
                    session.commit()
                return jsonify({'ok': True, 'estado': 'desactivado'})
            if not d:
                d = DescuentoBase(
                    laboratorio_id=lab_id,
                    drogueria_id=drog_id,
                    descuento_pct=pct,
                    plazo_pago=plazo,
                    observacion=observacion,
                    activo=True,
                )
                session.add(d)
            else:
                d.descuento_pct = pct
                d.plazo_pago = plazo
                d.observacion = observacion
                d.activo = True
            session.commit()
            return jsonify({
                'ok': True,
                'estado': 'guardado',
                'descuento_pct': float(d.descuento_pct),
                'plazo_pago': d.plazo_pago or '',
            })
