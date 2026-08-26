"""Chequeo de EANs contra Kellerhoff para avisar en el armado del pedido.

`eans_faltantes_kellerhoff` devuelve los EANs que Kellerhoff NO va a reconocer:
ni están en su catálogo ni se resuelven por equivalencia (a un código con EAN).
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import database
from database import KellerhoffCatalogo, KellerhoffEquivalencia
from routes.kellerhoff import KEL_NO_DISPONIBLE, eans_faltantes_kellerhoff


def _s():
    eng = create_engine('sqlite:///:memory:')
    database.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def test_sin_catalogo_no_avisa():
    # Sin catálogo cargado no hay base para afirmar → set vacío (no falsos avisos).
    assert eans_faltantes_kellerhoff(_s(), ['123', '456']) == set()


def test_directo_equivalencia_y_faltante():
    s = _s()
    # Catálogo: EAN 111 directo; código K9 (para una equivalencia) con EAN propio.
    s.add(KellerhoffCatalogo(codigo_kellerhoff='K1', ean='111'))
    s.add(KellerhoffCatalogo(codigo_kellerhoff='K9', ean='999'))
    # Equivalencia: nuestro EAN 222 → K9 (Kellerhoff lo va a encontrar como 999).
    s.add(KellerhoffEquivalencia(ean='222', codigo_kellerhoff='K9'))
    # Equivalencia marcada NO_DISPONIBLE: Kellerhoff no lo trae.
    s.add(KellerhoffEquivalencia(ean='333', codigo_kellerhoff=KEL_NO_DISPONIBLE))
    s.commit()

    faltan = eans_faltantes_kellerhoff(s, ['111', '222', '333', '444'])
    # 111 directo (ok), 222 resuelto por equivalencia (ok) → NO faltan.
    # 333 NO_DISPONIBLE y 444 sin nada → faltan.
    assert faltan == {'333', '444'}


def test_equivalencia_a_codigo_sin_ean_cuenta_como_faltante():
    s = _s()
    s.add(KellerhoffCatalogo(codigo_kellerhoff='K1', ean='111'))
    # KP es un pack sin EAN en el catálogo → mandar ese EAN no sirve.
    s.add(KellerhoffCatalogo(codigo_kellerhoff='KP', ean=None))
    s.add(KellerhoffEquivalencia(ean='555', codigo_kellerhoff='KP'))
    s.commit()
    assert eans_faltantes_kellerhoff(s, ['555']) == {'555'}


def test_lista_vacia():
    assert eans_faltantes_kellerhoff(_s(), []) == set()
