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

from database import ObsLaboratorio, ObsProducto, ObsRowaProducto, ObsStock, get_db

# Opciones del filtro de arriba, en el orden pedido por Diego.
FILTROS = ('con_stock', 'solo_robot', 'solo_deposito')


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
        deposito = getattr(f, 'al_deposito', None)
        deposito = int(deposito) if deposito is not None else max(total - en_robot, 0)
        out[pid] = (en_robot, total, deposito,
                    getattr(f, 'nombre_obs', None) or getattr(f, 'nombre', '') or '',
                    getattr(f, 'laboratorio', '') or '')
    return out, False


def _fuera_robot_con_stock(session):
    """{pid: total} de artículos con stock que NO están en el robot."""
    robot_sub = session.query(ObsRowaProducto.producto_observer)
    q = (session.query(ObsProducto.observer_id.label('pid'),
                       func.sum(ObsStock.stock_actual).label('stock'))
         .join(ObsStock, ObsStock.producto_observer == ObsProducto.observer_id)
         .filter(ObsProducto.fecha_baja.is_(None),
                 ObsProducto.observer_id.notin_(robot_sub))
         .group_by(ObsProducto.observer_id)
         .having(func.sum(ObsStock.stock_actual) > 0))
    return {r.pid: int(r.stock or 0) for r in q.all()}


def _armar_filas(session, robot_map):
    """Una fila por artículo con stock, con su split robot/depósito, uniendo los
    del robot con los de fuera del robot. Devuelve (filas, labs_disponibles)."""
    filas = []
    # 1) Artículos del robot (traen su nombre/lab del cruce que hizo _cargar).
    for _pid, (en_robot, total, deposito, nombre, lab) in robot_map.items():
        if total <= 0 and en_robot <= 0:
            continue
        filas.append({'nombre': nombre.strip(), 'laboratorio': lab or '',
                      'en_robot': en_robot, 'deposito': deposito,
                      'total': max(total, en_robot)})
    # 2) Artículos fuera del robot con stock → todo en depósito.
    fuera = _fuera_robot_con_stock(session)
    fuera = {pid: st for pid, st in fuera.items() if pid not in robot_map}
    if fuera:
        labs = dict(session.query(ObsLaboratorio.observer_id,
                                  ObsLaboratorio.descripcion).all())
        prods = (session.query(ObsProducto)
                 .filter(ObsProducto.observer_id.in_(list(fuera))).all())
        for p in prods:
            st = fuera[p.observer_id]
            filas.append({
                'nombre': (p.descripcion_custom or p.descripcion or '').strip(),
                'laboratorio': labs.get(p.laboratorio_observer) or '',
                'en_robot': 0, 'deposito': st, 'total': st})
    labs_disponibles = sorted({f['laboratorio'] for f in filas if f['laboratorio']})
    return filas, labs_disponibles


def _aplicar_filtro(filas, filtro):
    if filtro == 'solo_robot':
        return [f for f in filas if f['en_robot'] > 0 and f['deposito'] <= 0]
    if filtro == 'solo_deposito':
        return [f for f in filas if f['deposito'] > 0 and f['en_robot'] <= 0]
    return [f for f in filas if f['total'] > 0]   # con_stock (default)


def init_app(app):

    @app.route('/control-gondola')
    @login_required
    def control_gondola():
        lab = (request.args.get('lab') or '').strip()
        filtro = request.args.get('filtro') or 'con_stock'
        if filtro not in FILTROS:
            filtro = 'con_stock'
        robot_map, sin_robot = _robot_por_pid()
        with get_db() as session:
            todas, labs = _armar_filas(session, robot_map)
        filas = []
        if lab:
            filas = _aplicar_filtro([f for f in todas if f['laboratorio'] == lab], filtro)
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
        filtro = request.args.get('filtro') or 'con_stock'
        if filtro not in FILTROS:
            filtro = 'con_stock'
        q = (request.args.get('q') or '').strip().lower()
        robot_map, _sin = _robot_por_pid()
        with get_db() as session:
            todas, _labs = _armar_filas(session, robot_map)
        filas = _aplicar_filtro([f for f in todas if f['laboratorio'] == lab], filtro)
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
