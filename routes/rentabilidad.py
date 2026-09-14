"""Análisis de rentabilidad por producto.

Qué muestra y con qué criterio está en `docs/rentabilidad.md` — leerlo antes de
tocar los números. Los tres avisos que acompañan cada fila no son decoración:

· `confianza` — antigüedad del costo. Es el indicador de calidad del margen, NO
  la cobertura: un costo de hace 5 días sirve aunque se haya comprado 1 y
  vendido 19; uno de hace 90 días no sirve aunque la cobertura sea del 100%.
· `cobertura` — qué parte de lo vendido tiene su compra registrada. Avisa sobre
  la ganancia del período en $, que sí queda incompleta.
· `sospecha_unidad` — la unidad de compra no parece ser la de venta (se compra
  por caja y se vende suelto), así que el margen no significa nada.
"""
from __future__ import annotations

from datetime import date, timedelta

from flask import abort, render_template, request
from flask_login import login_required

from database import get_db
from services.inflacion import factores_a_hoy
from services.rentabilidad import detalle, ranking

# El histórico de compras arranca el 2026-07-01 (antes no se cargaban con
# detalle), así que un default más largo no agrega nada y sí tarda más.
MESES_DEFAULT = 3


def _parse_fecha(s):
    from datetime import datetime
    s = (s or '').strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except ValueError:
        return None


def init_app(app):

    @app.route('/rentabilidad')
    @login_required
    def rentabilidad_ranking():
        hoy = date.today()
        desde = _parse_fecha(request.args.get('desde')) or (hoy - timedelta(days=30 * MESES_DEFAULT))
        hasta = _parse_fecha(request.args.get('hasta'))
        solo_vendidos = request.args.get('todos') != '1'
        q = (request.args.get('q') or '').strip().lower()

        with get_db() as session:
            filas = ranking(session, desde=desde, hasta=hasta,
                            factores=factores_a_hoy(session),
                            solo_vendidos=solo_vendidos)
        if q:
            filas = [f for f in filas
                     if q in (f['descripcion'] or '').lower()
                     or q in (f['ean'] or '')
                     or q in (f['laboratorio'] or '').lower()]

        resumen = {
            'productos': len(filas),
            'facturacion': sum(f['facturacion'] for f in filas),
            'negativos': sum(1 for f in filas
                             if f['margen_pct'] is not None and f['margen_pct'] < 0
                             and not f['sospecha_unidad']),
            'sospechosos': sum(1 for f in filas if f['sospecha_unidad']),
            'costo_viejo': sum(1 for f in filas if f['confianza'] == 'no_decidir'),
            'sobreprecio': sum(f['sobreprecio'] for f in filas),
            # Si la mayoría de la facturación no tiene importe_neto, el margen
            # está inflado y hay que decirlo, no dejarlo en la letra chica.
            'neto_pct': (sum(f['neto_pct'] * f['facturacion'] for f in filas)
                         / sum(f['facturacion'] for f in filas)
                         if sum(f['facturacion'] for f in filas) else 0),
        }
        return render_template('rentabilidad.html', filas=filas, resumen=resumen,
                               desde=desde.isoformat() if desde else '',
                               hasta=hasta.isoformat() if hasta else '',
                               q=request.args.get('q') or '',
                               solo_vendidos=solo_vendidos, hoy=hoy)

    @app.route('/rentabilidad/<ean>')
    @login_required
    def rentabilidad_detalle(ean):
        hoy = date.today()
        desde = _parse_fecha(request.args.get('desde')) or (hoy - timedelta(days=30 * MESES_DEFAULT))
        hasta = _parse_fecha(request.args.get('hasta'))

        with get_db() as session:
            d = detalle(session, ean, desde=desde, hasta=hasta,
                       factores=factores_a_hoy(session), hoy=hoy)
        if d is None:
            abort(404, description=f'No hay compras registradas del EAN {ean}.')
        return render_template('rentabilidad_detalle.html', d=d,
                               desde=desde.isoformat() if desde else '',
                               hasta=hasta.isoformat() if hasta else '')
