import type { ToolCallResult, ToolCallStart } from "../../api/message-stream";

/** Tool calls that change a project file the dev server may serve: the preview reloads after each that succeeds. */
export const FILE_CHANGING_TOOLS = new Set(["write_file", "edit_file", "rename_symbol"]);

export interface FileChangeWatcher {
  call: (call: ToolCallStart) => void;
  result: (result: ToolCallResult) => void;
}

/** Follows a reply's tool calls by id: a result names only its call's id, the call names the tool. */
export function watchFileChanges(onChange: () => void): FileChangeWatcher {
  const names = new Map<string, string>();
  return {
    call: (call) => {
      names.set(call.id, call.name);
    },
    result: (result) => {
      const name = names.get(result.id);
      names.delete(result.id);
      if (!result.is_error && name !== undefined && FILE_CHANGING_TOOLS.has(name)) onChange();
    },
  };
}
