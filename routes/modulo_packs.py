"""Módulo packs routes: CRUD, import, vista, toggle activo."""

import os
from flask import render_template, request, redirect, url_for, flash, jsonify, make_response
from werkzeug.utils import secure_filename
import database
from database import Producto, Laboratorio, Modulo, ModuloPack
from helpers import UPLOAD_FOLDER


def init_app(app):

    @app.route('/modulo-packs')
    def modulo_packs_list():
        with database.get_db() as session:
            all_prods = session.query(Producto).order_by(Producto.codigo_barra).all()
            prod_map = {p.codigo_barra: p for p in all_prods}
            # Solo labs que tienen al menos un módulo
            labs_con_modulos = {
                lid for (lid,) in session.query(Modulo.laboratorio_id)
                .filter(Modulo.laboratorio_id.isnot(None)).distinct().all()
            }
            labs = (session.query(Laboratorio)
                    .filter(Laboratorio.id.in_(labs_con_modulos))
                    .order_by(Laboratorio.nombre).all()) if labs_con_modulos else []

            # Fallback para descripciones desde obs_productos (cuando la unidad
            # sugerida viene del catálogo general, ean_unidad = str(observer_id))
            ean_unidades = {mp.ean_unidad for mp in session.query(database.ModuloPack.ean_unidad).all()}
            # ean_unidad con prefijo 'OBS:' = observer_id directo al catálogo ObServer
            obs_candidates = [e for e in ean_unidades if e and e.startswith('OBS:') and e not in prod_map]
            obs_desc_map = {}
            if obs_candidates:
                obs_ids = []
                for e in obs_candidates:
                    try:
                        obs_ids.append(int(e[4:]))
                    except (ValueError, TypeError):
                        pass
                if obs_ids:
                    obs_rows = session.query(database.ObsProducto.observer_id, database.ObsProducto.descripcion).filter(
                        database.ObsProducto.observer_id.in_(obs_ids)
                    ).all()
                    obs_desc_map = {f'OBS:{oid}': desc for oid, desc in obs_rows}

            def _desc_unidad(mp):
                if mp.ean_unidad in prod_map:
                    return prod_map[mp.ean_unidad].descripcion or ''
                return obs_desc_map.get(mp.ean_unidad, '')

            def _pack_dict(mp):
                return {'id': mp.id, 'ean_pack': mp.ean_pack, 'ean_unidad': mp.ean_unidad,
                        'cantidad': mp.cantidad,
                        'cant_modulo': mp.cant_modulo,
                        'desc_pct': float(mp.desc_pct) if mp.desc_pct is not None else None,
                        'desc_pack':   mp.descripcion or '',
                        'desc_unidad': _desc_unidad(mp),
                        'prod_unidad_id': prod_map[mp.ean_unidad].id if mp.ean_unidad in prod_map else None,
                        'modulo_id': mp.modulo_id}

            modulos_raw = (session.query(Modulo)
                           .outerjoin(Laboratorio)
                           .order_by(Modulo.lista_nombre, Laboratorio.nombre, Modulo.nombre).all())
            def _lista_nombre(m):
                if m.lista_nombre:
                    return m.lista_nombre
                return m.laboratorio.nombre if m.laboratorio else '—'

            modulos = [{'id': m.id, 'nombre': m.nombre,
                        'lab_nombre': m.laboratorio.nombre if m.laboratorio else '—',
                        'lab_id': m.laboratorio_id or 0,
                        'lista_nombre': _lista_nombre(m),
                        'is_lista_marker': bool(m.lista_nombre and m.nombre == m.lista_nombre),
                        'creado_en': m.creado_en.strftime('%d/%m/%Y') if m.creado_en else '',
                        'activo': m.activo,
                        'packs': [_pack_dict(mp) for mp in m.packs]}
                       for m in modulos_raw]

            lista_activo_map = {}
            lista_toggle_map = {}
            for md in modulos:
                ln = md['lista_nombre']
                if md['activo']:
                    lista_activo_map[ln] = True
                if ln not in lista_toggle_map or md['is_lista_marker']:
                    lista_toggle_map[ln] = md['id']
            for md in modulos:
                ln = md['lista_nombre']
                md['lista_activo']    = lista_activo_map.get(ln, False)
                md['lista_toggle_id'] = lista_toggle_map.get(ln, md['id'])

            orphan_packs = [_pack_dict(mp) for mp in
                            session.query(ModuloPack).filter(ModuloPack.modulo_id.is_(None))
                            .order_by(ModuloPack.ean_pack).all()]

            def _first_alt(p):
                return p.codigo_barra_alt1 or p.codigo_barra_alt2 or p.codigo_barra_alt3 or ''
            prods_pack = [{'ean': p.codigo_barra, 'desc': p.descripcion or '',
                           'alt': _first_alt(p), 'is_pack': bool(p.es_pack)}
                          for p in all_prods if p.es_pack]
            prods_all  = [{'ean': p.codigo_barra, 'desc': p.descripcion or '',
                           'alt': _first_alt(p), 'is_pack': bool(p.es_pack)}
                          for p in all_prods]
            return render_template('modulo_packs.html',
                                   modulos=modulos, orphan_packs=orphan_packs,
                                   labs=[{'id': l.id, 'nombre': l.nombre} for l in labs],
                                   prods_pack=prods_pack, prods_all=prods_all)

    @app.route('/modulo-packs/vista')
    def modulo_packs_vista():
        with database.get_db() as session:
            prod_map = {p.codigo_barra: p for p in session.query(Producto).all()}
            # Solo labs que tienen al menos un módulo
            labs_con_modulos = {
                lid for (lid,) in session.query(Modulo.laboratorio_id)
                .filter(Modulo.laboratorio_id.isnot(None)).distinct().all()
            }
            labs = (session.query(Laboratorio)
                    .filter(Laboratorio.id.in_(labs_con_modulos))
                    .order_by(Laboratorio.nombre).all()) if labs_con_modulos else []
            lab_filter = request.args.get('lab', '').strip()

            q = session.query(Modulo).outerjoin(Laboratorio).order_by(Laboratorio.nombre, Modulo.nombre)
            modulos_raw = q.all()

            modulos = []
            for m in modulos_raw:
                lab_nombre = m.laboratorio.nombre if m.laboratorio else ''
                if lab_filter and lab_nombre != lab_filter:
                    continue
                packs = [{'ean_pack': mp.ean_pack,
                          'desc_pack': mp.descripcion or '—',
                          'ean_unidad': mp.ean_unidad,
                          'desc_unidad': (prod_map[mp.ean_unidad].descripcion or '—') if mp.ean_unidad in prod_map else '—',
                          'cantidad': mp.cantidad}
                         for mp in m.packs]
                modulos.append({'id': m.id, 'nombre': m.nombre,
                                'lab_nombre': lab_nombre or '—',
                                'packs': packs})

            return render_template('modulo_packs_vista.html',
                                   modulos=modulos,
                                   labs=[{'id': l.id, 'nombre': l.nombre} for l in labs],
                                   lab_filter=lab_filter)

    @app.route('/modulo-packs/plantilla')
    def modulo_packs_plantilla():
        """Descarga plantilla XLSX para importar módulos (Formato A)."""
        import io, openpyxl
        from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
        from openpyxl.styles.numbers import FORMAT_NUMBER
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Módulos'

        hdr_fill  = PatternFill('solid', fgColor='1C1C1E')
        hdr_font  = Font(bold=True, color='EAB308', size=10)
        mod_fill  = PatternFill('solid', fgColor='FEF9C3')
        mod_font  = Font(bold=True, color='92400E')
        item_fill = PatternFill('solid', fgColor='FAFAFA')
        border_b  = Border(bottom=Side(style='thin', color='D0D0D0'))

        # Fila 1: encabezados
        headers = ['NOMBRE MÓDULO', 'EAN', 'DESCRIPCIÓN', 'CANT. MÓDULO', 'DESC. %']
        ws.append(headers)
        for ci in range(1, 6):
            c = ws.cell(row=1, column=ci)
            c.fill = hdr_fill; c.font = hdr_font; c.border = border_b
            c.alignment = Alignment(horizontal='center')

        # Módulo de ejemplo 1
        ws.append(['MOD. EJEMPLO AMOXICILINA', None, None, None, None])
        c = ws.cell(row=2, column=1); c.fill = mod_fill; c.font = mod_font
        for ean, desc, cant, dto in [
            ('7790001000001', 'AMOXICILINA 500 mg COM x 16', 10, 7.0),
            ('7790001000003', 'AMOXICILINA 500 mg COM x 32', 6,  7.0),
        ]:
            r = ws.max_row + 1
            ws.append([None, ean, desc, cant, dto])
            ws.cell(row=r, column=1).fill = item_fill
            ws.cell(row=r, column=2).number_format = '@'

        # Fila vacía separadora
        ws.append([])

        # Módulo de ejemplo 2
        ws.append(['MOD. EJEMPLO IBUPROFENO', None, None, None, None])
        c = ws.cell(row=ws.max_row, column=1); c.fill = mod_fill; c.font = mod_font
        for ean, desc, cant, dto in [
            ('7790002000001', 'IBUPROFENO 400 mg COM x 20', 12, 10.0),
        ]:
            r = ws.max_row + 1
            ws.append([None, ean, desc, cant, dto])
            ws.cell(row=r, column=1).fill = item_fill
            ws.cell(row=r, column=2).number_format = '@'

        # Anchos
        ws.column_dimensions['A'].width = 30
        ws.column_dimensions['B'].width = 16
        ws.column_dimensions['C'].width = 42
        ws.column_dimensions['D'].width = 14
        ws.column_dimensions['E'].width = 10

        # Congelar encabezados
        ws.freeze_panes = 'A2'

        buf = io.BytesIO()
        wb.save(buf); buf.seek(0)
        resp = make_response(buf.getvalue())
        resp.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        resp.headers['Content-Disposition'] = 'attachment; filename="plantilla_modulos.xlsx"'
        return resp

    @app.route('/api/packs/buscar-unidad')
    def api_packs_buscar_unidad():
        """Búsqueda combinada productos locales + obs_productos para el dropdown
        del Buscar equivalente. Excluye candidatos que parecen pack (descripción
        con 'PACK X N').

        Query: ?q=amoxidal  (mínimo 2 chars)
        Respuesta: {results: [{ean, desc, fuente: 'local'|'observer'}]}
        """
        import re as _re
        q = (request.args.get('q') or '').strip()
        if len(q) < 2:
            return jsonify({'results': []})
        q_low = q.lower()
        PACK_RE = _re.compile(r'\bPACK\s*X\s*\d+\b', _re.IGNORECASE)
        out = []
        with database.get_db() as session:
            # Locales (tienen EAN real)
            locales = (session.query(database.Producto)
                       .filter(database.Producto.codigo_barra.ilike(f'%{q}%') |
                               database.Producto.descripcion.ilike(f'%{q}%'))
                       .limit(30).all())
            for p in locales:
                if not p.descripcion or PACK_RE.search(p.descripcion):
                    continue
                out.append({'ean': p.codigo_barra, 'desc': p.descripcion or '',
                            'fuente': 'local'})
            # Obs_productos (usamos observer_id como pseudo-EAN)
            eans_vistos = {r['ean'] for r in out}
            obs = (session.query(database.ObsProducto)
                   .filter(database.ObsProducto.descripcion.ilike(f'%{q}%'),
                           database.ObsProducto.fecha_baja.is_(None))
                   .limit(30).all())
            for o in obs:
                if not o.descripcion or PACK_RE.search(o.descripcion):
                    continue
                ean = f'OBS:{o.observer_id}'
                if ean in eans_vistos:
                    continue
                out.append({'ean': ean, 'desc': o.descripcion,
                            'fuente': 'observer'})
        # Orden: primero los que el q matchea en desc al inicio
        out.sort(key=lambda r: (0 if r['desc'].lower().startswith(q_low) else 1,
                                r['fuente'] == 'observer',
                                len(r['desc'])))
        return jsonify({'results': out[:40]})

    @app.route('/modulo-packs/importar', methods=['POST'])
    def modulo_packs_importar():
        """Importa módulos desde un XLSX (formato Roemmers o plantilla propia).

        Detecta automáticamente los packs usando:
          - filas destacadas en amarillo en el Excel
          - patrón 'PACK X N' en la descripción
          - ausencia de ventas históricas por ese EAN
        Los detectados se guardan con ean_unidad + cantidad sugeridos.
        Los no-pack se guardan con ean_unidad=ean_pack y cantidad=1 (como antes)."""
        from parsers.modulos_xlsx import parse_modulos_xlsx
        from pack_detector import detectar_packs
        f = request.files.get('file')
        lab_id = request.form.get('lab_id') or None
        lista_nombre = (request.form.get('lista_nombre') or '').strip() or None
        if not f:
            return jsonify({'error': 'No se recibió archivo'}), 400
        tmp = os.path.join(UPLOAD_FOLDER, secure_filename(f.filename))
        f.save(tmp)
        try:
            with database.get_db() as session:
                try:
                    modules = parse_modulos_xlsx(tmp)
                    if not modules:
                        return jsonify({'error': 'No se encontraron módulos en el archivo'}), 400

                    # Detectar packs. Criterios:
                    #  - alta/media con cantidad del regex → auto con cant real
                    #  - destacado en amarillo pero sin cantidad → cant=2 placeholder
                    #    (el user edita después). El amarillo lo marcó el vendedor,
                    #    es la señal más fuerte.
                    #  - baja (solo sin_ventas) → revisión manual, no auto
                    packs_detectados = detectar_packs(modules, session, saltear_registrados=False)
                    pack_map = {}
                    for p in packs_detectados:
                        if p['confianza'] == 'baja':
                            continue
                        if not p.get('cantidad'):
                            if p.get('destacado'):
                                # Placeholder: está marcado como pack pero sin
                                # cantidad definida. cant=2 para que supere 1
                                # y se trate como pack en el front.
                                p = dict(p, cantidad=2)
                            else:
                                continue
                        pack_map[p['ean_pack']] = p

                    creados = 0
                    packs_agregados = 0
                    packs_auto = 0
                    for mod in modules:
                        nombre_mod = mod['nombre']
                        modulo_actual = session.query(Modulo).filter_by(nombre=nombre_mod, lista_nombre=lista_nombre).first()
                        if not modulo_actual:
                            modulo_actual = Modulo(nombre=nombre_mod,
                                                   laboratorio_id=int(lab_id) if lab_id else None,
                                                   lista_nombre=lista_nombre)
                            session.add(modulo_actual)
                            session.flush()
                            creados += 1
                        for item in mod['items']:
                            ean_pack = item['ean']
                            if not ean_pack:
                                continue
                            existe = session.query(ModuloPack).filter_by(
                                ean_pack=ean_pack, modulo_id=modulo_actual.id).first()
                            if existe:
                                continue

                            det = pack_map.get(ean_pack)
                            if det:
                                # Si no hay unidad sugerida, ponemos ean_pack
                                # (marca el pack pero queda pendiente de
                                # completar la equivalencia a mano)
                                ean_u = det['ean_unidad_sug'] or ean_pack
                                cant = det['cantidad']
                                packs_auto += 1
                            else:
                                ean_u = ean_pack   # no es pack → relación 1:1
                                cant = 1

                            session.add(ModuloPack(
                                ean_pack=ean_pack,
                                ean_unidad=ean_u,
                                cantidad=cant,
                                descripcion=item.get('descripcion', ''),
                                cant_modulo=item.get('cant'),
                                desc_pct=item.get('desc_pct'),
                                modulo_id=modulo_actual.id,
                            ))
                            packs_agregados += 1
                    session.commit()
                    return jsonify({'ok': True,
                                    'modulos_creados': creados,
                                    'packs_agregados': packs_agregados,
                                    'packs_auto_detectados': packs_auto,
                                    'packs_pendientes_revision': len(packs_detectados) - packs_auto})
                except Exception as e:
                    session.rollback()
                    return jsonify({'error': str(e)}), 500
        finally:
            try: os.remove(tmp)
            except OSError: pass

    @app.route('/modulos/delete-by-lista', methods=['POST'])
    def modulos_delete_by_lista():
        """Elimina todos los módulos (y sus packs en cascada) de una lista/importación."""
        data = request.get_json(silent=True) or {}
        lista_nombre = data.get('lista_nombre', '').strip()
        if not lista_nombre:
            return jsonify({'error': 'lista_nombre requerido'}), 400
        with database.get_db() as session:
            try:
                modulos = session.query(Modulo).filter(
                    (Modulo.lista_nombre == lista_nombre) |
                    ((Modulo.lista_nombre.is_(None)) &
                     (Modulo.laboratorio.has(database.Laboratorio.nombre == lista_nombre)))
                ).all()
                count = len(modulos)
                for m in modulos:
                    session.delete(m)
                session.commit()
                return jsonify({'ok': True, 'eliminados': count})
            except Exception as e:
                session.rollback()
                return jsonify({'error': str(e)}), 500

    @app.route('/modulo-packs/activos')
    def modulo_packs_activos():
        """Devuelve las listas activas agrupadas por lista_nombre. ?lab=Nombre filtra por laboratorio."""
        lab_nombre = request.args.get('lab', '').strip()
        with database.get_db() as session:
            q = session.query(Modulo).filter_by(activo=True).outerjoin(Laboratorio)
            if lab_nombre:
                q = q.filter(Laboratorio.nombre == lab_nombre)
            raw = q.order_by(Modulo.lista_nombre, Modulo.nombre).all()
            prod_map = {p.codigo_barra: p for p in session.query(Producto).all()}

            from collections import OrderedDict
            listas = OrderedDict()
            for m in raw:
                ln = m.lista_nombre or m.nombre
                if ln not in listas:
                    listas[ln] = {'lista_nombre': ln,
                                   'lab_nombre': m.laboratorio.nombre if m.laboratorio else '',
                                   'modulos': []}
                if m.lista_nombre and m.nombre == m.lista_nombre:
                    continue
                packs = [{'ean_pack':    mp.ean_pack,
                          'desc_pack':   mp.descripcion or '',
                          'ean_unidad':  mp.ean_unidad,
                          'desc_unidad': (prod_map[mp.ean_unidad].descripcion or '') if mp.ean_unidad in prod_map else '',
                          'cant_modulo': mp.cant_modulo if mp.cant_modulo is not None else mp.cantidad,
                          'desc_pct':    float(mp.desc_pct) if mp.desc_pct is not None else 0.0}
                         for mp in m.packs]
                listas[ln]['modulos'].append({'id': m.id, 'nombre': m.nombre, 'packs': packs})

            return jsonify({'listas': list(listas.values())})

    @app.route('/modulo/<int:modulo_id>/toggle-activo', methods=['POST'])
    def modulo_toggle_activo(modulo_id):
        with database.get_db() as session:
            try:
                m = session.get(Modulo, modulo_id)
                if not m:
                    return jsonify({'error': 'No encontrado'}), 404
                nuevo_estado = not bool(m.activo)
                if nuevo_estado:
                    session.query(Modulo).filter(
                        Modulo.laboratorio_id == m.laboratorio_id
                    ).update({'activo': False})
                    session.flush()
                if m.lista_nombre:
                    session.query(Modulo).filter(
                        Modulo.lista_nombre == m.lista_nombre
                    ).update({'activo': nuevo_estado})
                else:
                    m.activo = nuevo_estado
                session.commit()
                return jsonify({'activo': nuevo_estado})
            except Exception as e:
                session.rollback()
                return jsonify({'error': str(e)}), 500

    @app.route('/modulo/add', methods=['POST'])
    def modulo_add():
        data = request.get_json(silent=True) or {}
        nombre = (data.get('nombre') or '').strip()
        lab_id = data.get('laboratorio_id')
        lista_nombre = (data.get('lista_nombre') or nombre or '').strip() or None
        if not nombre:
            return jsonify({'error': 'Nombre requerido'}), 400
        with database.get_db() as session:
            try:
                m = Modulo(nombre=nombre,
                           laboratorio_id=int(lab_id) if lab_id else None,
                           lista_nombre=lista_nombre)
                session.add(m)
                session.commit()
                lab_nombre = m.laboratorio.nombre if m.laboratorio else '—'
                return jsonify({'ok': True, 'id': m.id, 'nombre': m.nombre,
                                'lab_nombre': lab_nombre,
                                'creado_en': m.creado_en.strftime('%d/%m/%Y') if m.creado_en else ''})
            except Exception as e:
                session.rollback()
                return jsonify({'error': str(e)}), 500

    @app.route('/modulo/<int:modulo_id>/delete', methods=['POST'])
    def modulo_delete(modulo_id):
        with database.get_db() as session:
            try:
                m = session.get(Modulo, modulo_id)
                if m:
                    session.delete(m)
                    session.commit()
                return jsonify({'ok': True})
            except Exception as e:
                session.rollback()
                return jsonify({'error': str(e)}), 500

    @app.route('/modulo-pack/<int:pack_id>/assign', methods=['POST'])
    def modulo_pack_assign(pack_id):
        data = request.get_json(silent=True) or {}
        modulo_id = data.get('modulo_id')
        with database.get_db() as session:
            try:
                mp = session.get(ModuloPack, pack_id)
                if not mp:
                    return jsonify({'error': 'Pack no encontrado'}), 404
                mp.modulo_id = int(modulo_id) if modulo_id else None
                session.commit()
                return jsonify({'ok': True})
            except Exception as e:
                session.rollback()
                return jsonify({'error': str(e)}), 500

    @app.route('/modulo-pack/add', methods=['POST'])
    def modulo_pack_add():
        data = request.get_json(silent=True) or {}
        ean_pack   = (data.get('ean_pack') or '').strip()
        ean_unidad = (data.get('ean_unidad') or '').strip()
        cantidad   = int(data.get('cantidad') or 1)
        descripcion = (data.get('descripcion') or '').strip()
        modulo_id  = data.get('modulo_id')
        if not ean_pack or not ean_unidad or cantidad < 1:
            return {'error': 'Datos incompletos'}, 400
        with database.get_db() as session:
            try:
                # Dedup por (modulo_id, ean_pack): el mismo EAN puede estar
                # en distintos módulos.
                q = session.query(ModuloPack).filter_by(ean_pack=ean_pack)
                if modulo_id is not None:
                    q = q.filter_by(modulo_id=int(modulo_id) if modulo_id else None)
                existing = q.first()
                if existing:
                    existing.ean_unidad = ean_unidad
                    existing.cantidad = cantidad
                    existing.descripcion = descripcion or existing.descripcion
                    if modulo_id is not None:
                        existing.modulo_id = int(modulo_id) if modulo_id else None
                else:
                    session.add(ModuloPack(ean_pack=ean_pack, ean_unidad=ean_unidad,
                                           cantidad=cantidad, descripcion=descripcion,
                                           modulo_id=int(modulo_id) if modulo_id else None))
                session.commit()
                return {'ok': True}
            except Exception as e:
                session.rollback()
                return {'error': str(e)}, 500

    @app.route('/modulo-pack/<int:pack_id>/update', methods=['POST'])
    def modulo_pack_update(pack_id):
        with database.get_db() as session:
            try:
                data = request.get_json(silent=True) or {}
                mp = session.get(ModuloPack, pack_id)
                if not mp:
                    return jsonify({'error': 'No encontrado'}), 404
                if 'ean_pack' in data:
                    mp.ean_pack = str(data['ean_pack']).strip()
                if 'descripcion' in data:
                    mp.descripcion = str(data['descripcion']).strip() or None
                if 'ean_unidad' in data:
                    mp.ean_unidad = str(data['ean_unidad']).strip()
                if 'cantidad' in data:
                    mp.cantidad = int(data['cantidad'])
                if 'cant_modulo' in data:
                    mp.cant_modulo = int(data['cant_modulo']) if data['cant_modulo'] is not None else None
                if 'desc_pct' in data:
                    mp.desc_pct = float(data['desc_pct']) if data['desc_pct'] is not None else None
                session.commit()
                return jsonify({'ok': True})
            except Exception as e:
                session.rollback()
                return jsonify({'error': str(e)}), 500

    @app.route('/modulo-pack/<int:pack_id>/delete', methods=['POST'])
    def modulo_pack_delete(pack_id):
        with database.get_db() as session:
            mp = session.get(ModuloPack, pack_id)
            if mp:
                session.delete(mp)
                session.commit()
            return {'ok': True}
