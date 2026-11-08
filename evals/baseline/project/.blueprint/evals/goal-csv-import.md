---
summary: "Baseline: a long multi-step task (new module, CLI command, tests and docs)"
start:
  commit: 8916086225c95afd2d35e7e00a5614ee4099cc15
allowed_tools: [list_files, read_file, search, edit_file, write_file, run_command, delegate]
budget:
  steps: 120 steps
  tokens: 4M tokens
  time: 45 min
checks:
  - kind: file
    name: the importer exists
    path: stockroom/importer.py
    matches: '^def import_items\('
  - kind: command
    name: importing adds, updates and reports bad lines
    run: >-
      python3 -c "import pathlib, tempfile;
      from stockroom.importer import import_items;
      from stockroom.inventory import Inventory;
      from stockroom.models import Item;
      inventory = Inventory([Item('SR-1', 'Hammer', 'tools', 'A1', 5, 1000)]);
      path = pathlib.Path(tempfile.mkdtemp()) / 'in.csv';
      path.write_text('sku,name,category,quantity,unit_cost\nSR-1,Claw hammer,tools,3,12.50\nSR-2,Saw,tools,4,20.00\nSR-3,Bad,tools,many,1.00\n', encoding='utf-8');
      result = import_items(inventory, path);
      assert (result.added, result.updated) == (1, 1), result;
      assert [number for number, _ in result.errors] == [4], result.errors;
      hammer = inventory.get('SR-1');
      assert (hammer.name, hammer.quantity, hammer.unit_cost_cents) == ('Claw hammer', 8, 1250), hammer;
      assert inventory.get('SR-2').quantity == 4 and 'SR-3' not in inventory"
    timeout: 1 min
  - kind: command
    name: the CLI imports and saves
    run: >-
      python3 -c "import pathlib, shutil, subprocess, sys, tempfile;
      from stockroom import storage;
      folder = pathlib.Path(tempfile.mkdtemp()); data = folder / 'stock.json';
      shutil.copy('stockroom/data/sample.json', data);
      (folder / 'in.csv').write_text('sku,name,category,quantity,unit_cost\nSR-2001,Spade,garden,6,18.40\n', encoding='utf-8');
      done = subprocess.run([sys.executable, '-m', 'stockroom.cli', '--data', str(data), 'import', str(folder / 'in.csv')]);
      assert done.returncode == 0;
      assert storage.load(data).get('SR-2001').unit_cost_cents == 1840"
    timeout: 1 min
  - kind: command
    name: the importer has tests
    run: >-
      python3 -c "import pathlib, sys;
      path = pathlib.Path('tests/test_importer.py');
      sys.exit(0 if path.exists() and path.read_text(encoding='utf-8').count('def test_') >= 3 else 1)"
    timeout: 1 min
  - kind: command
    name: the tests pass
    run: python3 -m unittest discover -s tests -t .
    timeout: 5 min
  - kind: file
    name: the README explains it
    path: README.md
    contains: "## Importing items"
  - kind: rubric
    name: fits the codebase
    rubric: >-
      The importer follows the project's conventions (money in whole cents, quantities changed through Inventory so
      a movement is recorded, errors collected per line instead of stopping), the CLI command saves the data file
      only when something changed, and the tests cover new SKUs, updated SKUs, bad lines and a file without the
      required columns.
    files: [stockroom/importer.py, stockroom/cli.py, tests/test_importer.py]
---

Suppliers send their price lists as CSV, and we want to import them (issue SR-13). Work through it until it is done:

1. A module `stockroom/importer.py` with `import_items(inventory, path) -> ImportResult`. The CSV has the columns
   `sku`, `name`, `category`, `quantity` and `unit_cost` (euros with a dot, such as `12.50`), and optionally
   `location` and `reorder_level`. A new SKU is added (location `INBOX` when none is given). For a known SKU the
   name, category and unit cost are updated and the quantity is received as stock, so a movement is recorded.
2. `ImportResult` has `added` and `updated` (counts) and `errors`: a list of `(line number, message)` for lines that
   were skipped, where the header is line 1. A bad line never stops the import.
3. A CLI command `python3 -m stockroom.cli import <file.csv>` that prints a summary and the errors, and saves the
   data file when something changed.
4. Tests in `tests/test_importer.py`, and a section `## Importing items` in the README.

Run the tests at the end.
