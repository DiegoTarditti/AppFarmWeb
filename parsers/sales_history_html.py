"""
Parser para: rptEvolucionDeVentas.html (reporte HTML del ERP)

Estructura de columnas por td (0-indexed) en filas de producto:
  td[0]: vacío | td[1]: nombre | td[2]: lab | td[3]: PVP | td[4]: barcode
  td[5]: stock | td[6-10]: meses 1-5 | td[11]: spacer
  td[12-15]: meses 6-9 | td[16]: mes 10 | td[17]: mes 11 | td[18]: mes 12
  td[19]: spacer | td[20]: total

El período se lee del encabezado: "Período del MM/YYYY al MM/YYYY"
"""
import re
from bs4 import BeautifulSoup

# Índices de td que contienen ventas mensuales (12 meses en orden)
_MONTH_TD_INDICES = [6, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17, 18]

_MES_ABBR = {
    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4,
    'May': 5, 'Jun': 6, 'Jul': 7, 'Aug': 8,
    'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12,
}


def _text(td):
    return td.get_text(separator=' ', strip=True)


def _int(s):
    try:
        return int(re.sub(r'[^0-9]', '', s))
    except Exception:
        return 0


def _price(s):
    s = s.replace('$', '').replace('\xa0', '').strip()
    # "51.795,00" o "51795,00"
    s = s.replace('.', '').replace(',', '.')
    try:
        return float(s)
    except Exception:
        return 0.0


def parse_sales_history_html(path):
    with open(path, encoding='utf-8') as f:
        html = f.read()

    soup = BeautifulSoup(html, 'html.parser')

    # --- Farmacia ---
    farmacia = ''
    for td in soup.find_all('td'):
        t = _text(td)
        if 'Farmacia' in t and len(t) < 60:
            farmacia = t
            break

    # --- Período y laboratorio ---
    start_month, end_month = 5, 4
    start_year, end_year = 2025, 2026
    laboratorio = ''
    periodo = ''

    for td in soup.find_all('td'):
        t = _text(td)
        m = re.search(r'Per[ií]odo\s+del\s+(\d{2})/(\d{4})\s+al\s+(\d{2})/(\d{4})', t)
        if m:
            start_month = int(m.group(1))
            start_year  = int(m.group(2))
            end_month   = int(m.group(3))
            end_year    = int(m.group(4))
            periodo     = f"{m.group(1)}/{m.group(2)} - {m.group(3)}/{m.group(4)}"

        # Laboratorio: línea después de "Laboratorios:"
        if 'Laboratorios:' in t or 'Laboratorio:' in t:
            lines = [l.strip() for l in re.split(r'[\n\r]+', t) if l.strip()]
            for line in lines:
                if line not in ('Laboratorios:', 'Laboratorio:') \
                        and not line.startswith('Per') \
                        and not line.startswith('Farmacia') \
                        and not line.startswith('Fecha'):
                    laboratorio = line
                    break

    # --- Productos ---
    products = []
    for tr in soup.find_all('tr'):
        tds = tr.find_all('td')
        if len(tds) < 19:
            continue

        # Identificar fila de producto: td[4] debe ser barcode de 7-15 dígitos
        barcode_txt = _text(tds[4])
        if not re.match(r'^\d{7,15}$', barcode_txt):
            continue

        nombre  = _text(tds[1])
        lab     = _text(tds[2])
        pvp_txt = _text(tds[3])
        precio  = _price(pvp_txt) if pvp_txt.startswith('$') else 0.0
        stock   = _int(_text(tds[5]))

        # Ventas mensuales: 12 meses en el orden del período
        ventas = []
        for td_idx in _MONTH_TD_INDICES:
            ventas.append(_int(_text(tds[td_idx])))

        total = _int(_text(tds[20])) if len(tds) > 20 else sum(ventas)

        products.append({
            'nombre':       nombre,
            'laboratorio':  lab or laboratorio,
            'precio_pvp':   precio,
            'codigo_barra': barcode_txt,
            'stock':        stock,
            'ventas':       ventas,
            'total':        total,
        })

    return {
        'farmacia':    farmacia,
        'laboratorio': laboratorio,
        'periodo':     periodo,
        'start_month': start_month,
        'end_month':   end_month,
        'products':    products,
    }
