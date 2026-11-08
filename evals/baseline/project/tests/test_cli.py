import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from stockroom import cli, storage
from tests.helpers import make_inventory


class CommandLine(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.data = Path(self.folder.name) / "stock.json"
        storage.save(make_inventory(), self.data)

    def tearDown(self) -> None:
        self.folder.cleanup()

    def run_cli(self, *arguments: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--data", str(self.data), *arguments])
        return code, out.getvalue(), err.getvalue()

    def test_report_and_search_print_without_changing_the_file(self) -> None:
        before = self.data.read_text(encoding="utf-8")
        code, out, _ = self.run_cli("report")
        self.assertEqual(code, 0)
        self.assertIn("Stock report", out)
        self.assertIn("SR-1", self.run_cli("search", "hammer")[1])
        self.assertEqual(self.run_cli("search", "nothing")[1], "No items found.\n")
        self.assertEqual(self.data.read_text(encoding="utf-8"), before)

    def test_receiving_saves_the_file_and_errors_go_to_stderr(self) -> None:
        self.assertEqual(self.run_cli("receive", "SR-4", "3")[0], 0)
        self.assertEqual(storage.load(self.data).get("SR-4").quantity, 5)
        code, _, err = self.run_cli("ship", "SR-4", "50")
        self.assertEqual(code, 1)
        self.assertIn("only 5 available", err)

    def test_export_writes_a_file_and_other_reports_run(self) -> None:
        target = Path(self.folder.name) / "out.csv"
        self.assertIn("Wrote 4 items", self.run_cli("export", "--output", str(target))[1])
        self.assertTrue(target.read_text(encoding="utf-8").startswith("sku,name"))
        self.assertIn("Valuation", self.run_cli("valuation")[1])
        self.assertIn("Restock plan", self.run_cli("restock")[1])


if __name__ == "__main__":
    unittest.main()
