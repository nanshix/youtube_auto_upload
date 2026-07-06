#!/usr/bin/env python3
"""Upload a single MP4 to YouTube using metadata from a YAML file.

Usage:
    python upload.py --meta path/to/meta.yml
"""
import argparse
import sys
import time
from pathlib import Path

import yaml
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SCRIPT_DIR = Path(__file__).resolve().parent
CLIENT_SECRET_PATH = SCRIPT_DIR / "client_secret.json"
TOKEN_PATH = SCRIPT_DIR / "token.json"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

DEFAULT_CATEGORY = "27"
DEFAULT_PRIVACY_STATUS = "private"
VALID_PRIVACY_STATUSES = {"public", "unlisted", "private"}

MAX_RETRIES = 3
RETRYABLE_HTTP_STATUSES = {500, 502, 503, 504}


class FatalError(Exception):
    """Raised for errors that should abort the run with a clear message."""


def load_metadata(meta_path: Path) -> dict:
    if not meta_path.is_file():
        raise FatalError(f"metadata file not found: {meta_path}")

    with meta_path.open("r", encoding="utf-8") as f:
        try:
            data = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise FatalError(f"could not parse metadata YAML: {e}") from e

    if "video" not in data or not data["video"]:
        raise FatalError("metadata is missing required field: video")
    if "title" not in data or not data["title"]:
        raise FatalError("metadata is missing required field: title")

    meta_dir = meta_path.resolve().parent

    def resolve(rel_path: str) -> Path:
        p = Path(rel_path)
        return p if p.is_absolute() else (meta_dir / p)

    video_path = resolve(data["video"])
    if not video_path.is_file():
        raise FatalError(f"video file not found: {video_path}")

    thumbnail_path = None
    if data.get("thumbnail"):
        thumbnail_path = resolve(data["thumbnail"])
        if not thumbnail_path.is_file():
            raise FatalError(f"thumbnail file not found: {thumbnail_path}")

    privacy_status = data.get("privacy_status", DEFAULT_PRIVACY_STATUS)
    if privacy_status not in VALID_PRIVACY_STATUSES:
        raise FatalError(
            f"invalid privacy_status: {privacy_status!r} "
            f"(must be one of {sorted(VALID_PRIVACY_STATUSES)})"
        )

    return {
        "video_path": video_path,
        "thumbnail_path": thumbnail_path,
        "title": data["title"],
        "description": data.get("description", ""),
        "tags": data.get("tags", []),
        "category": str(data.get("category", DEFAULT_CATEGORY)),
        "privacy_status": privacy_status,
        "playlists": data.get("playlists") or [],
    }


def get_credentials() -> Credentials:
    creds = None
    if TOKEN_PATH.is_file():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        return creds

    if not CLIENT_SECRET_PATH.is_file():
        raise FatalError(
            f"client_secret.json not found at {CLIENT_SECRET_PATH}. "
            "See README.md for setup instructions."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds


def with_retries(func, *args, **kwargs):
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except HttpError as e:
            status = getattr(e.resp, "status", None)
            if status not in RETRYABLE_HTTP_STATUSES or attempt == MAX_RETRIES:
                raise
            last_error = e
        except (ConnectionError, TimeoutError) as e:
            if attempt == MAX_RETRIES:
                raise
            last_error = e
        wait = 2 ** attempt
        print(f"transient error ({last_error}), retrying in {wait}s...", file=sys.stderr)
        time.sleep(wait)


def upload_video(youtube, meta: dict) -> str:
    body = {
        "snippet": {
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta["tags"],
            "categoryId": meta["category"],
        },
        "status": {
            "privacyStatus": meta["privacy_status"],
        },
    }
    media = MediaFileUpload(str(meta["video_path"]), resumable=False)

    def do_insert():
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        return request.execute()

    response = with_retries(do_insert)
    return response["id"]


def set_thumbnail(youtube, video_id: str, thumbnail_path: Path) -> None:
    media = MediaFileUpload(str(thumbnail_path))

    def do_set():
        return youtube.thumbnails().set(videoId=video_id, media_body=media).execute()

    with_retries(do_set)


def find_playlist_id(youtube, name: str) -> str | None:
    page_token = None
    while True:
        response = youtube.playlists().list(
            part="snippet", mine=True, maxResults=50, pageToken=page_token
        ).execute()
        for item in response.get("items", []):
            if item["snippet"]["title"] == name:
                return item["id"]
        page_token = response.get("nextPageToken")
        if not page_token:
            return None


def create_playlist(youtube, name: str, privacy_status: str) -> str:
    body = {
        "snippet": {"title": name},
        "status": {"privacyStatus": privacy_status},
    }

    def do_insert():
        return youtube.playlists().insert(part="snippet,status", body=body).execute()

    response = with_retries(do_insert)
    return response["id"]


def add_video_to_playlist(youtube, playlist_id: str, video_id: str) -> None:
    body = {
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {"kind": "youtube#video", "videoId": video_id},
        }
    }

    def do_insert():
        return youtube.playlistItems().insert(part="snippet", body=body).execute()

    with_retries(do_insert)


def print_authorized_channels(youtube) -> None:
    response = youtube.channels().list(part="snippet", mine=True).execute()
    items = response.get("items", [])
    if not items:
        print("no channel is associated with this authorization", file=sys.stderr)
        return
    for item in items:
        print(f"{item['snippet']['title']}  ({item['id']})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload a video to YouTube.")
    parser.add_argument("--meta", type=Path, help="Path to the meta.yml file")
    parser.add_argument(
        "--whoami",
        action="store_true",
        help="Authenticate and print the channel(s) this authorization can act on, then exit (no upload).",
    )
    args = parser.parse_args()

    if not args.whoami and not args.meta:
        parser.error("--meta is required unless --whoami is given")

    try:
        creds = get_credentials()
        youtube = build("youtube", "v3", credentials=creds)

        if args.whoami:
            print_authorized_channels(youtube)
            return 0

        meta = load_metadata(args.meta)
        video_id = upload_video(youtube, meta)

        if meta["thumbnail_path"]:
            try:
                set_thumbnail(youtube, video_id, meta["thumbnail_path"])
            except (HttpError, ConnectionError, TimeoutError) as e:
                print(f"warning: video uploaded but thumbnail failed: {e}", file=sys.stderr)

        for playlist_name in meta["playlists"]:
            try:
                playlist_id = find_playlist_id(youtube, playlist_name)
                if not playlist_id:
                    playlist_id = create_playlist(youtube, playlist_name, meta["privacy_status"])
                add_video_to_playlist(youtube, playlist_id, video_id)
            except (HttpError, ConnectionError, TimeoutError) as e:
                print(
                    f"warning: video uploaded but adding to playlist {playlist_name!r} failed: {e}",
                    file=sys.stderr,
                )

        print(f"Uploaded: https://www.youtube.com/watch?v={video_id}")
        return 0
    except FatalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except HttpError as e:
        print(f"error: YouTube API request failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
