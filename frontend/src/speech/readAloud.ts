import { useSyncExternalStore } from "react";

import { api, errorMessage } from "../api/client";

/**
 * YAAP speaking: replies read aloud with the browser's own voices (`speechSynthesis`). YAAP gives the
 * text without thinking, tool calls and code blocks, and its language; the browser speaks it.
 */

const EVERY_REPLY_KEY = "yaap-read-every-reply";
// Chrome stops a long utterance after about fifteen seconds, so a reply is spoken in parts of about this length.
const PART_LENGTH = 220;

interface ReadAloudState {
  // The reply being read, or null.
  messageId: number | null;
  // Read every reply that finishes, in this browser.
  everyReply: boolean;
  error: string | null;
}

let state: ReadAloudState = { messageId: null, everyReply: readEveryReply(), error: null };
const listeners = new Set<() => void>();
// Each reading gets a number, so the end of an older one does not clear a newer one.
let reading = 0;

function readEveryReply(): boolean {
  try {
    return window.localStorage.getItem(EVERY_REPLY_KEY) === "true";
  } catch {
    return false;
  }
}

function setState(change: Partial<ReadAloudState>) {
  state = { ...state, ...change };
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useReadAloud(): ReadAloudState {
  return useSyncExternalStore(subscribe, () => state);
}

export function canReadAloud(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
}

/** The preference to read every reply aloud is kept per browser: voices and speakers belong to the device. */
export function setReadEveryReply(on: boolean) {
  try {
    window.localStorage.setItem(EVERY_REPLY_KEY, String(on));
  } catch {
    // Without storage the choice lasts until the page is reloaded.
  }
  if (!on) stopReading();
  setState({ everyReply: on });
}

export function readsEveryReply(): boolean {
  return state.everyReply && canReadAloud();
}

/** Sentences packed into parts of about PART_LENGTH characters; a long sentence is split at commas or spaces. */
export function speechParts(text: string): string[] {
  const parts: string[] = [];
  for (const paragraph of text.split(/\n{2,}/)) {
    const sentences = paragraph.match(/[^.!?…]+(?:[.!?…]+["')\]]*|$)\s*/g) ?? [paragraph];
    let current = "";
    for (const sentence of sentences.flatMap((item) => splitLong(item.trim()))) {
      if (!sentence) continue;
      if (current && current.length + sentence.length + 1 > PART_LENGTH) {
        parts.push(current);
        current = sentence;
      } else {
        current = current ? `${current} ${sentence}` : sentence;
      }
    }
    if (current) parts.push(current);
  }
  return parts;
}

function splitLong(sentence: string): string[] {
  if (sentence.length <= PART_LENGTH) return [sentence];
  const pieces: string[] = [];
  let rest = sentence;
  while (rest.length > PART_LENGTH) {
    const head = rest.slice(0, PART_LENGTH);
    const cut = Math.max(head.lastIndexOf(", "), head.lastIndexOf("; "));
    const space = head.lastIndexOf(" ");
    const at = cut > PART_LENGTH / 3 ? cut + 1 : space > 0 ? space : PART_LENGTH;
    pieces.push(rest.slice(0, at).trim());
    rest = rest.slice(at).trim();
  }
  if (rest) pieces.push(rest);
  return pieces;
}

async function voices(): Promise<SpeechSynthesisVoice[]> {
  const now = window.speechSynthesis.getVoices();
  if (now.length > 0) return now;
  // Chrome loads its voices after the page; wait briefly for them.
  return new Promise((resolve) => {
    const done = () => resolve(window.speechSynthesis.getVoices());
    window.speechSynthesis.addEventListener("voiceschanged", done, { once: true });
    window.setTimeout(done, 1000);
  });
}

/** A voice for the language, one that runs on this computer first: the online voices send the text to a service. */
export function chooseVoice(available: SpeechSynthesisVoice[], language: string | null): SpeechSynthesisVoice | null {
  if (!language) return available.find((voice) => voice.default && voice.localService) ?? null;
  const matching = available.filter((voice) => voice.lang.toLowerCase().split(/[-_]/)[0] === language.toLowerCase());
  return (
    matching.find((voice) => voice.localService && voice.default) ??
    matching.find((voice) => voice.localService) ??
    matching[0] ??
    null
  );
}

export function stopReading() {
  reading += 1;
  if (canReadAloud()) window.speechSynthesis.cancel();
  if (state.messageId !== null) setState({ messageId: null });
}

/** Read a stored reply aloud; reading another reply or pressing stop ends the current one. */
export async function readAloud(messageId: number): Promise<void> {
  if (!canReadAloud()) return;
  stopReading();
  const mine = reading;
  setState({ messageId, error: null });
  const { data, error } = await api.GET("/api/v1/messages/{message_id}/speech", {
    params: { path: { message_id: messageId } },
  });
  if (mine !== reading) return;
  if (!data) {
    setState({ messageId: null, error: errorMessage(error, "This reply could not be read aloud.") });
    return;
  }
  const parts = speechParts(data.text);
  if (parts.length === 0) {
    setState({ messageId: null });
    return;
  }
  const voice = chooseVoice(await voices(), data.language ?? null);
  if (mine !== reading) return;
  parts.forEach((part, index) => {
    const utterance = new SpeechSynthesisUtterance(part);
    if (voice) utterance.voice = voice;
    if (voice || data.language) utterance.lang = voice?.lang ?? data.language ?? "";
    if (index === parts.length - 1) {
      utterance.onend = () => {
        if (mine === reading) setState({ messageId: null });
      };
    }
    utterance.onerror = (event) => {
      if (mine !== reading || event.error === "canceled" || event.error === "interrupted") return;
      reading += 1;
      window.speechSynthesis.cancel();
      setState({ messageId: null, error: `The browser could not read this reply aloud (${event.error}).` });
    };
    window.speechSynthesis.speak(utterance);
  });
}
