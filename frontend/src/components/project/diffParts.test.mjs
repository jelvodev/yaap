// Tests of splitting a diff per file and the counts and preview of one file's diff (npm run test:unit).

import assert from "node:assert/strict";
import { test } from "node:test";

import { diffBody, diffPart, diffPreview, diffStats, splitDiff } from "./diffParts.ts";

const MODIFIED = [
  "diff --git a/README.md b/README.md",
  "index 1111111..2222222 100644",
  "--- a/README.md",
  "+++ b/README.md",
  "@@ -1,2 +1,3 @@",
  "-# old",
  "+# new",
  "+--- a horizontal rule",
  " text",
  "",
].join("\n");

const DELETED = [
  "diff --git a/gone.txt b/gone.txt",
  "deleted file mode 100644",
  "--- a/gone.txt",
  "+++ /dev/null",
  "@@ -1 +0,0 @@",
  "-bye",
  "",
].join("\n");

test("splitDiff keys each file's part by its path, a deleted file by its old path", () => {
  const parts = splitDiff(MODIFIED + DELETED);
  assert.deepEqual([...parts.keys()], ["README.md", "gone.txt"]);
  assert.equal(diffPart(parts, "gone.txt"), DELETED);
});

test("diffStats counts added and removed lines, not the header lines", () => {
  assert.deepEqual(diffStats(MODIFIED), { added: 2, removed: 1 });
  assert.deepEqual(diffStats(DELETED), { added: 0, removed: 1 });
  assert.deepEqual(diffStats(""), { added: 0, removed: 0 });
});

test("diffBody starts at the first hunk; a part without hunks stays whole", () => {
  assert.equal(diffBody(MODIFIED).split("\n")[0], "@@ -1,2 +1,3 @@");
  assert.equal(diffBody(MODIFIED).split("\n").length, 5);
  assert.equal(diffBody("Binary files differ\n"), "Binary files differ");
});

test("diffPreview shows the first lines and counts the rest", () => {
  const body = Array.from({ length: 35 }, (_, index) => `+line ${index}`).join("\n");
  const preview = diffPreview(body, 30);
  assert.equal(preview.more, 5);
  assert.equal(preview.shown.split("\n").length, 30);
  assert.deepEqual(diffPreview("+one\n+two", 30), { shown: "+one\n+two", more: 0 });
});
