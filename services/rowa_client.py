"""Conector WWKS2 con el robot BD Rowa (Mosaic).

Habla el protocolo WWKS2 directo por socket: XML plano UTF-8 sobre TCP, sin
framing MLLP. El robot es el servidor (Type="Robot", Id=999) y nosotros el
cliente / sistema de la farmacia (Type="IMS"). La conexion arranca con un
HelloRequest/HelloResponse y se sostiene con KeepAlive.

Solo depende de la stdlib, asi que se puede probar suelto:

    python -m services.rowa_client --status
    python -m services.rowa_client --stock --limit 5

Robot verificado (2026-08-19): 192.168.1.150:6050, Mosaic v26.1.0, State=Ready.

IMPORTANTE: este modulo hace solo LECTURAS (Status, StockInfo, Configuration).
Un OutputRequest dispensa fisicamente — no se implementa aca a proposito.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# Config por entorno, con el default verificado en Badia.
ROWA_HOST = os.getenv("ROWA_HOST", "192.168.1.150")
ROWA_PORT = int(os.getenv("ROWA_PORT", "6050"))
# Id de subscriber que usamos como IMS (sistema de la farmacia).
ROWA_IMS_ID = int(os.getenv("ROWA_IMS_ID", "1001"))
ROWA_ROBOT_ID = int(os.getenv("ROWA_ROBOT_ID", "999"))
ROWA_TIMEOUT = float(os.getenv("ROWA_TIMEOUT", "15"))

_END = b"</WWKS>"


def _msg_id() -> str:
    return uuid.uuid4().hex[:8]


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _envelope(inner: str) -> bytes:
    """Envuelve un fragmento de mensaje en el sobre <WWKS>."""
    return (
        f'<WWKS Version="2.0" TimeStamp="{_timestamp()}">{inner}</WWKS>'
    ).encode("utf-8")


@dataclass
class Pack:
    """Un envase fisico dentro del robot."""

    article_id: str
    pack_id: str
    scan_code: str | None = None          # codigo de barras del envase (EAN/DataMatrix)
    expiry_date: str | None = None
    expiry_source: str | None = None      # "Scanned" | "AutoCalculated" | ...
    stock_in_date: str | None = None
    batch_number: str | None = None
    external_id: str | None = None
    delivery_number: str | None = None
    sub_item_quantity: int | None = None
    depth_mm: int | None = None
    width_mm: int | None = None
    height_mm: int | None = None
    shape: str | None = None
    state: str | None = None
    is_in_fridge: bool = False
    stock_location_id: str | None = None
    machine_location: str | None = None

    @property
    def volume_cm3(self) -> float | None:
        if None in (self.depth_mm, self.width_mm, self.height_mm):
            return None
        return round(self.depth_mm * self.width_mm * self.height_mm / 1000.0, 1)


@dataclass
class Article:
    """Un articulo (renglon de catalogo) con sus packs en el robot."""

    article_id: str
    name: str | None = None
    dosage_form: str | None = None
    packing_unit: str | None = None
    quantity: int = 0
    max_sub_item_quantity: int | None = None
    product_codes: list[str] = field(default_factory=list)
    packs: list[Pack] = field(default_factory=list)


class RowaError(RuntimeError):
    pass


class RowaClient:
    """Cliente WWKS2. Usar como context manager para abrir/cerrar el socket."""

    def __init__(
        self,
        host: str = ROWA_HOST,
        port: int = ROWA_PORT,
        ims_id: int = ROWA_IMS_ID,
        robot_id: int = ROWA_ROBOT_ID,
        timeout: float = ROWA_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.ims_id = ims_id
        self.robot_id = robot_id
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self.robot_info: dict[str, str] = {}

    # -- ciclo de vida ---------------------------------------------------
    def __enter__(self) -> "RowaClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> dict[str, str]:
        """Abre el socket y hace el handshake Hello. Devuelve datos del robot."""
        self._sock = socket.create_connection((self.host, self.port), self.timeout)
        self._sock.settimeout(self.timeout)
        logger.info("Rowa: conectado a %s:%s", self.host, self.port)
        return self._hello()

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    # -- transporte ------------------------------------------------------
    def _send(self, inner: str) -> str:
        if self._sock is None:
            raise RowaError("socket no conectado")
        self._sock.sendall(_envelope(inner))
        return self._recv()

    def _recv(self) -> str:
        assert self._sock is not None
        buf = b""
        while _END not in buf:
            chunk = self._sock.recv(8192)
            if not chunk:
                break
            buf += chunk
        text = buf.decode("utf-8", errors="replace")
        if _END.decode() not in text:
            raise RowaError("respuesta incompleta del robot")
        return text

    @staticmethod
    def _parse(xml: str) -> ET.Element:
        try:
            return ET.fromstring(xml)
        except ET.ParseError as e:
            raise RowaError(f"XML invalido del robot: {e}") from e

    # -- mensajes --------------------------------------------------------
    def _hello(self) -> dict[str, str]:
        inner = (
            f'<HelloRequest Id="{_msg_id()}">'
            f'<Subscriber Id="{self.ims_id}" Type="IMS" Manufacturer="AppFarmWeb" '
            f'ProductInfo="RowaModule" VersionInfo="0.1">'
            '<Capability Name="KeepAlive"/>'
            '<Capability Name="Status"/>'
            '<Capability Name="StockInfo"/>'
            '<Capability Name="Configuration"/>'
            '</Subscriber>'
            '</HelloRequest>'
        )
        root = self._parse(self._send(inner))
        sub = root.find(".//HelloResponse/Subscriber")
        if sub is None:
            raise RowaError("HelloResponse sin Subscriber")
        self.robot_info = dict(sub.attrib)
        self.robot_info["capabilities"] = ",".join(
            c.get("Name", "") for c in sub.findall("Capability")
        )
        # El robot puede anunciar su Id real; lo adoptamos como destino.
        if sub.get("Id"):
            self.robot_id = int(sub.get("Id"))
        logger.info(
            "Rowa: %s %s v%s (Id=%s)",
            self.robot_info.get("Manufacturer", "?"),
            self.robot_info.get("ProductInfo", "?"),
            self.robot_info.get("VersionInfo", "?"),
            self.robot_id,
        )
        return self.robot_info

    def keep_alive(self) -> bool:
        inner = (
            f'<KeepAliveRequest Id="{_msg_id()}" '
            f'Source="{self.ims_id}" Destination="{self.robot_id}"/>'
        )
        root = self._parse(self._send(inner))
        return root.find(".//KeepAliveResponse") is not None

    def status(self) -> dict[str, str]:
        """Estado del robot. Read-only."""
        inner = (
            f'<StatusRequest Id="{_msg_id()}" '
            f'Source="{self.ims_id}" Destination="{self.robot_id}"/>'
        )
        root = self._parse(self._send(inner))
        resp = root.find(".//StatusResponse")
        if resp is None:
            raise RowaError("sin StatusResponse")
        return dict(resp.attrib)

    def configuration(self) -> str:
        """Pide la configuracion del robot (layout/modelo). Read-only.

        No esta en la spec 1.0.5 como request formal; se manda igual porque el
        robot anuncia la capability. Devuelve el XML crudo para inspeccion.
        """
        inner = (
            f'<ConfigurationRequest Id="{_msg_id()}" '
            f'Source="{self.ims_id}" Destination="{self.robot_id}"/>'
        )
        return self._send(inner)

    def stock_info(
        self,
        include_packs: bool = True,
        include_article_details: bool = True,
        article_id: str | None = None,
    ) -> list[Article]:
        """Inventario del robot: articulos y (opcional) sus packs.

        include_packs=True trae cada envase con dimensiones y vencimiento, que es
        lo que alimenta el analisis de espacio y de vencimientos.
        """
        criteria = f'<Criteria ArticleId="{article_id}"/>' if article_id else "<Criteria/>"
        inner = (
            f'<StockInfoRequest Id="{_msg_id()}" '
            f'Source="{self.ims_id}" Destination="{self.robot_id}" '
            f'IncludePacks="{str(include_packs).lower()}" '
            f'IncludeArticleDetails="{str(include_article_details).lower()}">'
            f'{criteria}'
            '</StockInfoRequest>'
        )
        root = self._parse(self._send(inner))
        resp = root.find(".//StockInfoResponse")
        if resp is None:
            raise RowaError("sin StockInfoResponse")
        return [self._parse_article(a) for a in resp.findall("Article")]

    # -- parseo ----------------------------------------------------------
    @staticmethod
    def _parse_article(el: ET.Element) -> Article:
        art = Article(
            article_id=el.get("Id", ""),
            name=el.get("Name"),
            dosage_form=el.get("DosageForm"),
            packing_unit=el.get("PackingUnit"),
            quantity=int(el.get("Quantity", "0") or 0),
            max_sub_item_quantity=_int_or_none(el.get("MaxSubItemQuantity")),
            product_codes=[
                c.get("Code", "") for c in el.findall("ProductCode") if c.get("Code")
            ],
        )
        for p in el.findall("Pack"):
            art.packs.append(RowaClient._parse_pack(art.article_id, p))
        return art

    @staticmethod
    def _parse_pack(article_id: str, el: ET.Element) -> Pack:
        return Pack(
            article_id=article_id,
            pack_id=el.get("Id", ""),
            scan_code=el.get("ScanCode") or None,
            expiry_date=el.get("ExpiryDate"),
            expiry_source=el.get("ExpiryDateSource"),
            stock_in_date=el.get("StockInDate"),
            batch_number=el.get("BatchNumber"),
            external_id=el.get("ExternalId"),
            delivery_number=el.get("DeliveryNumber"),
            sub_item_quantity=_int_or_none(el.get("SubItemQuantity")),
            depth_mm=_int_or_none(el.get("Depth")),
            width_mm=_int_or_none(el.get("Width")),
            height_mm=_int_or_none(el.get("Height")),
            shape=el.get("Shape"),
            state=el.get("State"),
            is_in_fridge=(el.get("IsInFridge", "false").lower() == "true"),
            stock_location_id=el.get("StockLocationId"),
            machine_location=el.get("MachineLocation"),
        )


def _int_or_none(v: str | None) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except ValueError:
        return None


# -- CLI de prueba -------------------------------------------------------
def _main() -> None:
    ap = argparse.ArgumentParser(description="Sonda WWKS2 al robot Rowa")
    ap.add_argument("--host", default=ROWA_HOST)
    ap.add_argument("--port", type=int, default=ROWA_PORT)
    ap.add_argument("--status", action="store_true", help="pide StatusResponse")
    ap.add_argument("--config", action="store_true", help="pide ConfigurationResponse crudo")
    ap.add_argument("--stock", action="store_true", help="pide StockInfo")
    ap.add_argument("--no-packs", action="store_true", help="stock sin detalle de packs")
    ap.add_argument("--limit", type=int, default=5, help="articulos a mostrar")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    with RowaClient(host=args.host, port=args.port) as rowa:
        info = rowa.robot_info
        print(f"Robot: {info.get('Manufacturer')} / {info.get('ProductInfo')} "
              f"v{info.get('VersionInfo')}")
        print(f"Capabilities: {info.get('capabilities')}")

        if args.status or not (args.config or args.stock):
            print("\n[Status]", rowa.status())

        if args.config:
            print("\n[Configuration]\n" + rowa.configuration()[:2000])

        if args.stock:
            arts = rowa.stock_info(include_packs=not args.no_packs)
            total_packs = sum(len(a.packs) for a in arts)
            print(f"\n[StockInfo] {len(arts)} articulos, {total_packs} packs")
            for a in arts[: args.limit]:
                vol = sum(p.volume_cm3 or 0 for p in a.packs)
                print(f"  {a.article_id:>8}  {(a.name or ''):<28.28} "
                      f"qty={a.quantity:<4} packs={len(a.packs):<4} vol={vol:.0f}cm3")


if __name__ == "__main__":
    _main()
