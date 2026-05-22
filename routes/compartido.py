"""Archivos compartidos entre instancias del grupo.

Hub: la instancia Render designada (HUB_BASE_URL en .env).
- POST /api/compartido/subir      — sube un archivo procesado al hub
- GET  /api/compartido/listar     — lista archivos disponibles en el hub
- GET  /api/compartido/<id>/json  — descarga JSON de un archivo del hub
- POST /compartido/<id>/importar  — importa un archivo del hub a la DB local
- GET  /compartido                — UI: lista + botones importar
"""
import json
import os
from datetime import datetime

import requests
from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

import database
from database import ArchivoCompartido, Laboratorio, OfertaMinimo, get_db
from helpers import now_ar

_HUB_BASE_URL = os.environ.get('HUB_BASE_URL', '').rstrip('/')
_HUB_TOKEN    = os.environ.get('HUB_TOKEN', '')
_FARMACIA_ID  = os.environ.get('OBSERVER_ID_FARMACIA', 'desconocida')

_TIPOS_LABEL = {
    'oferta_minimo': 'Ofertas con mínimo',
    'modulos':       'Módulos',
    'equivalencias': 'Equivalencias de barcode',
}

_ES_HUB = not _HUB_BASE_URL or _HUB_BASE_URL.rstrip('/') in (
    os.environ.get('RENDER_BASE_URL', '').rstrip('/'),
    'http://localhost:5000',
    'http://localhost:5000/',
)


def _auth_ok(req):
    token = req.headers.get('X-Hub-Token', '')
    return token and token == _HUB_TOKEN


def _hub_headers():
    return {'X-Hub-Token': _HUB_TOKEN, 'Content-Type': 'application/json'}


def init_app(app):

    # ── Endpoints hub (escritura/lectura de archivos compartidos) ────────────

    @app.route('/api/compartido/subir', methods=['POST'])
    def api_compartido_subir():
        """Recibe un archivo procesado y lo guarda en la DB del hub."""
        if not _auth_ok(request):
            return jsonify({'ok': False, 'error': 'No autorizado'}), 401
        data = request.get_json(silent=True) or {}
        tipo    = (data.get('tipo') or '').strip()
        nombre  = (data.get('nombre') or '').strip()
        origen  = (data.get('farmacia_origen') or 'desconocida').strip()
        desc    = (data.get('descripcion') or '').strip() or None
        items   = data.get('items')
        if not tipo or not nombre or not isinstance(items, list):
            return jsonify({'ok': False, 'error': 'Faltan campos: tipo, nombre, items[]'}), 400
        json_str = json.dumps(items, ensure_ascii=False)
        with get_db() as session:
            archivo = ArchivoCompartido(
                tipo=tipo, nombre=nombre, descripcion=desc,
                farmacia_origen=origen, json_data=json_str,
                n_items=len(items), creado_en=now_ar(),
            )
            session.add(archivo)
            session.commit()
            return jsonify({'ok': True, 'id': archivo.id, 'n_items': len(items)})

    @app.route('/api/compartido/listar')
    def api_compartido_listar():
        """Lista archivos disponibles (JSON). Opcionalmente filtra por tipo."""
        if not _auth_ok(request):
            return jsonify({'ok': False, 'error': 'No autorizado'}), 401
        tipo = request.args.get('tipo', '').strip() or None
        with get_db() as session:
            q = session.query(
                ArchivoCompartido.id,
                ArchivoCompartido.tipo,
                ArchivoCompartido.nombre,
                ArchivoCompartido.descripcion,
                ArchivoCompartido.farmacia_origen,
                ArchivoCompartido.n_items,
                ArchivoCompartido.creado_en,
            ).order_by(ArchivoCompartido.creado_en.desc())
            if tipo:
                q = q.filter(ArchivoCompartido.tipo == tipo)
            rows = q.limit(100).all()
        return jsonify({'ok': True, 'archivos': [
            {'id': r.id, 'tipo': r.tipo, 'nombre': r.nombre,
             'descripcion': r.descripcion, 'farmacia_origen': r.farmacia_origen,
             'n_items': r.n_items,
             'creado_en': r.creado_en.strftime('%d/%m/%Y %H:%M') if r.creado_en else ''}
            for r in rows
        ]})

    @app.route('/api/compartido/<int:archivo_id>/json')
    def api_compartido_json(archivo_id):
        """Descarga el JSON de un archivo específico."""
        if not _auth_ok(request):
            return jsonify({'ok': False, 'error': 'No autorizado'}), 401
        with get_db() as session:
            arch = session.get(ArchivoCompartido, archivo_id)
            if not arch:
                return jsonify({'ok': False, 'error': 'No encontrado'}), 404
            return jsonify({'ok': True, 'tipo': arch.tipo, 'nombre': arch.nombre,
                            'items': json.loads(arch.json_data)})

    # ── UI: pantalla de importación ──────────────────────────────────────────

    @app.route('/compartido')
    @login_required
    def compartido_index():
        """Lista archivos disponibles en el hub y permite importarlos."""
        archivos = []
        error_hub = None
        if _ES_HUB:
            with get_db() as session:
                rows = session.query(ArchivoCompartido).order_by(
                    ArchivoCompartido.creado_en.desc()).limit(100).all()
                archivos = [{
                    'id': r.id, 'tipo': r.tipo,
                    'tipo_label': _TIPOS_LABEL.get(r.tipo, r.tipo),
                    'nombre': r.nombre, 'descripcion': r.descripcion,
                    'farmacia_origen': r.farmacia_origen,
                    'n_items': r.n_items,
                    'creado_en': r.creado_en.strftime('%d/%m/%Y %H:%M') if r.creado_en else '',
                } for r in rows]
        elif _HUB_BASE_URL and _HUB_TOKEN:
            try:
                r = requests.get(f'{_HUB_BASE_URL}/api/compartido/listar',
                                 headers=_hub_headers(), timeout=10)
                data = r.json()
                if data.get('ok'):
                    archivos = [{**a, 'tipo_label': _TIPOS_LABEL.get(a['tipo'], a['tipo'])}
                                for a in data['archivos']]
                else:
                    error_hub = data.get('error', 'Error desconocido del hub')
            except Exception as e:
                error_hub = f'No se pudo conectar al hub: {e}'
        else:
            error_hub = 'HUB_BASE_URL o HUB_TOKEN no configurados.'

        return render_template('compartido.html',
                               archivos=archivos,
                               error_hub=error_hub,
                               es_hub=_ES_HUB,
                               tipos_label=_TIPOS_LABEL)

    @app.route('/compartido/<int:archivo_id>/importar', methods=['POST'])
    @login_required
    def compartido_importar(archivo_id):
        """Descarga un archivo del hub e importa su contenido a la DB local."""
        items_raw = None

        if _ES_HUB:
            with get_db() as session:
                arch = session.get(ArchivoCompartido, archivo_id)
                if not arch:
                    flash('Archivo no encontrado.', 'error')
                    return redirect(url_for('compartido_index'))
                tipo = arch.tipo
                nombre = arch.nombre
                items_raw = json.loads(arch.json_data)
        else:
            try:
                r = requests.get(f'{_HUB_BASE_URL}/api/compartido/{archivo_id}/json',
                                 headers=_hub_headers(), timeout=15)
                data = r.json()
                if not data.get('ok'):
                    flash(f'Error del hub: {data.get("error")}', 'error')
                    return redirect(url_for('compartido_index'))
                tipo = data['tipo']
                nombre = data['nombre']
                items_raw = data['items']
            except Exception as e:
                flash(f'No se pudo conectar al hub: {e}', 'error')
                return redirect(url_for('compartido_index'))

        n = _importar_items(tipo, nombre, items_raw)
        if n is None:
            flash(f'Tipo "{tipo}" no soportado para importación.', 'error')
        else:
            flash(f'Importados {n} registros de "{nombre}".', 'success')
        return redirect(url_for('compartido_index'))

    # ── API para compartir desde la app local al hub ─────────────────────────

    @app.route('/api/compartido/push', methods=['POST'])
    @login_required
    def api_compartido_push():
        """Sube un archivo procesado al hub (llamado desde la app local)."""
        data = request.get_json(silent=True) or {}
        tipo   = data.get('tipo', '').strip()
        nombre = data.get('nombre', '').strip()
        desc   = data.get('descripcion', '').strip() or None
        items  = data.get('items')
        if not tipo or not nombre or not isinstance(items, list):
            return jsonify({'ok': False, 'error': 'Faltan campos'}), 400

        payload = {
            'tipo': tipo, 'nombre': nombre, 'descripcion': desc,
            'farmacia_origen': str(_FARMACIA_ID), 'items': items,
        }

        if _ES_HUB:
            json_str = json.dumps(items, ensure_ascii=False)
            with get_db() as session:
                arch = ArchivoCompartido(
                    tipo=tipo, nombre=nombre, descripcion=desc,
                    farmacia_origen=str(_FARMACIA_ID),
                    json_data=json_str, n_items=len(items), creado_en=now_ar(),
                )
                session.add(arch)
                session.commit()
                return jsonify({'ok': True, 'id': arch.id, 'n_items': len(items)})
        elif _HUB_BASE_URL and _HUB_TOKEN:
            try:
                r = requests.post(f'{_HUB_BASE_URL}/api/compartido/subir',
                                  headers=_hub_headers(),
                                  data=json.dumps(payload, ensure_ascii=False),
                                  timeout=15)
                return jsonify(r.json())
            except Exception as e:
                return jsonify({'ok': False, 'error': str(e)}), 502
        else:
            return jsonify({'ok': False, 'error': 'HUB_BASE_URL o HUB_TOKEN no configurados'}), 503


def _importar_items(tipo, nombre, items):
    """Importa items al modelo local según tipo. Retorna n registros o None si tipo inválido."""
    if tipo == 'oferta_minimo':
        return _importar_oferta_minimo(items)
    return None


def _importar_oferta_minimo(items):
    """Upsert de OfertaMinimo por (ean, laboratorio_id, tipo_descuento)."""
    with get_db() as session:
        labs_cache = {l.nombre: l.id for l in session.query(
            Laboratorio.id, Laboratorio.nombre).all()}
        n = 0
        for it in items:
            lab_id = it.get('laboratorio_id')
            if not lab_id and it.get('laboratorio_nombre'):
                lab_id = labs_cache.get(it['laboratorio_nombre'])
            ean    = (it.get('ean') or '').strip()
            tipo_d = (it.get('tipo_descuento') or 'simple').strip()
            if not ean:
                continue
            existing = session.query(OfertaMinimo).filter_by(
                ean=ean, laboratorio_id=lab_id, tipo_descuento=tipo_d).first()
            if existing:
                obj = existing
            else:
                obj = OfertaMinimo(ean=ean, laboratorio_id=lab_id, tipo_descuento=tipo_d)
                session.add(obj)
            obj.descripcion     = it.get('descripcion')
            obj.codigo          = it.get('codigo')
            obj.unidades_minima = it.get('unidades_minima') or 1
            obj.descuento_psl   = it.get('descuento_psl')
            obj.rentabilidad    = it.get('rentabilidad')
            obj.plazo_pago      = it.get('plazo_pago')
            obj.grupo_id        = it.get('grupo_id')
            obj.activo          = True
            obj.actualizado_en  = now_ar()
            n += 1
        session.commit()
    return n
