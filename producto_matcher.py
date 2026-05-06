"""Módulo central de matching de productos.

Una función `match_producto()` que cualquier feature usa cuando necesita resolver
"este texto + estos datos a qué producto del catálogo corresponde".

Cascada de estrategias (primera con score alto gana):
1. EAN exacto.
2. Código alfabeta exacto.
3. Descripción exacta normalizada + lab.
4. Tokens superset (todos los tokens del input están en el producto).
5. Jaccard descripción + lab.
6. Jaccard descripción + monodroga (otro lab pero mismo principio activo).
7. Jaccard global (solo si threshold muy alto).

Modifiers de score:
- +0.10 si cantidad_envase matchea.
- +0.05 si monodroga matchea.
- -0.20 si precio referencia difiere >30% (agrega warning).

Cuando NO matchea: devuelve `candidatos_top` con los top-N similares para que
la UI pueda mostrar un dropdown de match manual (regla general del sistema).

Helpers públicos:
- normalizar_texto(s)
- tokens_significativos(s)
- jaccard(a, b)
- comparar_descripciones(a, b)
- buscar_candidatos(descripcion, lab_id=None, top=8) — para UIs que solo
  necesitan el dropdown.
- match_productos_bulk(items, lab_id=None) — para N items con precarga.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

# ── Helpers de texto ────────────────────────────────────────────────────────

# Tokens irrelevantes que aparecen en casi todas las descripciones farmacéuticas
# y no aportan distinción.
_STOPWORDS = {
    # Formas farmacéuticas (sinónimos)
    'comp', 'com', 'compr', 'comprimido', 'comprimidos', 'cpr',
    'cap', 'caps', 'capsula', 'capsulas',
    'tab', 'tableta', 'tabletas',
    'amp', 'ampolla', 'ampollas',
    'jbe', 'jarabe', 'crema', 'gel', 'pomada',
    'sup', 'supositorio', 'supositorios',
    'ovu', 'ovulo', 'ovulos',
    'tampon', 'tampones',
    'parche', 'parches',
    'enj', 'enjuague',
    'colirio',
    'gts', 'gotas',
    'sol', 'solucion',
    'sus', 'susp', 'suspension',
    'emul', 'emulsion',
    'inh', 'inhalador', 'inhalacion', 'aerosol',
    'spray',
    'sobre', 'sobres', 'frasco', 'frascos',
    'pol', 'polvo',
    'iny', 'inyectable',
    'recubierto', 'rec', 'recubiertos', 'recubiertas',
    'gastr', 'gastro', 'gastrorresistente', 'gastrorresistentes',
    'gastroresistente', 'gastroresistentes',
    'ent', 'enterico', 'entericos', 'enterica', 'entericas',
    'recub',
    'efervescente', 'efe', 'efervescentes',
    'mast', 'masticable', 'masticables',
    'lib', 'liberacion', 'prolongada',
    # Características de comprimido (no aportan diferenciación)
    'ran', 'ranurado', 'ranurados', 'ranurada', 'ranuradas',
    'bi', 'bicapa', 'tri', 'tricapa',
    # AP/AR/SR/CR/XR/LP NO van en stopwords: son sufijos de
    # acción/liberación prolongada que diferencian productos
    # (BALIGLUC ≠ BALIGLUC AP). Sacarlos provocaba empate de tokens
    # en bulk match y dejaba ítems como not_found erróneamente.
    'blister', 'blisters', 'bl',
    'ps', 'p',                              # p.bl. (perlas blister)
    # Unidades de medida
    'mg', 'gr', 'g', 'ml', 'l', 'mcg', 'ui', 'mui', 'kg',
    # Conectores/cantidad
    'x', 'un', 'unid', 'unidades', 'uds',
    'oral', 'tópico', 'topico', 'topica', 'sublingual',
}


def normalizar_texto(s) -> str:
    """Lowercase + sin acentos + sin puntuación + colapsa espacios.

    Además mergea nomenclaturas farma del estilo "B 12" → "b12" (vitaminas
    y formulaciones con letra+número espaciado). Sin esto, "XEDENOL B 12"
    tokenizaba {xedenol, 12} (la 'b' suelta se filtra por len<2) y
    "XEDENOL B12" tokenizaba {xedenol, b12} → match al 50%.

    Limitado a letras comunes en farma (b, c, d, e, k) para evitar mergear
    cosas no deseadas como "x 5".
    """
    if s is None:
        return ''
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode('ascii')
    s = s.lower()
    s = re.sub(r'[^a-z0-9\s]', ' ', s)
    # Merge letra-vitamina + número adyacentes ("b 12" → "b12").
    s = re.sub(r'\b([bcdek])\s+(\d+)\b', r'\1\2', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def tokens_significativos(s) -> set:
    """Tokens normalizados sin stopwords farmacéuticos.

    Reglas:
    - Letras sueltas de 1 char se filtran ('a', 'b' tras quitar puntuación
      no aportan).
    - **Dígitos de 1 char SÍ se preservan** (`3`, `5`, `7` son críticos
      para distinguir presentaciones: x3 vs x5 vs x7).
    - Stopwords farmacéuticos siempre fuera.
    """
    out = set()
    for t in normalizar_texto(s).split():
        if t in _STOPWORDS:
            continue
        # Solo filtrar tokens de 1 char si NO son dígitos (preservamos números
        # de presentación tipo "x 3" cuya `x` es stopword pero `3` no debería).
        if len(t) < 2 and not t.isdigit():
            continue
        out.add(t)
    return out


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = a & b
    union = a | b
    return len(inter) / len(union)


def comparar_descripciones(a, b) -> float:
    """Score 0..1 entre dos descripciones. Útil para usar fuera del módulo."""
    return jaccard(tokens_significativos(a), tokens_significativos(b))


def _extraer_cantidad_envase(descripcion) -> Optional[int]:
    """Extrae la cantidad por envase de una descripción tipo 'COM x 30' o 'x100'."""
    if not descripcion:
        return None
    m = re.search(r'x\s*(\d{1,4})\b', str(descripcion).lower())
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


# Patrones que indican "este item es un pack del proveedor" — no es un producto
# normal del catálogo. Reusados desde pack_detector.py.
_PACK_PATTERNS = [
    re.compile(r'\bPACK\s*X\s*\d+\b', re.IGNORECASE),
    re.compile(r'\bPACK\s+\d+\s*EST(?:UCHES?)?\b', re.IGNORECASE),
    re.compile(r'\bX\s*\d+\s*EST(?:UCHES?)?\b', re.IGNORECASE),
]


def descripcion_es_pack(s) -> bool:
    """True si la descripción matchea cualquier patrón de pack (PACK X 10, etc.)."""
    if not s:
        return False
    return any(p.search(str(s)) for p in _PACK_PATTERNS)


def limpiar_sufijos_pack(s) -> str:
    """Remueve sufijos 'PACK X N', 'PACK N EST', 'X N EST' al final de la descripción.
    Útil para normalizar antes de matchear módulos vs unidades."""
    if not s:
        return ''
    out = str(s)
    out = re.sub(r'\s*\(?\s*PACK\s*X\s*\d+\s*\)?\s*', ' ', out, flags=re.IGNORECASE)
    out = re.sub(r'\s*PACK\s+\d+\s*EST(?:UCHES?)?\s*', ' ', out, flags=re.IGNORECASE)
    out = re.sub(r'\s*X\s*\d+\s*EST(?:UCHES?)?\s*$', ' ', out, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', out).strip()


# ── Resultado ───────────────────────────────────────────────────────────────

CONFIANZA_ALTA = 'alta'
CONFIANZA_MEDIA = 'media'
CONFIANZA_BAJA = 'baja'
CONFIANZA_NONE = 'sin_match'


@dataclass
class MatchResult:
    producto: object = None              # Producto local, ObsProducto, o None
    score: float = 0.0
    estrategia: str = 'sin_match'
    confianza: str = CONFIANZA_NONE
    warnings: list = field(default_factory=list)
    candidatos_top: list = field(default_factory=list)
    candidatos_count: int = 0   # cantidad de productos del pool con jaccard >= UMBRAL_CAND
    debug: dict = field(default_factory=dict)

    def to_dict(self):
        """Serialización para enviar via JSON al frontend.
        Funciona tanto para Producto local como ObsProducto."""
        p = self.producto
        return {
            'producto_id': getattr(p, 'id', None) if p else None,
            'observer_id': getattr(p, 'observer_id', None) if p else None,
            'producto_descripcion': (getattr(p, 'descripcion', '') or '') if p else None,
            'producto_codigo_barra': (getattr(p, 'codigo_barra', '') or '') if p else None,
            'producto_codigo_alfabeta': (getattr(p, 'codigo_alfabeta', '') or '') if p else None,
            'producto_precio_pvp': (float(p.precio_pvp) if p and getattr(p, 'precio_pvp', None) else None),
            'score': round(self.score, 3),
            'estrategia': self.estrategia,
            'confianza': self.confianza,
            'warnings': list(self.warnings),
            'candidatos_top': list(self.candidatos_top),
        }


def _confianza(score: float) -> str:
    if score >= 0.95:
        return CONFIANZA_ALTA
    if score >= 0.80:
        return CONFIANZA_MEDIA
    if score > 0:
        return CONFIANZA_BAJA
    return CONFIANZA_NONE


# ── Targets: abstracción del modelo destino ────────────────────────────────
#
# Permite que match_producto matchee tanto contra `Producto` (catálogo local)
# como contra `ObsProducto` (catálogo ObServer). La idea: el algoritmo es el
# mismo, solo cambian las columnas y el campo de scope-por-lab.

class _TargetSpec:
    """Describe cómo matchear contra un modelo dado: qué columnas tiene, qué
    campo usar para scope por lab, qué columnas de EAN alternativos."""

    def __init__(self, *, model_attr, lab_field, ean_fields=('codigo_barra',),
                 alfabeta_field='codigo_alfabeta'):
        self.model_attr = model_attr           # nombre de la clase en database
        self.lab_field = lab_field             # nombre del campo de scope por lab
        self.ean_fields = ean_fields           # tuple con el principal + alts
        self.alfabeta_field = alfabeta_field

    def model(self, database):
        return getattr(database, self.model_attr)


_TARGETS = {
    'producto': _TargetSpec(
        model_attr='Producto',
        lab_field='laboratorio_id',
        # Solo principal — los alternativos viven en producto_codigos_barra
        # (1-a-N). Las columnas legacy alt1/2/3 fueron migradas y van a
        # DROP COLUMN.
        ean_fields=('codigo_barra',),
        alfabeta_field='codigo_alfabeta',
    ),
    'obs_producto': _TargetSpec(
        model_attr='ObsProducto',
        lab_field='laboratorio_observer',
        ean_fields=(),  # ObsProducto no tiene EAN propio
        alfabeta_field='codigo_alfabeta',
    ),
}


# ── Función principal ──────────────────────────────────────────────────────

def match_producto(*,
                   ean=None,
                   codigo_alfabeta=None,
                   descripcion=None,
                   laboratorio_id=None,
                   precio_referencia=None,
                   cantidad_envase=None,
                   monodroga=None,
                   contexto='general',
                   target='producto',
                   pool=None,
                   incluir_observer=True,
                   threshold=0.80,
                   incluir_candidatos=True,
                   top_candidatos=8,
                   session=None) -> MatchResult:
    """Encuentra el Producto local que mejor matchea con los datos provistos.

    Args:
        ean: código de barras (13 dígitos típico). Match exacto contra
             productos.codigo_barra y los 3 alts.
        codigo_alfabeta: código alfabeta. Match exacto contra
             productos.codigo_alfabeta.
        descripcion: texto libre. Usado para fuzzy match si los anteriores fallan.
        laboratorio_id: scope al lab (para fuzzy match).
        precio_referencia: para cross-check. Si difiere >30% del precio del
             match, agrega warning 'precio_variacion_alta'.
        cantidad_envase: para boost de score si matchea.
        monodroga: descripción del principio activo. Si se da, intenta
             match por droga aunque el lab no coincida.
        contexto: 'general' | 'oferta' | 'factura' | 'pedido' | 'modulo'.
             Reservado para tunear heurísticas (ej. en facturas el threshold
             podría ser más exigente).
        target: 'producto' (catálogo local) | 'obs_producto' (catálogo ObServer).
             Controla contra qué tabla se busca. Para 'obs_producto', el
             `laboratorio_id` se interpreta como `laboratorio_observer`.
        pool: lista pre-cargada de instancias del modelo target para el fuzzy
             match (ahorra el SQL de scan-por-lab; útil cuando matcheás N items
             contra el mismo lab y/o querés pre-filtrar por fecha_baja u otros).
             Cuando se da, NO se ejecuta el query de candidatos por lab.
        threshold: score mínimo para aceptar match fuzzy. Default 0.80.
        incluir_candidatos: si True, devuelve top-N similares en candidatos_top
             aunque haya match (para mostrar alternativas) o si no hay match
             (para dropdown de match manual).
        top_candidatos: cuántos candidatos devolver.
        session: SQLAlchemy session. Si None, abre/cierra una.

    Returns:
        MatchResult con producto (o None) + estrategia + score + warnings + candidatos.
    """
    # Lazy imports (evita circular imports)
    from sqlalchemy import or_

    import database

    spec = _TARGETS.get(target)
    if spec is None:
        raise ValueError(f"target='{target}' inválido. Usar 'producto' u 'obs_producto'.")
    P = spec.model(database)
    lab_col = getattr(P, spec.lab_field, None)
    own_session = session is None
    if own_session:
        session = database.SessionLocal()

    try:
        result = MatchResult()

        # Estrategia 1: EAN exacto (solo si el target tiene columnas de EAN)
        if ean and spec.ean_fields:
            ean_clean = str(ean).strip()
            if ean_clean:
                conds = []
                for col_name in spec.ean_fields:
                    col = getattr(P, col_name, None)
                    if col is not None:
                        conds.append(col == ean_clean)
                if conds:
                    prod = session.query(P).filter(or_(*conds)).first()
                    if prod:
                        result.producto = prod
                        result.score = 1.0
                        result.estrategia = 'ean_exacto'
                        result.confianza = CONFIANZA_ALTA

        # Estrategia 1b: EAN exacto en producto_codigos_barra (tabla 1-a-N).
        # Solo aplica al target 'producto'. La tabla 1-a-N es el reemplazo
        # gradual de los slots alt1/2/3 — productos que se vincularon vía
        # match dimensional, factura, o CRUD manual desde la ficha quedan
        # acá, no en las columnas legacy.
        if not result.producto and ean and target == 'producto':
            ean_clean = str(ean).strip()
            if ean_clean:
                try:
                    from database import ProductoCodigoBarra
                    pid_row = (session.query(ProductoCodigoBarra.producto_id)
                               .filter(ProductoCodigoBarra.codigo_barra == ean_clean)
                               .first())
                    if pid_row:
                        prod = session.get(P, pid_row[0])
                        if prod is not None:
                            result.producto = prod
                            result.score = 1.0
                            result.estrategia = 'ean_exacto_1aN'
                            result.confianza = CONFIANZA_ALTA
                except Exception:
                    pass

        # Fallback EAN vía obs_codigos_barras: solo aplica al target Producto
        # (no a ObsProducto) y cuando la estrategia 1 no encontró nada local.
        # Resuelve EAN → observer_id → Producto local con ese observer_id.
        # Si no existe Producto local pero sí obs_producto, lo auto-creamos para
        # que la oferta/factura pueda quedar linkeada.
        if not result.producto and ean and target == 'producto':
            ean_clean = str(ean).strip()
            try:
                from database import Laboratorio, ObsCodigoBarras, ObsLaboratorio, ObsProducto, Producto
                obs_id_row = (session.query(ObsCodigoBarras.producto_observer)
                              .filter(ObsCodigoBarras.codigo_barras == ean_clean,
                                      ObsCodigoBarras.fecha_baja.is_(None))
                              .first())
                if obs_id_row:
                    obs_pid = obs_id_row[0]
                    prod = (session.query(Producto)
                            .filter(Producto.observer_id == obs_pid)
                            .first())
                    if prod is None:
                        # Auto-crear desde obs_producto
                        op = session.get(ObsProducto, obs_pid)
                        if op:
                            # Resolver Laboratorio local por observer_id del lab.
                            lab_id_local = None
                            if op.laboratorio_observer:
                                lab_local = (session.query(Laboratorio)
                                             .filter(Laboratorio.observer_id == op.laboratorio_observer)
                                             .first())
                                if lab_local:
                                    lab_id_local = lab_local.id
                            prod = Producto(
                                codigo_barra=ean_clean,
                                descripcion=op.descripcion or '',
                                laboratorio_id=lab_id_local,
                                observer_id=obs_pid,
                                codigo_alfabeta=op.codigo_alfabeta,
                            )
                            session.add(prod)
                            session.flush()
                    if prod:
                        result.producto = prod
                        result.score = 1.0
                        result.estrategia = 'ean_obs_bridge'
                        result.confianza = CONFIANZA_ALTA
            except Exception:
                pass

        # Estrategia 2: alfabeta exacto.
        # Salvaguardas:
        #   a) Códigos cortos (<5 dígitos) son típicamente "código interno"
        #      del lab (ej. Baliarda usa 1-4 dígitos). Mappear ciegamente
        #      contra obs_productos.codigo_alfabeta da falsos positivos
        #      catastróficos (BALIGLUC 565 → TENSOPRIL 20mg). Si el código
        #      es corto, exigimos que matchee dentro del MISMO laboratorio.
        #   b) Aunque el código sea largo, si nos pasaron lab_id y hay
        #      varios productos con ese codigo_alfabeta en otros labs,
        #      preferimos el del lab dado.
        if not result.producto and codigo_alfabeta:
            alf_clean = str(codigo_alfabeta).strip()
            alf_col = getattr(P, spec.alfabeta_field, None)
            if alf_clean and alf_col is not None:
                base_q = session.query(P).filter(alf_col == alf_clean)
                prod = None
                # Si hay lab, intentar primero filtrado por lab.
                if laboratorio_id and lab_col is not None:
                    prod = base_q.filter(lab_col == laboratorio_id).first()
                # Fallback global solo si el código es "largo" (>=5 dígitos
                # numéricos): asumimos que es código alfabeta real, no
                # interno corto.
                if prod is None:
                    digits_only = ''.join(ch for ch in alf_clean if ch.isdigit())
                    if len(digits_only) >= 5:
                        prod = base_q.first()
                if prod is not None:
                    # SANITY CHECK: aunque el código alfabeta haya matcheado
                    # exacto, validamos que las descripciones compartan
                    # tokens significativos. Esto descarta falsos positivos
                    # del estilo "BLAVIN 5 mg" vs "DEPAKENE CAP" (códigos
                    # internos cortos colisionando entre labs distintos).
                    # Si las descripciones no comparten al menos 1 token
                    # significativo Y similitud >= 0.30, NO confiamos.
                    if descripcion and getattr(prod, 'descripcion', None):
                        toks_a = tokens_significativos(descripcion)
                        toks_b = tokens_significativos(prod.descripcion)
                        if toks_a and toks_b:
                            sim = jaccard(toks_a, toks_b)
                            comparten = bool(toks_a & toks_b)
                            if not comparten or sim < 0.30:
                                # Descartamos el match. Lo dejamos pasar a
                                # las estrategias de descripción.
                                prod = None
                if prod is not None:
                    result.producto = prod
                    result.score = 1.0
                    result.estrategia = 'alfabeta_exacto'
                    result.confianza = CONFIANZA_ALTA

        # Estrategia 3-7: descripción.
        # Si el contexto es 'modulo', limpiamos sufijos PACK X N antes de tokenizar
        # (para que matchee la unidad correspondiente, no otros packs).
        descr_norm = descripcion
        if descripcion and contexto in ('modulo', 'pack'):
            descr_norm = limpiar_sufijos_pack(descripcion)
        toks_input = tokens_significativos(descr_norm) if descr_norm else set()

        # Si ya matcheamos, igual hacemos cross-check de precio
        # y opcionalmente buscamos candidatos top.
        if not result.producto and toks_input:
            # Pre-cargar candidatos del lab (o globales si no hay lab),
            # salvo que el caller ya nos haya dado un pool en memoria.
            if pool is not None:
                candidatos = pool
            else:
                if laboratorio_id and lab_col is not None:
                    cand_query = session.query(P).filter(lab_col == laboratorio_id)
                else:
                    # Sin lab: solo buscamos en productos que tengan al menos un
                    # token en común con el input para no escanear todo el catálogo.
                    # Simplificado: traemos todos. Mejorable con índice trgm.
                    cand_query = session.query(P)
                candidatos = cand_query.all()

            # Estrategia 3: descripción exacta normalizada (+ lab si dado)
            input_norm = normalizar_texto(descripcion)
            for c in candidatos:
                if normalizar_texto(c.descripcion) == input_norm:
                    result.producto = c
                    result.score = 1.0
                    result.estrategia = 'descripcion_exacta'
                    result.confianza = CONFIANZA_ALTA
                    break

            # Estrategia 4: tokens superset (input ⊆ producto)
            if not result.producto:
                supersets = []
                for c in candidatos:
                    toks_c = tokens_significativos(c.descripcion)
                    if toks_c and toks_input.issubset(toks_c):
                        supersets.append(c)
                if len(supersets) == 1:
                    result.producto = supersets[0]
                    result.score = 0.95
                    result.estrategia = 'tokens_superset'
                    result.confianza = CONFIANZA_ALTA

            # Estrategia 5: Jaccard descripción + lab
            if not result.producto:
                mejor = None
                mejor_score = 0.0
                empate = False
                skip_packs = contexto in ('modulo', 'pack')
                for c in candidatos:
                    # En contexto módulo, saltear candidatos que también son packs:
                    # un pack no es la "unidad" de otro pack.
                    if skip_packs and descripcion_es_pack(c.descripcion):
                        continue
                    toks_c = tokens_significativos(c.descripcion)
                    score = jaccard(toks_input, toks_c)
                    # Modifier: cantidad envase
                    if cantidad_envase and _extraer_cantidad_envase(c.descripcion) == cantidad_envase:
                        score = min(1.0, score + 0.10)
                    if score > mejor_score:
                        mejor = c
                        mejor_score = score
                        empate = False
                    elif score == mejor_score and score > 0:
                        empate = True
                if mejor and mejor_score >= threshold and not empate:
                    result.producto = mejor
                    result.score = mejor_score
                    result.estrategia = 'fuzzy_lab' if laboratorio_id else 'fuzzy_global'
                    result.confianza = _confianza(mejor_score)
                elif empate and mejor_score >= threshold:
                    result.warnings.append('match_ambiguo')

            # Estrategia 5b: fallback global si scope al lab no encontró nada.
            # Cubre el caso donde el catálogo del lab está vacío o el producto
            # está cargado bajo otro lab/sin lab. Threshold un poco más alto
            # (penalización por venir de "otro lab") para reducir falsos
            # positivos. NO se ejecuta si el caller pasó un pool propio.
            if (not result.producto and laboratorio_id and pool is None
                    and lab_col is not None):
                from sqlalchemy import or_ as _or
                # Incluir productos con lab distinto Y los que tienen lab NULL
                # (en SQL `lab != X` excluye los NULL, hay que sumarlos aparte).
                global_query = session.query(P).filter(_or(
                    lab_col != laboratorio_id,
                    lab_col.is_(None),
                ))
                global_candidatos = global_query.all()
                mejor = None
                mejor_score = 0.0
                empate = False
                skip_packs = contexto in ('modulo', 'pack')
                threshold_global = max(threshold, 0.85)
                for c in global_candidatos:
                    if skip_packs and descripcion_es_pack(c.descripcion):
                        continue
                    toks_c = tokens_significativos(c.descripcion)
                    score = jaccard(toks_input, toks_c)
                    if cantidad_envase and _extraer_cantidad_envase(c.descripcion) == cantidad_envase:
                        score = min(1.0, score + 0.10)
                    if score > mejor_score:
                        mejor = c
                        mejor_score = score
                        empate = False
                    elif score == mejor_score and score > 0:
                        empate = True
                if mejor and mejor_score >= threshold_global and not empate:
                    result.producto = mejor
                    result.score = mejor_score - 0.05  # penalización
                    result.estrategia = 'fuzzy_otro_lab'
                    result.confianza = _confianza(result.score)
                    result.warnings.append('match_otro_lab')

            # Estrategia 8: fallback al catálogo ObServer (Alfabeta) cuando
            # nada matcheó en `productos` local. Útil porque el catálogo
            # local suele estar incompleto y obs_productos tiene Alfabeta
            # entero. Re-llamamos a match_producto con target='obs_producto'.
            # Se desactiva con incluir_observer=False (ej. desde bulk para
            # hacer la pasada en una sola precarga).
            if (not result.producto and target == 'producto' and pool is None
                    and incluir_observer):
                # Mapear lab local → lab observer si está vinculado.
                lab_obs_id = None
                if laboratorio_id:
                    lab_local = session.get(database.Laboratorio, laboratorio_id)
                    lab_obs_id = getattr(lab_local, 'observer_id', None) if lab_local else None
                obs_res = match_producto(
                    ean=None,                  # obs_productos no tiene EAN
                    codigo_alfabeta=codigo_alfabeta,
                    descripcion=descripcion,
                    laboratorio_id=lab_obs_id,
                    target='obs_producto',
                    threshold=max(threshold, 0.85),
                    incluir_candidatos=False,
                    session=session,
                )
                if obs_res.producto is not None:
                    result.producto = obs_res.producto
                    result.score = obs_res.score
                    result.estrategia = obs_res.estrategia + '_obs'
                    result.confianza = obs_res.confianza
                    result.warnings.append('match_observer')

        # Cross-check de precio si hay match
        if result.producto and precio_referencia is not None:
            try:
                pref = float(precio_referencia)
                pact = float(result.producto.precio_pvp) if result.producto.precio_pvp else None
                if pact and pact > 0:
                    var = abs(pref - pact) / pact
                    result.debug['variacion_precio'] = round(var * 100, 1)
                    if var > 0.30:
                        result.warnings.append('precio_variacion_alta')
                        result.score = max(0.0, result.score - 0.20)
                        result.confianza = _confianza(result.score)
            except (ValueError, TypeError):
                pass

        # Top candidatos para mostrar en UI (siempre que haya descripción y no
        # un match perfecto)
        if incluir_candidatos and toks_input and result.score < 1.0:
            if pool is not None:
                cand_pool = pool
            elif laboratorio_id and lab_col is not None:
                cand_pool = session.query(P).filter(lab_col == laboratorio_id).all()
            else:
                cand_pool = session.query(P).all()
            scored = []
            for c in cand_pool:
                toks_c = tokens_significativos(c.descripcion)
                score = jaccard(toks_input, toks_c)
                if score > 0:
                    scored.append({
                        'producto_id': getattr(c, 'id', None),
                        'observer_id': getattr(c, 'observer_id', None),
                        'descripcion': c.descripcion,
                        'codigo_barra': getattr(c, 'codigo_barra', None),
                        'codigo_alfabeta': getattr(c, 'codigo_alfabeta', '') or '',
                        'precio_pvp': float(c.precio_pvp) if getattr(c, 'precio_pvp', None) else None,
                        'score': round(score, 3),
                    })
            scored.sort(key=lambda x: -x['score'])
            result.candidatos_top = scored[:top_candidatos]

        return result
    finally:
        if own_session:
            session.close()


# ── API conveniente para UIs (ej. dropdown de match manual) ────────────────

def buscar_candidatos(descripcion, laboratorio_id=None, top=8, target='producto',
                      incluir_observer=True, threshold_min=0.50, session=None):
    """Devuelve lista de candidatos para un dropdown de match manual.

    Para `target='producto'` combina TRES pools (cuando aplica):
    1) Productos locales del lab (boost +0.05).
    2) Productos locales globales.
    3) Catálogo ObServer del mismo lab (mapeando Laboratorio.observer_id).
       Esto cubre el caso real donde el catálogo local está incompleto y
       el catálogo Alfabeta vía ObServer tiene el producto.

    Args:
        threshold_min: score mínimo para incluir candidatos (default 0.50).
            Si NINGÚN candidato supera el umbral, devuelve [] — preferimos
            "no encontrado" claro que sugerencias basura.
        incluir_observer: False desactiva el pool ObServer (para tests).
    """
    if not descripcion:
        return []
    cands = []
    own_session = session is None
    if own_session:
        import database
        session = database.SessionLocal()
    try:
        # 1. Búsqueda con scope al lab local (si se pidió). Boost al score
        #    para que los del lab queden primero ante empates.
        if laboratorio_id:
            res_lab = match_producto(
                descripcion=descripcion,
                laboratorio_id=laboratorio_id,
                target=target,
                incluir_candidatos=True,
                top_candidatos=top * 2,
                session=session,
            )
            for c in res_lab.candidatos_top:
                c['score'] = round(min(1.0, c['score'] + 0.05), 3)
                c['_origen'] = 'lab'
            cands.extend(res_lab.candidatos_top)

        # 2. Búsqueda global en el target principal.
        res_global = match_producto(
            descripcion=descripcion,
            laboratorio_id=None,
            target=target,
            incluir_candidatos=True,
            top_candidatos=top * 2,
            session=session,
        )
        for c in res_global.candidatos_top:
            c.setdefault('_origen', 'global')
            cands.append(c)

        # 3. Pool ObServer (catálogo Alfabeta vía obs_productos).
        #    Si target='producto' y el lab local tiene observer_id mapeado,
        #    scopeamos a ese lab observer. Si no, buscamos global en obs.
        #    Esto cubre el caso donde el lab local todavía no fue vinculado a
        #    ObServer pero el catálogo ObServer SÍ tiene el producto.
        if target == 'producto' and incluir_observer:
            import database
            lab_obs_id = None
            if laboratorio_id:
                lab = session.get(database.Laboratorio, laboratorio_id)
                lab_obs_id = getattr(lab, 'observer_id', None) if lab else None
            res_obs = match_producto(
                descripcion=descripcion,
                laboratorio_id=lab_obs_id,    # None → busca global en obs
                target='obs_producto',
                incluir_candidatos=True,
                top_candidatos=top * 2,
                session=session,
            )
            for c in res_obs.candidatos_top:
                c['_origen'] = 'observer'
            cands.extend(res_obs.candidatos_top)

        # Dedup por (origen, id) y ordenar por score desc.
        mejor_por_id = {}
        for c in cands:
            origen = c.get('_origen', 'global')
            key = (origen, c.get('producto_id') or c.get('observer_id'))
            if key[1] is None:
                continue
            if key not in mejor_por_id or c['score'] > mejor_por_id[key]['score']:
                mejor_por_id[key] = c
        out = sorted(mejor_por_id.values(), key=lambda x: -x['score'])
        # Threshold adaptativo: cortar candidatos muy lejos del mejor.
        # Si el top es 1.00 → exigir ≥ 0.85.
        # Si el top es 0.75 → exigir ≥ 0.60 (max(threshold_min, 0.60)).
        # Si el top es 0.50 → exigir ≥ 0.50.
        # Idea: solo mostrar los que están en una banda cercana al mejor.
        # Reduce ruido como ATENIX/FILTEN apareciendo cuando el mejor es BIATRIX.
        if out:
            top_score = out[0]['score']
            threshold_efectivo = max(threshold_min, top_score - 0.15)
            out = [c for c in out if c['score'] >= threshold_efectivo]
        else:
            out = []
        return out[:top]
    finally:
        if own_session:
            session.close()


# ── Bulk: para cuando hay N items que matchear (ej. importar oferta) ────────

def match_productos_bulk(items, laboratorio_id=None, target='producto', session=None):
    """Matchea N items reusando una sola precarga de catálogo.

    Para target='producto', hace el flujo en 2 fases:
    1) Pasada local: cada item contra `productos` (sin fallback observer).
    2) Para los items sin match, precarga UNA VEZ el pool de obs_productos
       (filtrado por lab observer si está mapeado) y los matchea contra
       ese pool en memoria. Evita el N×M (122k×N) que clavaba el endpoint.

    Args:
        items: lista de dicts con keys ean/codigo_alfabeta/descripcion/precio.
        laboratorio_id: scope al lab local (productos.laboratorio_id).
        target: 'producto' (default) | 'obs_producto'.

    Returns:
        Lista de MatchResult, una por item, en el mismo orden.
    """
    import database
    own_session = session is None
    if own_session:
        session = database.SessionLocal()
    try:
        # Fase 1: matchear contra `productos` local SIN fallback observer.
        # incluir_candidatos=False porque el bulk no los usa (la UI los pide
        # aparte via /import-candidatos). Eso ahorra una pasada por item.
        results = [
            match_producto(
                ean=it.get('ean'),
                codigo_alfabeta=it.get('codigo_alfabeta') or it.get('codigo'),
                descripcion=it.get('descripcion'),
                laboratorio_id=laboratorio_id,
                target=target,
                incluir_observer=False,
                incluir_candidatos=False,
                precio_referencia=it.get('precio'),
                cantidad_envase=it.get('cantidad_envase'),
                monodroga=it.get('monodroga'),
                session=session,
            )
            for it in items
        ]

        # Fase 2: fallback observer en bulk con UNA precarga + UNA tokenización
        # del pool. Inline (no via match_producto) para evitar N×M tokenizaciones.
        if target == 'producto':
            no_match_idx = [i for i, r in enumerate(results) if r.producto is None]
            if no_match_idx:
                lab_obs_id = None
                if laboratorio_id:
                    lab_local = session.get(database.Laboratorio, laboratorio_id)
                    lab_obs_id = getattr(lab_local, 'observer_id', None) if lab_local else None
                obs_q = session.query(database.ObsProducto)
                if lab_obs_id is not None:
                    obs_q = obs_q.filter(database.ObsProducto.laboratorio_observer == lab_obs_id)
                obs_q = obs_q.filter(database.ObsProducto.fecha_baja.is_(None))
                obs_pool = obs_q.all()

                # Pre-tokenizar UNA sola vez todo el pool. Estructuras:
                # - obs_index: lista de (obj, norm, tokens, alfabeta)
                # - by_norm: dict normalizado → list of obs (para descripcion exacta)
                # - by_alfabeta: dict alfabeta → obs (para alfabeta exacto)
                obs_index = []
                by_norm = {}
                by_alfabeta = {}
                for obs in obs_pool:
                    desc = obs.descripcion or ''
                    norm = normalizar_texto(desc)
                    toks = tokens_significativos(desc)
                    alf = (obs.codigo_alfabeta or '').strip() or None
                    obs_index.append((obs, norm, toks, alf))
                    if norm:
                        by_norm.setdefault(norm, []).append(obs)
                    if alf:
                        by_alfabeta[alf] = obs

                threshold_obs = 0.80
                for i in no_match_idx:
                    it = items[i]
                    desc_in = it.get('descripcion') or ''
                    if not desc_in.strip():
                        continue
                    alf_in = (it.get('codigo_alfabeta') or it.get('codigo') or '').strip() or None

                    res = MatchResult()
                    encontrado = None
                    estrategia = ''

                    # 1. alfabeta exacto. Doble salvaguarda:
                    #   a) Solo confiamos si el código tiene >=5 dígitos
                    #      (alfabeta real). Cortos (1-4) son código interno.
                    #   b) Sanity check: las descripciones tienen que
                    #      compartir al menos 1 token significativo Y
                    #      jaccard >= 0.30. Sin esto: BLAVIN 19 → DEPAKENE.
                    if alf_in and alf_in in by_alfabeta:
                        digits = ''.join(ch for ch in alf_in if ch.isdigit())
                        if len(digits) >= 5:
                            cand = by_alfabeta[alf_in]
                            cand_desc = getattr(cand, 'descripcion', '') or ''
                            toks_a = tokens_significativos(desc_in)
                            toks_b = tokens_significativos(cand_desc)
                            ok_sanity = (
                                bool(toks_a & toks_b)
                                and jaccard(toks_a, toks_b) >= 0.30
                            ) if (toks_a and toks_b) else True
                            if ok_sanity:
                                encontrado = cand
                                estrategia = 'alfabeta_exacto'
                                score = 1.0

                    # 2. descripcion exacta
                    if not encontrado:
                        norm_in = normalizar_texto(desc_in)
                        hits = by_norm.get(norm_in, [])
                        if len(hits) == 1:
                            encontrado = hits[0]
                            estrategia = 'descripcion_exacta'
                            score = 1.0

                    # 3. tokens superset / fuzzy
                    # Mientras iteramos también contamos cuántos obs son
                    # "candidatos razonables" (jaccard >= UMBRAL_CAND).
                    # Sirve para que la UI muestre "sin candidatos" upfront
                    # en items donde sabemos que el modal va a venir vacío.
                    UMBRAL_CAND = 0.20
                    candidatos_count = 0
                    if not encontrado:
                        toks_in = tokens_significativos(desc_in)
                        if toks_in:
                            mejor = None
                            mejor_score = 0.0
                            empate = False
                            supersets = []
                            for obs, _norm, toks_o, _alf in obs_index:
                                if toks_o and toks_in.issubset(toks_o):
                                    supersets.append(obs)
                                s = jaccard(toks_in, toks_o)
                                if s >= UMBRAL_CAND:
                                    candidatos_count += 1
                                if s > mejor_score:
                                    mejor = obs
                                    mejor_score = s
                                    empate = False
                                elif s == mejor_score and s > 0:
                                    empate = True
                            if len(supersets) == 1:
                                encontrado = supersets[0]
                                estrategia = 'tokens_superset'
                                score = 0.95
                            elif mejor and mejor_score >= threshold_obs and not empate:
                                encontrado = mejor
                                estrategia = 'fuzzy_lab' if lab_obs_id else 'fuzzy_global'
                                score = mejor_score

                    if encontrado is not None:
                        res.producto = encontrado
                        res.score = score
                        res.estrategia = estrategia + '_obs'
                        res.confianza = _confianza(score)
                        res.warnings.append('match_observer')
                        results[i] = res
                    else:
                        # Sigue como not_found: guardamos el conteo de
                        # candidatos en el MatchResult para que el caller
                        # pueda decidir qué UI mostrar.
                        res.candidatos_count = candidatos_count
                        results[i] = res

        return results
    finally:
        if own_session:
            session.close()


def buscar_candidatos_bulk(items, laboratorio_id=None, top=8,
                           threshold_min=0.50, session=None):
    """Versión bulk de buscar_candidatos: carga los pools UNA sola vez.

    Sustituye N llamadas secuenciales a buscar_candidatos (cada una carga
    obs_productos entero y tokeniza ~122k filas) por una sola pasada.

    Args:
        items: list de dicts con 'idx' (int, identificador arbitrario) y
               'descripcion'. Opcionalmente 'ean', 'codigo' (código interno
               proveedor).
        laboratorio_id: scope al lab local para boost y filtro obs.
        top: máximo de candidatos por item.
        threshold_min: jaccard mínimo para incluir un candidato.

    Returns:
        dict idx → list de candidatos (misma estructura que buscar_candidatos).
    """
    if not items:
        return {}

    import database

    own_session = session is None
    if own_session:
        session = database.SessionLocal()

    try:
        # ── Pool 1: todos los Productos locales (una sola query) ─────────────
        all_prods = session.query(database.Producto).all()
        # Tokenizamos UNA sola vez y marcamos si pertenecen al lab pedido.
        prod_index = []
        for p in all_prods:
            toks = tokens_significativos(p.descripcion or '')
            is_lab = (laboratorio_id is not None
                      and getattr(p, 'laboratorio_id', None) == laboratorio_id)
            prod_index.append((p, toks, is_lab))

        # ── Pool 2: ObServer (obs_productos) — una sola query ────────────────
        lab_obs_id = None
        if laboratorio_id:
            lab_local = session.get(database.Laboratorio, laboratorio_id)
            lab_obs_id = getattr(lab_local, 'observer_id', None) if lab_local else None

        obs_q = session.query(database.ObsProducto).filter(
            database.ObsProducto.fecha_baja.is_(None),
        )
        if lab_obs_id is not None:
            obs_q = obs_q.filter(
                database.ObsProducto.laboratorio_observer == lab_obs_id,
            )
        obs_index = []
        for obs in obs_q.all():
            toks = tokens_significativos(obs.descripcion or '')
            alf = (obs.codigo_alfabeta or '').strip() or None
            obs_index.append((obs, toks, alf))

        # ── Matchear cada item ────────────────────────────────────────────────
        result = {}
        for it in items:
            idx = it.get('idx', 0)
            desc = (it.get('descripcion') or '').strip()
            if not desc:
                result[idx] = []
                continue
            toks_in = tokens_significativos(desc)
            if not toks_in:
                result[idx] = []
                continue

            alf_in = (it.get('codigo') or it.get('codigo_alfabeta') or '').strip() or None
            cands = []
            seen_keys = set()

            # Productos locales
            for p, toks_p, is_lab in prod_index:
                if not toks_p:
                    continue
                s = jaccard(toks_in, toks_p)
                if s < threshold_min:
                    continue
                boost = 0.05 if is_lab else 0.0
                s_final = round(min(1.0, s + boost), 3)
                key = ('prod', p.id)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                cands.append({
                    'producto_id': p.id,
                    'observer_id': None,
                    'descripcion': p.descripcion or '',
                    'codigo_barra': getattr(p, 'codigo_barra', None),
                    'codigo_alfabeta': getattr(p, 'codigo_alfabeta', '') or '',
                    'precio_pvp': float(p.precio_pvp) if getattr(p, 'precio_pvp', None) else None,
                    'score': s_final,
                    '_origen': 'lab' if is_lab else 'global',
                })

            # ObServer
            for obs, toks_o, alf_o in obs_index:
                # Alfabeta exacto (sanity check: al menos 1 token en común)
                if alf_in and alf_o and alf_in == alf_o:
                    digits = ''.join(ch for ch in alf_in if ch.isdigit())
                    if len(digits) >= 5:
                        ok = (bool(toks_in & toks_o)
                              and jaccard(toks_in, toks_o) >= 0.30
                              ) if (toks_in and toks_o) else True
                        if ok:
                            key = ('obs', obs.observer_id)
                            if key not in seen_keys:
                                seen_keys.add(key)
                                cands.append({
                                    'producto_id': None,
                                    'observer_id': obs.observer_id,
                                    'descripcion': obs.descripcion or '',
                                    'codigo_barra': None,
                                    'codigo_alfabeta': alf_o or '',
                                    'precio_pvp': None,
                                    'score': 1.0,
                                    '_origen': 'observer',
                                })
                        continue
                if not toks_o:
                    continue
                s = jaccard(toks_in, toks_o)
                if s < threshold_min:
                    continue
                key = ('obs', obs.observer_id)
                if key in seen_keys or obs.observer_id is None:
                    continue
                seen_keys.add(key)
                cands.append({
                    'producto_id': None,
                    'observer_id': obs.observer_id,
                    'descripcion': obs.descripcion or '',
                    'codigo_barra': None,
                    'codigo_alfabeta': alf_o or '',
                    'precio_pvp': None,
                    'score': round(s, 3),
                    '_origen': 'observer',
                })

            cands.sort(key=lambda x: -x['score'])
            if cands:
                top_score = cands[0]['score']
                threshold_efectivo = max(threshold_min, top_score - 0.15)
                cands = [c for c in cands if c['score'] >= threshold_efectivo]
            result[idx] = cands[:top]

        return result

    finally:
        if own_session:
            session.close()
