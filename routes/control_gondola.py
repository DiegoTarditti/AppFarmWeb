"""Control de stock por laboratorio: dentro y fuera del robot.

Planilla para ir a contar el estante. Se elige un laboratorio y se listan sus
artículos con stock, mostrando cuánto está DENTRO del robot y cuánto en el
DEPÓSITO/góndola (fuera del robot), con un filtro de 3 opciones:

    Con stock · Solo en robot · Solo en depósito

Búsqueda + export PDF/Excel para imprimir y contar a mano. Mismo patrón que
/rowa/planilla.

De dónde sale cada número:
  - Total por artículo → ObServer (`ObsStock.stock_actual`).
  - En robot → `_cargar()` de Rowa (habla con la máquina): `fila.cantidad` es lo
    que hay adentro ahora. Es la ÚNICA fuente del stock del robot por artículo
    (la tabla de stock plana no lo separa). Si el robot no responde, se degrada a
    depósito y se avisa.
  - En depósito (fuera del robot) → total − robot (lo que Rowa ya calcula como
    `al_deposito`), y para los artículos que no están en el robot es el total.

Es POR laboratorio (server-side): la góndola es casi toda la farmacia (miles de
productos), renderizar todo sería una página pesadísima.
"""
from datetime import datetime

from flask import Response, abort, render_template, request
from flask_login import login_required
from sqlalchemy import func

from database import ObsLaboratorio, ObsProducto, ObsStock, get_db

# Opciones del filtro de arriba (orden pedido por Diego). Son INCLUSIVOS, no
# exclusivos: "en robot" trae todo lo que tiene algo en el robot (aunque además
# tenga depósito) y "en depósito" todo lo que tiene algo en depósito. Así
# En robot ∪ En depósito = Con stock y NADA queda afuera del control: un producto
# que está en los dos (ej. OPTAMOX 18 robot + 12 depósito) aparece en las dos, con
# su parte correspondiente, sin contarse doble.
FILTROS = ('con_stock', 'en_robot', 'en_deposito', 'en_ambos')

# Alias de las URLs viejas ('solo_*') para no romper bookmarks.
_ALIAS_FILTRO = {'solo_robot': 'en_robot', 'solo_deposito': 'en_deposito'}


def _normalizar_filtro(filtro):
    filtro = _ALIAS_FILTRO.get(filtro, filtro)
    return filtro if filtro in FILTROS else 'con_stock'


def _robot_por_pid():
    """{producto_observer: (en_robot, total, deposito, nombre, laboratorio)} desde
    el robot. Vacío (y sin_robot=True) si la máquina no responde."""
    try:
        from routes.rowa import _cargar
        data = _cargar()
    except Exception:  # noqa: BLE001 — RowaError/OSError/sin ObServer → degradar
        return {}, True
    out = {}
    for f in data.get('filas', []):
        pid = getattr(f, 'producto_observer', None)
        if not pid:
            continue
        en_robot = int(getattr(f, 'cantidad', 0) or 0)
        total = getattr(f, 'stock_total', None)
        total = int(total) if total is not None else en_robot
        # OJO: en la fila de Rowa `al_deposito` es el "Sacar" (cuánto mover del
        # robot al depósito), NO el stock de depósito. El stock real es
        # `stock_deposito` (= stock_total − en_robot). Usar al_deposito daba
        # depósito 0 y la suma no cerraba (robot 18 + 0 ≠ total 30).
        deposito = getattr(f, 'stock_deposito', None)
        deposito = int(deposito) if deposito is not None else max(total - en_robot, 0)
        out[pid] = (en_robot, total, deposito,
                    getattr(f, 'nombre_obs', None) or getattr(f, 'nombre', '') or '',
                    getattr(f, 'laboratorio', '') or '')
    return out, False


def _labs_con_stock(session):
    """Nombres de laboratorio que tienen al menos un producto con stock."""
    stocked = (session.query(ObsStock.producto_observer)
               .group_by(ObsStock.producto_observer)
               .having(func.sum(ObsStock.stock_actual) > 0)).subquery()
    rows = (session.query(ObsLaboratorio.descripcion)
            .join(ObsProducto,
                  ObsProducto.laboratorio_observer == ObsLaboratorio.observer_id)
            .filter(ObsProducto.observer_id.in_(
                        session.query(stocked.c.producto_observer)),
                    ObsProducto.fecha_baja.is_(None))
            .distinct().all())
    return sorted({r[0] for r in rows if r[0]})


def _filas_de_lab(session, robot_map, lab_nombre):
    """Todas las filas con stock de ese laboratorio, con el split robot/depósito.

    El universo es TODO lo que tiene stock (esté o no en el robot); 'en robot'
    sale de los packs REALES (robot_map, vía _cargar), no de la membresía de
    capacidad (ObsRowaProducto tiene ~29k filas de cupo, no de presencia física
    — usarla dejaba afuera los que tienen cupo pero 0 packs adentro y stock en
    depósito). depósito = total − en_robot.
    """
    lab_id = (session.query(ObsLaboratorio.observer_id)
              .filter(ObsLaboratorio.descripcion == lab_nombre).scalar())
    if lab_id is None:
        return []
    rows = (session.query(ObsProducto.observer_id.label('pid'),
                          ObsProducto.descripcion,
                          ObsProducto.descripcion_custom,
                          func.sum(ObsStock.stock_actual).label('stock'))
            .join(ObsStock, ObsStock.producto_observer == ObsProducto.observer_id)
            .filter(ObsProducto.laboratorio_observer == lab_id,
                    ObsProducto.fecha_baja.is_(None))
            .group_by(ObsProducto.observer_id, ObsProducto.descripcion,
                      ObsProducto.descripcion_custom)
            .having(func.sum(ObsStock.stock_actual) > 0).all())
    filas = []
    for r in rows:
        total = int(r.stock or 0)
        en_robot = robot_map.get(r.pid, (0,))[0]
        deposito = max(total - en_robot, 0)
        filas.append({'nombre': (r.descripcion_custom or r.descripcion or '').strip(),
                      'laboratorio': lab_nombre,
                      'en_robot': en_robot, 'deposito': deposito,
                      'total': max(total, en_robot)})
    return filas


def _aplicar_filtro(filas, filtro):
    # Inclusivos: un producto en robot Y depósito aparece en las dos vistas.
    if filtro == 'en_robot':
        return [f for f in filas if f['en_robot'] > 0]
    if filtro == 'en_deposito':
        return [f for f in filas if f['deposito'] > 0]
    if filtro == 'en_ambos':
        # Sí o sí stock en los DOS: robot ≥ 1 y depósito ≥ 1.
        return [f for f in filas if f['en_robot'] > 0 and f['deposito'] > 0]
    return [f for f in filas if f['total'] > 0]   # con_stock (default)


def init_app(app):

    @app.route('/control-gondola')
    @login_required
    def control_gondola():
        lab = (request.args.get('lab') or '').strip()
        filtro = _normalizar_filtro(request.args.get('filtro') or 'con_stock')
        robot_map, sin_robot = _robot_por_pid()
        with get_db() as session:
            labs = _labs_con_stock(session)
            filas = _filas_de_lab(session, robot_map, lab) if lab else []
        if lab:
            filas = _aplicar_filtro(filas, filtro)
            filas.sort(key=lambda f: f['nombre'].lower())
        return render_template('control_gondola.html', labs=labs, filas=filas,
                               lab=lab, filtro=filtro, sin_robot=sin_robot,
                               generado=datetime.now())

    @app.route('/control-gondola/export.<fmt>')
    @login_required
    def control_gondola_export(fmt):
        if fmt not in ('pdf', 'xlsx'):
            abort(404)
        lab = (request.args.get('lab') or '').strip()
        if not lab:
            abort(400, description='Falta elegir el laboratorio.')
        filtro = _normalizar_filtro(request.args.get('filtro') or 'con_stock')
        q = (request.args.get('q') or '').strip().lower()
        robot_map, _sin = _robot_por_pid()
        with get_db() as session:
            filas = _filas_de_lab(session, robot_map, lab)
        filas = _aplicar_filtro(filas, filtro)
        if q:
            filas = [f for f in filas if q in f['nombre'].lower()]
        filas.sort(key=lambda f: f['nombre'].lower())

        from services.control_gondola_export import construir_pdf, construir_xlsx
        generado = datetime.now()
        args = (filas, lab, filtro, generado)
        contenido = construir_pdf(*args) if fmt == 'pdf' else construir_xlsx(*args)
        mime = ('application/pdf' if fmt == 'pdf'
                else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        slug = ''.join(c if c.isalnum() else '-' for c in lab)[:30]
        nombre = f'Control-stock-{slug}-{generado.strftime("%Y-%m-%d")}.{fmt}'
        return Response(contenido, mimetype=mime,
                        headers={'Content-Disposition': f'attachment; filename="{nombre}"'})
