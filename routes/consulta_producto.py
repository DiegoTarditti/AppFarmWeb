"""Consulta de un medicamento por EAN/troquel.

Pantalla mobile-first para escanear (cámara o pistola) un troquel y ver
nombre, lab, stock, ventas, gráfico histórico. NO requiere elegir lab/drog
previo — el EAN resuelve directo contra el catálogo local + bridge ObServer.

Endpoints:
  GET /consulta-producto           → form de entrada (input + cámara)
  POST /consulta-producto/buscar   → recibe EAN, resuelve, redirect al detail
  GET /consulta-producto/<ean>     → renderiza resultado con KPIs + chart
"""
from flask import flash, redirect, render_template, request, url_for

import database
from helpers import _find_producto


def init_app(app):

    @app.route('/consulta-producto')
    def consulta_producto():
        """Pantalla entrada: input grande + botón cámara para escanear EAN."""
        return render_template('consulta_producto.html')

    @app.route('/consulta-producto/buscar', methods=['POST'])
    def consulta_producto_buscar():
        """Recibe el EAN del form (input manual o cámara), resuelve contra
        catálogo local + bridge ObServer, redirige al detalle si encuentra.
        Si no encuentra, vuelve al form con flash.
        """
        ean = (request.form.get('ean') or '').strip()
        if not ean:
            flash('Ingresá un código de barras.', 'error')
            return redirect(url_for('consulta_producto'))
        # Validación mínima: solo dígitos, 8-14 chars (EAN-13 es lo común;
        # EAN-8 / GTIN-14 también aparecen).
        if not ean.isdigit() or len(ean) < 8 or len(ean) > 14:
            flash(f'Código inválido: "{ean}". Debe ser 8-14 dígitos.', 'error')
            return redirect(url_for('consulta_producto'))
        return redirect(url_for('consulta_producto_detalle', ean=ean))

    @app.route('/consulta-producto/<ean>')
    def consulta_producto_detalle(ean):
        """Resultado: resuelve EAN → producto + observer_id, pasa contexto al
        template. El chart histórico se llena vía fetch a /api/product/<ean>/chart
        (mismo endpoint que usan otras pantallas — single source of truth).
        """
        ean = (ean or '').strip()
        info = {'ean': ean, 'encontrado': False}
        with database.get_db() as session:
            prod = _find_producto(session, ean)
            if prod:
                # Acceder a los atributos AHORA antes de cerrar la sesión.
                info.update({
                    'encontrado': True,
                    'producto_id': prod.id,
                    'observer_id': prod.observer_id,
                    'descripcion': prod.descripcion or '',
                    'codigo_barra': prod.codigo_barra,
                    'precio_pvp': float(prod.precio_pvp) if prod.precio_pvp else None,
                    'monodroga': prod.monodroga or '',
                    'presentacion': prod.presentacion or '',
                    'laboratorio_id': prod.laboratorio_id,
                    'no_pedir': bool(prod.no_pedir),
                })
                # Nombre del laboratorio (resuelto en mismo session).
                if prod.laboratorio_id:
                    lab = session.get(database.Laboratorio, prod.laboratorio_id)
                    info['laboratorio'] = lab.nombre if lab else ''
                else:
                    info['laboratorio'] = ''
        return render_template('consulta_producto_resultado.html', info=info)
