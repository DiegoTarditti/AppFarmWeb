"""
Agente de documentos pendientes — Escanea una carpeta local y sube los PDFs
al endpoint /docs-pendientes/upload-api de la app en Render.

Uso:
  python agente_pendientes.py --carpeta "C:/Facturas" --url "https://farmacia-web-rj1z.onrender.com"

Opciones:
  --carpeta   Ruta local a escanear (obligatorio)
  --url       URL base de la app (obligatorio)
  --mover     Si se pasa, mueve los PDFs procesados a subcarpeta 'enviados/'
"""

import argparse
import json
import os
import sys
import shutil
import uuid
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


def _build_multipart(file_paths):
    """Construye un body multipart/form-data sin dependencias externas."""
    boundary = uuid.uuid4().hex
    lines = []
    for path in file_paths:
        fname = os.path.basename(path)
        lines.append(f'--{boundary}'.encode())
        lines.append(f'Content-Disposition: form-data; name="pdfs"; filename="{fname}"'.encode())
        lines.append(b'Content-Type: application/pdf')
        lines.append(b'')
        with open(path, 'rb') as f:
            lines.append(f.read())
    lines.append(f'--{boundary}--'.encode())
    body = b'\r\n'.join(lines)
    content_type = f'multipart/form-data; boundary={boundary}'
    return body, content_type


def escanear_y_subir(carpeta, url_base, mover=False):
    """Busca PDFs en la carpeta y los sube al endpoint API."""
    carpeta = os.path.abspath(carpeta)
    if not os.path.isdir(carpeta):
        print(f"ERROR: La carpeta no existe: {carpeta}")
        return 1

    endpoint = url_base.rstrip('/') + '/docs-pendientes/upload-api'

    pdfs = [f for f in os.listdir(carpeta)
            if f.lower().endswith('.pdf') and os.path.isfile(os.path.join(carpeta, f))]

    if not pdfs:
        print(f"No se encontraron PDFs en {carpeta}")
        return 0

    print(f"Encontrados {len(pdfs)} PDF(s) en {carpeta}")

    BATCH = 10
    total_nuevos = 0
    total_enviados = []

    for i in range(0, len(pdfs), BATCH):
        lote = pdfs[i:i + BATCH]
        paths = [os.path.join(carpeta, f) for f in lote]

        try:
            print(f"  Subiendo lote {i // BATCH + 1}: {len(lote)} archivo(s)...")
            body, content_type = _build_multipart(paths)
            req = Request(endpoint, data=body, method='POST')
            req.add_header('Content-Type', content_type)
            resp = urlopen(req, timeout=120)
            data = json.loads(resp.read().decode('utf-8'))
            n = data.get('nuevos', 0)
            total_nuevos += n
            total_enviados.extend(data.get('archivos', []))
            print(f"    OK — {n} nuevo(s)")
        except HTTPError as e:
            body_text = e.read().decode('utf-8', errors='replace')[:200]
            print(f"    ERROR {e.code}: {body_text}")
        except URLError as e:
            print(f"    ERROR: No se pudo conectar a {url_base} — {e.reason}")
            return 1
        except Exception as e:
            print(f"    ERROR: {e}")

    # Mover los enviados exitosamente
    if mover and total_enviados:
        enviados_dir = os.path.join(carpeta, 'enviados')
        os.makedirs(enviados_dir, exist_ok=True)
        for fname in total_enviados:
            src = os.path.join(carpeta, fname)
            dst = os.path.join(enviados_dir, fname)
            if os.path.exists(src):
                shutil.move(src, dst)
        print(f"  Movidos {len(total_enviados)} archivo(s) a {enviados_dir}")

    print(f"\nResumen: {total_nuevos} documento(s) nuevo(s) subido(s) de {len(pdfs)} encontrado(s).")
    return 0


def main():
    parser = argparse.ArgumentParser(description='Agente de documentos pendientes')
    parser.add_argument('--carpeta', required=True, help='Carpeta local con PDFs')
    parser.add_argument('--url', required=True, help='URL base de la app (ej: https://farmacia-web-rj1z.onrender.com)')
    parser.add_argument('--mover', action='store_true', help='Mover PDFs subidos a subcarpeta enviados/')
    args = parser.parse_args()

    sys.exit(escanear_y_subir(args.carpeta, args.url, args.mover))


if __name__ == '__main__':
    main()
