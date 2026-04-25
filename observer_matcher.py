"""Matcher bulk entre productos locales (EAN) y obs_productos (IdProducto).

Es el único matcher del sistema que NO delega en `producto_matcher.match_producto`
item por item: la performance del job (30k locales × 122k obs) requiere precargar
tokens del catálogo ObServer una sola vez. Por eso reimplementa la cascada con un
índice in-memory.

Cascada (alineada con `producto_matcher`):
1. Match por `codigo_alfabeta` (1:1, determinístico).
2. Descripción exacta normalizada dentro del lab.
3. Tokens superset: tokens del producto local ⊆ tokens del obsproducto.
4. Jaccard fuzzy dentro del lab (threshold).

Si hay empate de scores en (4) → ambiguo (no vincula, deja para revisión manual).

Las primitivas de texto (normalizar/tokens/jaccard) vienen de `producto_matcher`
para tener UNA fuente de verdad en todo el sistema.
"""
from collections import defaultdict

from producto_matcher import jaccard as _jaccard
from producto_matcher import normalizar_texto as _normalize
from producto_matcher import tokens_significativos as _tokens


def match_productos(session, threshold=0.80, commit_each=500):
    """Corre el matcher sobre TODOS los productos locales sin observer_id.

    Args:
        session: SQLAlchemy session abierta.
        threshold: Jaccard mínimo para auto-link (0.80 = bastante estricto).
        commit_each: cada N productos procesados hace un flush.

    Returns:
        dict con stats: {'procesados', 'linked_alfabeta', 'linked_exact',
                         'linked_superset', 'linked_fuzzy',
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

    stats = dict(procesados=0, linked_alfabeta=0, linked_exact=0,
                 linked_superset=0, linked_fuzzy=0,
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

        # 2-3. Estrategias por descripción (solo dentro del lab)
        if lab_obs is None:
            stats['sin_match'] += 1
            continue

        candidatos = index_por_lab.get(lab_obs, [])

        # 2. Tokens superset: input ⊆ obs. Si exactamente 1 candidato lo cumple,
        #    es match de alta confianza (alineado con matcher central).
        if toks_p:
            supersets = [obs_id for obs_id, _desc, _norm, toks_o in candidatos
                         if toks_o and toks_p.issubset(toks_o)]
            if len(supersets) == 1:
                p.observer_id = supersets[0]
                stats['linked_superset'] += 1
                if stats['procesados'] % commit_each == 0:
                    session.flush()
                continue

        # 3. Fuzzy: mejor Jaccard dentro del lab.
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
    Delega en `producto_matcher.buscar_candidatos(target='obs_producto')` para
    mantener UNA sola implementación del scoring.
    """
    from database import Laboratorio, Producto
    from producto_matcher import buscar_candidatos

    p = session.get(Producto, producto_id)
    if not p:
        return []

    lab = session.get(Laboratorio, p.laboratorio_id) if p.laboratorio_id else None
    lab_obs = lab.observer_id if lab else None

    return buscar_candidatos(
        descripcion=p.descripcion or '',
        laboratorio_id=lab_obs,
        target='obs_producto',
        top=top_n,
        session=session,
    )
