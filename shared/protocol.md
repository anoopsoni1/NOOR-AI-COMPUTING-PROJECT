# NOOR wire protocol

Single source of truth for the backend ⇄ desktop UI contract. TypeScript mirrors live in
`desktop/frontend/types/index.ts`; Python models in `backend/agent/state.py`.

## WebSocket `ws://127.0.0.1:8000/ws` (optional `?token=<API_TOKEN>`)

Server → client

| type       | payload                          | when                                             |
|------------|----------------------------------|--------------------------------------------------|
| `hello`    | `task: Task \| null`             | on connect (current task, if any)                |
| `task`     | `task: Task`                     | every state / plan / step / log change           |
| `finished` | `taskId`, `status`               | task reached COMPLETED / FAILED / CANCELLED      |
| `pong`     | –                                | reply to `ping`                                  |

Client → server

| type                  | payload                  | effect                                     |
|-----------------------|--------------------------|--------------------------------------------|
| `stop`                | –                        | emergency stop                             |
| `permission_response` | `id`, `approve: bool`    | approve / cancel a pending action          |
| `respond`             | `text`                   | answer a WAITING_FOR_USER question         |
| `ping`                | –                        | keep-alive                                 |

## Agent states

`IDLE → PLANNING → WAITING_FOR_PERMISSION → EXECUTING → OBSERVING → VERIFYING → RECOVERING → COMPLETED`
plus `FAILED`, `CANCELLED`, `WAITING_FOR_USER`.

## Plan step

```json
{ "id": 2, "tool": "type_text", "description": "Search for React docs",
  "args": { "target": "search box", "text": "react documentation", "submit": true },
  "expect": "Search results are shown", "verify": { "kind": "url_contains", "value": "search" },
  "status": "pending|running|done|failed|skipped" }
```

## Tool result (every tool)

```json
{ "success": true, "action": "click", "target": "Download button", "message": "Clicked Download button",
  "error_code": null, "data": {} }
```

## REST (prefix `/api`)

`GET /health` · `GET /stats` · `POST /tasks {goal}` · `GET /tasks` · `GET /tasks/current` · `GET /tasks/{id}` ·
`DELETE /tasks/{id}` · `POST /tasks/stop` · `POST /tasks/respond {text}` ·
`POST /permissions/requests/{id} {approve}` · `GET|PUT /permissions/rules[/{tool}]` · `GET /permissions/log` ·
`GET|POST /memories` · `PUT|DELETE /memories/{id}` · `GET|PUT /settings`

## Voice events (NOOR)

Server → client: `voice.state {state, engaged, mic, tts, wakeWord, stt, tts_provider, vad}` · `voice.started {reason: wake|activate}` ·
`voice.activity {speaking}` · `voice.interim {text}` · `voice.transcribed {text, source, final}` · `voice.error {message}` ·
`assistant.delta {text}` · `assistant.message {text}` · `tts.started` · `tts.chunk {epoch, mime, data(base64)}` · `tts.stop {epoch}` ·
`tts.duck {on}` · `tts.finished` · `agent.thinking|planning|executing|verifying|recovering|waiting_for_permission|waiting_for_user|completed|failed|cancelled`.

Client → server: binary frames = microphone audio (16 kHz mono PCM16, 20 ms) · `user_text {text}` · `voice_enable {enabled}` ·
`tts_enable {enabled}` · `tts_idle {epoch}` (playback finished) · `activate` · `stop` · `permission_response {id, approve}` · `respond {text}`.

REST additions: `GET /api/voice` · `POST /api/tasks/pause` · `POST /api/tasks/resume`.
