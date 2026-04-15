# Acceso remoto con Tailscale

## Cómo funciona

Tailscale crea una VPN privada entre tus dispositivos. La app sigue corriendo en tu PC como ahora,
y vos y Lisandro se conectan a ella desde cualquier lugar como si estuvieran en la misma red local.

**Ventajas:**
- Gratis para hasta 3 usuarios / 100 dispositivos
- La app no se "duerme", responde igual que ahora
- Los archivos PDF/Excel no tienen límite
- No cambiás nada del código ni de Docker
- Los datos siguen en tu PC

---

## Pasos

### En la PC que corre Docker (el "servidor")

1. Instalar Tailscale → https://tailscale.com/download
2. Crear cuenta y hacer login
3. Tailscale te asigna una IP fija privada, ej: `100.64.x.x`
4. Verificar que Docker esté corriendo: `docker-compose up -d`

### Para cada usuario (vos y Lisandro)

1. Instalar Tailscale en cada dispositivo (PC, celular, lo que sea)
2. Loguearse con la misma cuenta, o invitar desde el panel de Tailscale
3. Abrir el browser y entrar a: `http://100.64.x.x:5000`
   (reemplazar con la IP que asigna Tailscale al servidor)

---

## Requisito

La PC que corre Docker tiene que estar **encendida y con Docker corriendo** para que los demás puedan acceder.
