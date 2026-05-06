"""Genera el PDF con la propuesta REAL post-descubrimiento del schema completo
(ObServerGestion: 255 tablas en Gestion + Generales).

Uso:
    docker-compose exec web python scripts/generar_propuesta_observer_pdf.py

Output:
    docs/propuesta_observer_features.pdf
"""
import os
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

OUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'docs', 'propuesta_observer_features.pdf'
)


# ─── Styles ────────────────────────────────────────────────────────────
styles = getSampleStyleSheet()
TITULO = ParagraphStyle('Titulo', parent=styles['Title'],
    fontSize=22, leading=26, textColor=colors.HexColor('#1c1c1e'),
    spaceAfter=4, alignment=TA_LEFT, fontName='Helvetica-Bold')
SUBTITULO = ParagraphStyle('Subtitulo', parent=styles['Normal'],
    fontSize=11, textColor=colors.HexColor('#6f6f6f'), spaceAfter=14)
H2 = ParagraphStyle('H2', parent=styles['Heading2'],
    fontSize=16, leading=22, textColor=colors.HexColor('#1c1c1e'),
    spaceBefore=18, spaceAfter=8, fontName='Helvetica-Bold')
H3_URG = ParagraphStyle('H3Urg', parent=styles['Heading3'],
    fontSize=13, leading=17, textColor=colors.HexColor('#b91c1c'),
    spaceBefore=12, spaceAfter=4, fontName='Helvetica-Bold')
H3_MID = ParagraphStyle('H3Mid', parent=styles['Heading3'],
    fontSize=13, leading=17, textColor=colors.HexColor('#b45309'),
    spaceBefore=12, spaceAfter=4, fontName='Helvetica-Bold')
H3_LOW = ParagraphStyle('H3Low', parent=styles['Heading3'],
    fontSize=13, leading=17, textColor=colors.HexColor('#3f6650'),
    spaceBefore=12, spaceAfter=4, fontName='Helvetica-Bold')
BODY = ParagraphStyle('Body', parent=styles['Normal'],
    fontSize=10, leading=14, textColor=colors.HexColor('#1e1e1e'),
    spaceAfter=4)
BULLET = ParagraphStyle('Bullet', parent=BODY,
    leftIndent=14, bulletIndent=4, spaceAfter=2, fontSize=10, leading=13)
META = ParagraphStyle('Meta', parent=styles['Normal'],
    fontSize=9, textColor=colors.HexColor('#6f6f6f'),
    spaceAfter=2, fontName='Helvetica-Oblique')
NOTA = ParagraphStyle('Nota', parent=styles['Normal'],
    fontSize=10, textColor=colors.HexColor('#1e1e1e'),
    backColor=colors.HexColor('#FEF3C7'),
    borderPadding=8, borderColor=colors.HexColor('#F59E0B'),
    borderWidth=1, spaceBefore=6, spaceAfter=10, leading=14)


def b(txt):
    return Paragraph(f'• {txt}', BULLET)


def tabla_simple(data, col_widths=None, header_color='#1c1c1e', header_text='#EAB308'):
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor(header_color)),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.HexColor(header_text)),
        ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0,0), (-1,-1), 9),
        ('VALIGN',     (0,0), (-1,-1), 'TOP'),
        ('ALIGN',      (0,0), (-1,-1), 'LEFT'),
        ('GRID',       (0,0), (-1,-1), 0.5, colors.HexColor('#cccccc')),
        ('LEFTPADDING',(0,0), (-1,-1), 6),
        ('RIGHTPADDING',(0,0), (-1,-1), 6),
        ('TOPPADDING',  (0,0), (-1,-1), 5),
        ('BOTTOMPADDING',(0,0), (-1,-1), 5),
        ('ROWBACKGROUNDS', (0,1), (-1,-1),
         [colors.white, colors.HexColor('#fafafa')]),
    ]))
    return t


# ─── Construcción del PDF ──────────────────────────────────────────────
def construir():
    doc = SimpleDocTemplate(
        OUT_PATH, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
        title='Propuesta — Features Observer (post-descubrimiento)',
        author='AppFarmWeb',
    )
    elems = []

    # ═══════════ Cover ═══════════
    elems.append(Paragraph('Observer DB — Mapa completo y propuesta de features', TITULO))
    elems.append(Paragraph(
        f'DB <b>ObServerGestion</b> · 255 tablas en <code>Gestion.*</code> + <code>Generales.*</code> · '
        f'~360 foreign keys · Generado {datetime.now().strftime("%d/%m/%Y")}.',
        SUBTITULO))
    elems.append(HRFlowable(width='100%', thickness=1, color=colors.HexColor('#e0e0e0'),
                             spaceBefore=0, spaceAfter=12))

    # ═══════════ Resumen ejecutivo ═══════════
    elems.append(Paragraph('Resumen ejecutivo', H2))
    elems.append(Paragraph(
        'La vista <code>DW.*</code> que veníamos sincronizando (29 tablas) es solo una '
        'representación denormalizada del schema operacional real <code>ObServerGestion.*</code>. '
        'Tras explorar la base con el query playground SQL, descubrimos:',
        BODY))
    for x in [
        '<b>255 tablas reales</b> en los schemas <code>Gestion.*</code> (operacional) y <code>Generales.*</code> (lookups).',
        '<b>Cierres de caja completos disponibles</b> (CajasMostradorCierres + Movimientos + Tarjetas) — no hay que pedir nada nuevo.',
        '<b>Kardex completo en MovimientosStock</b> (4.1M filas) + AjustesStock con motivos.',
        '<b>Operaciones granuladas</b>: Operaciones (2.2M) + Renglones (3.8M) + Pagos (2.4M) + Cajas + Planes.',
        '<b>Cuenta corriente clientes</b> (~150K filas en total) — gestión de cobranzas automatizable.',
        '<b>Clasificación farmacológica oficial ATC + Acciones Terapéuticas</b> ya cargada.',
        '<b>360 foreign keys explícitas</b> — el modelo está bien normalizado, los joins son obvios.',
    ]:
        elems.append(b(x))

    elems.append(Spacer(1, 6))
    elems.append(Paragraph(
        '<b>La propuesta original (que asumía datos faltantes) cae:</b> casi todo lo que '
        'imaginábamos pedir ya existe. El trabajo es <b>sincronizar las tablas correctas</b> '
        'y <b>construir features encima</b>.',
        NOTA))

    # ═══════════ Estado actual ═══════════
    elems.append(Paragraph('Lo que ya tenemos sincronizado (15 entidades)', H2))
    elems.append(Paragraph(
        'Vía <code>obs_*</code> en nuestra DB local: laboratorios, rubros, subrubros, '
        'productos, stock, ventas mensuales, ventas detalle (con tipo_operacion='
        'V/D/NC ya filtrado), clientes (+ grupos/categorías), obras sociales (+ convenios/'
        'planes), médicos (+ matrículas, colegios), nombres drogas. Todas vía '
        '<code>DW.*</code>; algunas están más completas en <code>Gestion.*</code> '
        '(ver siguiente sección).',
        BODY))

    # ═══════════ Mapa funcional ═══════════
    elems.append(PageBreak())
    elems.append(Paragraph('Mapa del schema por área funcional', H2))
    elems.append(Paragraph(
        'Las 255 tablas agrupadas por dominio. Numeros entre paréntesis = filas.',
        BODY))

    # Mapa por áreas
    areas = [
        ('🏪 Operaciones / Ventas (core)',
         '#fef3c7',
         [
             ('Operaciones', '2.2M', 'Cabecera de cada venta'),
             ('OperacionesRenglones', '3.8M', 'Detalle: 1 fila por producto'),
             ('OperacionesPagos', '2.4M', 'Pagos de la operación: medio + monto'),
             ('OperacionesRecetas', '721K', 'Si hay receta, link y plan'),
             ('OperacionesRenglonesCajas', '—', 'Detalle por caja (devuelta?)'),
             ('OperacionesRenglonesPlanes', '953K', 'Descuento aplicado por plan'),
             ('GruposOperaciones', '1.7M', 'Agrupa para facturas/devoluciones'),
         ]),
        ('📦 Stock / Movimientos / Kardex',
         '#dbeafe',
         [
             ('MovimientosStock', '4.1M', 'KARDEX completo con signos. Crítico.'),
             ('MovimientosStockProductosCajas', '—', 'Mov por caja (auditoría)'),
             ('AjustesStock', '—', 'Ajustes manuales (mermas, vencidos)'),
             ('AjustesStockProductos', '95K', 'Detalle de ajuste'),
             ('MotivosAjustesStock', '—', 'Lookup motivos'),
             ('IngresosEgresosMercaderia', '105K', 'Compras + devoluciones a prov.'),
             ('IngresosEgresosMercaderiaRenglones', '748K', 'Detalle ingreso'),
             ('StockFarmaciasProductos', '67K', 'Stock actual (ya sync)'),
             ('ProductosAPedir', '—', 'Sugerencias del propio Observer'),
             ('RegistrosDeReposiciones', '—', 'Historial de reposiciones'),
         ]),
        ('💰 Cierre de Caja / Pagos electrónicos',
         '#fee2e2',
         [
             ('CajasMostradorCierres', '—', 'Cierre por turno/caja'),
             ('CajasMostradorMovimientos', '173K', 'Apertura + cierre + movs caja'),
             ('CajasMostradorCierresComprobantes', '—', 'Cuáles comprobantes cubren'),
             ('CajasMostradorCierresControl', '—', 'Diferencias contra esperado'),
             ('CajasMostradorCierresImpresoras', '—', 'Z report fiscal'),
             ('CuponTarjeta', '373K', 'Cupones POS individuales'),
             ('TarjetaCierres', '—', 'Cierre lote por marca'),
             ('Tarjetas', '—', 'Lookup: Visa, Master, Amex, etc.'),
             ('TiposFormaDePago', '—', 'Efectivo, Tarjeta, Transf, Cheque'),
             ('Bancos', '—', 'Lookup bancos'),
             ('Cheques', '—', 'Cheques recibidos'),
         ]),
        ('💊 Productos / Drogas / Vademécum',
         '#dcfce7',
         [
             ('Productos', '123K', 'Master de productos (ya sync vía obs_productos)'),
             ('ProductosCodigosBarras', '131K', 'EANs por producto (1→N)'),
             ('ProductosCodigosGTIN', '54K', 'GTIN-14 para trazabilidad'),
             ('ProductosPrecios', '3.8M', 'Histórico precios (todos los cambios)'),
             ('ProductosPreciosVigentes', '—', 'Precios actuales (rápido)'),
             ('ProductosPreciosVigentesProveedores', '—', 'Precio por proveedor'),
             ('ProductosDrogas', '59K', 'Producto → droga(s)'),
             ('ProductosAccionesTerapeuticas', '58K', 'Producto → ATC'),
             ('Drogas + NombresDrogas + Presentaciones', '—', 'Catálogo drogas'),
             ('ATC + AccionesTerapeuticas', '—', 'Clasif. farmacológica internacional'),
             ('FormasFarmaceuticas + Vías', '—', 'CPR, INY, JBE, ORAL, IV...'),
             ('TiposVentaYControl', '—', 'Libre/Recetario/Psicotrópico/Estupef.'),
             ('Vademecums + VademecumsProductos', '4.0M', 'Vademécum por OS/plan'),
             ('PreciosReferenciaPorDroga', '—', 'Referencia tipo alfabeta'),
         ]),
        ('🏥 Recetas / Obras Sociales',
         '#f3e8ff',
         [
             ('Recetas', '842K', 'Cabecera de receta'),
             ('RecetasRenglones', '1.1M', 'Productos prescriptos'),
             ('RecetasPagos', '740K', 'Pago de la receta'),
             ('RecetasPrescripciones', '—', 'Diagnóstico, dosis'),
             ('ObrasSociales', '—', 'OS (ya sync)'),
             ('Convenios + ConveniosFarmacias', '—', 'OS x farmacia'),
             ('ConveniosPatologias + Drogas + ProductosPropios', '—', 'Restricciones de convenio'),
             ('Planes + restricciones', '—', '5 tablas de restricciones por plan'),
             ('Cartillas + CartillasMedicos', '—', 'Cartillas habilitadas'),
             ('Patologias + CIE10 + Drogas', '—', 'Diagnósticos catalogados'),
         ]),
        ('💳 Cuenta Corriente Clientes',
         '#fef3c7',
         [
             ('CtaCteVentas', '—', 'Ventas a cuenta'),
             ('CtaCteRenglones', '54K', 'Detalle ítems CC'),
             ('CtaCtePagos', '105K', 'Cobranzas'),
             ('CtaCteRecibos', '—', 'Recibos emitidos'),
             ('CtaCteCreditosDebitos', '—', 'NC/ND'),
             ('CtaCteImputaciones', '—', 'Match factura ↔ cobro'),
             ('CtaCteAjustes', '—', 'Ajustes saldo'),
             ('CtaCteUnidades', '69K', 'Por unidades (medicamentos)'),
             ('CtaCteFacturacionesGrupales', '—', 'Facturación masiva'),
         ]),
        ('🛒 Compras / Proveedores',
         '#e0e7ff',
         [
             ('Pedidos + PedidosRenglones', '—', 'Pedidos a proveedores'),
             ('Distribuidoras + Droguerias + Laboratorios', '—', 'Maestros'),
             ('ProveedoresPropios + GLN', '—', 'Definición + GLN GS1'),
             ('Remitos + Detalles', '—', 'Recepción mercadería'),
             ('ProveedoresProductosEnFalta', '—', 'Faltantes reportados'),
             ('SolicitudesNcNdProveedores', '—', 'NC/ND al proveedor'),
             ('CondicionesComerciales (+_HIS)', '—', 'Plazos pago + histórico'),
             ('ReposicionesInternas', '—', 'Entre sucursales'),
         ]),
        ('👤 Clientes / Afiliados',
         '#fce7f3',
         [
             ('Clientes', '85K', 'Master clientes (ya sync)'),
             ('ClientesFarmacias + InformacionContacto', '76K', 'Per-farmacia'),
             ('Afiliados + Caracteristicas', '—', 'Datos OS/plan del afiliado'),
             ('Domicilios', '88K', 'Direcciones'),
             ('CategoriasClientes + GruposClientes', '—', 'Segmentación'),
         ]),
        ('🧑‍💼 Operativo (cajeros, puestos, canales, delivery)',
         '#ecfeff',
         [
             ('Cajeros', '—', 'Operadores de venta'),
             ('PuestosDeTrabajo', '—', 'Mostrador 1, 2, depósito...'),
             ('CanalesDeVenta', '—', 'Mostrador, delivery, web, OS'),
             ('Cadeterias + Cadetes', '—', 'Gestión delivery'),
         ]),
        ('📋 Facturación / IVA',
         '#fef9c3',
         [
             ('LibroIVA', '1.0M', 'Libro fiscal'),
             ('LibroIVAAlicuotas', '1.2M', 'Detalle alícuotas'),
             ('TiposComprobantes', '—', 'A/B/C, Factura/NC/ND'),
             ('ImpresorasFiscales + Ticket', '—', 'Hardware fiscal'),
             ('NumeradoresComprobantes', '—', 'Numeración automática'),
             ('ControlPeriodoFacturacion', '—', 'Periodo abierto/cerrado'),
         ]),
    ]

    for nombre, _color, filas in areas:
        elems.append(Paragraph(f'<b>{nombre}</b>', BODY))
        data = [['Tabla', 'Filas', 'Descripción']]
        for tabla, n, desc in filas:
            data.append([tabla, n, desc])
        elems.append(tabla_simple(data, col_widths=[6.5*cm, 1.7*cm, 8.5*cm]))
        elems.append(Spacer(1, 8))

    # ═══════════ Tier de prioridades ═══════════
    elems.append(PageBreak())
    elems.append(Paragraph('Prioridades por tier (urgencia descendente)', H2))

    # ─── TIER 1 ───
    elems.append(Paragraph('TIER 1 — URGENTE (alto impacto, ROI inmediato)', H3_URG))

    elems.append(Paragraph('1.1 — Cierre de caja diario automático', H3_URG))
    elems.append(Paragraph(
        '<b>Tablas:</b> CajasMostradorMovimientos (173K), CajasMostradorCierres + 4 '
        'tablas relacionadas, CuponTarjeta (373K), TarjetaCierres, OperacionesPagos (2.4M).',
        META))
    elems.append(Paragraph('<b>Features:</b>', BODY))
    for f in [
        'Reemplazar la <b>planilla manual de cierre</b> por dashboard automático.',
        'Comparar <b>esperado vs real</b> por medio de pago (efectivo, tarjeta, etc.).',
        'Detectar diferencias por turno/cajero/puesto.',
        'Conciliación automática contra extractos de POSnet/MercadoPago/banco.',
        'Cierre fiscal mensual con un click (basado en LibroIVA + LibroIVAAlicuotas).',
        'Alertas de contracargos / liquidaciones no acreditadas.',
    ]:
        elems.append(b(f))
    elems.append(Paragraph(
        '<b>Esfuerzo:</b> 2-3 sprints. <b>ROI:</b> ahorra ~30 min/día de trabajo manual + '
        'detecta plata perdida (faltantes, contracargos no detectados).',
        META))

    elems.append(Paragraph('1.2 — Kardex completo (auditoría de stock)', H3_URG))
    elems.append(Paragraph(
        '<b>Tablas:</b> MovimientosStock (4.1M, incremental por fecha), AjustesStock + '
        'AjustesStockProductos (95K), MotivosAjustesStock, IngresosEgresosMercaderia + '
        'Renglones (854K).',
        META))
    elems.append(Paragraph('<b>Features:</b>', BODY))
    for f in [
        '<b>Stock auditado</b> — reconcilia stock_actual vs (entradas − salidas − ajustes).',
        '<b>Detección automática de mermas/robos</b> — flagear divergencias.',
        '<b>Análisis de rotación REAL</b> (no calculada con ventas mensuales aproximadas).',
        '<b>Histórico de cambios de precio</b> (ProductosPrecios 3.8M filas).',
        '<b>Trazabilidad por factura</b> — qué unidades concretas se vendieron.',
        '<b>Costo promedio ponderado real</b> por producto → pricing y márgenes precisos.',
        'Alertas de <b>stock fantasma</b> (stock>0 sin movs en N meses → inspección física).',
    ]:
        elems.append(b(f))
    elems.append(Paragraph(
        '<b>Esfuerzo:</b> 1-2 sprints. <b>Volumen:</b> alto (4.1M filas) — sync incremental '
        'por <code>FechaMovimiento</code>, ~10K/día.',
        META))

    elems.append(Paragraph('1.3 — Productividad operativa (cajeros + puestos + canales)', H3_URG))
    elems.append(Paragraph(
        '<b>Tablas:</b> Cajeros, PuestosDeTrabajo, CanalesDeVenta, Cadeterias, Cadetes, '
        'Operaciones (con foreign keys a todas las anteriores).',
        META))
    elems.append(Paragraph('<b>Features:</b>', BODY))
    for f in [
        'Dashboard <b>productividad por empleado</b> (ítems/hora, ticket promedio, mix).',
        '<b>Performance por turno y puesto</b> — saber cuándo rinde cada uno.',
        'Detección de <b>patrones sospechosos</b> (descuentos altos en un operador, '
        'devoluciones concentradas).',
        '<b>Comisiones automatizadas</b>.',
        'Análisis <b>canal digital vs físico</b> (mostrador, delivery, web).',
        '<b>Margen efectivo por canal</b> (descontando delivery).',
        '<b>Análisis cadetería</b> — entregas/cadete, tiempos, zonas.',
    ]:
        elems.append(b(f))
    elems.append(Paragraph(
        '<b>Esfuerzo:</b> 1-2 sprints. <b>Volumen:</b> bajo en lookups, alto en operaciones '
        '(2.2M filas con FK a operadores/puestos/canales).',
        META))

    # ─── TIER 2 ───
    elems.append(PageBreak())
    elems.append(Paragraph('TIER 2 — IMPORTANTE (siguientes a habilitar)', H3_MID))

    elems.append(Paragraph('2.1 — Cuenta corriente clientes (cobranzas)', H3_MID))
    elems.append(Paragraph(
        '<b>Tablas:</b> CtaCteVentas, CtaCteRenglones, CtaCtePagos, CtaCteRecibos, '
        'CtaCteCreditosDebitos, CtaCteImputaciones, CtaCteAjustes, CtaCteUnidades.',
        META))
    elems.append(Paragraph('<b>Features:</b>', BODY))
    for f in [
        '<b>Saldos por cliente</b> en tiempo real con aging (0-30, 30-60, 60+).',
        '<b>Recordatorios automáticos</b> de cuentas vencidas.',
        '<b>Reporte de cobranzas diarias</b> conciliado con cierre de caja.',
        '<b>Top deudores</b> y predicción de incobrables.',
        '<b>Imputación automática</b> de pagos a facturas (matching).',
        'Si tienen <b>facturación grupal</b> (CtaCteFacturacionesGrupales) — alertas de '
        'fin de período.',
    ]:
        elems.append(b(f))
    elems.append(Paragraph('<b>Esfuerzo:</b> 2 sprints. <b>Volumen:</b> medio (~150K filas total).', META))

    elems.append(Paragraph('2.2 — Análisis de recetas y obras sociales avanzado', H3_MID))
    elems.append(Paragraph(
        '<b>Tablas:</b> Recetas (842K), RecetasRenglones (1.1M), RecetasPrescripciones, '
        'RecetasPagos (740K), Convenios (+ extensiones, patologías, productos propios), '
        'Planes (+ restricciones por droga/lab/forma/principal/tipo, vademecums).',
        META))
    elems.append(Paragraph('<b>Features:</b>', BODY))
    for f in [
        '<b>Margen por OS/Plan</b> con detalle de descuento absorbido por farmacia vs OS.',
        '<b>Top médicos prescriptores</b> + alerta de prescripciones inusuales.',
        '<b>Análisis de patologías</b> (CIE10 + Drogas) — qué se trata más en la zona.',
        'Validación automática de <b>restricciones por plan</b> antes de la venta '
        '(droga/lab/forma).',
        '<b>Alertas de cartillas vencidas</b> o convenios con problemas.',
        'Ranking de <b>productos propios convenidos</b> que tenemos que tener en stock.',
        'Forecast de <b>consumo por convenio</b> (ej. PAMI) para negociar con droguería.',
    ]:
        elems.append(b(f))
    elems.append(Paragraph('<b>Esfuerzo:</b> 2-3 sprints. <b>Volumen:</b> alto (3M+ filas).', META))

    elems.append(Paragraph('2.3 — Histórico de precios y márgenes', H3_MID))
    elems.append(Paragraph(
        '<b>Tablas:</b> ProductosPrecios (3.8M), ProductosPreciosVigentes, '
        'ProductosPreciosVigentesProveedores, PreciosReferenciaPorDroga, '
        'ParametrosCalculoPrecio.',
        META))
    elems.append(Paragraph('<b>Features:</b>', BODY))
    for f in [
        'Gráfico <b>histórico de precio por producto</b> (PVP + costo).',
        '<b>Margen efectivo histórico</b> y alertas de márgenes negativos.',
        '<b>Comparador de precios entre proveedores</b> para mismo producto.',
        '<b>Alfabeta interno</b> (PreciosReferenciaPorDroga) — ya tenemos data.',
        'Forecast de <b>impacto de aumentos</b> antes de aplicarlos.',
    ]:
        elems.append(b(f))

    # ─── TIER 3 ───
    elems.append(Paragraph('TIER 3 — Nice to have / consolidación', H3_LOW))

    elems.append(Paragraph('3.1 — Compras a proveedores con KPIs', H3_LOW))
    elems.append(Paragraph(
        '<b>Tablas:</b> Pedidos + PedidosRenglones, Remitos + Detalles, '
        'ProveedoresProductosEnFalta, SolicitudesNcNdProveedores, CondicionesComerciales (+_HIS).',
        META))
    elems.append(Paragraph('<b>Features:</b>', BODY))
    for f in [
        '<b>Cash-flow forecast</b> con condiciones comerciales (a 30/60/90 días).',
        '<b>Performance por proveedor</b> (% cumplimiento, lead time).',
        'Reporte de <b>NC/ND pendientes</b>.',
        '<b>Auto-armado de pedidos</b> con histórico de faltantes.',
    ]:
        elems.append(b(f))

    elems.append(Paragraph('3.2 — Trazabilidad GTIN-14 / ANMAT', H3_LOW))
    elems.append(Paragraph(
        '<b>Tablas:</b> ProductosCodigosGTIN (54K), DistribuidorasGLN, DrogueriasGLN, '
        'LaboratoriosGLN, ProveedoresPropiosGLN.',
        META))
    elems.append(Paragraph('<b>Features:</b>', BODY))
    for f in [
        '<b>Trazabilidad ANMAT</b> automática (medicamentos controlados).',
        'Verificación de <b>GLN GS1</b> para receta electrónica.',
        'Alertas de <b>productos vencidos en stock</b> (cuando llegue ese campo).',
    ]:
        elems.append(b(f))

    # ═══════════ Roadmap ═══════════
    elems.append(PageBreak())
    elems.append(Paragraph('Roadmap propuesto (8 sprints)', H2))

    roadmap = [
        ['Sprint', 'Foco', 'Tablas a sumar', 'Output'],
        ['1', 'Lookups + Canales',
         'CanalesDeVenta, Cajeros, PuestosDeTrabajo, Tarjetas, Bancos, TiposFormaDePago, MotivosAjustesStock',
         'Filtros nuevos en informes'],
        ['2', 'Cierre de caja',
         'CajasMostradorMovimientos, CajasMostradorCierres (+4 hijas), CuponTarjeta, TarjetaCierres, OperacionesPagos',
         'Dashboard cierre diario'],
        ['3', 'Kardex completo',
         'MovimientosStock (incremental), AjustesStock + Productos, IngresosEgresosMercaderia + Renglones',
         'Auditoría de stock + alertas mermas'],
        ['4', 'Productividad',
         '(ya las cargamos en sprint 1) — ahora dashboards: empleados, turnos, canales, cadetería',
         'Reportes de empleado'],
        ['5', 'Cuenta corriente',
         'CtaCte* (8 tablas)',
         'Saldos clientes + cobranzas'],
        ['6', 'Recetas y OS avanzado',
         'Recetas + Renglones + Pagos + Prescripciones, Convenios completos, Planes + restricciones',
         'Margen por OS, validaciones'],
        ['7', 'Histórico de precios',
         'ProductosPrecios, ProductosPreciosVigentes (+ Proveedores), PreciosReferenciaPorDroga',
         'Charts precio + alfabeta interno'],
        ['8', 'Compras + GTIN',
         'Pedidos, Remitos, ProveedoresProductosEnFalta, ProductosCodigosGTIN, GLN',
         'Cash-flow + trazabilidad'],
    ]
    elems.append(tabla_simple(roadmap, col_widths=[1.2*cm, 2.7*cm, 7.5*cm, 5.3*cm]))

    # ═══════════ Estrategia de sync ═══════════
    elems.append(Paragraph('Estrategia de sync por tipo de tabla', H2))
    sync = [
        ['Categoría', 'Ejemplos', 'Frecuencia', 'Estrategia'],
        ['Lookups (<10K filas)',
         'Cajeros, Tarjetas, Bancos, CanalesDeVenta, MotivosAjustesStock, TiposFormaDePago',
         'Diaria',
         'Full snapshot'],
        ['Operacionales medianas (10K-200K)',
         'CajasMostradorMovimientos (173K), AjustesStockProductos (95K), Clientes (85K), StockFarmaciasProductos (67K)',
         'Cada 1-4 horas',
         'Incremental por FechaModificacion / Id incremental'],
        ['Operacionales grandes (200K-2M)',
         'OperacionesPagos (2.4M), Operaciones (2.2M), GruposOperaciones (1.7M), LibroIVA (1.0M), RecetasRenglones (1.1M)',
         'Cada 1-2 horas',
         'Incremental por fecha + watermark'],
        ['Masivas (>2M)',
         'MovimientosStock (4.1M), VademecumsProductos (4.0M), OperacionesRenglones (3.8M), ProductosPrecios (3.8M)',
         'Continua / streaming',
         'CDC (Change Data Capture) o backfill por mes + incremental'],
        ['Históricos (HIS)',
         'Paquetes_HIS, CondicionesComerciales_HIS',
         'Solo si feature lo necesita',
         'On-demand'],
    ]
    elems.append(tabla_simple(sync, col_widths=[3*cm, 6.5*cm, 2.5*cm, 4.7*cm]))

    # ═══════════ Pedido al equipo Observer ═══════════
    elems.append(PageBreak())
    elems.append(Paragraph('Pedido concreto al equipo de Observer', H2))
    elems.append(Paragraph(
        'Tras el descubrimiento del schema completo, el "pedido" a Observer cambia '
        'radicalmente. Ya casi todo existe — el bloqueo es de <b>acceso/permisos</b>, no '
        'de funcionalidad faltante.',
        BODY))
    elems.append(Spacer(1, 6))

    pedidos = [
        ('Acceso de lectura a `ObServerGestion.*`',
         'Hoy accedemos solo via la vista DW (29 tablas). Necesitamos credenciales '
         'read-only sobre los schemas <code>Gestion</code> y <code>Generales</code> para '
         'cubrir las features Tier 1-3.'),
        ('Documentación de FechaModificacion / RowVersion',
         'Para sync incremental sin bajar 4M de filas cada vez. Confirmar qué tablas '
         'tienen ese campo y si es confiable.'),
        ('Aclaración de 3-4 enums clave',
         'IdTipoOperacion (V/D/NC...), IdTipoMovimientoStock (en MovimientosStock), '
         'IdMotivoAjusteStock, IdEstadoOperacion. Lookup textual para mostrar al usuario.'),
        ('Confirmación de columnas con datos sensibles (PII)',
         'Para excluir del sync: nombre/dni/teléfono/dirección de Clientes y Médicos si '
         'no las necesitamos para la feature.'),
        ('Permiso para crear vistas custom en Observer (opcional)',
         'Si hay queries pesadas que se repiten, vistas materializadas en Observer '
         'reducen carga. Si no, las creamos del lado nuestro.'),
    ]
    for n, (titulo, detalle) in enumerate(pedidos, 1):
        elems.append(Paragraph(f'<b>{n}. {titulo}</b>', BODY))
        elems.append(Paragraph(detalle, BULLET))
        elems.append(Spacer(1, 4))

    elems.append(Spacer(1, 12))
    elems.append(Paragraph(
        '<b>Lo que NO hay que pedir más:</b> CierreCaja, MovimientosCaja, transacciones '
        'electrónicas detalladas, 1-producto-a-N-EANs. <b>Todo eso ya existe</b> en '
        '<code>ObServerGestion.*</code>; solo hay que sincronizarlo del lado nuestro.',
        NOTA))

    # ═══════════ Notas técnicas finales ═══════════
    elems.append(Paragraph('Notas técnicas', H2))
    for x in [
        '<b>Patrón de sync:</b> mantener el actual (espejos en <code>obs_*</code> con '
        'upsert por PK + log en <code>obs_sync_log</code>). Las nuevas tablas siguen el '
        'mismo patrón, solo cambia el batch size y la frecuencia.',
        '<b>Volumen total a almacenar:</b> ~30M filas si traemos todo Tier 1+2 (vs ~5M '
        'actuales). Implica revisar config de Postgres en Render (RAM, disco) — '
        'probablemente upgrade de plan.',
        '<b>Migración:</b> los syncs nuevos pueden correr en paralelo a los viejos sin '
        'tocar la app. Solo se cambia código de los features cuando las tablas estén '
        'pobladas.',
        '<b>Privacidad:</b> Clientes/Médicos tienen PII. Si la app va multi-tenant en '
        'Render, encriptar columnas sensibles o no sincronizarlas.',
        '<b>Tabla `Vademecums.VademecumsProductos` (4M filas):</b> es la más grande pero '
        'también la menos prioritaria — solo se necesita para validaciones de planes en '
        'tiempo real. Considerar dejarla on-demand (query directo a Observer cuando '
        'se necesite).',
    ]:
        elems.append(b(x))

    doc.build(elems)
    print(f'PDF generado: {OUT_PATH}')
    print(f'Tamaño: {os.path.getsize(OUT_PATH):,} bytes')


if __name__ == '__main__':
    construir()
