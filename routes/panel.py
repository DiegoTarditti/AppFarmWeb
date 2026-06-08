"""Panel del dueño — vista de estado operativo en tiempo real.

GET /panel          → dashboard HTML (solo admin/dev)
GET /panel/api/resumen → JSON con todos los KPIs de las cards
"""
from datetime import datetime, timedelta

from flask import jsonify, render_template
from flask_login import current_user, login_required
from sqlalchemy import func, text

import database

_ROLES = ('admin', 'dev')


def init_app(app):

    @app.route('/panel')
    @login_required
    def panel_dueno():
        if current_user.rol not in _ROLES:
            return 'Sin acceso', 403
        return render_template('panel.html')

    @app.route('/panel/api/resumen')
    @login_required
    def panel_api_resumen():
        if current_user.rol not in _ROLES:
            return jsonify({'ok': False}), 403

        hoy = database.now_ar().date()
        inicio_dia = datetime(hoy.year, hoy.month, hoy.day, 0, 0, 0)
        fin_dia = inicio_dia + timedelta(days=1)

        with database.get_db() as s:
            # ── Caja ────────────────────────────────────────────────────────
            T = database.TicketCaja
            conf_count = s.query(func.count(T.id)).filter(
                T.estado == 'confirmado').scalar() or 0
            conf_total = s.query(func.sum(T.total)).filter(
                T.estado == 'confirmado').scalar() or 0

            cob_count = s.query(func.count(T.id)).filter(
                T.estado == 'cobrado').scalar() or 0
            cob_total = s.query(func.sum(T.total)).filter(
                T.estado == 'cobrado').scalar() or 0

            # Ventas del día: entregados o cobrados creados hoy
            venta_count = s.query(func.count(T.id)).filter(
                T.estado.in_(['cobrado', 'entregado']),
                T.creado_en >= inicio_dia,
                T.creado_en < fin_dia,
            ).scalar() or 0
            venta_total = s.query(func.sum(T.total)).filter(
                T.estado.in_(['cobrado', 'entregado']),
                T.creado_en >= inicio_dia,
                T.creado_en < fin_dia,
            ).scalar() or 0

            # ── Atención bot ────────────────────────────────────────────────
            C = database.BotConversacion
            cola = s.query(func.count(C.id)).filter(
                C.estado_atencion == 'cola').scalar() or 0
            humano = s.query(func.count(C.id)).filter(
                C.estado_atencion == 'humano').scalar() or 0
            bot_activo = s.query(func.count(C.id)).filter(
                C.estado_atencion == 'bot',
                C.ultimo_en >= database.now_ar() - timedelta(hours=1),
            ).scalar() or 0

            # Supervisados (alertas bot enviadas, no reseteadas)
            supervisados = s.execute(text(
                'SELECT COUNT(DISTINCT conversacion_id) FROM informe_enviado'
            )).scalar() or 0

            # ── Repartos ────────────────────────────────────────────────────
            P = database.PedidoReparto
            rep_pendiente = s.query(func.count(P.id)).filter(
                P.fecha == hoy, P.estado == 'pendiente').scalar() or 0
            rep_en_camino = s.query(func.count(P.id)).filter(
                P.fecha == hoy, P.estado == 'en_camino').scalar() or 0
            rep_entregado = s.query(func.count(P.id)).filter(
                P.fecha == hoy, P.estado == 'entregado').scalar() or 0
            rep_total_hoy = rep_pendiente + rep_en_camino + rep_entregado

        return jsonify({
            'ok': True,
            'ts': database.now_ar().strftime('%H:%M:%S'),
            'ventas': {
                'count': venta_count,
                'total': float(venta_total),
            },
            'caja': {
                'confirmados': conf_count,
                'confirmados_total': float(conf_total),
                'cobrados': cob_count,
                'cobrados_total': float(cob_total),
            },
            'atencion': {
                'cola': cola,
                'humano': humano,
                'bot_activo': bot_activo,
                'supervisados': supervisados,
            },
            'repartos': {
                'pendiente': rep_pendiente,
                'en_camino': rep_en_camino,
                'entregado': rep_entregado,
                'total': rep_total_hoy,
            },
        })
