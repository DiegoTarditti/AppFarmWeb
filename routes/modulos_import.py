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


def init_app(app):

    @app.route('/modulos/import', methods=['GET'])
    @login_required
    def modulos_import_page():
        with database.get_db() as session:
            labs = (session.query(Laboratorio)
                    .filter(Laboratorio.activo == True)  # noqa: E712
                    .order_by(Laboratorio.nombre).all())
            labs_data = [{'id': l.id, 'nombre': l.nombre} for l in labs]
        return render_template('modulos_import.html', laboratorios=labs_data)

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
        Cada item: { nombre_modulo, ean, codigo, descripcion, cantidad, descuento_psl }
        """
        import producto_matcher as pm
        from pack_detector import detectar_packs
        data = request.get_json(silent=True) or {}
        items = data.get('items') or []
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

            # 2. Detección de packs: agrupamos los items por nombre_modulo (formato
            # esperado por pack_detector).
            por_modulo = {}
            for it in items:
                nm = (it.get('nombre_modulo') or 'sin_nombre').strip() or 'sin_nombre'
                por_modulo.setdefault(nm, {'nombre': nm, 'items': []})
                por_modulo[nm]['items'].append({
                    'ean': it.get('ean'),
                    'descripcion': it.get('descripcion') or '',
                    'cant': it.get('cantidad'),
                    'desc_pct': it.get('descuento_psl'),
                })
            packs_detectados = detectar_packs(list(por_modulo.values()), session,
                                              saltear_registrados=False)
            pack_map = {p['ean_pack']: p for p in packs_detectados if p['ean_pack']}

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

                # Datos del pack detector.
                ean = it.get('ean')
                pack_info = pack_map.get(ean) if ean else None
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
                ean_pack = (str(it.get('ean') or '').strip()) or None
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
