import protocol from "../../plugin-sdk/protocol.json";

/** The messages between YAAP's page and a plugin page's SDK, as protocol.json names them. */
export type MessageType = keyof typeof protocol.messages;

export function hostMessage(type: MessageType, fields: object): object {
  return { protocol: protocol.name, version: protocol.version, type: protocol.messages[type], ...fields };
}

/** Whether a message is the SDK's message of this type, in this protocol and version. */
export function isFrameMessage(data: unknown, type: MessageType): data is Record<string, unknown> {
  if (typeof data !== "object" || data === null) return false;
  const fields = data as Record<string, unknown>;
  return (
    fields.protocol === protocol.name && fields.version === protocol.version && fields.type === protocol.messages[type]
  );
}
