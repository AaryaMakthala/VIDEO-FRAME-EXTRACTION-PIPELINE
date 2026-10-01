"""Video frame extraction for dataset preparation.

Reads a CSV file containing video URLs and a category flag ("red" or "green"),
downloads each video to a temporary file, and saves evenly spaced frames as
JPEG images. The flag determines how many frames are extracted and which
output sub-folder they are saved in.

Usage:
    python src/extract_frames.py --input path/to/videos.csv --output output
"""

from __future__ import annotations

import argparse
import re
import tempfile
from pathlib import Path
from typing import Any

import cv2
import pandas as pd
import requests
from tqdm import tqdm


# ============================================================
# CONFIGURATION
# ============================================================

# Default CSV column names (matching is tolerant of case, spacing and punctuation).
DEFAULT_FLAG_COLUMN = "Flag"
DEFAULT_URL_COLUMN = "Video URL"

# Default number of frames extracted per video, by flag.
DEFAULT_RED_FRAMES = 10
DEFAULT_GREEN_FRAMES = 15

# Flags accepted from the CSV; rows with any other flag are ignored.
VALID_FLAGS = ("red", "green")

# Download timeouts in seconds: (connect, read).
REQUEST_TIMEOUT = (30, 120)

# Download chunk size in bytes (1 MB).
DOWNLOAD_CHUNK_SIZE = 1024 * 1024

SEPARATOR = "=" * 60


# ============================================================
# INPUT HANDLING
# ============================================================

def normalize_column_name(name: Any) -> str:
    """Lower-case a column name and remove non-alphanumeric characters.

    Example: 'Video URL' -> 'videourl'
    """
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def find_column(df: pd.DataFrame, requested_name: str) -> str | None:
    """Return the DataFrame column matching `requested_name`, or None.

    Comparison ignores case, spacing and punctuation.
    """
    target = normalize_column_name(requested_name)

    for column in df.columns:
        if normalize_column_name(column) == target:
            return column

    return None


def clean_flag(value: Any) -> str:
    """Normalise a flag value to lower-case text.

    Example: ' Red ' -> 'red'. Missing values become an empty string.
    """
    if pd.isna(value):
        return ""

    return str(value).strip().lower()


def load_input(csv_path: Path, flag_column: str, url_column: str) -> pd.DataFrame:
    """Read the input CSV and return only valid rows.

    A row is valid when its flag is one of VALID_FLAGS and its URL is not empty.
    The returned DataFrame contains two helper columns:
        _flag_clean       normalised flag
        _video_url_clean  stripped URL
    """
    # Read the file, falling back to a Windows encoding if UTF-8 fails.
    try:
        df = pd.read_csv(csv_path)
    except UnicodeDecodeError:
        df = pd.read_csv(csv_path, encoding="latin1")

    print("\nCSV columns detected:")
    for column in df.columns:
        print(f"  - {column}")

    # Locate the required columns.
    actual_flag = find_column(df, flag_column)
    actual_url = find_column(df, url_column)

    if actual_flag is None:
        raise ValueError(
            f"Could not find the flag column (expected something like "
            f"'{flag_column}'). Available columns: {list(df.columns)}"
        )

    if actual_url is None:
        raise ValueError(
            f"Could not find the URL column (expected something like "
            f"'{url_column}'). Available columns: {list(df.columns)}"
        )

    print("\nUsing columns:")
    print(f"  Flag       : {actual_flag}")
    print(f"  Video URL  : {actual_url}")

    # Clean values and keep only valid rows.
    df = df.copy()
    df["_flag_clean"] = df[actual_flag].apply(clean_flag)
    df["_video_url_clean"] = df[actual_url].fillna("").astype(str).str.strip()

    valid = df[
        df["_flag_clean"].isin(VALID_FLAGS) & (df["_video_url_clean"] != "")
    ].copy()

    print(f"\nTotal CSV rows : {len(df):,}")
    print(f"Valid rows     : {len(valid):,}")

    return valid


# ============================================================
# VIDEO RETRIEVAL
# ============================================================

def create_session() -> requests.Session:
    """Create an HTTP session with a browser-like User-Agent header."""
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; video-to-frames)"})
    return session


def download_video(session: requests.Session, url: str, output_path: Path) -> bool:
    """Download one video to `output_path`.

    Returns True if a non-empty file was written, False otherwise.
    """
    try:
        response = session.get(
            url,
            stream=True,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        response.raise_for_status()

        with open(output_path, "wb") as file:
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                if chunk:
                    file.write(chunk)

        return output_path.stat().st_size > 0

    except Exception as error:
        tqdm.write(f"Download failed: {url}\n  Error: {error}")
        return False


# ============================================================
# FRAME EXTRACTION
# ============================================================

def calculate_frame_positions(total_frames: int, required_frames: int) -> list[int]:
    """Return evenly spaced frame indexes from the first to the last frame.

    If the video has fewer frames than requested, every frame is used.
    """
    if total_frames <= 0:
        return []

    if required_frames <= 1:
        return [0]

    if total_frames < required_frames:
        return list(range(total_frames))

    positions = [
        int(round(i * (total_frames - 1) / (required_frames - 1)))
        for i in range(required_frames)
    ]

    # Remove consecutive duplicates while preserving order.
    unique_positions: list[int] = []
    for position in positions:
        if not unique_positions or position != unique_positions[-1]:
            unique_positions.append(position)

    return unique_positions


def extract_frames(
    video_path: Path,
    output_folder: Path,
    video_id: str,
    required_frames: int,
) -> dict[str, Any]:
    """Save evenly spaced frames from one video as '<video_id>_frame_NN.jpg'.

    Frames are read sequentially (no seeking) and written when their index
    matches one of the target positions.

    Returns a dictionary with keys: success, reason, saved, total_frames and,
    when the video could be opened, fps and duration.
    """
    cap = cv2.VideoCapture(str(video_path))

    if not cap.isOpened():
        return {
            "success": False,
            "reason": "Could not open video",
            "saved": 0,
            "total_frames": 0,
        }

    # Read video metadata.
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    duration = total_frames / fps if fps > 0 else 0

    if total_frames <= 0:
        cap.release()
        return {
            "success": False,
            "reason": "Video contains no readable frames",
            "saved": 0,
            "total_frames": 0,
        }

    # Decide which frame indexes to save.
    frame_positions = calculate_frame_positions(total_frames, required_frames)
    target_frames = set(frame_positions)

    # Read the video once, saving the target frames.
    saved = 0
    current_frame = 0

    while True:
        ok, frame = cap.read()

        if not ok:
            break

        if current_frame in target_frames:
            output_path = output_folder / f"{video_id}_frame_{saved + 1:02d}.jpg"

            if cv2.imwrite(str(output_path), frame):
                saved += 1

        current_frame += 1

    cap.release()

    success = saved == len(frame_positions)

    return {
        "success": success,
        "reason": (
            ""
            if success
            else f"Expected {len(frame_positions)} frames but saved {saved}"
        ),
        "saved": saved,
        "total_frames": total_frames,
        "fps": fps,
        "duration": duration,
    }


# ============================================================
# RESUME SUPPORT
# ============================================================

def existing_frames(output_folder: Path, video_id: str) -> list[Path]:
    """Return frame files already saved for `video_id`.

    Used to skip completed videos and clean up partial output after an
    interrupted run.
    """
    return list(output_folder.glob(f"{video_id}_frame_*.jpg"))


# ============================================================
# PER-VIDEO PIPELINE
# ============================================================

def process_video(
    session: requests.Session,
    url: str,
    flag: str,
    video_id: str,
    required_frames: int,
    output_folder: Path,
) -> dict[str, Any]:
    """Process one video: resume check, download, extract, clean up.

    Returns a log entry describing the outcome.
    """
    entry: dict[str, Any] = {"video_id": video_id, "flag": flag}

    # Resume check: skip videos that already have the full set of frames.
    found = existing_frames(output_folder, video_id)

    if len(found) == required_frames:
        return {
            **entry,
            "status": "skipped_existing",
            "frames_saved": required_frames,
            "video_url": url,
        }

    # Remove partial output before re-processing.
    for file in found:
        try:
            file.unlink()
        except OSError:
            pass

    temp_path: Path | None = None

    try:
        # Reserve a temporary file for the downloaded video.
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_file:
            temp_path = Path(temp_file.name)

        # Download.
        if not download_video(session, url, temp_path):
            return {
                **entry,
                "status": "download_failed",
                "frames_saved": 0,
                "video_url": url,
            }

        # Extract frames.
        result = extract_frames(temp_path, output_folder, video_id, required_frames)

        return {
            **entry,
            "status": "success" if result["success"] else result["reason"],
            "frames_saved": result["saved"],
            "video_frames": result["total_frames"],
            "fps": result.get("fps", ""),
            "duration_seconds": result.get("duration", ""),
            "video_url": url,
        }

    except Exception as error:
        return {
            **entry,
            "status": f"processing_error: {error}",
            "frames_saved": 0,
            "video_url": url,
        }

    finally:
        # Always delete the temporary downloaded video.
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


# ============================================================
# REPORTING
# ============================================================

def print_dataset_summary(
    valid_df: pd.DataFrame,
    frame_counts: dict[str, int],
) -> dict[str, int]:
    """Print the dataset check and return the expected frame count per flag."""
    print(f"\n{SEPARATOR}")
    print("DATASET CHECK")
    print(SEPARATOR)

    expected: dict[str, int] = {}

    for flag in VALID_FLAGS:
        videos = int((valid_df["_flag_clean"] == flag).sum())
        expected[flag] = videos * frame_counts[flag]

        print(
            f"{flag.capitalize():<6}: {videos:,} videos x {frame_counts[flag]} "
            f"= {expected[flag]:,} frames"
        )

    print(f"Total : {sum(expected.values()):,} frames")

    return expected


def print_final_report(
    log_df: pd.DataFrame,
    folders: dict[str, Path],
    expected: dict[str, int],
    log_file: Path,
) -> None:
    """Print video outcome counts and compare expected vs. actual frame counts."""
    status = log_df["status"] if not log_df.empty else pd.Series(dtype=str)

    print(f"\n\n{SEPARATOR}")
    print("FINAL VERIFICATION")
    print(SEPARATOR)

    print("\nVideos:")
    print(f"  Successful : {int((status == 'success').sum()):,}")
    print(f"  Skipped    : {int((status == 'skipped_existing').sum()):,}")
    print(
        f"  Failed     : "
        f"{int((~status.isin(['success', 'skipped_existing'])).sum()):,}"
    )

    actual = {flag: len(list(folders[flag].glob("*.jpg"))) for flag in VALID_FLAGS}

    print("\nFrames (expected vs. actual):")
    for flag in VALID_FLAGS:
        verdict = "PASS" if actual[flag] == expected[flag] else "CHECK"
        print(
            f"  {flag.capitalize():<6}: "
            f"{expected[flag]:,} vs {actual[flag]:,}  [{verdict}]"
        )

    if actual == expected:
        print("\nAll expected frames were generated.")
    else:
        print(
            f"\nWarning: frame counts do not match. "
            f"Check {log_file} for failed videos."
        )

    print("\nOutput folders:")
    for flag in VALID_FLAGS:
        print(f"  {flag.capitalize():<6}: {folders[flag]}")
    print(f"  {'Log':<6}: {log_file}")

    print(f"\n{SEPARATOR}")
    print("DONE")
    print(SEPARATOR)


# ============================================================
# COMMAND LINE INTERFACE
# ============================================================

def positive_int(value: str) -> int:
    """Argparse type: accept only integers greater than 0."""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' is not a valid integer")

    if number <= 0:
        raise argparse.ArgumentTypeError(
            f"must be greater than 0 (got {number})"
        )

    return number


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Download videos listed in a CSV and extract evenly spaced frames."
    )

    parser.add_argument(
        "--input", required=True, type=Path,
        help="Path to the input CSV file.",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("output"),
        help="Output directory (default: ./output).",
    )
    parser.add_argument(
        "--red-frames", type=positive_int, default=DEFAULT_RED_FRAMES,
        help=f"Frames per 'red' video (default: {DEFAULT_RED_FRAMES}).",
    )
    parser.add_argument(
        "--green-frames", type=positive_int, default=DEFAULT_GREEN_FRAMES,
        help=f"Frames per 'green' video (default: {DEFAULT_GREEN_FRAMES}).",
    )
    parser.add_argument(
        "--flag-column", default=DEFAULT_FLAG_COLUMN,
        help=f"Flag column name (default: '{DEFAULT_FLAG_COLUMN}').",
    )
    parser.add_argument(
        "--url-column", default=DEFAULT_URL_COLUMN,
        help=f"Video URL column name (default: '{DEFAULT_URL_COLUMN}').",
    )

    return parser.parse_args(argv)


# ============================================================
# MAIN
# ============================================================

def main(argv: list[str] | None = None) -> None:
    """Run the full video-to-frames pipeline."""
    args = parse_args(argv)

    if not args.input.is_file():
        raise FileNotFoundError(f"Input CSV not found: {args.input}")

    # Output configuration.
    frame_counts = {"red": args.red_frames, "green": args.green_frames}
    folders = {flag: args.output / flag for flag in VALID_FLAGS}

    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)

    print(SEPARATOR)
    print("VIDEO FRAME EXTRACTION")
    print(SEPARATOR)
    print(f"\nInput CSV : {args.input}")
    print(f"Output    : {args.output}")

    # Read input and show the expected output.
    valid_df = load_input(args.input, args.flag_column, args.url_column)
    expected = print_dataset_summary(valid_df, frame_counts)

    # Process every valid video.
    print(f"\n{SEPARATOR}")
    print("STARTING VIDEO PROCESSING")
    print(SEPARATOR)

    session = create_session()
    log: list[dict[str, Any]] = []

    # video_id is the video's 1-based position among the valid rows.
    for number, (_, row) in enumerate(
        tqdm(valid_df.iterrows(), total=len(valid_df), desc="Processing videos"),
        start=1,
    ):
        flag = row["_flag_clean"]

        log.append(
            process_video(
                session=session,
                url=row["_video_url_clean"],
                flag=flag,
                video_id=f"video_{number:06d}",
                required_frames=frame_counts[flag],
                output_folder=folders[flag],
            )
        )

    # Save the processing log and print the final report.
    log_file = args.output / "processing_log.csv"
    log_df = pd.DataFrame(log)
    log_df.to_csv(log_file, index=False)

    print_final_report(log_df, folders, expected, log_file)


if __name__ == "__main__":
    main()
