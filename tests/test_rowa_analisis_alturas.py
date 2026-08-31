"""analizar_alturas(): cruce de distribución de alturas con rotación real.

Sin `filas_analizadas` se comporta como antes (solo distribución/cobertura).
Con `filas_analizadas` (las ArticuloAnalizado ya cruzadas con ObServer), cada
separación candidata debe sumar qué artículos de rotación Alta/Media quedan
afuera del canal y cuánto movimiento mensual representan — para no tratar
igual a un durmiente que a algo que se vende todos los días.
"""
from services.rowa_analisis import ArticuloAnalizado, analizar_alturas
from services.rowa_client import Article, Pack


def _pack(article_id, height_mm):
    return Pack(article_id=article_id, pack_id=f'{article_id}-p', height_mm=height_mm,
                width_mm=50, depth_mm=50)


def _articulo(article_id, alturas_mm):
    return Article(article_id=article_id, name=article_id,
                   packs=[_pack(article_id, h) for h in alturas_mm])


def _fila(article_id, rotacion, unid_mes_est, nombre=None):
    return ArticuloAnalizado(
        article_id=article_id, ean=None, nombre=nombre or article_id,
        cantidad=1, rotacion=rotacion, unid_mes_est=unid_mes_est,
        antig_prom_d=10, antig_max_d=10, vol_unit_cm3=100.0, vol_total_cm3=100.0,
        prox_venc=None, dias_prox_venc=None, recomendacion='OK mantener',
        sug_en_robot=1, al_deposito=0,
    )


def test_sin_filas_no_calcula_riesgo():
    """Backward-compat: sin filas_analizadas, no aparece la info de riesgo."""
    articulos = [_articulo('A1', [70]), _articulo('A2', [90])]
    r = analizar_alturas(articulos)
    assert r['con_movimiento'] is False
    assert 'riesgo_n' not in r['rendimiento'][0]


def test_riesgo_solo_cuenta_alta_media():
    """A2 (90mm) no entra en el canal de 80mm. Si es Baja/Durmiente no cuenta
    como riesgo (poco importa); si es Alta/Media, sí."""
    articulos = [_articulo('A1', [70]), _articulo('A2', [90])]
    filas = [_fila('A1', 'Alta', 20.0), _fila('A2', 'Baja', 5.0)]
    r = analizar_alturas(articulos, filas_analizadas=filas)
    fila_80 = next(x for x in r['rendimiento'] if x['canal_mm'] == 80)
    # A2 (90mm) no entra en 80mm, pero es Baja -> no es "riesgo".
    assert fila_80['riesgo_n'] == 0
    assert fila_80['riesgo_movimiento_mes'] == 0.0

    fila_60 = next(x for x in r['rendimiento'] if x['canal_mm'] == 60)
    # A1 (70mm) tampoco entra en 60mm, y es Alta -> sí es riesgo.
    assert fila_60['riesgo_n'] == 1
    assert fila_60['riesgo_movimiento_mes'] == 20.0
    assert fila_60['riesgo_top'][0]['nombre'] == 'A1'


def test_articulo_con_packs_de_distinta_altura_usa_el_maximo():
    """Un articulo con un pack de 40mm y otro de 90mm (lote/proveedor
    distinto) no entra en un canal de 80mm: manda el pack MAS ALTO."""
    articulos = [_articulo('MIX', [40, 90])]
    filas = [_fila('MIX', 'Alta', 15.0)]
    r = analizar_alturas(articulos, filas_analizadas=filas)
    fila_80 = next(x for x in r['rendimiento'] if x['canal_mm'] == 80)
    assert fila_80['riesgo_n'] == 1
    assert fila_80['riesgo_top'][0]['altura_mm'] == 90


def test_riesgo_top_ordenado_por_movimiento_desc_y_capado():
    articulos = [_articulo(f'A{i}', [90]) for i in range(10)]
    filas = [_fila(f'A{i}', 'Alta', float(i)) for i in range(10)]
    r = analizar_alturas(articulos, filas_analizadas=filas)
    fila_80 = next(x for x in r['rendimiento'] if x['canal_mm'] == 80)
    assert fila_80['riesgo_n'] == 10
    assert len(fila_80['riesgo_top']) == 8          # capado a 8
    unidades = [a['unid_mes'] for a in fila_80['riesgo_top']]
    assert unidades == sorted(unidades, reverse=True)
    assert unidades[0] == 9.0                        # el de mas movimiento primero


def test_con_movimiento_true_cuando_hay_filas():
    articulos = [_articulo('A1', [70])]
    filas = [_fila('A1', 'Alta', 5.0)]
    r = analizar_alturas(articulos, filas_analizadas=filas)
    assert r['con_movimiento'] is True
