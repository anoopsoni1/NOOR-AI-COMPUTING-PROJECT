import { Send } from "lucide-react";
import { FormEvent, useState } from "react";
import { useNoor } from "../hooks/useNoor";

/** Typed command box. Goes through the same conversation pipeline as speech. */
export default function TaskInput({ autoFocus = false }: { autoFocus?: boolean }) {
  const { sendText, running, health, connection } = useNoor();
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const disabled = connection !== "open" || !health?.llmConfigured;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (t.length < 2 || disabled) return;
    setError(null);
    try {
      await sendText(t);
      setText("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <form onSubmit={submit} className="w-full">
      <div className="flex gap-2">
        <input
          className="hud-input"
          placeholder={running ? "Say or type: stop, pause, what are you doing…" : "Type a command for NOOR…"}
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={2000}
          autoFocus={autoFocus}
          aria-label="Type a command"
        />
        <button type="submit" className="hud-btn hud-btn-primary" disabled={disabled || text.trim().length < 2} aria-label="Send"><Send size={14} /></button>
      </div>
      {error && <p className="mt-2 text-xs text-[var(--danger)]">{error}</p>}
    </form>
  );
}
