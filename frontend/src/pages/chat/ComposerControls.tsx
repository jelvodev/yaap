import type { AccessLevel } from "./useActiveConversation";
import type { TargetInfo } from "./useChatCatalog";
import type { DraftConversation } from "./useDraftConversation";

/** What a conversation may do without asking, chosen in the composer. */
const ACCESS_LEVELS: { value: AccessLevel; label: string; title: string }[] = [
  { value: "default", label: "as set", title: "Asks as the tool permissions in Settings say" },
  {
    value: "full",
    label: "full",
    title:
      "Runs every tool without asking, also after reading the web; only pushing and commands with a secret still ask",
  },
  { value: "read_only", label: "read only", title: "Reads and searches, but changes nothing" },
];

export function AccessSelect({ value, onChange }: { value: AccessLevel; onChange: (access: AccessLevel) => void }) {
  return (
    <>
      <label className="visually-hidden" htmlFor="chat-access">
        Access
      </label>
      <select
        id="chat-access"
        value={value}
        title={ACCESS_LEVELS.find((level) => level.value === value)?.title}
        onChange={(event) => {
          const chosen = ACCESS_LEVELS.find((level) => level.value === event.target.value);
          if (chosen) onChange(chosen.value);
        }}
      >
        {ACCESS_LEVELS.map((level) => (
          <option key={level.value} value={level.value} title={level.title}>
            access · {level.label}
          </option>
        ))}
      </select>
    </>
  );
}

/**
 * Outside a project, where a new conversation works: YAAP's container or a computer from Settings › Targets. The
 * agent, preset, model and limit of a new conversation are in the model setup (`ModelSetupButton`).
 */
export function TargetSelect({ draft, targets }: { draft: DraftConversation; targets: TargetInfo[] }) {
  if (targets.length === 0) return null;
  return (
    <>
      <label className="visually-hidden" htmlFor="draft-target">
        Where the agent works
      </label>
      <select
        id="draft-target"
        value={draft.target}
        title="Where this conversation works; fixed after its first message"
        onChange={(event) => draft.setTarget(event.target.value)}
      >
        <option value="">on · YAAP&apos;s container</option>
        {targets.map((target) => (
          <option key={target.id} value={target.id} disabled={target.state === "new" || target.state === "confirm"}>
            on · {target.name}
          </option>
        ))}
      </select>
    </>
  );
}
