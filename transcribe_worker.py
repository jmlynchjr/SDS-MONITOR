#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE.
"""Speech-to-text worker for SDS100 Monitor (v1.2.0).

Runs under the transcription engine's own Python (a virtual environment with
faster-whisper installed), so the Monitor itself needs nothing extra.
Talks to the Monitor with one JSON object per line:

  stdin   {"id": 7, "path": "/…/123456_Dispatch.mp3", "prompt": "…"}
  stdout  {"type": "status", "text": "…"}
          {"type": "ready", "device": "cpu", "compute": "int8", "model": "small.en"}
          {"type": "result", "id": 7, "text": "…", "seconds": 1.4, "language": "en",
           "words": [[0.62, 0.9, " Engine"], …]}   (v1.1.105: seconds into the recording, for who-said-what)
          {"type": "error", "id": 7, "error": "…"}
          {"type": "fatal", "error": "…"}            (then exits)
          {"type": "idle_exit"}                      (unloaded after --idle-exit seconds, exits 0)
"""
import argparse
import json
import re
import os
import sys
import time

# Whisper sometimes "hears" these in noise or silence; a transcript that is only
# one of these is dropped.
HALLUCINATIONS = {
    "thank you", "thank you.", "thanks for watching", "thanks for watching!",
    "thank you for watching", "thank you for watching.", "you", "bye", "bye.", "okay.",
    "subtitles by the amara.org community", "please subscribe", ".", "...",
}


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def load_audio(path):
    """Decode a recording to 16 kHz mono float32 samples with ffmpeg.

    faster-whisper's own decoder (PyAV) breaks whenever the two packages fall
    out of step; e.g. PyAV 19 removed an option faster-whisper 1.2.1 passes
    ("unexpected keyword argument 'metadata_errors'"). The Monitor already
    needs ffmpeg for PC audio capture, so decode with it and hand Whisper the
    samples. Falls back to the file path if ffmpeg isn't installed.
    """
    import shutil
    import subprocess
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return path
    import numpy as np
    result = subprocess.run(
        [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", path,
         "-f", "f32le", "-acodec", "pcm_f32le", "-ac", "1", "-ar", "16000", "pipe:1"],
        capture_output=True, timeout=300)
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip().splitlines()
        raise RuntimeError("ffmpeg could not read the recording: "
                           + (detail[-1] if detail else f"exit code {result.returncode}"))
    return np.frombuffer(result.stdout, dtype=np.float32).copy()


NORMALIZE_TARGET = 0.1       # speech level after normalizing: −20 dBFS (v1.1.115)
NORMALIZE_PEAK = 0.89        # never louder than −1 dBFS at the peaks


def level_stats(audio):
    """(speech level, peak) of float samples: the RMS of the 20 ms frames that stand above the noise."""
    import numpy as np
    n = len(audio) // 320
    if n == 0:
        return 0.0, float(np.max(np.abs(audio))) if len(audio) else 0.0
    frames = audio[:n * 320].reshape(n, 320)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    floor = float(np.percentile(rms, 10))
    active = rms[rms > max(floor * 3.16, 1e-4)]       # 10 dB above the noise
    level = float(np.sqrt(np.mean(active ** 2))) if active.size else 0.0
    return level, float(np.max(np.abs(audio)))


def apply_level(audio, gain_db=0.0, normalize=False):
    """The samples the speech engine hears: louder or quieter by gain_db (clipping like a turned-up volume
    would), or normalized so speech sits at about −20 dBFS without clipping. Recordings aren't changed."""
    import numpy as np
    if isinstance(audio, str) or audio.size == 0:
        return audio
    if normalize:
        level, peak = level_stats(audio)
        if level > 0 and peak > 0:
            gain = min(NORMALIZE_TARGET / level, NORMALIZE_PEAK / peak, 31.6)     # at most +30 dB
            audio = audio * np.float32(max(gain, 0.1))
    if gain_db:
        audio = np.clip(audio * np.float32(10 ** (gain_db / 20)), -1.0, 1.0)
    return audio.astype(np.float32, copy=False)


def release_memory():
    """Hand memory freed after a call back to the system (glibc keeps it otherwise)."""
    import gc
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def read_jobs(idle_exit):
    """Lines from stdin; stops after `idle_exit` seconds without one (0 = never)."""
    import select
    buf = b""
    fd = sys.stdin.fileno()
    while True:
        while b"\n" not in buf:
            ready, _, _ = select.select([fd], [], [], idle_exit if idle_exit > 0 else None)
            if not ready:
                out({"type": "idle_exit"})
                return
            chunk = os.read(fd, 65536)
            if not chunk:
                return                       # the Monitor closed the pipe
            buf += chunk
        line, buf = buf.split(b"\n", 1)
        yield line.decode("utf-8", errors="replace")


def pick_device(requested):
    if requested != "auto":
        return requested
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:
        pass
    return "cpu"


LOOP_RUN = 3          # the same word more than this many times in a row is a loop, not speech


def _norm(word):
    return re.sub(r"[^\w-]", "", word).casefold()


def cut_loops(words):
    """Drop a run of the same word more than LOOP_RUN times in a row ("10, 10, 10, 10, 10, …") entirely:
    it is the speech engine stuck in a loop on noise or a short burst, not speech (v1.1.115)."""
    out, i = [], 0
    while i < len(words):
        j = i + 1
        key = _norm(words[i][2])
        while key and j < len(words) and _norm(words[j][2]) == key:
            j += 1
        if j - i <= LOOP_RUN:
            out.extend(words[i:j])
        i = j
    return out


def cut_text_loops(text):
    text = re.sub(r"\b([\w-]+)(?:[,.]?\s+\1\b){%d,}[,.]?" % LOOP_RUN, "", text, flags=re.I)
    return re.sub(r"\s{2,}", " ", text).strip(" ,")


def clean(segments, stats=None):
    """(text, words): the transcript, and [start, end, word] for every word kept (the segment itself when
    the engine gave no word times). `stats` (a dict) gets how sure the engine was (v1.1.115)."""
    parts, words = [], []
    seconds = logprob = no_speech = 0.0
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        # Probably noise: Whisper thinks there's no speech and isn't confident.
        if getattr(seg, "no_speech_prob", 0) > 0.6 and getattr(seg, "avg_logprob", 0) < -1.0:
            continue
        # A repetition loop the engine couldn't get out of even after retrying (v1.1.115)
        if getattr(seg, "compression_ratio", 0) > 2.4:
            continue
        seg_words = cut_loops([[round(w.start, 2), round(w.end, 2), w.word]
                               for w in (getattr(seg, "words", None) or [])])
        if seg_words:
            text = "".join(w[2] for w in seg_words).strip()
            if not text:
                continue
            words.extend(seg_words)
        else:
            text = cut_text_loops(text)
            words.append([round(seg.start, 2), round(seg.end, 2), " " + text])
        parts.append(text)
        length = max(0.1, float(getattr(seg, "end", 0) or 0) - float(getattr(seg, "start", 0) or 0))
        seconds += length
        logprob += float(getattr(seg, "avg_logprob", 0) or 0) * length
        no_speech += float(getattr(seg, "no_speech_prob", 0) or 0) * length
    if stats is not None:
        stats["speech_seconds"] = round(seconds, 2)
        stats["logprob"] = round(logprob / seconds, 4) if seconds else None
        stats["no_speech"] = round(no_speech / seconds, 4) if seconds else None
    text = " ".join(parts).strip()
    if text.casefold() in HALLUCINATIONS:
        return "", []
    return text, words


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="small.en")
    ap.add_argument("--device", default="auto", choices=("auto", "cpu", "cuda"))
    ap.add_argument("--language", default="en", help="language code, or 'auto'")
    ap.add_argument("--beam", type=int, default=5)
    ap.add_argument("--idle-exit", type=float, default=0,
                    help="unload (exit) after this many seconds without a call; 0 = stay loaded")
    ap.add_argument("--check", action="store_true", help="only check the engine is installed")
    a = ap.parse_args()

    try:
        from faster_whisper import WhisperModel
    except Exception as exc:  # ImportError, or a broken native library
        out({"type": "fatal", "error": f"faster-whisper is not available: {exc}"})
        return 2
    if a.check:
        import faster_whisper
        out({"type": "ok", "version": getattr(faster_whisper, "__version__", "?")})
        return 0

    device = pick_device(a.device)
    compute = "float16" if device == "cuda" else "int8"
    out({"type": "status", "text": f"Loading model {a.model} on {device.upper()} "
                                   "(downloaded the first time it is used)…"})
    try:
        model = WhisperModel(a.model, device=device, compute_type=compute)
    except Exception as exc:
        if device == "cuda":
            out({"type": "status", "text": f"GPU not usable ({exc}); using the CPU."})
            device, compute = "cpu", "int8"
            try:
                model = WhisperModel(a.model, device=device, compute_type=compute)
            except Exception as exc2:
                out({"type": "fatal", "error": f"Could not load model {a.model}: {exc2}"})
                return 3
        else:
            out({"type": "fatal", "error": f"Could not load model {a.model}: {exc}"})
            return 3
    out({"type": "ready", "device": device, "compute": compute, "model": a.model})

    # ".en" models only do English; otherwise "auto" lets Whisper detect it.
    if a.model.endswith(".en"):
        language = "en"
    else:
        language = None if a.language in ("", "auto") else a.language
    release_memory()
    for line in read_jobs(a.idle_exit):
        line = line.strip()
        if not line:
            continue
        try:
            job = json.loads(line)
        except ValueError:
            continue
        started = time.monotonic()
        try:
            audio = load_audio(job["path"])
        except Exception as exc:
            out({"type": "error", "id": job.get("id"), "error": str(exc)})
            continue
        if not isinstance(audio, str) and audio.size == 0:
            out({"type": "result", "id": job.get("id"), "text": "", "seconds": 0, "language": "", "words": []})
            continue
        try:                                     # v1.1.115: level test and "normalize before transcribing"
            audio = apply_level(audio, float(job.get("gain_db") or 0), bool(job.get("normalize")))
        except Exception as exc:
            out({"type": "status", "text": f"Level not changed: {exc}"})
        for attempt in (1, 2):
            try:
                segments, info = model.transcribe(
                    audio,
                    language=language,
                    beam_size=max(1, a.beam),
                    vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 500},
                    initial_prompt=job.get("prompt") or None,
                    condition_on_previous_text=False,
                    word_timestamps=bool(job.get("words", True)),
                )
                stats = {}
                text, words = clean(segments, stats)   # segments is a generator; this runs the model
                out(dict({"type": "result", "id": job.get("id"), "text": text,
                          "seconds": round(time.monotonic() - started, 2),
                          "language": getattr(info, "language", "") or "", "words": words}, **stats))
                break
            except Exception as exc:
                msg = str(exc)
                gpu_problem = device == "cuda" and any(
                    w in msg.lower() for w in ("cuda", "cudnn", "cublas", "libcu"))
                if attempt == 1 and gpu_problem:
                    # The GPU loaded but can't run (usually missing NVIDIA libraries).
                    out({"type": "status", "text": f"GPU failed ({msg[:120]}); switching to the CPU…"})
                    device, compute = "cpu", "int8"
                    model = WhisperModel(a.model, device=device, compute_type=compute)
                    out({"type": "ready", "device": device, "compute": compute, "model": a.model})
                    continue
                out({"type": "error", "id": job.get("id"), "error": msg})
                break
        del audio
        release_memory()
    return 0


if __name__ == "__main__":
    sys.exit(main())
