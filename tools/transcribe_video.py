"""
mp4(또는 오디오 포함 영상) 파일을 음성인식(STT)으로 텍스트화하는 도구.

사용법:
    python transcribe_video.py <video_path> [--model medium] [--out-dir OUTDIR] [--lang ko]

동작:
    1. ffmpeg으로 오디오만 추출 (16kHz mono wav) -> <out-dir>/<이름>_audio.wav
    2. faster-whisper(GPU 우선, 실패 시 CPU)로 전사
    3. 결과를 <out-dir>/<이름>_transcript.md (타임스탬프 포함) 와
       <out-dir>/<이름>_transcript.txt (텍스트만) 로 저장

주의: NVIDIA GPU + CUDA 런타임(cublas/cudnn)이 site-packages/nvidia 아래 설치되어 있다는 전제.
      없으면 자동으로 CPU(int8)로 폴백한다.
"""

import argparse
import os
import subprocess
import sys
import time

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

SITE_PACKAGES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _add_cuda_dll_dirs():
    import site

    for sp in site.getsitepackages() if hasattr(site, "getsitepackages") else []:
        for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin"):
            p = os.path.join(sp, *sub.split("/"))
            if os.path.isdir(p):
                os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")
                if hasattr(os, "add_dll_directory"):
                    try:
                        os.add_dll_directory(p)
                    except OSError:
                        pass


def format_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:05.2f}"


def extract_audio(video_path: str, audio_path: str):
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        audio_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 오디오 추출 실패:\n{result.stderr[-2000:]}")


def transcribe(audio_path: str, model_size: str, lang: str):
    _add_cuda_dll_dirs()
    from faster_whisper import WhisperModel

    device_used = "cpu"
    compute_type = "int8"
    try:
        model = WhisperModel(model_size, device="cuda", compute_type="float16")
        device_used = "cuda"
        compute_type = "float16"
    except Exception as e:
        print(f"[info] CUDA 사용 불가, CPU로 전환: {e}", file=sys.stderr)
        model = WhisperModel(model_size, device="cpu", compute_type="int8")

    print(f"[info] device={device_used} compute_type={compute_type} model={model_size}", file=sys.stderr)

    segments, info = model.transcribe(audio_path, language=lang or None, vad_filter=True)
    print(f"[info] detected language={info.language} prob={info.language_probability:.2f}", file=sys.stderr)
    return segments, info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video_path")
    ap.add_argument("--model", default="medium", help="tiny/base/small/medium/large-v3")
    ap.add_argument("--lang", default="ko")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    video_path = os.path.abspath(args.video_path)
    base = os.path.splitext(os.path.basename(video_path))[0]
    out_dir = args.out_dir or os.path.join(os.path.dirname(video_path), "_extracted")
    os.makedirs(out_dir, exist_ok=True)

    audio_path = os.path.join(out_dir, f"{base}_audio.wav")
    md_path = os.path.join(out_dir, f"{base}_transcript.md")
    txt_path = os.path.join(out_dir, f"{base}_transcript.txt")

    print(f"[step 1/3] 오디오 추출: {video_path}", file=sys.stderr)
    t0 = time.time()
    extract_audio(video_path, audio_path)
    print(f"[step 1/3] 완료 ({time.time()-t0:.1f}s) -> {audio_path}", file=sys.stderr)

    print(f"[step 2/3] 전사 시작 (model={args.model})", file=sys.stderr)
    t0 = time.time()
    segments, info = transcribe(audio_path, args.model, args.lang)

    md_lines = [f"# Transcript — {base}", "", f"- source: {video_path}", f"- language: {info.language} (p={info.language_probability:.2f})", f"- model: {args.model}", ""]
    txt_lines = []
    n = 0
    for seg in segments:
        n += 1
        text = seg.text.strip()
        md_lines.append(f"**[{format_ts(seg.start)} - {format_ts(seg.end)}]** {text}")
        txt_lines.append(text)
        if n % 50 == 0:
            print(f"[step 2/3] ...{format_ts(seg.end)} 처리됨", file=sys.stderr)

    elapsed = time.time() - t0
    print(f"[step 2/3] 완료 ({elapsed:.1f}s, {n}개 세그먼트)", file=sys.stderr)

    print("[step 3/3] 저장 중...", file=sys.stderr)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(md_lines))
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(txt_lines))
    print(f"[step 3/3] 완료 -> {md_path}, {txt_path}", file=sys.stderr)
    print("DONE", file=sys.stderr)


if __name__ == "__main__":
    main()
