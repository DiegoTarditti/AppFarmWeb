"""Planilla de carga del robot: qué mover del depósito a la máquina.

El modus operandi es de Lisandro: **mover desde el depósito hacia adentro del
robot la diferencia para llegar al máximo, si es que hay stock**. El máximo no es
algo a calcular — ya está decidido y cargado a mano en ObServer
(`Varios.Rowa_Productos.CantidadMaxima`, ver `database.ObsRowaProducto`).

Sobre eso se agrega el **dato inteligente**: cuántos packs justifica la demanda
real. No discute el máximo; avisa cuándo llenarlo hasta el tope es guardar
mercadería que no se va a mover en la semana. Las dos cifras van en la planilla,
una al lado de la otra, con la sugerida resaltada — pero la decisión final es de
quien camina el depósito.

SE PARTE DE ObServer, NO DEL ROBOT. `stock_info()` sólo devuelve artículos que
tienen packs adentro, así que armar la planilla desde ahí pierde el caso más
importante: robot en CERO con mercadería esperando en el depósito. Medido el
24/8/2026 eran **86 artículos y 377 packs**, más que los 319 de todo el resto
junto, y no aparecían en ninguna pantalla.

Es lógica pura (sin robot ni DB): entra todo por parámetro y se testea sola.
"""
from __future__ import annotations

import math

# Días que debería aguantar el robot sin que nadie lo recargue.
#
# NO es lead time del proveedor: a la droguería se le compra varias veces por día
# casi todos los días, así que el robot nunca espera mercadería. Lo único que
# cubre esta autonomía es cada cuánto alguien camina hasta la máquina. Por eso el
# número es bajo — con 15 se guardaba mercadería sin motivo.
DIAS_AUTONOMIA = 7

# Ventana de snapshots a partir de la cual la medición directa se banca sola.
DIAS_CONFIANZA_PLENA = 14.0

# Los snapshots miden salidas DEL ROBOT; ObServer mide VENTAS, hayan pasado o no
# por la máquina. Miden lo mismo (correlación de Pearson 0,883 sobre 801
# artículos) pero con distinta escala: en agregado los snapshots dan 398 u/día
# contra 315 de ObServer. Este factor corrige ese sesgo.
FACTOR_OBSERVER = 1.27

# Cuándo vale la pena avisar que el máximo de ObServer quedó holgado. Editarlo es
# trabajo manual, así que sólo se marca cuando la diferencia lo justifica: con
# estos valores quedan 526 artículos que liberarían 2.281 packs, el 85% del
# espacio recuperable, sin arrastrar 183 casos chicos.
CORREGIR_MIN_PACKS = 3
CORREGIR_MIN_PCT = 0.40


def confianza_snapshots(dias_ventana: float) -> float:
    """Cuánto pesa la medición directa contra el proxy de ventas, de 0 a 1.

    Con pocos días de snapshots un producto que vende una unidad cada dos semanas
    no muestra ninguna baja, y tratarlo como "no rota" es un error caro. Mientras
    tanto se apoya en las ventas de ObServer, y el proxy **se extingue solo** a
    medida que se acumulan tomas: a los 14 días ya no pesa nada.
    """
    if dias_ventana <= 0:
        return 0.0
    return min(1.0, dias_ventana / DIAS_CONFIANZA_PLENA)


def salidas_estimadas(salidas_snapshot: float | None,
                      salidas_observer: float | None,
                      dias_ventana: float) -> tuple[float, str]:
    """Mezcla las dos señales. Devuelve (unidades/día, de dónde salió)."""
    w = confianza_snapshots(dias_ventana)
    snap = salidas_snapshot or 0.0
    obs = (salidas_observer or 0.0) * FACTOR_OBSERVER

    if salidas_snapshot is None and salidas_observer is None:
        return 0.0, "sin datos"
    if salidas_observer is None:
        return snap, "robot"
    if salidas_snapshot is None:
        return obs, "ventas"
    return w * snap + (1 - w) * obs, "mezcla"


def _fila(pid, nombre, laboratorio, ean, en_robot, maximo, stock_total,
          salidas, origen_salidas, dias_autonomia, precio_pvp=None):
    """Arma una fila con sus dos cantidades y sus avisos. Sin decidir nada."""
    deposito = None if stock_total is None else stock_total - en_robot
    valor_deposito = (round(float(precio_pvp) * max(deposito or 0, 0), 2)
                      if precio_pvp and deposito else None)

    # El robot no puede tener más que el stock total: es parte de él. Cuando pasa,
    # el depósito calculado da negativo y la fila no se puede resolver. Antes
    # desaparecía en silencio; ahora se muestra marcada.
    #
    # Se exige `en_robot > 0`: con el robot en cero un depósito negativo no es una
    # diferencia robot/ObServer sino stock negativo en ObServer, que es otro
    # problema y no se arregla mirando la máquina. Sin esta condición entraban 73
    # filas donde sólo 24 eran diferencias reales.
    diferencia = en_robot > 0 and deposito is not None and deposito < 0
    disponible = max(deposito or 0, 0)

    if maximo is None or maximo <= 0:
        # Sin cupo cargado en ObServer no hay objetivo contra el cual comparar.
        # `construir_planilla` sólo deja pasar estas filas si el artículo ESTÁ en
        # la máquina: un producto sin cupo y fuera del robot es simplemente un
        # producto que no va al robot (brochas, jeringas, electrodos...), y
        # dejarlos entrar llenaba la planilla con 7.886 filas de ruido.
        return {
            "producto_observer": pid, "nombre": nombre, "laboratorio": laboratorio,
            "ean": ean, "en_robot": en_robot, "maximo": None, "deposito": deposito,
            "salidas_dia": round(salidas, 2), "origen_salidas": origen_salidas,
            "cobertura_d": None, "objetivo_sug": None,
            "a_mover_max": 0, "a_mover_sug": 0,
            "vacio": en_robot == 0, "parcial": False, "diferencia": diferencia,
            "sin_maximo": True, "sin_senal": salidas <= 0, "corregir_a": None,
            "precio_pvp": precio_pvp, "valor_deposito": valor_deposito,
        }

    hueco = max(0, maximo - en_robot)
    a_mover_max = min(hueco, disponible)

    # SIN SEÑAL NO SE OPINA. Salidas en cero puede significar dos cosas muy
    # distintas: que el producto no se mueve, o que no tenemos con qué medirlo —
    # un artículo que no está en el robot no genera snapshots, y con la ventana
    # corta uno que vende poco tampoco. Tratar la ausencia de dato como demanda
    # cero llevaba a proponer bajar un máximo de 6 a 1 sin ninguna evidencia.
    #
    # Cuando no hay señal se respeta el máximo, que es una decisión humana, y no
    # se marca nada para corregir.
    sin_senal = salidas <= 0

    if sin_senal:
        objetivo = maximo
        a_mover_sug = a_mover_max
        corregir = False
    else:
        # Objetivo por demanda, acotado por el cupo físico y con piso de 1: dejar
        # un canal en cero significa que el producto deja de estar disponible.
        objetivo = max(1, min(math.ceil(salidas * dias_autonomia), maximo))
        a_mover_sug = min(max(0, objetivo - en_robot), disponible)
        sobra = maximo - objetivo
        corregir = (sobra >= CORREGIR_MIN_PACKS and sobra / maximo >= CORREGIR_MIN_PCT)

    return {
        "producto_observer": pid, "nombre": nombre, "laboratorio": laboratorio,
        "ean": ean, "en_robot": en_robot, "maximo": maximo, "deposito": deposito,
        "salidas_dia": round(salidas, 2), "origen_salidas": origen_salidas,
        "cobertura_d": round(en_robot / salidas) if salidas > 0 else None,
        "objetivo_sug": objetivo,
        "a_mover_max": a_mover_max,
        "a_mover_sug": a_mover_sug,
        "vacio": en_robot == 0,
        # El depósito no alcanza para llenar el hueco: se mueve lo que hay y se
        # avisa. Si un artículo aparece parcial una y otra vez, el problema no es
        # de carga sino que el mínimo de COMPRA quedó corto.
        "parcial": hueco > 0 and 0 < disponible < hueco,
        "diferencia": diferencia,
        "sin_maximo": False,
        "sin_senal": sin_senal,
        "corregir_a": objetivo if corregir else None,
        "precio_pvp": precio_pvp, "valor_deposito": valor_deposito,
    }


def construir_planilla(articulos, dias_ventana: float,
                       dias_autonomia: int = DIAS_AUTONOMIA,
                       orden: str = "lab") -> dict:
    """Arma la planilla completa.

    `articulos`: iterable de dicts con producto_observer, nombre, laboratorio,
    ean, en_robot, maximo, stock_total, salidas_snapshot, salidas_observer,
    precio_pvp (opcional — sin precio, valor_deposito queda None).

    `orden`: "lab" (default, por laboratorio y alfabético — para caminar el
    depósito) o "valor" (por `valor_deposito` descendente — para priorizar
    qué ir a buscar primero cuando lo que importa es la plata parada).

    Devuelve {filas, totales}. Sólo entran las filas con algo que hacer: mover
    packs, una diferencia que revisar, o un máximo sin cargar. Un artículo con
    hueco pero sin nada en el depósito NO entra — no hay acción posible, y
    mandarlo a buscar mercadería que no está no es trabajo, es frustración. Ese
    es un problema de compra y vive en el informe de mínimos.
    """
    filas = []
    for a in articulos:
        salidas, origen = salidas_estimadas(
            a.get("salidas_snapshot"), a.get("salidas_observer"), dias_ventana)
        f = _fila(
            a["producto_observer"], a.get("nombre") or "?", a.get("laboratorio") or "",
            a.get("ean") or "", int(a.get("en_robot") or 0), a.get("maximo"),
            a.get("stock_total"), salidas, origen, dias_autonomia,
            precio_pvp=a.get("precio_pvp"))
        hay_algo = (f["a_mover_max"] > 0 or f["a_mover_sug"] > 0 or f["diferencia"]
                    # Sin cupo sólo interesa si el artículo está adentro: hay que
                    # ir a cargarle el máximo en ObServer para que entre al
                    # circuito. Si no está en la máquina, no es asunto del robot.
                    or (f["sin_maximo"] and f["en_robot"] > 0))
        if hay_algo:
            filas.append(f)

    if orden == "valor":
        filas.sort(key=lambda f: -(f["valor_deposito"] or 0))
    else:
        # Por laboratorio y alfabético: el depósito se camina un laboratorio a la vez.
        filas.sort(key=lambda f: ((f["laboratorio"] or "~").lower(), (f["nombre"] or "").lower()))

    return {
        "filas": filas,
        "totales": {
            "articulos": len(filas),
            "packs_sug": sum(f["a_mover_sug"] for f in filas),
            "packs_max": sum(f["a_mover_max"] for f in filas),
            "vacios": sum(1 for f in filas if f["vacio"]),
            "parciales": sum(1 for f in filas if f["parcial"]),
            "diferencias": sum(1 for f in filas if f["diferencia"]),
            "sin_maximo": sum(1 for f in filas if f["sin_maximo"]),
            "a_corregir": sum(1 for f in filas if f["corregir_a"] is not None),
            "sin_senal": sum(1 for f in filas if f.get("sin_senal")),
            "confianza": round(confianza_snapshots(dias_ventana), 2),
            "dias_ventana": round(dias_ventana, 2),
            "dias_autonomia": dias_autonomia,
        },
    }


def candidatos_sin_cupo(articulos, top_n: int = 30) -> list[dict]:
    """Productos con stock en depósito y SIN cupo asignado en el robot
    (`ObsRowaProducto.cantidad_maxima`), ordenados por valor $ parado.

    No es la misma pregunta que `construir_planilla` (que sólo sabe reponer
    cupos ya decididos): acá el universo es justamente lo que HOY no tiene
    ningún canal en la máquina, y por eso vive **fuera de cualquier control**
    del robot (packs, fechas, egresos). No hay "a mover" que calcular —no
    existe un objetivo sin cupo—, sólo señala dónde mirar primero si se va a
    decidir darle un canal.

    `articulos`: iterable de dicts con producto_observer, nombre, laboratorio,
    stock_deposito, precio_pvp. Sin alguno de los dos números no se puede
    valorizar, así que esos quedan afuera (no se inventa un valor).
    """
    out = []
    for a in articulos:
        stock = a.get("stock_deposito") or 0
        precio = a.get("precio_pvp")
        if stock <= 0 or not precio:
            continue
        valor = round(float(precio) * stock, 2)
        if valor <= 0:
            continue
        out.append({**a, "precio_pvp": float(precio), "valor_deposito": valor})
    out.sort(key=lambda c: -c["valor_deposito"])
    return out[:top_n]
