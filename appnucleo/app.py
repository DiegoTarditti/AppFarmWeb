"""AppNúcleo — Flask app (separada). Landing + Ventas-multi del grupo.

Run local:
    docker-compose exec -e NUCLEO_FARMACIAS='[...]' web python -m appnucleo.app
    # o sin env → modo DEMO en http://localhost:5001
Deploy: gunicorn 'appnucleo.app:create_app()' (servicio Render aparte).
"""
import os
from functools import wraps

from flask import Flask, redirect, render_template, request, session, url_for

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

    # ── Auth ──────────────────────────────────────────────────────────────
    def login_required(f):
        """Exige sesión SOLO si hay usuarios configurados (NUCLEO_USERS).
        Sin usuarios → acceso abierto (dev/tests no se rompen)."""
        @wraps(f)
        def wrapper(*a, **k):
            if data.auth_activa() and not session.get('nuc_user'):
                return redirect(url_for('login', next=request.path))
            return f(*a, **k)
        return wrapper

    def _grupo_del_usuario(force=False):
        """Carga el grupo y lo filtra a las farmacias permitidas del usuario."""
        grupo = data.cargar_grupo(force=force)
        return data.filtrar_grupo(grupo, session.get('nuc_farmacias', '*'))

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if not data.auth_activa():
            return redirect(url_for('landing'))
        error = None
        if request.method == 'POST':
            u = data.validar_credenciales(request.form.get('usuario'),
                                          request.form.get('password'))
            if u:
                session['nuc_user'] = u['usuario']
                session['nuc_nombre'] = u.get('nombre', u['usuario'])
                session['nuc_farmacias'] = data.farmacias_permitidas(u)
                dest = request.args.get('next') or url_for('landing')
                return redirect(dest)
            error = 'Usuario o contraseña incorrectos.'
        return render_template('login.html', error=error)

    @app.route('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.context_processor
    def _inject_auth():
        return {'auth_activa': data.auth_activa(),
                'nuc_user': session.get('nuc_user'),
                'nuc_nombre': session.get('nuc_nombre')}

    @app.route('/ping')
    def ping():
        return {'ok': True, 'app': 'appnucleo'}

    @app.route('/')
    @login_required
    def landing():
        force = request.args.get('refrescar') == '1'
        grupo = _grupo_del_usuario(force=force)
        tot, por_far = data.kpis(grupo)
        return render_template(
            'landing.html',
            demo=grupo['demo'],
            tot=tot,
            por_far=por_far,
            meses=data.meses_labels(),
            tendencia=data.tendencia(grupo),
            top_labs=data.top_laboratorios_por_farmacia(grupo, 10),
            rotacion=data.rotacion_dist(grupo),
            heatmap=data.heatmap_cobertura(grupo, 12),
            detalle=data.detalle_por_farmacia(grupo, 6),
        )

    @app.route('/ventas-multi')
    @login_required
    def ventas_multi():
        grupo = _grupo_del_usuario()
        group_by = (request.args.get('group_by') or 'laboratorio').strip()
        q = request.args.get('q', '')
        rubro = request.args.get('rubro', '')
        pivot = data.ventas_multi(grupo, group_by=group_by, q=q, rubro=rubro)
        return render_template('ventas_multi.html', demo=grupo['demo'],
                               pivot=pivot, q=q, rubro=rubro, group_by=group_by)

    @app.route('/comparar')
    @login_required
    def comparar():
        grupo = _grupo_del_usuario()
        _, por_far = data.kpis(grupo)
        detalle = data.detalle_por_farmacia(grupo, 8)
        slugs = [p['slug'] for p in por_far]
        a = request.args.get('a') or (slugs[0] if slugs else '')
        b = request.args.get('b') or (slugs[1] if len(slugs) > 1 else a)
        pf = {p['slug']: p for p in por_far}
        return render_template('comparar.html', demo=grupo['demo'],
                               por_far=por_far, pf=pf, detalle=detalle,
                               meses=data.meses_labels(), a=a, b=b)

    @app.route('/refrescar', methods=['POST'])
    @login_required
    def refrescar():
        data.cargar_grupo(force=True)
        return redirect(url_for('landing'))

    return app


if __name__ == '__main__':
    create_app().run(host='0.0.0.0', port=int(os.environ.get('NUCLEO_PORT', 5001)),
                     debug=True)
