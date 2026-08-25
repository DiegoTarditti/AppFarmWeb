"""Verificación de que la mercadería cargada entró de verdad al robot.

Registrar una carga no prueba nada: guarda lo que el operador declaró. El
25/8/2026 se registraron 23 packs y el robot nunca los tomó — su total sólo bajó
(10.115 → 10.102) y los 11 artículos quedaron con el mismo stock. La app los
daba por cargados.
"""
import pytest

from services.rowa_analisis import clasificar_carga
# El fixture con todos los routers vive en el smoke.
from tests.test_smoke_routes import smoke_app, smoke_client  # noqa: F401


def test_el_stock_subio_lo_cargado():
    assert clasificar_carga(cantidad=5, stock_antes=14, stock_despues=19) == 'confirmada'


def test_subio_mas_de_lo_cargado_tambien_confirma():
    """Puede haber entrado algo más por otra vía; lo declarado está adentro."""
    assert clasificar_carga(cantidad=5, stock_antes=14, stock_despues=25) == 'confirmada'


def test_el_caso_del_25_de_agosto():
    """No subió nada: o sigue en la cinta, o el robot no la tomó."""
    assert clasificar_carga(cantidad=5, stock_antes=14, stock_despues=14) == 'no_detectada'


def test_si_el_stock_bajo_tampoco_se_detecta():
    """Bajar entre las dos mediciones significa que se despachó más de lo que
    entró: la carga no se puede dar por buena."""
    assert clasificar_carga(cantidad=5, stock_antes=14, stock_despues=12) == 'no_detectada'


def test_subio_menos_de_lo_cargado_es_parcial():
    assert clasificar_carga(cantidad=6, stock_antes=28, stock_despues=31) == 'parcial'


@pytest.mark.parametrize('antes,despues', [(None, 19), (14, None), (None, None)])
def test_sin_las_dos_mediciones_no_se_opina(antes, despues):
    """Si el robot no respondía al registrar, no hay "antes" contra el cual
    comparar: queda pendiente en vez de inventar un veredicto."""
    assert clasificar_carga(cantidad=5, stock_antes=antes, stock_despues=despues) == 'pendiente'


def test_una_carga_de_cero_no_se_confirma_sola():
    """Sin cantidad declarada no hay nada que verificar; que el stock no se
    mueva no la convierte en válida."""
    assert clasificar_carga(cantidad=0, stock_antes=14, stock_despues=14) == 'no_detectada'


# ── Recuperar el "antes" de las cargas viejas ───────────────────────────────

def test_una_carga_sin_stock_antes_se_verifica_con_el_snapshot_previo(smoke_client):
    """Las cargas registradas antes de que existiera `stock_antes` quedaban
    'pendiente' PARA SIEMPRE: el botón de verificar no las resolvía nunca. El
    "antes" existe igual — es el último snapshot previo a la carga."""
    from datetime import datetime
    from unittest.mock import patch

    import database
    import routes.rowa as rowa

    momento = datetime(2026, 8, 25, 18, 45)
    with database.get_db() as s:
        # snapshot ANTES de la carga (el "antes" recuperable)
        s.add(database.RowaSnapshot(tomado_en=datetime(2026, 8, 25, 18, 38),
                                    article_id='21398', cantidad=14))
        # la carga, sin stock_antes (como quedaron las viejas)
        s.add(database.RowaCarga(sesion_id='x', article_id='21398', cantidad=5,
                                 cargado_en=momento, estado='pendiente'))
        s.commit()

    # El robot sigue reportando 14: la mercadería no entró (el caso del 25/8).
    class _Fila:
        article_id, cantidad = '21398', 14

    with patch.object(rowa, '_cargar', lambda refresh=False: {'filas': [_Fila()],
                                                              'generado': momento}):
        r = smoke_client.post('/rowa/carga/verificar')

    assert r.status_code == 200
    assert r.get_json()['resumen']['no_detectada'] == 1, (
        'con el snapshot previo tiene que poder concluir, no quedar pendiente')

    with database.get_db() as s:
        c = s.query(database.RowaCarga).filter_by(article_id='21398').first()
        assert c.stock_antes == 14, 'el antes recuperado se guarda'
        assert c.estado == 'no_detectada'
