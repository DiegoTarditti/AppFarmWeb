"""Filtro droguería: separa un pedido de ObServer por la matriz lab×droguería.

Flujo: el operador elige un pedido (DW.Pedidos) → se lee cada renglón y, según
`LaboratorioDrogueria`, se decide a qué droguería va:

- lab NO está en la matriz                         → queda en la droguería pedida.
- lab en la matriz e INCLUYE la pedida             → queda en la pedida.
- lab en la matriz pero NO incluye la pedida       → va a la(s) otra(s).

Se genera un archivo por droguería en su formato nativo:
- Kellerhoff: `.ped` fixed-width  → `cant(5) alfabeta(10) [troquel+ean+"0"](24)`
- 20 de Junio: `.txt` pipe-delim  → `C|...|` header + `D|ean|cant|desc|troquel|` + `F|n|`

El EAN sale de `kellerhoff_catalogo` (por alfabeta, fallback troquel). La
ESCRITURA de los archivos la hace el browser (File System Access API), que sí
tiene acceso al share `\\pcroman` vía la sesión Windows del operador — el
contenedor no. El server solo genera el contenido.
"""
import re
import unicodedata
from datetime import datetime

from flask import jsonify, render_template, request

import observer_source
from database import KellerhoffCatalogo, Laboratorio, LaboratorioDrogueria, Producto, Provider, get_db
from helpers import drogueria_defaults, get_config

# La identidad de la farmacia (nombre/CUIT) sale de Config (id=1) y la config por
# droguería (codcli/formato/sufijo/carpeta) de cada Provider → funciona en
# cualquier farmacia sin hardcode. Se editan en /settings y /providers.


def _norm(s):
    """Normaliza para matchear nombres: sin tildes, minúsculas, solo alfanum."""
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]', '', s.lower())


def _num_comprob(comprob):
    """'X-0001-00010327' → '10327' (último grupo numérico, sin ceros a la izq)."""
    nums = re.findall(r'\d+', comprob or '')
    if not nums:
        return ''
    return str(int(nums[-1]))


def init_app(app):

    @app.route('/filtro_drogueria')
    @app.route('/filtro-drogueria')
    @app.route('/FILTRO_DROGUERIA')
    @app.route('/FILTRO-DROGUERIA')
    def filtro_drogueria():
        error = None
        pedidos = []
        if not observer_source.observer_disponible():
            error = 'ObServer no está disponible (sin conexión a SQL Server).'
        else:
            try:
                pedidos = observer_source.get_pedidos_recientes(10)
            except Exception as e:  # noqa: BLE001
                error = f'No pude leer pedidos de ObServer: {e}'
        return render_template('filtro_drogueria.html', pedidos=pedidos, error=error)

    @app.route('/filtro_drogueria/generar', methods=['POST'])
    def filtro_drogueria_generar():
        data = request.get_json(silent=True) or {}
        try:
            id_pedido = int(data.get('id_pedido'))
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'Pedido inválido'}), 400

        try:
            items = observer_source.get_pedido_items(id_pedido)
        except Exception as e:  # noqa: BLE001
            return jsonify({'ok': False, 'error': f'ObServer: {e}'}), 502
        if not items:
            return jsonify({'ok': False, 'error': 'El pedido no tiene renglones'}), 404

        comprob = ''
        for it in items:
            if it.get('comprobante'):
                comprob = it['comprobante']
                break
        num = _num_comprob(comprob)
        fecha_ddmmaaaa = datetime.now().strftime('%d%m%Y')

        with get_db() as session:
            # Droguería origen (proveedor del pedido en ObServer → nuestro Provider).
            source_id = _source_provider_id(data.get('proveedor') or '', session)
            if source_id is None:
                return jsonify({'ok': False, 'error':
                                f"No pude mapear la droguería del pedido "
                                f"('{data.get('proveedor')}') a un proveedor nuestro."}), 422

            # Mapas: lab observer→local, matriz lab→drogs, nombres de drog.
            lab_local = {l.observer_id: l.id for l in
                         session.query(Laboratorio).filter(Laboratorio.observer_id.isnot(None))}
            matriz = {}
            for r in session.query(LaboratorioDrogueria).all():
                matriz.setdefault(r.laboratorio_id, set()).add(r.drogueria_id)
            drog_nombre = {d.id: d.razon_social for d in
                           session.query(Provider).filter(Provider.tipo == 'drogueria')}
            # Config del archivo por droguería (antes DROG_CFG hardcodeado).
            # Clave = Provider.id local. El formato/sufijo sale del Provider o, si
            # no está cargado, del default por nombre (Kellerhoff/20 de Junio).
            drog_cfg = {}
            for d in session.query(Provider).filter(Provider.tipo == 'drogueria'):
                dd = drogueria_defaults(d.razon_social)
                fmt = d.formato_archivo or dd.get('formato_archivo')
                if not fmt:
                    continue   # droguería sin formato conocido ni configurado
                drog_cfg[d.id] = {'codcli': d.codcli or '',
                                  'sufijo': d.sufijo or dd.get('sufijo') or '',
                                  'formato': fmt, 'carpeta': d.carpeta_filtro or ''}

            # Clasificación por renglón.
            grupos = {}        # drog_id -> [item]
            ambiguos = []      # lab en >1 otra droguería (sin la pedida)
            for it in items:
                loc = lab_local.get(it['id_laboratorio'])
                drogs = matriz.get(loc, set()) if loc else set()
                if not drogs or source_id in drogs:
                    destinos = [source_id]
                elif len(drogs) == 1:
                    destinos = list(drogs)
                else:
                    ambiguos.append(it)
                    destinos = [source_id]   # ante duda, queda en la pedida
                for d in destinos:
                    grupos.setdefault(d, []).append(it)

            # EAN map (alfabeta/troquel → ean) desde catálogo Kellerhoff + fallback local.
            ean_of = _build_ean_resolver(items, session)

            # Identidad de la farmacia (encabezado de los archivos).
            cfg_farm = get_config()
            farm_nombre = cfg_farm.get('farmacia_nombre') or 'Farmacia'
            farm_cuit = cfg_farm.get('farmacia_cuit') or ''

            out_groups = []
            for drog_id, its in sorted(grupos.items(),
                                       key=lambda kv: (kv[0] != source_id, kv[0])):
                cfg = drog_cfg.get(drog_id)
                nombre = drog_nombre.get(drog_id, f'Drog #{drog_id}')
                sin_ean = sum(1 for it in its if not ean_of(it))
                if not cfg:
                    out_groups.append({
                        'drog_id': drog_id, 'drogueria': nombre,
                        'es_origen': drog_id == source_id,
                        'formato': None, 'filename': None, 'content': None,
                        'n_items': len(its), 'sin_ean': sin_ean,
                        'aviso': 'Sin formato configurado para esta droguería.',
                        'items': _items_preview(its, ean_of),
                    })
                    continue
                comp_field = f'{num}{cfg["sufijo"]}'
                if cfg['formato'] == 'ped':
                    content = _fmt_ped(its, ean_of)
                    filename = f'S{cfg["codcli"]}{num[-3:]}{cfg["sufijo"]}.PED'
                else:  # txt20j
                    content = _fmt_txt20j(its, comp_field, cfg['codcli'],
                                          fecha_ddmmaaaa, ean_of, farm_nombre, farm_cuit)
                    filename = f'2{fecha_ddmmaaaa}{comp_field}.txt'
                out_groups.append({
                    'drog_id': drog_id, 'drogueria': nombre,
                    'es_origen': drog_id == source_id,
                    'formato': cfg['formato'], 'filename': filename,
                    'carpeta': cfg['carpeta'],
                    'subcarpeta': cfg['carpeta'].rstrip('\\').split('\\')[-1],
                    'content': content,
                    'n_items': len(its), 'sin_ean': sin_ean, 'aviso': None,
                    'items': _items_preview(its, ean_of),
                })

        return jsonify({
            'ok': True,
            'id_pedido': id_pedido,
            'comprobante': comprob,
            'origen': {'id': source_id, 'nombre': drog_nombre.get(source_id, '?')},
            'total_items': len(items),
            'ambiguos': len(ambiguos),
            'groups': out_groups,
        })


# ── Helpers de mapeo / EAN ───────────────────────────────────────────────────

def _source_provider_id(nombre_observer, session):
    """Mapea el nombre del proveedor de ObServer a nuestro Provider.id."""
    norm = _norm(nombre_observer)
    if not norm:
        return None
    for d in session.query(Provider).filter(Provider.tipo == 'drogueria'):
        dn = _norm(d.razon_social)
        if dn and (dn in norm or norm in dn):
            return d.id
    return None


def _build_ean_resolver(items, session):
    """Devuelve f(item)->ean. Resuelve por alfabeta, luego troquel (catálogo
    Kellerhoff) y por último el codigo_barra de nuestra tabla productos."""
    alfs = {it['alfabeta'] for it in items if it['alfabeta']}
    troqs = {it['troquel'] for it in items if it['troquel']}
    by_alf, by_troq = {}, {}
    if alfs:
        for a, e in (session.query(KellerhoffCatalogo.alfabeta, KellerhoffCatalogo.ean)
                     .filter(KellerhoffCatalogo.alfabeta.in_(alfs),
                             KellerhoffCatalogo.ean.isnot(None))):
            if a and e and a not in by_alf:
                by_alf[a] = e
    if troqs:
        for t, e in (session.query(KellerhoffCatalogo.troquel, KellerhoffCatalogo.ean)
                     .filter(KellerhoffCatalogo.troquel.in_(troqs),
                             KellerhoffCatalogo.ean.isnot(None))):
            if t and e and t not in by_troq:
                by_troq[t] = e
    # Fallback local por observer_id (productos.codigo_barra).
    obs_ids = [it['id_producto'] for it in items if it['id_producto'] is not None]
    by_obs = {}
    if obs_ids:
        for oid, cb in (session.query(Producto.observer_id, Producto.codigo_barra)
                        .filter(Producto.observer_id.in_(obs_ids),
                                Producto.codigo_barra.isnot(None))):
            if oid and cb:
                by_obs[oid] = cb

    def _resolve(it):
        return (by_alf.get(it['alfabeta'])
                or by_troq.get(it['troquel'])
                or by_obs.get(it['id_producto'])
                or '')
    return _resolve


def _items_preview(items, ean_of):
    return [{
        'cant': it['cantidad'], 'producto': it['producto'],
        'troquel': it['troquel'], 'alfabeta': it['alfabeta'],
        'lab': it['laboratorio'], 'ean': ean_of(it),
    } for it in items]


# ── Generadores de formato ──────────────────────────────────────────────────

def _fmt_ped(items, ean_of):
    """Kellerhoff .ped: cant(5,rjust) + alfabeta(10,rjust) + col3(24,rjust).
    col3 = troquel + ean + '0' (21 díg). Cada línea termina en CRLF (incl. última)."""
    lines = []
    for it in items:
        ean = ean_of(it)
        col3 = f'{it["troquel"]}{ean}0' if ean else it['troquel']
        lines.append(f'{it["cantidad"]:>5}{it["alfabeta"]:>10}{col3:>24}')
    return ''.join(l + '\r\n' for l in lines)


def _fmt_txt20j(items, comprob, codcli, fecha_ddmmaaaa, ean_of, farmacia_nombre, farmacia_cuit):
    """20 de Junio .txt: C|...| + D|ean|cant|desc|troquel| + F|count|."""
    lines = [
        f'C|{farmacia_nombre:<40}|{codcli:<10}|{farmacia_cuit:<11}|'
        f'{fecha_ddmmaaaa}|{comprob:<10}|'
    ]
    for it in items:
        ean = (ean_of(it) or '')[:13]
        desc = it['producto'][:60]
        lines.append(f'D|{ean:<13}|{it["cantidad"]:>6}|{desc:<60}|{it["troquel"]:<60}|')
    total = len(lines) + 1  # incluye el propio renglón F
    lines.append(f'F|{str(total):<11}|')
    return ''.join(l + '\r\n' for l in lines)
