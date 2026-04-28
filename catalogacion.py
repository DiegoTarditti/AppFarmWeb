"""Catalogación estructurada de medicamentos.

Extrae atributos (droga, concentración, forma, cantidad, vía) de un producto
usando 3 fuentes en cascada:

  1. obs_productos (oro: viene de DW.Productos validado por la farmacia)
  2. Regex sobre la descripción libre (rapidísimo, cubre ~80%)
  3. LLM (Claude Haiku) — opcional, para descripciones residuales (fallback)

API pública:
    extraer_de_descripcion(desc) → dict con concentracion_mg, forma_farma, cantidad, via
    enriquecer_desde_obs(producto, session) → dict con datos de obs_productos
    upsert_atributos(producto, session, force=False) → ProductoAtributo (orquesta y guarda)
    backfill_todos(session, log=print) → (n_total, n_nuevos, n_actualizados, n_sin_datos)

Diseño:
- Idempotente: correr `backfill_todos` 2 veces no rompe ni duplica.
- Si fuente == 'manual', NO se pisa salvo `force=True`.
- Si la descripción cambió respecto al snapshot guardado, se reextrae.
- Sin red. El LLM se llama solo si está habilitado y `ANTHROPIC_API_KEY` está set.
"""

import re
import unicodedata
from decimal import Decimal

from sqlalchemy.orm import Session

import database
from database import ObsNombreDroga, ObsProducto, Producto, ProductoAtributo

# ─── 1. Extractor regex ─────────────────────────────────────────────────────

# Formas farmacéuticas: orden importa, las más específicas primero.
# Cada tupla: (regex, código corto, vía sugerida)
FORMA_PATTERNS = [
    (r'\bCOMPRIMIDOS?\s*RECUBIERTOS?\b', 'CPR', 'ORAL'),
    (r'\bCOMPR(?:IMIDOS?)?\b|\bCPR\b|\bCOMP\b', 'CPR', 'ORAL'),
    (r'\bC[AÁ]PSULAS?\b|\bCAP\b', 'CAP', 'ORAL'),
    (r'\bSUSPENSI[OÓ]N\b|\bSUSP\b|\bJARABE?\b|\bJBE\b', 'SUSP', 'ORAL'),
    (r'\bGOTAS?\b|\bGTS\b', 'GTS', 'ORAL'),
    (r'\bAMPOLLAS?\b|\bAMP\b', 'AMP', 'IV'),
    (r'\bJERINGAS?\s*PRELLENADAS?\b|\bJER\b', 'JER', 'SC'),
    (r'\bSUPOSITORIOS?\b|\bSUP\b', 'SUP', 'RECT'),
    (r'\b[OÓ]VULOS?\b|\bOVU\b', 'OVU', 'VAG'),
    (r'\bCREMA\b|\bCRE\b', 'CRE', 'TOP'),
    (r'\bPOMADA\b|\bPOM\b|\bUNG[UÜ]ENTO\b', 'POM', 'TOP'),
    (r'\bGEL\b', 'GEL', 'TOP'),
    (r'\bLOCI[OÓ]N\b|\bLOC\b', 'LOC', 'TOP'),
    (r'\bSPRAY\b|\bAEROSOL\b', 'SPRAY', 'NAS'),
    (r'\bINHALADOR\b|\bINH\b', 'INH', 'INH'),
    (r'\bPARCHES?\b|\bPCH\b', 'PCH', 'TOP'),
    (r'\bSOLUCI[OÓ]N\b|\bSOL\b', 'SOL', 'IV'),
    (r'\bPOLVO\b|\bPOL\b', 'POL', 'ORAL'),
    (r'\bGRANULADO\b|\bGRA\b', 'GRA', 'ORAL'),
    (r'\bCOLIRIO\b', 'COL', 'OFT'),
    (r'\bENJUAGUE\b', 'ENJ', 'TOP'),
    (r'\bSHAMPOO\b|\bSHA\b', 'SHA', 'TOP'),
]

# Concentración: una expresión con número (entero o decimal con , o .) + unidad.
# OJO: las unidades compuestas (MG/ML, MG/5ML) van primero para que matcheen antes que MG sola.
# El final acepta o boundary de palabra o no-letra (% no tiene word-boundary contra espacio).
CONCENTRACION_RX = re.compile(
    r'\b(\d+(?:[.,]\d+)?)\s*'
    r'(MG\s*/\s*\d*\s*ML|MCG\s*/\s*ML|UI\s*/\s*ML|G\s*/\s*L|MG|MCG|GR?|UI|%|ML)'
    r'(?![A-Z0-9])',
    re.IGNORECASE
)

# Prioridad para elegir entre múltiples matches de concentración:
# unidades de dosis (mg/mcg/g/%/UI) ganan a unidades de volumen (ML).
# Si la forma_farma es líquida y solo hay ML, NO es concentración.
_UNIDADES_DOSIS = ('MG', 'MCG', 'G', '%', 'UI', 'MG/ML', 'MG/5ML', 'MCG/ML', 'UI/ML', 'G/L')
_FORMAS_LIQUIDAS = ('SUSP', 'SOL', 'GTS', 'COL', 'GEL', 'LOC', 'SPRAY')

# Cantidad de envase: "X 16", "X16", "POR 30", "30 COMP", "30 CAP", "30 UN"
CANTIDAD_PATTERNS = [
    re.compile(r'\bX\s*(\d{1,4})\b', re.IGNORECASE),
    re.compile(r'\bPOR\s*(\d{1,4})\b', re.IGNORECASE),
    re.compile(r'\b(\d{1,4})\s*(?:COMPR(?:IMIDOS?)?|CPR|COMP|C[AÁ]PSULAS?|CAP|UN(?:IDADES)?|UND)\b', re.IGNORECASE),
]


def _quitar_acentos(s):
    if not s:
        return s
    nfkd = unicodedata.normalize('NFKD', s)
    return ''.join(c for c in nfkd if not unicodedata.combining(c))


def _normalizar_droga(s):
    if not s:
        return None
    return _quitar_acentos(s).lower().strip()


def _to_mg(numero, unidad):
    """Normaliza una concentración a mg. Devuelve (Decimal, unidad_original).
    Si la unidad no se puede convertir directo (ej: MG/ML), devuelve el número crudo
    y la unidad textual — el match dimensional usará ambas piezas.
    """
    try:
        valor = Decimal(str(numero).replace(',', '.'))
    except Exception:
        return None, unidad
    u = unidad.upper().replace(' ', '')
    # Conversiones simples
    if u == 'G' or u == 'GR':
        return valor * 1000, 'MG'
    if u == 'MCG':
        return valor / 1000, 'MG'
    if u == 'MG':
        return valor, 'MG'
    if u == 'UI':
        return valor, 'UI'
    if u == '%':
        return valor, '%'
    if u == 'ML':
        return valor, 'ML'
    # Compuestas: dejamos el numerador en mg pero retornamos la unidad textual
    return valor, u


def extraer_de_descripcion(descripcion):
    """Extrae atributos parseables de una descripción libre.

    Devuelve dict con keys (todas opcionales según lo que se pudo extraer):
        concentracion_mg, concentracion_unidad, forma_farma, cantidad_envase, via_admin
    """
    if not descripcion:
        return {}
    desc = descripcion.upper()
    out = {}

    # Forma farmacéutica
    for rx, codigo, via in FORMA_PATTERNS:
        if re.search(rx, desc):
            out['forma_farma'] = codigo
            out['via_admin'] = via
            break

    # Concentración: buscamos TODOS los matches y elegimos el de unidad de dosis
    # (MG/MCG/G/%/UI). Si solo hay ML y la forma es líquida → es volumen, no
    # concentración (LACTULON JARABE X 200 ML).
    matches = list(CONCENTRACION_RX.finditer(desc))
    if matches:
        elegido = None
        # 1ra pasada: priorizar unidades de dosis
        for m in matches:
            u_norm = m.group(2).upper().replace(' ', '')
            if any(u_norm == d or u_norm.startswith(d) for d in _UNIDADES_DOSIS):
                elegido = m
                break
        # 2da: si no hubo dosis y la forma NO es líquida, aceptar ML como concentración
        if elegido is None and out.get('forma_farma') not in _FORMAS_LIQUIDAS:
            elegido = matches[0]
        if elegido:
            valor, unidad = _to_mg(elegido.group(1), elegido.group(2))
            if valor is not None:
                out['concentracion_mg'] = valor
                out['concentracion_unidad'] = unidad

    # Cantidad de envase
    for rx in CANTIDAD_PATTERNS:
        cm = rx.search(desc)
        if cm:
            try:
                cant = int(cm.group(1))
                if 1 <= cant <= 9999:  # filtro de números absurdos
                    out['cantidad_envase'] = Decimal(cant)
                    break
            except (ValueError, IndexError):
                continue

    return out


# ─── 2. Enriquecedor desde obs_productos ────────────────────────────────────

def enriquecer_desde_obs(producto, session):
    """Si el producto tiene observer_id, traer datos estructurados de obs_productos.

    Devuelve dict con keys que se pueden poblar (monodroga, cantidad_envase).
    """
    if not producto.observer_id:
        return {}
    obs = session.get(ObsProducto, producto.observer_id)
    if not obs:
        return {}
    out = {}
    if obs.cantidad_envase is not None:
        out['cantidad_envase'] = obs.cantidad_envase
    if obs.nombre_droga_observer:
        droga = session.get(ObsNombreDroga, obs.nombre_droga_observer)
        if droga and droga.descripcion:
            out['monodroga_display'] = droga.descripcion.strip()
            out['monodroga_norm'] = _normalizar_droga(droga.descripcion)
    return out


# ─── 3. Orquestador upsert ──────────────────────────────────────────────────

def upsert_atributos(producto, session, force=False):
    """Calcula y guarda atributos para un producto. Retorna ProductoAtributo o None.

    - Si ya existe con fuente='manual' y force=False, no toca nada.
    - Combina obs (alta confianza) + regex sobre descripción.
    - fuente='mixto' si ambas fuentes aportaron, sino la única que aportó.
    """
    existente = session.get(ProductoAtributo, producto.id)
    if existente and existente.fuente == 'manual' and not force:
        return existente

    # Si la descripción no cambió Y existe atributos, no hace falta recomputar
    desc_actual = (producto.descripcion or '').strip()
    if existente and existente.raw_descripcion == desc_actual and not force:
        return existente

    # Combinar fuentes
    desde_regex = extraer_de_descripcion(desc_actual)
    desde_obs = enriquecer_desde_obs(producto, session)

    fuentes_aportaron = []
    if desde_obs:
        fuentes_aportaron.append('observer')
    if desde_regex:
        fuentes_aportaron.append('regex')

    if not fuentes_aportaron:
        # Nada que guardar. Si había un registro previo, lo dejamos como estaba.
        return existente

    fuente = 'mixto' if len(fuentes_aportaron) == 2 else fuentes_aportaron[0]
    # Confianza: ALTA si obs aporta cantidad+droga, MEDIA si solo regex, BAJA si fragmentado.
    confianza = 'ALTA' if 'observer' in fuentes_aportaron and desde_obs.get('monodroga_norm') else 'MEDIA'

    # Merge: obs tiene prioridad para sus campos (cantidad, droga). Regex completa el resto.
    merged = {**desde_regex, **desde_obs}

    if existente:
        for k, v in merged.items():
            setattr(existente, k, v)
        existente.fuente = fuente
        existente.confianza = confianza
        existente.raw_descripcion = desc_actual
        existente.extraido_en = database.now_ar()
        return existente

    nuevo = ProductoAtributo(
        producto_id=producto.id,
        fuente=fuente,
        confianza=confianza,
        raw_descripcion=desc_actual,
        **merged,
    )
    session.add(nuevo)
    return nuevo


# ─── 4. Backfill ────────────────────────────────────────────────────────────

def backfill_todos(session=None, log=None):
    """Recorre todos los productos y puebla `producto_atributos`.

    Idempotente. Devuelve (n_total, n_actualizados, n_sin_datos).
    """
    log = log or (lambda msg: None)

    def _run(s: Session):
        productos = s.query(Producto).order_by(Producto.id).all()
        n_total = len(productos)
        n_act = 0
        n_sin = 0
        for i, p in enumerate(productos, 1):
            res = upsert_atributos(p, s)
            if res is None:
                n_sin += 1
            else:
                n_act += 1
            if i % 500 == 0:
                s.commit()
                log(f'  ... {i}/{n_total} procesados')
        s.commit()
        return n_total, n_act, n_sin

    if session is not None:
        return _run(session)
    with database.get_db() as s:
        return _run(s)


# ─── 5. Match dimensional ───────────────────────────────────────────────────

def match_dimensional_candidatos(session, descripcion=None, monodroga_norm=None,
                                  concentracion_mg=None, concentracion_unidad=None,
                                  forma_farma=None, cantidad_envase=None,
                                  limit=10):
    """Busca productos con atributos similares.

    Si pasás `descripcion`, primero extrae atributos de ahí (con regex) y los
    usa para la búsqueda. Si pasás los atributos directos, los usa tal cual.

    Score (cuánto matchea cada candidato):
      - Misma droga (norm):        +5  (la dimensión más fuerte)
      - Misma concentración_mg:    +3
      - Misma forma_farma:         +2
      - Misma cantidad_envase:     +2

    Devuelve lista de candidatos con su score, ordenados desc.
    Score >= 5 = match probable. Score >= 7 = match casi seguro.
    """
    # Si nos dan una descripción, extraer atributos primero
    if descripcion and not (concentracion_mg or forma_farma or cantidad_envase):
        atrs = extraer_de_descripcion(descripcion)
        concentracion_mg = concentracion_mg or atrs.get('concentracion_mg')
        concentracion_unidad = concentracion_unidad or atrs.get('concentracion_unidad')
        forma_farma = forma_farma or atrs.get('forma_farma')
        cantidad_envase = cantidad_envase or atrs.get('cantidad_envase')

    # Sin ningún atributo conocido → no podemos buscar nada útil
    if not (monodroga_norm or concentracion_mg or forma_farma or cantidad_envase):
        return []

    # Query base: trae todos los ProductoAtributo que matchean AL MENOS UN atributo.
    # Después calculamos score en Python.
    from sqlalchemy import or_
    filtros = []
    if monodroga_norm:
        filtros.append(ProductoAtributo.monodroga_norm == monodroga_norm)
    if concentracion_mg is not None:
        from decimal import Decimal
        filtros.append(ProductoAtributo.concentracion_mg == Decimal(str(concentracion_mg)))
    if forma_farma:
        filtros.append(ProductoAtributo.forma_farma == forma_farma)
    if cantidad_envase is not None:
        from decimal import Decimal
        filtros.append(ProductoAtributo.cantidad_envase == Decimal(str(cantidad_envase)))

    if not filtros:
        return []

    candidatos = (session.query(ProductoAtributo)
                  .filter(or_(*filtros))
                  .all())

    resultados = []
    for c in candidatos:
        score = 0
        if monodroga_norm and c.monodroga_norm == monodroga_norm:
            score += 5
        if concentracion_mg is not None and c.concentracion_mg is not None:
            from decimal import Decimal
            if c.concentracion_mg == Decimal(str(concentracion_mg)):
                score += 3
        if forma_farma and c.forma_farma == forma_farma:
            score += 2
        if cantidad_envase is not None and c.cantidad_envase is not None:
            from decimal import Decimal
            if c.cantidad_envase == Decimal(str(cantidad_envase)):
                score += 2
        if score == 0:
            continue
        prod = session.get(Producto, c.producto_id)
        if not prod:
            continue
        resultados.append({
            'producto_id': c.producto_id,
            'codigo_barra': prod.codigo_barra,
            'descripcion': prod.descripcion,
            'monodroga': c.monodroga_display,
            'concentracion': f'{c.concentracion_mg} {c.concentracion_unidad}' if c.concentracion_mg else None,
            'forma_farma': c.forma_farma,
            'cantidad_envase': float(c.cantidad_envase) if c.cantidad_envase else None,
            'score': score,
            'fuente': c.fuente,
            'confianza': c.confianza,
        })
    resultados.sort(key=lambda r: -r['score'])
    return resultados[:limit]
