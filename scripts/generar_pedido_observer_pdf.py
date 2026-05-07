"""Genera el PDF cortito con SOLO el pedido al equipo Observer.

Uso:
    docker-compose exec web python scripts/generar_pedido_observer_pdf.py

Output:
    docs/pedido_a_observer.pdf
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
    'docs', 'pedido_a_observer.pdf'
)


styles = getSampleStyleSheet()
TITULO = ParagraphStyle('Titulo', parent=styles['Title'],
    fontSize=18, leading=22, textColor=colors.HexColor('#1c1c1e'),
    spaceAfter=4, alignment=TA_LEFT, fontName='Helvetica-Bold')
SUBTITULO = ParagraphStyle('Subtitulo', parent=styles['Normal'],
    fontSize=10, textColor=colors.HexColor('#6f6f6f'), spaceAfter=12)
H2 = ParagraphStyle('H2', parent=styles['Heading2'],
    fontSize=13, leading=18, textColor=colors.HexColor('#1c1c1e'),
    spaceBefore=14, spaceAfter=6, fontName='Helvetica-Bold')
H3 = ParagraphStyle('H3', parent=styles['Heading3'],
    fontSize=11, leading=15, textColor=colors.HexColor('#1c1c1e'),
    spaceBefore=10, spaceAfter=3, fontName='Helvetica-Bold')
BODY = ParagraphStyle('Body', parent=styles['Normal'],
    fontSize=10, leading=14, textColor=colors.HexColor('#1e1e1e'),
    spaceAfter=4)
BULLET = ParagraphStyle('Bullet', parent=BODY,
    leftIndent=14, bulletIndent=4, spaceAfter=2)
NOTA = ParagraphStyle('Nota', parent=styles['Normal'],
    fontSize=10, textColor=colors.HexColor('#1e1e1e'),
    backColor=colors.HexColor('#FEF3C7'),
    borderPadding=8, borderColor=colors.HexColor('#F59E0B'),
    borderWidth=1, spaceBefore=6, spaceAfter=10, leading=14)
INFO = ParagraphStyle('Info', parent=styles['Normal'],
    fontSize=9.5, textColor=colors.HexColor('#1e3a8a'),
    backColor=colors.HexColor('#dbeafe'),
    borderPadding=8, borderColor=colors.HexColor('#3b82f6'),
    borderWidth=1, spaceBefore=6, spaceAfter=10, leading=13)


def b(t):
    return Paragraph(f'• {t}', BULLET)


def construir():
    doc = SimpleDocTemplate(
        OUT_PATH, pagesize=A4,
        leftMargin=2.2*cm, rightMargin=2.2*cm,
        topMargin=1.8*cm, bottomMargin=1.8*cm,
        title='Pedido al equipo Observer',
        author='AppFarmWeb',
    )
    elems = []

    # ─── Cabecera ───
    elems.append(Paragraph('Pedido al equipo de Observer', TITULO))
    elems.append(Paragraph(
        f'Resumen de lo que necesitamos del lado de Observer · '
        f'{datetime.now().strftime("%d/%m/%Y")}',
        SUBTITULO))
    elems.append(HRFlowable(width='100%', thickness=1,
                             color=colors.HexColor('#e0e0e0'),
                             spaceBefore=0, spaceAfter=10))

    # ─── Intro ───
    elems.append(Paragraph(
        'Tras explorar <code>ObServerGestion.*</code> (255 tablas en '
        '<code>Gestion.*</code> + <code>Generales.*</code>), descubrimos '
        'que <b>casi todo lo que imaginábamos pedir ya existe</b>. El '
        'bloqueo es de <b>acceso / permisos</b> y <b>documentación</b>, '
        'no de funcionalidad faltante.',
        BODY))
    elems.append(Spacer(1, 8))

    # ═══════════ Pedidos generales ═══════════
    elems.append(Paragraph('Pedidos generales', H2))

    # Pedido 1
    elems.append(Paragraph('1. Acceso de lectura a <code>ObServerGestion.*</code>', H3))
    elems.append(Paragraph(
        'Hoy accedemos solo vía la vista <code>DW.*</code> (29 tablas — '
        'versión denormalizada). Necesitamos credenciales <b>read-only</b> '
        'sobre los schemas <code>Gestion</code> y <code>Generales</code> '
        'para cubrir features de cierre de caja diario, kardex completo, '
        'cuenta corriente clientes, recetas/OS avanzado, histórico de '
        'precios y compras a proveedores.',
        BODY))
    elems.append(Paragraph(
        '<b>Idealmente</b>: usuario de servicio dedicado (ej. <code>app_lector</code>) '
        'con permiso <code>db_datareader</code> sobre la DB. Evitamos usar '
        'cuentas personales para auditoría.',
        BODY))

    # Pedido 2
    elems.append(Paragraph('2. Documentación de <code>FechaModificacion</code> / <code>RowVersion</code>', H3))
    elems.append(Paragraph(
        'Para sync incremental sin bajar 4M de filas cada vez. Confirmar '
        'qué tablas tienen ese campo confiable, especialmente:',
        BODY))
    for x in [
        '<code>MovimientosStock</code> (4.1M filas)',
        '<code>OperacionesRenglones</code> (3.8M)',
        '<code>ProductosPrecios</code> (3.8M)',
        '<code>OperacionesPagos</code> (2.4M)',
        '<code>Operaciones</code> (2.2M)',
        '<code>RecetasRenglones</code> (1.1M)',
        '<code>Recetas</code> (842K)',
    ]:
        elems.append(b(x))

    # Pedido 3
    elems.append(Paragraph('3. Aclaración de enums / lookups', H3))
    elems.append(Paragraph('Mapeo de IDs a descripciones para mostrar al usuario:', BODY))
    for x in [
        '<b><code>IdTipoOperacion</code></b> (V/D/NC/...): valores y descripciones oficiales.',
        '<b><code>IdTipoMovimientoStock</code></b> en <code>MovimientosStock</code>.',
        '<b><code>IdMotivoAjusteStock</code></b> (FK a <code>MotivosAjustesStock</code>).',
        '<b><code>IdEstadoCierreCajaMostrador</code></b>, <code>IdEstadoOperacion</code>.',
        '<b><code>IdTipoFormaDePagoContable</code></b>: cuál es Efectivo / Visa / MP / etc.',
    ]:
        elems.append(b(x))

    # Pedido 4
    elems.append(Paragraph('4. Confirmación de columnas con datos sensibles (PII)', H3))
    elems.append(Paragraph(
        'Para excluir del sync local lo que no necesitamos: nombre / DNI / '
        'teléfono / domicilio de <code>Clientes</code> y <code>Medicos</code> '
        'cuando no afectan a la feature.',
        BODY))

    # Pedido 5
    elems.append(Paragraph('5. (Opcional) Permiso para crear vistas custom o ambiente de testing', H3))
    elems.append(Paragraph(
        'Si hay queries pesadas que se repiten, una vista materializada del '
        'lado de Observer reduce mucho la carga. Si existe un ambiente de '
        'staging / sandbox, mejor para validar queries sin riesgo a producción.',
        BODY))

    # ═══════════ Tabla de tablas ═══════════
    elems.append(PageBreak())
    elems.append(Paragraph('Tablas concretas que necesitamos sincronizar', H2))
    elems.append(Paragraph(
        'Lista priorizada de tablas en <code>ObServerGestion.Gestion.*</code> '
        'a las que necesitamos acceso de lectura. Las lookups chicas en '
        '<code>Generales.*</code> las consultamos al pasar.',
        BODY))
    elems.append(Spacer(1, 6))

    tablas = [
        ['Tier', 'Tabla', 'Filas', 'Para qué'],
        # ─── TIER 1 ───
        ['1', 'Gestion.CajasMostradorCierres', '—', 'Cabecera de cierre por turno/caja/cajero'],
        ['1', 'Gestion.CajasMostradorMovimientos', '173K', 'Apertura+cierre+movs intra-turno'],
        ['1', 'Gestion.CajasMostradorCierresComprobantes', '—', 'Comprobantes incluidos en el cierre'],
        ['1', 'Gestion.CajasMostradorCierresControl', '—', 'Diferencias contra esperado'],
        ['1', 'Gestion.CajasMostradorCierresImpresoras', '—', 'Z report de impresora fiscal'],
        ['1', 'Gestion.Cajeros', '—', 'Maestro de operadores'],
        ['1', 'Gestion.PuestosDeTrabajo', '—', 'Cajas físicas (mostrador, depósito, etc.)'],
        ['1', 'Gestion.CuponTarjeta', '373K', 'Cupones POS individuales (Visa/Master/...)'],
        ['1', 'Gestion.TarjetaCierres', '—', 'Cierre de lote por marca'],
        ['1', 'Gestion.Tarjetas', '—', 'Marcas y emisores de tarjetas'],
        ['1', 'Gestion.TiposFormaDePago', '—', 'Lookup efectivo/tarjeta/transf/cheque'],
        ['1', 'Gestion.Cheques', '—', 'Cheques recibidos de terceros'],
        ['1', 'Gestion.OperacionesPagos', '2.4M', 'Pagos por operación (medio + monto)'],
        ['1', 'Gestion.MovimientosStock', '4.1M', 'Kardex completo con signos'],
        ['1', 'Gestion.AjustesStock + Productos', '95K', 'Ajustes manuales (mermas, vencidos)'],
        ['1', 'Gestion.MotivosAjustesStock', '—', 'Lookup motivos ajuste'],
        ['1', 'Gestion.IngresosEgresosMercaderia + Renglones', '853K', 'Compras/devoluciones a proveedor'],
        ['1', 'Gestion.CanalesDeVenta', '—', 'Mostrador / delivery / web / OS'],
        ['1', 'Gestion.Cadeterias / Cadetes', '—', 'Gestión de delivery'],
        # ─── TIER 2 ───
        ['2', 'Gestion.Operaciones', '2.2M', 'Cabecera de cada venta'],
        ['2', 'Gestion.OperacionesRenglones', '3.8M', 'Productos vendidos por operación'],
        ['2', 'Gestion.OperacionesRecetas', '721K', 'Link operación ↔ receta'],
        ['2', 'Gestion.OperacionesRenglonesPlanes', '953K', 'Descuento aplicado por plan'],
        ['2', 'Gestion.GruposOperaciones', '1.7M', 'Agrupa para facturas/devoluciones'],
        ['2', 'Gestion.CtaCte* (8 tablas)', '~150K', 'Cuenta corriente clientes (cobranzas)'],
        ['2', 'Gestion.Recetas', '842K', 'Cabecera de receta (full)'],
        ['2', 'Gestion.RecetasRenglones', '1.1M', 'Productos prescriptos'],
        ['2', 'Gestion.RecetasPagos', '740K', 'Pago de la receta'],
        ['2', 'Gestion.RecetasPrescripciones', '—', 'Diagnóstico, dosis'],
        ['2', 'Gestion.Convenios + ConveniosFarmacias', '—', 'OS x farmacia'],
        ['2', 'Gestion.ConveniosPatologias / Drogas / ProductosPropios', '—', 'Restricciones de convenio'],
        ['2', 'Gestion.Planes + 5 tablas restricciones', '—', 'Restricc. droga/lab/forma/tipo'],
        ['2', 'Gestion.ProductosPrecios', '3.8M', 'Histórico precios (todos los cambios)'],
        ['2', 'Gestion.ProductosPreciosVigentes (+ Proveedores)', '—', 'Snapshot de precios actuales'],
        ['2', 'Gestion.PreciosReferenciaPorDroga', '—', 'Referencia tipo Alfabeta'],
        # ─── TIER 3 ───
        ['3', 'Gestion.Pedidos + PedidosRenglones', '—', 'Pedidos a proveedores'],
        ['3', 'Gestion.Remitos + Detalles', '—', 'Recepción de mercadería'],
        ['3', 'Gestion.ProductosPendientesDeEntrega', '—', 'Faltantes pedidos'],
        ['3', 'Gestion.SolicitudesNcNdProveedores', '—', 'NC/ND al proveedor'],
        ['3', 'Gestion.CondicionesComerciales (+_HIS)', '—', 'Plazos pago + histórico'],
        ['3', 'Gestion.ProductosCodigosGTIN', '54K', 'GTIN-14 trazabilidad ANMAT'],
        ['3', 'Gestion.Distribuidoras / Droguerias / Laboratorios + GLN', '—', 'GLN GS1'],
        ['3', 'Gestion.LibroIVA + Alicuotas', '2.2M', 'Libro fiscal (compliance)'],
        ['3', 'Gestion.Vademecums + Productos', '4.0M', 'Vademécum por OS/plan'],
    ]

    t = Table(tablas, colWidths=[1.0*cm, 8.5*cm, 1.5*cm, 5.6*cm], repeatRows=1)
    base_style = [
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1c1c1e')),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.HexColor('#EAB308')),
        ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0,0), (-1,-1), 8),
        ('VALIGN',     (0,0), (-1,-1), 'TOP'),
        ('ALIGN',      (0,0), (-1,-1), 'LEFT'),
        ('ALIGN',      (0,0), (0,-1), 'CENTER'),
        ('ALIGN',      (2,0), (2,-1), 'RIGHT'),
        ('GRID',       (0,0), (-1,-1), 0.4, colors.HexColor('#cccccc')),
        ('LEFTPADDING',(0,0), (-1,-1), 4),
        ('RIGHTPADDING',(0,0), (-1,-1), 4),
        ('TOPPADDING',  (0,0), (-1,-1), 3),
        ('BOTTOMPADDING',(0,0), (-1,-1), 3),
    ]
    # Coloreo según tier
    for i, fila in enumerate(tablas[1:], start=1):
        tier = fila[0]
        if tier == '1':
            base_style.append(('BACKGROUND', (0,i), (-1,i), colors.HexColor('#fee2e2')))
        elif tier == '2':
            base_style.append(('BACKGROUND', (0,i), (-1,i), colors.HexColor('#fef3c7')))
        elif tier == '3':
            base_style.append(('BACKGROUND', (0,i), (-1,i), colors.HexColor('#dcfce7')))
    t.setStyle(TableStyle(base_style))
    elems.append(t)

    elems.append(Spacer(1, 8))
    elems.append(Paragraph(
        '<b>Tier 1</b> (rojo): urgente — bloquea cierre de caja, kardex y '
        'productividad. <b>Tier 2</b> (amarillo): siguiente — cuenta corriente, '
        'recetas, precios. <b>Tier 3</b> (verde): consolidación — compras, '
        'fiscal, trazabilidad.',
        BODY))

    # ═══════════ Específicos AppCajas ═══════════
    elems.append(PageBreak())
    elems.append(Paragraph('Específicos para AppCajas (cierre de caja automatizado)', H2))
    elems.append(Paragraph(
        'AppCajas es el primer feature concreto que vamos a construir con '
        'estos datos: detecta el evento de cierre en Observer, consulta '
        'APIs externas (Payway, Mercado Pago) y reconcilia. Casi todo lo '
        'que necesita está en las tablas listadas, pero quedan 3 puntos '
        'a aclarar:',
        BODY))
    elems.append(Spacer(1, 6))

    elems.append(Paragraph('A. ¿Dónde vive el Terminal_Payway_ID por caja/puesto?', H3))
    elems.append(Paragraph(
        'No vemos un campo <code>TerminalPaywayId</code> en '
        '<code>PuestosDeTrabajo</code> ni en <code>Cajeros</code>. ¿Está en '
        'otra tabla de configuración? ¿O ese mapeo vive fuera de Observer y '
        'lo configuramos del lado nuestro?',
        BODY))

    elems.append(Paragraph('B. Concepto de "Turno" (Mañana / Tarde / Noche)', H3))
    elems.append(Paragraph(
        '<code>CajasMostradorCierres</code> no parece tener un campo <code>Turno</code> '
        'explícito. Confirmar:',
        BODY))
    for x in [
        '¿Se infiere por hora de <code>FechaDesde</code>?',
        '¿Hay parametrización de turnos por farmacia?',
        '¿O cada sesión cajero (apertura → cierre) ya es 1 turno?',
    ]:
        elems.append(b(x))

    elems.append(Paragraph('C. Detección en tiempo real del evento de cierre', H3))
    elems.append(Paragraph(
        'Necesitamos disparar la conciliación apenas el cajero cierra. ¿Qué '
        'opción nos recomiendan?',
        BODY))
    for x in [
        'Polling sobre <code>CajasMostradorCierres.TS_Edicion</code> (o <code>FW_Fecha</code>).',
        'Trigger / Service Broker / Change Tracking que notifique al cerrar.',
        'Webhook / evento publicado por la aplicación Observer.',
    ]:
        elems.append(b(x))

    elems.append(Paragraph(
        '<b>Bonus:</b> con acceso directo a <code>OperacionesPagos.IdCuponTarjeta</code> + '
        '<code>CuponTarjeta</code>, el cruce cupón ↔ venta se hace con un JOIN. '
        'Eso elimina el adapter de parsing CSV de AppCajas y hace la detección '
        'casi en tiempo real.',
        NOTA))

    # ═══════════ Lo que NO hay que pedir ═══════════
    elems.append(Paragraph('Lo que NO hay que pedir más', H2))
    elems.append(Paragraph(
        'Inicialmente íbamos a pedir tablas nuevas para cierre de caja, '
        'transacciones electrónicas y "1 producto → N EANs". <b>Todo eso '
        'ya existe</b> en <code>ObServerGestion.*</code>:',
        BODY))
    elems.append(Spacer(1, 4))

    data = [
        ['Lo que pensábamos pedir', 'Tabla que ya existe en ObServerGestion'],
        ['Tabla de cierre de caja',
         'Gestion.CajasMostradorCierres (+ 4 hijas)'],
        ['Movimientos de caja',
         'Gestion.CajasMostradorMovimientos (173K filas)'],
        ['Transacciones electrónicas',
         'Gestion.CuponTarjeta (373K) + TarjetaCierres'],
        ['1 producto → N EANs',
         'Gestion.ProductosCodigosBarras (131K)'],
        ['Kardex con signos',
         'Gestion.MovimientosStock (4.1M)'],
    ]
    tbl = Table(data, colWidths=[7*cm, 9.6*cm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1c1c1e')),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.HexColor('#EAB308')),
        ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0,0), (-1,-1), 9.5),
        ('VALIGN',     (0,0), (-1,-1), 'TOP'),
        ('ALIGN',      (0,0), (-1,-1), 'LEFT'),
        ('GRID',       (0,0), (-1,-1), 0.5, colors.HexColor('#cccccc')),
        ('LEFTPADDING',(0,0), (-1,-1), 6),
        ('RIGHTPADDING',(0,0), (-1,-1), 6),
        ('TOPPADDING',  (0,0), (-1,-1), 6),
        ('BOTTOMPADDING',(0,0), (-1,-1), 6),
        ('ROWBACKGROUNDS', (0,1), (-1,-1),
         [colors.white, colors.HexColor('#fafafa')]),
        ('FONTNAME', (0,1), (-1,-1), 'Helvetica'),
    ]))
    elems.append(tbl)

    # ═══════════ Comentarios y notas ═══════════
    elems.append(Spacer(1, 14))
    elems.append(Paragraph('Comentarios y notas adicionales', H2))

    elems.append(Paragraph('Patrón de sincronización propuesto', H3))
    elems.append(Paragraph(
        'Mantenemos el patrón actual: cada tabla de Observer se espeja en '
        'una tabla local <code>obs_*</code> en nuestro Postgres, con upsert '
        'por PK + log en <code>obs_sync_log</code>. Las nuevas tablas siguen '
        'el mismo patrón — solo cambia el batch size y la frecuencia según '
        'el volumen.',
        BODY))

    elems.append(Paragraph('Volumen estimado a almacenar', H3))
    elems.append(Paragraph(
        'Con todo Tier 1 + 2 sincronizado en local, el volumen total es '
        '~30M filas (vs ~5M actuales). Implica revisar el plan de Postgres '
        'en Render — probablemente hay que upgradearlo. Tier 3 suma otros '
        '~10M en su mayoría inmutables (LibroIVA, Vademecums) — esos '
        'pueden quedar on-demand sin sincronizar.',
        BODY))

    elems.append(Paragraph('Consultas que NO van a hacer sync', H3))
    elems.append(Paragraph(
        'Algunas tablas las consultaríamos <b>directo en Observer</b> (sin '
        'sync local), porque son lookups chicos o data que no necesitamos '
        'persistir. Por ejemplo: <code>VademecumsProductos</code> (4M filas '
        'pero raramente se usa fuera de validación de plan en tiempo real). '
        'Para esto sirve el ítem 5 (vistas custom o sandbox).',
        BODY))

    elems.append(Paragraph('Compatibilidad con APIs externas', H3))
    elems.append(Paragraph(
        'AppCajas usa también APIs de Payway y Mercado Pago para reconciliar. '
        'Esas integraciones no dependen de Observer, pero el cruce final se '
        'hace en nuestra DB local con los datos de las 3 fuentes (Observer + '
        'Payway + MP). Por eso es crítico tener acceso a <code>CuponTarjeta</code> '
        'con <code>NumeroAutorizacion</code> — es la PK natural de cruce con Payway.',
        BODY))

    elems.append(Paragraph('Sugerencia operativa', H3))
    elems.append(Paragraph(
        'Sería útil una reunión técnica de 30-45 min con quien tenga el '
        'modelo Observer en la cabeza, para repasar los puntos 2-4 (campos '
        'de modificación, enums, PII) sobre tablas concretas. Eso nos '
        'destraba 80% del trabajo de sync. Si hay un slot, mejor.',
        INFO))

    doc.build(elems)
    print(f'PDF generado: {OUT_PATH}')
    print(f'Tamaño: {os.path.getsize(OUT_PATH):,} bytes')


if __name__ == '__main__':
    construir()
