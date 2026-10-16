"""Stage the verified bundle in the repo, then post it to LinkedIn and Facebook.

    python src/publish.py stage --from out/today   # out/today -> out/<date>, add state entry
    python src/publish.py post                     # post whatever today's entry still lacks
    python src/publish.py post --dry-run --dir out/today   # print, touch nothing

Order matters, and is the fix for two bugs in the first design:
  1. stage + commit + push happen BEFORE posting, so the "proof" link in the
     post (github.com/.../tree/main/out/<date>) resolves the moment anyone
     clicks it.
  2. state is per platform. If LinkedIn succeeds and Facebook fails, a re-run
     posts to Facebook only -- never LinkedIn twice. Each id is written to
     state/posted.json the moment its platform confirms, and the workflow
     commits that file even when the job fails.

POSTs are never retried (a plain requests.Session has no retry adapter):
a timeout after the platform accepted the post would otherwise publish it
twice. Losing a day is recoverable; a duplicate public post is not.

Secret names match Daily-news-IT-Guy so the same values can be reused.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from importlib.metadata import version
from pathlib import Path
from urllib.parse import quote

import requests

from common import (OUT_DIR, PLATFORMS, REPO_ROOT, entry_for, load_state, log,
                    read_json, save_state, setup_logging, today)

HTTP_TIMEOUT = 30
LINKEDIN_TEXT_LIMIT = 3000
# Retired on a rolling ~12-month schedule; a stale value answers 426. Kept in
# step with Daily-news-IT-Guy and overridable without a commit.
# `or`, not a get() default: an unset repository variable reaches the job as
# an empty string, which get() would happily return.
LINKEDIN_VERSION = (os.environ.get("LINKEDIN_VERSION") or "202601").strip()
FACEBOOK_API_VERSION = (os.environ.get("FACEBOOK_API_VERSION") or "v26.0").strip()
CLOUD_TAG = {"aws": "#AWS", "azure": "#Azure", "gcp": "#GoogleCloud"}
HASHTAGS = ("#Terraform", "#Checkov", "#DevSecOps", "#IaC")


# --------------------------------------------------------------------------
# Post text
# --------------------------------------------------------------------------

def repo_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "aekanun-phanphomma/Daily-Checkov-Terraform")
    return f"{server}/{repo}"


def compose_post(bundle: Path, date: str) -> str:
    """Model-written body + links and hashtags that only code writes.

    The model is forbidden from writing URLs (G6) because it would invent
    them. Every link below comes from a tool: the repo path we are about to
    push, the check's source from `checkov --list`, the guideline from
    Checkov's own JSON report.
    """
    content = read_json(bundle / "content.json")
    meta = read_json(bundle / "meta.json")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    tags = " ".join((*HASHTAGS, CLOUD_TAG[meta["rule"]["cloud"]]))
    return (
        f"{content['post_en'].strip()}\n\n"
        f"Proof: both versions, verified with Checkov {version('checkov')} "
        f"(bad must FAIL, fix must PASS):\n"
        f"{repo_url()}/tree/{branch}/out/{date}\n\n"
        f"Check source: {meta['source_url']}\n"
        f"Guideline: {meta['guideline_url']}\n\n"
        f"{tags}"
    )


# --------------------------------------------------------------------------
# LinkedIn (personal profile, w_member_social)
# --------------------------------------------------------------------------

def escape_linkedin(text: str) -> str:
    """Escape LinkedIn's "little text format" reserved characters.

    The Posts API parses `commentary` for inline entities; an unescaped
    reserved character is a 422, not a rendering quirk. Backslash first, or
    the escapes we add get re-escaped. URLs survive this: the backslashes
    are stripped at render time and LinkedIn still auto-links them.
    """
    text = text.replace("\\", "\\\\")
    for char in "|{}@[]()<>#*_~":
        text = text.replace(char, "\\" + char)
    return text


def linkedin_commentary(text: str) -> str:
    r"""Escaped body, with the trailing hashtag line as real hashtag entities.

    An escaped `\#Terraform` renders as plain text. LinkedIn only links a
    hashtag written as its template `{hashtag|\#|Terraform}`.
    """
    body, _, last = text.rpartition("\n\n")
    tags = last.split()
    if body and tags and all(t.startswith("#") and t[1:].isalnum() for t in tags):
        return escape_linkedin(body) + "\n\n" + " ".join(
            "{hashtag|\\#|" + t[1:] + "}" for t in tags)
    return escape_linkedin(text)


def _linkedin_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": LINKEDIN_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
    }


def _linkedin_raise(response: requests.Response, step: str) -> None:
    if response.status_code == 401:
        raise RuntimeError(
            f"LinkedIn {step}: token rejected (401) -- almost certainly the "
            "60-day expiry. Re-run the OAuth flow and update LINKEDIN_ACCESS_TOKEN "
            "and the LINKEDIN_TOKEN_ISSUED variable.")
    if response.status_code == 426:
        raise RuntimeError(
            f"LinkedIn {step}: version {LINKEDIN_VERSION} retired (426). Set the "
            "LINKEDIN_VERSION repository variable to a current YYYYMM.")
    raise RuntimeError(f"LinkedIn {step}: {response.status_code} {response.text[:400]}")


def linkedin_author(session: requests.Session, token: str) -> str:
    configured = os.environ.get("LINKEDIN_AUTHOR_URN", "").strip()
    if configured:
        return configured
    response = session.get("https://api.linkedin.com/v2/userinfo",
                           headers={"Authorization": f"Bearer {token}"},
                           timeout=HTTP_TIMEOUT)
    if not response.ok:
        _linkedin_raise(response, "userinfo (set LINKEDIN_AUTHOR_URN to skip this call)")
    return f"urn:li:person:{response.json()['sub']}"


class MediaUploadFailed(RuntimeError):
    """Uploading the media failed before any post existed: safe to fall back."""


def _linkedin_image(session: requests.Session, token: str, author: str,
                    image: Path, alt: str) -> dict:
    """initializeUpload -> PUT bytes -> wait for AVAILABLE. Returns content.media."""
    headers = _linkedin_headers(token)
    init = session.post("https://api.linkedin.com/rest/images?action=initializeUpload",
                        headers=headers, timeout=HTTP_TIMEOUT,
                        json={"initializeUploadRequest": {"owner": author}})
    if not init.ok:
        _linkedin_raise(init, "image initializeUpload")
    upload_url = init.json()["value"]["uploadUrl"]
    image_urn = init.json()["value"]["image"]

    put = session.put(upload_url, data=image.read_bytes(), timeout=HTTP_TIMEOUT * 2,
                      headers={"Authorization": f"Bearer {token}"})
    if put.status_code not in (200, 201):
        _linkedin_raise(put, "image upload")

    # The image is processed asynchronously. A post that references it while
    # still PROCESSING can fail, so wait (briefly) for AVAILABLE. A failed
    # status lookup is not fatal -- the post call will say if it matters.
    for _ in range(10):
        status = session.get(f"https://api.linkedin.com/rest/images/{quote(image_urn)}",
                             headers=headers, timeout=HTTP_TIMEOUT)
        if not status.ok or status.json().get("status") == "AVAILABLE":
            break
        time.sleep(3)
    return {"id": image_urn, "altText": alt[:4000]}


def _linkedin_video(session: requests.Session, token: str, author: str,
                    video: Path, title: str) -> dict:
    """initializeUpload -> PUT each part -> finalizeUpload -> wait for AVAILABLE.

    Every failure here raises MediaUploadFailed: no post exists yet, so the
    caller can fall back to the PNG without any risk of a duplicate.
    """
    headers = _linkedin_headers(token)
    data = video.read_bytes()
    try:
        init = session.post(
            "https://api.linkedin.com/rest/videos?action=initializeUpload",
            headers=headers, timeout=HTTP_TIMEOUT,
            json={"initializeUploadRequest": {"owner": author, "fileSizeBytes": len(data),
                                              "uploadCaptions": False,
                                              "uploadThumbnail": False}})
        if not init.ok:
            raise MediaUploadFailed(f"video initializeUpload {init.status_code}: "
                                    f"{init.text[:300]}")
        value = init.json()["value"]
        video_urn = value["video"]

        # Small files come back as one part; the API decides, not us.
        etags = []
        for part in value["uploadInstructions"]:
            chunk = data[part["firstByte"]:part["lastByte"] + 1]
            put = session.put(part["uploadUrl"], data=chunk, timeout=HTTP_TIMEOUT * 4,
                              headers={"Content-Type": "application/octet-stream"})
            if not put.ok or not put.headers.get("etag"):
                raise MediaUploadFailed(f"video part upload {put.status_code}")
            etags.append(put.headers["etag"])

        done = session.post(
            "https://api.linkedin.com/rest/videos?action=finalizeUpload",
            headers=headers, timeout=HTTP_TIMEOUT,
            json={"finalizeUploadRequest": {"video": video_urn,
                                            "uploadToken": value.get("uploadToken", ""),
                                            "uploadedPartIds": etags}})
        if not done.ok:
            raise MediaUploadFailed(f"video finalizeUpload {done.status_code}: "
                                    f"{done.text[:300]}")

        # Unlike images, a video that is still PROCESSING cannot be posted.
        for _ in range(40):
            status = session.get(f"https://api.linkedin.com/rest/videos/{quote(video_urn)}",
                                 headers=headers, timeout=HTTP_TIMEOUT)
            state = status.json().get("status") if status.ok else None
            if state == "AVAILABLE":
                return {"id": video_urn, "title": title[:200]}
            if state == "PROCESSING_FAILED":
                raise MediaUploadFailed("LinkedIn could not process the video")
            time.sleep(3)
        raise MediaUploadFailed("video still processing after 2 minutes")
    except (requests.RequestException, KeyError, ValueError) as exc:
        raise MediaUploadFailed(f"video upload: {exc!r}") from exc


def send_linkedin(session: requests.Session, text: str, bundle: Path, alt: str) -> str:
    """Upload the video (or the PNG if that fails), then create the post."""
    token = os.environ["LINKEDIN_ACCESS_TOKEN"].strip()
    author = linkedin_author(session, token)
    headers = _linkedin_headers(token)

    media = None
    if (bundle / "card.mp4").exists():
        try:
            media = _linkedin_video(session, token, author, bundle / "card.mp4", alt)
        except MediaUploadFailed as exc:
            log.warning("linkedin: %s -- falling back to the PNG", exc)
    if media is None:
        media = _linkedin_image(session, token, author, bundle / "card.png", alt)

    # Never retried and never followed by a fallback: if this call times out
    # the post may already exist, and a second attempt would duplicate it.
    post = session.post("https://api.linkedin.com/rest/posts",
                        headers={**headers, "Content-Type": "application/json"},
                        timeout=HTTP_TIMEOUT, json={
        "author": author,
        "commentary": linkedin_commentary(text),
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                         "thirdPartyDistributionChannels": []},
        "content": {"media": media},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    })
    if post.status_code not in (200, 201):
        _linkedin_raise(post, "create post")
    # The post URN is in a header, not the body.
    return post.headers.get("x-restli-id", "unknown")


# --------------------------------------------------------------------------
# Facebook Page
# --------------------------------------------------------------------------

def _facebook_error(response: requests.Response) -> RuntimeError:
    try:
        error = response.json().get("error", {})
    except ValueError:
        error = {}
    code, message = error.get("code"), error.get("message", response.text[:300])
    if code == 190:
        return RuntimeError(
            f"Facebook token rejected (190): {message}. A Page token from a "
            "long-lived user token does not expire on its own -- suspect the Meta "
            "app being published, a password change, or revoked access.")
    if code in (10, 200, 283):
        return RuntimeError(
            f"Facebook refused the post ({code}): {message}. Check that the token "
            "is a Page token (Access Token Debugger: Type = Page) with "
            "pages_manage_posts.")
    return RuntimeError(f"Facebook {response.status_code} ({code}): {message}")


def send_facebook(session: requests.Session, text: str, bundle: Path) -> str:
    """POST /{page-id}/videos with the MP4, or /photos with the PNG.

    On Facebook the upload *is* the post, so the fallback is narrower than
    LinkedIn's: only an HTTP error answer (nothing was published) falls back
    to the photo. A timeout or dropped connection is raised instead -- the
    video may have been accepted, and a photo on top would be a duplicate.

    No escaping (Facebook has no inline-entity parser). The token goes in
    the form body, never the URL, so it cannot leak into logs that echo the
    request line.
    """
    token = os.environ["FACEBOOK_PAGE_TOKEN"].strip()
    page_id = os.environ["FACEBOOK_PAGE_ID"].strip()
    video = bundle / "card.mp4"
    if video.exists():
        with video.open("rb") as fh:
            # graph-video.* is the host Meta documents for video uploads.
            response = session.post(
                f"https://graph-video.facebook.com/{FACEBOOK_API_VERSION}/{page_id}/videos",
                data={"description": text, "access_token": token},
                files={"source": ("card.mp4", fh, "video/mp4")},
                timeout=HTTP_TIMEOUT * 6,
            )
        if response.ok:
            return response.json().get("id", "unknown")
        log.warning("facebook: video refused (%s) -- falling back to the PNG",
                    _facebook_error(response))

    with (bundle / "card.png").open("rb") as fh:
        response = session.post(
            f"https://graph.facebook.com/{FACEBOOK_API_VERSION}/{page_id}/photos",
            data={"message": text, "access_token": token},
            files={"source": ("card.png", fh, "image/png")},
            timeout=HTTP_TIMEOUT * 2,
        )
    if response.ok:
        body = response.json()
        return body.get("post_id") or body.get("id", "unknown")
    raise _facebook_error(response)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def configured(platform: str) -> bool:
    keys = {"linkedin": ("LINKEDIN_ACCESS_TOKEN",),
            "facebook": ("FACEBOOK_PAGE_TOKEN", "FACEBOOK_PAGE_ID")}[platform]
    present = [bool(os.environ.get(k, "").strip()) for k in keys]
    if any(present) and not all(present):
        raise RuntimeError(f"{platform} is half-configured: set all of {keys}")
    return all(present)


def cmd_stage(src: Path) -> int:
    date = today()
    state = load_state()
    if entry_for(state, date):
        log.info("%s already staged -- resuming", date)
        return 0
    dest = OUT_DIR / date
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("card.html"))
    meta = read_json(dest / "meta.json")
    (dest / "post.txt").write_text(compose_post(dest, date) + "\n", encoding="utf-8")
    state["posts"].append({
        "date": date, "check_id": meta["rule"]["id"], "cloud": meta["rule"]["cloud"],
        **{platform: None for platform in PLATFORMS},
    })
    save_state(state)
    log.info("staged %s as out/%s", meta["rule"]["id"], date)
    return 0


def cmd_post(bundle: Path | None, dry_run: bool) -> int:
    date = today()
    if dry_run:
        bundle = bundle or OUT_DIR / date
        text = compose_post(bundle, date)
        print(f"----- DRY RUN ({len(text)}/{LINKEDIN_TEXT_LIMIT} chars) -----\n{text}")
        media = bundle / "card.mp4" if (bundle / "card.mp4").exists() else bundle / "card.png"
        print(f"----- media: {media} -----")
        return 0

    state = load_state()
    entry = entry_for(state, date)
    if not entry:
        log.error("no state entry for %s -- run `stage` first", date)
        return 1
    bundle = OUT_DIR / date
    text = (bundle / "post.txt").read_text(encoding="utf-8").strip()
    if len(linkedin_commentary(text)) > LINKEDIN_TEXT_LIMIT:
        log.error("post is %d chars after escaping, LinkedIn allows %d",
                  len(linkedin_commentary(text)), LINKEDIN_TEXT_LIMIT)
        return 1
    alt = read_json(bundle / "diagram.json")["title"] + " -- before/after diagram for " \
        + entry["check_id"]

    session = requests.Session()
    session.headers["User-Agent"] = "checkov-daily/1.0 (+" + repo_url() + ")"
    errors = []
    for platform in PLATFORMS:
        if entry.get(platform):
            log.info("%s: already posted (%s)", platform, entry[platform])
            continue
        try:
            if not configured(platform):
                log.info("%s: skipped (not configured)", platform)
                continue
            post_id = (send_linkedin(session, text, bundle, alt) if platform == "linkedin"
                       else send_facebook(session, text, bundle))
        except Exception as exc:
            log.error("%s: %s", platform, exc)
            errors.append(platform)
            continue
        entry[platform] = post_id
        save_state(state)                 # immediately: see module docstring
        log.info("%s: posted (%s)", platform, post_id)
    return 1 if errors else 0


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    stage = sub.add_parser("stage")
    stage.add_argument("--from", dest="src", default="out/today")
    post = sub.add_parser("post")
    post.add_argument("--dir")
    post.add_argument("--dry-run", action="store_true",
                      default=os.environ.get("DRY_RUN", "") in ("1", "true"))
    args = parser.parse_args()
    if args.cmd == "stage":
        return cmd_stage((REPO_ROOT / args.src).resolve())
    return cmd_post((REPO_ROOT / args.dir).resolve() if args.dir else None, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
