"""ナレーション付きデモ動画（MP4）を生成する。

パイプライン:
  1) Playwright（システム導入の Edge を channel=msedge で使用）で web/index.html を開き、
     RRR_DEMO.showStep(i) で各ステップの静止状態（遷移・ハイライト・カーソル・字幕）を作り、
     1600x900 のビューポートを PNG で撮影する（全11ステップ）。
  2) 各ステップの表示時間 = 先頭無音(LEAD) + ナレーション長。フレームを結合し、
     ナレーション音声（web/data/narration/step-XX.mp3）を LEAD 秒ずらして重ねる → 完全同期。
  3) imageio-ffmpeg 同梱の ffmpeg で H.264/AAC の MP4 に出力（web/demo/demo.mp4）。

依存: playwright, imageio-ffmpeg（`pip install playwright imageio-ffmpeg`）。ffmpeg は同梱を使用。
すべて合成データのデモ。
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
NARR = WEB / "data" / "narration"
OUT = WEB / "demo" / "demo.mp4"
VW, VH = 1600, 900
LEAD = 0.6          # 各ステップ先頭の無音（遷移が落ち着いてからナレーション開始）
FPS = 30


def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def mp3_duration(ff: str, path: Path) -> float:
    out = subprocess.run([ff, "-i", str(path)], capture_output=True, text=True, encoding="utf-8", errors="ignore")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", out.stderr)
    if not m:
        return 6.0
    h, mm, ss = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mm * 60 + ss


def capture_frames(frames_dir: Path) -> int:
    from playwright.sync_api import sync_playwright
    url = (WEB / "index.html").resolve().as_uri()
    n = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True,
                                    args=["--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page(viewport={"width": VW, "height": VH}, device_scale_factor=1)
        page.goto(url)
        page.wait_for_function("window.RRR_DEMO && window.DEMO_DATA")
        n = page.evaluate("window.RRR_DEMO.stepCount()")
        page.wait_for_timeout(500)
        for i in range(n):
            page.evaluate("(i) => window.RRR_DEMO.showStep(i)", i)
            page.wait_for_timeout(1500)   # 遷移・カーソル・ハイライトの落ち着き待ち
            page.screenshot(path=str(frames_dir / f"frame_{i:02d}.png"))
            print(f"  captured frame {i + 1}/{n}")
        browser.close()
    return n


def build(ff: str, n: int, frames_dir: Path, work: Path) -> None:
    durations = [LEAD + mp3_duration(ff, NARR / f"step-{i + 1:02d}.mp3") for i in range(n)]

    # 1) 映像: フレームを各表示時間だけ保持（concat demuxer）
    frames_txt = work / "frames.txt"
    lines = []
    for i in range(n):
        lines.append(f"file '{(frames_dir / f'frame_{i:02d}.png').as_posix()}'")
        lines.append(f"duration {durations[i]:.3f}")
    lines.append(f"file '{(frames_dir / f'frame_{n - 1:02d}.png').as_posix()}'")  # 最終フレーム保持
    frames_txt.write_text("\n".join(lines), encoding="utf-8")
    silent = work / "silent.mp4"
    run([ff, "-y", "-f", "concat", "-safe", "0", "-i", str(frames_txt),
         "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         str(silent)])

    # 2) 音声: ナレーション i を (Σ先行表示時間 + LEAD) 秒遅らせて重ねる（完全同期）
    starts = []
    acc = 0.0
    for i in range(n):
        starts.append(acc + LEAD)
        acc += durations[i]
    inputs = []
    for i in range(n):
        inputs += ["-i", str(NARR / f"step-{i + 1:02d}.mp3")]
    delays = ";".join(f"[{i}:a]adelay={int(starts[i] * 1000)}|{int(starts[i] * 1000)}[a{i}]" for i in range(n))
    mixed = "".join(f"[a{i}]" for i in range(n)) + f"amix=inputs={n}:normalize=0[aout]"
    audio = work / "audio.m4a"
    run([ff, "-y", *inputs, "-filter_complex", f"{delays};{mixed}", "-map", "[aout]",
         "-c:a", "aac", "-b:a", "192k", str(audio)])

    # 3) 多重化
    OUT.parent.mkdir(parents=True, exist_ok=True)
    run([ff, "-y", "-i", str(silent), "-i", str(audio),
         "-c:v", "copy", "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(OUT)])


def run(cmd) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-2000:])
        raise SystemExit(f"ffmpeg failed: {' '.join(cmd[:2])} ... rc={r.returncode}")


def main() -> int:
    ff = ffmpeg_exe()
    print("ffmpeg:", ff)
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        frames_dir = work / "frames"
        frames_dir.mkdir()
        print("フレーム撮影中（Edge headless）...")
        n = capture_frames(frames_dir)
        print(f"組み立て中（{n} ステップ）...")
        build(ff, n, frames_dir, work)
    size = OUT.stat().st_size / 1024 / 1024
    print(f"出力: {OUT}  ({size:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
