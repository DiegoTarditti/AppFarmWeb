"""El filtro de cargos administrativos de /api/publica/paciente/<id>/compras.

Filtra datos en silencio, así que lo peligroso no es que deje pasar un cargo, sino
que se lleve puesto un producto real: ahí AppClinica muestra un histórico clínico
incompleto y nadie se entera. Los patrones son de dos palabras justamente por eso.
"""
import pytest

from routes.api_publica import es_cargo_administrativo


@pytest.mark.parametrize('desc', [
    'SELLADO DE RECETAS',
    'SELLADO RECETAS',
    'RETIRA EN FARMACIA',
    'Costo Receta/Cupón',
    'Costo Cupon',
    'sellado de recetas',        # minúsculas
    '  RETIRA EN FARMACIA  ',    # con espacios
])
def test_filtra_cargos_administrativos(desc):
    assert es_cargo_administrativo(desc), f'no filtró el cargo: {desc!r}'


@pytest.mark.parametrize('desc', [
    'IBUPIRAC 600 COMP X 10',
    'AMOXIDAL 500 COMP X 16',
    'GASA ESTERIL X 10',
    'RECETARIO MAGISTRAL',   # contiene "RECETA": con el patrón corto se filtraba
    'COSTO QUIRURGICO',      # contiene "COSTO": idem
    'SELLADOR DENTAL',       # contiene "SELLADO": idem
])
def test_no_filtra_productos_reales(desc):
    assert not es_cargo_administrativo(desc), f'se llevó puesto un producto: {desc!r}'


@pytest.mark.parametrize('desc', [None, '', '   '])
def test_sin_descripcion_no_explota(desc):
    assert es_cargo_administrativo(desc) is False
