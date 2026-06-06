# Asistente de farmacia (bot)

Bot conversacional para atención por WhatsApp/Telegram. **Fase 0**: prototipo en
Telegram, en local, con la data real de la farmacia.

## Arquitectura (capa de canal abstraída)

```
Telegram / WhatsApp ──► adaptador ──► cerebro (agnóstico de canal) ──► respuesta
                                         │
                                         ├─ flujo.py    (los nodos del menú)
                                         └─ acciones.py (consultas a la data real)
```

El **cerebro** procesa `(user_id, texto) → {texto, opciones}` sin saber del canal.
Para pasar de Telegram a WhatsApp se reemplaza solo el adaptador.

| Archivo | Rol |
|---|---|
| `flujo.py` | Definición del flujo (nodos: menú / texto / pedir_input). Editable a mano (Fase 0); va a DB + UI en Fase 1 |
| `acciones.py` | Acciones que tocan la data (`consultar_producto` → `product_analytics`) |
| `cerebro.py` | Router del flujo (estado de conversación persistido en DB vía `store.py`) |
| `store.py` | Persistencia: conversaciones + mensajes en DB (habilita handoff, historial y que sobreviva reinicios) |
| `telegram_bot.py` | Adaptador Telegram + long polling |

## Correr (Fase 0)

1. Crear un bot en Telegram con **@BotFather** → `/newbot` → copiar el token.
2. Correr el adaptador con el token por env:

```bash
docker-compose exec -e TELEGRAM_BOT_TOKEN='123456789:ABC...' web python -m bot.telegram_bot
```

3. Escribirle al bot desde tu Telegram. "hola" abre el menú.

## Probar el cerebro sin Telegram (contra la data)

```bash
docker-compose exec web python -c "
import os, database; database.init_engine(os.environ['DATABASE_URL'])
from bot import cerebro
print(cerebro.procesar('u1', 'ibuprofeno'))
"
```

## Hecho
- Nodo de **IA libre** (Claude entiende "algo para la tos") con tool use sobre el stock real, y **memoria** del hilo de conversación.
- **Foto de receta** → Claude visión extrae medicamentos → cruza con stock.
- **Notas de voz** → Whisper local → texto → cerebro.
- **Derivación a humano** (handoff) + **panel de operadores**, con estado de conversación persistido en **DB** (sobrevive reinicios / multi-worker).
- **Encargar un producto** → cae en la bandeja del operador con contexto.

## Pendiente (próximas iteraciones)
- **Hacer pedido** integrado al módulo de pedidos real (hoy el encargo se deriva al operador, no crea el pedido en el sistema).
- Adaptador **WhatsApp Cloud API** (Fase 1) + **UI de edición de flujos**.
