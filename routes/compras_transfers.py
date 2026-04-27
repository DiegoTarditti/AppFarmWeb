"""Panel de transfers vigentes — revisar/activar/desactivar antes de
entrar al optimizador de compra rápida.

Permite:
- Ver todos los transfers/ofertas activos por droguería + lab.
- Activar/desactivar manual (sin borrar — quedan en histórico).
- Ver vencidos / por vencer próximos 7 días.
- Renovar un vencido extendiendo `vigencia_hasta`.
"""
from datetime import date, timedelta

from flask import jsonify, render_template, request
from flask_login import login_required
from sqlalchemy import or_

import database
from database import Laboratorio, OfertaMinimo, Provider


def init_app(app):

    @app.route('/compras/transfers')
    @login_required
    def compras_transfers():
        """Panel principal de transfers. Filtros: por droguería, vencidos."""
        filtro = (request.args.get('filtro') or 'activos').strip()
        drog_id_param = request.args.get('drog_id', type=int)
        lab_id_param = request.args.get('lab_id', type=int)

        hoy = date.today()
        en_7d = hoy + timedelta(days=7)

        with database.get_db() as session:
            base = session.query(OfertaMinimo)
            if filtro == 'activos':
                base = base.filter(
                    OfertaMinimo.activo == True,  # noqa: E712
                    or_(OfertaMinimo.vigencia_hasta.is_(None),
                        OfertaMinimo.vigencia_hasta >= hoy),
                )
            elif filtro == 'por_vencer':
                base = base.filter(
                    OfertaMinimo.activo == True,  # noqa: E712
                    OfertaMinimo.vigencia_hasta.isnot(None),
                    OfertaMinimo.vigencia_hasta >= hoy,
                    OfertaMinimo.vigencia_hasta <= en_7d,
                )
            elif filtro == 'vencidos':
                base = base.filter(
                    OfertaMinimo.vigencia_hasta.isnot(None),
                    OfertaMinimo.vigencia_hasta < hoy,
                )
            elif filtro == 'inactivos':
                base = base.filter(OfertaMinimo.activo == False)  # noqa: E712
            # filtro=='todos' → sin filtro

            if drog_id_param:
                if drog_id_param == -1:  # -1 = "directo lab" (sin drog)
                    base = base.filter(OfertaMinimo.drogueria_id.is_(None))
                else:
                    base = base.filter(OfertaMinimo.drogueria_id == drog_id_param)
            if lab_id_param:
                base = base.filter(OfertaMinimo.laboratorio_id == lab_id_param)

            ofertas = base.order_by(OfertaMinimo.laboratorio_id,
                                     OfertaMinimo.drogueria_id,
                                     OfertaMinimo.observacion,
                                     OfertaMinimo.descripcion).all()

            # Resolver lab + drog en batch
            lab_ids = {o.laboratorio_id for o in ofertas}
            drog_ids = {o.drogueria_id for o in ofertas if o.drogueria_id}
            labs_map = dict(session.query(Laboratorio.id, Laboratorio.nombre)
                            .filter(Laboratorio.id.in_(lab_ids)).all()) if lab_ids else {}
            drogs_map = dict(session.query(Provider.id, Provider.razon_social)
                             .filter(Provider.id.in_(drog_ids)).all()) if drog_ids else {}

            # Agrupar por (lab, drog)
            grupos = {}
            for o in ofertas:
                key = (o.laboratorio_id, o.drogueria_id)
                if key not in grupos:
                    drog_label = drogs_map.get(o.drogueria_id) if o.drogueria_id else 'Directo lab'
                    grupos[key] = {
                        'lab_id':    o.laboratorio_id,
                        'lab':       labs_map.get(o.laboratorio_id, '—'),
                        'drog_id':   o.drogueria_id or -1,
                        'drog':      drog_label,
                        'ofertas':   [],
                    }
                dias_quedan = None
                if o.vigencia_hasta:
                    dias_quedan = (o.vigencia_hasta - hoy).days
                grupos[key]['ofertas'].append({
                    'id':              o.id,
                    'descripcion':     o.descripcion or '',
                    'codigo':          o.codigo or '',
                    'ean':             o.ean or '',
                    'descuento_psl':   float(o.descuento_psl) if o.descuento_psl else 0,
                    'unidades_minima': o.unidades_minima or 1,
                    'plazo_pago':      o.plazo_pago or '',
                    'observacion':     o.observacion or '',
                    'vigencia_hasta':  o.vigencia_hasta.isoformat() if o.vigencia_hasta else None,
                    'dias_quedan':     dias_quedan,
                    'activo':          bool(o.activo),
                    'tipo_descuento':  o.tipo_descuento or '',
                })

            # Stats
            stats = {
                'total':        len(ofertas),
                'lab_count':    len(lab_ids),
                'drog_count':   len(drog_ids),
                'por_vencer':   sum(1 for o in ofertas
                                    if o.vigencia_hasta and hoy <= o.vigencia_hasta <= en_7d
                                    and o.activo),
                'vencidos':     sum(1 for o in ofertas
                                    if o.vigencia_hasta and o.vigencia_hasta < hoy),
            }

            # Para los filtros: lista de drogs y labs presentes en TODOS los datos
            todos_labs_q = (session.query(Laboratorio.id, Laboratorio.nombre)
                            .filter(Laboratorio.id.in_(
                                session.query(OfertaMinimo.laboratorio_id).distinct()))
                            .order_by(Laboratorio.nombre).all())
            todos_drogs_q = (session.query(Provider.id, Provider.razon_social)
                             .filter(Provider.tipo == 'drogueria',
                                     Provider.id.in_(
                                         session.query(OfertaMinimo.drogueria_id).distinct()))
                             .order_by(Provider.razon_social).all())

            return render_template('compras_transfers.html',
                                   grupos=list(grupos.values()),
                                   stats=stats,
                                   filtro=filtro,
                                   drog_id_param=drog_id_param,
                                   lab_id_param=lab_id_param,
                                   labs_filtro=[{'id': l[0], 'nombre': l[1]} for l in todos_labs_q],
                                   drogs_filtro=[{'id': d[0], 'nombre': d[1]} for d in todos_drogs_q])

    @app.route('/api/compras/transfer/<int:oferta_id>/toggle', methods=['POST'])
    @login_required
    def api_compras_transfer_toggle(oferta_id):
        """Activa/desactiva una oferta puntual sin borrarla."""
        with database.get_db() as session:
            o = session.get(OfertaMinimo, oferta_id)
            if not o:
                return jsonify({'ok': False, 'error': 'Oferta no encontrada'}), 404
            o.activo = not bool(o.activo)
            session.commit()
            return jsonify({'ok': True, 'activo': bool(o.activo)})

    @app.route('/api/compras/transfer/<int:oferta_id>/renovar', methods=['POST'])
    @login_required
    def api_compras_transfer_renovar(oferta_id):
        """Extiende la vigencia de una oferta. Body: {dias: 30}."""
        data = request.get_json(silent=True) or {}
        try:
            dias = max(1, min(365, int(data.get('dias', 30))))
        except (ValueError, TypeError):
            dias = 30
        nueva_vigencia = date.today() + timedelta(days=dias)
        with database.get_db() as session:
            o = session.get(OfertaMinimo, oferta_id)
            if not o:
                return jsonify({'ok': False, 'error': 'Oferta no encontrada'}), 404
            o.vigencia_hasta = nueva_vigencia
            o.activo = True  # renovar también re-activa
            session.commit()
            return jsonify({'ok': True, 'vigencia_hasta': nueva_vigencia.isoformat()})

    @app.route('/api/compras/transfer-grupo/toggle', methods=['POST'])
    @login_required
    def api_compras_transfer_grupo_toggle():
        """Activa/desactiva todos los transfers de un (lab_id, drog_id)."""
        data = request.get_json(silent=True) or {}
        try:
            lab_id = int(data.get('lab_id'))
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'lab_id inválido'}), 400
        drog_id = data.get('drog_id')
        try:
            drog_id = int(drog_id) if drog_id and drog_id != -1 else None
        except (TypeError, ValueError):
            drog_id = None
        accion = (data.get('accion') or '').strip().lower()
        if accion not in ('activar', 'desactivar'):
            return jsonify({'ok': False, 'error': 'accion inválida'}), 400
        nuevo_activo = accion == 'activar'
        with database.get_db() as session:
            q = session.query(OfertaMinimo).filter(OfertaMinimo.laboratorio_id == lab_id)
            if drog_id:
                q = q.filter(OfertaMinimo.drogueria_id == drog_id)
            else:
                q = q.filter(OfertaMinimo.drogueria_id.is_(None))
            n = q.update({'activo': nuevo_activo})
            session.commit()
            return jsonify({'ok': True, 'afectados': n, 'activo': nuevo_activo})
