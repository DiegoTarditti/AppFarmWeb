"""Detecta duplicados existentes en `laboratorios` y `proveedores` (Provider)
agrupando por nombre normalizado profundo (sin acentos, sin sufijos societarios,
sin puntuación). Por default corre en --dry-run: solo lista qué fusionaría.

Uso:
    # Solo listar (dry-run, default):
    docker compose exec -T web python -m scripts.dedupe_labs_drogs

    # Aplicar la fusión (cuidado, modifica DB):
    docker compose exec -T web python -m scripts.dedupe_labs_drogs --apply

Estrategia de "ganador" (cuál se queda como canónico cuando hay duplicados):
    1. Mayor cantidad de FKs entrantes (facturas, pedidos, módulos).
    2. Empate → el que tenga `observer_id` (bridge a Observer).
    3. Empate → el que tenga `cuit` (en Provider).
    4. Empate → el ID más bajo (más viejo).

Lo que migra antes de borrar el duplicado:
    Laboratorio:  Producto.laboratorio_id, ExportTemplate, OfertaMinimo,
                  Modulo.laboratorio_id, ProductoStock, AnalisisSesion
    Provider:     Invoice.proveedor_*, Claim.proveedor_id, BarcodeMapping,
                  Pedido.canal_partner_id, PlantillaExportacion

Idempotente: corre 2 veces y la 2da no tiene nada que fusionar.
"""
import argparse
import sys
from collections import defaultdict

from sqlalchemy import func

# Path hack — uso desde docker exec o como módulo.
import os as _os
_BASE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

import database
from database import Laboratorio, Provider, Producto, Invoice, Claim, BarcodeMapping
from helpers import _normalizar_nombre_entidad


def _grupos_por_norma(session, modelo, attr_nombre):
    """Agrupa registros por nombre normalizado. Devuelve {norm: [registros...]}."""
    grupos = defaultdict(list)
    for r in session.query(modelo).all():
        nombre = getattr(r, attr_nombre, None)
        norm = _normalizar_nombre_entidad(nombre)
        if norm:
            grupos[norm].append(r)
    return {k: v for k, v in grupos.items() if len(v) > 1}


def _elegir_ganador_lab(session, candidatos):
    """Elige el lab que se queda como canónico de un grupo de duplicados."""
    def score(lab):
        n_prods = (session.query(func.count(Producto.id))
                   .filter(Producto.laboratorio_id == lab.id).scalar() or 0)
        return (
            n_prods,                          # más productos vinculados
            1 if lab.observer_id else 0,      # tiene observer_id
            -lab.id,                          # ID más bajo (negado para ordenar desc)
        )
    return max(candidatos, key=score)


def _elegir_ganador_prov(session, candidatos):
    def score(p):
        n_inv = (session.query(func.count(Invoice.id))
                 .filter(Invoice.proveedor_razon == p.razon_social).scalar() or 0)
        return (
            n_inv,
            1 if p.cuit else 0,
            1 if p.parser_file else 0,
            -p.id,
        )
    return max(candidatos, key=score)


def _fusionar_lab(session, ganador, perdedores, dry_run=True):
    """Migra FKs de los `perdedores` al `ganador`, después borra los perdedores."""
    cambios = []
    perd_ids = [p.id for p in perdedores]

    # 1. Producto.laboratorio_id
    n = (session.query(Producto)
         .filter(Producto.laboratorio_id.in_(perd_ids)).count())
    if n:
        cambios.append(f'  - {n} producto(s) van de los IDs {perd_ids} → lab {ganador.id} ({ganador.nombre!r})')
        if not dry_run:
            (session.query(Producto)
             .filter(Producto.laboratorio_id.in_(perd_ids))
             .update({Producto.laboratorio_id: ganador.id}, synchronize_session=False))

    # 2. ExportTemplate
    try:
        from database import ExportTemplate
        n = session.query(ExportTemplate).filter(ExportTemplate.laboratorio_id.in_(perd_ids)).count()
        if n:
            cambios.append(f'  - {n} ExportTemplate(s) → lab {ganador.id}')
            if not dry_run:
                # ExportTemplate.PK = laboratorio_id; pueden chocar al fusionar.
                # Borramos los del perdedor si el ganador ya tiene template.
                if session.query(ExportTemplate).filter_by(laboratorio_id=ganador.id).first():
                    session.query(ExportTemplate).filter(ExportTemplate.laboratorio_id.in_(perd_ids)).delete(synchronize_session=False)
                else:
                    (session.query(ExportTemplate)
                     .filter(ExportTemplate.laboratorio_id.in_(perd_ids))
                     .update({ExportTemplate.laboratorio_id: ganador.id}, synchronize_session=False))
    except ImportError:
        pass

    # 3. OfertaMinimo
    try:
        from database import OfertaMinimo
        n = session.query(OfertaMinimo).filter(OfertaMinimo.laboratorio_id.in_(perd_ids)).count()
        if n:
            cambios.append(f'  - {n} OfertaMinimo(s) → lab {ganador.id}')
            if not dry_run:
                (session.query(OfertaMinimo)
                 .filter(OfertaMinimo.laboratorio_id.in_(perd_ids))
                 .update({OfertaMinimo.laboratorio_id: ganador.id}, synchronize_session=False))
    except ImportError:
        pass

    # 4. Modulo
    try:
        from database import Modulo
        n = session.query(Modulo).filter(Modulo.laboratorio_id.in_(perd_ids)).count()
        if n:
            cambios.append(f'  - {n} Modulo(s) → lab {ganador.id}')
            if not dry_run:
                (session.query(Modulo)
                 .filter(Modulo.laboratorio_id.in_(perd_ids))
                 .update({Modulo.laboratorio_id: ganador.id}, synchronize_session=False))
    except ImportError:
        pass

    # 5. AnalisisSesion
    try:
        from database import AnalisisSesion
        n = session.query(AnalisisSesion).filter(AnalisisSesion.laboratorio_id.in_(perd_ids)).count()
        if n:
            cambios.append(f'  - {n} AnalisisSesion(s) → lab {ganador.id}')
            if not dry_run:
                (session.query(AnalisisSesion)
                 .filter(AnalisisSesion.laboratorio_id.in_(perd_ids))
                 .update({AnalisisSesion.laboratorio_id: ganador.id}, synchronize_session=False))
    except ImportError:
        pass

    # 6. Pedido.lab_id (si existe)
    try:
        from database import Pedido
        if hasattr(Pedido, 'lab_id'):
            n = session.query(Pedido).filter(Pedido.lab_id.in_(perd_ids)).count()
            if n:
                cambios.append(f'  - {n} Pedido(s).lab_id → lab {ganador.id}')
                if not dry_run:
                    (session.query(Pedido)
                     .filter(Pedido.lab_id.in_(perd_ids))
                     .update({Pedido.lab_id: ganador.id}, synchronize_session=False))
    except ImportError:
        pass

    # Finalmente borrar los perdedores
    cambios.append(f'  - Borrar {len(perdedores)} duplicado(s): {[(p.id, p.nombre) for p in perdedores]}')
    if not dry_run:
        for p in perdedores:
            session.delete(p)

    return cambios


def _fusionar_prov(session, ganador, perdedores, dry_run=True):
    cambios = []
    perd_ids = [p.id for p in perdedores]
    perd_razones = [p.razon_social for p in perdedores]

    # 1. Invoice.proveedor_razon (string-FK)
    n = session.query(Invoice).filter(Invoice.proveedor_razon.in_(perd_razones)).count()
    if n:
        cambios.append(f'  - {n} Invoice(s) renombrados a "{ganador.razon_social}"')
        if not dry_run:
            (session.query(Invoice)
             .filter(Invoice.proveedor_razon.in_(perd_razones))
             .update({Invoice.proveedor_razon: ganador.razon_social}, synchronize_session=False))

    # 2. Claim.proveedor_id
    n = session.query(Claim).filter(Claim.proveedor_id.in_(perd_ids)).count()
    if n:
        cambios.append(f'  - {n} Claim(s).proveedor_id → prov {ganador.id}')
        if not dry_run:
            (session.query(Claim)
             .filter(Claim.proveedor_id.in_(perd_ids))
             .update({Claim.proveedor_id: ganador.id}, synchronize_session=False))

    # 3. BarcodeMapping
    n = session.query(BarcodeMapping).filter(BarcodeMapping.proveedor_id.in_(perd_ids)).count()
    if n:
        cambios.append(f'  - {n} BarcodeMapping(s) → prov {ganador.id}')
        if not dry_run:
            (session.query(BarcodeMapping)
             .filter(BarcodeMapping.proveedor_id.in_(perd_ids))
             .update({BarcodeMapping.proveedor_id: ganador.id}, synchronize_session=False))

    # 4. Pedido.canal_partner_id (si existe)
    try:
        from database import Pedido
        if hasattr(Pedido, 'canal_partner_id'):
            n = session.query(Pedido).filter(Pedido.canal_partner_id.in_(perd_ids)).count()
            if n:
                cambios.append(f'  - {n} Pedido(s).canal_partner_id → prov {ganador.id}')
                if not dry_run:
                    (session.query(Pedido)
                     .filter(Pedido.canal_partner_id.in_(perd_ids))
                     .update({Pedido.canal_partner_id: ganador.id}, synchronize_session=False))
    except ImportError:
        pass

    # 5. PlantillaExportacion
    try:
        from database import PlantillaExportacion
        n = session.query(PlantillaExportacion).filter(PlantillaExportacion.proveedor_id.in_(perd_ids)).count()
        if n:
            cambios.append(f'  - {n} PlantillaExportacion(s) → prov {ganador.id}')
            if not dry_run:
                (session.query(PlantillaExportacion)
                 .filter(PlantillaExportacion.proveedor_id.in_(perd_ids))
                 .update({PlantillaExportacion.proveedor_id: ganador.id}, synchronize_session=False))
    except ImportError:
        pass

    # 6. ProductoPrecioHist (string-FK por proveedor_razon, también por proveedor_id)
    try:
        from database import ProductoPrecioHist
        n = session.query(ProductoPrecioHist).filter(ProductoPrecioHist.proveedor_id.in_(perd_ids)).count()
        if n:
            cambios.append(f'  - {n} ProductoPrecioHist(s) → prov {ganador.id}')
            if not dry_run:
                (session.query(ProductoPrecioHist)
                 .filter(ProductoPrecioHist.proveedor_id.in_(perd_ids))
                 .update({ProductoPrecioHist.proveedor_id: ganador.id, ProductoPrecioHist.proveedor_razon: ganador.razon_social}, synchronize_session=False))
    except ImportError:
        pass

    cambios.append(f'  - Borrar {len(perdedores)} duplicado(s): {[(p.id, p.razon_social) for p in perdedores]}')
    if not dry_run:
        for p in perdedores:
            session.delete(p)

    return cambios


def main():
    parser = argparse.ArgumentParser(description='Detecta y fusiona duplicados de Laboratorio y Provider')
    parser.add_argument('--apply', action='store_true', help='Aplicar la fusión (sin esto = dry-run)')
    args = parser.parse_args()
    dry_run = not args.apply

    database.init_db()
    print('🔍 Buscando duplicados…')
    print(f'   Modo: {"DRY-RUN (no toca DB)" if dry_run else "APLICAR (modifica DB)"}\n')

    with database.get_db() as session:
        # ── LABORATORIOS ──────────────────────────────────────────────────
        grupos_lab = _grupos_por_norma(session, Laboratorio, 'nombre')
        if not grupos_lab:
            print('✅ Sin duplicados en LABORATORIOS.\n')
        else:
            print(f'⚠ {len(grupos_lab)} grupo(s) de duplicados en LABORATORIOS:\n')
            for norm, candidatos in grupos_lab.items():
                ganador = _elegir_ganador_lab(session, candidatos)
                perdedores = [c for c in candidatos if c.id != ganador.id]
                print(f'  Norma "{norm}" ({len(candidatos)} variantes):')
                for c in candidatos:
                    marca = '👑 GANA' if c.id == ganador.id else '   '
                    print(f'    {marca}  id={c.id:>5}  obs={c.observer_id or "-":>5}  "{c.nombre}"')
                cambios = _fusionar_lab(session, ganador, perdedores, dry_run=dry_run)
                for c in cambios:
                    print(c)
                print()

        # ── PROVEEDORES ───────────────────────────────────────────────────
        grupos_prov = _grupos_por_norma(session, Provider, 'razon_social')
        if not grupos_prov:
            print('✅ Sin duplicados en PROVEEDORES.\n')
        else:
            print(f'⚠ {len(grupos_prov)} grupo(s) de duplicados en PROVEEDORES:\n')
            for norm, candidatos in grupos_prov.items():
                ganador = _elegir_ganador_prov(session, candidatos)
                perdedores = [c for c in candidatos if c.id != ganador.id]
                print(f'  Norma "{norm}" ({len(candidatos)} variantes):')
                for c in candidatos:
                    marca = '👑 GANA' if c.id == ganador.id else '   '
                    print(f'    {marca}  id={c.id:>5}  cuit={c.cuit or "-":<14}  "{c.razon_social}"')
                cambios = _fusionar_prov(session, ganador, perdedores, dry_run=dry_run)
                for c in cambios:
                    print(c)
                print()

        if not dry_run:
            session.commit()
            print('✅ Cambios persistidos en DB.')
        elif grupos_lab or grupos_prov:
            print('💡 Esto fue un DRY-RUN. Para aplicar, corré el mismo comando con --apply')


if __name__ == '__main__':
    main()
