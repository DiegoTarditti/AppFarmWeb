# App de Control de Stock para Farmacia

[![CI](https://github.com/DiegoTarditti/AppFarmWeb/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/DiegoTarditti/AppFarmWeb/actions/workflows/ci.yml)

## 🗺️ Empezá por acá

| Buscás… | Andá a |
|---|---|
| **Dónde está algo** (una ruta, un modelo, un sync) | [`docs/MAPA.generado.md`](docs/MAPA.generado.md) — índice de 765 rutas, 122 modelos, 21 syncs, 26 services y 11 parsers, con archivo:línea |
| **Por qué las cosas son así** + trampas del dominio | [`CLAUDE.md`](CLAUDE.md) — en especial *"Trampas de ObServer"* |
| **Qué falta hacer** | [`docs/backlog_urgente.md`](docs/backlog_urgente.md) (P0/P1/P2) y [`docs/mejoras_pendientes.md`](docs/mejoras_pendientes.md) |

El mapa **se genera del código** (`python scripts/mapa.py`) y el CI falla si queda
desactualizado — no puede pudrirse en silencio. Si tocás rutas, modelos o syncs,
regeneralo. Lo que el código NO dice (decisiones, trampas) va en el `CLAUDE.md`,
nunca en el mapa.

## Pre-push hooks (recomendado)

Para que el sistema corra `compileall` y `ruff` automáticamente antes de cada `git push` y bloquee el push si hay problemas:

```bash
git config core.hooksPath git-hooks
```

Una sola vez por clon. Bypass de emergencia: `SKIP_PUSH_CHECK=1 git push`.

Esta aplicación es un prototipo para cargar facturas en PDF y reportes ERP en Excel, extraer datos, comparar inventarios y generar un informe de diferencias.

## Tecnología
- Python 3
- Flask
- SQLAlchemy
- PostgreSQL / SQLite
- Pandas
- OpenPyXL
- Camelot / pdfplumber

## Estructura
- `app.py`: servidor Flask y rutas de carga.
- `database.py`: modelos SQLAlchemy y creación de tablas.
- `data_extract.py`: funciones de extracción y comparación.
- `templates/`: interfaz de usuario básica.

## Instalación
1. Crear un entorno virtual:
   ```bash
   python -m venv venv
   venv\Scripts\activate
   ```
2. Instalar dependencias:
   ```bash
   pip install -r requirements.txt
   ```
3. Configurar la base de datos PostgreSQL (opcional):
   ```bash
   set DATABASE_URL=postgresql://user:pass@localhost/farmacia
   ```
4. Ejecutar la aplicación localmente:
   ```bash
   python app.py
   ```

## Uso
1. Abrir `http://127.0.0.1:5000`
2. Cargar un PDF de factura y un Excel de stock ERP.
3. Revisar las diferencias e identificar elementos para reclamo.

## Docker y PostgreSQL
Este proyecto puede ejecutarse como servicio Docker con PostgreSQL.

1. Construir y levantar los servicios:
   ```bash
   docker compose up --build
   ```
2. El backend quedará disponible en `http://localhost:5000`.
3. El servicio PostgreSQL se ejecuta en `db:5432` y la URL se define automáticamente como:
   `postgresql://postgres:postgres@db:5432/farmacia`

## API
- `POST /api/upload`: subir `invoice_pdf` y `erp_excel` via multipart y obtener diferencias JSON.
- `GET /api/invoice/<invoice_id>/differences`: consultar diferencias ya guardadas.
- `POST /api/claims`: crear un reclamo a partir de los IDs de diferencias seleccionadas.
- `GET /api/claims/<claim_id>`: consultar datos del reclamo.
- `GET /health`: comprobación de estado.

## Reclamos y proveedores
- El sistema ahora guarda proveedores en la tabla `proveedores`.
- Al generar un reclamo se crea un encabezado en `reclamos` con proveedor, número de factura y fecha.
- Los ítems seleccionados para reclamo se registran en `reclamo_items`.
- Los reclamos pueden marcarse como `COMPLETADO` cuando llegan los productos faltantes.

## Notas
- `parse_invoice_pdf` necesita ser adaptado al formato real de tu factura.
- `templates/` mantiene una interfaz simple, pero el backend ya está listo para un cliente separado.

## Notas
- `parse_invoice_pdf` es un lugar de inicio. Debes adaptar la extracción según el formato exacto de la factura.
- Si prefieres PostgreSQL, define `DATABASE_URL` antes de ejecutar.
