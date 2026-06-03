"""Mapa de mercado por droga — materializa (en memoria) la inteligencia de
mercado recopilada por web search (marcas estrella por lab, cacheadas en
AnalisisIaCache) y la cruza con las ventas propias.

Hoy el web search guarda un JSON por lab (`gap_ws_data:<lab_norm>`). Esto NO es
consultable cross-lab por droga. Acá leemos esos JSON de los labs habilitados,
agrupamos las marcas por molécula y cruzamos cada una contra las ventas (vía
helpers.cruzar_marcas_vs_ventas) → comparativa por droga: qué marcas/labs
lideran cada molécula y cuánto vendo de cada una.

Liviano (POC sin migración): si valida, se promueve a una tabla materializada.
"""
import json
from collections import defaultdict

import database
import helpers
import referencia_mercado
from helpers import _normalizar_nombre_entidad, _ventana_12m_ym


def _clave_lab(nombre):
    return f'gap_ws_data:{_normalizar_nombre_entidad(nombre)}'[:80]


def _leer_cache_lab(session, nombre):
    """Devuelve (observer_id, marcas, fuentes, creado_en) del lab cacheado, o
    None si el lab no está en ObServer o no tiene caché de web search."""
    oid = helpers.resolver_obs_lab_por_nombre(session, nombre)
    if oid is None:
        return None
    row = session.get(database.AnalisisIaCache, _clave_lab(nombre))
    if not row or not row.texto:
        return (oid, None, None, None)
    try:
        cached = json.loads(row.texto)
    except (ValueError, TypeError):
        return (oid, None, None, None)
    return (oid, cached.get('marcas', []), cached.get('fuentes', []), row.creado_en)


def labs_cacheados_estado(session):
    """Estado del caché de mercado por lab: [{nombre, observer_id, en_observer,
    consultado_en, edad_dias, n_marcas, n_fuentes, cacheado}]. Para el panel
    'laboratorio + última consulta'."""
    out = []
    for nombre in referencia_mercado.LABS_GAP_WEBSEARCH:
        leido = _leer_cache_lab(session, nombre)
        if leido is None:
            out.append({'nombre': nombre, 'observer_id': None, 'en_observer': False,
                        'consultado_en': None, 'edad_dias': None,
                        'n_marcas': 0, 'n_fuentes': 0, 'cacheado': False})
            continue
        oid, marcas, fuentes, creado = leido
        edad = (database.now_ar() - creado).days if creado else None
        out.append({
            'nombre': nombre, 'observer_id': oid, 'en_observer': True,
            'consultado_en': creado.strftime('%d/%m/%Y') if creado else None,
            'edad_dias': edad,
            'n_marcas': len(marcas) if marcas else 0,
            'n_fuentes': len(fuentes) if fuentes else 0,
            'cacheado': bool(creado),
        })
    return out


def _marcas_estrella_por_droga(session):
    """Mapa {droga_observer_id: {lab_observer_id: {'marca', 'top10'}}} a partir
    de las marcas estrella cacheadas: para cada marca, busca sus productos (por
    match_pattern) y de ahí saca la droga real (nombre_droga_observer). Así se
    sabe qué lab es 'marca líder de mercado' en cada droga real."""
    estrella = defaultdict(dict)
    for nombre in referencia_mercado.LABS_GAP_WEBSEARCH:
        leido = _leer_cache_lab(session, nombre)
        if not leido:
            continue
        oid, marcas, _fu, _cr = leido
        for m in (marcas or []):
            patt = (m.get('match_pattern') or m.get('marca') or '').strip()
            if not patt:
                continue
            rows = (session.query(database.ObsProducto.nombre_droga_observer)
                    .filter(database.ObsProducto.laboratorio_observer == oid,
                            database.ObsProducto.descripcion.ilike(f'%{patt}%'),
                            database.ObsProducto.fecha_baja.is_(None),
                            database.ObsProducto.nombre_droga_observer.isnot(None))
                    .distinct().all())
            for (drid,) in rows:
                prev = estrella[drid].get(oid, {})
                estrella[drid][oid] = {
                    'marca': prev.get('marca') or m.get('marca'),
                    'top10': prev.get('top10', False) or bool(m.get('top10_nacional')),
                }
    return estrella


def fuentes_por_lab(session):
    """{lab_observer_id: [{titulo, url}]} de los labs cacheados — las fuentes
    de mercado que respaldan la info de cada lab (para desplegar al click)."""
    out = {}
    for nombre in referencia_mercado.LABS_GAP_WEBSEARCH:
        leido = _leer_cache_lab(session, nombre)
        if not leido:
            continue
        oid, _marcas, fuentes, _cr = leido
        if oid is not None and fuentes:
            out[oid] = fuentes
    return out


def comparativa_mercado_por_droga(session):
    """Comparativa por droga REAL (ObsNombreDroga). Para cada droga donde hay al
    menos una marca estrella conocida, trae TODOS los labs que la farmacia vende
    (competencia + genéricos incluidos), marcando cuál es la marca líder de
    mercado (web search) y su share. Devuelve lista ordenada por u12m:

      [{droga, droga_id, n_labs, total_u12m, total_monto, lider_marca, lider_lab,
        lider_share_pct, gap, labs: [{lab, lab_id, u12m, monto, n_productos,
        es_estrella, marca_estrella, top10_nacional, share_pct}]}].
    """
    from sqlalchemy import func as _f
    desde, hasta = _ventana_12m_ym()
    estrella_por_droga = _marcas_estrella_por_droga(session)

    nombre_lab = {}

    def _lab_nombre(lab_oid):
        if lab_oid not in nombre_lab:
            row = session.get(database.ObsLaboratorio, lab_oid)
            nombre_lab[lab_oid] = row.descripcion if row else f'lab {lab_oid}'
        return nombre_lab[lab_oid]

    out = []
    for drid, estrellas in estrella_por_droga.items():
        drow = session.get(database.ObsNombreDroga, drid)
        droga_nombre = drow.descripcion if drow else f'droga {drid}'
        vm, op = database.ObsVentaMensual, database.ObsProducto
        rows = (session.query(op.laboratorio_observer,
                              _f.sum(vm.unidades), _f.sum(vm.monto),
                              _f.count(op.observer_id.distinct()))
                .join(vm, vm.producto_observer == op.observer_id)
                .filter(op.nombre_droga_observer == drid,
                        op.fecha_baja.is_(None),
                        vm.anio * 100 + vm.mes >= desde,
                        vm.anio * 100 + vm.mes <= hasta)
                .group_by(op.laboratorio_observer).all())
        labs, total_u, total_m = [], 0, 0.0
        for lab_oid, u, mo, nprod in rows:
            u, mo = int(u or 0), float(mo or 0)
            total_u += u
            total_m += mo
            est = estrellas.get(lab_oid)
            labs.append({
                'lab': _lab_nombre(lab_oid), 'lab_id': lab_oid,
                'u12m': u, 'monto': round(mo, 2), 'n_productos': int(nprod or 0),
                'es_estrella': bool(est),
                'marca_estrella': est.get('marca') if est else None,
                'top10_nacional': est.get('top10', False) if est else False,
            })
        if not labs:
            continue
        for lab in labs:
            lab['share_pct'] = round(lab['u12m'] / total_u * 100, 1) if total_u else 0.0
        labs.sort(key=lambda x: -x['u12m'])
        lider = (next((x for x in labs if x['top10_nacional']), None)
                 or next((x for x in labs if x['es_estrella']), None))
        lider_share = lider['share_pct'] if lider else None
        out.append({
            'droga': droga_nombre, 'droga_id': drid,
            'n_labs': len(labs), 'total_u12m': total_u, 'total_monto': round(total_m, 2),
            'labs': labs,
            'lider_marca': lider['marca_estrella'] if lider else None,
            'lider_lab': lider['lab'] if lider else None,
            'lider_share_pct': lider_share,
            # gap: la marca líder de mercado captura poco share de tu venta de la droga
            'gap': lider is not None and lider_share is not None and lider_share < 40,
        })
    out.sort(key=lambda d: -d['total_u12m'])
    return out
