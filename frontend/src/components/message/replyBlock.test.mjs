// Tests of how a reply's blocks are shown (npm run test:unit). Node strips the types of replyBlock.ts as it loads it.

import assert from "node:assert/strict";
import { test } from "node:test";

import { toolLineLabel } from "./replyBlock.ts";

const LINES = [
  // tool names in order, line
  [
    ["list_files", "run_command", "write_file", "write_file", "write_file", "write_file"],
    "list_files · run_command · 4 × write_file",
  ],
  [["edit_file", "write_file", "edit_file"], "2 × edit_file · write_file"],
  [["start_process"], "start_process"],
  [[], ""],
];

for (const [names, line] of LINES) {
  test(`a tool line names ${names.length} calls as "${line}"`, () => {
    assert.equal(toolLineLabel(names.map((name) => ({ name }))), line);
  });
}
