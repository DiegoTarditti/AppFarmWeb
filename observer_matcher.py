"""Matcher entre productos locales (EAN) y obs_productos (IdProducto).

Estrategia:
1. Para cada producto local con observer_id NULL:
   a. Si su Laboratorio local tiene observer_id, limita candidatos a obs_productos
      del mismo laboratorio_observer. Si no, busca global.
   b. Aplica match por nombre normalizado (exacto, después tokens).
2. Si hay match único → setea productos.observer_id.
3. Si no hay match o hay varios con mismo score → lo deja sin vincular para revisión manual.

Normalización:
- Lowercase
- Unicode NFKD + strip accents
- Colapsa whitespace
- Saca puntuación/sufijos comunes
"""
import re
import unicodedata
from collections import defaultdict


def _normalize(s):
    if not s:
        return ''
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r'[^\w\s]', ' ', s)   # puntuación -> espacio
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _tokens(s):
    """Set de tokens alfanuméricos normalizados, ignora tokens de 1 char."""
    return {t for t in _normalize(s).split() if len(t) >= 2}


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = a & b
    union = a | b
    return len(inter) / len(union) if union else 0.0


def match_productos(session, threshold=0.80, commit_each=500):
    """Corre el matcher sobre TODOS los productos locales sin observer_id.

    Args:
        session: SQLAlchemy session abierta.
        threshold: Jaccard mínimo para auto-link (0.80 = bastante estricto).
        commit_each: cada N productos procesados hace un flush.

    Returns:
        dict con stats: {'procesados', 'linked_exact', 'linked_fuzzy',
                         'sin_match', 'ambiguos', 'sin_lab'}
    """
    from database import Laboratorio, ObsProducto, Producto

    # Mapa laboratorio_id local → observer_id
    lab_to_obs = dict(
        session.query(Laboratorio.id, Laboratorio.observer_id)
        .filter(Laboratorio.observer_id.isnot(None)).all()
    )

    # Indice por lab: normalizado → lista de (observer_id, desc, tokens)
    # Cargamos todos los obs_productos a memoria (122k filas, manejable).
    index_por_lab = defaultdict(list)
    # Indice por codigo_alfabeta → observer_id (bridge preferente si existe
    # en el producto local; más confiable que matchear por descripción).
    index_alfabeta = {}
    for obs in session.query(ObsProducto).all():
        entry = (obs.observer_id, obs.descripcion, _normalize(obs.descripcion),
                 _tokens(obs.descripcion))
        index_por_lab[obs.laboratorio_observer].append(entry)
        if obs.codigo_alfabeta:
            index_alfabeta[obs.codigo_alfabeta.strip()] = obs.observer_id

    # Indice exacto por lab: (normalizado) → list of obs_ids
    exacto_por_lab = defaultdict(lambda: defaultdict(list))
    for lab_obs, entries in index_por_lab.items():
        for obs_id, _desc, norm, _toks in entries:
            exacto_por_lab[lab_obs][norm].append(obs_id)

    # Pendientes: productos locales sin observer_id
    pendientes = (session.query(Producto)
                  .filter(Producto.observer_id.is_(None)).all())

    stats = dict(procesados=0, linked_alfabeta=0, linked_exact=0, linked_fuzzy=0,
                 sin_match=0, ambiguos=0, sin_lab=0)

    for p in pendientes:
        stats['procesados'] += 1

        # 0. Match por codigo_alfabeta: es 1:1 y deterministico.
        if p.codigo_alfabeta:
            obs_id_alfa = index_alfabeta.get(p.codigo_alfabeta.strip())
            if obs_id_alfa:
                p.observer_id = obs_id_alfa
                stats['linked_alfabeta'] += 1
                if stats['procesados'] % commit_each == 0:
                    session.flush()
                continue

        if not p.descripcion:
            stats['sin_match'] += 1
            continue

        norm = _normalize(p.descripcion)
        toks_p = _tokens(p.descripcion)
        lab_obs = lab_to_obs.get(p.laboratorio_id) if p.laboratorio_id else None

        # 1. Exact match dentro del lab (o global si no hay lab)
        if lab_obs is not None:
            exact_hits = exacto_por_lab.get(lab_obs, {}).get(norm, [])
        else:
            # Global: unir exactos de todos los labs
            exact_hits = []
            for d in exacto_por_lab.values():
                exact_hits += d.get(norm, [])
            if p.laboratorio_id is None:
                stats['sin_lab'] += 1

        if len(exact_hits) == 1:
            p.observer_id = exact_hits[0]
            stats['linked_exact'] += 1
            if stats['procesados'] % commit_each == 0:
                session.flush()
            continue
        if len(exact_hits) > 1:
            stats['ambiguos'] += 1
            continue

        # 2. Fuzzy dentro del lab (si hay lab); si no hay lab, no fuzzy global
        if lab_obs is None:
            stats['sin_match'] += 1
            continue

        candidatos = index_por_lab.get(lab_obs, [])
        mejor = None
        mejor_score = 0.0
        empate = False
        for obs_id, _desc, _norm, toks_o in candidatos:
            score = _jaccard(toks_p, toks_o)
            if score > mejor_score:
                mejor = obs_id
                mejor_score = score
                empate = False
            elif score == mejor_score and score > 0:
                empate = True

        if mejor and mejor_score >= threshold and not empate:
            p.observer_id = mejor
            stats['linked_fuzzy'] += 1
        elif empate:
            stats['ambiguos'] += 1
        else:
            stats['sin_match'] += 1

        if stats['procesados'] % commit_each == 0:
            session.flush()

    return stats


def candidatos_para_producto(session, producto_id, top_n=10):
    """Devuelve los top_n candidatos de obs_productos para un Producto local.

    Ordenados por score Jaccard decreciente. Scope al laboratorio si está vinculado.
    """
    from database import Laboratorio, ObsProducto, Producto

    p = session.get(Producto, producto_id)
    if not p:
        return []

    toks_p = _tokens(p.descripcion or '')
    lab = session.get(Laboratorio, p.laboratorio_id) if p.laboratorio_id else None
    lab_obs = lab.observer_id if lab else None

    q = session.query(ObsProducto)
    if lab_obs is not None:
        q = q.filter(ObsProducto.laboratorio_observer == lab_obs)

    resultados = []
    for obs in q.all():
        score = _jaccard(toks_p, _tokens(obs.descripcion or ''))
        if score > 0:
            resultados.append({
                'observer_id': obs.observer_id,
                'descripcion': obs.descripcion,
                'codigo_alfabeta': obs.codigo_alfabeta,
                'score': round(score, 3),
            })
    resultados.sort(key=lambda r: r['score'], reverse=True)
    return resultados[:top_n]
