FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-spa \
    libglib2.0-0 libsm6 libxext6 libxrender-dev \
    curl ca-certificates gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
       | gpg --dearmor -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p uploads

EXPOSE 5000

# gthread + threads: un worker bloqueado en una query lenta no impide que otros
# threads del mismo worker atiendan requests rápidas (health check, /api/notifications).
# Workers=2 alcanza para free tier de Render; threads=4 da paralelismo barato.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --timeout 120 --workers 2 --threads 4 --worker-class gthread --preload app:app"]
