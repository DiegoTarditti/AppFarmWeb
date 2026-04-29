# Spec — Tests de catalogación (10 casos)

Documento para la sesión que va a implementar los tests del módulo `catalogacion.py`. Los 10 casos surgen del handoff de la sesión B ([sesion-b-handoff-2026-04-29.md](sesion-b-handoff-2026-04-29.md)) y cubren tanto `extraer_de_descripcion()` como `match_dimensional_candidatos()`.

## Setup

**Archivo a crear**: `tests/test_catalogacion.py`

**Imports estándar** (mirá [tests/test_pack_modulos.py](../tests/test_pack_modulos.py) o [tests/test_producto_matcher.py](../tests/test_producto_matcher.py) como referencia):

```python
import pytest
from decimal import Decimal
import database
from catalogacion import (
    extraer_de_descripcion,
    match_dimensional_candidatos,
)
```

**Fixture base**: usar `database.SessionLocal()` directo. La conftest ya monta SQLite en memoria + truncado entre tests, no hace falta nada extra.

```python
@pytest.fixture
def session():
    s = database.SessionLocal()
    try:
        yield s
    finally:
        s.close()
```

**Helper para crear productos con atributos** (recomendado para no repetir):

```python
def _crear_producto_con_atributos(session, codigo_barra, descripcion,
                                   monodroga_norm=None, monodroga_display=None,
                                   concentracion_mg=None, concentracion_unidad=None,
                                   forma_farma=None, cantidad_envase=None,
                                   fuente='manual'):
    p = database.Producto(
        codigo_barra=codigo_barra,
        descripcion=descripcion,
    )
    session.add(p)
    session.flush()
    atr = database.ProductoAtributo(
        producto_id=p.id,
        monodroga_norm=monodroga_norm,
        monodroga_display=monodroga_display,
        concentracion_mg=Decimal(str(concentracion_mg)) if concentracion_mg else None,
        concentracion_unidad=concentracion_unidad,
        forma_farma=forma_farma,
        cantidad_envase=Decimal(str(cantidad_envase)) if cantidad_envase else None,
        fuente=fuente,
        confianza='alta',
    )
    session.add(atr)
    session.commit()
    return p, atr
```

## Cómo correr

```bash
docker exec appfarmweb-web-1 sh -c "cd /app && python -m pytest tests/test_catalogacion.py -v"
```

O todos los tests del repo:

```bash
docker exec appfarmweb-web-1 sh -c "cd /app && python -m pytest tests/ -v"
```

## Los 10 tests

### Test 1 — Datos completos (score=12)

Los 4 atributos matchean exacto → score = 5+3+2+2 = 12.

```python
def test_match_completo_score_12(session):
    _crear_producto_con_atributos(
        session, '7791111111111', 'IBUPIRAC 600 MG CPR x 30',
        monodroga_norm='ibuprofeno', monodroga_display='IBUPROFENO',
        concentracion_mg=600, concentracion_unidad='MG',
        forma_farma='CPR', cantidad_envase=30,
    )
    candidatos = match_dimensional_candidatos(
        session,
        monodroga_norm='ibuprofeno',
        concentracion_mg=600,
        forma_farma='CPR',
        cantidad_envase=30,
    )
    assert len(candidatos) == 1
    assert candidatos[0]['score'] == 12
    assert candidatos[0]['codigo_barra'] == '7791111111111'
```

### Test 2 — Solo descripción (extracción + match)

Pasar solo `desc='IBUPIRAC 600 X 10 CPR'` y verificar que extrae los atributos y matchea.

```python
def test_match_solo_descripcion(session):
    _crear_producto_con_atributos(
        session, '7791111111112', 'IBUPIRAC 600 MG CPR x 10',
        monodroga_norm='ibuprofeno', monodroga_display='IBUPROFENO',
        concentracion_mg=600, concentracion_unidad='MG',
        forma_farma='CPR', cantidad_envase=10,
    )
    # Pasamos solo descripcion + monodroga (la droga no se extrae solo del texto;
    # los demás atributos sí).
    candidatos = match_dimensional_candidatos(
        session,
        descripcion='IBUPIRAC 600 X 10 CPR',
        monodroga_norm='ibuprofeno',
    )
    assert len(candidatos) >= 1
    # Score esperado: 5 (droga) + 3 (conc 600) + 2 (CPR) + 2 (cant 10) = 12
    assert candidatos[0]['score'] == 12
```

### Test 3 — Score exacto 5 (solo droga, distinta concentración)

Catálogo tiene IBUPIRAC 400, buscamos IBUPIRAC 600. Solo matchea droga.

```python
def test_match_score_5_solo_droga(session):
    _crear_producto_con_atributos(
        session, '7791111111113', 'IBUPIRAC 400 CPR x 20',
        monodroga_norm='ibuprofeno', concentracion_mg=400,
        forma_farma='CPR', cantidad_envase=20,
    )
    candidatos = match_dimensional_candidatos(
        session,
        monodroga_norm='ibuprofeno',
        concentracion_mg=600,
        forma_farma='CAP',     # distinto a CPR
        cantidad_envase=10,    # distinto a 20
    )
    assert len(candidatos) == 1
    assert candidatos[0]['score'] == 5
```

### Test 4 — Score exacto 7 (droga + cantidad)

5 (droga) + 2 (cantidad) = 7. Probable.

```python
def test_match_score_7(session):
    _crear_producto_con_atributos(
        session, '7791111111114', 'IBUPIRAC 800 X 30',
        monodroga_norm='ibuprofeno', concentracion_mg=800,
        forma_farma='CPR', cantidad_envase=30,
    )
    candidatos = match_dimensional_candidatos(
        session,
        monodroga_norm='ibuprofeno',
        concentracion_mg=600,    # NO matchea
        forma_farma='CAP',       # NO matchea
        cantidad_envase=30,      # SÍ matchea
    )
    assert len(candidatos) == 1
    assert candidatos[0]['score'] == 7
```

### Test 5 — Sin atributos útiles (devuelve [])

Sin descripción, sin atributos → no puede buscar nada.

```python
def test_match_sin_atributos_vacio(session):
    candidatos = match_dimensional_candidatos(session)
    assert candidatos == []
    candidatos = match_dimensional_candidatos(session, descripcion='')
    assert candidatos == []
```

### Test 6 — ML en líquido NO es concentración

`LACTULON JARABE X 200 ML` → forma=SUSP (jarabe), cantidad=200, sin concentracion_mg.

```python
def test_extraer_ml_liquido_es_volumen(session):
    atrs = extraer_de_descripcion('LACTULON JARABE X 200 ML')
    assert atrs.get('forma_farma') == 'SUSP'
    assert atrs.get('cantidad_envase') == Decimal(200)
    assert 'concentracion_mg' not in atrs
```

### Test 7 — Compuesta MG/ML preserva unidad textual

`AMOXIDAL 250 MG/5ML` → concentracion_mg=250, unidad='MG/5ML'.

```python
def test_extraer_compuesta_mg_ml(session):
    atrs = extraer_de_descripcion('AMOXIDAL 250 MG/5ML SUSP X 60ML')
    assert atrs.get('concentracion_mg') == Decimal(250)
    assert atrs.get('concentracion_unidad') == 'MG/5ML'
    assert atrs.get('forma_farma') == 'SUSP'
    assert atrs.get('cantidad_envase') == Decimal(60)
```

### Test 8 — Concentración huérfana en CPR/CAP

Número 1-1000 sin unidad pegada en sólido oral → mg implícito.

```python
def test_extraer_concentracion_huerfana(session):
    atrs = extraer_de_descripcion('ACTRON 600 RAPIDA ACCION CAP X 10')
    assert atrs.get('concentracion_mg') == Decimal(600)
    assert atrs.get('concentracion_unidad') == 'MG'
    assert atrs.get('forma_farma') == 'CAP'
    assert atrs.get('cantidad_envase') == Decimal(10)
```

### Test 9 — Limit honorado

50 candidatos en DB con la misma droga, `limit=10` → devuelve 10.

```python
def test_limit_honorado(session):
    for i in range(50):
        _crear_producto_con_atributos(
            session, f'7791111{i:06d}', f'IBUPIRAC {i}',
            monodroga_norm='ibuprofeno',
        )
    candidatos = match_dimensional_candidatos(
        session,
        monodroga_norm='ibuprofeno',
        limit=10,
    )
    assert len(candidatos) == 10
```

### Test 10 — Ordenamiento por score desc

Crear 3 productos con scores 12, 8, 5 (ajustando atributos) → devuelven en orden 12, 8, 5.

```python
def test_ordenamiento_por_score_desc(session):
    # Score 12 — todos los atributos matchean
    _crear_producto_con_atributos(
        session, '7791111111120', 'PERFECT 600 CPR x 30',
        monodroga_norm='ibuprofeno', concentracion_mg=600,
        forma_farma='CPR', cantidad_envase=30,
    )
    # Score 8 — droga + concentración (5+3)
    _crear_producto_con_atributos(
        session, '7791111111121', 'PARCIAL 600 CAP x 20',
        monodroga_norm='ibuprofeno', concentracion_mg=600,
        forma_farma='CAP', cantidad_envase=20,
    )
    # Score 5 — solo droga
    _crear_producto_con_atributos(
        session, '7791111111122', 'MINIMO 400 CAP x 10',
        monodroga_norm='ibuprofeno', concentracion_mg=400,
        forma_farma='CAP', cantidad_envase=10,
    )
    candidatos = match_dimensional_candidatos(
        session,
        monodroga_norm='ibuprofeno',
        concentracion_mg=600,
        forma_farma='CPR',
        cantidad_envase=30,
    )
    assert len(candidatos) == 3
    scores = [c['score'] for c in candidatos]
    assert scores == [12, 8, 5]
```

## Reglas para los tests

- **No tocar la DB de farmacia real** — la fixture ya monta SQLite in-memory.
- **Cada test es independiente** — la fixture `_limpiar_tablas_entre_tests` trunca todo entre tests.
- **Usar `Decimal` para concentracion_mg / cantidad_envase** — el modelo ProductoAtributo usa DECIMAL.
- **No testear `enriquecer_desde_obs`** — ese requiere ObsProducto poblado, dejalo para otro round.

## Reglas para no chocar con sesión A

Sesión A está implementando match dimensional en `routes/converter.py` + `templates/converter_verify.html`. NO toca `tests/`, `catalogacion.py`, ni `routes/productos.py`.

**Vos podés tocar libremente**:
- `tests/test_catalogacion.py` (archivo nuevo)

**Antes de cada commit**: `git pull --rebase`. Si chocás algo, comunicate verbal.

## Workflow git

```bash
cd c:/AppFarmWeb
git pull --rebase
# crear tests/test_catalogacion.py + correr pytest
docker exec appfarmweb-web-1 sh -c "cd /app && python -m pytest tests/test_catalogacion.py -v"
# cuando todos pasen
git add tests/test_catalogacion.py
git pull --rebase
git commit -m "test(catalogacion): 10 tests sobre extraer + match_dimensional"
git push
```

## Checkbox a tildar al cerrar

En `c:/AppSeguimiento/06-calidad.md` agregar fila:

```markdown
| [x] | — | ~~Tests catalogación + match dimensional~~ | `tests/test_catalogacion.py`: 10 tests sobre extraer_de_descripcion (ML/líquido, MG/ML, huérfano CPR) + match_dimensional_candidatos (scores 5/7/8/12, limit, orden). ✅ 2026-04-29 |
```
