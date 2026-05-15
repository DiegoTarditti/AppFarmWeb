"""Admin de la matriz `tipo_pedido_config` — config de cómo se calcula
cantidad a pedir según el contexto (REPOSICION, COMPRA_LAB, ...).

Centraliza las reglas que vivían dispersas en `if lab_id else` por el código.
Cada fila define piso/target/buffer/override/redondeo. El motor en
`services/calculo_pedido.py` lee de acá.
"""
import json

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from database import TipoPedidoConfig, get_db
from services.calculo_pedido import calcular_a_pedir, invalidar_cache

# Enums válidos por llave del config_json. Single source of truth para los
# selects del form. Si agregás un nuevo valor, hay que mapearlo también en
# `services/calculo_pedido.py:calcular_a_pedir`.
ENUMS = {
    'piso_ideal': [
        ('min_efectivo',               'Mínimo efectivo del producto'),
        ('daily_rate_x_cubrir_dias',   'Tasa diaria × días a cubrir (slider)'),
        ('cero',                       'Sin piso (target manda)'),
    ],
    'target_horizonte': [
        ('factor_h',                   'Hasta el próximo cierre (horas/24)'),
        ('cubrir_dias_config',         'Días configurados (slider del armado)'),
        ('none',                       'Sin target adicional'),
    ],
    'universo': [
        ('bajo_min_o_cobertura',       'Stock bajo mínimo o cobertura insuficiente'),
        ('lab_x',                      'Productos de un laboratorio específico'),
        ('modulo_x',                   'Productos de un módulo'),
        ('manual',                     'Selección manual del operador'),
        ('oferta',                     'Productos de un archivo de oferta'),
    ],
    'override_producto': [
        ('cantidad_reposicion_fija',   'Producto.cantidad_reposicion_fija'),
        ('pack_quantity',              'Tamaño de pack (Producto.pack_quantity)'),
        ('none',                       'Sin override'),
    ],
    'redondeo': [
        ('ceil',                       'Hacia arriba al entero'),
        ('multiplo_pack',              'Hacia arriba al múltiplo de pack'),
        ('unidad',                     'Sin redondeo extra (unidad)'),
    ],
}

DEFAULT_CONFIG = {
    'piso_ideal': 'min_efectivo',
    'target_horizonte': 'factor_h',
    'buffer_pct': 0,
    'universo': 'bajo_min_o_cobertura',
    'override_producto': 'none',
    'redondeo': 'ceil',
}


def _parse_cfg(row):
    try:
        return json.loads(row.config_json or '{}')
    except (ValueError, TypeError):
        return {}


def init_app(app):

    @app.route('/config/tipos-pedido')
    @login_required
    def tipos_pedido_list():
        with get_db() as session:
            rows = (session.query(TipoPedidoConfig)
                    .order_by(TipoPedidoConfig.slug).all())
            tipos = [{
                'id': r.id,
                'slug': r.slug,
                'nombre': r.nombre,
                'descripcion': r.descripcion or '',
                'activo': r.activo,
                'config': _parse_cfg(r),
                'actualizado_en': r.actualizado_en.strftime('%d/%m/%Y %H:%M')
                                  if r.actualizado_en else '',
            } for r in rows]
        return render_template('tipos_pedido_list.html', tipos=tipos)

    @app.route('/config/tipos-pedido/<slug>/edit', methods=['GET', 'POST'])
    @login_required
    def tipos_pedido_edit(slug):
        with get_db() as session:
            row = (session.query(TipoPedidoConfig).filter_by(slug=slug).first())
            if not row:
                flash(f'Tipo de pedido "{slug}" no encontrado.', 'error')
                return redirect(url_for('tipos_pedido_list'))

            if request.method == 'POST':
                cfg_actual = _parse_cfg(row)
                _dias_fijo_raw = request.form.get('dias_cobertura_fijo', '').strip()
                _dias_fijo = int(_dias_fijo_raw) if _dias_fijo_raw.isdigit() else None
                cfg_nuevo = {
                    'piso_ideal':          request.form.get('piso_ideal') or cfg_actual.get('piso_ideal', 'min_efectivo'),
                    'target_horizonte':    request.form.get('target_horizonte') or cfg_actual.get('target_horizonte', 'factor_h'),
                    'buffer_pct':          max(0, min(100, int(request.form.get('buffer_pct') or 0))),
                    'universo':            request.form.get('universo') or cfg_actual.get('universo', 'bajo_min_o_cobertura'),
                    'override_producto':   request.form.get('override_producto') or cfg_actual.get('override_producto', 'none'),
                    'redondeo':            request.form.get('redondeo') or cfg_actual.get('redondeo', 'ceil'),
                    'dias_cobertura_fijo': _dias_fijo,
                }
                # Validar enums
                for k, v in cfg_nuevo.items():
                    if k == 'buffer_pct':
                        continue
                    valid = {opt for opt, _ in ENUMS.get(k, [])}
                    if v not in valid:
                        flash(f'Valor inválido para {k}: {v}', 'error')
                        return redirect(url_for('tipos_pedido_edit', slug=slug))
                row.nombre = (request.form.get('nombre') or row.nombre).strip()
                row.descripcion = (request.form.get('descripcion') or '').strip() or None
                row.config_json = json.dumps(cfg_nuevo)
                row.activo = request.form.get('activo') == '1'
                session.commit()
                invalidar_cache()
                flash(f'Tipo "{slug}" actualizado.')
                return redirect(url_for('tipos_pedido_list'))

            tipo = {
                'id': row.id, 'slug': row.slug, 'nombre': row.nombre,
                'descripcion': row.descripcion or '', 'activo': row.activo,
                'config': _parse_cfg(row),
            }
        return render_template('tipos_pedido_edit.html', tipo=tipo, enums=ENUMS)

    @app.route('/config/tipos-pedido/<slug>/toggle', methods=['POST'])
    @login_required
    def tipos_pedido_toggle(slug):
        with get_db() as session:
            row = session.query(TipoPedidoConfig).filter_by(slug=slug).first()
            if not row:
                return jsonify({'ok': False, 'error': 'no encontrado'}), 404
            row.activo = not row.activo
            session.commit()
            invalidar_cache()
            return jsonify({'ok': True, 'activo': row.activo})

    @app.route('/config/tipos-pedido/<slug>/probar', methods=['POST'])
    @login_required
    def tipos_pedido_probar(slug):
        """Simula calcular_a_pedir con un ctx ficticio + la config del form.

        Body JSON: {cfg: {...}, ctx: {...}}. La cfg viene del form en edición
        sin guardar todavía, así el user ve el efecto antes de commit.
        """
        body = request.get_json(silent=True) or {}
        cfg = body.get('cfg') or {}
        ctx = body.get('ctx') or {}
        # Defaults razonables para no romper si faltan campos
        ctx.setdefault('daily_rate', 4)
        ctx.setdefault('min_efectivo', 40)
        ctx.setdefault('factor_h', 0.5)
        ctx.setdefault('cubrir_dias', 30)
        ctx.setdefault('stock_actual', 10)
        ctx.setdefault('cantidad_reposicion_fija', None)
        ctx.setdefault('pack_quantity', None)
        ctx.setdefault('u12m', 48)
        ctx.setdefault('sin_mov', False)
        try:
            result = calcular_a_pedir(cfg, ctx)
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 400
        return jsonify({'ok': True, 'result': result, 'ctx_usado': ctx})
