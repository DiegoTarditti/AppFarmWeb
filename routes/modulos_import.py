"""Importador de módulos de descuento — wizard 4 pasos.

Reusa toda la infraestructura del wizard de ofertas:
- _previsualizar_xlsx / _previsualizar_pdf / _previsualizar_imagen → importados
  desde routes.ofertas_import.
- field_inference para autodetect de columnas.
- producto_matcher para cruzar EANs con catálogo.
- pack_detector (existente) para detectar packs por descripción.

Diferencias clave respecto al de ofertas:
- Modelo destino: Modulo + ModuloPack (no OfertaMinimo).
- Cada item se categoriza como 'pack' (con cantidad y ean_unidad sugerido) o
  'unidad' (relación 1:1).
- Campos esperados: nombre_modulo, ean (o codigo), descripcion, cantidad, descuento_psl.

El endpoint legacy /modulo-packs/importar sigue funcionando como fallback.
"""
import os
import re
import tempfile

from flask import jsonify, render_template, request
from flask_login import login_required

import database
from database import Laboratorio, Modulo, ModuloPack
from routes.ofertas_import import (
    _previsualizar_imagen,
    _previsualizar_pdf,
    _previsualizar_xlsx,
)

# ── Detección de packs (función pura, testeable sin DB) ───────────────────────

_RE_PACK_XN = re.compile(r'\bPACK\s*X\s*(\d+)\b', re.IGNORECASE)
_RE_PACK_PALABRA = re.compile(r'\bPACK\b', re.IGNORECASE)
_RE_ENVASE = re.compile(r'\b[xX]\s*(\d{1,4})\b')


def _ean_o_codigo(it):
    """EAN si tiene; si no, el código del proveedor; None si ninguno."""
    e = (it.get('ean') or '').strip()
    if e:
        return e
    return (it.get('codigo') or '').strip() or None


def detectar_packs_modulos(items, usar_historico=False, sin_ventas_func=None):
    """Detecta packs a partir de los ítems del wizard de módulos.

    Por defecto: SOLO marca pack la fila amarilla del Excel (campo `_destacado`).
    Si `usar_historico=True`, también aplica el combo:
        primero del módulo + descripción contiene "PACK" + nunca vendido por ese código.

    El regex `PACK X N` aporta la cantidad cuando está disponible (no marca por sí solo).
    Heurística envase múltiplo: dentro del mismo módulo, sugiere ean_unidad y cantidad
    cuando el envase de un pack es múltiplo del envase de otro item.

    Args:
        items: lista de dicts con keys {nombre_modulo, ean, codigo, descripcion,
               _destacado (bool)}.
        usar_historico: si True, aplica el combo + sin_ventas_func.
        sin_ventas_func: callable(ean) -> bool. Si None, se asume que ningún
                         producto tiene ventas (todos sin ventas).

    Returns:
        dict {ean: {confianza, razon, cantidad, ean_unidad_sug}}.
    """
    if not items:
        return {}

    if sin_ventas_func is None:
        def sin_ventas_func(_):
            return True

    # 1. Agrupar por módulo en orden de aparición.
    por_modulo = {}
    for idx, it in enumerate(items):
        nm = (it.get('nombre_modulo') or 'sin_nombre').strip() or 'sin_nombre'
        por_modulo.setdefault(nm, [])
        por_modulo[nm].append((idx, it))

    pack_map = {}
    for _nm, lst in por_modulo.items():
        # Pre-calcular envases del módulo para sugerir ean_unidad.
        envases = {}
        for idx, prod in lst:
            nums = _RE_ENVASE.findall(prod.get('descripcion') or '')
            if nums:
                envases[idx] = int(nums[-1])

        for pos, (idx, prod) in enumerate(lst):
            e = _ean_o_codigo(prod)
            if not e:
                continue
            desc = prod.get('descripcion') or ''
            destacado = bool(prod.get('_destacado'))
            m_xn = _RE_PACK_XN.search(desc)
            tiene_pack = bool(_RE_PACK_PALABRA.search(desc))
            primero = (pos == 0 and len(lst) >= 2)
            sin_v = sin_ventas_func(e)

            combo = usar_historico and primero and tiene_pack and sin_v

            if not destacado and not combo:
                continue

            cant_pack = int(m_xn.group(1)) if m_xn else None
            cant_pack_del_regex = m_xn is not None
            razon = []
            if destacado:
                razon.append('amarillo')
            if combo:
                razon.append('primero+pack+sin_ventas')
            if m_xn:
                razon.append(f'PACKx{cant_pack}')

            # Sugerir ean_unidad por envase múltiplo. Si encontramos varios
            # divisores, nos quedamos con el de mayor k. Si el cant_pack no
            # vino del regex PACK X N, también lo actualizamos al mejor k.
            ean_unidad = ''
            env_i = envases.get(idx)
            if env_i and env_i >= 2:
                mejor_k = None
                for j_idx, otro in lst:
                    if j_idx == idx:
                        continue
                    env_j = envases.get(j_idx)
                    if not env_j or env_j >= env_i:
                        continue
                    if env_i % env_j == 0:
                        k = env_i // env_j
                        if k >= 2 and (mejor_k is None or k > mejor_k):
                            mejor_k = k
                            ean_unidad = _ean_o_codigo(otro) or ''
                            if not cant_pack_del_regex:
                                cant_pack = k

            pack_map[e] = {
                'confianza': 'alta' if destacado else 'media',
                'razon': '+'.join(razon),
                'cantidad': cant_pack,
                'ean_unidad_sug': ean_unidad,
            }
    return pack_map


def init_app(app):

    @app.route('/modulos/import', methods=['GET'])
    @login_required
    def modulos_import_page():  # type: ignore[reportUnusedFunction]
        from helpers import get_config
        cfg = get_config()
        with database.get_db() as session:
            labs = (session.query(Laboratorio)
                    .filter(Laboratorio.activo == True)  # noqa: E712
                    .order_by(Laboratorio.nombre).all())
            labs_data = [{'id': l.id, 'nombre': l.nombre} for l in labs]
        return render_template('modulos_import.html',
                               laboratorios=labs_data,
                               ruta_excels=cfg.get('ruta_excels', ''))

    @app.route('/api/modulos/import-preview', methods=['POST'])
    @login_required
    def api_modulos_import_preview():
        """Recibe XLSX/PDF/imagen, devuelve preview con headers + filas + mapping."""
        if 'archivo' not in request.files:
            return jsonify({'error': 'Falta archivo'}), 400
        f = request.files['archivo']
        if not f.filename:
            return jsonify({'error': 'Archivo sin nombre'}), 400

        ext = os.path.splitext(f.filename)[1].lower()
        IMG = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.tif')
        if ext not in ('.xlsx', '.xls', '.pdf', *IMG):
            return jsonify({'error': f'Formato {ext} no soportado.'}), 400

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            f.save(tmp.name)
            tmp_path = tmp.name
        try:
            if ext == '.pdf':
                preview = _previsualizar_pdf(tmp_path)
            elif ext in IMG:
                preview = _previsualizar_imagen(tmp_path)
            else:
                preview = _previsualizar_xlsx(tmp_path)
        except Exception as e:
            return jsonify({'error': f'Error al parsear: {e}'}), 500
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        # Re-aplicar autodetect con candidatos específicos de módulos.
        import field_inference as fi
        preview['mapping'] = fi.inferir_columnas(
            preview.get('headers') or [],
            sample_rows=(preview.get('rows') or [])[:10],
            candidatos=['nombre_modulo', 'codigo', 'ean', 'descripcion',
                        'cantidad', 'descuento_psl'],
        )
        return jsonify({**preview, 'filename': f.filename})

    @app.route('/api/modulos/import-validar', methods=['POST'])
    @login_required
    def api_modulos_import_validar():
        """Para cada item: detecta si es pack, cruza con catálogo y sugiere
        ean_unidad si corresponde.

        Body: { items: [...], laboratorio_id?: N }
        Cada item: { nombre_modulo, ean, codigo, descripcion, cantidad,
                     descuento_psl, _destacado (bool, opcional) }

        Por defecto SOLO se marca pack si la fila viene en amarillo en el
        Excel (señal explícita del laboratorio).

        Si el caller pasa `usar_historico: true`, además se aplica el combo:
          - Primero del grupo (módulo)
          - Descripción contiene la palabra 'PACK'
          - Nunca se vendió por ese código (sin ventas históricas)
        Cuando se cumplen los 3 → pack. El regex 'PACK X N' aporta cantidad.
        """
        import producto_matcher as pm
        from database import ObsVentaMensual, Producto
        data = request.get_json(silent=True) or {}
        items = data.get('items') or []
        usar_historico = bool(data.get('usar_historico'))
        lab_id = data.get('laboratorio_id')
        try:
            lab_id = int(lab_id) if lab_id else None
        except (TypeError, ValueError):
            lab_id = None

        if not items:
            return jsonify({'items': [], 'stats': {}})

        with database.get_db() as session:
            # 1. Match contra catálogo (mismo flujo que ofertas).
            items_match = [{
                'ean': it.get('ean'),
                'codigo_alfabeta': it.get('codigo'),
                'descripcion': it.get('descripcion'),
            } for it in items]
            results = pm.match_productos_bulk(items_match, laboratorio_id=lab_id, session=session)

            # 2. Lookup bulk de "tuvo ventas alguna vez" — solo si se pidió
            # usar el histórico (la query es cara y no la necesitamos por defecto).
            ean_a_obs = {}
            con_ventas = set()
            if usar_historico:
                todos_eans = {_ean_o_codigo(it) for it in items if _ean_o_codigo(it)}
                ean_a_obs = dict(
                    session.query(Producto.codigo_barra, Producto.observer_id)
                    .filter(Producto.codigo_barra.in_(todos_eans),
                            Producto.observer_id.isnot(None)).all()
                ) if todos_eans else {}
                obs_ids = {oid for oid in ean_a_obs.values() if oid}
                if obs_ids:
                    rows = (session.query(ObsVentaMensual.producto_observer)
                            .filter(ObsVentaMensual.producto_observer.in_(obs_ids),
                                    ObsVentaMensual.unidades > 0)
                            .distinct().all())
                    con_ventas = {r[0] for r in rows}

            def _sin_ventas(ean):
                # Sin registro local = nunca vendido por ese código.
                oid = ean_a_obs.get(ean)
                if oid is None:
                    return True
                return oid not in con_ventas

            # 3. Detección de packs (función pura, ver detectar_packs_modulos arriba).
            pack_map = detectar_packs_modulos(
                items, usar_historico=usar_historico,
                sin_ventas_func=_sin_ventas if usar_historico else None,
            )

            # 3. Combinar match + pack en cada entry.
            validados = []
            stats = {'ok': 0, 'fuzzy': 0, 'not_found': 0,
                     'pack_auto': 0, 'pack_dudoso': 0}
            for it, res in zip(items, results):
                entry = dict(it)
                # Datos del match contra catálogo.
                if res.producto is None:
                    entry['_status'] = 'not_found'
                    entry['_motivo'] = 'No está en el catálogo local'
                    stats['not_found'] += 1
                else:
                    p = res.producto
                    entry['_match_descripcion_local'] = getattr(p, 'descripcion', '') or ''
                    entry['_producto_id'] = getattr(p, 'id', None)
                    entry['_observer_id'] = getattr(p, 'observer_id', None)
                    entry['_estrategia'] = res.estrategia
                    entry['_score'] = res.score
                    if res.estrategia.endswith('_obs') or res.estrategia in (
                            'fuzzy_lab', 'fuzzy_global', 'fuzzy_otro_lab', 'tokens_superset'):
                        entry['_status'] = 'fuzzy'
                        stats['fuzzy'] += 1
                    else:
                        entry['_status'] = 'ok'
                        stats['ok'] += 1

                # Datos del pack detector. Reuso el mismo fallback ean→codigo.
                ean = _ean_o_codigo(it)
                pack_info = pack_map.get(ean) if ean else None
                # Para que el frontend tenga claro qué EAN usamos (si fue
                # fallback al código), lo agrego al entry.
                entry['_ean_efectivo'] = ean
                if pack_info:
                    entry['_es_pack'] = True
                    entry['_pack_confianza'] = pack_info.get('confianza', 'media')
                    entry['_pack_razon'] = pack_info.get('razon', '')
                    entry['_cantidad_pack'] = pack_info.get('cantidad')
                    entry['_ean_unidad_sugerido'] = pack_info.get('ean_unidad_sug')
                    if pack_info.get('confianza') == 'alta':
                        stats['pack_auto'] += 1
                    else:
                        stats['pack_dudoso'] += 1
                else:
                    entry['_es_pack'] = False
                validados.append(entry)

        return jsonify({'items': validados, 'stats': stats, 'total': len(validados)})

    @app.route('/api/modulos/import-guardar', methods=['POST'])
    @login_required
    def api_modulos_import_guardar():
        """Crea Modulo + ModuloPack para cada fila confirmada.

        Body: { laboratorio_id, lista_nombre?, items: [...] }
        Cada item con: nombre_modulo, ean, descripcion, cantidad (cant_modulo),
        descuento_psl, _es_pack (bool), _cantidad_pack, _ean_unidad_sugerido.
        """
        from helpers import now_ar
        data = request.get_json(silent=True) or {}
        try:
            lab_id = int(data.get('laboratorio_id'))
        except (TypeError, ValueError):
            return jsonify({'error': 'laboratorio_id inválido'}), 400
        items = data.get('items') or []
        if not isinstance(items, list) or not items:
            return jsonify({'error': 'items vacío'}), 400
        lista_nombre = (data.get('lista_nombre') or '').strip() or None

        with database.get_db() as session:
            lab = session.get(Laboratorio, lab_id)
            if not lab:
                return jsonify({'error': 'Laboratorio no encontrado'}), 404

            modulos_creados = 0
            packs_agregados = 0
            saltados = 0
            modulos_cache = {}
            for it in items:
                # Mismo fallback: ean del archivo o código del proveedor.
                ean_pack = (str(it.get('ean') or '').strip()) or None
                if not ean_pack:
                    ean_pack = (str(it.get('codigo') or '').strip()) or None
                if not ean_pack:
                    saltados += 1
                    continue
                nombre_mod = (it.get('nombre_modulo') or '').strip() or 'SIN_NOMBRE'
                key = (nombre_mod, lista_nombre)
                if key not in modulos_cache:
                    existing = (session.query(Modulo)
                                .filter_by(nombre=nombre_mod, lista_nombre=lista_nombre,
                                           laboratorio_id=lab_id).first())
                    if not existing:
                        existing = Modulo(nombre=nombre_mod,
                                          laboratorio_id=lab_id,
                                          lista_nombre=lista_nombre,
                                          creado_en=now_ar())
                        session.add(existing)
                        session.flush()
                        modulos_creados += 1
                    modulos_cache[key] = existing
                modulo = modulos_cache[key]

                ya = (session.query(ModuloPack)
                      .filter_by(ean_pack=ean_pack, modulo_id=modulo.id).first())
                if ya:
                    saltados += 1
                    continue

                es_pack = bool(it.get('_es_pack'))
                if es_pack:
                    cant_pack = int(it.get('_cantidad_pack') or 2)
                    ean_unidad = it.get('_ean_unidad_sugerido') or ean_pack
                else:
                    cant_pack = 1
                    ean_unidad = ean_pack

                desc_pct = it.get('descuento_psl')
                try:
                    desc_pct = float(desc_pct) if desc_pct not in (None, '') else None
                except (TypeError, ValueError):
                    desc_pct = None
                cant_modulo = it.get('cantidad')
                try:
                    cant_modulo = int(cant_modulo) if cant_modulo not in (None, '') else None
                except (TypeError, ValueError):
                    cant_modulo = None

                session.add(ModuloPack(
                    ean_pack=ean_pack[:30],
                    ean_unidad=str(ean_unidad)[:30],
                    cantidad=cant_pack,
                    cant_modulo=cant_modulo,
                    desc_pct=desc_pct,
                    descripcion=(str(it.get('descripcion') or ''))[:255] or None,
                    modulo_id=modulo.id,
                    creado_en=now_ar(),
                ))
                packs_agregados += 1
            session.commit()

        return jsonify({
            'ok': True,
            'laboratorio': lab.nombre,
            'modulos_creados': modulos_creados,
            'packs_agregados': packs_agregados,
            'saltados': saltados,
        })
