"""Lock del sync de Kellerhoff en DB (P0: evitar doble scraping con --workers 2).

El threading.Lock viejo era una copia por worker → un segundo click en otro
worker arrancaba un segundo scraping. Ahora el lock vive en sync_lock (id=2) y el
acquire es un UPDATE atómico. Estos tests fijan que no se pueda tomar dos veces,
que se libere, que el zombie se pueda recuperar, y que no pise al lock de ObServer.
"""
from datetime import datetime, timedelta

import database
from routes import kellerhoff_sync as ks


def _reset_lock():
    with database.get_db() as s:
        s.query(database.SyncLock).delete()
        s.commit()


def test_no_se_puede_tomar_dos_veces():
    _reset_lock()
    assert ks._kh_lock_acquire() is True, 'primer acquire debería tomarlo'
    assert ks._kh_lock_acquire() is False, 'segundo acquire NO debe tomarlo (evita doble scraping)'


def test_release_libera():
    _reset_lock()
    assert ks._kh_lock_acquire() is True
    ks._kh_lock_release({'ok': True, 'creados': 3})
    assert ks._kh_lock_acquire() is True, 'tras release, se puede volver a tomar'
    est = ks._kh_lock_estado()
    # release deja el resultado leíble y en_curso vuelve a True por el nuevo acquire
    assert est['corriendo'] is True


def test_estado_expone_resultado_tras_release():
    _reset_lock()
    ks._kh_lock_acquire()
    ks._kh_lock_release({'ok': True, 'creados': 5, 'ligados': 2})
    est = ks._kh_lock_estado()
    assert est['corriendo'] is False
    assert est['resultado'] == {'ok': True, 'creados': 5, 'ligados': 2}
    assert est['ultimo'] is not None   # finalizado_en formateado


def test_msg_escribe_log_y_paso_en_db():
    _reset_lock()
    ks._kh_lock_acquire()
    ks._kh_log_buffer.clear()
    ks._msg('Iniciando Chromium…')
    ks._msg('[1/2] FAC 00046-00255782 → 2 ítem(s)')
    est = ks._kh_lock_estado()
    assert est['log'] == ['Iniciando Chromium…', '[1/2] FAC 00046-00255782 → 2 ítem(s)']
    # paso_actual es la última línea (recortada a 80)
    assert est['msg'] == '[1/2] FAC 00046-00255782 → 2 ítem(s)'


def test_paso_actual_se_recorta_a_80():
    _reset_lock()
    ks._kh_lock_acquire()
    ks._kh_log_buffer.clear()
    linea = 'X' * 200
    ks._msg(linea)
    with database.get_db() as s:
        row = s.query(database.SyncLock).filter_by(id=ks._KH_LOCK_ID).first()
    assert len(row.paso_actual) == 80         # paso_actual (VARCHAR 80) recortado
    assert ks._kh_lock_estado()['log'] == [linea]   # el log guarda la línea entera


def test_lock_zombie_se_recupera():
    _reset_lock()
    ks._kh_lock_acquire()   # queda en_curso=True
    # Simular worker muerto: iniciado_en viejo (> timeout).
    viejo = datetime.now() - timedelta(minutes=ks._KH_LOCK_TIMEOUT_MIN + 5)
    with database.get_db() as s:
        s.query(database.SyncLock).filter_by(id=ks._KH_LOCK_ID).update(
            {'iniciado_en': viejo})
        s.commit()
    assert ks._kh_lock_acquire() is True, 'un lock abandonado se puede tomar'


def test_no_pisa_el_lock_de_observer():
    _reset_lock()
    # ObServer (id=1) en curso.
    with database.get_db() as s:
        s.add(database.SyncLock(id=1, en_curso=True, iniciado_en=datetime.now()))
        s.commit()
    # Kellerhoff (id=2) debe poder tomarse igual — son locks distintos.
    assert ks._kh_lock_acquire() is True
    with database.get_db() as s:
        obs = s.query(database.SyncLock).filter_by(id=1).first()
    assert obs.en_curso is True, 'el lock de ObServer no se tocó'
