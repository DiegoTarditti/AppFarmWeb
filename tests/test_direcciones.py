"""Tests del parser de direcciones (bot/direcciones.py) + integración con
DomicilioCliente y backfill.

Casos cubiertos de la tabla de aceptación de docs/tarea_domicilios_estructurados.md:
- Parser puro: 1 test por fila + 3 casos extra (sin unidad, entre calles, casing).
- Geocoder: mock que verifica que recibe calle+número limpio.
- Persistencia: DomicilioCliente guarda 4 campos separados.
- Backfill: fila legacy mixta se estructura por el parser.
"""
import pytest
from sqlalchemy import text

import database
from database import DomicilioCliente
from bot import direcciones


# ── Parser puro (casos de la tabla de aceptación) ──────────────────────────

def test_separar_direccion_dto_numero():
    r = direcciones.separar_direccion('bolivia 1614 DTO 2')
    assert r == {'direccion': 'bolivia 1614', 'piso': None,
                 'depto': '2', 'referencia': None}


def test_separar_direccion_piso_numero():
    r = direcciones.separar_direccion('San Martín 100 piso 1')
    assert r == {'direccion': 'San Martín 100', 'piso': '1',
                 'depto': None, 'referencia': None}


def test_separar_direccion_depto_letra():
    r = direcciones.separar_direccion('Av Pellegrini 1234 depto B')
    assert r == {'direccion': 'Av Pellegrini 1234', 'piso': None,
                 'depto': 'B', 'referencia': None}


def test_separar_direccion_pb():
    r = direcciones.separar_direccion('Mendoza 2500 PB')
    assert r == {'direccion': 'Mendoza 2500', 'piso': 'PB',
                 'depto': None, 'referencia': None}


def test_separar_direccion_calle_con_numero_en_nombre():
    """Caso crítico: 'Pasaje 3 de Febrero 1614 dto 2' NO debe romperse."""
    r = direcciones.separar_direccion('Pasaje 3 de Febrero 1614 dto 2')
    assert r == {'direccion': 'Pasaje 3 de Febrero 1614', 'piso': None,
                 'depto': '2', 'referencia': None}


def test_separar_direccion_ordinal_y_letra_suelta():
    """Caso crítico: 'Rioja 950 1° B' → piso='1', depto='B' (sin keyword dto)."""
    r = direcciones.separar_direccion('Rioja 950 1° B')
    assert r == {'direccion': 'Rioja 950', 'piso': '1',
                 'depto': 'B', 'referencia': None}


def test_separar_direccion_monoblock_y_dto():
    r = direcciones.separar_direccion('Av Francia 2000 monoblock 4 dto 12')
    assert r['direccion'] == 'Av Francia 2000'
    assert r['depto'] == '12'
    assert r['referencia'] == 'monoblock 4'
    assert r['piso'] is None


# ── Parser: casos extra ────────────────────────────────────────────────────

def test_separar_direccion_sin_unidad():
    r = direcciones.separar_direccion('Bolivia 1614')
    assert r == {'direccion': 'Bolivia 1614', 'piso': None,
                 'depto': None, 'referencia': None}


def test_separar_direccion_vacio_o_none():
    for inp in ('', None, '   '):
        r = direcciones.separar_direccion(inp)
        assert r == {'direccion': '', 'piso': None,
                     'depto': None, 'referencia': None}


def test_separar_preserva_casing_y_acentos():
    """Verifica regla B: dirección conserva mayúsculas y acentos del input."""
    r = direcciones.separar_direccion('San Martín 100 piso 1')
    assert r['direccion'] == 'San Martín 100'   # mayúscula S + acento í
    assert r['piso'] == '1'


def test_separar_direccion_entre_calles():
    r = direcciones.separar_direccion('San Juan 1500 entre Mitre y Belgrano')
    assert r['direccion'] == 'San Juan 1500'
    assert 'entre' in r['referencia'].lower()
    assert 'mitre' in r['referencia'].lower()
    assert 'belgrano' in r['referencia'].lower()


def test_separar_direccion_piso_dto_combinados():
    r = direcciones.separar_direccion('Av San Martín 2000 piso 2 dto B')
    assert r['direccion'] == 'Av San Martín 2000'
    assert r['piso'] == '2'
    assert r['depto'] == 'B'


def test_separar_direccion_uf():
    r = direcciones.separar_direccion('Brasil 800 UF 5')
    assert r['direccion'] == 'Brasil 800'
    assert r['depto'] == '5'


# ── Geocoder usa solo calle+número ────────────────────────────────────────

def test_geocode_usa_solo_calle_numero(monkeypatch):
    """Mockeando _georef_una: el geocoder debe recibir la dirección LIMPIA,
    sin el sufijo 'dto 2' (si no, georef-ar no encuentra la calle o devuelve
    resultados fuera de Rosario)."""
    from bot import envio

    # Mock _georef_una: captura el argumento 'direccion' que recibe.
    capturado = {}

    def fake_georef(direccion, provincia, localidad):
        capturado['direccion'] = direccion
        return (-32.95, -60.65)   # coords fake

    monkeypatch.setattr(envio, '_georef_una', fake_georef)

    envio.geocodificar('Pasaje 3 de Febrero 1614 dto 2',
                       provincia='santa fe', localidad='Rosario')

    # El geocoder debe haber recibido el input sin 'dto 2'
    assert capturado['direccion'] == 'Pasaje 3 de Febrero 1614'
    # No debe contener el sufijo
    assert 'dto' not in capturado['direccion'].lower()


def test_geocode_no_modifica_direccion_limpia(monkeypatch):
    """Si la dirección ya viene sin unidad, geocoder la manda tal cual."""
    from bot import envio

    capturado = {}

    def fake_georef(direccion, provincia, localidad):
        capturado['direccion'] = direccion
        return (-32.95, -60.65)

    monkeypatch.setattr(envio, '_georef_una', fake_georef)
    envio.geocodificar('Bolivia 1614', provincia='santa fe')
    assert capturado['direccion'] == 'Bolivia 1614'


# ── Persistencia: DomicilioCliente con 4 campos ───────────────────────────

@pytest.fixture
def session():
    s = database.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def test_domicilio_guarda_campos_separados(session):
    d = DomicilioCliente(conversacion_id=None, cliente_id=None,
                        etiqueta='Casa', direccion='San Martín 100',
                        piso='1', depto='B',
                        referencia='monoblock 4', lat=-32.95, lng=-60.65,
                        origen='manual')
    session.add(d)
    session.commit()
    session.expire_all()

    rec = session.get(DomicilioCliente, d.id)
    assert rec.direccion == 'San Martín 100'
    assert rec.piso == '1'
    assert rec.depto == 'B'
    assert rec.referencia == 'monoblock 4'


# ── Backfill: fila legacy mixta se estructura ──────────────────────────────

def test_backfill_estructura_legacy(session, monkeypatch, caplog):
    """Fila con direccion='bolivia 1614 DTO 2' + piso/depto/referencia NULL
    → el backfill la estructura: direccion='bolivia 1614', depto='2'."""
    d = DomicilioCliente(conversacion_id=None, cliente_id=None,
                        etiqueta='Casa',
                        direccion='bolivia 1614 DTO 2',
                        piso=None, depto=None, referencia=None,
                        origen='legacy')
    session.add(d)
    session.commit()
    legacy_id = d.id

    # Forzar el env var que gatea el backfill en _ejecutar_backfills_async.
    monkeypatch.setenv('RUN_BACKFILLS', '1')

    # Llamada directa al helper (sin pasar por el gate de RUN_BACKFILLS):
    from database import _ejecutar_backfills_async
    import logging
    with caplog.at_level(logging.INFO, logger='database'):
        _ejecutar_backfills_async()

    session.expire_all()
    rec = session.get(DomicilioCliente, legacy_id)
    assert rec.direccion == 'bolivia 1614'
    assert rec.depto == '2'
    assert rec.piso is None
    assert rec.referencia is None


def test_backfill_idempotente(session, monkeypatch):
    """Si la fila ya está estructurada, el backfill no la vuelve a tocar."""
    d = DomicilioCliente(conversacion_id=None, cliente_id=None,
                        etiqueta='Casa',
                        direccion='San Martín 100',
                        piso='1', depto='B', referencia=None,
                        origen='manual')
    session.add(d)
    session.commit()
    legacy_id = d.id

    monkeypatch.setenv('RUN_BACKFILLS', '1')
    from database import _ejecutar_backfills_async
    _ejecutar_backfills_async()

    session.expire_all()
    rec = session.get(DomicilioCliente, legacy_id)
    # Sin cambios (gate por los 3 campos NULL no se cumple)
    assert rec.direccion == 'San Martín 100'
    assert rec.piso == '1'
    assert rec.depto == 'B'