"""Datasets de referencia de mercado (posicionamiento por laboratorio).

Datos NO presentes en la DB — vienen de fuentes externas (IQVIA/IMS, CILFA,
comunicación corporativa). Se usan en /informes/cadencias-lab y los informes
de comparación portfolio-líder vs ventas propias.

Estructura pensada para extender a otros labs: agregar entradas en
LABS_REFERENCIA con la misma forma.

Fuentes (Roemmers): ranking IQVIA "5 de los 10 más vendidos del país",
portfolio corporativo, ranking nacional de drogas más recetadas. Snapshot
cargado 2026-05 — actualizar si entran datos nuevos de IQVIA/CILFA.
"""

# ── Roemmers ──────────────────────────────────────────────────────────────
# observer_id del lab en ObsLaboratorio = 152 (Roemmers).

# Marcas del portfolio Roemmers. `top10_nacional=True` para las 5 que están
# entre los 10 medicamentos más vendidos del país (IQVIA).
# `match` = patrón ILIKE para cruzar contra ObsProducto.descripcion.
ROEMMERS_MARCAS = [
    # marca,        molecula,                         indicacion,                    top10, match
    ('Lotrial',     'enalapril',                      'Hipertensión (IECA)',         True,  'LOTRIAL'),
    ('Optamox',     'amoxicilina + ác. clavulánico',  'Antibiótico amplio espectro', True,  'OPTAMOX'),
    ('Amoxidal',    'amoxicilina',                    'Antibiótico (líder histórico)', True, 'AMOXIDAL'),
    ('Sertal',      'propinox (± clonixinato)',       'Antiespasmódico-analgésico',  True,  'SERTAL'),
    ('Losacor',     'losartán',                       'Hipertensión (ARA II)',       True,  'LOSACOR'),
    ('Atlansil',    'amiodarona',                     'Antiarrítmico',               False, 'ATLANSIL'),
    ('Taural',      'ranitidina',                     'Antiulceroso',                False, 'TAURAL'),
    ('Acalix',      'diltiazem',                      'Bloqueante cálcico',          False, 'ACALIX'),
    ('Lanzopral',   'lansoprazol',                    'IBP (gastro)',                False, 'LANZOPRAL'),
    ('Ciriax',      'ciprofloxacina',                 'Antibiótico quinolónico',     False, 'CIRIAX'),
    ('Endial',      'glimepirida',                    'Antidiabético oral',          False, 'ENDIAL'),
    ('Corbis',      'bisoprolol',                     'Betabloqueante',              False, 'CORBIS'),
    ('Plenica',     'pregabalina',                    'Dolor neuropático',           False, 'PLENICA'),
    ('Dorixina',    'clonixinato de lisina',          'AINE (I+D propio Roemmers)',  False, 'DORIXINA'),
]

# Ranking nacional de drogas más recetadas (IQVIA). `ranking` = posición país
# (None si está en el top pero sin posición exacta). `marca_roemmers` = la
# marca con la que Roemmers compite en esa molécula (None si no tiene).
# `lider` = quién domina la categoría a nivel país.
# `match_droga` = patrón ILIKE para cruzar contra ObsNombreDroga.descripcion.
MOLECULAS_LIDERES_NACIONALES = [
    # molecula,                 ranking, marca_roemmers, lider_mercado,   match_droga
    ('levotiroxina',            1,    None,       'Montpellier',          'LEVOTIROXINA'),
    ('enalapril',               3,    'Lotrial',  'Roemmers (Lotrial)',   'ENALAPRIL'),
    ('aspirina',                None, None,       'Bayer/varios',         'ACETIL%SALICILICO'),
    ('alprazolam',              None, None,       'varios',               'ALPRAZOLAM'),
    ('clonazepam',              None, None,       'varios',               'CLONAZEPAM'),
    ('paracetamol',             None, None,       'varios',               'PARACETAMOL'),
    ('metformina',              None, None,       'varios',               'METFORMINA'),
    ('bisoprolol',              None, 'Corbis',   'Roemmers (Corbis)',    'BISOPROLOL'),
    ('losartán',                None, 'Losacor',  'competido',            'LOSARTAN'),
    ('amoxicilina + clavul.',   None, 'Optamox',  'Roemmers (Optamox)',   'AMOXICILINA%CLAVUL'),
    ('pregabalina',             None, 'Plenica',  'en crecimiento',       'PREGABALINA'),
]

# Registro general: lab_observer_id -> dataset.
LABS_REFERENCIA = {
    152: {
        'nombre': 'Roemmers',
        'marcas': ROEMMERS_MARCAS,
        'moleculas_lideres': MOLECULAS_LIDERES_NACIONALES,
        'nota': ('Laboratorio nº1 de Argentina (~13% del mercado). 5 de los 10 '
                 'medicamentos más vendidos del país son Roemmers. Fortaleza en '
                 'cardiología, antibioticoterapia, gastro, dolor y diabetes.'),
    },
}


def referencia_de_lab(lab_observer_id):
    """Devuelve el dataset de referencia para un lab, o None si no hay."""
    return LABS_REFERENCIA.get(lab_observer_id)


def labs_con_referencia():
    """Lista de (observer_id, nombre) de labs con dataset de referencia cargado."""
    return [(lid, d['nombre']) for lid, d in LABS_REFERENCIA.items()]


# ── Gap de marcas con web search ─────────────────────────────────────────────
# Labs habilitados en el informe de gap (los 8 más grandes del mercado AR). Las
# marcas estrella las trae la web search de Claude en runtime (no hay dataset
# curado salvo Roemmers, que igual pasa por el mismo flujo). El observer_id se
# resuelve por NOMBRE contra obs_laboratorios (difiere entre farmacias).
LABS_GAP_WEBSEARCH = ['Roemmers', 'Bagó', 'Elea', 'Gador', 'Bayer',
                      'Montpellier', 'Casasco', 'Raffo']


def labs_gap_disponibles(session):
    """Lista [{'observer_id', 'nombre'}] de los labs de LABS_GAP_WEBSEARCH que
    existen en esta farmacia (resueltos por nombre). Para el dropdown del informe.
    """
    import helpers
    out = []
    for nombre in LABS_GAP_WEBSEARCH:
        oid = helpers.resolver_obs_lab_por_nombre(session, nombre)
        if oid is not None:
            out.append({'observer_id': oid, 'nombre': nombre})
    return out
