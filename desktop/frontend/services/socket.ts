import type { ServerEvent } from "../types";
import { API_TOKEN, BACKEND_URL } from "./api";

export type ConnectionStatus = "connecting" | "open" | "closed";

/** WebSocket client with automatic reconnect (exponential backoff, capped at 5s). Text = JSON events, binary = mic audio. */
export class NoorSocket {
  private ws: WebSocket | null = null;
  private retry = 0;
  private stopped = false;
  private timer: number | undefined;

  constructor(
    private onEvent: (e: ServerEvent) => void,
    private onStatus: (s: ConnectionStatus) => void,
  ) {}

  connect(): void {
    this.stopped = false;
    this.onStatus("connecting");
    const url = BACKEND_URL.replace(/^http/, "ws") + "/ws" + (API_TOKEN ? `?token=${encodeURIComponent(API_TOKEN)}` : "");
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.onStatus("open");
    };
    ws.onmessage = (m) => {
      if (typeof m.data !== "string") return;
      try {
        this.onEvent(JSON.parse(m.data) as ServerEvent);
      } catch { /* ignore malformed frames */ }
    };
    ws.onclose = () => {
      this.onStatus("closed");
      if (this.stopped) return;
      this.timer = window.setTimeout(() => this.connect(), Math.min(5000, 500 * 2 ** this.retry++));
    };
    ws.onerror = () => ws.close();
  }

  send(msg: Record<string, unknown>): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify(msg));
    return true;
  }

  sendBinary(data: ArrayBuffer): void {
    if (this.ws?.readyState === WebSocket.OPEN && this.ws.bufferedAmount < 1_000_000) this.ws.send(data);
  }

  close(): void {
    this.stopped = true;
    window.clearTimeout(this.timer);
    this.ws?.close();
  }
}
