"""Seed completo de datos demo para probar /compras/rapido con 5 laboratorios.

Genera datos sintéticos consistentes con TODAS las tablas que el flujo de
compra rápida lee:
  - Laboratorio + ObsLaboratorio (5 labs)
  - ObsProducto (15 productos por lab, 75 total) con monodroga y EAN
  - ObsCodigoBarras (EAN ↔ observer_id)
  - ObsStock (con varios bajo mínimo para que aparezcan en la pantalla)
  - ObsVentaMensual (12 meses con estacionalidad para cálculo de u3m/u12m)
  - Provider (3 droguerías)
  - DescuentoBase (matriz lab × droguería con descuentos variados)
  - OfertaMinimo (transfers / ofertas con mínimo de muestra)

ID_FARMACIA: 1 (la farmacia única que usa el sistema hoy).
EANs: prefijo 9999 para no chocar con datos reales.
observer_ids: rango 990000+ para no chocar con sync de ObServer real.

Idempotente: usa upserts por (laboratorio.nombre, observer_id, ean). Re-correr
no duplica registros, solo refresca campos.

Uso:
    docker exec appfarmweb-web-1 sh -c "cd /app && python -m scripts.seed_demo_5_labs"

Para limpiar todo lo demo y recrear:
    docker exec appfarmweb-web-1 sh -c "cd /app && python -m scripts.seed_demo_5_labs --reset"
"""
import argparse
import os
import random
from datetime import date, datetime, timedelta
from decimal import Decimal

import database
from database import (
    DescuentoBase,
    Laboratorio,
    ObsCodigoBarras,
    ObsLaboratorio,
    ObsProducto,
    ObsStock,
    ObsVentaMensual,
    OfertaMinimo,
    Producto,
    Provider,
    get_db,
    init_db,
    now_ar,
)

# El sistema usa OBSERVER_ID_FARMACIA (default 10525) en /compras/rapido y
# en todos los queries de obs_stock / obs_ventas_mensuales. El seed tiene
# que poblar exactamente esa farmacia para que los datos aparezcan.
ID_FARMACIA = int(os.environ.get('OBSERVER_ID_FARMACIA', '10525'))
RNG_SEED = 42  # determinístico
OBS_LAB_BASE = 990000   # observer_ids para los labs demo
OBS_PROD_BASE = 990000  # observer_ids para los productos demo
EAN_PREFIX = '9999'

# ── 5 laboratorios + portfolio de productos ──────────────────────────────────
# Cada lab con productos representativos y monodrogas reales.
LABS_DATA = {
    'ROEMMERS DEMO': {
        'productos': [
            ('AMOXIDAL 500 MG COMP X 16',     'AMOXICILINA',                'L', 8500),
            ('AMOXIDAL DUO 875 COMP X 14',    'AMOXICILINA',                'L', 9200),
            ('LOTRIAL 10 MG COMP X 30',       'ENALAPRIL',                  'R', 4500),
            ('LOTRIAL 20 MG COMP X 30',       'ENALAPRIL',                  'R', 5800),
            ('SERTAL COMP X 20',              'PROPINOX+CLONIXINATO',       'L', 3200),
            ('SERTAL COMP X 40',              'PROPINOX+CLONIXINATO',       'L', 5800),
            ('AMOIXIDAL DUO SUSP X 70 ML',    'AMOXICILINA',                'L', 6500),
            ('TAFIROL 500 MG COMP X 16',      'PARACETAMOL',                'L', 2100),
            ('CIRIAX 500 MG COMP X 10',       'CIPROFLOXACINA',             'R', 7800),
            ('CIRIAX OTIC L GTAS X 5 ML',     'CIPROFLOXACINA',             'R', 5400),
            ('REVOTRIDE 50 MG COMP X 30',     'TRAMADOL+PARACETAMOL',       '1', 4900),
            ('REVOTRIDE 100 MG COMP X 30',    'TRAMADOL+PARACETAMOL',       '1', 6800),
            ('PROVIATE 5 MG COMP X 30',       'BISOPROLOL',                 'R', 4200),
            ('PROVIATE 10 MG COMP X 30',      'BISOPROLOL',                 'R', 5500),
            ('NIMOTOP 30 MG COMP X 30',       'NIMODIPINA',                 'R', 8900),
        ],
    },
    'BAGO DEMO': {
        'productos': [
            ('TAFLOTAN 0.0015% GTS X 2.5 ML', 'TAFLUPROST',                 'R',12500),
            ('TENSOPRIL 10 MG COMP X 40',     'ENALAPRIL',                  'R', 4800),
            ('TENSOPRIL 20 MG COMP X 40',     'ENALAPRIL',                  'R', 6100),
            ('VEKLURY 100 MG VIAL',           'REMDESIVIR',                 'R',45000),
            ('FOSFOMICINA 3 G SOBRES X 1',    'FOSFOMICINA',                'R', 5400),
            ('PRAVATOR 20 MG COMP X 30',      'PRAVASTATINA',               'R', 7200),
            ('PRAVATOR 40 MG COMP X 30',      'PRAVASTATINA',               'R', 8900),
            ('SOMAZINA 500 MG COMP X 20',     'CITICOLINA',                 'R', 9800),
            ('CALMEX 750 MG COMP X 20',       'METOCARBAMOL',               'L', 4200),
            ('CALMEX FLEX COMP X 30',         'METOCARBAMOL+IBUPROFENO',    'L', 5800),
            ('NEUROEPO 4000 UI X 1 AMP',      'ERITROPOYETINA',             'R',18500),
            ('CASCAFEN 500 MG COMP X 20',     'NAPROXENO',                  'L', 3400),
            ('TRIBASE 1 MG COMP X 30',        'CLONAZEPAM',                 '1', 4100),
            ('SYNALAR CR X 30 G',             'FLUOCINOLONA',               'R', 4500),
            ('GLIDIPID 5 MG COMP X 30',       'GLIPIZIDA',                  'R', 5200),
        ],
    },
    'BAYER DEMO': {
        'productos': [
            ('ASPIRINA 500 MG COMP X 20',     'ACIDO ACETILSALICILICO',     'L', 1900),
            ('ASPIRINA PREVENT 100 X 28',     'ACIDO ACETILSALICILICO',     'L', 2800),
            ('CARDIOASPIRINA 100 MG X 90',    'ACIDO ACETILSALICILICO',     'R', 4900),
            ('REDOXON 500 MG EFE X 10',       'VITAMINA C',                 'L', 2400),
            ('SUPRADYN ACTIVO COMP X 30',     'MULTIVITAMINICO',            'L', 6800),
            ('BEROCCA PERFORM EFE X 30',      'COMPLEJO B+C',               'L', 7100),
            ('CIPROFLOXACINA 500 X 10',       'CIPROFLOXACINA',             'R', 6500),
            ('GLUCOBAY 50 MG COMP X 30',      'ACARBOSA',                   'R', 8200),
            ('XARELTO 20 MG COMP X 28',       'RIVAROXABAN',                'R',95000),
            ('YASMIN COMP X 21',              'DROSPIRENONA+ETINILESTRADIOL','R', 7800),
            ('YAZ COMP X 28',                 'DROSPIRENONA+ETINILESTRADIOL','R', 8400),
            ('ADALAT OROS 30 MG X 30',        'NIFEDIPINA',                 'R', 9200),
            ('CIPROXINA 500 MG X 14',         'CIPROFLOXACINA',             'R', 7400),
            ('CANESTEN CR X 20 G',            'CLOTRIMAZOL',                'L', 3100),
            ('ALEVE LIQUID GELS COMP X 12',   'NAPROXENO SODICO',           'L', 4500),
        ],
    },
    'BALIARDA DEMO': {
        'productos': [
            ('AUDIPAX 16 MG COMP X 30',       'BETAHISTINA',                'R', 5800),
            ('AUDIPAX MULTIDOSIS 24 MG X 30', 'BETAHISTINA',                'R', 6500),
            ('AXEPIN 2.5 MG COMP X 60',       'OLANZAPINA',                 'R', 8900),
            ('AXEPIN 5 MG COMP X 60',         'OLANZAPINA',                 'R',12400),
            ('AZIBIOTIC 500 MG COM X 7',      'AZITROMICINA',               'R', 5200),
            ('AZIBIOTIC 500 MG COM X 5',      'AZITROMICINA',               'R', 4100),
            ('BALIGLUC 500 MG COM X 30',      'METFORMINA',                 'R', 4800),
            ('BALIGLUC AP 500 MG COM X 30',   'METFORMINA',                 'R', 5500),
            ('BALIGLUC AP 850 MG COM X 30',   'METFORMINA',                 'R', 6200),
            ('BALIGLUC AP 1000 MG COM X 30',  'METFORMINA',                 'R', 6900),
            ('BIATRIX 100 MG COM X 30',       'METOPROLOL',                 'R', 4500),
            ('BIATRIX 200 MG COM X 30',       'METOPROLOL',                 'R', 5800),
            ('CLARIBIOTIC 125 MG SUS X 60ML', 'CLARITROMICINA',             'R', 6800),
            ('CLARIBIOTIC 250 MG SUS X 60ML', 'CLARITROMICINA',             'R', 8400),
            ('PANTUS 20 MG COMP X 14',        'PANTOPRAZOL',                'R', 5100),
        ],
    },
    'GLAXO DEMO': {
        'productos': [
            ('ZYLORIC 100 MG COMP X 30',      'ALOPURINOL',                 'R', 4800),
            ('ZYLORIC 300 MG COMP X 30',      'ALOPURINOL',                 'R', 6200),
            ('AUGMENTIN BID 1G COMP X 14',    'AMOXICILINA+ACIDO CLAVULAN', 'R',12500),
            ('AUGMENTIN ES SUS X 70 ML',      'AMOXICILINA+ACIDO CLAVULAN', 'R', 9800),
            ('VENTOLIN INH 100 MCG X 200 D',  'SALBUTAMOL',                 'R', 7200),
            ('VENTOLIN JBE X 120 ML',         'SALBUTAMOL',                 'R', 4500),
            ('SERETIDE DISKUS 50/250 X 60',   'SALMETEROL+FLUTICASONA',     'R',32000),
            ('SERETIDE DISKUS 50/500 X 60',   'SALMETEROL+FLUTICASONA',     'R',38000),
            ('AVAMYS SPRAY 27.5 MCG X 120 D', 'FLUTICASONA',                'R',12400),
            ('FLIXOTIDE 250 MCG INH X 120 D', 'FLUTICASONA',                'R', 9800),
            ('LAMICTAL 100 MG COMP X 30',     'LAMOTRIGINA',                'R', 8900),
            ('LAMICTAL 200 MG COMP X 30',     'LAMOTRIGINA',                'R',12400),
            ('VOLTAREN EMULGEL X 100 G',      'DICLOFENAC',                 'L', 6800),
            ('PHYSIOTENS 0.4 MG COMP X 28',   'MOXONIDINA',                 'R',11200),
            ('AVODART 0.5 MG CAP X 30',       'DUTASTERIDA',                'R',16800),
        ],
    },
}

# ── Droguerías ──────────────────────────────────────────────────────────────
DROGUERIAS_DATA = [
    {'razon_social': 'DROGUERÍA KELLERHOFF S.A.', 'cuit': '30-50001234-9', 'compra_minima_pesos': None,    'tipo': 'drogueria'},
    {'razon_social': 'PHARMOS S.A.',               'cuit': '30-64266156-2', 'compra_minima_pesos': 50000,   'tipo': 'drogueria'},
    {'razon_social': 'VIA PHARMA',                 'cuit': '30-71234567-1', 'compra_minima_pesos': 80000,   'tipo': 'drogueria'},
]

# Matriz descuento_base lab × droguería. Diferencias intencionales para que
# /compras/rapido tenga que elegir la mejor droguería por producto.
DESCUENTOS_BASE_MATRIX = {
    # (lab, drog) → (pct, plazo)
    ('ROEMMERS DEMO', 'DROGUERÍA KELLERHOFF S.A.'): (30.00, '30 dias'),
    ('ROEMMERS DEMO', 'PHARMOS S.A.'):              (28.50, '45 dias'),
    ('ROEMMERS DEMO', 'VIA PHARMA'):                (24.00, '45 dias'),
    ('BAGO DEMO',     'DROGUERÍA KELLERHOFF S.A.'): (26.00, '30 dias'),
    ('BAGO DEMO',     'PHARMOS S.A.'):              (28.00, '30 dias'),
    ('BAGO DEMO',     'VIA PHARMA'):                (22.00, '45 dias'),
    ('BAYER DEMO',    'DROGUERÍA KELLERHOFF S.A.'): (31.03, '30 dias'),
    ('BAYER DEMO',    'PHARMOS S.A.'):              (29.50, '30 dias'),
    ('BAYER DEMO',    'VIA PHARMA'):                (25.50, '45 dias'),
    ('BALIARDA DEMO', 'DROGUERÍA KELLERHOFF S.A.'): (31.03, '30 dias'),
    ('BALIARDA DEMO', 'PHARMOS S.A.'):              (28.00, '45 dias'),
    ('BALIARDA DEMO', 'VIA PHARMA'):                (25.00, '45 dias'),
    ('GLAXO DEMO',    'DROGUERÍA KELLERHOFF S.A.'): (27.50, '30 dias'),
    ('GLAXO DEMO',    'PHARMOS S.A.'):              (26.00, '60 dias'),
    ('GLAXO DEMO',    'VIA PHARMA'):                (23.00, '45 dias'),
}


def gen_ean(lab_idx, prod_idx):
    """EAN sintético determinístico de 13 dígitos: 9999<lab2><prod3><checksum?>.
    No cumple checksum EAN-13 real (no nos importa), pero es único por par."""
    return f'{EAN_PREFIX}{lab_idx:02d}{prod_idx:03d}0000'[:13]


def upsert_lab(session, nombre, obs_id):
    """Crea Laboratorio + ObsLaboratorio + linkea observer_id."""
    obs_lab = session.get(ObsLaboratorio, obs_id)
    if not obs_lab:
        session.add(ObsLaboratorio(observer_id=obs_id, descripcion=nombre))
    lab = session.query(Laboratorio).filter_by(nombre=nombre).first()
    if not lab:
        lab = Laboratorio(nombre=nombre, observer_id=obs_id, activo=True)
        session.add(lab)
    elif lab.observer_id != obs_id:
        lab.observer_id = obs_id
    session.flush()
    return lab


def upsert_obs_producto(session, obs_id, descripcion, lab_obs_id, monodroga_obs_id, tvc, ean):
    """Upsert ObsProducto + ObsCodigoBarras."""
    p = session.get(ObsProducto, obs_id)
    if not p:
        p = ObsProducto(
            observer_id=obs_id,
            descripcion=descripcion,
            laboratorio_observer=lab_obs_id,
            nombre_droga_observer=monodroga_obs_id,
            id_tipo_venta_control=tvc,
            es_habilitado_venta=True,
            fecha_baja=None,
        )
        session.add(p)
        # Flush ANTES de agregar el ObsCodigoBarras (FK a obs_productos):
        # SQLAlchemy unit-of-work no siempre resuelve el orden por FK con
        # batch inserts, y la FK falla si el código se inserta primero.
        session.flush()
    else:
        p.descripcion = descripcion
        p.laboratorio_observer = lab_obs_id
        p.nombre_droga_observer = monodroga_obs_id
        p.id_tipo_venta_control = tvc
        p.fecha_baja = None
    # EAN
    cb = (session.query(ObsCodigoBarras)
          .filter_by(producto_observer=obs_id, codigo_barras=ean).first())
    if not cb:
        # Generar id_codigo_barras único — usamos obs_id * 10 + 1
        existing_id = obs_id * 10 + 1
        while session.get(ObsCodigoBarras, existing_id):
            existing_id += 1
        session.add(ObsCodigoBarras(
            id_codigo_barras=existing_id,
            producto_observer=obs_id,
            codigo_barras=ean,
            orden=1,
            fecha_baja=None,
        ))
    return p


def upsert_stock(session, obs_id, stock_actual, minimo, maximo):
    s = (session.query(ObsStock)
         .filter_by(id_farmacia=ID_FARMACIA, producto_observer=obs_id).first())
    if not s:
        s = ObsStock(id_farmacia=ID_FARMACIA, producto_observer=obs_id,
                     stock_actual=stock_actual, minimo=minimo, maximo=maximo)
        session.add(s)
    else:
        s.stock_actual = stock_actual
        s.minimo = minimo
        s.maximo = maximo


def upsert_venta(session, obs_id, anio, mes, unidades, precio_unit):
    monto = float(unidades) * float(precio_unit)
    v = (session.query(ObsVentaMensual)
         .filter_by(id_farmacia=ID_FARMACIA, producto_observer=obs_id,
                    anio=anio, mes=mes).first())
    if not v:
        v = ObsVentaMensual(
            id_farmacia=ID_FARMACIA, producto_observer=obs_id,
            anio=anio, mes=mes, unidades=unidades, monto=monto,
            transacciones=max(1, int(unidades) // 2),
        )
        session.add(v)
    else:
        v.unidades = unidades
        v.monto = monto


def upsert_drogueria(session, data):
    p = session.query(Provider).filter_by(razon_social=data['razon_social']).first()
    if not p:
        p = Provider(
            razon_social=data['razon_social'],
            cuit=data['cuit'],
            tipo=data['tipo'],
            activo=True,
            compra_minima_pesos=data['compra_minima_pesos'],
        )
        session.add(p)
    else:
        p.compra_minima_pesos = data['compra_minima_pesos']
    session.flush()
    return p


def upsert_descuento_base(session, lab_id, drog_id, pct, plazo):
    d = (session.query(DescuentoBase)
         .filter_by(laboratorio_id=lab_id, drogueria_id=drog_id).first())
    if not d:
        d = DescuentoBase(
            laboratorio_id=lab_id, drogueria_id=drog_id,
            descuento_pct=Decimal(str(pct)), plazo_pago=plazo, activo=True,
            vigencia_desde=date.today() - timedelta(days=30),
            vigencia_hasta=date.today() + timedelta(days=180),
        )
        session.add(d)
    else:
        d.descuento_pct = Decimal(str(pct))
        d.plazo_pago = plazo
        d.activo = True


def gen_ventas_realistas(rng, base_unidades, meses_atras=12):
    """Devuelve [(anio, mes, unidades), ...] simulando estacionalidad.
    base_unidades = unidades promedio por mes para este producto."""
    hoy = date.today()
    out = []
    y, m = hoy.year, hoy.month
    for i in range(meses_atras):
        # Variación ±30% para simular ruido
        factor = rng.uniform(0.7, 1.3)
        # Estacionalidad invierno (jun-ago) +20% para antibióticos
        if m in (6, 7, 8):
            factor *= 1.2
        u = max(0, int(base_unidades * factor))
        out.append((y, m, u))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


def seed_5_labs(reset=False):
    init_db()
    rng = random.Random(RNG_SEED)

    with get_db() as session:
        if reset:
            # Borrar solo registros DEMO (los que tienen observer_id en rango).
            print('🗑  Limpiando datos demo previos...')
            session.query(OfertaMinimo).filter(
                OfertaMinimo.descripcion.like('%DEMO%')).delete(synchronize_session=False)
            session.query(DescuentoBase).filter(
                DescuentoBase.laboratorio_id.in_(
                    session.query(Laboratorio.id).filter(Laboratorio.nombre.like('% DEMO'))
                )).delete(synchronize_session=False)
            session.query(ObsVentaMensual).filter(
                ObsVentaMensual.producto_observer >= OBS_PROD_BASE).delete(synchronize_session=False)
            session.query(ObsStock).filter(
                ObsStock.producto_observer >= OBS_PROD_BASE).delete(synchronize_session=False)
            session.query(ObsCodigoBarras).filter(
                ObsCodigoBarras.producto_observer >= OBS_PROD_BASE).delete(synchronize_session=False)
            session.query(ObsProducto).filter(
                ObsProducto.observer_id >= OBS_PROD_BASE).delete(synchronize_session=False)
            session.query(Laboratorio).filter(
                Laboratorio.nombre.like('% DEMO')).delete(synchronize_session=False)
            session.query(ObsLaboratorio).filter(
                ObsLaboratorio.observer_id >= OBS_LAB_BASE).delete(synchronize_session=False)
            session.commit()

        # ── 1) Labs ──
        print('\n📦 Creando 5 laboratorios...')
        lab_ids = {}
        lab_obs_ids = {}
        for i, (lab_name, _) in enumerate(LABS_DATA.items()):
            obs_id = OBS_LAB_BASE + i
            lab = upsert_lab(session, lab_name, obs_id)
            lab_ids[lab_name] = lab.id
            lab_obs_ids[lab_name] = obs_id
            print(f'   ✓ {lab_name} (lab_id={lab.id}, obs={obs_id})')
        session.commit()

        # ── 2) Productos + EAN ──
        print('\n💊 Creando productos por laboratorio...')
        prod_obs_ids = []   # [(obs_id, lab_name, prod_idx, descripcion, precio_pvp)]
        for lab_idx, (lab_name, info) in enumerate(LABS_DATA.items()):
            for prod_idx, (descr, monodroga, tvc, precio) in enumerate(info['productos']):
                obs_id = OBS_PROD_BASE + lab_idx * 100 + prod_idx
                ean = gen_ean(lab_idx, prod_idx)
                upsert_obs_producto(
                    session, obs_id, descr,
                    lab_obs_ids[lab_name], None, tvc, ean,
                )
                # También Producto local para que aparezca en /productos
                p_local = session.query(Producto).filter_by(codigo_barra=ean).first()
                if not p_local:
                    session.add(Producto(
                        codigo_barra=ean, descripcion=descr,
                        precio_pvp=Decimal(str(precio)),
                        laboratorio_id=lab_ids[lab_name],
                        actualizado_en=now_ar(),
                    ))
                else:
                    p_local.descripcion = descr
                    p_local.precio_pvp = Decimal(str(precio))
                    p_local.laboratorio_id = lab_ids[lab_name]
                prod_obs_ids.append((obs_id, lab_name, prod_idx, descr, precio))
            print(f'   ✓ {lab_name}: {len(info["productos"])} productos')
        session.commit()

        # ── 3) Stock + ventas ──
        print('\n📊 Generando stock + 12 meses de ventas...')
        bajo_minimo = 0
        for obs_id, lab_name, prod_idx, descr, precio in prod_obs_ids:
            # Distribución intencional:
            # 40% bajo mínimo (aparece en compra rápida)
            # 30% en mínimo (no aparece pero está cerca)
            # 30% sobrestock
            r = rng.random()
            minimo = rng.choice([5, 10, 15, 20, 30])
            maximo = minimo * 3
            if r < 0.4:
                stock_actual = max(0, minimo - rng.randint(1, minimo))
                bajo_minimo += 1
            elif r < 0.7:
                stock_actual = minimo + rng.randint(0, 5)
            else:
                stock_actual = maximo + rng.randint(0, 20)
            upsert_stock(session, obs_id, stock_actual, minimo, maximo)

            # Ventas: base proporcional al mínimo (más vendido = mayor mín).
            base_u = minimo * rng.uniform(0.8, 1.5)
            for anio, mes, u in gen_ventas_realistas(rng, base_u):
                upsert_venta(session, obs_id, anio, mes, u, precio)
        session.commit()
        print(f'   ✓ {bajo_minimo}/{len(prod_obs_ids)} productos bajo mínimo')

        # ── 4) Droguerías ──
        print('\n🏪 Creando 3 droguerías...')
        drog_ids = {}
        for d in DROGUERIAS_DATA:
            prov = upsert_drogueria(session, d)
            drog_ids[d['razon_social']] = prov.id
            print(f'   ✓ {d["razon_social"]} (id={prov.id}, mín=${d["compra_minima_pesos"] or "—"})')
        session.commit()

        # ── 5) Descuentos base lab × droguería ──
        print('\n💰 Creando matriz descuentos base...')
        for (lab_name, drog_name), (pct, plazo) in DESCUENTOS_BASE_MATRIX.items():
            upsert_descuento_base(
                session, lab_ids[lab_name], drog_ids[drog_name], pct, plazo,
            )
        session.commit()
        print(f'   ✓ {len(DESCUENTOS_BASE_MATRIX)} descuentos base creados')

        # ── 6) Algunas ofertas con mínimo (transfers) ──
        print('\n🎁 Generando ofertas con mínimo de muestra...')
        ofertas_count = 0
        for lab_idx, (lab_name, info) in enumerate(LABS_DATA.items()):
            # 3 ofertas por lab: 1 simple, 2 con mínimo
            picks = rng.sample(range(len(info['productos'])), 3)
            for j, p_idx in enumerate(picks):
                descr, _, _, precio = info['productos'][p_idx]
                obs_id = OBS_PROD_BASE + lab_idx * 100 + p_idx
                ean = gen_ean(lab_idx, p_idx)
                tipo = 'simple' if j == 0 else 'con_minimo'
                um = None if tipo == 'simple' else rng.choice([6, 12, 18, 24])
                dto = rng.choice([15, 18, 20, 22, 25, 28, 30])
                # Una oferta por lab a Kellerhoff, una a Pharmos, una directa
                drog = [drog_ids['DROGUERÍA KELLERHOFF S.A.'],
                        drog_ids['PHARMOS S.A.'],
                        None][j]
                # Chequear si ya existe
                exists = (session.query(OfertaMinimo)
                          .filter_by(laboratorio_id=lab_ids[lab_name],
                                     ean=ean, drogueria_id=drog).first())
                if not exists:
                    session.add(OfertaMinimo(
                        laboratorio_id=lab_ids[lab_name],
                        ean=ean, descripcion=descr + ' DEMO',
                        unidades_minima=um, descuento_psl=Decimal(str(dto)),
                        plazo_pago='30 dias', tipo_descuento=tipo,
                        drogueria_id=drog, activo=True,
                        vigencia_desde=date.today() - timedelta(days=10),
                        vigencia_hasta=date.today() + timedelta(days=60),
                    ))
                    ofertas_count += 1
        session.commit()
        print(f'   ✓ {ofertas_count} ofertas creadas')

        # ── 7) Diagnóstico final para confirmar que /compras/rapido los ve ──
        print('\n🔎 Verificación final:')
        from sqlalchemy import func
        labs_demo_ids = list(lab_ids.values())
        n_descuentos = (session.query(DescuentoBase)
                        .filter(DescuentoBase.laboratorio_id.in_(labs_demo_ids),
                                DescuentoBase.activo == True).count())  # noqa: E712
        print(f'   • DescuentoBase activos para labs DEMO: {n_descuentos}')
        n_stock_bajo = (session.query(ObsStock)
                        .filter(ObsStock.id_farmacia == ID_FARMACIA,
                                ObsStock.producto_observer >= OBS_PROD_BASE,
                                ObsStock.minimo.isnot(None),
                                ObsStock.minimo > 0,
                                ObsStock.stock_actual < ObsStock.minimo).count())
        print(f'   • Productos DEMO bajo mínimo (id_farmacia={ID_FARMACIA}): {n_stock_bajo}')
        n_ventas = (session.query(func.count(ObsVentaMensual.producto_observer.distinct()))
                    .filter(ObsVentaMensual.id_farmacia == ID_FARMACIA,
                            ObsVentaMensual.producto_observer >= OBS_PROD_BASE).scalar())
        print(f'   • Productos DEMO con ventas registradas: {n_ventas}')
        if n_descuentos == 0 or n_stock_bajo == 0:
            print('\n⚠ ALGO NO CARGÓ. Revisá el log arriba — probablemente una excepción silenciosa.')
        else:
            print('\n✅ Seed completo. Probá /compras/rapido con los 5 labs DEMO.')
            print(f'   Ir a: /compras/rapido?labs={",".join(str(i) for i in labs_demo_ids)}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reset', action='store_true',
                        help='Borrar datos demo previos antes de generar')
    args = parser.parse_args()
    seed_5_labs(reset=args.reset)
