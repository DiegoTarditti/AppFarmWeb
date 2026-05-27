"""AppNúcleo — Flask app (separada). Landing + Ventas-multi del grupo.

Run local:
    docker-compose exec -e NUCLEO_FARMACIAS='[...]' web python -m appnucleo.app
    # o sin env → modo DEMO en http://localhost:5001
Deploy: gunicorn 'appnucleo.app:create_app()' (servicio Render aparte).
"""
import os

from flask import Flask, redirect, render_template, request, url_for

from appnucleo import data

# Carga appnucleo/.env (gitignored) si python-dotenv está instalado — así
# NUCLEO_FARMACIAS / NUCLEO_SECRET_KEY se ponen ahí sin pasarlos por comando.
# Graceful: si dotenv no está, sigue andando con las env vars del entorno.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except ImportError:
    pass


def create_app():
    app = Flask(__name__, template_folder='templates')
    app.secret_key = os.environ.get('NUCLEO_SECRET_KEY', 'nucleo-dev')

    @app.template_filter('miles')
    def _miles(n):
        try:
            return f"{int(round(float(n))):,}".replace(',', '.')
        except (ValueError, TypeError):
            return n

    @app.template_filter('money')
    def _money(n):
        """$ compacto: 15.073.115.093 → '$ 15,1 MM'."""
        try:
            v = float(n)
        except (ValueError, TypeError):
            return n
        for div, suf in ((1e9, ' MM'), (1e6, ' M'), (1e3, ' K')):
            if abs(v) >= div:
                return f"$ {v / div:.1f}{suf}".replace('.', ',')
        return f"$ {v:.0f}"

    @app.route('/ping')
    def ping():
        return {'ok': True, 'app': 'appnucleo'}

    @app.route('/')
    def landing():
        force = request.args.get('refrescar') == '1'
        grupo = data.cargar_grupo(force=force)
        tot, por_far = data.kpis(grupo)
        return render_template(
            'landing.html',
            demo=grupo['demo'],
            tot=tot,
            por_far=por_far,
            meses=data.meses_labels(),
            tendencia=data.tendencia(grupo),
            top_labs=data.top_laboratorios(grupo, 10),
            rotacion=data.rotacion_dist(grupo),
        )

    @app.route('/ventas-multi')
    def ventas_multi():
        grupo = data.cargar_grupo()
        group_by = (request.args.get('group_by') or 'laboratorio').strip()
        q = request.args.get('q', '')
        rubro = request.args.get('rubro', '')
        pivot = data.ventas_multi(grupo, group_by=group_by, q=q, rubro=rubro)
        return render_template('ventas_multi.html', demo=grupo['demo'],
                               pivot=pivot, q=q, rubro=rubro, group_by=group_by)

    @app.route('/refrescar', methods=['POST'])
    def refrescar():
        data.cargar_grupo(force=True)
        return redirect(url_for('landing'))

    return app


if __name__ == '__main__':
    create_app().run(host='0.0.0.0', port=int(os.environ.get('NUCLEO_PORT', 5001)),
                     debug=True)
