"""Parser de direcciones: separa "calle+número" de "piso / depto / referencia".

Diseño (ver docs/tarea_domicilios_estructurados.md):
- Calle y número van JUNTOS en `direccion` (la línea geocodable).
- `piso`, `depto`, `referencia` quedan SEPARADOS (no entran al geocoder).

Reglas de extracción (greedy desde el final del string):
  1) PISO ordinal + letra suelta:  "1° B", "2do A", "3er C"
     → piso="1°", depto="B"   (cubre el caso Rioja 950 1° B)
  2) DEPTO con keyword: dto / dpto / depto / dep / departamento / uf
  3) PISO con keyword: piso / p° / p. / pb / planta baja
  4) REFERENCIA: monoblock/torre/barrio/manzana/mz/lote/lt/casa + valor
  5) "entre X y Y" → referencia

Casting: se normaliza (lowercase + NFD sin acentos) para UBICAR los patrones
sobre el string, pero los recortes del `direccion` resultante se hacen sobre el
TEXTO ORIGINAL (preserva mayúsculas y acentos).
"""
from __future__ import annotations

import re
import unicodedata


def _norm(s: str) -> str:
    """Minúsculas + sin tildes (NFD). SOLO para matchear patrones, no para recortar."""
    s = (s or '').strip().lower()
    return ''.join(c for c in unicodedata.normalize('NFD', s)
                   if unicodedata.category(c) != 'Mn')


# Regex compiladas (sobre texto NORMALIZADO).
# IMPORTANTE: cada match tiene un patrón "antes" opcional con captura del piso
# para extraer el ordinal cuando hay letra suelta (caso "1° B").
_RE_PISO_ORD_LETRA = re.compile(
    r'\s*'                                                  # espacios
    r'(?P<piso>\d+\s*(?:°|er|do|ra|to|ta))\s+'              # "1°" / "2do" / "3er"
    r'(?P<depto>[a-z0-9]+)\s*\.?\s*$',                      # "B" / "2" / "12b"
    re.IGNORECASE
)
_RE_DEPTO_KW = re.compile(
    r'\s*'
    r'\b(?:dto|dpto|depto|dep|departamento|uf)\s*'
    r'[:.]?\s*'
    r'(?P<depto>[a-z0-9]+)\s*\.?\s*$',
    re.IGNORECASE
)
_RE_PISO_KW = re.compile(
    r'\s*'
    r'\b(?:planta\s*baja|pb|piso|p°|p\.?)\s*'
    r'[:.]?\s*'
    r'(?P<piso>[a-z0-9]+)?\s*\.?\s*$',
    re.IGNORECASE
)
_RE_REF = re.compile(
    r'\s*'
    r'\b(?:monoblock|mb|torre|t°|t\.|barrio|b°|b\.|casa|'
    r'manzana|mz|mz\.|lote|lt|lt\.)\s*'
    r'(?P<ref>[a-z0-9]+)\s*\.?\s*$',
    re.IGNORECASE
)
_RE_ENTRE_CALLES = re.compile(
    r'\s*'
    r'\bentre\s+(?P<uno>.+?)\s+y\s+(?P<dos>.+?)\s*\.?\s*$',
    re.IGNORECASE
)
_TRAIL_GARBAGE = re.compile(r'[\s,;\.]+$')

# Patrón "/ CIUDAD" o "- CIUDAD" o ", CIUDAD" al final del string. Típico de
# ObServer Argentina (ej. "CALLE 6 C 4418 / FUNES").
_RE_LOCALIDAD_TAIL = re.compile(
    r'\s*[/\-,]\s*([a-záéíóúñ\.\s]+)\s*$',
    re.IGNORECASE
)


def _localidades_filtro() -> list[str]:
    """Whitelist de ciudades válidas — misma fuente que el filtro del geocoder
    (ENVIO_CIUDADES_FILTRO). Si la env var no está, default a Rosario/Funes/Roldán.
    Devuelve nombres normalizados (lowercase + sin tildes) para comparar."""
    import os as _os
    raw = _os.environ.get('ENVIO_CIUDADES_FILTRO', 'rosario,funes,roldan')
    return [_norm(c) for c in raw.split(',') if c.strip()]


def separar_direccion(texto: str, ciudades_validas: list[str] | None = None) -> dict:
    """'bolivia 1614 DTO 2' → {direccion:'bolivia 1614', depto:'2', piso:None, referencia:None}.

    Calle y número quedan juntos en `direccion`; piso / depto / referencia se
    separan en sus propios campos. Si el string no tiene unidad, `direccion`
    es el input completo (con casing y acentos originales) y el resto None.

    Si `ciudades_validas` se pasa (lista de strings normalizados) o por default
    ENVIO_CIUDADES_FILTRO está seteada, se intenta extraer la ciudad del final
    del string (patrones típicos de ObServer: "CALLE X 123 / FUNES",
    "AV. Y 456 - ROSARIO", etc.). Devuelve `localidad` con el casing canónico
    (Title Case sobre el match de la whitelist).

    Robustez: tolera mayúsculas, acentos, abreviaturas (DTO/DPTO/DEP/UF),
    ordinales con grado (1°/2do/3er) y combinaciones (piso+depto, monoblock+dto).
    NO rompe direcciones con números en el nombre de la calle
    (ej. 'Pasaje 3 de Febrero 1614 dto 2' → direccion intacta).
    """
    if not texto or not texto.strip():
        return {'direccion': '', 'piso': None, 'depto': None,
                'referencia': None, 'localidad': None}

    original = texto.strip()
    norm = _norm(original)

    piso = depto = referencia = localidad = None

    # Regla 0 (corre PRIMERO, antes de piso/depto/ref): extraer "/ CIUDAD" al
    # final si la ciudad está en la whitelist. Así "CALLE 6 C 4418 / FUNES" →
    # localidad='Funes', direccion='CALLE 6 C 4418', y el resto de las reglas
    # operan sobre el string sin la cola de ciudad.
    if ciudades_validas is None:
        ciudades_validas = _localidades_filtro()
    if ciudades_validas:
        m = _RE_LOCALIDAD_TAIL.search(norm)
        if m:
            candidata = _norm(m.group(1).strip())
            if candidata in ciudades_validas:
                # Devolver con Title Case del valor canónico de la whitelist.
                localidad = candidata.title()
                norm = norm[:m.start()].rstrip()
                original = original[:m.start()].rstrip()

    # Regla 1: ordinal + letra suelta (p.ej. "1° B", "2do A")
    m = _RE_PISO_ORD_LETRA.search(norm)
    if m:
        # Solo el número del ordinal: "1°"/"2do"/"3er" → "1"/"2"/"3".
        piso = re.match(r'\d+', m.group('piso').strip()).group()
        depto = m.group('depto').upper() if m.group('depto') else None
        norm = norm[:m.start()].rstrip()
        original = original[:m.start()].rstrip()
    else:
        # Regla 2: depto con keyword
        m = _RE_DEPTO_KW.search(norm)
        if m:
            depto = m.group('depto').upper() if m.group('depto') else None
            norm = norm[:m.start()].rstrip()
            original = original[:m.start()].rstrip()
        # Regla 3: piso con keyword
        m = _RE_PISO_KW.search(norm)
        if m:
            piso_raw = m.group('piso')
            if piso_raw:
                piso = piso_raw.upper()
            else:
                # "PB" / "planta baja" sin número → piso="PB"
                # Detectar si quedó "pb" o "planta baja" como resto de la kw
                if re.search(r'\bpb\b|planta\s*baja', norm):
                    piso = 'PB'
            norm = norm[:m.start()].rstrip()
            original = original[:m.start()].rstrip()
        # Regla 4: "entre X y Y" (antes que ref genérica, es más específica)
        m = _RE_ENTRE_CALLES.search(norm)
        if m:
            referencia = f"entre {m.group('uno').strip()} y {m.group('dos').strip()}"
            norm = norm[:m.start()].rstrip()
            original = original[:m.start()].rstrip()
        else:
            # Regla 5: referencia genérica (monoblock/torre/etc.)
            m = _RE_REF.search(norm)
            if m:
                # Combinar el keyword con el valor (ej. "monoblock 4" → "monoblock 4")
                # Para eso reconstruimos desde el ORIGINAL, no la versión normalizada.
                ref_raw = original[m.start():].strip()
                # Si el recorte ya quedó como "monoblock 4", lo mantenemos; si
                # quedó como "Monoblock 4" (casing original), también.
                # Limpiar trailing garbage.
                ref_raw = _TRAIL_GARBAGE.sub('', ref_raw)
                referencia = ref_raw
                norm = norm[:m.start()].rstrip()
                original = original[:m.start()].rstrip()

    # Limpiar trailing garbage del direccion (comas, espacios, etc.)
    direccion = _TRAIL_GARBAGE.sub('', original).strip()
    if not direccion:
        # Si solo quedaron espacios → None para que el caller detecte "vacío"
        direccion = ''

    return {
        'direccion': direccion,
        'piso': piso or None,
        'depto': depto or None,
        'referencia': referencia or None,
        'localidad': localidad or None,
    }