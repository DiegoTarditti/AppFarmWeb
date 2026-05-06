"""Cruce de recetas físicas (scan de códigos) contra Observer Gestion.Recetas.

UI: el operador escanea/pega varios códigos de barra (uno por línea), elige tipo
de búsqueda (auto = prueba OPF/Numero/AutorizExterno) y se cruza directo contra
ObServer en tiempo real (read-only).

Útil para:
- Liquidación PAMI/OS: asegurar que cada receta física esté en Observer.
- Detección de duplicados (mismo NumeroAutorizacionExterno cargado 2 veces).
- Auditoría de montos: papel vs sistema.
"""
from datetime import datetime

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required


def init_app(app):

    @app.route('/recetas/scan', methods=['GET', 'POST'])
    @login_required
    def recetas_scan():
        codigos_raw = (request.form.get('codigos') or '').strip()
        modo = (request.form.get('modo') or 'auto').strip()
        codigos = [c.strip() for c in codigos_raw.replace(',', '\n').splitlines() if c.strip()]
        # Sanity: máximo 50 por scan para no saturar.
        codigos = codigos[:50]

        resultados = None  # dict: codigo → {'match': dict|None, 'multiples': bool, 'tipo': str}
        error = None
        stats = None

        if codigos:
            import observer_source
            if not observer_source.observer_disponible():
                error = 'ObServer no disponible.'
            else:
                # Construir cláusula segura: solo dígitos/letras/-_ (sin SQL injection).
                # Cada código se queda como está si pasa el filtro.
                import re
                codigos_safe = [c for c in codigos if re.match(r'^[A-Za-z0-9_\-]{4,40}$', c)]
                if not codigos_safe:
                    error = 'Códigos inválidos. Solo alfanuméricos, 4-40 chars.'
                else:
                    quoted = ','.join(f"'{c}'" for c in codigos_safe)
                    if modo == 'opf':
                        where = f"r.OPF IN ({quoted})"
                    elif modo == 'numero':
                        where = f"r.NumeroReceta IN ({quoted})"
                    elif modo == 'autoriz':
                        where = f"r.NumeroAutorizacionExterno IN ({quoted})"
                    else:  # auto: cualquiera
                        where = (f"r.OPF IN ({quoted}) "
                                 f"OR r.NumeroReceta IN ({quoted}) "
                                 f"OR r.NumeroAutorizacionExterno IN ({quoted})")
                    sql = f"""
                    SELECT
                        r.IdReceta,
                        r.OPF,
                        r.NumeroReceta,
                        r.NumeroAutorizacionExterno AS Autoriz,
                        r.NumeroAfiliado,
                        r.NombreAfiliado,
                        r.MatriculaMedico,
                        r.IdPlan,
                        r.FechaDeVenta,
                        r.FechaAutorizacionOnLine,
                        r.TotalReceta,
                        r.TotalACargoOS,
                        r.TotalAfiliado,
                        r.Anulada,
                        r.Autorizada
                    FROM Gestion.Recetas r
                    WHERE {where}
                    ORDER BY r.FechaDeVenta DESC
                    """
                    try:
                        result = observer_source.ejecutar_sql_readonly(sql, max_rows=500)
                        rows = result['rows']
                    except Exception as e:
                        error = f'Error SQL: {e}'
                        rows = []

                    # Indexar por cada campo posible
                    by_opf = {}
                    by_num = {}
                    by_aut = {}
                    for r in rows:
                        if r.get('OPF'): by_opf.setdefault(r['OPF'], []).append(r)
                        if r.get('NumeroReceta'): by_num.setdefault(r['NumeroReceta'], []).append(r)
                        if r.get('Autoriz'): by_aut.setdefault(r['Autoriz'], []).append(r)

                    # Para cada código, encontrar match
                    resultados = []
                    found = 0
                    not_found = 0
                    multi = 0
                    anulada = 0
                    for c in codigos:
                        match_rows = []
                        match_tipo = None
                        if modo in ('auto', 'opf') and c in by_opf:
                            match_rows = by_opf[c]; match_tipo = 'OPF'
                        elif modo in ('auto', 'numero') and c in by_num:
                            match_rows = by_num[c]; match_tipo = 'NumeroReceta'
                        elif modo in ('auto', 'autoriz') and c in by_aut:
                            match_rows = by_aut[c]; match_tipo = 'Autorización'
                        if not match_rows:
                            resultados.append({
                                'codigo': c, 'estado': 'no_encontrada',
                                'match': None, 'tipo': None, 'multiples': False,
                            })
                            not_found += 1
                        else:
                            es_multi = len(match_rows) > 1
                            if es_multi:
                                multi += 1
                            m = match_rows[0]
                            est = 'anulada' if m.get('Anulada') else 'ok'
                            if est == 'anulada':
                                anulada += 1
                            else:
                                found += 1
                            resultados.append({
                                'codigo': c, 'estado': est,
                                'match': m, 'tipo': match_tipo,
                                'multiples': es_multi, 'todas': match_rows,
                            })
                    stats = {
                        'total': len(codigos),
                        'found': found,
                        'not_found': not_found,
                        'multi': multi,
                        'anulada': anulada,
                        'monto_total': sum(float(r['match'].get('TotalReceta') or 0)
                                           for r in resultados if r['match']),
                        'monto_os': sum(float(r['match'].get('TotalACargoOS') or 0)
                                        for r in resultados if r['match']),
                    }

        return render_template('recetas_scan.html',
                               codigos=codigos_raw, modo=modo,
                               resultados=resultados, stats=stats, error=error)
