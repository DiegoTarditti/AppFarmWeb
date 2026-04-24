"""Detector de packs en módulos de laboratorio.

Combina 3 señales independientes para decidir si un ítem del módulo es pack:
  1. Destacado en amarillo en el Excel (marca del vendedor).
  2. Regex 'PACK X N' en la descripción (explícito + aporta cantidad).
  3. Sin ventas históricas por ese EAN (un pack nunca se vende por su código).

Un ítem es pack si tiene ≥1 señal. Confianza = cantidad de señales.
"""
import re

# Patrones alternativos para detectar "este producto es un pack de N unidades":
#   - "PACK X 10"
#   - "PACK 4 EST" / "PACK 4 ESTUCHES"
#   - "(PACK X 20)"
#   - "X 10 ESTUCHES" al final
PACK_PATTERNS = [
    re.compile(r'\bPACK\s*X\s*(\d+)\b', re.IGNORECASE),
    re.compile(r'\bPACK\s+(\d+)\s*EST(?:UCHES?)?\b', re.IGNORECASE),
    re.compile(r'\bX\s*(\d+)\s*EST(?:UCHES?)?\b', re.IGNORECASE),
]


def _match_pack(desc):
    """Prueba todos los patrones. Devuelve (match, cantidad_int) o (None, None)."""
    if not desc:
        return None, None
    for pat in PACK_PATTERNS:
        m = pat.search(desc)
        if m:
            try:
                return m, int(m.group(1))
            except (ValueError, TypeError):
                continue
    return None, None


# Compatibilidad con código viejo
PACK_PATTERN = PACK_PATTERNS[0]


def detectar_packs(modules, session, saltear_registrados=True):
    """Devuelve lista de candidatos a pack.

    modules: output de parsers.modulos_xlsx.parse_modulos_xlsx
    session: SQLAlchemy session

    Cada candidato: {ean_pack, desc_pack, cantidad, ean_unidad_sug, desc_unidad_sug,
                     fuente, modulo, destacado, tiene_regex, sin_ventas, confianza}
    """
    from database import ObsProducto, ObsVentaMensual, ModuloPack, Producto

    ya_registrados = set()
    if saltear_registrados:
        ya_registrados = {ep for (ep,) in session.query(ModuloPack.ean_pack)
                          .filter(ModuloPack.cantidad > 1).all()}

    # Juntar todos los EANs para bulk lookup
    todos_eans = set()
    for mod in modules or []:
        for it in mod.get('items') or mod.get('productos') or []:
            e = (it.get('ean') or '').strip()
            if e:
                todos_eans.add(e)

    # Map EAN → observer_id
    ean_a_obs = dict(
        session.query(Producto.codigo_barra, Producto.observer_id)
        .filter(Producto.codigo_barra.in_(todos_eans),
                Producto.observer_id.isnot(None)).all()
    )
    obs_ids = {oid for oid in ean_a_obs.values() if oid}
    con_ventas = set()
    if obs_ids:
        rows = (session.query(ObsVentaMensual.producto_observer)
                .filter(ObsVentaMensual.producto_observer.in_(obs_ids),
                        ObsVentaMensual.unidades > 0)
                .distinct().all())
        con_ventas = {r[0] for r in rows}

    def tuvo_ventas(ean):
        oid = ean_a_obs.get(ean)
        if oid is None:
            return False  # sin registro local = pack probable
        return oid in con_ventas

    candidatos = []
    for mod in modules or []:
        items = mod.get('items') or mod.get('productos') or []
        for it in items:
            desc = (it.get('desc') or it.get('descripcion') or '').strip()
            ean_pack = (it.get('ean') or '').strip()
            if not ean_pack or not desc or ean_pack in ya_registrados:
                continue
            destacado = bool(it.get('destacado'))
            m, cantidad = _match_pack(desc)
            sin_ventas = not tuvo_ventas(ean_pack)

            senales = sum([destacado, bool(m), sin_ventas])
            if senales == 0:
                continue

            if senales >= 2:
                confianza = 'alta'
            elif destacado or m:
                confianza = 'media'
            else:
                confianza = 'baja'

            # Remover todos los sufijos pack conocidos para obtener el "nombre base"
            base = desc
            base = re.sub(r'\s*\(?\s*PACK\s*X\s*\d+\s*\)?\s*', ' ', base, flags=re.I)
            base = re.sub(r'\s*PACK\s+\d+\s*EST(?:UCHES?)?\s*', ' ', base, flags=re.I)
            base = re.sub(r'\s*X\s*\d+\s*EST(?:UCHES?)?\s*$', ' ', base, flags=re.I)
            base = base.strip()
            base_toks = {t for t in re.split(r'\s+', base.lower()) if len(t) >= 2}

            unidad_ean = unidad_desc = None
            fuente = 'none'
            cand_unidad = []
            for it2 in items:
                d2 = (it2.get('desc') or it2.get('descripcion') or '').strip()
                e2 = (it2.get('ean') or '').strip()
                if not e2 or not d2 or e2 == ean_pack:
                    continue
                toks2 = {t for t in re.split(r'\s+', d2.lower()) if len(t) >= 2}
                inter = base_toks & toks2
                if len(inter) >= max(2, int(len(base_toks) * 0.5)):
                    score = len(inter) / max(len(base_toks | toks2), 1)
                    if tuvo_ventas(e2):
                        score += 0.5
                    cand_unidad.append((score, e2, d2))
            if cand_unidad:
                cand_unidad.sort(key=lambda x: -x[0])
                unidad_ean, unidad_desc = cand_unidad[0][1], cand_unidad[0][2]
                fuente = 'modulo'

            if not unidad_ean and base_toks:
                primer = next(iter(sorted(base_toks, key=len, reverse=True)), '')
                if len(primer) >= 3:
                    q = session.query(ObsProducto).filter(
                        ObsProducto.descripcion.ilike(f'%{primer}%'),
                        ObsProducto.fecha_baja.is_(None),
                    ).limit(50).all()
                    best, best_score = None, 0
                    for op in q:
                        # Saltear candidatos que también parecen pack (evita
                        # sugerir "CIRIAX OTIC... (PACK)" como unidad de otro pack)
                        if any(p.search(op.descripcion or '') for p in PACK_PATTERNS):
                            continue
                        toks_op = {t for t in re.split(r'\s+', op.descripcion.lower()) if len(t) >= 2}
                        if not toks_op:
                            continue
                        score = len(base_toks & toks_op) / len(base_toks | toks_op)
                        if score > best_score:
                            best, best_score = op, score
                    if best and best_score >= 0.4:
                        unidad_ean = f'OBS:{best.observer_id}'
                        unidad_desc = best.descripcion
                        fuente = 'catalogo'

            candidatos.append({
                'ean_pack':        ean_pack,
                'desc_pack':       desc,
                'cantidad':        cantidad,
                'ean_unidad_sug':  unidad_ean or '',
                'desc_unidad_sug': unidad_desc or '',
                'fuente':          fuente,
                'modulo':          mod.get('nombre') or '',
                'destacado':       destacado,
                'tiene_regex':     bool(m),
                'sin_ventas':      sin_ventas,
                'confianza':       confianza,
            })
    return candidatos
