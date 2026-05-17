"""Motor unificado de cálculo de cantidad a pedir por tipo de pedido.

Reemplaza los `if lab_id else` dispersos en compras_dia.py / order_detail
con una matriz de configuración (`TipoPedidoConfig` en DB). Cada tipo de
pedido (REPOSICION, COMPRA_LAB, CRONOGRAMA_PROG, MODULO, ...) define cómo
se calcula el piso, target, buffer, etc. via JSON en `config_json`.

USO TÍPICO (en compras_dia.py):

    from services.calculo_pedido import calcular_a_pedir, cargar_config

    cfg = cargar_config('REPOSICION')   # cache en memoria, una llamada por proceso
    result = calcular_a_pedir(cfg, {
        'daily_rate': 4.2,
        'min_efectivo': 40,
        'factor_h': 0.5,
        'cubrir_dias': 30,
        'stock_actual': 12,
        'cantidad_reposicion_fija': None,
    })
    # → {'a_pedir': 28, 'ideal': 40, 'regla_usada': 'piso_min_efectivo', ...}

ENUMS válidos por llave del `config_json`:

  piso_ideal:        'min_efectivo' | 'daily_rate_x_cubrir_dias' | 'cero'
  target_horizonte:  'factor_h' | 'cubrir_dias_config' | 'none'
  buffer_pct:        int 0..100
  universo:          'bajo_min_o_cobertura' | 'lab_x' | 'modulo_x' (informativo,
                     no se aplica acá — usado en la query base de items).
  override_producto: 'cantidad_reposicion_fija' | 'pack_quantity' | 'none'
  redondeo:          'ceil' | 'multiplo_pack' | 'unidad'

Idempotente. Sin side effects. Testeable con dicts puros.

SHADOW LOGGING: durante la migración usamos el helper EN PARALELO al cálculo
hard-coded de compras_dia.py. Cuando `a_pedir` de ambos difiere, se loguea
para investigar antes de cambiar a la matriz como única fuente de verdad.
"""
import json
import logging
import math

logger = logging.getLogger(__name__)

def cargar_config(slug):
    """Lee la config del tipo de pedido por slug. Sin cache — siempre DB.

    Sin cache en memoria porque en Render con múltiples workers gunicorn,
    invalidar_cache() solo limpia el worker que atiende el save, y los demás
    sirven el config viejo indefinidamente. El config cambia raramente y
    la query es barata.

    Devuelve dict con las llaves del config_json. Si el slug no existe en DB
    o la fila está inactiva, devuelve None (el caller decide qué hacer).
    """
    from database import TipoPedidoConfig, get_db
    with get_db() as session:
        row = (session.query(TipoPedidoConfig)
               .filter_by(slug=slug, activo=True).first())
        if not row:
            return None
        try:
            return json.loads(row.config_json or '{}')
        except (ValueError, TypeError):
            logger.warning('Config inválido para tipo_pedido %s', slug)
            return {}


def invalidar_cache():
    """No-op — sin cache en memoria. Se mantiene por compatibilidad con callers."""


def calcular_a_pedir(cfg, ctx):
    """Calcula ideal + a_pedir según la matriz de config.

    Args:
      cfg: dict con las llaves del config_json (ver enums arriba).
      ctx: dict con los datos del producto + contexto:
        - daily_rate (float): u/día estimadas
        - min_efectivo (int): mínimo del producto (corregido o de Observer)
        - factor_h (float): multiplicador horas/24 con piso 0.25
        - cubrir_dias (int): días configurados por el slider del modo lab
        - stock_actual (int): stock actual (ERP)
        - cantidad_reposicion_fija (int|None): override por producto
        - pack_quantity (int|None): tamaño de pack si aplica
        - u12m (int): ventas 12m (para validar si rota)
        - sin_mov (bool): True si sin movimiento 60d

    Returns:
      dict {a_pedir, ideal, regla_usada, override_aplicado}
        a_pedir: int >= 0
        ideal: int >= 0 (la cantidad objetivo antes de restar stock)
        regla_usada: string describiendo qué rama del cálculo se aplicó
        override_aplicado: bool (True si cantidad_reposicion_fija o pack_quantity mandó)
    """
    daily_rate = ctx.get('daily_rate') or 0
    min_efectivo = ctx.get('min_efectivo') or 0
    factor_h = ctx.get('factor_h') or 1.0
    cubrir_dias = ctx.get('cubrir_dias') or 30
    stock = ctx.get('stock_actual') or 0
    cant_fija = ctx.get('cantidad_reposicion_fija')
    pack_qty = ctx.get('pack_quantity')
    u12m = ctx.get('u12m') or 0
    sin_mov = bool(ctx.get('sin_mov'))

    # Sin rotación → nada que pedir, independiente del tipo.
    if u12m == 0 or sin_mov:
        return {'a_pedir': 0, 'ideal': 0,
                'regla_usada': 'sin_rotacion', 'override_aplicado': False}

    # Override por producto. Solo si el override está habilitado en el tipo
    # Y el valor está seteado en el producto Y stock cayó al mínimo.
    override_kind = cfg.get('override_producto', 'none')
    if override_kind == 'cantidad_reposicion_fija' and cant_fija and cant_fija > 0 \
            and stock <= min_efectivo:
        return {'a_pedir': int(cant_fija), 'ideal': int(cant_fija),
                'regla_usada': 'override_cantidad_fija', 'override_aplicado': True}

    # 1) Piso: a qué nivel apuntar como mínimo objetivo.
    piso_kind = cfg.get('piso_ideal', 'min_efectivo')
    if piso_kind == 'min_efectivo':
        piso = min_efectivo
    elif piso_kind == 'daily_rate_x_cubrir_dias':
        _dias = cfg.get('dias_cobertura_fijo') or cubrir_dias
        piso = math.ceil(daily_rate * _dias)
    else:  # 'cero' u otro
        piso = 0

    # 2) Target: cuántos más, para cubrir un horizonte adelante.
    tgt_kind = cfg.get('target_horizonte', 'factor_h')
    if tgt_kind == 'factor_h':
        target_unid = math.ceil(daily_rate * factor_h)
    elif tgt_kind == 'cubrir_dias_config':
        target_unid = math.ceil(daily_rate * cubrir_dias)
    else:  # 'none'
        target_unid = 0

    # 3) Combinar: ideal = mayor de los dos. Si tgt_kind='none' (caso COMPRA_LAB),
    # piso ya contiene la cobertura completa, target_unid=0 no aporta.
    ideal = max(piso, target_unid)

    # 4) Buffer (%) — margen sobre el ideal para no quedar en 0.
    buffer_pct = int(cfg.get('buffer_pct', 0) or 0)
    if buffer_pct:
        ideal = math.ceil(ideal * (1 + buffer_pct / 100))

    # 5) Redondeo final.
    redondeo = cfg.get('redondeo', 'ceil')
    if redondeo == 'multiplo_pack' and pack_qty and pack_qty > 1:
        # Redondear hacia arriba al múltiplo de pack más cercano.
        ideal = math.ceil(ideal / pack_qty) * pack_qty
    # 'ceil' y 'unidad' ya están enteros desde math.ceil de arriba.

    a_pedir = max(0, int(ideal) - int(stock))
    return {'a_pedir': a_pedir, 'ideal': int(ideal),
            'regla_usada': f'piso={piso_kind} target={tgt_kind} buffer={buffer_pct}%',
            'override_aplicado': False}


def shadow_compare(slug, ctx, a_pedir_actual, log_threshold=0):
    """Calcula via matriz y compara contra el valor actual. Loguea si difiere.

    Args:
      slug: tipo de pedido a usar para la matriz (ej 'REPOSICION').
      ctx: contexto (mismo formato que calcular_a_pedir).
      a_pedir_actual: el valor que calculó el código hard-coded.
      log_threshold: solo loguea si |new - old| > threshold. Default 0 (siempre).

    Devuelve el dict de calcular_a_pedir o None si no hay config.
    """
    cfg = cargar_config(slug)
    if not cfg:
        return None
    result = calcular_a_pedir(cfg, ctx)
    diff = abs(result['a_pedir'] - int(a_pedir_actual or 0))
    if diff > log_threshold:
        logger.warning(
            'SHADOW [%s] diff a_pedir: legacy=%s motor=%s | regla=%s | ctx=%s',
            slug, a_pedir_actual, result['a_pedir'],
            result['regla_usada'],
            {k: v for k, v in ctx.items() if k in ('daily_rate', 'min_efectivo',
                                                    'factor_h', 'cubrir_dias',
                                                    'stock_actual', 'cantidad_reposicion_fija')}
        )
    return result
