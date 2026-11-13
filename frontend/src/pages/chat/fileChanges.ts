import type { ToolCallResult, ToolCallStart } from "../../api/message-stream";

export interface FileChangeWatcher {
  call: (call: ToolCallStart) => void;
  result: (result: ToolCallResult) => void;
}

/**
 * Follows a reply's tool calls by id: a result names only its call's id, the call says whether it writes project
 * files. The preview reloads after each such call that succeeds.
 */
export function watchFileChanges(onChange: () => void): FileChangeWatcher {
  const writing = new Set<string>();
  return {
    call: (call) => {
      if (call.writes_files) writing.add(call.id);
    },
    result: (result) => {
      const writes = writing.delete(result.id);
      if (!result.is_error && writes) onChange();
    },
  };
}
