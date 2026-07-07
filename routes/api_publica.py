"""API pública de AppFarmWeb — sirve los catálogos Praxis-uniformes a productos
externos (AppChatFarm y futuros).

Auth: `X-Api-Key` en header. La key se hashea con SHA-256 al crearla; nunca
se guarda en claro. Rate limit por cuota diaria en la propia tabla `api_keys`.

Endpoints:
  GET /api/publica/producto/<observer_id>          → 1 producto con precio
  GET /api/publica/producto/buscar?q=...           → búsqueda por nombre/droga
  GET /api/publica/obras-sociales                  → lista de OS activas
  GET /api/publica/obras-sociales/<oid>/planes    → planes de una OS
  GET /api/publica/ping                            → chequeo de conectividad
"""
import hashlib
from functools import wraps

from flask import jsonify, request

import database

# ── Auth por API key ──────────────────────────────────────────────────────

def _hash_key(clave):
    return hashlib.sha256(clave.encode('utf-8')).hexdigest()


def _validar_key(key_str):
    """Devuelve la fila ApiKey si es válida, activa y con cuota disponible.
    Actualiza contadores. `None` si es inválida/revocada/sin cuota."""
    if not key_str:
        return None
    key_hash = _hash_key(key_str)
    with database.get_db() as s:
        ak = s.query(database.ApiKey).filter_by(key_hash=key_hash, activo=True).first()
        if not ak:
            return None
        # Reset del contador diario
        hoy = database.now_ar().date()
        if ak.usos_hoy_fecha != hoy:
            ak.usos_hoy = 0
            ak.usos_hoy_fecha = hoy
        # Chequeo de cuota diaria
        if ak.cuota_diaria is not None and ak.usos_hoy >= ak.cuota_diaria:
            s.commit()  # persistimos el reset del contador aunque no autoricemos
            return None
        # Autorizado — incrementar contadores
        ak.usos_hoy += 1
        ak.total_usos += 1
        ak.ultimo_uso = database.now_ar()
        ak.ultimo_ip = (request.headers.get('X-Forwarded-For', request.remote_addr) or '')[:45]
        s.commit()
        # Devolver un dict simple (evitar detached instance al salir del `with`)
        return {'id': ak.id, 'cliente': ak.cliente_nombre, 'prefix': ak.prefix}


def requiere_api_key(fn):
    @wraps(fn)
    def wrap(*a, **kw):
        key = request.headers.get('X-Api-Key', '').strip()
        auth = _validar_key(key)
        if not auth:
            return jsonify({'error': 'API key inválida o cuota excedida'}), 401
        request.api_client = auth
        return fn(*a, **kw)
    return wrap


# ── Endpoints ─────────────────────────────────────────────────────────────

def init_app(app):

    @app.route('/api/publica/ping')
    @requiere_api_key
    def api_publica_ping():
        """Chequeo de conectividad — devuelve el cliente identificado."""
        return jsonify({'ok': True, 'cliente': request.api_client['cliente']})

    @app.route('/api/publica/producto/<int:observer_id>')
    @requiere_api_key
    def api_publica_producto(observer_id):
        """Devuelve un producto con precio_lista. Incluye monodroga y presentación."""
        with database.get_db() as s:
            p = s.get(database.ObsProducto, observer_id)
            if not p or p.fecha_baja:
                return jsonify({'error': 'no encontrado'}), 404
            droga = None
            if p.nombre_droga_observer:
                nd = s.get(database.ObsNombreDroga, p.nombre_droga_observer)
                if nd:
                    droga = nd.descripcion
            return jsonify({
                'observer_id': p.observer_id,
                'descripcion': p.descripcion,
                'droga': droga,
                'cantidad_envase': float(p.cantidad_envase) if p.cantidad_envase else None,
                'es_fraccionable': bool(p.es_fraccionable),
                'tipo_venta_control': (p.id_tipo_venta_control or '').strip() or None,
                'precio_lista': float(p.precio_lista) if p.precio_lista is not None else None,
                'precio_fecha_vigencia': p.precio_lista_fecha_vigencia.isoformat() if p.precio_lista_fecha_vigencia else None,
                'precio_actualizado_en': p.precio_lista_actualizado_en.isoformat() if p.precio_lista_actualizado_en else None,
            })

    @app.route('/api/publica/producto/buscar')
    @requiere_api_key
    def api_publica_producto_buscar():
        """Búsqueda multi-token por descripción y monodroga.
        Params: q (string), limite (default 12, max 50)."""
        q = (request.args.get('q') or '').strip()
        try:
            limite = min(50, max(1, int(request.args.get('limite') or 12)))
        except (TypeError, ValueError):
            limite = 12
        if len(q) < 2:
            return jsonify({'productos': []})
        palabras = [p for p in q.split() if p][:6]
        params = {f'p{i}': f'%{w}%' for i, w in enumerate(palabras)}
        params['lim'] = limite
        cond_prod = ' AND '.join(f"op.descripcion ILIKE :p{i}" for i in range(len(palabras)))
        cond_droga = ' AND '.join(f"nd.descripcion ILIKE :p{i}" for i in range(len(palabras)))
        sql = database.text(f"""
            SELECT op.observer_id, op.descripcion, op.cantidad_envase,
                   op.es_fraccionable, op.id_tipo_venta_control,
                   op.precio_lista, op.precio_lista_fecha_vigencia,
                   op.laboratorio_observer,
                   nd.descripcion AS droga,
                   ol.descripcion AS laboratorio_nombre
              FROM obs_productos op
              LEFT JOIN obs_nombres_drogas nd
                ON nd.observer_id = op.nombre_droga_observer
              LEFT JOIN obs_laboratorios ol
                ON ol.observer_id = op.laboratorio_observer
             WHERE op.fecha_baja IS NULL
               AND ({cond_prod} OR {cond_droga})
             ORDER BY op.descripcion
             LIMIT :lim
        """)
        try:
            with database.get_db() as s:
                rows = s.execute(sql, params).fetchall()
        except Exception as e:
            return jsonify({'error': f'query fallida: {type(e).__name__}'}), 500
        out = []
        for r in rows:
            tvc = (r.id_tipo_venta_control or '').strip()
            out.append({
                'observer_id': r.observer_id,
                'descripcion': r.descripcion,
                'droga': r.droga,
                'cantidad_envase': float(r.cantidad_envase) if r.cantidad_envase else None,
                'es_fraccionable': bool(r.es_fraccionable),
                'requiere_receta': bool(tvc and tvc != 'L'),
                'precio_lista': float(r.precio_lista) if r.precio_lista is not None else None,
                'precio_fecha_vigencia': r.precio_lista_fecha_vigencia.isoformat() if r.precio_lista_fecha_vigencia else None,
                'laboratorio_observer': r.laboratorio_observer,
                'laboratorio_nombre': r.laboratorio_nombre,
            })
        return jsonify({'productos': out})

    @app.route('/api/publica/obras-sociales')
    @requiere_api_key
    def api_publica_obras_sociales():
        """Lista de OS activas."""
        q = (request.args.get('q') or '').strip()
        with database.get_db() as s:
            base = s.query(database.ObsObraSocial).filter(
                database.ObsObraSocial.fecha_baja.is_(None))
            if q:
                base = base.filter(database.ObsObraSocial.descripcion.ilike(f'%{q}%'))
            rows = base.order_by(database.ObsObraSocial.descripcion).limit(200).all()
            out = [{'observer_id': r.observer_id, 'descripcion': r.descripcion}
                   for r in rows]
        return jsonify({'obras_sociales': out})

    @app.route('/api/publica/obras-sociales/<int:observer_id>/planes')
    @requiere_api_key
    def api_publica_planes(observer_id):
        """Planes + convenios de una OS. Relación: OS → Convenios → Planes."""
        with database.get_db() as s:
            os_row = s.get(database.ObsObraSocial, observer_id)
            if not os_row:
                return jsonify({'error': 'OS no encontrada'}), 404
            convenios = (s.query(database.ObsConvenio)
                         .filter(database.ObsConvenio.obra_social_observer == observer_id,
                                 database.ObsConvenio.fecha_baja.is_(None))
                         .order_by(database.ObsConvenio.descripcion).all())
            convenio_ids = [c.observer_id for c in convenios]
            if convenio_ids:
                planes = (s.query(database.ObsPlan)
                          .filter(database.ObsPlan.convenio_observer.in_(convenio_ids),
                                  database.ObsPlan.fecha_baja.is_(None))
                          .order_by(database.ObsPlan.descripcion).all())
            else:
                planes = []
            return jsonify({
                'obra_social': {
                    'observer_id': os_row.observer_id,
                    'descripcion': os_row.descripcion,
                },
                'convenios': [{'observer_id': c.observer_id, 'descripcion': c.descripcion}
                              for c in convenios],
                'planes': [{'observer_id': p.observer_id, 'descripcion': p.descripcion,
                            'convenio_observer': p.convenio_observer}
                           for p in planes],
            })

    # ── Pacientes (para consumo desde AppClinica) ──────────────────────────

    @app.route('/api/publica/paciente/<int:observer_id>')
    @requiere_api_key
    def api_publica_paciente(observer_id):
        """Ficha del paciente de esta farmacia. Devuelve identidad + OS."""
        with database.get_db() as s:
            c = s.get(database.ObsCliente, observer_id)
            if not c:
                return jsonify({'error': 'no encontrado'}), 404
            return jsonify({
                'observer_id': c.observer_id,
                'apellido_nombre': c.apellido_nombre,
                'documento_tipo': c.documento_tipo,
                'documento_numero': c.documento_numero,
                'telefono': c.telefono,
                'domicilio_direccion': c.domicilio_direccion,
                'localidad': c.localidad,
                # ObsCliente no tiene obra_social_observer ni fecha_baja hoy.
                # La OS del paciente vive en el modelo Cliente (extension local).
                'obra_social_observer': None,
                'obra_social_nombre': None,
                'baja': False,
            })

    @app.route('/api/publica/paciente/buscar')
    @requiere_api_key
    def api_publica_paciente_buscar():
        """Busca pacientes por DNI (exacto) o por texto en apellido/nombre.
        Params: dni=X | q=texto | limite (default 12)."""
        dni = (request.args.get('dni') or '').strip()
        q = (request.args.get('q') or '').strip()
        try:
            limite = min(50, max(1, int(request.args.get('limite') or 12)))
        except (TypeError, ValueError):
            limite = 12
        with database.get_db() as s:
            # NOTA: ObsCliente no tiene columna fecha_baja hoy — el filtro por
            # 'no dados de baja' que existia antes matcheaba 0 filas siempre.
            base = s.query(database.ObsCliente)
            if dni:
                if not dni.isdigit():
                    return jsonify({'error': 'dni debe ser numérico'}), 400
                base = base.filter(database.ObsCliente.documento_numero == int(dni))
            elif q:
                # Multi-token AND sobre apellido_nombre
                palabras = [p for p in q.split() if len(p) >= 2][:5]
                if not palabras:
                    return jsonify({'pacientes': []})
                for p in palabras:
                    base = base.filter(database.ObsCliente.apellido_nombre.ilike(f'%{p}%'))
            else:
                return jsonify({'error': 'especificá dni o q'}), 400
            rows = base.order_by(database.ObsCliente.apellido_nombre).limit(limite).all()
            out = [{
                'observer_id': c.observer_id,
                'apellido_nombre': c.apellido_nombre,
                'documento_numero': c.documento_numero,
                'telefono': c.telefono,
                'localidad': c.localidad,
            } for c in rows]
        return jsonify({'pacientes': out})

    @app.route('/api/publica/paciente/<int:observer_id>/compras')
    @requiere_api_key
    def api_publica_paciente_compras(observer_id):
        """Compras del paciente en Badia (via obs_ventas_detalle).

        Consumido por el cron receta→compra de AppClinica: dado un paciente
        (observer_id) y una fecha desde X, devuelve las compras posteriores.

        Params:
          desde=YYYY-MM-DD (obligatorio) — solo compras desde esa fecha.
          hasta=YYYY-MM-DD (opcional)    — solo compras hasta esa fecha.
          limite (default 100, max 500).
        """
        from datetime import date, datetime  # noqa: F401
        desde_str = (request.args.get('desde') or '').strip()
        hasta_str = (request.args.get('hasta') or '').strip()
        try:
            limite = min(500, max(1, int(request.args.get('limite') or 100)))
        except (TypeError, ValueError):
            limite = 100
        if not desde_str:
            return jsonify({'error': 'falta desde=YYYY-MM-DD'}), 400
        try:
            desde_dt = datetime.strptime(desde_str, '%Y-%m-%d')
        except ValueError:
            return jsonify({'error': 'desde inválido, formato YYYY-MM-DD'}), 400
        hasta_dt = None
        if hasta_str:
            try:
                hasta_dt = datetime.strptime(hasta_str, '%Y-%m-%d')
            except ValueError:
                return jsonify({'error': 'hasta inválido, formato YYYY-MM-DD'}), 400
        with database.get_db() as s:
            V = database.ObsVentaDetalle
            q = (s.query(V)
                 .filter(V.cliente_observer == observer_id)
                 .filter(V.fecha_operacion >= desde_dt))
            if hasta_dt:
                q = q.filter(V.fecha_operacion < hasta_dt)
            rows = q.order_by(V.fecha_operacion.desc()).limit(limite).all()
            # Descripciones de producto (una query por lote de IDs, no N+1)
            prod_ids = list({r.producto_observer for r in rows if r.producto_observer})
            prod_ix = {}
            if prod_ids:
                prods = s.query(database.ObsProducto).filter(
                    database.ObsProducto.observer_id.in_(prod_ids)).all()
                prod_ix = {p.observer_id: p.descripcion for p in prods}
            # Blacklist de cargos administrativos (no son productos reales):
            # 'SELLADO DE RECETAS', 'RETIRA EN FARMACIA', 'Costo Receta/Cupón'.
            # Diego 2026-07-07: ensucian el histórico clínico de AppClinica.
            # Match case-insensitive por substring — cubre variantes ("Costo Cupón",
            # "Costo Receta", etc.). Los datos siguen en la DB por si Badia los
            # necesita para audit/facturación; solo se descartan en esta respuesta.
            _BLACKLIST = ('SELLADO DE RECETAS', 'SELLADO RECETAS',
                          'RETIRA EN FARMACIA', 'COSTO RECETA', 'COSTO CUPON')
            def _es_administrativo(desc):
                d = (desc or '').upper()
                return any(pat in d for pat in _BLACKLIST)
            out = [{
                'id_producto_vendido': r.id_producto_vendido,
                'id_operacion': r.id_operacion,
                'fecha_operacion': r.fecha_operacion.isoformat() if r.fecha_operacion else None,
                'producto_observer': r.producto_observer,
                'producto_descripcion': prod_ix.get(r.producto_observer),
                'importe': float(r.importe) if r.importe is not None else None,
                'importe_a_cargo_os': float(r.importe_a_cargo_os) if r.importe_a_cargo_os is not None else None,
                'importe_efectivo': float(r.importe_efectivo) if r.importe_efectivo is not None else None,
            } for r in rows if not _es_administrativo(prod_ix.get(r.producto_observer))]
        return jsonify({'compras': out, 'total': len(out)})

    @app.route('/api/publica/stock/<int:observer_id>')
    @requiere_api_key
    def api_publica_stock_snapshot(observer_id):
        """Stock snapshot desde obs_stock (última sync desde ObServer). Se
        usa como fallback cuando el DockerPanel esta apagado y no puede
        responder la consulta en vivo. Suma stock de todas las farmacias
        (habitualmente solo Badia = 1)."""
        with database.get_db() as s:
            rows = (s.query(database.ObsStock)
                    .filter_by(producto_observer=observer_id).all())
            if not rows:
                return jsonify({'ok': False, 'error': 'sin registro de stock'}), 404
            total = sum(int(r.stock_actual or 0) for r in rows)
            # Tomamos la sync_en mas reciente entre todas las farmacias
            syncs = [r.sync_en for r in rows if r.sync_en]
            sync_en = max(syncs).isoformat() if syncs else None
            return jsonify({
                'ok': True,
                'observer_id': observer_id,
                'stock': total,
                'sync_en': sync_en,
            })

    # ── Panel remoto: consultas de stock en vivo ──────────────────────────
    # AppClinica encola una consulta de stock que el DockerPanel de la
    # farmacia local ejecuta contra su DB (stock en tiempo real) y devuelve
    # el resultado. Ver docs/BACKLOG.md item 17 opcion C.

    @app.route('/api/publica/panel/stock', methods=['POST'])
    @requiere_api_key
    def api_publica_panel_stock_encolar():
        """Encola una consulta de stock. Body JSON: {observer_id}.
        Devuelve {cmd_id} para que el cliente polee el resultado."""
        body = request.get_json(silent=True) or {}
        try:
            observer_id = int(body.get('observer_id'))
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'observer_id invalido'}), 400
        comando = f'stock:{observer_id}'
        cliente = request.api_client.get('cliente', 'api')
        with database.get_db() as s:
            cmd = database.PanelComando(
                comando=comando, estado='pendiente',
                solicitado_por=f'api:{cliente}'[:80])
            s.add(cmd)
            s.commit()
            cmd_id = cmd.id
        return jsonify({'ok': True, 'cmd_id': cmd_id, 'comando': comando})

    @app.route('/api/publica/panel/comandos/<int:cmd_id>')
    @requiere_api_key
    def api_publica_panel_comando_estado(cmd_id):
        """Polea el estado + resultado del comando. Cualquier cliente con
        api key puede consultar cualquier cmd_id — no hay ownership. Aceptable
        porque los resultados son de stock (no datos sensibles)."""
        with database.get_db() as s:
            cmd = s.get(database.PanelComando, cmd_id)
            if not cmd:
                return jsonify({'ok': False, 'error': 'no existe'}), 404
            return jsonify({
                'ok': True,
                'estado': cmd.estado,
                'resultado': cmd.resultado,
                'duracion_ms': cmd.duracion_ms,
                'comando': cmd.comando,
            })

    # ── PAMI: buscar Nº de afiliado por DNI ──────────────────────────────────
    # Fuente: Gestion.Recetas.NumeroAfiliado (schema premium, requiere user sa).
    # Consumido por AppClinica para backfill de pacientes.afiliado_nro cuando
    # tenemos DNI pero no el número de afiliado PAMI. Solo devuelve el afiliado
    # y el nombre — NO datos médicos, NO productos, NO historial.

    def _buscar_afiliado_pami_por_dni_impl(dni_int):
        """Devuelve dict con datos del afiliado PAMI más recientemente registrado
        para ese DNI, o None si no hay match. Prioriza plan PAMI (via JOIN a
        DW.ObrasSociales). Fallback: primera receta con ese DNI si no hay PAMI.

        Requiere acceso al schema Gestion (user sa). Sin acceso → None.
        """
        from observer_source import _connect, _test_acceso_gestion
        if not _test_acceso_gestion():
            return None
        conn = _connect(timeout=15)
        if conn is None:
            return None
        try:
            with conn.cursor(as_dict=True) as cur:
                # PAMI primero
                cur.execute("""
                    SELECT TOP 1
                        r.NumeroAfiliado,
                        r.NombreAfiliado,
                        r.SexoAfiliado,
                        r.DocumentoAfiliado_Numero,
                        MAX(r.FechaDeOperacion) OVER () AS ultima_op
                    FROM Gestion.Recetas r
                    LEFT JOIN DW.Planes p ON p.IdPlan = r.IdPlan
                    LEFT JOIN DW.Convenios cv ON cv.IdConvenio = p.IdConvenio
                    LEFT JOIN DW.ObrasSociales os ON os.IdObraSocial = cv.IdObraSocial
                    WHERE r.DocumentoAfiliado_Numero = %d
                      AND r.Anulada = 0
                      AND os.Descripcion = 'PAMI'
                    ORDER BY r.FechaDeOperacion DESC
                """, (int(dni_int),))
                row = cur.fetchone()
                if row:
                    return {
                        'numero_afiliado': (row['NumeroAfiliado'] or '').strip(),
                        'nombre_afiliado': (row['NombreAfiliado'] or '').strip(),
                        'sexo': row['SexoAfiliado'],
                        'obra_social': 'PAMI',
                        'documento_numero': row['DocumentoAfiliado_Numero'],
                    }
                return None
        finally:
            conn.close()

    @app.route('/api/publica/pami/afiliado-por-dni')
    @requiere_api_key
    def api_publica_pami_afiliado_por_dni():
        """Busca NumeroAfiliado PAMI dado un DNI.
        Params: dni=numérico. Devuelve {found, numero_afiliado, nombre_afiliado, ...}
        o {found: false} si no hay match.
        """
        dni = (request.args.get('dni') or '').strip()
        if not dni.isdigit():
            return jsonify({'error': 'dni debe ser numérico'}), 400
        try:
            data = _buscar_afiliado_pami_por_dni_impl(int(dni))
        except Exception as e:
            return jsonify({'error': f'observer no disponible: {e}'}), 503
        if data is None:
            return jsonify({'found': False, 'dni': dni})
        return jsonify({'found': True, 'dni': dni, **data})

    @app.route('/api/publica/pami/afiliados-por-dnis', methods=['POST'])
    @requiere_api_key
    def api_publica_pami_afiliados_por_dnis():
        """Batch: recibe {dnis: [str, ...]} y devuelve {resultados: {dni: {found, ...}}}.
        Máx 500 DNIs por llamada para evitar timeouts.
        Consulta Gestion.Recetas con IN (...) y agrupa por DNI. Prioriza OS=PAMI.
        """
        body = request.get_json(silent=True) or {}
        dnis_in = body.get('dnis') or []
        if not isinstance(dnis_in, list):
            return jsonify({'error': 'dnis debe ser lista'}), 400
        dnis = []
        for d in dnis_in[:500]:
            s = str(d).strip()
            if s.isdigit():
                dnis.append(int(s))
        if not dnis:
            return jsonify({'resultados': {}, 'consultados': 0})

        from observer_source import _connect, _test_acceso_gestion
        if not _test_acceso_gestion():
            return jsonify({
                'error': 'sin_acceso_premium',
                'mensaje': 'Esta consulta requiere acceso al schema Gestion (user sa).',
            }), 501
        conn = _connect(timeout=30)
        if conn is None:
            return jsonify({'error': 'observer no disponible'}), 503
        try:
            with conn.cursor(as_dict=True) as cur:
                placeholders = ','.join(['%d'] * len(dnis))
                cur.execute(f"""
                    WITH ranked AS (
                        SELECT
                            r.DocumentoAfiliado_Numero AS dni,
                            r.NumeroAfiliado, r.NombreAfiliado, r.SexoAfiliado,
                            r.FechaDeOperacion,
                            CASE WHEN os.Descripcion = 'PAMI' THEN 0 ELSE 1 END AS es_pami,
                            os.Descripcion AS obra_social,
                            ROW_NUMBER() OVER (
                                PARTITION BY r.DocumentoAfiliado_Numero
                                ORDER BY
                                    CASE WHEN os.Descripcion = 'PAMI' THEN 0 ELSE 1 END,
                                    r.FechaDeOperacion DESC
                            ) AS rn
                        FROM Gestion.Recetas r
                        LEFT JOIN DW.Planes p ON p.IdPlan = r.IdPlan
                        LEFT JOIN DW.Convenios cv ON cv.IdConvenio = p.IdConvenio
                        LEFT JOIN DW.ObrasSociales os ON os.IdObraSocial = cv.IdObraSocial
                        WHERE r.DocumentoAfiliado_Numero IN ({placeholders})
                          AND r.Anulada = 0
                    )
                    SELECT dni, NumeroAfiliado, NombreAfiliado, SexoAfiliado, obra_social
                      FROM ranked WHERE rn = 1
                """, tuple(dnis))
                found_rows = cur.fetchall()
        finally:
            conn.close()

        found_map = {}
        for r in found_rows:
            found_map[str(r['dni'])] = {
                'found': True,
                'numero_afiliado': (r['NumeroAfiliado'] or '').strip(),
                'nombre_afiliado': (r['NombreAfiliado'] or '').strip(),
                'sexo': r['SexoAfiliado'],
                'obra_social': r['obra_social'],
            }
        resultados = {}
        for d in dnis:
            resultados[str(d)] = found_map.get(str(d), {'found': False})
        return jsonify({'resultados': resultados, 'consultados': len(dnis),
                         'encontrados': len(found_map)})

    # ── PAMI: crónicos sugeridos por afiliado ────────────────────────────────
    # Detector de crónicos: agrupa recetas por DROGA (no producto puntual, para
    # captar cambios de marca/dosis). Sugiere las drogas con >= N dispensas en
    # la ventana y cadencia entre X e Y días.
    #
    # Consumido por AppClinica: pantalla "sugeridos por consumo" en la ficha del
    # paciente. Devuelve las presentaciones concretas dispensadas para que la
    # UI pueda linkear al catálogo Praxis (observer_id de producto).
    #
    # Umbrales default acordados 2026-07-13: 3 dispensas, cadencia 20-55d,
    # ventana 6m, agrupado por IdNombresDrogas.

    @app.route('/api/publica/pami/afiliado/<numero>/cronicos-sugeridos')
    @requiere_api_key
    def api_publica_pami_cronicos_sugeridos(numero):
        """Devuelve crónicos sugeridos para un afiliado PAMI.

        Path: numero = NumeroAfiliado (13-16 dígitos).
        Query params (todos opcionales):
          ventana_meses  (default 6, max 24)
          min_dispensas  (default 3, min 2)
          cadencia_min   (default 20 días)
          cadencia_max   (default 55 días)
        """
        numero = (numero or '').strip()
        if not numero.isdigit() or len(numero) < 10:
            return jsonify({'error': 'numero de afiliado inválido'}), 400
        try:
            ventana = min(24, max(1, int(request.args.get('ventana_meses') or 6)))
            min_disp = max(2, int(request.args.get('min_dispensas') or 3))
            cad_min = max(1, int(request.args.get('cadencia_min') or 20))
            cad_max = max(cad_min + 1, int(request.args.get('cadencia_max') or 55))
        except (TypeError, ValueError):
            return jsonify({'error': 'parámetros inválidos'}), 400

        from observer_source import _connect, _test_acceso_gestion
        if not _test_acceso_gestion():
            return jsonify({
                'error': 'sin_acceso_premium',
                'mensaje': ('Esta consulta requiere acceso al schema Gestion de Observer '
                            '(user sa). Esta farmacia usa credenciales limitadas (usuarioDW).'),
            }), 501
        conn = _connect(timeout=30)
        if conn is None:
            return jsonify({'error': 'observer no disponible'}), 503
        try:
            with conn.cursor(as_dict=True) as cur:
                # Cabecera: nombre del afiliado + total recetas en la ventana
                cur.execute("""
                    SELECT TOP 1 NombreAfiliado, SexoAfiliado
                      FROM Gestion.Recetas
                     WHERE NumeroAfiliado = %s
                     ORDER BY FechaDeOperacion DESC
                """, (numero,))
                cab = cur.fetchone()
                if not cab:
                    return jsonify({'found': False, 'numero_afiliado': numero,
                                    'mensaje': 'sin recetas en Observer'})

                # Query por droga con cadencia y todas las presentaciones concretas
                cur.execute(f"""
                    SELECT
                        nd.IdNombresDrogas AS id_droga,
                        nd.Descripcion     AS droga,
                        COUNT(DISTINCT r.IdReceta)             AS dispensas,
                        COUNT(DISTINCT rr.IdProducto)          AS n_presentaciones,
                        AVG(CAST(rr.Cantidad AS FLOAT))        AS cant_prom,
                        MAX(rr.DiasTratamiento)                AS dias_tto,
                        MIN(r.FechaDeVenta)                    AS primera,
                        MAX(r.FechaDeVenta)                    AS ultima,
                        DATEDIFF(day, MIN(r.FechaDeVenta), MAX(r.FechaDeVenta)) AS span_dias
                    FROM Gestion.Recetas r
                    JOIN Gestion.RecetasRenglones rr ON rr.IdReceta = r.IdReceta
                    JOIN DW.Productos pr             ON pr.IdProducto = rr.IdProducto
                    JOIN DW.NombresDrogas nd         ON nd.IdNombresDrogas = pr.IdNombresDrogas
                    WHERE r.NumeroAfiliado = %s
                      AND r.Anulada = 0 AND rr.Rechazado = 0
                      AND r.FechaDeOperacion >= DATEADD(month, -{ventana}, GETDATE())
                    GROUP BY nd.IdNombresDrogas, nd.Descripcion
                    HAVING COUNT(DISTINCT r.IdReceta) >= {min_disp}
                    ORDER BY COUNT(DISTINCT r.IdReceta) DESC
                """, (numero,))
                drogas = cur.fetchall()
                if not drogas:
                    return jsonify({
                        'found': True, 'numero_afiliado': numero,
                        'nombre_afiliado': (cab['NombreAfiliado'] or '').strip(),
                        'sexo': cab['SexoAfiliado'],
                        'ventana_meses': ventana,
                        'criterios': {'min_dispensas': min_disp,
                                       'cadencia_min': cad_min,
                                       'cadencia_max': cad_max},
                        'sugeridos': [], 'descartados': [],
                    })

                # Cargar todas las presentaciones (id_producto + nombre) por droga en 1 query
                id_drogas = [d['id_droga'] for d in drogas]
                placeholders = ','.join(['%d'] * len(id_drogas))
                cur.execute(f"""
                    SELECT DISTINCT
                        pr.IdNombresDrogas AS id_droga,
                        pr.IdProducto      AS id_producto,
                        pr.Producto        AS producto
                      FROM Gestion.RecetasRenglones rr
                      JOIN Gestion.Recetas r ON r.IdReceta = rr.IdReceta
                      JOIN DW.Productos pr ON pr.IdProducto = rr.IdProducto
                     WHERE r.NumeroAfiliado = %s
                       AND r.Anulada = 0 AND rr.Rechazado = 0
                       AND r.FechaDeOperacion >= DATEADD(month, -{ventana}, GETDATE())
                       AND pr.IdNombresDrogas IN ({placeholders})
                     ORDER BY pr.IdNombresDrogas, pr.Producto
                """, tuple([numero] + id_drogas))
                pres_por_droga = {}
                for r in cur.fetchall():
                    pres_por_droga.setdefault(r['id_droga'], []).append({
                        'id_producto': r['id_producto'],
                        'producto': (r['producto'] or '').strip(),
                    })
        finally:
            conn.close()

        sugeridos, descartados = [], []
        for d in drogas:
            disp = d['dispensas']
            span = d['span_dias'] or 0
            cad = round(span / max(1, disp - 1), 1) if disp > 1 else None
            item = {
                'id_droga': d['id_droga'],
                'droga': (d['droga'] or '').strip(),
                'dispensas': disp,
                'n_presentaciones': d['n_presentaciones'],
                'cantidad_prom': round(float(d['cant_prom'] or 0), 2),
                'dias_tratamiento_max': d['dias_tto'],
                'cadencia_dias': cad,
                'primera_dispensa': d['primera'].isoformat() if d['primera'] else None,
                'ultima_dispensa': d['ultima'].isoformat() if d['ultima'] else None,
                'presentaciones': pres_por_droga.get(d['id_droga'], []),
            }
            if cad is not None and cad_min <= cad <= cad_max:
                sugeridos.append(item)
            else:
                razon = ('cadencia_muy_rapida' if cad is not None and cad < cad_min
                         else 'cadencia_muy_lenta' if cad is not None else 'una_sola_fecha')
                item['razon_descarte'] = razon
                descartados.append(item)

        return jsonify({
            'found': True,
            'numero_afiliado': numero,
            'nombre_afiliado': (cab['NombreAfiliado'] or '').strip(),
            'sexo': cab['SexoAfiliado'],
            'ventana_meses': ventana,
            'criterios': {'min_dispensas': min_disp,
                           'cadencia_min': cad_min, 'cadencia_max': cad_max},
            'sugeridos': sugeridos,
            'descartados': descartados,
        })
