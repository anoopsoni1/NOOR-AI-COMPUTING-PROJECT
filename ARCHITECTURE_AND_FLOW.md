# NOOR AI Computing Agent: Architecture & Code Flow

This document provides a comprehensive, production-grade reference for the **NOOR AI Computing Agent**. It details the complete system architecture, module boundaries, data pipelines, hands-free voice state machine, Kokoro local TTS integration, LangGraph execution loop, tool framework, permissions engine, and step-by-step code execution flows.

---

## 1. High-Level System Architecture

NOOR is an autonomous, voice-first AI computing agent that executes complex tasks on the user's computer via browser automation, desktop control, and filesystem actions — completely locally or with optional cloud acceleration.

```mermaid
flowchart TB
    subgraph Frontend ["Desktop Client (Electron + React 18 + Tailwind)"]
        UI["HUD & Particle Orb (WebGL/Canvas)"]
        AudioClient["Web Audio API / PCM Downsampler"]
        SettingsUI["Voice & Model Settings"]
        WSClient["WebSocket /ws Client"]
    end

    subgraph Gateway ["FastAPI Gateway & Realtime Hub"]
        WS["WebSocket /ws Handler"]
        RestAPI["REST API (/api/voice/*, /api/llm/*, /api/tasks/*)"]
        Hub["EventHub Broadcaster"]
    end

    subgraph AudioEngine ["Voice & Speech Pipeline"]
        VAD["RMS Voice Activity Detector"]
        WakeWord["Wake Word Engine ('Hey Noor')"]
        STT["Faster-Whisper (Local CT2 / ONNX)"]
        EchoGuard["Acoustic Echo & Mic Suppression"]
    end

    subgraph TTSManagerEngine ["Modular TTS Manager & Engines"]
        TTSMgr["TTSManager Switchboard"]
        Kokoro["Kokoro TTS (Local 82M ONNX · 24kHz · Free)"]
        EdgeTTS["Edge-TTS (Cloud Neural · Free)"]
        Piper["Piper TTS (Local Fast ONNX)"]
        SystemSAPI["Windows SAPI (Offline Built-in)"]
        ElevenLabs["ElevenLabs (Optional Cloud API)"]
    end

    subgraph Brain ["Conversation & Intelligence Layer"]
        ConvMgr["Conversation Manager"]
        Classifier["Intent Classifier (TurnOut)"]
        LLM["LLM Client (Groq / Ollama / Gemini)"]
    end

    subgraph AgentLoop ["LangGraph Task Execution Loop"]
        Planner["Planner Node (PlanOut + Tool Normalization)"]
        Executor["Executor Node (Tool Selection & Execution)"]
        Observer["Observer Node (DOM & Visual Verification)"]
        Recovery["Recovery / Replanning Node"]
    end

    subgraph HostSystem ["Operating System & Automation"]
        Playwright["Playwright Chromium Automation"]
        Desktop["PyAutoGUI / Mouse & Keyboard"]
        FileSystem["Local Workspace & Filesystem Tools"]
        Mongo["MongoDB Atlas / Local Embedded Store"]
    end

    AudioClient <-->|16kHz PCM16 Stream| WS
    WS <--> AudioEngine
    AudioEngine --> Brain
    Brain --> AgentLoop
    Brain --> TTSMgr
    TTSMgr --> Kokoro
    TTSMgr --> EdgeTTS
    TTSMgr --> Piper
    AgentLoop --> HostSystem
    AgentLoop --> Hub
    Hub --> WSClient
```

---

## 2. Voice & Audio Interaction Pipeline

NOOR uses a full-duplex, low-latency audio pipeline designed to prevent self-echo and ensure reliable hands-free operation.

### Automatic Microphone Arbitration Flow
```
USER SPEAKS
    ↓
SPEECH RECOGNITION (Faster-Whisper Local)
    ↓
LLM RESPONSE / INTENT CLASSIFICATION
    ↓
KOKORO TTS SYNTHESIS (Local 24kHz ONNX)
    ↓
MICROPHONE AUTO-PAUSE (Prevents Hearing Own Voice)
    ↓
AUDIO PLAYBACK (Frontend / speaker)
    ↓
PLAYBACK COMPLETED EVENT (tts_idle)
    ↓
MICROPHONE AUTO-RESUMES (LISTENING MODE)
```

### Detailed Component Roles
1. **Frontend PCM Streamer (`desktop/frontend/services/audio.ts`)**:
   - Captures microphone input via `navigator.mediaDevices.getUserMedia`.
   - Downsamples system audio sample rate (e.g., 44.1 kHz or 48 kHz) to **16 kHz mono 16-bit PCM** in 20 ms frames (320 samples).
   - Downsampling is performed inside an `AudioWorkletNode` (with automatic fallback to `ScriptProcessorNode`).
   - Streams frames in real time over the WebSocket connection (`sendBinary`).

2. **Voice Activity Detection (`backend/voice/vad.py`)**:
   - Calculates root-mean-square (RMS) energy per 20 ms chunk.
   - Compares energy against adaptive thresholds (`speech_start_threshold: 0.012`, `speech_end_threshold: 0.008`).
   - Automatically raises VAD sensitivity when NOOR is speaking to reject speaker spillover.

3. **Wake Word Detection (`backend/voice/wake.py`)**:
   - When in `IDLE` state, incoming audio segments are transcribed with prompt biasing towards the configured wake phrase (`Hey Noor`).
   - Phonetic and normalized fuzzy string matching triggers `_on_wake()`, transitioning NOOR to the `LISTENING` state.
   - When the user taps the Particle Orb or sends a command via keyboard, wake-word matching is bypassed (`engaged` mode).

4. **Speech-to-Text (`backend/voice/speech_to_text.py`)**:
   - Powered by `faster-whisper` (`base.en` or `small` quantized model using CTranslate2).
   - Performs streaming interim transcripts during active speech for responsive visual feedback.
   - Emits finalized text to the Conversation Manager upon end-of-speech silence detection.

---

## 3. Kokoro Local TTS Integration

Kokoro is an 82-million parameter high-fidelity neural Text-to-Speech model capable of generating studio-grade 24 kHz speech on local hardware without cloud API keys or external costs.

### Technical Specifications
- **Model Architecture**: Kokoro 82M (`kokoro-v1.0.onnx`, ~325 MB).
- **Voice Embeddings**: 512-dimensional voice vectors (`voices-v1.0.bin`, ~28 MB).
- **Native Sample Rate**: 24,000 Hz (WAV PCM 16-bit).
- **Execution Engine**: ONNX Runtime with automatic hardware provider selection (`CUDAExecutionProvider` -> `CPUExecutionProvider` fallback).
- **Memory Optimization**:
  - The ONNX inference session and voice embedding matrix are loaded **once** at startup and cached in memory.
  - Thread pool intra-op concurrency is bounded (`min(4, os.cpu_count())`) to prevent CPU starvation on 16 GB machines.
  - Generates speech with an average Real-Time Factor (RTF) of **0.55 – 0.65** on CPU, synthesizing 2 seconds of speech in approximately 1.1 seconds.

### Kokoro Voice Catalog
NOOR integrates 29 Kokoro voices categorized by gender and accent:

| Voice ID | Gender | Accent | Description |
| :--- | :--- | :--- | :--- |
| `af_heart` | Female | American | Warm, expressive flagship voice |
| `af_bella` | Female | American | Clear, energetic, professional |
| `af_nicole` | Female | American | Soft, calm, conversational |
| `af_sarah` | Female | American | Crisp, modern, articulate |
| `af_sky` | Female | American | Youthful, upbeat |
| `am_adam` | Male | American | Deep, balanced, natural |
| `am_michael` | Male | American | Professional, authoritative |
| `bf_emma` | Female | British | Sophisticated, clear British English |
| `bf_isabella` | Female | British | Melodic, natural RP English |
| `bm_george` | Male | British | Refined, resonant British English |
| `bm_lewis` | Male | British | Casual, warm British English |
| `hf_alpha` | Female | Hindi | Hindi neural voice (eSpeak-NG) |
| `hf_beta` | Female | Hindi | Hindi expressive voice |
| `hm_omega` | Male | Hindi | Hindi deep male voice |
| `hm_psi` | Male | Hindi | Hindi clear male voice |

---

## 4. Multilingual Engine & Language Routing

NOOR supports three distinct response-language modes:

```mermaid
flowchart TD
    UserQuery["User Input (Spoken or Typed)"] --> LangPref{"Response Language Preference"}
    
    LangPref -->|English| EngRoute["English Mode"]
    EngRoute --> KokoroEng["Kokoro TTS (af_heart / af_bella / am_adam)"]
    
    LangPref -->|Hindi| HindiRoute["Hindi Mode (Devanagari Script)"]
    HindiRoute --> KokoroHindi{"Kokoro Hindi (hf_alpha) or Edge TTS?"}
    KokoroHindi -->|Local Mode| KokoroHindiVoice["Kokoro hf_alpha (via eSpeak-NG phonemization)"]
    KokoroHindi -->|Cloud Fallback| EdgeHindiVoice["Edge-TTS hi-IN-MadhurNeural / hi-IN-SwaraNeural"]
    
    LangPref -->|Hinglish| HinglishRoute["Hinglish Mode (Latin Hindi + English)"]
    HinglishRoute --> EdgeHinglish["Edge-TTS hi-IN-MadhurNeural / en-IN-NeerjaNeural (Native Code-Switching)"]
```

### Language Routing Rules
1. **English Mode**:
   - Synthesized using Kokoro's native English voices (`af_heart`, `am_adam`, etc.).
   - Code, technical identifiers, filenames, and URLs are preserved verbatim.
2. **Hindi Mode**:
   - Local: Kokoro experimental Hindi voices (`hf_alpha`, `hf_beta`, `hm_omega`, `hm_psi`) utilize eSpeak-NG phonemization for Devanagari script.
   - Cloud Fallback: Edge-TTS (`hi-IN-MadhurNeural`) offers human-grade Indian neural cadence.
3. **Hinglish Mode**:
   - Kokoro does not natively code-switch mixed English/Devanagari scripts.
   - For mixed conversational Hinglish (e.g., *"Hello! Main files open kar rahi hoon."*), Edge-TTS provides seamless native bilingual synthesis.

---

## 5. Modular TTS Manager Architecture

The `TTSManager` (`backend/voice/tts_manager.py`) operates as a central switchboard, cleanly decoupling the voice subsystem from LLM providers. Changing Ollama to Groq or Gemini never alters voice configurations.

### REST Endpoints
- `POST /api/voice/tts`: Direct text-to-speech synthesis returning raw 24 kHz WAV audio.
- `GET /api/voice/providers`: Introspects available TTS engines, local/cloud flags, hardware device, and default sample rates.
- `GET /api/voice/voices?provider={id}&language={lang}`: Dynamically lists compatible voices for the selected provider and language.
- `POST /api/voice/test`: Synthesizes preview audio and returns latency (ms), RTF, audio base64, and execution hardware without persisting settings.
- `GET /api/voice/status`: Live status of the audio engine, speaker state, VAD, and active model cache.

---

## 6. Agent Execution Graph & LangGraph Loop

NOOR executes computer actions through a cyclic LangGraph state machine:

```mermaid
stateDiagram-v2
    [*] --> Plan: make_plan(TaskRun)
    Plan --> SelectTool: Plan ready
    Plan --> Fail: Could not plan

    SelectTool --> PermissionCheck: Determine risk
    PermissionCheck --> Execute: Safe / Approved
    PermissionCheck --> WaitForApproval: Confirm required
    WaitForApproval --> Execute: User approves
    WaitForApproval --> Fail: User denies

    Execute --> Observe: Action executed
    Observe --> Verify: Visual & DOM inspection
    Verify --> SelectTool: Step verified (advance)
    Verify --> Replan: Step failed / unexpected state

    Replan --> SelectTool: Recovery plan ready
    Replan --> Fail: Max replans exceeded

    SelectTool --> Complete: All steps done
    Complete --> [*]
    Fail --> [*]
```

### Small Model Tool Normalization
Small local models (such as `llama3.2:3b` or `qwen3:4b`) occasionally hallucinate intuitive tool aliases instead of strict registry names. To prevent unexpected `"Task failed"` errors, NOOR incorporates an automated normalizer (`_normalize_tool_and_args` in `backend/agent/planner.py`):

| Model Output Tool | Normalized Tool | Parameter Adaptations |
| :--- | :--- | :--- |
| `google`, `search`, `web_search` | `open_url` | Maps `{"query": "..."}` -> `{"url": "https://www.google.com/search?q=..."}` |
| `write_file`, `make_file`, `save_file` | `create_file` | Maps `filename` -> `path`, `text` -> `content` |
| `run_command`, `cmd`, `terminal`, `shell` | `run_shell` | Maps `cmd` / `script` -> `command` |
| `open_app`, `start_app`, `launch` | `open_application` | Maps `app` / `program` -> `name` |
| `type`, `input`, `write` | `type_text` | Maps `content` / `value` -> `text` |
| `ask`, `question`, `clarify` | `ask_user` | Preserves `question` string |

---

## 7. Security, Sandboxing, & Permissions

1. **Local Privacy**: Local speech recognition (Whisper) and local speech synthesis (Kokoro) process all audio strictly on-device without external data egress.
2. **Permission Guard**: Destructive actions (shell commands, file modifications outside sandbox) require explicit user approval via desktop notification or voice confirmation.
3. **Electron Device Sandbox**: Electron permissions explicitly grant microphone access only within the application origin (`session.defaultSession.setPermissionRequestHandler`).

---

## 8. Android Compatibility Architecture

NOOR is architected to support future Android companion applications:
- **Provider-Independent API**: The REST endpoints (`/api/voice/tts`, `/api/voice/status`) accept standard JSON payloads and stream binary WAV/MP3 responses.
- **Platform Separation**: Desktop-specific automation (PyAutoGUI, Windows SAPI) is isolated in provider modules; mobile clients interact with NOOR purely through HTTP/WebSocket endpoints and Android-native TTS engines.
- **Account Synchronization**: Preferences saved in MongoDB automatically sync voice and personality preferences across desktop and mobile devices.

---

## 9. Step-by-Step Code Flows

### Flow A: User Speaks -> Voice Synthesis -> Audio Playback
```
1. User speaks: "What is the weather today?"
2. MicStreamer captures 16kHz PCM frames -> WebSocket sends binary frames.
3. Audio manager VAD detects speech start -> Marks metrics -> Streams to Faster-Whisper.
4. Whisper yields final transcript -> Hub broadcasts 'voice.final_transcript'.
5. ConversationManager classifies intent as 'question'.
6. LLM generates concise response.
7. TTSManager.build_chain() dispatches text to KokoroService.synth().
8. Kokoro generates 24kHz WAV audio in ~1.1s (RTF 0.55).
9. Speaker plays audio -> Sets mic_paused=True (echo suppression).
10. Playback finishes -> Speaker emits 'tts_idle' -> Microphone resumes listening.
```

### Flow B: User Submits Task -> Normalization -> Execution Loop
```
1. User submits: "search python documentation on google" or "open youtube"
2. ConversationManager classifies intent as 'new_task' -> ACKS spoken ("On it.").
3. TasksService starts LangGraph execution loop with goal.
4. Planner asks LLM for structured PlanOut.
5. If LLM outputs alias tool 'google' or 'navigate' or bare 'open', _normalize_tool_and_args automatically converts to:
   - Tool: open_url
   - Args: {"url": "https://www.google.com/search?q=..." or "https://www.youtube.com"}
6. Step validated successfully with 0 errors (with automatic fallback synthesizer if small models emit empty plans).
7. Executor launches Playwright browser -> Navigates to search page.
8. Observer verifies page title and content -> Marks step DONE.
9. Task completed successfully -> Spoken notification via Kokoro TTS.
```

---

## 10. Performance Benchmarks & Diagnostic Metrics

Real-time developer diagnostics are exposed via `GET /api/system/metrics` and rendered live on the frontend HUD (`MetricsPanel.tsx`):

| Layer / Metric | Baseline (Unoptimized) | Optimized Performance | Speedup / Improvement |
| :--- | :--- | :--- | :--- |
| **MongoDB Atlas Prefs** | 95.48 ms per query | 0.071 ms (in-memory cached) | **1,340x faster** |
| **Ollama Local TTFT** | 17,643 ms (cold load disk thrash) | 1,232 ms (`keep_alive: 60m`) | **14.3x faster** |
| **Faster-Whisper STT** | 820 ms (CPU float32) | 185 ms (NVIDIA CUDA FP16) | **4.4x faster** |
| **Kokoro TTS Graph Load**| > 60 s (ORT_ENABLE_ALL compile hang) | 2.5 s (`ORT_ENABLE_EXTENDED`) | **24x faster** |
| **Kokoro Audio Synthesis** | ~1.4 s | 0.65 s (Pipelined pre-synthesis) | **2.2x faster** |
| **Groq Cloud TTFT** | 380 ms | 195 ms (HTTP connection pool) | **1.9x faster** |
| **Gemini 3.5 Cloud TTFT** | 404 Error (deprecated 1.5/2.5) | 240 ms (`gemini-3.5-flash`) | **Fixed & Live** |
| **Frontend Mic Capture** | Duplicate frames / Audio conflicts | Single AudioWorklet Stream (16kHz PCM) | **Clean Hands-Free Voice** |
| **Unit Test Suite** | Broken dependencies | 68/68 Passed (100%) | **Production Ready** |
