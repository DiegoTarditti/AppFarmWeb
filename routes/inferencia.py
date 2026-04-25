"""Endpoints genéricos de inferencia (campos, tipos, relaciones aritméticas).

Reutilizables desde cualquier wizard que necesite autodetección:
- Importador de ofertas (XLSX/PDF)
- Conversor de facturas (texto del PDF tokenizado)
- Importador de módulos
- Cualquier futuro flujo de importación

Todos usan `field_inference.py` como única fuente de verdad. Si la lógica
cambia, cambia acá y todos los formularios se actualizan.
"""
from flask import jsonify, request
from flask_login import login_required


def init_app(app):

    @app.route('/api/inferir-columnas', methods=['POST'])
    @login_required
    def api_inferir_columnas():
        """Inferir mapeo de columnas en un import tabular.

        Body JSON:
            { "headers": ["EAN", "Producto", ...],
              "rows": [["7793...", "TAFIROL"], ...]   # opcional
              "candidatos": ["ean", "codigo", "descripcion"]  # opcional }

        Response:
            { "mapping": {"ean": 0, "descripcion": 1, ...},
              "campos": {"ean": {label, descripcion, tipo, ejemplos}, ...} }
        """
        import field_inference as fi
        data = request.get_json(silent=True) or {}
        headers = data.get('headers') or []
        rows = data.get('rows') or None
        candidatos = data.get('candidatos') or None
        if not isinstance(headers, list):
            return jsonify({'error': 'headers debe ser lista'}), 400
        mapping = fi.inferir_columnas(headers, sample_rows=rows, candidatos=candidatos)
        cat = candidatos or fi.nombres_campos()
        campos_meta = {n: {
            'label': fi.CAMPOS[n].get('label', n),
            'descripcion': fi.CAMPOS[n].get('descripcion', ''),
            'tipo': fi.CAMPOS[n].get('tipo', 'text'),
            'nucleo': fi.CAMPOS[n].get('nucleo', False),
            'ejemplos': fi.CAMPOS[n].get('ejemplos', []),
        } for n in cat if n in fi.CAMPOS}
        return jsonify({'mapping': mapping, 'campos': campos_meta})

    @app.route('/api/inferir/tipo-valor', methods=['POST'])
    @login_required
    def api_inferir_tipo_valor():
        """Inferir el tipo de un valor individual.

        Body JSON: { "valor": "7793450121123" }
        Response:  { "tipo": "ean" }   # o int|money|pct|date|text|null
        """
        import field_inference as fi
        data = request.get_json(silent=True) or {}
        return jsonify({'tipo': fi.inferir_tipo_valor(data.get('valor'))})

    @app.route('/api/inferir/fila-factura', methods=['POST'])
    @login_required
    def api_inferir_fila_factura():
        """Asignar campos a tokens de una fila de factura.

        Reemplaza la lógica JS `autodetectarCampos` de converter_pick.html.

        Body JSON: { "tokens": ["7793...", "5", "TAFIROL", ...] }
        Response:
            { "asignaciones": {
                  "codigo_barra": 0, "cantidad": 1,
                  "descripcion": [2, 7],            # rango [start, end]
                  "precio_publico": 5, "dto": 6,
                  "precio_unitario": 7, "importe": 8
              },
              "tipos": ["ean", "int", "text", ...],
              "warnings": ["..."] }
        """
        import field_inference as fi
        data = request.get_json(silent=True) or {}
        tokens = data.get('tokens') or []
        if not isinstance(tokens, list):
            return jsonify({'error': 'tokens debe ser lista'}), 400
        return jsonify(fi.detectar_campos_factura(tokens))

    @app.route('/api/inferir/fila-totales', methods=['POST'])
    @login_required
    def api_inferir_fila_totales():
        """Asignar campos del pie de factura a tokens por matemática.

        Reemplaza la lógica JS `autodetectarTotales` de converter_pick.html.
        Usa parsear_numero_ar tolerante a OCR-rotos.

        Body JSON: { "tokens": ["40", "1.183.326,62", "10.486,61", ...] }
        Response:
            { "asignaciones": {
                  "cantidad_total": 0,
                  "monto_exento": 5,
                  "monto_gravado": 1, "iva_21": 2,
                  "percepciones": 3,
                  "total": 4
              },
              "tipos": ["int", "money", "money", ...],
              "warnings": ["..."] }
        """
        import field_inference as fi
        data = request.get_json(silent=True) or {}
        tokens = data.get('tokens') or []
        if not isinstance(tokens, list):
            return jsonify({'error': 'tokens debe ser lista'}), 400
        return jsonify(fi.detectar_campos_totales(tokens))

    @app.route('/api/inferir/relaciones', methods=['POST'])
    @login_required
    def api_inferir_relaciones():
        """Detectar relaciones aritméticas entre valores (cant×unit=imp,
        iva=gravado×rate, total=sum, etc.).

        Body JSON:
            { "valores": [5, 100.0, 500.0],
              "contexto": "item" | "totales" | "pub_dto" }
        Response:
            { "relaciones": [{tipo, indices, formula}, ...] }
        """
        import field_inference as fi
        data = request.get_json(silent=True) or {}
        valores = data.get('valores') or []
        contexto = data.get('contexto') or 'item'
        try:
            valores = [float(v) if v is not None else None for v in valores]
        except (TypeError, ValueError):
            return jsonify({'error': 'valores deben ser numéricos'}), 400
        rels = fi.relacion_aritmetica(valores, contexto=contexto)
        return jsonify({'relaciones': rels})
