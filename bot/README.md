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
| `cerebro.py` | Router + estado de conversación (en memoria; TODO: DB) |
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

## Pendiente (próximas iteraciones)
- Nodo de **IA libre** (Claude entiende "algo para la tos") sobre el de búsqueda directa.
- **Hacer pedido** (toma producto + cantidad + datos → módulo de pedidos).
- **Foto de receta** → Claude visión extrae medicamentos → cruza con stock.
- **Derivación a humano** real (notificar al personal).
- Estado de conversación en **DB** (multi-worker / sobrevive reinicios).
- Adaptador **WhatsApp Cloud API** (Fase 1) + **UI de edición de flujos**.
