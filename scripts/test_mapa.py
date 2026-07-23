# -*- coding: utf-8 -*-
"""Chequeos que protegen las afirmaciones del CLAUDE.md.

La doc miente; el test no. Estos chequeos existen porque el CLAUDE.md ya
afirmaba cosas falsas (que el catalogo era uniforme entre farmacias) y costo
medio dia de rediseño descubrirlo. Si alguien cambia el codigo de forma que la
doc quede vieja, esto falla y avisa.

Uso:  python scripts/test_mapa.py
"""
import io
import os
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

fallos = []


def ck(cond, msg):
    print(('  OK   ' if cond else '  FALLO ') + msg)
    if not cond:
        fallos.append(msg)


print('1) El mapa esta actualizado')
r = subprocess.run([sys.executable, os.path.join(RAIZ, 'scripts', 'mapa.py'), '--check'],
                   capture_output=True, text=True, cwd=RAIZ)
ck(r.returncode == 0, f'docs/MAPA.generado.md al dia ({r.stdout.strip() or r.stderr.strip()})')

print('\n2) Las trampas del CLAUDE.md siguen siendo ciertas')
import mapa  # noqa: E402  (scripts/mapa.py)

S = {nombre: (vistas, premium) for nombre, vistas, premium, _ln, _d in mapa.syncs()}

# Si esto cambia, la seccion "premium" del CLAUDE.md quedo vieja.
premium = sorted(n for n, (_v, p) in S.items() if p)
ck(premium == ['sync_condiciones_comerciales', 'sync_precios_vigentes'],
   f'las features premium (schema Gestion) siguen siendo 2: {premium}')

ck(S.get('sync_precios_vigentes', ([], 0))[0] == ['Gestion.ProductosPreciosVigentes'],
   'los precios siguen saliendo de Gestion.ProductosPreciosVigentes (por farmacia)')

# El sync de stock lee StockFarmaciasProductos, NO "DW.Stock" (DockerPanel-Lite
# de AppChatFarm asume el nombre corto y por eso no sirve contra un ObServer real).
ck(S.get('sync_stock', ([], 0))[0] == ['DW.StockFarmaciasProductos'],
   'el stock sale de DW.StockFarmaciasProductos (ojo: NO "DW.Stock")')

ck(S.get('sync_productos', ([], 0))[0] == ['DW.Productos'],
   'el catalogo sale de DW.Productos')

print('\n3) La deteccion de acceso premium sigue en su lugar')
src = io.open(os.path.join(RAIZ, 'observer_source.py'), encoding='utf-8').read()
ck('_test_acceso_gestion' in src, '_test_acceso_gestion existe (skipea premium sin SA)')
ck("IS_SRVROLEMEMBER('sysadmin')" in src,
   "diagnostico_acceso usa IS_SRVROLEMEMBER('sysadmin')")
ck('def explorar_schema' in src,
   'explorar_schema existe (para farmacias sin capa DW)')
ck('def ejecutar_sql_readonly' in src, 'ejecutar_sql_readonly existe (SQL read-only)')

print('\n4) El CLAUDE.md tiene las trampas documentadas')
claude = io.open(os.path.join(RAIZ, 'CLAUDE.md'), encoding='utf-8').read()
for frase, que in [
    ('observer_id', 'la trampa del observer_id por farmacia'),
    ('capa `DW.*` NO es parte de ObServer', 'que la capa DW no viene con ObServer'),
    ('MAPA.generado.md', 'el link al mapa generado'),
    ('verificá la rama', 'la regla de verificar la rama antes de decir "no existe"'),
]:
    ck(frase in claude, f'documentado: {que}')

print()
print('RESULTADO:', 'TODO OK' if not fallos else f'{len(fallos)} FALLOS')
sys.exit(1 if fallos else 0)
