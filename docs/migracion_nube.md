# Migración del proyecto a la nube

El proyecto ya está muy bien preparado para la nube (Flask + PostgreSQL + Docker). Los pasos en orden lógico:

---

## 1. Elegir dónde hostear

| Opción | Costo | Complejidad | Recomendado para |
|--------|-------|-------------|-----------------|
| **Railway** | ~$5/mes | Muy bajo | Empezar rápido |
| **Render** | Gratis/~$7/mes | Bajo | Simple, sin ops |
| **DigitalOcean App Platform** | ~$12/mes | Medio | Más control |
| **VPS (DigitalOcean/Linode)** | ~$6/mes | Alto | Máximo control, igual a local |

Para este proyecto se recomienda **un VPS** porque ya tenés docker-compose y querés control total (backups, parsers, uploads).

---

## 2. Cambios necesarios en el código

**Variables de entorno** — sacar credenciales hardcodeadas:

```env
# .env (nunca al repo)
DATABASE_URL=postgresql://postgres:PASS@db:5432/farmacia
SECRET_KEY=clave-segura-aleatoria
```

**Archivos subidos** — actualmente van a `uploads/` local. En la nube eso se pierde al reiniciar el contenedor. Necesitás:
- Un volumen persistente (en VPS ya lo tenés con `pgdata`)
- O mover uploads a S3/Cloudflare R2 si usás plataforma serverless

**`gunicorn`** — ya está en `requirements.txt`, bien.

---

## 3. Si elegís VPS (más simple para este proyecto)

```bash
# En el servidor
apt install docker.io docker-compose
git clone https://github.com/DiegoTarditti/farmacia /app
cd /app
cp .env.example .env   # configurar variables
docker-compose up -d
```

Agregar **Nginx** como reverse proxy:

```nginx
server {
    listen 80;
    server_name tudominio.com;
    location / {
        proxy_pass http://localhost:5000;
    }
}
```

Y **SSL gratis** con Certbot:

```bash
certbot --nginx -d tudominio.com
```

---

## 4. Lo que hay que cambiar en el repo

- [ ] `.env` con variables reales (nunca al git — ya está en `.gitignore`)
- [ ] `SECRET_KEY` aleatoria y segura
- [ ] `UPLOAD_FOLDER` apuntando a un volumen persistente
- [ ] Nginx + SSL en el servidor
- [ ] Backups automáticos de la DB (cron que llame al pg_dump)

---

## Resumen

El proyecto necesita **cambios mínimos** para ir a la nube. La opción más directa es un VPS donde corrés exactamente lo mismo que localmente con docker-compose. En 2-3 horas está funcionando.
