"""Asistente de farmacia (bot conversacional).

Arquitectura con capa de canal abstraída: el `cerebro` procesa mensajes
genéricos (user_id, texto) y devuelve respuestas genéricas (texto + opciones);
los adaptadores de canal (Telegram hoy, WhatsApp Cloud API después) traducen
entre la API del canal y ese formato. Reusa la data de la app (product_analytics).

Fase 0: prototipo en Telegram, en local, con la data real.
"""
