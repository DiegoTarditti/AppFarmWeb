"""Admin de tipo_pedido_config — dos categorías:
  'pedido': REPOSICION, COMPRA_LAB, ... (reglas de cálculo de cantidad)
  'flag':   DISCONTINUADO, REEMPLAZADO, SIN_DESCUENTO, NOTA (comportamientos excepcionales)
"""
import json

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from database import TipoPedidoConfig, get_db
from services.calculo_pedido import calcular_a_pedir, invalidar_cache

ENUMS_PEDIDO = {
    'piso_ideal': [
        ('min_efectivo',             'Mínimo efectivo del producto'),
        ('daily_rate_x_cubrir_dias', 'Tasa diaria × días a cubrir (slider)'),
        ('cero',                     'Sin piso (target manda)'),
    ],
    'target_horizonte': [
        ('factor_h',           'Hasta el próximo cierre (horas/24)'),
        ('cubrir_dias_config', 'Días configurados (slider del armado)'),
        ('none',               'Sin target adicional'),
    ],
    'universo': [
        ('bajo_min_o_cobertura', 'Stock bajo mínimo o cobertura insuficiente'),
        ('lab_x',               'Productos de un laboratorio específico'),
        ('modulo_x',            'Productos de un módulo'),
        ('manual',              'Selección manual del operador'),
        ('oferta',              'Productos de un archivo de oferta'),
    ],
    'override_producto': [
        ('cantidad_reposicion_fija', 'Producto.cantidad_reposicion_fija'),
        ('pack_quantity',            'Tamaño de pack (Producto.pack_quantity)'),
        ('none',                     'Sin override'),
    ],
    'redondeo': [
        ('ceil',         'Hacia arriba al entero'),
        ('round',        'Al entero más cercano'),
        ('multiplo_pack','Hacia arriba al múltiplo de pack'),
        ('unidad',       'Sin redondeo extra (unidad)'),
    ],
    # ── Ejes nuevos (boceto config_pedidos) ──
    'base_demanda': [
        ('u3m',             'Recientes (últimos 3 meses)'),
        ('u12m',            'Año completo (12 meses)'),
        ('u12m_estacional', 'Estacional (12m × índice de la droga)'),
    ],
    'cant_fija_efecto': [
        ('override', 'Gana: pide esa cantidad fija'),
        ('piso',     'Es un piso (nunca pide menos)'),
        ('ninguno',  'Ignorar la cantidad fija'),
    ],
    'oferta_min_efecto': [
        ('piso',      'Subir al mínimo de oferta'),
        ('indicador', 'Solo avisar (chip), no toca cantidad'),
        ('ninguno',   'Ignorar el mínimo de oferta'),
    ],
}

ENUMS_FLAG = {
    'efecto_armado': [
        ('excluir',    'Excluir del armado (no aparece)'),
        ('badge_cero', 'Badge + a_pedir forzado a 0'),
        ('solo_badge', 'Solo badge informativo (no afecta cantidad)'),
        ('ninguno',    'Sin efecto visual en el armado'),
    ],
    'color': [
        ('red',    'Rojo'),
        ('amber',  'Ámbar'),
        ('violet', 'Violeta'),
        ('sky',    'Celeste'),
        ('gray',   'Gris'),
    ],
}


def _parse_cfg(row):
    try:
        return json.loads(row.config_json or '{}')
    except (ValueError, TypeError):
        return {}


def _row_to_dict(r):
    return {
        'id': r.id, 'slug': r.slug, 'nombre': r.nombre,
        'descripcion': r.descripcion or '', 'activo': r.activo,
        'categoria': getattr(r, 'categoria', 'pedido') or 'pedido',
        'config': _parse_cfg(r),
        'actualizado_en': r.actualizado_en.strftime('%d/%m/%Y %H:%M')
                          if r.actualizado_en else '',
    }


def init_app(app):

    @app.route('/config/tipos-pedido')
    @login_required
    def tipos_pedido_list():
        with get_db() as session:
            rows = (session.query(TipoPedidoConfig)
                    .order_by(TipoPedidoConfig.slug).all())
            pedidos = [_row_to_dict(r) for r in rows
                       if (getattr(r, 'categoria', 'pedido') or 'pedido') == 'pedido']
            flags   = [_row_to_dict(r) for r in rows
                       if (getattr(r, 'categoria', 'pedido') or 'pedido') == 'flag']
        return render_template('tipos_pedido_list.html', pedidos=pedidos, flags=flags)

    @app.route('/config/tipos-pedido/<slug>/edit', methods=['GET', 'POST'])
    @login_required
    def tipos_pedido_edit(slug):
        with get_db() as session:
            row = session.query(TipoPedidoConfig).filter_by(slug=slug).first()
            if not row:
                flash(f'Tipo "{slug}" no encontrado.', 'error')
                return redirect(url_for('tipos_pedido_list'))

            categoria = getattr(row, 'categoria', 'pedido') or 'pedido'

            if request.method == 'POST':
                cfg_actual = _parse_cfg(row)
                if categoria == 'flag':
                    cfg_nuevo = {
                        'efecto_armado':    request.form.get('efecto_armado') or cfg_actual.get('efecto_armado', 'solo_badge'),
                        'icono':            (request.form.get('icono') or cfg_actual.get('icono', '📝')).strip(),
                        'color':            request.form.get('color') or cfg_actual.get('color', 'gray'),
                        'permite_reemplazo': request.form.get('permite_reemplazo') == '1',
                        'permite_vigencia':  request.form.get('permite_vigencia') == '1',
                    }
                else:
                    _dias_raw = request.form.get('dias_cobertura_fijo', '').strip()
                    _dias = int(_dias_raw) if _dias_raw.isdigit() else None
                    cfg_nuevo = {
                        'piso_ideal':          request.form.get('piso_ideal') or cfg_actual.get('piso_ideal', 'min_efectivo'),
                        'target_horizonte':    request.form.get('target_horizonte') or cfg_actual.get('target_horizonte', 'factor_h'),
                        'buffer_pct':          max(0, min(100, int(request.form.get('buffer_pct') or 0))),
                        'universo':            request.form.get('universo') or cfg_actual.get('universo', 'bajo_min_o_cobertura'),
                        'override_producto':   request.form.get('override_producto') or cfg_actual.get('override_producto', 'none'),
                        'redondeo':            request.form.get('redondeo') or cfg_actual.get('redondeo', 'ceil'),
                        'dias_cobertura_fijo': _dias,
                        # Ejes nuevos (defaults = comportamiento histórico).
                        'base_demanda':        request.form.get('base_demanda') or cfg_actual.get('base_demanda', 'u3m'),
                        'cant_fija_efecto':    request.form.get('cant_fija_efecto') or cfg_actual.get('cant_fija_efecto', 'override'),
                        'oferta_min_efecto':   request.form.get('oferta_min_efecto') or cfg_actual.get('oferta_min_efecto', 'piso'),
                    }
                row.nombre      = (request.form.get('nombre') or row.nombre).strip()
                row.descripcion = (request.form.get('descripcion') or '').strip() or None
                row.config_json = json.dumps(cfg_nuevo)
                row.activo      = request.form.get('activo') == '1'
                session.commit()
                invalidar_cache()
                flash(f'"{slug}" actualizado.')
                return redirect(url_for('tipos_pedido_list'))

            tipo = _row_to_dict(row)
        return render_template('tipos_pedido_edit.html', tipo=tipo,
                               enums=ENUMS_PEDIDO, enums_flag=ENUMS_FLAG)

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
        body = request.get_json(silent=True) or {}
        cfg  = body.get('cfg') or {}
        ctx  = body.get('ctx') or {}
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
