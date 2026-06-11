r"""Filtro droguería — variante por archivo subido.

Alternativa al `/filtro_drogueria` que lee de ObServer SQL (vista DW.Pedidos).
Acá el operador sube el archivo que ObServer ya generó en el share
`\\server-1\ObServerGestion\Pedidos\<droguería>\`, y la app lo parsea +
separa los renglones por droguería destino según la matriz lab×droguería.

Pensado para farmacias donde el schema DW no expone `DW.Pedidos` (caso
Badia, jun-2026). La ruta SQL sigue intacta para las que SÍ lo tienen
(caso Pieri).

Formato 20 de Junio (.txt) — pipe-delimited:
    C|<razon_social farmacia>|<codcli>|<CUIT_drogueria>|<DDMMYYYY>|<n°>|
    D|<EAN>|<cantidad>|<descripcion>|<alfabeta>|
    F|<total>|

Formato Kellerhoff (.PED) — fixed-width 39 chars:
    cant(5)  alfabeta(10)  bloque(24)=[troquel+EAN+"0"]

Regla de oro: el código (EAN/alfabeta/troquel) del archivo se preserva
tal cual en el output. Cero descarte de renglones. Si no resuelvo el lab
de un renglón, queda en la droguería origen del archivo.
"""
import base64
from datetime import datetime

from flask import jsonify, render_template, request
from flask_login import login_required

import database
from database import (
    KellerhoffCatalogo, Laboratorio, LaboratorioDrogueria, Producto, Provider,
    get_db,
)
from helpers import drogueria_defaults, get_config

# Gating: el sistema de perfiles (auth.PERFILES['filtro_drogueria'].prefijos)
# valida que el operador tenga el perfil al matchear el prefix /filtro-drogueria/.


# ── Parsers ──────────────────────────────────────────────────────────────────

def _parse_20j(text):
    """Parsea archivo .txt de 20 de Junio (pipe-delimited)."""
    cabecera = {}
    items = []
    total = None
    for raw in text.splitlines():
        line = raw.rstrip('\r\n')
        if not line:
            continue
        parts = [p.strip() for p in line.split('|')]
        kind = parts[0] if parts else ''
        if kind == 'C' and len(parts) >= 6:
            cabecera = {
                'farmacia': parts[1],
                'codcli':   parts[2],
                'cuit_drogueria': parts[3],
                'fecha':    parts[4],
                'numero':   parts[5],
            }
        elif kind == 'D' and len(parts) >= 5:
            try:
                cant = int(parts[2]) if parts[2] else 0
            except ValueError:
                cant = 0
            # OJO: la 5ta columna del .txt 20jun es TROQUEL (no alfabeta).
            # ObServer lo guarda en DW.Productos.Troquel; el alfabeta vive
            # aparte (DW.Productos.CodigoAlfabeta) y no viene en este archivo.
            items.append({
                'ean':         parts[1],
                'cantidad':    cant,
                'descripcion': parts[3],
                'alfabeta':    '',
                'troquel':     parts[4],
            })
        elif kind == 'F' and len(parts) >= 2:
            try:
                total = int(parts[1]) if parts[1] else 0
            except ValueError:
                total = None
    return {'origen': '20 de Junio',
            'formato': 'txt20j',
            'cabecera': cabecera,
            'total_declarado': total,
            'items': items}


def _parse_ped(text):
    """Parsea archivo .PED de Kellerhoff (fixed-width 39 chars).

    Layout: cant(5) alfabeta(10) [troquel+EAN(13)+"0"](24).
    Los últimos 14 chars del bloque son EAN(13) + literal '0'; el resto
    es troquel padeado a la izquierda.
    """
    items = []
    for raw in text.splitlines():
        line = raw.rstrip('\r\n')
        if len(line) < 39:
            continue
        try:
            cant = int(line[0:5].strip() or '0')
        except ValueError:
            cant = 0
        alfabeta = line[5:15].strip()
        bloque = line[15:39]
        ean_raw = bloque[-14:-1].strip() if len(bloque) >= 14 else ''
        ean = ean_raw if ean_raw.isdigit() else ''
        troquel = bloque[:len(bloque) - 14].strip()
        items.append({
            'cantidad':    cant,
            'alfabeta':    alfabeta,
            'ean':         ean,
            'troquel':     troquel,
            'descripcion': '',
        })
    return {'origen': 'Kellerhoff',
            'formato': 'ped',
            'cabecera': {},
            'total_declarado': None,
            'items': items}


def _detectar_y_parsear(filename, content_bytes):
    """Detecta formato por extensión + contenido y delega al parser."""
    try:
        text = content_bytes.decode('utf-8')
    except UnicodeDecodeError:
        text = content_bytes.decode('latin-1', errors='replace')

    name_lower = (filename or '').lower()
    primera = next((ln for ln in text.splitlines() if ln.strip()), '')

    if primera.startswith('C|') or name_lower.endswith('.txt'):
        return _parse_20j(text)
    if name_lower.endswith('.ped') or len(primera) == 39:
        return _parse_ped(text)
    return None


# ── Resolución de laboratorio ────────────────────────────────────────────────

def _resolver_labs(items, session):
    """Devuelve f(item) -> laboratorio_id_local (o None).

    Fuente única: DW.Productos en ObServer, llave Troquel. Es la llave que
    está en los dos formatos (.txt 20jun en la col 5, .PED Kellerhoff en el
    bloque). Una sola query a ObServer + mapeo IdLaboratorio → local por
    Laboratorio.observer_id. Sin joins múltiples ni catálogos intermedios.
    """
    troqs = {it['troquel'].strip() for it in items
             if it.get('troquel') and it['troquel'].strip()}
    if not troqs:
        return lambda it: None

    # Una query a ObServer: troquel → IdLaboratorio (observer).
    lab_obs_por_troq = {}
    try:
        import observer_source
        conn = observer_source._connect(timeout=15)
        if conn is not None:
            try:
                with conn.cursor(as_dict=True) as cur:
                    # Troquel en DW.Productos es int; comparo con strings numéricos.
                    troqs_num = [int(t) for t in troqs if t.isdigit()]
                    if troqs_num:
                        inlist = ','.join(str(t) for t in troqs_num)
                        cur.execute(
                            f'SELECT DISTINCT Troquel, MIN(IdLaboratorio) AS IdLab '
                            f'FROM DW.Productos '
                            f'WHERE Troquel IN ({inlist}) AND IdLaboratorio IS NOT NULL '
                            f'GROUP BY Troquel')
                        for r in cur.fetchall():
                            lab_obs_por_troq[str(r['Troquel'])] = r['IdLab']
            finally:
                conn.close()
    except Exception:
        pass  # fallback silencioso: lo que no resuelva queda en origen

    # Mapeo IdLaboratorio observer → Laboratorio.id local.
    obs_ids = set(lab_obs_por_troq.values())
    local_por_obs = {}
    if obs_ids:
        rows = (session.query(Laboratorio.observer_id, Laboratorio.id)
                .filter(Laboratorio.observer_id.in_(obs_ids)).all())
        local_por_obs = {obs_id: local_id for obs_id, local_id in rows}

    def _resolve(it):
        troq = (it.get('troquel') or '').strip()
        if not troq:
            return None
        obs_id = lab_obs_por_troq.get(troq)
        return local_por_obs.get(obs_id) if obs_id else None
    return _resolve


# ── Resolución de droguería origen + destino ────────────────────────────────

def _norm_prov(s):
    return ''.join(c.lower() for c in (s or '') if c.isalnum())


def _provider_id_por_nombre(nombre_target, session):
    """Mapea un nombre canónico ('20 de Junio', 'Kellerhoff') al Provider.id local."""
    norm_target = _norm_prov(nombre_target)
    if not norm_target:
        return None
    for d in session.query(Provider).filter(Provider.tipo == 'drogueria'):
        dn = _norm_prov(d.razon_social)
        if dn and (dn in norm_target or norm_target in dn):
            return d.id
    return None


# ── Generadores de archivo (output) ──────────────────────────────────────────

def _fmt_txt20j(items, codcli, fecha_ddmmaaaa, numero, farm_nombre, farm_cuit):
    """Genera contenido .txt para 20 de Junio. EAN/alfabeta de cada item van
    tal cual (los del archivo subido o vacío si faltaban)."""
    lines = [
        f'C|{farm_nombre:<40}|{codcli:<10}|{farm_cuit:<11}|'
        f'{fecha_ddmmaaaa}|{numero:<10}|'
    ]
    for it in items:
        ean = (it.get('ean') or '')[:13]
        desc = (it.get('descripcion') or '')[:60]
        alfa = it.get('alfabeta') or ''
        cant = it.get('cantidad', 0)
        lines.append(f'D|{ean:<13}|{cant:>6}|{desc:<60}|{alfa:<60}|')
    total = sum(int(it.get('cantidad') or 0) for it in items)
    lines.append(f'F|{str(total):<11}|')
    return ''.join(l + '\r\n' for l in lines)


def _fmt_ped(items):
    """Genera contenido .PED Kellerhoff (fixed-width 39 chars)."""
    lines = []
    for it in items:
        cant = it.get('cantidad', 0)
        alfa = it.get('alfabeta') or ''
        troq = it.get('troquel') or ''
        ean = it.get('ean') or ''
        col3 = f'{troq}{ean}0' if ean else troq
        lines.append(f'{cant:>5}{alfa:>10}{col3:>24}')
    return ''.join(l + '\r\n' for l in lines)


# ── Separación principal ────────────────────────────────────────────────────

def _separar(items, origen_provider_id, session):
    """Aplica matriz lab→droguería y devuelve {drog_id: [item, ...]} preservando
    todos los items (cero descarte). Items sin lab resuelto van a origen.

    Solo cuenta droguerías con `matriz_visible=True`. Las invisibles en ABM
    matriz (ej. Pharmos/Ciafarma) NO son destinos válidos; los labs que las
    tenían como única opción quedan en origen.
    """
    resolver = _resolver_labs(items, session)
    visibles = {d.id for d in
                session.query(Provider).filter(Provider.tipo == 'drogueria',
                                                Provider.matriz_visible.is_(True))}
    matriz = {}
    for r in session.query(LaboratorioDrogueria).all():
        if r.drogueria_id in visibles:
            matriz.setdefault(r.laboratorio_id, set()).add(r.drogueria_id)

    grupos = {}
    ambiguos = []
    for it in items:
        lab_id = resolver(it)
        it['_lab_resuelto'] = lab_id     # solo para debug/UI
        drogs = matriz.get(lab_id, set()) if lab_id else set()
        if not drogs or origen_provider_id in drogs:
            destino = origen_provider_id
        elif len(drogs) == 1:
            destino = next(iter(drogs))
        else:
            ambiguos.append(it)
            destino = origen_provider_id   # ante duda, queda en origen
        grupos.setdefault(destino, []).append(it)
    return grupos, ambiguos


def _info_drogueria(session, drog_id):
    """Devuelve dict con razon_social, formato, codcli para una droguería."""
    d = session.get(Provider, drog_id)
    if not d:
        return None
    dd = drogueria_defaults(d.razon_social)
    return {
        'razon_social': d.razon_social,
        'codcli': d.codcli or '',
        'formato': d.formato_archivo or dd.get('formato_archivo'),
        'sufijo': d.sufijo or dd.get('sufijo') or '',
    }


# ── Rutas ────────────────────────────────────────────────────────────────────

def init_app(app):

    @app.route('/filtro-drogueria/archivo')
    @app.route('/filtro_drogueria/archivo')
    @login_required
    def filtro_drogueria_archivo():
        return render_template('filtro_drogueria_archivo.html')

    @app.route('/filtro-drogueria/archivo/parsear', methods=['POST'])
    @app.route('/filtro_drogueria/archivo/parsear', methods=['POST'])
    @login_required
    def filtro_drogueria_archivo_parsear():
        """Solo parsea el archivo y devuelve los items (preview)."""
        f = request.files.get('archivo')
        if not f or not (f.filename or '').strip():
            return jsonify({'ok': False, 'error': 'Falta el archivo'}), 400
        content = f.read()
        if not content:
            return jsonify({'ok': False, 'error': 'Archivo vacío'}), 400
        if len(content) > 2 * 1024 * 1024:
            return jsonify({'ok': False, 'error': 'Archivo demasiado grande (>2MB)'}), 400
        resultado = _detectar_y_parsear(f.filename, content)
        if resultado is None:
            return jsonify({'ok': False, 'error':
                            'No reconozco el formato (esperaba .txt de 20 de Junio o .PED de Kellerhoff).'}), 422
        if not resultado['items']:
            return jsonify({'ok': False, 'error': 'El archivo no tiene renglones de productos.'}), 422
        return jsonify({'ok': True, 'filename': f.filename, **resultado})

    @app.route('/filtro-drogueria/archivo/separar', methods=['POST'])
    @app.route('/filtro_drogueria/archivo/separar', methods=['POST'])
    @login_required
    def filtro_drogueria_archivo_separar():
        """Parsea + separa por droguería destino + genera contenido de cada archivo.

        Devuelve grupos = [{drog_id, drogueria, es_origen, formato, filename,
                            n_items, content_b64, items_preview, sin_lab}].
        """
        f = request.files.get('archivo')
        if not f or not (f.filename or '').strip():
            return jsonify({'ok': False, 'error': 'Falta el archivo'}), 400
        content = f.read()
        if not content:
            return jsonify({'ok': False, 'error': 'Archivo vacío'}), 400
        if len(content) > 2 * 1024 * 1024:
            return jsonify({'ok': False, 'error': 'Archivo demasiado grande (>2MB)'}), 400
        parsed = _detectar_y_parsear(f.filename, content)
        if parsed is None:
            return jsonify({'ok': False, 'error':
                            'No reconozco el formato (esperaba .txt o .PED).'}), 422
        if not parsed['items']:
            return jsonify({'ok': False, 'error': 'El archivo no tiene renglones.'}), 422

        with get_db() as session:
            origen_id = _provider_id_por_nombre(parsed['origen'], session)
            if origen_id is None:
                return jsonify({'ok': False, 'error':
                                f"No tengo cargada la droguería '{parsed['origen']}'. "
                                f"Configurala en /providers?tipo=drogueria"}), 422

            grupos, ambiguos = _separar(parsed['items'], origen_id, session)

            # Datos de farmacia + drogerías + comprobante
            cfg = get_config()
            farm_nombre = cfg.get('farmacia_nombre') or 'Farmacia'
            farm_cuit = cfg.get('farmacia_cuit') or ''
            cab = parsed.get('cabecera') or {}
            num_pedido = cab.get('numero') or ''
            fecha_dd = cab.get('fecha') or datetime.now().strftime('%d%m%Y')

            out_groups = []
            for drog_id in sorted(grupos.keys(),
                                  key=lambda d: (d != origen_id, d)):
                its = grupos[drog_id]
                info = _info_drogueria(session, drog_id)
                if not info:
                    out_groups.append({
                        'drog_id': drog_id, 'drogueria': f'Drog #{drog_id}',
                        'es_origen': drog_id == origen_id,
                        'formato': None, 'filename': None, 'content_b64': None,
                        'n_items': len(its), 'sin_lab': len(its),
                        'aviso': 'Droguería no encontrada.',
                        'items_preview': _items_preview_dict(its),
                    })
                    continue
                sin_lab = sum(1 for it in its if not it.get('_lab_resuelto'))
                if not info['formato']:
                    out_groups.append({
                        'drog_id': drog_id, 'drogueria': info['razon_social'],
                        'es_origen': drog_id == origen_id,
                        'formato': None, 'filename': None, 'content_b64': None,
                        'n_items': len(its), 'sin_lab': sin_lab,
                        'aviso': 'Droguería sin formato de archivo configurado.',
                        'items_preview': _items_preview_dict(its),
                    })
                    continue
                # Generar contenido según formato destino
                if info['formato'] == 'ped':
                    content_str = _fmt_ped(its)
                    filename = (f'S{info["codcli"]}{(num_pedido or "0")[-3:]}'
                                f'{info["sufijo"]}.PED')
                else:  # txt20j
                    comp = f'{num_pedido}{info["sufijo"]}'
                    content_str = _fmt_txt20j(its, info['codcli'], fecha_dd,
                                              comp, farm_nombre, farm_cuit)
                    filename = f'2{fecha_dd}{comp}.txt'
                out_groups.append({
                    'drog_id': drog_id, 'drogueria': info['razon_social'],
                    'es_origen': drog_id == origen_id,
                    'formato': info['formato'], 'filename': filename,
                    'content_b64': base64.b64encode(
                        content_str.encode('latin-1', errors='replace')).decode('ascii'),
                    'n_items': len(its), 'sin_lab': sin_lab,
                    'aviso': None,
                    'items_preview': _items_preview_dict(its),
                })

        return jsonify({
            'ok': True,
            'filename_original': f.filename,
            'origen': parsed['origen'],
            'cabecera': parsed.get('cabecera') or {},
            'total_items': len(parsed['items']),
            'ambiguos': len(ambiguos),
            'groups': out_groups,
        })


def _items_preview_dict(items):
    """Convierte items internos a dicts JSON-safe para la UI."""
    return [{
        'cantidad': it.get('cantidad', 0),
        'ean': it.get('ean', ''),
        'alfabeta': it.get('alfabeta', ''),
        'troquel': it.get('troquel', ''),
        'descripcion': it.get('descripcion', ''),
        'lab_resuelto': it.get('_lab_resuelto'),
    } for it in items]
