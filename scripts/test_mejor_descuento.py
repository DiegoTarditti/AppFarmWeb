"""Test rápido de la lógica `mejor_descuento`."""
import database
from services.descuentos import combinar_multiplicativo, mejor_descuento


def main():
    print('Test fórmula multiplicativa:')
    print(f'  base 31.03% + transfer 25%  = {combinar_multiplicativo(31.03, 25):.2f}%  (esperado: 48.27)')
    print(f'  base 31.03% + transfer 7%   = {combinar_multiplicativo(31.03, 7):.2f}%  (esperado: 35.86)')
    print(f'  base 31.03% solo            = {combinar_multiplicativo(31.03):.2f}%  (esperado: 31.03)')
    print(f'  3 niveles: 31 + 25 + 5      = {combinar_multiplicativo(31.03, 25, 5):.2f}%')
    print()

    database.init_db()
    with database.get_db() as s:
        lab = s.query(database.Laboratorio).filter_by(nombre='Baliarda').first()
        print(f'Lab: {lab.nombre} (id={lab.id})')
        print()

        for obs_id, desc in [(77790, 'ALERTIAL 120 rec x10'),
                             (77791, 'ALERTIAL 120 x30'),
                             (15151, 'BIATRIX 100 mg x30')]:
            print(f'Producto {obs_id}: {desc}')
            opts = mejor_descuento(s, obs_id, lab.id, cantidad=1)
            if not opts:
                print('  Sin descuentos disponibles')
            for o in opts:
                cm = o['compra_minima'] or 0
                cm_str = f'mín ${cm:,.0f}' if cm else 'sin mín'
                print(f"  {o['drogueria_nombre']:<35} {o['descuento_total_pct']:>6.2f}%  ({cm_str})")
                for d in o['desglose']:
                    print(f"    └ {d['nivel']}: {d['pct']}% — {d.get('fuente', '')}")
            print()


if __name__ == '__main__':
    main()
