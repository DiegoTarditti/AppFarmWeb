"""Tests para los scripts de migración EANs (Fase 1.2 + Fase 2).

Cubre:
- backfill_codigos_barra.ejecutar(): inserta principal + alts, idempotente,
  dry-run no toca nada, skipea filas existentes, ignora alt vacíos / iguales
  al principal.
- bridge_productos_observer.ejecutar(): vincula por EAN o alfabeta cuando
  match único, deja en dudosos cuando hay múltiples, respeta UNIQUE de
  observer_id (no duplica), no toca productos ya vinculados.
"""
import pytest

import database
from database import (
    ObsCodigoBarras,
    ObsNombreDroga,
    ObsProducto,
    Producto,
    ProductoCodigoBarra,
)


@pytest.fixture
def session():
    s = database.SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── helpers de fixture ────────────────────────────────────────────────────

def _crear_producto(s, codigo_barra, alt1=None, alt2=None, alt3=None,
                    descripcion='Test', codigo_alfabeta=None, observer_id=None):
    p = Producto(codigo_barra=codigo_barra, descripcion=descripcion,
                 codigo_barra_alt1=alt1, codigo_barra_alt2=alt2,
                 codigo_barra_alt3=alt3, codigo_alfabeta=codigo_alfabeta,
                 observer_id=observer_id)
    s.add(p)
    s.commit()
    return p


def _crear_obs_producto(s, observer_id, descripcion='Obs', codigo_alfabeta=None):
    op = ObsProducto(observer_id=observer_id, descripcion=descripcion,
                     codigo_alfabeta=codigo_alfabeta)
    s.add(op)
    s.commit()
    return op


def _crear_obs_codigo_barras(s, id_, producto_observer, ean, fecha_baja=None):
    cb = ObsCodigoBarras(id_codigo_barras=id_, producto_observer=producto_observer,
                          codigo_barras=ean, orden=1, fecha_baja=fecha_baja)
    s.add(cb)
    s.commit()
    return cb


# ── Fase 1.2: backfill_codigos_barra ──────────────────────────────────────

def test_backfill_inserta_principal_y_alts(session):
    p = _crear_producto(session, '7790001', alt1='7790002', alt2='7790003')
    from scripts.backfill_codigos_barra import ejecutar
    stats = ejecutar(dry_run=False)
    assert stats['principales_insertados'] == 1
    assert stats['alts_insertados'] == 2

    # Verificar en DB
    rows = session.query(ProductoCodigoBarra).filter_by(producto_id=p.id).all()
    assert len(rows) == 3
    principales = [r for r in rows if r.es_principal]
    assert len(principales) == 1
    assert principales[0].codigo_barra == '7790001'
    assert principales[0].fuente == 'legacy_principal'
    alts = sorted([r.codigo_barra for r in rows if not r.es_principal])
    assert alts == ['7790002', '7790003']
    for r in rows:
        if not r.es_principal:
            assert r.fuente == 'legacy_alt'


def test_backfill_dry_run_no_escribe(session):
    p = _crear_producto(session, '7790001', alt1='7790002')
    from scripts.backfill_codigos_barra import ejecutar
    stats = ejecutar(dry_run=True)
    assert stats['principales_insertados'] == 1
    assert stats['alts_insertados'] == 1
    # Nada en DB
    assert session.query(ProductoCodigoBarra).count() == 0


def test_backfill_idempotente(session):
    _crear_producto(session, '7790001', alt1='7790002')
    from scripts.backfill_codigos_barra import ejecutar
    ejecutar(dry_run=False)
    # Segunda corrida: todo ya existe
    stats = ejecutar(dry_run=False)
    assert stats['principales_insertados'] == 0
    assert stats['alts_insertados'] == 0
    assert stats['saltados_existentes'] == 2  # principal + alt1


def test_backfill_ignora_alt_igual_al_principal(session):
    _crear_producto(session, '7790001', alt1='7790001', alt2='7790002')
    from scripts.backfill_codigos_barra import ejecutar
    stats = ejecutar(dry_run=False)
    # alt1 == codigo_barra → skipeado por la lógica del script
    assert stats['principales_insertados'] == 1
    assert stats['alts_insertados'] == 1


def test_backfill_producto_sin_alts(session):
    _crear_producto(session, '7790001')  # sin alts
    from scripts.backfill_codigos_barra import ejecutar
    stats = ejecutar(dry_run=False)
    assert stats['productos_sin_alts'] == 1
    assert stats['principales_insertados'] == 1
    assert stats['alts_insertados'] == 0


# ── Fase 2: bridge_productos_observer ─────────────────────────────────────

def test_bridge_match_unico_por_ean(session):
    # Producto local con EAN '999' / obs con mismo EAN en obs_codigos_barras
    p = _crear_producto(session, '999', descripcion='LocalProd')
    _crear_obs_producto(session, observer_id=42, descripcion='ObsProd')
    _crear_obs_codigo_barras(session, id_=1, producto_observer=42, ean='999')

    from scripts.bridge_productos_observer import ejecutar
    stats = ejecutar(dry_run=False)
    assert stats['vinculados_ean'] == 1
    assert stats['dudosos'] == 0

    session.expire_all()
    p2 = session.get(Producto, p.id)
    assert p2.observer_id == 42


def test_bridge_dry_run_no_escribe(session):
    p = _crear_producto(session, '999')
    _crear_obs_producto(session, observer_id=42)
    _crear_obs_codigo_barras(session, id_=1, producto_observer=42, ean='999')

    from scripts.bridge_productos_observer import ejecutar
    stats = ejecutar(dry_run=True)
    assert stats['vinculados_ean'] == 1

    session.expire_all()
    p2 = session.get(Producto, p.id)
    assert p2.observer_id is None  # dry-run no escribió


def test_bridge_match_multiple_es_dudoso(session):
    # 2 obs_productos con el mismo EAN → ambiguo
    p = _crear_producto(session, '999')
    _crear_obs_producto(session, observer_id=42)
    _crear_obs_producto(session, observer_id=43)
    _crear_obs_codigo_barras(session, id_=1, producto_observer=42, ean='999')
    _crear_obs_codigo_barras(session, id_=2, producto_observer=43, ean='999')

    from scripts.bridge_productos_observer import ejecutar
    stats = ejecutar(dry_run=False)
    assert stats['dudosos'] == 1
    assert stats['vinculados_ean'] == 0
    assert len(stats['ejemplos_dudosos']) == 1
    assert sorted(stats['ejemplos_dudosos'][0]['obs_candidatos']) == [42, 43]

    session.expire_all()
    p2 = session.get(Producto, p.id)
    assert p2.observer_id is None  # no se vincula cuando es ambiguo


def test_bridge_skip_ya_vinculados(session):
    _crear_obs_producto(session, observer_id=42)
    _crear_producto(session, '999', observer_id=42)  # ya linked

    from scripts.bridge_productos_observer import ejecutar
    stats = ejecutar(dry_run=False)
    assert stats['ya_vinculados'] == 1
    assert stats['vinculados_ean'] == 0


def test_bridge_match_por_alfabeta_cuando_no_hay_ean(session):
    p = _crear_producto(session, '999', codigo_alfabeta='ALF1')
    _crear_obs_producto(session, observer_id=42, codigo_alfabeta='ALF1')
    # Sin obs_codigos_barras → fallback a alfabeta

    from scripts.bridge_productos_observer import ejecutar
    stats = ejecutar(dry_run=False)
    assert stats['vinculados_alfabeta'] == 1
    assert stats['vinculados_ean'] == 0

    session.expire_all()
    p2 = session.get(Producto, p.id)
    assert p2.observer_id == 42


def test_bridge_respeta_observer_id_tomado(session):
    # obs_id=42 ya tomado por otro producto → no se vuelve a usar
    _crear_obs_producto(session, observer_id=42)
    _crear_producto(session, 'OTRO', observer_id=42, descripcion='Tomado')
    # Otro producto local con mismo EAN apuntando a obs 42 → ya tomado
    p = _crear_producto(session, 'NUEVO')
    _crear_obs_codigo_barras(session, id_=1, producto_observer=42, ean='NUEVO')

    from scripts.bridge_productos_observer import ejecutar
    stats = ejecutar(dry_run=False)
    # obs 42 ya tomado → producto_id de p NO matchea
    assert stats['sin_match'] == 1
    assert stats['vinculados_ean'] == 0

    session.expire_all()
    p2 = session.get(Producto, p.id)
    assert p2.observer_id is None


def test_bridge_skip_obs_baja(session):
    p = _crear_producto(session, '999')
    _crear_obs_producto(session, observer_id=42)
    # codigo de barra dado de baja → no debería matchear
    from datetime import datetime
    _crear_obs_codigo_barras(session, id_=1, producto_observer=42, ean='999',
                              fecha_baja=datetime.now())

    from scripts.bridge_productos_observer import ejecutar
    stats = ejecutar(dry_run=False)
    assert stats['sin_match'] == 1
    assert stats['vinculados_ean'] == 0


def test_bridge_ean_desde_tabla_1_a_n(session):
    """El bridge también debe leer EANs de producto_codigos_barra (no solo alt1/2/3)."""
    p = _crear_producto(session, 'PRINCIPAL')
    # EAN nuevo solo en la 1-a-N, no en alt1/2/3
    session.add(ProductoCodigoBarra(producto_id=p.id, codigo_barra='ALT_NEW',
                                     es_principal=False, fuente='factura'))
    session.commit()
    _crear_obs_producto(session, observer_id=42)
    _crear_obs_codigo_barras(session, id_=1, producto_observer=42, ean='ALT_NEW')

    from scripts.bridge_productos_observer import ejecutar
    stats = ejecutar(dry_run=False)
    assert stats['vinculados_ean'] == 1
    session.expire_all()
    p2 = session.get(Producto, p.id)
    assert p2.observer_id == 42
