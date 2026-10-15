"""Measure two visually selected video frames using their actual PTS.

Frame numbers are one-based in presentation order. They match the numbered
images produced by the FFmpeg command in docs/MAC_GALAXY_S24_PLUS_SOP.md.
Requires ffprobe from FFmpeg; all calculation and hashing use the stdlib.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def parse_pts(payload: str) -> list[Decimal]:
    """Return strictly increasing video-frame presentation times in seconds."""
    try:
        frames = json.loads(payload)["frames"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("ffprobe did not return a video frame list") from exc
    if not frames:
        raise ValueError("the video contains no readable frames")
    pts = []
    for number, frame in enumerate(frames, start=1):
        try:
            value = Decimal(frame["best_effort_timestamp_time"])
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise ValueError(f"frame {number} has no usable PTS") from exc
        if not value.is_finite() or (pts and value <= pts[-1]):
            raise ValueError(f"frame {number} has a non-increasing PTS")
        pts.append(value)
    return pts


def measure(pts: list[Decimal], start_frame: int, end_frame: int) -> dict[str, Decimal]:
    """Calculate elapsed time from one-based, visually verified frame IDs."""
    if not 1 <= start_frame < end_frame <= len(pts):
        raise ValueError(f"choose 1 <= start_frame < end_frame <= {len(pts)}")
    start_ms = pts[start_frame - 1] * 1000
    end_ms = pts[end_frame - 1] * 1000
    return {
        "start_pts_ms": start_ms,
        "end_pts_ms": end_ms,
        "elapsed_ms": end_ms - start_ms,
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_numbered_frames(directory: Path, frame_count: int) -> None:
    """Ensure FFmpeg images still correspond one-to-one to ffprobe frames."""
    if not directory.is_dir():
        raise ValueError(f"numbered frame directory not found: {directory}")
    names = sorted(path.name for path in directory.glob("*.png"))
    if len(names) != frame_count or any(
        name != f"{index:06d}.png" for index, name in enumerate(names, start=1)
    ):
        raise ValueError(
            f"numbered PNGs and PTS do not match: {len(names)} images, "
            f"{frame_count} PTS frames; inspect decode errors and frame extraction"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path, help="Unedited original screen recording")
    parser.add_argument("--start-frame", type=int, required=True, help="1-based touch-dot frame")
    parser.add_argument("--end-frame", type=int, required=True, help="1-based first complete content frame")
    parser.add_argument("--frames-dir", type=Path, help="Numbered FFmpeg PNGs; checks one-to-one frame count")
    args = parser.parse_args()
    video = args.video.expanduser().resolve()
    if not video.is_file():
        parser.error(f"video not found: {video}")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        parser.error("ffprobe is required; on a Homebrew Mac: brew install ffmpeg")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_frames",
         "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(video)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        parser.error(f"ffprobe failed: {result.stderr.strip() or result.returncode}")
    try:
        pts = parse_pts(result.stdout)
        if args.frames_dir:
            validate_numbered_frames(args.frames_dir.expanduser().resolve(), len(pts))
        times = measure(pts, args.start_frame, args.end_frame)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"video: {video}")
    print(f"sha256: {sha256(video)}")
    print(f"video_frames: {len(pts)}")
    print(f"selected_frames: {args.start_frame} -> {args.end_frame}")
    for name, value in times.items():
        print(f"{name}: {value.quantize(Decimal('0.001'))}")
    print(f"start_pts_seconds: {pts[args.start_frame - 1]}")
    print(f"end_pts_seconds: {pts[args.end_frame - 1]}")
    gaps = [(second - first) * 1000 for first, second in zip(pts, pts[1:])]
    if gaps:
        print(f"observed_frame_gap_ms: {min(gaps).quantize(Decimal('0.001'))} .. "
              f"{max(gaps).quantize(Decimal('0.001'))}")


if __name__ == "__main__":
    main()
