"""Equivalencia venta↔compra: productos que se venden como un artículo del
catálogo pero se piden al proveedor como otro artículo distinto (ej. sobre
suelto vendido vs. caja que hay que comprar). Ver database.py
`EquivalenciaCompra` y services/equivalencias_compra.py para el detalle."""
from flask import jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import text

from database import EquivalenciaCompra, get_db
from services.equivalencias_compra import detectar_candidatos


def _obs_info(session, observer_id):
    row = session.execute(text("""
        SELECT op.descripcion, op.cantidad_envase, ol.descripcion
        FROM obs_productos op
        LEFT JOIN obs_laboratorios ol ON ol.observer_id = op.laboratorio_observer
        WHERE op.observer_id = :oid
    """), {'oid': observer_id}).fetchone()
    if not row:
        return {'desc': f'(observer_id {observer_id} no encontrado)', 'envase': None, 'lab': ''}
    return {'desc': row[0], 'envase': int(row[1]) if row[1] is not None else None, 'lab': row[2] or ''}


def init_app(app):

    @app.route('/productos/equivalencias-compra')
    @login_required
    def equivalencias_compra_list():
        with get_db() as session:
            confirmadas = (session.query(EquivalenciaCompra)
                            .filter_by(activo=True)
                            .order_by(EquivalenciaCompra.creado_en.desc()).all())
            filas = []
            for eq in confirmadas:
                venta = _obs_info(session, eq.producto_venta_observer_id)
                compra = _obs_info(session, eq.producto_compra_observer_id)
                filas.append({
                    'id': eq.id,
                    'venta_oid': eq.producto_venta_observer_id,
                    'venta_desc': venta['desc'],
                    'compra_oid': eq.producto_compra_observer_id,
                    'compra_desc': compra['desc'],
                    'compra_envase': compra['envase'],
                    'lab': venta['lab'] or compra['lab'],
                    'fuente': eq.fuente,
                    'creado_en': eq.creado_en.strftime('%d/%m/%Y') if eq.creado_en else '',
                })
        return render_template('equivalencias_compra.html', filas=filas)

    @app.route('/api/equivalencia-compra/candidatos')
    @login_required
    def api_equivalencia_compra_candidatos():
        with get_db() as session:
            candidatos = detectar_candidatos(session)
        return jsonify({'ok': True, 'data': candidatos})

    @app.route('/api/equivalencia-compra', methods=['POST'])
    @login_required
    def api_equivalencia_compra_confirmar():
        body = request.get_json(silent=True) or {}
        venta_oid = body.get('producto_venta_observer_id')
        compra_oid = body.get('producto_compra_observer_id')
        fuente = 'sugerido' if body.get('desde_sugerido') else 'manual'
        if not venta_oid or not compra_oid:
            return jsonify({'ok': False, 'error': 'Faltan los dos observer_id.'}), 400
        if int(venta_oid) == int(compra_oid):
            return jsonify({'ok': False, 'error': 'El producto de venta y de compra no pueden ser el mismo.'}), 400

        creado_por = (getattr(current_user, 'email', None)
                      or str(getattr(current_user, 'id', '')))
        with get_db() as session:
            eq = (session.query(EquivalenciaCompra)
                  .filter_by(producto_venta_observer_id=venta_oid).first())
            if eq:
                eq.producto_compra_observer_id = compra_oid
                eq.activo = True
                eq.fuente = fuente
            else:
                eq = EquivalenciaCompra(
                    producto_venta_observer_id=venta_oid,
                    producto_compra_observer_id=compra_oid,
                    activo=True, fuente=fuente, creado_por=creado_por,
                )
                session.add(eq)
            session.commit()
            return jsonify({'ok': True, 'id': eq.id})

    @app.route('/api/equivalencia-compra/descartar', methods=['POST'])
    @login_required
    def api_equivalencia_compra_descartar():
        """Recuerda que un candidato sugerido NO debe volver a proponerse
        (no confirma una equivalencia real, sólo la excluye de /candidatos)."""
        body = request.get_json(silent=True) or {}
        venta_oid = body.get('producto_venta_observer_id')
        compra_oid = body.get('producto_compra_observer_id')
        if not venta_oid or not compra_oid:
            return jsonify({'ok': False, 'error': 'Faltan los dos observer_id.'}), 400
        with get_db() as session:
            eq = (session.query(EquivalenciaCompra)
                  .filter_by(producto_venta_observer_id=venta_oid).first())
            if eq:
                eq.activo = False
                eq.fuente = 'descartado'
            else:
                session.add(EquivalenciaCompra(
                    producto_venta_observer_id=venta_oid,
                    producto_compra_observer_id=compra_oid,
                    activo=False, fuente='descartado',
                ))
            session.commit()
        return jsonify({'ok': True})

    @app.route('/api/equivalencia-compra/<int:eq_id>/desactivar', methods=['POST'])
    @login_required
    def api_equivalencia_compra_desactivar(eq_id):
        with get_db() as session:
            eq = session.get(EquivalenciaCompra, eq_id)
            if eq:
                eq.activo = False
                session.commit()
        return jsonify({'ok': True})
