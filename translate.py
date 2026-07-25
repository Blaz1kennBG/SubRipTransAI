import urllib.request
import urllib.error
import json
import re
import os
import sys
from datetime import datetime

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "translategemma:12b"

def parse_srt(filepath):
    with open(filepath, "r", encoding="utf-8-sig") as f:
        text = f.read()
    blocks = re.split(r'\n\s*\n', text.strip())
    result = []
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) >= 2 and '-->' in lines[1]:
            idx = lines[0].strip()
            timestamp = lines[1].strip()
            content = '\n'.join(lines[2:])
            result.append((idx, timestamp, content))
    return result

def translate_block(block_text):
    prompt = (
        "You are a precise subtitle translation assistant. Translate the following SRT subtitle text from English to Bulgarian. "
        "CRITICAL RULES:\n"
        "1. Translate the text naturally and idiomatically into Bulgarian, adapting cultural nuances where necessary.\n"
        "2. DO NOT wrap the output in markdown code blocks.\n"
        "3. DO NOT add any introductory text, explanations, or closing remarks. Output ONLY the translated text.\n"
        f"Text to translate:\n{block_text}"
    )
    payload = json.dumps({
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": 512}
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("response", "").strip()
    except Exception as e:
        return f"[TRANSLATION ERROR: {e}]"

def main():
    if len(sys.argv) < 2:
        print("Usage: python translate.py <video_name>")
        print("Example: python translate.py \"Supernatural S01E02 Wendigo  [1080p x265 10bit Joy]\"")
        sys.exit(1)

    video_name = sys.argv[1]
    base_dir = R"D:\ollama\Codex Projects\Movie Subtitles ripper and translator"
    srt_path = os.path.join(base_dir, "output", f"{video_name}.srt")
    out_dir = os.path.join(base_dir, "output", "translated")
    os.makedirs(out_dir, exist_ok=True)
    out_srt = os.path.join(out_dir, f"{video_name}.srt")
    out_log = os.path.join(out_dir, f"{video_name}.txt")

    if not os.path.exists(srt_path):
        print(f"Error: SRT file not found: {srt_path}")
        sys.exit(1)

    blocks = parse_srt(srt_path)
    total = len(blocks)
    print(f"Found {total} subtitle blocks. Starting translation...")

    open(out_srt, "w", encoding="utf-8").close()
    open(out_log, "w", encoding="utf-8").close()

    for i, (idx, timestamp, content) in enumerate(blocks, 1):
        start = datetime.now()
        ts_str = start.strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{i}/{total}] Translating block {idx}... ", end="", flush=True)
        translated = translate_block(content)
        elapsed = datetime.now() - start
        print(f"done ({elapsed.total_seconds():.1f}s)  |  {ts_str}")

        block_str = f"{idx}\n{timestamp}\n{translated}"
        with open(out_srt, "a", encoding="utf-8") as f:
            f.write(block_str + "\n\n")

        log_line = f"Block {idx} - {ts_str}\n"
        with open(out_log, "r+", encoding="utf-8") as f:
            old = f.read()
            f.seek(0)
            f.write(log_line + old)

    print(f"\nDone! Translated SRT: {out_srt}")
    print(f"Timestamp log: {out_log}")

if __name__ == "__main__":
    main()
