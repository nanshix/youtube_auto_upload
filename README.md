# youtube_auto_upload

Upload an MP4 to your YouTube channel, with metadata defined in a YAML file.

## Setup

### 1. Install dependencies

```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Create a Google Cloud OAuth client

1. Go to https://console.cloud.google.com/ and create (or pick) a project.
2. Enable the **YouTube Data API v3** for that project:
   APIs & Services -> Library -> search "YouTube Data API v3" -> Enable.
3. Configure the OAuth consent screen (APIs & Services -> OAuth consent
   screen). "External" + "Testing" mode is fine for personal use; add your
   own Google account under Test users.
4. Create credentials: APIs & Services -> Credentials -> Create Credentials
   -> OAuth client ID -> Application type: **Desktop app**.
5. Download the resulting JSON and save it in this folder as
   `client_secret.json`. It is gitignored and will never be committed.

### 3. First run (one-time browser auth)

The first upload will open a browser window asking you to sign in and grant
access to your channel. This creates `token.json` in this folder (also
gitignored), which caches a refresh token so future runs don't need a
browser.

## Usage

1. Copy `meta.example.yml` next to your video, rename it, and fill it in:

```yaml
video: "./my_video.mp4"
title: "My Video Title"
description: |
  Multi-line description here.
tags:
  - tag1
  - tag2
category: "22"
privacy_status: "private"
thumbnail: "./thumb.jpg"   # optional
```

2. Run:

```
python upload.py --meta path/to/meta.yml
```

On success it prints the video's watch URL. On failure it prints an error
to stderr and exits non-zero.

## Files

- `upload.py` — the CLI script
- `meta.example.yml` — template for per-video metadata
- `client_secret.json`, `token.json` — credentials, created/stored locally,
  never committed (see `.gitignore`)
