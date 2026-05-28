"""API unificada de partners (laboratorio | drogueria | proveedor).

Sirve de base para reemplazar los ~12 selects dispersos por un único
componente typeahead con chips de "más usados".
"""

from flask import jsonify, request
from sqlalchemy import func

import database
from database import Invoice, Laboratorio, Pedido, ProcesoCompra, Provider
from helpers import PARTNER_TIPOS

VALID_TIPOS = set(PARTNER_TIPOS)


def _query_base(session, tipo):
    """Devuelve query base de partners filtrada por tipo.
    - 'laboratorio' → tabla Laboratorio
    - 'drogueria'   → Provider filtrado por tipo='drogueria'
    - 'proveedor'   → Provider filtrado por tipo='proveedor' (o distinto de drogueria)
    Normaliza al shape {id, nombre}.
    """
    if tipo == 'laboratorio':
        return session.query(Laboratorio.id.label('id'),
                             Laboratorio.nombre.label('nombre'))
    if tipo == 'drogueria':
        return session.query(Provider.id.label('id'),
                             Provider.razon_social.label('nombre')) \
                      .filter(Provider.tipo == 'drogueria')
    # proveedor "otro"
    return session.query(Provider.id.label('id'),
                         Provider.razon_social.label('nombre')) \
                  .filter(Provider.tipo != 'drogueria')


def _top_usados(session, tipo, n=10):
    """Top N partners más usados. Cuenta apariciones en Pedido (para lab),
    Invoice (para drogueria/proveedor) y ProcesoCompra (todos).
    Fallback alfabético si no hay historia."""
    top_ids = []

    if tipo == 'laboratorio':
        # Pedidos guardan laboratorio por nombre; contamos por nombre y
        # después resolvemos el id.
        rows = (session.query(Pedido.laboratorio, func.count(Pedido.id).label('c'))
                .filter(Pedido.laboratorio.isnot(None))
                .group_by(Pedido.laboratorio)
                .order_by(func.count(Pedido.id).desc())
                .limit(n * 2).all())
        nombres = [r[0] for r in rows if r[0]]
        if nombres:
            top = (session.query(Laboratorio.id, Laboratorio.nombre)
                   .filter(Laboratorio.nombre.in_(nombres)).all())
            name_to_id = {l.nombre: l.id for l in top}
            top_ids = [name_to_id[n_] for n_ in nombres if n_ in name_to_id][:n]
    else:
        # Invoice.proveedor_razon o ProcesoCompra.partner_id
        rows = (session.query(ProcesoCompra.partner_id,
                              func.count(ProcesoCompra.id).label('c'))
                .filter(ProcesoCompra.tipo == tipo)
                .filter(ProcesoCompra.partner_id.isnot(None))
                .group_by(ProcesoCompra.partner_id)
                .order_by(func.count(ProcesoCompra.id).desc())
                .limit(n).all())
        top_ids = [r[0] for r in rows]

    # Completar con alfabéticos si falta
    if len(top_ids) < n:
        faltantes = n - len(top_ids)
        q = _query_base(session, tipo)
        if top_ids:
            q = q.filter(~database.Laboratorio.id.in_(top_ids)) if tipo == 'laboratorio' \
                else q.filter(~Provider.id.in_(top_ids))
        extras = [r.id for r in q.order_by('nombre').limit(faltantes).all()]
        top_ids.extend(extras)

    if not top_ids:
        return []

    # Resolver nombre respetando el orden de top_ids
    q = _query_base(session, tipo)
    if tipo == 'laboratorio':
        rows = q.filter(Laboratorio.id.in_(top_ids)).all()
    else:
        rows = q.filter(Provider.id.in_(top_ids)).all()
    by_id = {r.id: r.nombre for r in rows}
    return [{'id': i, 'nombre': by_id[i]} for i in top_ids if i in by_id]


def init_app(app):

    @app.route('/api/partners/search')
    def api_partners_search():
        tipo = (request.args.get('tipo') or '').strip()
        q = (request.args.get('q') or '').strip()
        try:
            limit = min(int(request.args.get('limit') or 15), 50)
        except ValueError:
            limit = 15
        if tipo not in VALID_TIPOS:
            return jsonify({'error': 'tipo inválido'}), 400

        with database.get_db() as session:
            name_col = Laboratorio.nombre if tipo == 'laboratorio' else Provider.razon_social

            # Para 'laboratorio' con q ≥ 2 chars: también consultamos
            # ObsLaboratorio y materializamos en `laboratorios` los faltantes
            # (vía get_or_create_laboratorio, idempotente). Esto cubre el caso
            # de labs presentes en ObServer pero nunca materializados — el
            # autocomplete dejaba afuera Roemmers, Siegfried, etc. La operación
            # es side-effect en GET pero limitada a humanos tipeando, vinculada
            # a `observer_id`, y no crea duplicados (dedup por normalización).
            if tipo == 'laboratorio' and q and len(q) >= 2:
                from database import ObsLaboratorio
                from helpers import get_or_create_laboratorio
                obs_rows = (session.query(ObsLaboratorio.descripcion,
                                          ObsLaboratorio.observer_id)
                            .filter(ObsLaboratorio.fecha_baja.is_(None),
                                    ObsLaboratorio.descripcion.ilike(f'%{q}%'))
                            .limit(limit).all())
                for desc, obs_id in obs_rows:
                    get_or_create_laboratorio(
                        session, desc, observer_id=obs_id, activo=True)
                # get_or_create no commitea (flush solo). Si nada nuevo se
                # agregó, el commit es no-op. Si hubo inserts, los persistimos
                # antes de re-consultar laboratorios.
                if obs_rows:
                    session.commit()

            qs = _query_base(session, tipo)
            if q:
                qs = qs.filter(name_col.ilike(f'%{q}%'))
            rows = qs.order_by('nombre').limit(limit).all()
            data = [{'id': r.id, 'nombre': r.nombre} for r in rows]
        return jsonify({'data': data, 'tipo': tipo, 'q': q})

    @app.route('/api/partners/create', methods=['POST'])
    def api_partners_create():
        body = request.get_json(silent=True) or {}
        tipo = (body.get('tipo') or '').strip()
        nombre = (body.get('nombre') or '').strip()
        if tipo not in VALID_TIPOS:
            return jsonify({'error': 'tipo inválido'}), 400
        if len(nombre) < 2:
            return jsonify({'error': 'nombre demasiado corto'}), 400

        with database.get_db() as session:
            try:
                from helpers import (
                    _normalizar_nombre_entidad,
                    get_or_create_laboratorio,
                    get_or_create_proveedor,
                )
                if tipo == 'laboratorio':
                    norm_nuevo = _normalizar_nombre_entidad(nombre)
                    # ¿Ya existe (con normalización profunda)?
                    for c in session.query(Laboratorio).all():
                        if _normalizar_nombre_entidad(c.nombre) == norm_nuevo:
                            return jsonify({'data': {'id': c.id, 'nombre': c.nombre},
                                            'created': False})
                    nuevo = get_or_create_laboratorio(session, nombre)
                    session.commit()
                    return jsonify({'data': {'id': nuevo.id, 'nombre': nuevo.nombre},
                                    'created': True})

                # drogueria | proveedor → Provider
                prov_tipo = 'drogueria' if tipo == 'drogueria' else 'proveedor'
                norm_nuevo = _normalizar_nombre_entidad(nombre)
                # Match por razón social normalizada + tipo
                for c in session.query(Provider).filter_by(tipo=prov_tipo).all():
                    if _normalizar_nombre_entidad(c.razon_social) == norm_nuevo:
                        return jsonify({'data': {'id': c.id, 'nombre': c.razon_social},
                                        'created': False})
                nuevo = get_or_create_proveedor(session, nombre, tipo=prov_tipo)
                session.commit()
                return jsonify({'data': {'id': nuevo.id, 'nombre': nuevo.razon_social},
                                'created': True})
            except Exception as e:
                session.rollback()
                return jsonify({'error': str(e)}), 500

    @app.route('/api/partners/top')
    def api_partners_top():
        tipo = (request.args.get('tipo') or '').strip()
        try:
            n = min(int(request.args.get('n') or 8), 20)
        except ValueError:
            n = 8
        if tipo not in VALID_TIPOS:
            return jsonify({'error': 'tipo inválido'}), 400
        with database.get_db() as session:
            data = _top_usados(session, tipo, n)
        return jsonify({'data': data, 'tipo': tipo})
