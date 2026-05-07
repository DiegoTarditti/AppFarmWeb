"""Lista las droguerías que existen en ObServer.Gestion.Droguerias.

Uso:
    docker-compose exec web python scripts/listar_droguerias_observer.py
"""
import observer_source


def main():
    r = observer_source.ejecutar_sql_readonly(
        "SELECT IdDrogueria, Nombre, CUIT, FW_FechaBaja "
        "FROM Gestion.Droguerias ORDER BY Nombre",
        max_rows=500)
    activas = [x for x in r['rows'] if not x.get('FW_FechaBaja')]
    bajas = [x for x in r['rows'] if x.get('FW_FechaBaja')]
    print(f"Total: {len(r['rows'])}  · activas: {len(activas)} · bajas: {len(bajas)}")
    print()
    print("ACTIVAS:")
    for row in activas:
        cuit = row.get('CUIT') or ''
        print(f"  #{row['IdDrogueria']:>4}  {row['Nombre']:<55}  {cuit}")
    if bajas:
        print()
        print("BAJAS (no se importan):")
        for row in bajas:
            print(f"  #{row['IdDrogueria']:>4}  {row['Nombre']}")


if __name__ == '__main__':
    main()
