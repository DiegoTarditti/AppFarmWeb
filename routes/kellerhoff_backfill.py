"""Backfill del histórico Kellerhoff desde el export masivo de comprobantes (PDF).

El sync solo cubre 60 días. Este flujo toma el PDF "Comprobantes-…" que agrupa
cientos de facturas/NC y completa las facturas YA existentes (matcheadas por
número) con sus ítems + dto + TRF, sin depender del portal.

Corre en un thread daemon (el parse de >1000 páginas no entra en un request) y
publica progreso en `sync_lock` id=3, igual patrón que el sync (comparte entre
workers de gunicorn).
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import text as _text
from werkzeug.utils import secure_filename

import database
from database import get_db

_BF_LOCK_ID = 3
_BF_TIMEOUT_MIN = 60
KELLERHOFF_CUIT = '30539756490'
_bf_log_buffer: list[str] = []


def _lock_acquire() -> bool:
    with get_db() as s:
        if s.query(database.SyncLock).filter_by(id=_BF_LOCK_ID).first() is None:
            s.add(database.SyncLock(id=_BF_LOCK_ID, en_curso=False))
            s.commit()
        umbral = datetime.now() - timedelta(minutes=_BF_TIMEOUT_MIN)
        r = s.execute(_text(
            "UPDATE sync_lock SET en_curso=:on, iniciado_en=:now, finalizado_en=NULL, "
            "ultimo_resultado=NULL, log=NULL WHERE id=:lid AND "
            "(en_curso=:off OR iniciado_en IS NULL OR iniciado_en < :umbral)"),
            {'on': True, 'off': False, 'now': datetime.now(), 'umbral': umbral,
             'lid': _BF_LOCK_ID})
        s.commit()
        return r.rowcount == 1


def _lock_release(resultado=None) -> None:
    with get_db() as s:
        s.execute(_text(
            "UPDATE sync_lock SET en_curso=:off, finalizado_en=:now, ultimo_resultado=:res "
            "WHERE id=:lid"),
            {'off': False, 'now': datetime.now(), 'lid': _BF_LOCK_ID,
             'res': json.dumps(resultado, default=str) if resultado else None})
        s.commit()


def _estado() -> dict:
    with get_db() as s:
        row = s.query(database.SyncLock).filter_by(id=_BF_LOCK_ID).first()
    if row is None:
        return {'corriendo': False, 'msg': '', 'log': [], 'resultado': None, 'ultimo': None}
    try:
        resultado = json.loads(row.ultimo_resultado) if row.ultimo_resultado else None
    except (ValueError, TypeError):
        resultado = None
    return {'corriendo': bool(row.en_curso), 'msg': row.paso_actual or '',
            'log': row.log.split('\n') if row.log else [], 'resultado': resultado,
            'ultimo': row.finalizado_en.strftime('%d/%m/%Y %H:%M') if row.finalizado_en else None}


def _msg(texto: str) -> None:
    _bf_log_buffer.append(texto)
    if len(_bf_log_buffer) > 400:
        del _bf_log_buffer[:len(_bf_log_buffer) - 400]
    try:
        with get_db() as s:
            s.execute(_text("UPDATE sync_lock SET paso_actual=:p, log=:l WHERE id=:lid"),
                      {'p': texto[:80], 'l': '\n'.join(_bf_log_buffer), 'lid': _BF_LOCK_ID})
            s.commit()
    except Exception:
        pass


def _run(pdf_path: str) -> None:
    global _bf_log_buffer
    _bf_log_buffer = []
    resultado = None
    try:
        from services.kellerhoff_bulk_pdf import backfill, iter_comprobantes_texto, leer_pdf_texto
        _msg('Leyendo el PDF…')
        texto = leer_pdf_texto(pdf_path)
        _msg('Parseando comprobantes…')
        comps = list(iter_comprobantes_texto(texto))
        _msg(f'{len(comps)} comprobante(s) parseados. Aplicando backfill…')
        with get_db() as session:
            resultado = backfill(session, comps, KELLERHOFF_CUIT, log=_msg)
        _msg(f'Listo: {resultado["facturas_backfill"]} facturas completadas, '
             f'{resultado["items_creados"]} ítems.')
    except Exception as e:  # noqa: BLE001
        resultado = {'ok': False, 'error': str(e)}
        _msg(f'Error: {e}')
    finally:
        try:
            os.unlink(pdf_path)
        except OSError:
            pass
        _lock_release(resultado)


def init_app(app):

    @app.route('/kellerhoff/backfill')
    @login_required
    def kellerhoff_backfill():
        return render_template('kellerhoff_backfill.html', estado=_estado())

    @app.route('/kellerhoff/backfill/run', methods=['POST'])
    @login_required
    def kellerhoff_backfill_run():
        f = request.files.get('pdf')
        if not f or not f.filename:
            flash('Subí el PDF del export de comprobantes.', 'error')
            return redirect(url_for('kellerhoff_backfill'))
        if not _lock_acquire():
            flash('Ya hay un backfill en curso.', 'error')
            return redirect(url_for('kellerhoff_backfill'))
        nombre = secure_filename(f.filename) or 'comprobantes.pdf'
        destino = os.path.join(app.config['UPLOAD_FOLDER'], f'bf_{nombre}')
        f.save(destino)
        threading.Thread(target=_run, args=(destino,), daemon=True).start()
        flash('Backfill iniciado. Seguí el progreso abajo.')
        return redirect(url_for('kellerhoff_backfill'))

    @app.route('/kellerhoff/backfill/estado')
    @login_required
    def kellerhoff_backfill_estado():
        return jsonify(_estado())
