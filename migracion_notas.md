database.py:286-287 — traducción postgres:// → postgresql:// (el connection string que entrega Render viene con el scheme viejo).
render.yaml — Blueprint con:
Postgres managed plan free (1GB DB, suficiente para probar).
Web service docker plan starter (USD 7/mes, el free no soporta disk persistente ni docker).
Disk 1GB montado en /app/uploads para medir consumo.
DATABASE_URL auto-inyectado desde la DB, SECRET_KEY autogenerado.
Healthcheck en /health (ya existe la ruta).
Para desplegar: push a un repo Git → en Render "New → Blueprint" → apuntar al repo → confirma. El render.yaml hace el resto.

Nota sobre espacio: con disk de 1GB podés ver crecimiento de uploads/ en el dashboard de Render. Si se queda corto, escalás el disk sin redeploy.

¿Querés que verifique el endpoint /health y prepare un .dockerignore para que el build no suba PDFs/xlsx locales?