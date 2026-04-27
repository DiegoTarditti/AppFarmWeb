"""Seed de datos de prueba para el flujo de compra rápida.

Popula:
- DescuentoBase: matriz labs × drogerías con descuentos base 31.03%.
- Provider.compra_minima_pesos: un mínimo de prueba en una droguería.
- OfertaMinimo: ofertas reales del transfer Baliarda Agosto'25 con
  productos reales (observer_id como pseudo-EAN).

Idempotente: borrar+recrear los descuentos_base si ya existen.

Uso:
    docker exec appfarmweb-web-1 sh -c "cd /app && python -m scripts.seed_compra_rapida"
"""
from datetime import date, timedelta

import database
from database import (DescuentoBase, Laboratorio, OfertaMinimo, Provider,
                       get_db, init_db, now_ar)


# Descuento base STANDARD validado con el ejemplo del usuario:
# 31.03% base + 25% transfer = 48.27% (coincide con el "% PVP" 48.29 del Excel
# Baliarda, diferencia por redondeo).
DTO_BASE_STANDARD = 31.03

DESCUENTOS_BASE = [
    # (lab_nombre, drog_razon_social, descuento_pct, plazo)
    # Bayer Consumer (validado por usuario)
    ('Bayer Consumer',  'DROGUERÍA KELLERHOFF S.A.', 31.03, '30 dias'),
    ('Bayer Consumer',  'PHARMOS S.A.',              28.00, '30 dias'),
    ('Bayer Consumer',  'VIA PHARMA',                25.50, '45 dias'),
    # Roemmers
    ('Roemmers',        'DROGUERÍA KELLERHOFF S.A.', 30.00, '30 dias'),
    ('Roemmers',        'PHARMOS S.A.',              28.50, '45 dias'),
    ('Roemmers',        'VIA PHARMA',                24.00, '45 dias'),
    # BAGO
    ('BAGO S.A.',       'DROGUERÍA KELLERHOFF S.A.', 26.00, '30 dias'),
    ('BAGO S.A.',       'PHARMOS S.A.',              25.00, '30 dias'),
    ('BAGO S.A.',       'VIA PHARMA',                22.00, '45 dias'),
    # Baliarda (mismo 31.03 que Bayer según los Excel del usuario)
    ('Baliarda',        'DROGUERÍA KELLERHOFF S.A.', 31.03, '30 dias'),
    ('Baliarda',        'PHARMOS S.A.',              28.00, '45 dias'),
    ('Baliarda',        'VIA PHARMA',                25.00, '45 dias'),
]

# Mínimo de compra para una droguería (los demás quedan sin mínimo).
MINIMOS_DROGUERIA = [
    ('PHARMOS S.A.', 50000.00),  # solo Pharmos exige mínimo
]


# Las ofertas con cantidad mínima (transfers) NO se seedean — deben pasar por
# `/ofertas/importar` (módulo existente con depuración fuzzy match para
# resolver EANs faltantes). El usuario carga el Excel real por ahí.


def seed():
    init_db()
    with get_db() as session:
        # ── Resolver IDs lab + drog ──────────────────────────────────────
        labs_ids = {}
        for nombre in {x[0] for x in DESCUENTOS_BASE}:
            lab = session.query(Laboratorio).filter(
                Laboratorio.nombre.ilike(nombre)).first()
            if not lab:
                print(f'  ⚠ Lab "{nombre}" no encontrado, skip')
                continue
            labs_ids[nombre] = lab.id
            print(f'  Lab "{nombre}" → id={lab.id}')

        drogs_ids = {}
        for razon in {x[1] for x in DESCUENTOS_BASE}:
            drog = session.query(Provider).filter(
                Provider.razon_social.ilike(razon),
                Provider.tipo == 'drogueria').first()
            if not drog:
                print(f'  ⚠ Droguería "{razon}" no encontrada, skip')
                continue
            drogs_ids[razon] = drog.id
            print(f'  Droguería "{razon}" → id={drog.id}')

        # ── Limpiar descuentos_base previos del seed ─────────────────────
        deleted = session.query(DescuentoBase).filter(
            DescuentoBase.laboratorio_id.in_(labs_ids.values()),
            DescuentoBase.drogueria_id.in_(drogs_ids.values()),
        ).delete(synchronize_session=False)
        if deleted:
            print(f'\n  Limpiados {deleted} descuentos previos del seed')

        # ── Insertar matriz de descuentos base ───────────────────────────
        hoy = date.today()
        n_inserted = 0
        for (lab_nombre, drog_razon, pct, plazo) in DESCUENTOS_BASE:
            lab_id = labs_ids.get(lab_nombre)
            drog_id = drogs_ids.get(drog_razon)
            if not (lab_id and drog_id):
                continue
            session.add(DescuentoBase(
                laboratorio_id=lab_id,
                drogueria_id=drog_id,
                descuento_pct=pct,
                plazo_pago=plazo,
                vigencia_desde=hoy.replace(month=1, day=1),  # Inicio de año
                vigencia_hasta=hoy.replace(month=12, day=31),  # Fin de año
                activo=True,
                observacion='Seed automático para pruebas de compra rápida',
            ))
            n_inserted += 1
        session.commit()
        print(f'\n✅ {n_inserted} descuentos base insertados.')

        # ── Mínimos de compra ────────────────────────────────────────────
        for razon, monto in MINIMOS_DROGUERIA:
            drog = session.query(Provider).filter(
                Provider.razon_social.ilike(razon)).first()
            if drog:
                drog.compra_minima_pesos = monto
                print(f'  💰 {razon}: mínimo ${monto:,.0f} configurado')
        session.commit()

        # ── Resumen ──────────────────────────────────────────────────────
        print('\n📊 MATRIZ DE DESCUENTOS BASE INSERTADA:')
        print(f'   {"Lab":<20}{"Droguería":<35}{"Dto%":>8}{"Plazo":>15}')
        print(f'   {"-"*78}')
        rows = (session.query(DescuentoBase, Laboratorio, Provider)
                .join(Laboratorio, DescuentoBase.laboratorio_id == Laboratorio.id)
                .join(Provider, DescuentoBase.drogueria_id == Provider.id)
                .order_by(Laboratorio.nombre, Provider.razon_social).all())
        for db_, lab, drog in rows:
            print(f'   {lab.nombre:<20}{drog.razon_social:<35}'
                  f'{float(db_.descuento_pct):>7.2f}%{db_.plazo_pago or "":>15}')

        print(f'\n📌 Mínimos de compra:')
        for prov in session.query(Provider).filter(
                Provider.compra_minima_pesos.isnot(None)).all():
            print(f'   {prov.razon_social}: ${float(prov.compra_minima_pesos):,.0f}')


if __name__ == '__main__':
    seed()
