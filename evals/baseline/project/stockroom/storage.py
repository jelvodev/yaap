"""Saving and loading the inventory as one JSON file.

The file holds the items, the movements and the reservations. Writing goes through a temporary file that replaces
the old one, so a crash never leaves half a file behind.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from stockroom.inventory import Inventory
from stockroom.models import Item, Movement, MovementKind, Reservation

FORMAT_VERSION = 2


class StorageError(Exception):
    """The file cannot be read as a stockroom file."""


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


def _moment(text: str) -> datetime:
    return datetime.fromisoformat(text)


def item_to_dict(item: Item) -> dict[str, Any]:
    return {
        "sku": item.sku,
        "name": item.name,
        "category": item.category,
        "location": item.location,
        "quantity": item.quantity,
        "unit_cost_cents": item.unit_cost_cents,
        "reorder_level": item.reorder_level,
        "reorder_quantity": item.reorder_quantity,
        "reserved": item.reserved,
    }


def item_from_dict(data: dict[str, Any]) -> Item:
    try:
        return Item(
            sku=str(data["sku"]),
            name=str(data["name"]),
            category=str(data.get("category", "")),
            location=str(data.get("location", "")),
            quantity=int(data["quantity"]),
            unit_cost_cents=int(data["unit_cost_cents"]),
            reorder_level=int(data.get("reorder_level", 0)),
            reorder_quantity=int(data.get("reorder_quantity", 0)),
            reserved=int(data.get("reserved", 0)),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise StorageError(f"An item cannot be read: {error}") from error


def to_dict(inventory: Inventory) -> dict[str, Any]:
    movements, reservations = inventory.state()
    return {
        "version": FORMAT_VERSION,
        "items": [item_to_dict(item) for item in inventory.items()],
        "movements": [
            {"sku": m.sku, "kind": m.kind.value, "quantity": m.quantity, "at": _stamp(m.at), "note": m.note}
            for m in movements
        ],
        "reservations": [
            {
                "id": r.id,
                "sku": r.sku,
                "quantity": r.quantity,
                "expires_at": _stamp(r.expires_at),
                "customer": r.customer,
                "released": r.released,
            }
            for r in reservations
        ],
    }


def from_dict(data: dict[str, Any], inventory: Inventory | None = None) -> Inventory:
    version = data.get("version", 1)
    if version not in (1, FORMAT_VERSION):
        raise StorageError(f"Unknown file version {version}.")
    inventory = inventory or Inventory()
    for raw in data.get("items", []):
        inventory.add_item(item_from_dict(raw))
    try:
        movements = [
            Movement(raw["sku"], MovementKind(raw["kind"]), int(raw["quantity"]), _moment(raw["at"]), raw.get("note", ""))
            for raw in data.get("movements", [])
        ]
        reservations = [
            Reservation(
                id=raw["id"],
                sku=raw["sku"],
                quantity=int(raw["quantity"]),
                expires_at=_moment(raw["expires_at"]),
                customer=raw.get("customer", ""),
                released=bool(raw.get("released", False)),
            )
            for raw in data.get("reservations", [])
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise StorageError(f"A movement or reservation cannot be read: {error}") from error
    inventory.load_state(movements, reservations)
    return inventory


def save(inventory: Inventory, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(to_dict(inventory), indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load(path: Path, inventory: Inventory | None = None) -> Inventory:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise StorageError(f"{path} does not exist.") from error
    except json.JSONDecodeError as error:
        raise StorageError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise StorageError(f"{path} does not hold a stockroom file.")
    return from_dict(data, inventory)
