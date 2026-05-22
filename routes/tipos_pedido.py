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

# Config base por tipo (espejo del seed en database.py::init_db _seed_tipos).
# Usado por "Restaurar a base". Si cambiás el seed, actualizá acá también.
BASE_CONFIGS = {
    'REPOSICION': {'piso_ideal': 'daily_rate_x_cubrir_dias', 'target_horizonte': 'none',
                   'buffer_pct': 0, 'universo': 'bajo_min_o_cobertura',
                   'override_producto': 'cantidad_reposicion_fija', 'redondeo': 'ceil',
                   'dias_cobertura_fijo': 4, 'base_demanda': 'u3m',
                   'cant_fija_efecto': 'override', 'oferta_min_efecto': 'piso',
                   'valor_piso': 0},
    'COMPRA_LAB': {'piso_ideal': 'daily_rate_x_cubrir_dias', 'target_horizonte': 'none',
                   'buffer_pct': 0, 'universo': 'lab_x',
                   'override_producto': 'none', 'redondeo': 'ceil',
                   'base_demanda': 'u3m', 'cant_fija_efecto': 'override',
                   'oferta_min_efecto': 'piso', 'valor_piso': 0},
    'PRUEBA':     {'piso_ideal': 'min_efectivo', 'target_horizonte': 'none',
                   'buffer_pct': 0, 'universo': 'manual',
                   'override_producto': 'cantidad_reposicion_fija', 'redondeo': 'ceil',
                   'base_demanda': 'u12m_estacional', 'cant_fija_efecto': 'override',
                   'oferta_min_efecto': 'indicador', 'valor_piso': 0},
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
                    _vp_raw = (request.form.get('valor_piso') or '').strip().replace('.', '').replace(',', '.')
                    try:
                        _valor_piso = max(0.0, float(_vp_raw)) if _vp_raw else float(cfg_actual.get('valor_piso') or 0)
                    except ValueError:
                        _valor_piso = float(cfg_actual.get('valor_piso') or 0)
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
                        # Precio desde el cual el producto es "caro": si rota bajo, repone máx 1. 0 = off.
                        'valor_piso':          _valor_piso,
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
        ctx.setdefault('pvp', 0)
        ctx.setdefault('rotacion', None)
        try:
            result = calcular_a_pedir(cfg, ctx)
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 400
        return jsonify({'ok': True, 'result': result, 'ctx_usado': ctx})

    @app.route('/config/tipos-pedido/sim-producto')
    @login_required
    def tipos_pedido_sim_producto():
        """Datos reales de un producto para alimentar el simulador con un caso
        concreto en vez de números inventados. Devuelve stock/mínimo/ventas
        reales (de ObServer vía producto_metrics) + cantidad fija del master."""
        from database import ObsProducto, Producto
        from services.producto_metrics import metricas_producto
        obs_id = request.args.get('observer_id', type=int)
        if not obs_id:
            return jsonify({'ok': False, 'error': 'falta observer_id'}), 400
        with get_db() as session:
            op = session.get(ObsProducto, obs_id)
            if not op:
                return jsonify({'ok': False, 'error': 'producto no encontrado'}), 404
            m = metricas_producto(session, obs_id)
            prod = session.query(Producto).filter_by(observer_id=obs_id).first()
            cant_fija = (int(prod.cantidad_reposicion_fija)
                         if prod and prod.cantidad_reposicion_fija else None)
            _pvp = float(prod.precio_pvp) if (prod and prod.precio_pvp) else 0.0
            return jsonify({
                'ok': True,
                'nombre': op.descripcion or str(obs_id),
                'avg_3m': m['avg_3m'], 'avg_12m': m['avg_12m'],
                'stock': m['stock'], 'minimo': m['minimo'],
                'cant_fija': cant_fija,
                'u12m': int(sum(m['ventas12'])),
                'sin_historial': m['sin_historial'],
                'pvp': _pvp,
                'rotacion': m.get('rotacion'),  # 'A'|'M'|'B' (para la regla caro+baja)
            })

    @app.route('/config/tipos-pedido/<slug>/restaurar', methods=['POST'])
    @login_required
    def tipos_pedido_restaurar(slug):
        """Restaura la config de un tipo a su BASE conocida-buena (red de
        seguridad si alguien dejó valores raros)."""
        base = BASE_CONFIGS.get(slug)
        if base is None:
            return jsonify({'ok': False, 'error': f'sin base definida para {slug}'}), 400
        with get_db() as session:
            row = session.query(TipoPedidoConfig).filter_by(slug=slug).first()
            if not row:
                return jsonify({'ok': False, 'error': 'no encontrado'}), 404
            row.config_json = json.dumps(base)
            session.commit()
            invalidar_cache()
        return jsonify({'ok': True, 'config': base})
