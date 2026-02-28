# PTT — Push-to-Talk for Windows

Hold **Right Ctrl** to record your voice, release to transcribe and type the result into whatever window has focus. Works across multiple applications simultaneously — whichever window you were in gets the text.

## Features

- Hold Right Ctrl to record, release to transcribe and type
- Transcription via local [Whisper](https://github.com/ggerganov/whisper.cpp) (no cloud, no latency)
- Multi-monitor overlay showing REC / THINKING state
- Red border highlights the target window while recording
- System tray icon changes colour with state (grey → red → orange → grey)
- Left Ctrl unaffected — only Right Ctrl is intercepted
- Auto-starts on login

## Requirements

- Windows 10/11
- Python 3.10+
- A running [whisper.cpp](https://github.com/ggerganov/whisper.cpp) server on `http://localhost:2022`

### Python packages

```
pip install keyboard sounddevice numpy requests pystray pillow
```

## Setup

1. Clone the repo:
   ```
   git clone https://github.com/TheProgrammingGiantPanda/ptt.git
   ```

2. Install dependencies:
   ```
   pip install keyboard sounddevice numpy requests pystray pillow
   ```

3. Start your Whisper server on port 2022.

4. Run:
   ```
   pythonw ptt.py
   ```

### Auto-start on login

Create a `.vbs` file in your Windows Startup folder (`shell:startup`):

```vbscript
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run """C:\Path\To\pythonw.exe"" ""C:\Path\To\ptt\ptt.py""", 0, False
```

## Configuration

Edit the constants at the top of `ptt.py`:

| Variable | Default | Description |
|---|---|---|
| `WHISPER_URL` | `http://localhost:2022/v1/audio/transcriptions` | Whisper server endpoint |
| `PTT_KEY` | `right ctrl` | Key to hold for recording |
| `SAMPLE_RATE` | `16000` | Audio sample rate (Hz) |

## Log

A `ptt.log` file is written alongside `ptt.py` — useful for debugging transcriptions and state transitions.
