# Video-to-Frames Dataset Preparation

## Overview

A Python script that automates video retrieval and image-frame extraction for dataset preparation. It reads a CSV file of video URLs, downloads each video, and saves evenly spaced frames as JPEG images, organised into folders by category.

## Workflow

```text
Input CSV (video URL + flag)
        ↓
Normalise columns and flags, discard invalid rows
        ↓
Resume check (skip videos whose frames already exist)
        ↓
Download video to a temporary file
        ↓
Open video with OpenCV and compute frame positions
        ↓
Read frames sequentially and save selected frames as JPEG
        ↓
Delete temporary video, record result in processing\_log.csv
```

## Input Format

A CSV file with two columns. Column names are matched ignoring case, spacing and punctuation, so `Video URL`, `video\_url` and `VIDEO URL` are all accepted.

|Column|Description|
|-|-|
|`Video URL`|Direct HTTP(S) link to a video file|
|`Flag`|Category of the video: `Red` or `Green`|

Placeholder example:

```csv
Video URL,Flag
https://example.com/path/video\_a.mp4,Red
https://example.com/path/video\_b.mp4,Green
```

Rows are ignored if the flag is not `red` or `green` (case-insensitive) or if the URL is empty. UTF-8 files are expected; `latin1` is used as a fallback if decoding fails.

## Flag Values and Frame Counts

The flag decides how many frames are extracted from each video and which folder they are saved in.

|Flag|Default frames per video|Output folder|
|-|-|-|
|`red`|10|`output/red/`|
|`green`|15|`output/green/`|

The counts can be changed with `--red-frames` and `--green-frames`. Both must be integers greater than 0.

## Frame Selection

Frames are evenly spaced from the first to the last frame of the video. For a video with `N` frames and `K` requested frames, the frame indexes are `round(i \* (N - 1) / (K - 1))` for `i = 0 ... K-1`. Consecutive duplicate indexes are removed. If a video has fewer than `K` frames, all of its frames are used. The video is read once, sequentially, and the selected frames are written as JPEG files.

## Temporary Video Download

Each video is downloaded in full to a temporary `.mp4` file using `requests` (streamed in 1 MB chunks), processed, and then deleted. Videos are not kept after processing.

## Output

```text
output/
├── red/
│   ├── video\_000001\_frame\_01.jpg
│   ├── ...
├── green/
│   ├── video\_000002\_frame\_01.jpg
│   ├── ...
└── processing\_log.csv
```

* Frame files are named `video\_NNNNNN\_frame\_NN.jpg`.
* `video\_NNNNNN` is the video's 1-based position among the valid rows of the CSV.
* `processing\_log.csv` contains one row per video with `video\_id`, `flag`, `status`, `frames\_saved`, `video\_frames`, `fps`, `duration\_seconds` and `video\_url`. Some columns are empty for skipped or failed videos. The log contains the processed URLs, so it should not be published.

## Resume Behaviour

Before processing a video, the script checks for existing frames named after its video ID:

* If the full expected number of frames already exists, the video is skipped (`skipped\_existing`).
* If only some frames exist, they are deleted and the video is processed again.

## Installation

Requires Python 3.9 or newer.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

## Usage

```bash
python src/extract\_frames.py --input path/to/videos.csv --output output
```

### Options

|Option|Default|Description|
|-|-|-|
|`--input`|(required)|Path to the input CSV file|
|`--output`|`output`|Output directory|
|`--red-frames`|`10`|Frames per `red` video (integer > 0)|
|`--green-frames`|`15`|Frames per `green` video (integer > 0)|
|`--flag-column`|`Flag`|Name of the flag column|
|`--url-column`|`Video URL`|Name of the video URL column|

Run `python src/extract\_frames.py --help` for the same list.

## Error Handling

Errors are handled per video, so one failure does not stop the run. Statuses recorded in `processing\_log.csv` include `success`, `skipped\_existing`, `download\_failed`, `Could not open video`, `Video contains no readable frames`, `Expected X frames but saved Y` and `processing\_error: <message>`. At the end, the script prints expected versus actual frame counts for each category. A missing input file, missing columns, or invalid frame counts stop the script with an error message.

## Limitations

* Videos must be downloadable with a plain HTTP(S) request; authentication is not supported.
* Videos are processed one at a time.
* The frame count is read from video metadata, which can be inaccurate for some files. Such videos are logged as `Expected X frames but saved Y`.
* The expected-frame total assumes each video has enough frames, so very short videos will show `CHECK` in the final summary.
* Video IDs depend on row order. Reordering or editing the CSV between runs changes the IDs and affects resuming.
* The final frame count includes every `.jpg` in the output folders, including files from earlier runs.



