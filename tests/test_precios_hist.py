"""El snapshot de precio se escribe desde los caminos de Kellerhoff.

Hasta ahora sólo lo escribían las importaciones de archivo/PDF de
`routes/converter.py`: una compra cargada por el portal no dejaba histórico y el
precio público del día se perdía sin forma de reconstruirlo.
"""
from datetime import date

import database
from database import Invoice, ProductoPrecioHist
from services import precios_hist

_ID = [0]


def _factura(s, numero='0046-00374111', fecha=date(2026, 9, 3), tipo='FAC'):
    prov = database.Provider(razon_social='DROGUERIA KELLERHOFF S.A.',
                              cuit='30539756490')
    s.add(prov)
    s.flush()
    inv = Invoice(numero_factura=numero, fecha=fecha, tipo_comprobante=tipo,
                  proveedor_razon='DROGUERIA KELLERHOFF S.A.',
                  proveedor_cuit='30539756490', proveedor_id=prov.id, total=1000)
    s.add(inv)
    s.flush()
    return inv


def test_registra_el_snapshot_con_los_datos_de_la_factura():
    s = database.SessionLocal()
    try:
        inv = _factura(s)
        assert precios_hist.registrar(
            s, inv, codigo_barra='7798058930969', precio_publico=470174,
            dto_pct=33.41, precio_unitario=313089, importe=626178) is True
        s.commit()

        h = s.query(ProductoPrecioHist).one()
        assert h.codigo_barra == '7798058930969'
        assert float(h.precio_publico) == 470174        # lo que se perdía
        assert float(h.precio_unitario) == 313089
        # Proveedor y fecha salen de la factura, no del llamador.
        assert h.proveedor_id == inv.proveedor_id
        assert h.proveedor_razon == 'DROGUERIA KELLERHOFF S.A.'
        assert h.fecha == date(2026, 9, 3)
        assert h.tipo_comprobante == 'FAC'
        assert h.factura_id == inv.id
    finally:
        s.close()


def test_una_nota_de_credito_queda_marcada_como_tal():
    """Si no se distinguiera, una devolución se leería como una compra más."""
    s = database.SessionLocal()
    try:
        inv = _factura(s, numero='NC-1', tipo='NCR')
        precios_hist.registrar(s, inv, codigo_barra='111', precio_unitario=500)
        s.commit()
        assert s.query(ProductoPrecioHist).one().tipo_comprobante == 'NCR'
    finally:
        s.close()


def test_descarta_lo_que_no_se_puede_ubicar():
    """Sin código de barras o sin fecha la fila no es buscable ni ubicable."""
    s = database.SessionLocal()
    try:
        inv = _factura(s)
        assert precios_hist.registrar(s, inv, codigo_barra='') is False
        assert precios_hist.registrar(s, inv, codigo_barra='   ') is False
        assert precios_hist.registrar(s, inv, codigo_barra=None) is False

        # `facturas.fecha` es NOT NULL en la base, así que este caso sólo se da
        # con una factura todavía sin persistir: la guarda igual tiene que estar,
        # porque el histórico sin fecha no se puede ubicar en el tiempo.
        sin_fecha = Invoice(numero_factura='X-1', fecha=None,
                            tipo_comprobante='FAC', proveedor_razon='X')
        assert precios_hist.registrar(s, sin_fecha, codigo_barra='111') is False
        s.commit()
        assert s.query(ProductoPrecioHist).count() == 0
    finally:
        s.close()


# ── El camino del portal de Kellerhoff ───────────────────────────────────────

def test_el_sync_del_portal_guarda_el_precio_publico():
    from routes.kellerhoff_sync import _crear_items
    s = database.SessionLocal()
    try:
        inv = _factura(s)
        _crear_items(s, inv, [{
            'barcode': '7798058930969', 'descripcion': 'NOVORAPID FLEXPEN',
            'cantidad': 2, 'precio_pub': 470174, 'dto_pct': 33.41,
            'precio_unitario': 313089, 'importe': 626178,
        }])
        s.commit()

        h = s.query(ProductoPrecioHist).one()
        assert float(h.precio_publico) == 470174
        assert float(h.precio_unitario) == 313089
        assert h.proveedor_id == inv.proveedor_id
    finally:
        s.close()


def test_el_portal_sin_dto_igual_deja_el_precio_publico():
    """La tabla HTML del portal no trae DTO (`dto_pct` viene None): eso no puede
    impedir que se guarde el precio público, que es el dato irrecuperable."""
    from routes.kellerhoff_sync import _crear_items
    s = database.SessionLocal()
    try:
        inv = _factura(s)
        _crear_items(s, inv, [{
            'barcode': '111', 'descripcion': 'X', 'cantidad': 1,
            'precio_pub': 1000, 'dto_pct': None,
            'precio_unitario': 700, 'importe': 700,
        }])
        s.commit()
        h = s.query(ProductoPrecioHist).one()
        assert float(h.precio_publico) == 1000
        assert h.dto_pct is None
    finally:
        s.close()


def test_un_renglon_sin_codigo_de_barras_no_frena_a_los_demas():
    from routes.kellerhoff_sync import _crear_items
    s = database.SessionLocal()
    try:
        inv = _factura(s)
        _crear_items(s, inv, [
            {'barcode': '', 'descripcion': 'SIN EAN', 'cantidad': 1,
             'precio_pub': 500, 'precio_unitario': 400, 'importe': 400},
            {'barcode': '222', 'descripcion': 'CON EAN', 'cantidad': 1,
             'precio_pub': 900, 'precio_unitario': 600, 'importe': 600},
        ])
        s.commit()
        # Los dos renglones se crean; sólo el que tiene EAN deja histórico.
        assert s.query(database.InvoiceItem).count() == 2
        assert s.query(ProductoPrecioHist).one().codigo_barra == '222'
    finally:
        s.close()


# ── Backfill de lo ya cargado ────────────────────────────────────────────────

def test_backfill_llena_lo_que_falta_y_es_idempotente():
    from scripts.backfill_precios_hist import backfill
    s = database.SessionLocal()
    try:
        inv = _factura(s)
        s.add(database.InvoiceItem(factura_id=inv.id, codigo_barra='111',
                                   descripcion='X', cantidad=2,
                                   precio_unitario=313089, dto=33.41, importe=626178))
        s.commit()

        assert backfill(s)['escritos'] == 1
        h = s.query(ProductoPrecioHist).one()
        assert float(h.precio_unitario) == 313089
        assert h.precio_publico is None       # no se inventa: no se observó

        # Segunda corrida: no duplica (la tabla es append-only, la guarda es del script).
        r = backfill(s)
        assert r['escritos'] == 0 and r['saltados'] == 1
        assert s.query(ProductoPrecioHist).count() == 1
    finally:
        s.close()


def test_backfill_puede_derivar_el_pvp_desde_el_descuento():
    """313.089,19 / (1 - 0,3341) = 470.174 — verificado contra una factura real."""
    from scripts.backfill_precios_hist import backfill
    s = database.SessionLocal()
    try:
        inv = _factura(s)
        s.add(database.InvoiceItem(factura_id=inv.id, codigo_barra='111',
                                   descripcion='X', cantidad=2,
                                   precio_unitario=313089.19, dto=33.41, importe=626178))
        s.commit()
        backfill(s, derivar_pvp=True)
        assert abs(float(s.query(ProductoPrecioHist).one().precio_publico) - 470174) < 2
    finally:
        s.close()


def test_sin_descuento_no_se_deriva_un_pvp_falso():
    """Con dto nulo o 0, derivar daría el precio unitario como si fuera el PVP."""
    from scripts.backfill_precios_hist import _pvp_derivado
    assert _pvp_derivado(1000, None) is None
    assert _pvp_derivado(1000, 0) is None
    assert _pvp_derivado(None, 30) is None
    assert _pvp_derivado(1000, 100) is None      # division por cero
    assert abs(_pvp_derivado(700, 30) - 1000) < 0.01


def test_backfill_dry_run_no_escribe():
    from scripts.backfill_precios_hist import backfill
    s = database.SessionLocal()
    try:
        inv = _factura(s)
        s.add(database.InvoiceItem(factura_id=inv.id, codigo_barra='111',
                                   descripcion='X', cantidad=1,
                                   precio_unitario=100, importe=100))
        s.commit()
        assert backfill(s, dry_run=True)['escritos'] == 1
        assert s.query(ProductoPrecioHist).count() == 0
    finally:
        s.close()
