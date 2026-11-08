import { useEffect, useRef, useState } from "react";

import {
  ModelNotReady,
  type SpeechScope,
  type SpeechStatus,
  downloadSpeechModel,
  megabytes,
  recordingType,
  speechStatus,
  transcribe,
} from "./transcription";
import "./speech.css";

// Longer recordings are cut off here; YAAP accepts up to ten minutes.
const MAX_RECORDING_MS = 5 * 60 * 1000;

type Phase = "idle" | "checking" | "downloading" | "recording" | "transcribing";

function MicIcon({ size = 16 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
    </svg>
  );
}

function elapsed(ms: number): string {
  const seconds = Math.floor(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function downloadText(status: SpeechStatus): string {
  const share = status.size ? Math.floor((status.downloaded / status.size) * 100) : 0;
  return `Downloading the speech model: ${share}% of ${megabytes(status.size)}. This happens once; click the microphone again when it is ready.`;
}

/**
 * Speaking to YAAP: records in the browser and puts YAAP's local transcription in the input field
 * for review. The audio goes only to YAAP, which runs Whisper itself; the browser's own speech recognition is not used.
 */
export default function MicButton({ scope, onText }: { scope: SpeechScope; onText: (text: string) => void }) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  const [startedAt, setStartedAt] = useState(0);
  const [now, setNow] = useState(0);
  const recorder = useRef<MediaRecorder | null>(null);
  const poll = useRef<number | null>(null);
  const mounted = useRef(true);
  const scopeRef = useRef(scope);
  scopeRef.current = scope;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (poll.current !== null) window.clearTimeout(poll.current);
      // Leaving the conversation drops a recording in progress.
      const active = recorder.current;
      recorder.current = null;
      if (active && active.state !== "inactive") active.stop();
    };
  }, []);

  useEffect(() => {
    if (phase !== "recording") return;
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, [phase]);

  useEffect(() => {
    // An informational note goes away by itself; an error stays until the next click.
    if (!note || note.error || phase !== "idle") return;
    const timer = window.setTimeout(() => setNote(null), 6000);
    return () => window.clearTimeout(timer);
  }, [note, phase]);

  function follow(status: SpeechStatus) {
    if (!mounted.current) return;
    if (status.state === "downloading") {
      setPhase("downloading");
      setNote({ text: downloadText(status) });
      poll.current = window.setTimeout(() => {
        void speechStatus(scopeRef.current)
          .then(follow)
          .catch((error: unknown) => fail(error));
      }, 1000);
      return;
    }
    setPhase("idle");
    if (status.state === "ready") setNote({ text: "The speech model is ready. Click the microphone to speak." });
    else setNote({ text: status.message, error: true });
  }

  function fail(error: unknown) {
    if (!mounted.current) return;
    setPhase("idle");
    setNote({ text: error instanceof Error ? error.message : String(error), error: true });
  }

  async function start() {
    setNote(null);
    if (!navigator.mediaDevices?.getUserMedia || !recordingType()) {
      setNote({
        text: "This browser cannot record here. Open YAAP at http://localhost or 127.0.0.1, or over https.",
        error: true,
      });
      return;
    }
    setPhase("checking");
    let status: SpeechStatus;
    try {
      status = await speechStatus(scope);
      if (status.state === "missing" || status.state === "failed") status = await downloadSpeechModel(scope);
    } catch (error) {
      fail(error);
      return;
    }
    if (status.state !== "ready") {
      follow(status);
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      fail(new Error("YAAP may not use the microphone. Allow it in the browser's site settings and try again."));
      return;
    }
    if (!mounted.current) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    const chunks: Blob[] = [];
    const media = new MediaRecorder(stream, { mimeType: recordingType() });
    media.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data);
    };
    media.onstop = () => {
      stream.getTracks().forEach((track) => track.stop());
      const wanted = recorder.current === media;
      recorder.current = null;
      if (wanted) void finish(new Blob(chunks, { type: media.mimeType }));
    };
    recorder.current = media;
    media.start();
    const at = Date.now();
    setStartedAt(at);
    setNow(at);
    setPhase("recording");
    window.setTimeout(() => {
      if (recorder.current === media && media.state === "recording") media.stop();
    }, MAX_RECORDING_MS);
  }

  async function finish(recording: Blob) {
    if (!mounted.current) return;
    setPhase("transcribing");
    try {
      const result = await transcribe(scopeRef.current, recording);
      if (!mounted.current) return;
      setPhase("idle");
      if (result.text.trim()) onText(result.text.trim());
      else setNote({ text: "Nothing was heard. Check the microphone and try again.", error: true });
    } catch (error) {
      if (error instanceof ModelNotReady) {
        void speechStatus(scopeRef.current).then(follow).catch(fail);
        return;
      }
      fail(error);
    }
  }

  function click() {
    if (phase === "recording") {
      recorder.current?.stop();
      return;
    }
    if (phase === "idle") void start();
  }

  const busy = phase === "checking" || phase === "downloading" || phase === "transcribing";
  const label = phase === "recording" ? "Stop recording" : "Speak: record a message to transcribe";
  const status =
    phase === "recording"
      ? `Recording ${elapsed(now - startedAt)}. Click the microphone to stop.`
      : phase === "transcribing"
        ? "Transcribing on this computer…"
        : phase === "checking"
          ? "Checking the speech model…"
          : note?.text;
  return (
    <span className="speech-mic">
      <button
        type="button"
        className={`attach-button mic-button${phase === "recording" ? " mic-recording" : ""}`}
        onClick={click}
        disabled={busy}
        aria-label={label}
        aria-pressed={phase === "recording"}
        title={label}
      >
        <MicIcon size={16} />
      </button>
      {status && (
        <span className={`speech-note${note?.error && phase === "idle" ? " speech-note-error" : ""}`} role="status">
          {status}
          {phase === "idle" && note && (
            <button type="button" className="speech-note-close" aria-label="Close" onClick={() => setNote(null)}>
              ×
            </button>
          )}
        </span>
      )}
    </span>
  );
}
