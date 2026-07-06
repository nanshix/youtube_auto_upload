# YouTube Auto Upload — Design

## Purpose

A small CLI tool to upload a single MP4 to a YouTube channel, with per-video
metadata defined in a YAML file, using the YouTube Data API v3. Intended to be
run manually or from cron/scripts for automated uploads.

## Non-goals

- Batch/folder scanning of multiple videos in one run.
- Resumable/chunked upload progress reporting (videos are expected to be
  under 10MB; a plain non-chunked upload is sufficient).
- Multi-channel / multi-account support.
- Playlist management or scheduled publish times (`publish_at`) — can be
  added later if needed.

## CLI

```
python upload.py --meta path/to/meta.yml
```

Single argument: the path to a metadata YAML file. All other inputs (video
file, title, etc.) are derived from that file.

## meta.yml format

```yaml
video: "./my_video.mp4"      # required. Path to the mp4, resolved relative
                              # to the directory containing this yml file if
                              # not absolute.
title: "My Video Title"      # required
description: |                # optional, defaults to ""
  Multi-line description here.
tags:                         # optional, defaults to []
  - tag1
  - tag2
category: "22"                 # optional, defaults to "22" (People & Blogs)
privacy_status: "private"      # optional, defaults to "private"
                                # one of: public | unlisted | private
thumbnail: "./thumb.jpg"       # optional. Path resolved the same way as
                                # `video`. If present, set as custom thumbnail
                                # after upload.
```

A template is provided at `meta.example.yml`.

## Credentials

- `client_secret.json` — OAuth client downloaded from Google Cloud Console.
  Lives in the repo root at a fixed path.
- `token.json` — auto-created after first successful auth; holds the cached
  refresh token. Lives in the repo root at a fixed path.
- Both paths are hardcoded constants in `upload.py` (no config file needed).
- Both are listed in `.gitignore` so they are never committed.

### Auth flow

1. On first run, no `token.json` exists (or it's invalid/expired without a
   refresh token) → `InstalledAppFlow.run_local_server()` opens a browser for
   the user to grant consent. The resulting credentials (including refresh
   token) are saved to `token.json`.
2. On subsequent runs, `token.json` is loaded and refreshed silently via the
   refresh token — no browser interaction, safe to run from cron/headless
   environments.
3. Required OAuth scope: `https://www.googleapis.com/auth/youtube.upload`.

## Upload flow

1. Parse `--meta` argument, load and validate the YAML:
   - `video` and `title` are required; missing either is a fatal error
     printed to stderr with a non-zero exit before any network call.
   - Resolve `video` (and `thumbnail`, if present) relative to the yml
     file's parent directory when not already absolute.
   - Verify the resolved video file exists on disk; fatal error if not.
2. Authenticate (see Auth flow above) and build the YouTube API client.
3. Call `videos.insert` with a `MediaFileUpload(video_path, resumable=False)`
   (non-chunked, appropriate for small files) and a `snippet`/`status` body
   built from the metadata (title, description, tags, category, privacy
   status).
4. If `thumbnail` is set, call `thumbnails.set` with the video ID after a
   successful upload.
5. Wrap the `videos.insert` and `thumbnails.set` calls in a small retry loop
   (e.g. 3 attempts, exponential backoff) that retries only on transient
   errors (network errors, HTTP 5xx, HTTP 403 rate-limit/quota errors are
   NOT retried since retrying won't help).
6. On success, print the resulting video ID and its watch URL
   (`https://www.youtube.com/watch?v=<id>`) to stdout, exit 0.
7. On any unrecoverable error, print a clear message to stderr and exit
   non-zero.

## Repo layout

```
youtube_auto_upload/
  upload.py              # the CLI script
  requirements.txt        # google-api-python-client, google-auth-oauthlib, PyYAML
  .gitignore              # client_secret.json, token.json, __pycache__/, venv/
  meta.example.yml         # template for per-video metadata
  README.md                # setup: GCP console steps (create project, enable
                            # YouTube Data API v3, create OAuth client ID for
                            # a Desktop app, download as client_secret.json)
                            # + first-run auth walkthrough + usage example
```

## Error handling summary

| Failure | Behavior |
|---|---|
| meta.yml missing/unreadable | fatal, exit non-zero, before auth |
| required field missing (`video`, `title`) | fatal, exit non-zero, before auth |
| video file not found on disk | fatal, exit non-zero, before auth |
| `client_secret.json` missing | fatal, exit non-zero, with a message pointing to README setup steps |
| first-run browser auth fails/cancelled | fatal, exit non-zero |
| transient network/5xx during upload | retried up to 3 times with backoff, then fatal |
| quota exceeded / permission error (4xx) | fatal immediately, no retry |
| thumbnail upload fails after video upload succeeded | warning printed (video is already live), exit 0 — the video upload itself succeeded |

## Testing approach

No YouTube sandbox API exists, so testing is manual:
- Unit-test YAML parsing/validation and path resolution logic in isolation
  (no network calls).
- Manual end-to-end test: run against a real (test) channel with
  `privacy_status: private`, verify the video appears with correct metadata.
