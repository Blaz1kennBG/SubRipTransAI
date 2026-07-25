You are working in: `D:\ollama\Codex Projects\Movie Subtitles ripper and translator`

## Project Goal
Extract embedded subtitles from video files and translate them from English to Bulgarian using a local Ollama model.

## Tools
- **ffmpeg/ffprobe** — extract subtitle streams from videos
- **Ollama** (v0.30.5) — local LLM server with model `translategemma:12b`
- **Python 3.13** — translation/extraction scripts

## All Files in Project Root
```
input/                  → place video files here (.mkv, .mp4, etc.)
output/                 → extracted .srt files (raw, untouched)
output/translated/      → final translated .srt + timestamp .txt log
translated/             → old/empty directory (unused now)
prompt.txt              → old instructions (original project description)
subtitles_instructions.txt → translation system prompt for Ollama
run.bat                 → batch file to run subtitle_extractor.py
subtitle_extractor.py   → old subtitle extraction script
translate_srt.py        → old translation script (chunks of 10, uses requests)
translate.py            → CURRENT translation script (block-by-block, uses urllib)
state.md                → full project state reference
session_prompt.md       → this file
```

## Workflow

### 1. Extract Subtitles
```bash
ffprobe -v error -select_streams s -show_entries stream=index:stream_tags=language,title -of csv=p=0 "input/<video>.mkv"
ffmpeg -i "input/<video>.mkv" -map 0:<stream_index> -c:s copy "output/<video>.srt" -y
```

### 2. Translate (use translate.py)
```bash
python3 translate.py
```

- Calls Ollama REST API at `http://localhost:11434/api/generate`
- Sends each SRT block individually
- Writes results incrementally after each block
- Clears output files at start of run

### Output Files (per video)
- `output/translated/<video_name>.srt` — translated Bulgarian subtitles (clean SRT, no markdown, no extra text)
- `output/translated/<video_name>.txt` — timestamp log, newest entries prepended at top. Format: `Block {N} - {YYYY-MM-DD HH:MM:SS}`

## Rules
1. Maintain exact SRT formatting (index, timestamps, line breaks).
2. Translate naturally and idiomatically into Bulgarian.
3. NO markdown code blocks in output.
4. NO introductory text, explanations, or closing remarks.
5. If multiple subtitle streams exist per video, append `_1`, `_2`, etc. to filenames.

