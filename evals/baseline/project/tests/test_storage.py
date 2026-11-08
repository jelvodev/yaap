import json
import tempfile
import unittest
from pathlib import Path

from stockroom import storage
from tests.helpers import make_inventory


class Storage(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "stock.json"

    def tearDown(self) -> None:
        self.folder.cleanup()

    def test_items_movements_and_reservations_survive_a_round_trip(self) -> None:
        inventory = make_inventory()
        inventory.receive("SR-1", 2, "delivery")
        inventory.reserve("SR-2", 3, "Jansen")
        storage.save(inventory, self.path)
        loaded = storage.load(self.path)
        self.assertEqual([item.sku for item in loaded.items()], ["SR-1", "SR-2", "SR-3", "SR-4"])
        self.assertEqual(loaded.get("SR-1").quantity, 12)
        self.assertEqual(len(loaded.movements()), 1)
        self.assertEqual([(r.id, r.quantity) for r in loaded.reservations()], [("R-0001", 3)])
        self.assertFalse(self.path.with_suffix(".json.tmp").exists())

    def test_files_that_are_not_stockroom_files_are_refused(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(storage.StorageError):
            storage.load(self.path)
        self.path.write_text(json.dumps({"version": 9}), encoding="utf-8")
        with self.assertRaises(storage.StorageError):
            storage.load(self.path)
        self.path.write_text(json.dumps({"items": [{"sku": "X"}]}), encoding="utf-8")
        with self.assertRaises(storage.StorageError):
            storage.load(self.path)
        with self.assertRaises(storage.StorageError):
            storage.load(self.path.with_name("missing.json"))


if __name__ == "__main__":
    unittest.main()
