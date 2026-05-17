"""Estacionalidad por droga.

Calcula el patron mensual (12 indices) de cada monodroga basado en
`obs_ventas_mensuales` (unidades). Aplica shrinkage bayesiano hacia el
patron del subrubro de la droga para drogas con poca historia, para que
las recien-vendidas no se vean dominadas por el ruido de 1-2 meses.

Indice m = avg_unidades_mes_m / promedio_global (escalado a 1.0 = neutro).
CV (coef. variacion) = stdev(indices) / mean(indices) para ordenar por
"mas estacional".
"""

import json
import os
from collections import defaultdict
from statistics import mean, stdev

from flask import jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import text as _text

from database import (
    EstacionalidadEscenario,
    ObsNombreDroga,
    ObsSubrubro,
    get_db,
)

K_PRIOR = 12

# Si un subrubro agrupa mas de N drogas distintas, lo consideramos
# demasiado heterogeneo (caso "Medicamentos" 40k productos, "Perfumeria" 58k)
# y el patron del grupo no aporta senal real. En esos casos no hacemos
# pooling y dejamos el patron crudo de la droga.
HETEROGENEIDAD_MAX_DROGAS = 30


def _escenario_a_dict(e):
    return {
        'id': e.id,
        'droga_id': e.droga_id,
        'nombre': e.nombre,
        'indices': json.loads(e.indices_json),
        'lead_time_dias': int(e.lead_time_dias or 0),
        'cobertura_dias': int(e.cobertura_dias or 30),
        'es_default': bool(e.es_default),
        'creado_por': e.creado_por,
        'actualizado_en': e.actualizado_en.isoformat() if e.actualizado_en else None,
    }


def _validar_payload_escenario(payload):
    """Devuelve (ok, error_msg, datos_normalizados)."""
    nombre = (payload.get('nombre') or 'base').strip()[:60]
    if not nombre:
        return False, 'El nombre del escenario no puede estar vacio.', None
    indices = payload.get('indices')
    if not isinstance(indices, list) or len(indices) != 12:
        return False, 'Se esperan 12 indices mensuales.', None
    try:
        indices = [max(0.0, float(v)) for v in indices]
    except (TypeError, ValueError):
        return False, 'Los indices deben ser numericos.', None
    try:
        # Limites centralizados en services/pedido_estacional.LIMITES.
        from services.pedido_estacional import LIMITES
        lead = max(
            LIMITES['lead_dias_piso'],
            min(LIMITES['lead_dias_max'],
                int(payload.get('lead_time_dias', LIMITES['lead_dias_default']))),
        )
    except (TypeError, ValueError):
        return False, 'lead_time_dias invalido.', None
    try:
        cob = max(
            LIMITES['cob_dias_min'],
            min(LIMITES['cob_dias_max'],
                int(payload.get('cobertura_dias', LIMITES['cob_dias_default']))),
        )
    except (TypeError, ValueError):
        return False, 'cobertura_dias invalida.', None
    es_default = bool(payload.get('es_default', False))
    return True, None, {
        'nombre': nombre,
        'indices': indices,
        'lead_time_dias': lead,
        'cobertura_dias': cob,
        'es_default': es_default,
    }


def _calcular_estacionalidad_droga(meses_data, indice_grupo=None):
    """Calcula los 12 indices estacionales (1.0 = neutro) de una droga.

    Args:
        meses_data: dict[mes(1-12) -> list[unidades]] con todas las
            observaciones de esa droga (una entrada por anio observado).
        indice_grupo: dict[mes(1-12) -> indice] opcional. Patron del subrubro
            al que pertenece la droga. Si se pasa, se hace shrinkage.

    Returns:
        dict con: indices[12], cv, n_obs, lambda_shrink, pooled (bool).
        None si la droga no tiene suficientes meses observados (<6).
    """
    n_obs = sum(len(v) for v in meses_data.values())

    avg_mes = {}
    for m in range(1, 13):
        vals = meses_data.get(m) or []
        avg_mes[m] = (sum(vals) / len(vals)) if vals else None

    obs_meses = [v for v in avg_mes.values() if v is not None and v > 0]
    if len(obs_meses) < 6:
        return None
    media_global = sum(obs_meses) / len(obs_meses)
    if media_global <= 0:
        return None

    indices_crudos = {
        m: (avg_mes[m] / media_global) if avg_mes[m] is not None else None
        for m in range(1, 13)
    }

    lam = n_obs / (n_obs + K_PRIOR)
    pooled = indice_grupo is not None

    indices_final = []
    for m in range(1, 13):
        crudo = indices_crudos[m]
        grupo = indice_grupo.get(m) if indice_grupo else None
        if crudo is None and grupo is None:
            indices_final.append(1.0)
        elif crudo is None:
            indices_final.append(grupo)
        elif grupo is None or not pooled:
            indices_final.append(crudo)
        else:
            indices_final.append(lam * crudo + (1 - lam) * grupo)

    obs_final = [
        indices_final[m - 1] for m in range(1, 13) if indices_crudos[m] is not None
    ]
    if len(obs_final) > 1 and mean(obs_final) > 0:
        cv = stdev(obs_final) / mean(obs_final)
    else:
        cv = 0.0

    return {
        'indices': indices_final,
        'cv': cv,
        'n_obs': n_obs,
        'lambda_shrink': lam,
        'pooled': pooled,
    }


def _calcular_indice_subrubro(meses):
    """Patron estacional de un subrubro (agregado de todas sus drogas).

    Args:
        meses: dict[mes(1-12) -> list[unidades]] con todas las
            observaciones de productos del subrubro.

    Returns:
        dict[mes -> indice] o None si no hay datos suficientes.
    """
    avg_mes = {}
    for m in range(1, 13):
        vals = meses.get(m) or []
        if vals:
            avg_mes[m] = sum(vals) / len(vals)
    obs = [v for v in avg_mes.values() if v > 0]
    if len(obs) < 6:
        return None
    media = sum(obs) / len(obs)
    if media <= 0:
        return None
    return {m: (avg_mes[m] / media if m in avg_mes else 1.0) for m in range(1, 13)}


def init_app(app):

    @app.route('/informes/estacionalidad-drogas')
    @login_required
    def informe_estacionalidad_drogas():
        q = (request.args.get('q') or '').strip()
        try:
            min_anios = max(1, int(request.args.get('min_anios', '1')))
        except ValueError:
            min_anios = 1
        try:
            min_u12m = max(0.0, float(request.args.get('min_u12m', '0')))
        except ValueError:
            min_u12m = 0.0
        orden = request.args.get('orden', 'cv')
        if orden not in ('cv', 'u12m', 'nombre'):
            orden = 'cv'
        try:
            page = max(1, int(request.args.get('page', '1')))
        except ValueError:
            page = 1
        per_page = 50

        id_farmacia = int(os.environ.get('OBSERVER_ID_FARMACIA', '10525'))

        with get_db() as session:
            sql = _text("""
                SELECT
                  p.nombre_droga_observer AS droga_id,
                  p.subrubro_observer     AS subrubro_id,
                  v.anio                  AS anio,
                  v.mes                   AS mes,
                  SUM(v.unidades)         AS unidades
                FROM obs_productos p
                JOIN obs_ventas_mensuales v ON v.producto_observer = p.observer_id
                WHERE p.fecha_baja IS NULL
                  AND p.nombre_droga_observer IS NOT NULL
                  AND v.id_farmacia = :fid
                  AND v.unidades > 0
                GROUP BY p.nombre_droga_observer, p.subrubro_observer, v.anio, v.mes
            """)
            rows = session.execute(sql, {'fid': id_farmacia}).fetchall()

            por_droga_meses = defaultdict(lambda: defaultdict(list))
            por_subrubro_meses = defaultdict(lambda: defaultdict(list))
            drogas_por_subrubro = defaultdict(set)
            droga_subrubro_votos = defaultdict(lambda: defaultdict(float))
            droga_anios = defaultdict(set)
            droga_total = defaultdict(float)

            for r in rows:
                droga = r.droga_id
                u = float(r.unidades or 0)
                por_droga_meses[droga][r.mes].append(u)
                droga_anios[droga].add(r.anio)
                droga_total[droga] += u
                if r.subrubro_id:
                    droga_subrubro_votos[droga][r.subrubro_id] += u
                    por_subrubro_meses[r.subrubro_id][r.mes].append(u)
                    drogas_por_subrubro[r.subrubro_id].add(droga)

            droga_subrubro = {
                d: max(votos, key=votos.get)
                for d, votos in droga_subrubro_votos.items()
            }

            # Pooling adaptativo: subrubros con muchas drogas distintas son
            # "ruidosos" y su patron promedio aporta poca senal. Los marcamos
            # como heterogeneos y para las drogas que pertenecen a uno, NO
            # hacemos pooling (el patron crudo es preferible).
            subrubros_heterogeneos = {
                sr for sr, drogas in drogas_por_subrubro.items()
                if len(drogas) > HETEROGENEIDAD_MAX_DROGAS
            }
            tamano_subrubro = {
                sr: len(drogas) for sr, drogas in drogas_por_subrubro.items()
            }

            indice_subrubro = {}
            for sr, meses in por_subrubro_meses.items():
                if sr in subrubros_heterogeneos:
                    continue
                idx = _calcular_indice_subrubro(meses)
                if idx is not None:
                    indice_subrubro[sr] = idx

            droga_ids = list(por_droga_meses.keys())
            nombres = (
                dict(
                    session.query(ObsNombreDroga.observer_id, ObsNombreDroga.descripcion)
                    .filter(ObsNombreDroga.observer_id.in_(droga_ids))
                    .all()
                )
                if droga_ids
                else {}
            )

            escenarios_default = {}
            if droga_ids:
                for e in (session.query(EstacionalidadEscenario)
                          .filter(EstacionalidadEscenario.droga_id.in_(droga_ids),
                                  EstacionalidadEscenario.es_default.is_(True))
                          .all()):
                    escenarios_default[e.droga_id] = e.nombre

            subrubro_ids = list(indice_subrubro.keys())
            nombres_sr = (
                dict(
                    session.query(ObsSubrubro.observer_id, ObsSubrubro.descripcion)
                    .filter(ObsSubrubro.observer_id.in_(subrubro_ids))
                    .all()
                )
                if subrubro_ids
                else {}
            )

            resultado = []
            for droga in droga_ids:
                anios = len(droga_anios[droga])
                if anios < min_anios:
                    continue
                if droga_total[droga] < min_u12m:
                    continue
                nombre = nombres.get(droga) or f'#{droga}'
                if q and q.lower() not in nombre.lower():
                    continue

                sr = droga_subrubro.get(droga)
                grupo_idx = indice_subrubro.get(sr) if sr else None
                est = _calcular_estacionalidad_droga(por_droga_meses[droga], grupo_idx)
                if est is None:
                    continue

                if anios >= 3:
                    confianza = 'alta'
                elif anios == 2:
                    confianza = 'media'
                else:
                    confianza = 'baja'

                razon_sin_pool = None
                if not est['pooled']:
                    if sr and sr in subrubros_heterogeneos:
                        razon_sin_pool = 'subrubro_heterogeneo'
                    elif not sr:
                        razon_sin_pool = 'sin_subrubro'
                    else:
                        razon_sin_pool = 'sin_data_grupo'

                resultado.append({
                    'droga_id': droga,
                    'nombre': nombre,
                    'indices': est['indices'],
                    'cv': est['cv'],
                    'anios': anios,
                    'confianza': confianza,
                    'u12m': droga_total[droga],
                    'pooled': est['pooled'],
                    'lambda_shrink': est['lambda_shrink'],
                    'subrubro_nombre': nombres_sr.get(sr) if sr else None,
                    'n_drogas_grupo': tamano_subrubro.get(sr, 0) if sr else 0,
                    'razon_sin_pool': razon_sin_pool,
                    'escenario_default': escenarios_default.get(droga),
                })

            if orden == 'cv':
                resultado.sort(key=lambda x: x['cv'], reverse=True)
            elif orden == 'u12m':
                resultado.sort(key=lambda x: x['u12m'], reverse=True)
            else:
                resultado.sort(key=lambda x: x['nombre'])

        total = len(resultado)
        last_page = max(1, (total + per_page - 1) // per_page)
        page = min(page, last_page)
        offset = (page - 1) * per_page
        resultado_pag = resultado[offset:offset + per_page]

        meses_es = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun',
                    'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']

        from services.pedido_estacional import LIMITES as _LIMITES
        return render_template(
            'estacionalidad_drogas.html',
            drogas=resultado_pag,
            total=total,
            page=page,
            last_page=last_page,
            q=q,
            min_anios=min_anios,
            min_u12m=min_u12m,
            orden=orden,
            per_page=per_page,
            meses_es=meses_es,
            limites=_LIMITES,
        )

    @app.route('/api/estacionalidad/droga/<int:droga_id>')
    @login_required
    def api_estacionalidad_droga(droga_id):
        """Serie mensual por anio para el chart de detalle (no agregada)."""
        id_farmacia = int(os.environ.get('OBSERVER_ID_FARMACIA', '10525'))

        with get_db() as session:
            sql = _text("""
                SELECT v.anio AS anio, v.mes AS mes, SUM(v.unidades) AS unidades
                FROM obs_productos p
                JOIN obs_ventas_mensuales v ON v.producto_observer = p.observer_id
                WHERE p.fecha_baja IS NULL
                  AND p.nombre_droga_observer = :d
                  AND v.id_farmacia = :fid
                GROUP BY v.anio, v.mes
                ORDER BY v.anio, v.mes
            """)
            rows = session.execute(sql, {'d': droga_id, 'fid': id_farmacia}).fetchall()

            nombre = session.query(ObsNombreDroga.descripcion).filter_by(
                observer_id=droga_id).scalar() or f'#{droga_id}'

        por_anio = defaultdict(lambda: [0.0] * 12)
        for r in rows:
            por_anio[r.anio][r.mes - 1] = float(r.unidades or 0)

        return jsonify({
            'droga_id': droga_id,
            'nombre': nombre,
            'series': [{'anio': a, 'unidades': por_anio[a]} for a in sorted(por_anio.keys())],
        })

    @app.route('/api/estacionalidad/droga/<int:droga_id>/escenarios', methods=['GET'])
    @login_required
    def api_escenarios_listar(droga_id):
        with get_db() as session:
            escenarios = (session.query(EstacionalidadEscenario)
                          .filter_by(droga_id=droga_id)
                          .order_by(EstacionalidadEscenario.es_default.desc(),
                                    EstacionalidadEscenario.nombre)
                          .all())
            return jsonify({
                'droga_id': droga_id,
                'escenarios': [_escenario_a_dict(e) for e in escenarios],
            })

    @app.route('/api/estacionalidad/droga/<int:droga_id>/escenarios', methods=['POST'])
    @login_required
    def api_escenarios_crear_o_actualizar(droga_id):
        """Upsert por (droga_id, nombre). Si es_default=True, des-marca los otros."""
        payload = request.get_json(silent=True) or {}
        ok, err, datos = _validar_payload_escenario(payload)
        if not ok:
            return jsonify({'error': err}), 400

        with get_db() as session:
            droga = session.query(ObsNombreDroga.observer_id).filter_by(
                observer_id=droga_id).first()
            if not droga:
                return jsonify({'error': 'Droga inexistente.'}), 404

            existente = (session.query(EstacionalidadEscenario)
                         .filter_by(droga_id=droga_id, nombre=datos['nombre'])
                         .first())
            if existente:
                existente.indices_json = json.dumps(datos['indices'])
                existente.lead_time_dias = datos['lead_time_dias']
                existente.cobertura_dias = datos['cobertura_dias']
                if datos['es_default']:
                    existente.es_default = True
                esc = existente
            else:
                esc = EstacionalidadEscenario(
                    droga_id=droga_id,
                    nombre=datos['nombre'],
                    indices_json=json.dumps(datos['indices']),
                    lead_time_dias=datos['lead_time_dias'],
                    cobertura_dias=datos['cobertura_dias'],
                    es_default=datos['es_default'],
                    creado_por=getattr(current_user, 'username', None),
                )
                session.add(esc)
                session.flush()

            if datos['es_default']:
                (session.query(EstacionalidadEscenario)
                 .filter(EstacionalidadEscenario.droga_id == droga_id,
                         EstacionalidadEscenario.id != esc.id)
                 .update({'es_default': False}))

            session.commit()
            session.refresh(esc)
            return jsonify(_escenario_a_dict(esc))

    @app.route('/api/estacionalidad/droga/<int:droga_id>/escenarios/<int:esc_id>',
               methods=['DELETE'])
    @login_required
    def api_escenarios_eliminar(droga_id, esc_id):
        with get_db() as session:
            esc = (session.query(EstacionalidadEscenario)
                   .filter_by(id=esc_id, droga_id=droga_id).first())
            if not esc:
                return jsonify({'error': 'Escenario inexistente.'}), 404
            session.delete(esc)
            session.commit()
            return jsonify({'ok': True})

    @app.route('/api/estacionalidad/droga/<int:droga_id>/escenarios/<int:esc_id>/default',
               methods=['POST'])
    @login_required
    def api_escenarios_marcar_default(droga_id, esc_id):
        with get_db() as session:
            esc = (session.query(EstacionalidadEscenario)
                   .filter_by(id=esc_id, droga_id=droga_id).first())
            if not esc:
                return jsonify({'error': 'Escenario inexistente.'}), 404
            (session.query(EstacionalidadEscenario)
             .filter(EstacionalidadEscenario.droga_id == droga_id)
             .update({'es_default': False}))
            esc.es_default = True
            session.commit()
            session.refresh(esc)
            return jsonify(_escenario_a_dict(esc))
