"""Informe PDF para reunión con gerente de Roemmers. STANDALONE — no toca la app.

Genera /app/informe_roemmers.pdf (= c:\\AppFarmWeb\\informe_roemmers.pdf) con 6 secciones:
  1. Histórico de movimiento (ventas mensuales = proxy de compras)
  2. Mix de productos (ranking por $ y unidades)
  3. Rotación y cobertura (sobre/sub-stock)
  4. Descuentos actuales por droguería
  5. Oportunidades (capital inmovilizado + ventas perdidas)
  6. Proyección de volumen anual

Uso (dentro del contenedor web, que tiene reportlab + acceso a la DB):
    docker-compose exec -T web python scripts/informe_roemmers.py
"""
import os
from datetime import date

import psycopg2
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

LAB_OBSERVER = 152
LAB_NOMBRE = 'Roemmers'

# ── Paleta ──
AZUL = colors.HexColor('#1e3a5f')
VERDE = colors.HexColor('#10b981')
ROJO = colors.HexColor('#ef4444')
AMBAR = colors.HexColor('#f59e0b')
GRIS = colors.HexColor('#6b7280')
GRIS_CLARO = colors.HexColor('#f3f4f6')


def _conn():
    url = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@db:5432/farmacia')
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return psycopg2.connect(url)


def _fmt_money(v):
    """1234567 → $1.234.568"""
    if v is None:
        return '—'
    return '$' + f'{int(round(v)):,}'.replace(',', '.')


def _fmt_num(v):
    if v is None:
        return '—'
    return f'{int(round(v)):,}'.replace(',', '.')


def _mes_label(anio, mes):
    meses = ['', 'Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
    return f'{meses[mes]} {str(anio)[2:]}'


def _ventana_12m(cur):
    """Devuelve (desde_ym, hasta_ym) de los últimos 12 meses con data."""
    cur.execute("""
        SELECT MAX(anio*100+mes) FROM obs_ventas_mensuales vm
        JOIN obs_productos p ON p.observer_id=vm.producto_observer
        WHERE p.laboratorio_observer=%s
    """, (LAB_OBSERVER,))
    hasta = cur.fetchone()[0]
    hy, hm = hasta // 100, hasta % 100
    dm = hm + 1
    dy = hy - 1
    if dm > 12:
        dm -= 12
        dy += 1
    return dy * 100 + dm, hasta


# ───────────────────── secciones ─────────────────────

def seccion_historico(cur, styles, story):
    story.append(Paragraph('1. Histórico de movimiento', styles['h2']))
    story.append(Paragraph(
        'Ventas mensuales de productos Roemmers en la farmacia (unidades, facturación y '
        'transacciones). Proxy directo del volumen de compra requerido al laboratorio.',
        styles['nota']))
    cur.execute("""
        SELECT anio, mes, SUM(unidades), SUM(monto), SUM(transacciones)
        FROM obs_ventas_mensuales vm
        JOIN obs_productos p ON p.observer_id=vm.producto_observer
        WHERE p.laboratorio_observer=%s
        GROUP BY anio, mes ORDER BY anio, mes
    """, (LAB_OBSERVER,))
    rows = cur.fetchall()
    # Solo últimos 13 meses para no saturar.
    rows = rows[-13:]
    data = [['Mes', 'Unidades', 'Facturación', 'Transacc.']]
    max_monto = max((r[3] or 0) for r in rows) or 1
    for anio, mes, u, monto, tx in rows:
        data.append([_mes_label(anio, mes), _fmt_num(u), _fmt_money(monto), _fmt_num(tx)])
    t = Table(data, colWidths=[3 * cm, 3.5 * cm, 5 * cm, 3 * cm])
    t.setStyle(_estilo_tabla())
    story.append(t)
    # Mini barra de tendencia (texto)
    total_u = sum(r[2] or 0 for r in rows)
    total_m = sum(r[3] or 0 for r in rows)
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f'<b>Total período:</b> {_fmt_num(total_u)} unidades · {_fmt_money(total_m)} facturado.',
        styles['nota']))
    # Tendencia: comparar primer trimestre vs último.
    if len(rows) >= 6:
        prim = sum(r[3] or 0 for r in rows[:3]) / 3
        ult = sum(r[3] or 0 for r in rows[-3:]) / 3
        if prim > 0:
            var = (ult - prim) / prim * 100
            signo = '▲' if var >= 0 else '▼'
            col = VERDE if var >= 0 else ROJO
            story.append(Paragraph(
                f'<b>Tendencia (prom. mensual primer trim. vs último):</b> '
                f'<font color="{col.hexval()}">{signo} {abs(var):.0f}%</font>',
                styles['nota']))
    story.append(Spacer(1, 12))


def seccion_mix(cur, styles, story):
    story.append(Paragraph('2. Mix de productos (top 20 por facturación)', styles['h2']))
    story.append(Paragraph(
        'Productos de Roemmers que más mueve la farmacia en los últimos 12 meses. '
        'Concentración del negocio: foco de la negociación.', styles['nota']))
    desde, hasta = _ventana_12m(cur)
    cur.execute("""
        SELECT p.descripcion, SUM(vm.unidades), SUM(vm.monto)
        FROM obs_ventas_mensuales vm
        JOIN obs_productos p ON p.observer_id=vm.producto_observer
        WHERE p.laboratorio_observer=%s AND vm.anio*100+vm.mes BETWEEN %s AND %s
        GROUP BY p.descripcion ORDER BY SUM(vm.monto) DESC LIMIT 20
    """, (LAB_OBSERVER, desde, hasta))
    rows = cur.fetchall()
    total_lab = sum(r[2] or 0 for r in rows)
    data = [['#', 'Producto', 'Unid.', 'Facturación', '% acum.']]
    acum = 0
    for i, (desc, u, monto) in enumerate(rows, 1):
        acum += (monto or 0)
        pct_acum = (acum / total_lab * 100) if total_lab else 0
        data.append([str(i), desc[:38], _fmt_num(u), _fmt_money(monto), f'{pct_acum:.0f}%'])
    t = Table(data, colWidths=[1 * cm, 7.5 * cm, 2.3 * cm, 3.7 * cm, 1.8 * cm])
    t.setStyle(_estilo_tabla(align_desc_col=1))
    story.append(t)
    story.append(Spacer(1, 12))


def seccion_rotacion(cur, styles, story):
    story.append(Paragraph('3. Rotación y cobertura de stock', styles['h2']))
    story.append(Paragraph(
        'Cruce de stock actual vs ritmo de venta (prom. 3 meses). '
        'Sobre-stock = capital inmovilizado; sub-stock = riesgo de quiebre.', styles['nota']))
    desde, hasta = _ventana_12m(cur)
    # avg mensual últimos 3 meses + stock actual
    cur.execute("""
        WITH v3 AS (
            SELECT vm.producto_observer, SUM(vm.unidades)/3.0 AS avg_mes
            FROM obs_ventas_mensuales vm
            JOIN obs_productos p ON p.observer_id=vm.producto_observer
            WHERE p.laboratorio_observer=%s AND vm.anio*100+vm.mes > %s
            GROUP BY vm.producto_observer
        )
        SELECT p.descripcion, COALESCE(s.stock_actual,0), COALESCE(v3.avg_mes,0)
        FROM obs_productos p
        LEFT JOIN obs_stock s ON s.producto_observer=p.observer_id
        LEFT JOIN v3 ON v3.producto_observer=p.observer_id
        WHERE p.laboratorio_observer=%s
          AND (COALESCE(s.stock_actual,0) > 0 OR COALESCE(v3.avg_mes,0) > 0)
    """, (LAB_OBSERVER, (hasta - 3 if hasta % 100 > 3 else hasta - 100 + 9), LAB_OBSERVER))
    rows = cur.fetchall()
    sobre, sub, ok = 0, 0, 0
    sobre_items, sub_items = [], []
    for desc, stock, avg_mes in rows:
        stock = float(stock or 0)
        avg_mes = float(avg_mes or 0)
        avg_dia = avg_mes / 30.42 if avg_mes else 0
        cob = (stock / avg_dia) if avg_dia > 0 else (999 if stock > 0 else 0)
        if avg_dia > 0 and cob > 90:
            sobre += 1
            sobre_items.append((desc, stock, cob))
        elif avg_dia > 0 and cob < 15:
            sub += 1
            sub_items.append((desc, stock, cob, avg_mes))
        else:
            ok += 1
    # Resumen
    data = [['Categoría', 'Productos', 'Lectura']]
    data.append(['🟢 Cobertura OK (15-90 días)', str(ok), 'Stock sano'])
    data.append(['🔴 Sobre-stock (>90 días)', str(sobre), 'Capital inmovilizado'])
    data.append(['🟠 Sub-stock (<15 días)', str(sub), 'Riesgo de quiebre'])
    t = Table(data, colWidths=[7 * cm, 2.5 * cm, 5 * cm])
    t.setStyle(_estilo_tabla())
    story.append(t)
    story.append(Spacer(1, 8))
    # Top sub-stock (lo que hay que reponer ya)
    if sub_items:
        sub_items.sort(key=lambda x: x[3], reverse=True)
        story.append(Paragraph('Productos con riesgo de quiebre (reponer):', styles['nota_b']))
        d2 = [['Producto', 'Stock', 'Cobertura', 'Venta/mes']]
        for desc, stock, cob, avg_mes in sub_items[:8]:
            d2.append([desc[:40], _fmt_num(stock), f'{cob:.0f}d', _fmt_num(avg_mes)])
        t2 = Table(d2, colWidths=[7.5 * cm, 2 * cm, 2.5 * cm, 2.5 * cm])
        t2.setStyle(_estilo_tabla(align_desc_col=0))
        story.append(t2)
    story.append(Spacer(1, 12))


def seccion_descuentos(cur, styles, story):
    story.append(Paragraph('4. Descuentos vigentes por droguería', styles['h2']))
    story.append(Paragraph(
        'Condiciones comerciales actuales para comprar Roemmers a través de cada droguería. '
        'Base de la negociación de mejora.', styles['nota']))
    cur.execute("""
        SELECT pr.razon_social, db.descuento_pct, db.plazo_pago, db.activo
        FROM descuentos_base db
        JOIN proveedores pr ON pr.id=db.drogueria_id
        WHERE db.laboratorio_id=(SELECT id FROM laboratorios WHERE observer_id=%s LIMIT 1)
        ORDER BY db.descuento_pct DESC
    """, (LAB_OBSERVER,))
    rows = cur.fetchall()
    data = [['Droguería', 'Descuento', 'Plazo pago', 'Estado']]
    for razon, pct, plazo, activo in rows:
        data.append([razon, f'{pct:.1f}%', plazo or '—', 'Activo' if activo else 'Inactivo'])
    t = Table(data, colWidths=[6.5 * cm, 2.5 * cm, 3 * cm, 2.5 * cm])
    t.setStyle(_estilo_tabla())
    story.append(t)
    story.append(Spacer(1, 6))
    if rows:
        mejor = rows[0]
        story.append(Paragraph(
            f'<b>Mejor condición actual:</b> {mejor[0]} con {mejor[1]:.1f}% a {mejor[2]}.',
            styles['nota']))
    story.append(Spacer(1, 12))


def seccion_oportunidades(cur, styles, story):
    story.append(Paragraph('5. Oportunidades', styles['h2']))
    desde, hasta = _ventana_12m(cur)
    seis_meses = hasta - 6 if hasta % 100 > 6 else hasta - 100 + 6
    # A) Capital inmovilizado: stock > 0 pero sin ventas en 6m
    cur.execute("""
        SELECT p.descripcion, s.stock_actual
        FROM obs_productos p
        JOIN obs_stock s ON s.producto_observer=p.observer_id
        WHERE p.laboratorio_observer=%s AND s.stock_actual > 0
          AND NOT EXISTS (
            SELECT 1 FROM obs_ventas_mensuales vm
            WHERE vm.producto_observer=p.observer_id AND vm.anio*100+vm.mes > %s
          )
        ORDER BY s.stock_actual DESC LIMIT 10
    """, (LAB_OBSERVER, seis_meses))
    inmov = cur.fetchall()
    story.append(Paragraph('A) Capital inmovilizado (stock sin ventas en 6 meses):', styles['nota_b']))
    if inmov:
        d = [['Producto', 'Stock parado']]
        for desc, stock in inmov:
            d.append([desc[:50], _fmt_num(stock)])
        t = Table(d, colWidths=[10 * cm, 4 * cm])
        t.setStyle(_estilo_tabla(align_desc_col=0))
        story.append(t)
    else:
        story.append(Paragraph('Sin productos con stock parado. 👍', styles['nota']))
    story.append(Spacer(1, 8))
    # B) Ventas perdidas: buena venta histórica pero stock 0
    cur.execute("""
        WITH v AS (
            SELECT vm.producto_observer, SUM(vm.unidades) AS u12, SUM(vm.monto) AS m12
            FROM obs_ventas_mensuales vm
            JOIN obs_productos p ON p.observer_id=vm.producto_observer
            WHERE p.laboratorio_observer=%s AND vm.anio*100+vm.mes BETWEEN %s AND %s
            GROUP BY vm.producto_observer
        )
        SELECT p.descripcion, v.u12, v.m12, COALESCE(s.stock_actual,0)
        FROM v JOIN obs_productos p ON p.observer_id=v.producto_observer
        LEFT JOIN obs_stock s ON s.producto_observer=p.observer_id
        WHERE COALESCE(s.stock_actual,0)=0 AND v.u12 > 0
        ORDER BY v.m12 DESC LIMIT 10
    """, (LAB_OBSERVER, desde, hasta))
    perdidas = cur.fetchall()
    story.append(Paragraph('B) Ventas en riesgo (demanda histórica con stock 0 hoy):', styles['nota_b']))
    if perdidas:
        d = [['Producto', 'Vendido 12m', 'Facturación 12m']]
        for desc, u12, m12, _ in perdidas:
            d.append([desc[:42], _fmt_num(u12), _fmt_money(m12)])
        t = Table(d, colWidths=[8 * cm, 3 * cm, 3 * cm])
        t.setStyle(_estilo_tabla(align_desc_col=0))
        story.append(t)
    else:
        story.append(Paragraph('Sin quiebres relevantes. 👍', styles['nota']))
    story.append(Spacer(1, 12))


def seccion_proyeccion(cur, styles, story):
    story.append(Paragraph('6. Proyección de volumen anual', styles['h2']))
    story.append(Paragraph(
        'Volumen anualizado en base a los últimos 12 meses y proyección con la tendencia '
        'reciente. Sirve para dimensionar un acuerdo de compra por volumen.', styles['nota']))
    desde, hasta = _ventana_12m(cur)
    cur.execute("""
        SELECT SUM(vm.unidades), SUM(vm.monto)
        FROM obs_ventas_mensuales vm
        JOIN obs_productos p ON p.observer_id=vm.producto_observer
        WHERE p.laboratorio_observer=%s AND vm.anio*100+vm.mes BETWEEN %s AND %s
    """, (LAB_OBSERVER, desde, hasta))
    u12, m12 = cur.fetchone()
    u12, m12 = float(u12 or 0), float(m12 or 0)
    # Tendencia: prom últimos 3 vs 3 anteriores.
    cur.execute("""
        SELECT anio, mes, SUM(monto)
        FROM obs_ventas_mensuales vm
        JOIN obs_productos p ON p.observer_id=vm.producto_observer
        WHERE p.laboratorio_observer=%s AND vm.anio*100+vm.mes BETWEEN %s AND %s
        GROUP BY anio, mes ORDER BY anio, mes
    """, (LAB_OBSERVER, desde, hasta))
    serie = [float(r[2] or 0) for r in cur.fetchall()]
    factor = 1.0
    if len(serie) >= 6:
        ult3 = sum(serie[-3:]) / 3
        ant3 = sum(serie[-6:-3]) / 3
        if ant3 > 0:
            factor = ult3 / ant3
    proy_m = m12 * factor
    proy_u = u12 * factor
    data = [
        ['Métrica', 'Últimos 12m', 'Proyección 12m'],
        ['Unidades', _fmt_num(u12), _fmt_num(proy_u)],
        ['Facturación', _fmt_money(m12), _fmt_money(proy_m)],
    ]
    t = Table(data, colWidths=[4 * cm, 5 * cm, 5 * cm])
    t.setStyle(_estilo_tabla())
    story.append(t)
    story.append(Spacer(1, 8))
    # Estimación de compra (a PVP, descontando mejor descuento)
    cur.execute("""
        SELECT MAX(db.descuento_pct) FROM descuentos_base db
        WHERE db.laboratorio_id=(SELECT id FROM laboratorios WHERE observer_id=%s LIMIT 1)
          AND db.activo
    """, (LAB_OBSERVER,))
    mejor_desc = float(cur.fetchone()[0] or 0)
    # La facturación es a PVP. El costo de compra ≈ PVP × (1 - margen_farmacia).
    # Sin margen exacto, mostramos el monto a PVP y el ahorro por el descuento.
    story.append(Paragraph(
        f'<b>Volumen anual proyectado a PVP:</b> {_fmt_money(proy_m)}.<br/>'
        f'<b>Mejor descuento vigente:</b> {mejor_desc:.1f}% — sobre ese volumen de compra '
        f'representa un ahorro relevante; cada punto adicional de descuento es '
        f'~{_fmt_money(proy_m * 0.01)} sobre el equivalente a PVP.',
        styles['nota']))
    story.append(Spacer(1, 12))


def seccion_competitiva(cur, styles, story):
    story.append(Paragraph('7. Posición competitiva por molécula', styles['h2']))
    story.append(Paragraph(
        'Para cada molécula que Roemmers ofrece, se compara cuánto vende la farmacia de '
        'la marca Roemmers vs. competidores de otros laboratorios de la misma droga. '
        'Identifica dónde Roemmers lidera (defender) y dónde está rezagado pese a haber '
        'mercado (potenciar — ej. Coroval vs Arteriosan).', styles['nota']))
    desde, hasta = _ventana_12m(cur)
    cur.execute("""
        SELECT nd.descripcion AS molecula, l.descripcion AS lab,
               SUM(vm.monto) AS monto, SUM(vm.unidades) AS u
        FROM obs_ventas_mensuales vm
        JOIN obs_productos p ON p.observer_id=vm.producto_observer
        JOIN obs_nombres_drogas nd ON nd.observer_id=p.nombre_droga_observer
        JOIN obs_laboratorios l ON l.observer_id=p.laboratorio_observer
        WHERE p.nombre_droga_observer IN (
            SELECT DISTINCT nombre_droga_observer FROM obs_productos
            WHERE laboratorio_observer=%s AND nombre_droga_observer IS NOT NULL)
          AND vm.anio*100+vm.mes BETWEEN %s AND %s
        GROUP BY nd.descripcion, l.descripcion
    """, (LAB_OBSERVER, desde, hasta))
    # Agrupar por molécula en Python.
    molec = {}  # molecula -> {'total': $, 'roem': $, 'labs': {lab: $}}
    for molecula, lab, monto, u in cur.fetchall():
        monto = float(monto or 0)
        d = molec.setdefault(molecula, {'total': 0.0, 'roem': 0.0, 'labs': {}})
        d['total'] += monto
        d['labs'][lab] = d['labs'].get(lab, 0.0) + monto
        if lab == LAB_NOMBRE:
            d['roem'] += monto

    filas = []
    for m, d in molec.items():
        if d['total'] <= 0:
            continue
        share = d['roem'] / d['total'] * 100
        # competidor líder (lab que más vende excluyendo Roemmers)
        comp = sorted(((lab, v) for lab, v in d['labs'].items() if lab != LAB_NOMBRE),
                      key=lambda x: x[1], reverse=True)
        comp_lider = comp[0] if comp else None
        filas.append({'molecula': m, 'share': share, 'roem': d['roem'],
                      'total': d['total'], 'comp': comp_lider})

    # LÍDER: share >= 50% y volumen relevante
    lideres = sorted([f for f in filas if f['share'] >= 50 and f['total'] > 0],
                     key=lambda x: x['roem'], reverse=True)[:10]
    # OPORTUNIDAD: share < 40%, mercado grande (hay plata en juego)
    oport = sorted([f for f in filas if f['share'] < 40 and f['comp']],
                   key=lambda x: x['total'], reverse=True)[:12]

    story.append(Paragraph('A) Moléculas donde Roemmers LIDERA (defender posición):', styles['nota_b']))
    if lideres:
        d = [['Molécula', 'Share Roem.', '$ Roemmers', '$ Mercado']]
        for f in lideres:
            d.append([f['molecula'][:34], f'{f["share"]:.0f}%',
                      _fmt_money(f['roem']), _fmt_money(f['total'])])
        t = Table(d, colWidths=[6.5 * cm, 2.3 * cm, 3.3 * cm, 3.3 * cm])
        t.setStyle(_estilo_tabla(align_desc_col=0))
        story.append(t)
    else:
        story.append(Paragraph('—', styles['nota']))
    story.append(Spacer(1, 8))

    story.append(Paragraph(
        'B) Oportunidades — molécula con mercado pero Roemmers rezagado (potenciar):',
        styles['nota_b']))
    if oport:
        d = [['Molécula', 'Share Roem.', '$ Roemmers', 'Competidor líder', '$ Comp.']]
        for f in oport:
            comp_lab, comp_monto = f['comp']
            d.append([f['molecula'][:26], f'{f["share"]:.0f}%', _fmt_money(f['roem']),
                      comp_lab[:16], _fmt_money(comp_monto)])
        t = Table(d, colWidths=[4.8 * cm, 1.8 * cm, 3 * cm, 3 * cm, 2.8 * cm])
        t.setStyle(_estilo_tabla(align_desc_col=0))
        story.append(t)
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            'Lectura: en estas moléculas la demanda existe pero se la lleva la competencia. '
            'Margen para crecer con Roemmers vía descuento, canje o acción comercial.',
            styles['nota']))
    else:
        story.append(Paragraph('—', styles['nota']))
    story.append(Spacer(1, 12))


# ───────────────────── estilos ─────────────────────

def _estilo_tabla(align_desc_col=None):
    cmds = [
        ('BACKGROUND', (0, 0), (-1, 0), AZUL),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, GRIS_CLARO]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d1d5db')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
    ]
    if align_desc_col is not None:
        cmds.append(('ALIGN', (align_desc_col, 1), (align_desc_col, -1), 'LEFT'))
    return TableStyle(cmds)


def main():
    out = '/app/informe_roemmers.pdf'
    doc = SimpleDocTemplate(out, pagesize=A4,
                            topMargin=1.5 * cm, bottomMargin=1.5 * cm,
                            leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                            title=f'Informe {LAB_NOMBRE}')
    base = getSampleStyleSheet()
    styles = {
        'h1': ParagraphStyle('h1', parent=base['Title'], fontSize=20, textColor=AZUL,
                             spaceAfter=4),
        'h2': ParagraphStyle('h2', parent=base['Heading2'], fontSize=13, textColor=AZUL,
                             spaceBefore=8, spaceAfter=4),
        'nota': ParagraphStyle('nota', parent=base['Normal'], fontSize=8.5, textColor=GRIS,
                               spaceAfter=4, leading=11),
        'nota_b': ParagraphStyle('nota_b', parent=base['Normal'], fontSize=9,
                                 textColor=AZUL, spaceAfter=3, fontName='Helvetica-Bold'),
        'sub': ParagraphStyle('sub', parent=base['Normal'], fontSize=9, textColor=GRIS),
    }
    story = []
    story.append(Paragraph(f'Informe comercial — {LAB_NOMBRE}', styles['h1']))
    story.append(Paragraph(
        f'Generado {date.today().strftime("%d/%m/%Y")} · Datos: ventas, stock y condiciones '
        f'de la farmacia · Uso interno para reunión con el laboratorio.', styles['sub']))
    story.append(Spacer(1, 12))

    with _conn() as conn:
        with conn.cursor() as cur:
            seccion_historico(cur, styles, story)
            seccion_mix(cur, styles, story)
            story.append(PageBreak())
            seccion_rotacion(cur, styles, story)
            seccion_descuentos(cur, styles, story)
            story.append(PageBreak())
            seccion_oportunidades(cur, styles, story)
            seccion_proyeccion(cur, styles, story)
            story.append(PageBreak())
            seccion_competitiva(cur, styles, story)

    doc.build(story)
    print(f'PDF generado: {out}')


if __name__ == '__main__':
    main()
