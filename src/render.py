"""diagram.json -> HTML template -> animated MP4 + static PNG (1200x1500).

    python src/render.py --dir out/today      # re-render an existing bundle

generate.py calls render_card() as the last gate (G8), so a spec whose
labels do not fit the canvas goes back to the model as a repair instead of
failing the whole run after every other gate has passed.

Why not a text-to-image model: they misspell labels, point arrows the wrong
way and invent icons, and nothing can verify the result. Here the model
only writes a small JSON spec, G7 checks it against the verified code, and
the pixels come from a fixed template -- the same spec always renders the
same image, and the image can never say something the code does not.

Why the video is frame-stepped instead of screen-recorded: Playwright's
recorder captures wall-clock time, so a slow CI runner drops frames and
every run differs. Here every CSS animation is paused and seeked to exactly
N / FPS seconds before frame N is captured. A slow machine takes longer to
render; it never renders a different video. Frames are piped straight into
ffmpeg (bundled by imageio-ffmpeg, so local and CI use the same binary).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from importlib.metadata import version
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from common import REPO_ROOT, TEMPLATES_DIR, log, read_json, setup_logging

WIDTH, HEIGHT = 1200, 1500
FPS = 24
DURATION = 9.0            # seconds; LinkedIn needs >= 3, feeds loop it anyway
STILL_AT = 7.6            # the PNG is this moment: everything revealed, packets mid-flight

# When each part enters, in seconds. The template reads these, so the whole
# storyboard lives in one place: problem first, then the fix, then the code.
TIMELINE = {"before": 0.3, "after": 2.8, "flow_offset": 0.5, "code": 5.0}

ICONS = {"globe", "user", "server", "bucket", "database", "shield", "firewall", "key",
         "function", "container", "cluster", "identity", "queue", "lock", "app", "network"}
TYPE_ICON = {"internet": "globe", "user": "user", "service": "app",
             "resource": "server", "control": "shield"}
# Resource nodes get an icon for what they are, inferred from the label the
# model wrote and the rule's resource type. First match wins, so the more
# specific words come first ("key vault" before "vault", "sql" before "server").
RESOURCE_ICON = [
    (r"bucket|s3|blob|storage", "bucket"),
    (r"key ?vault|kms|crypto ?key|\bkey\b", "key"),
    (r"secret|password|credential", "lock"),
    (r"sql|database|\bdb\b|rds|aurora|cosmos|redshift|bigquery|dynamo|spanner|redis|cache", "database"),
    (r"security ?group|firewall|nsg|network ?security|nacl|acl rule", "firewall"),
    (r"lambda|function|cloud ?run|app ?service|web ?app", "function"),
    (r"eks|aks|gke|kubernetes|cluster", "cluster"),
    (r"ecr|registry|artifact|image|container", "container"),
    (r"iam|role|policy|identity|principal|service ?account", "identity"),
    (r"sqs|sns|queue|topic|pub ?sub|service ?bus|event ?hub", "queue"),
    (r"vpc|subnet|network|load ?balancer|\blb\b|listener|gateway|cloudfront|dns", "network"),
    (r"instance|vm\b|virtual ?machine|ec2|compute|server", "server"),
]


def pick_icon(node: dict, resource_type: str) -> str:
    if node.get("icon") in ICONS:
        return node["icon"]
    if node["type"] == "resource":
        text = f"{node['label']} {resource_type}".lower().replace("_", " ")
        for pattern, icon in RESOURCE_ICON:
            if re.search(pattern, text):
                return icon
    return TYPE_ICON[node["type"]]


def render_html(spec: dict, rule: dict) -> str:
    icons = {f"{panel}:{n['id']}": pick_icon(n, rule["resource"])
             for panel in ("before", "after") for n in spec[panel]["nodes"]}
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR),
                      autoescape=select_autoescape(["html"]))
    return env.get_template("diagram.html").render(
        spec=spec, icons=icons, t=TIMELINE,
        check_id=rule["id"],
        cloud=rule["cloud"],
        resource=rule["resource"],
        checkov_version=version("checkov"),
        repo="github.com/" + os.environ.get(
            "GITHUB_REPOSITORY", "aekanun-phanphomma/Daily-Checkov-Terraform"),
    )


class CanvasOverflow(RuntimeError):
    pass


def render_card(spec: dict, rule: dict, bundle: Path, video: bool = True) -> Path:
    """Write card.html (debugging), card.png and, unless disabled, card.mp4."""
    html = render_html(spec, rule)
    (bundle / "card.html").write_text(html, encoding="utf-8")
    render_media(html, bundle / "card.png", bundle / "card.mp4" if video else None)
    return bundle / "card.png"


_SEEK = "t => document.getAnimations().forEach(a => { a.pause(); a.currentTime = t; })"


def render_media(html: str, png: Path, mp4: Path | None) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        page.set_content(html, wait_until="load")
        page.evaluate(_SEEK, STILL_AT * 1000)
        # A label longer than its box would silently overflow off-canvas.
        # Fail instead, so G8 sends it back to the model as a repair.
        overflow = page.evaluate(
            "document.body.scrollHeight > %d || document.body.scrollWidth > %d"
            % (HEIGHT, WIDTH))
        if overflow:
            raise CanvasOverflow("diagram content does not fit the 1200x1500 canvas; "
                                 "shorten labels/callouts or use fewer edges")
        clip = {"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT}
        page.screenshot(path=str(png), clip=clip)
        if mp4:
            _encode(page, clip, mp4)
        browser.close()


def _encode(page, clip: dict, mp4: Path) -> None:
    import imageio_ffmpeg

    frames = int(DURATION * FPS)
    ffmpeg = subprocess.Popen(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
         "-f", "image2pipe", "-framerate", str(FPS), "-i", "-",
         # H.264 + yuv420p + faststart is the combination every feed player
         # (LinkedIn, Facebook, iOS Safari) accepts without re-encoding.
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "20",
         "-movflags", "+faststart", str(mp4)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for n in range(frames):
            page.evaluate(_SEEK, n * 1000 / FPS)
            ffmpeg.stdin.write(page.screenshot(clip=clip, type="png"))
        ffmpeg.stdin.close()
        if ffmpeg.wait(timeout=300) != 0:
            raise RuntimeError("ffmpeg failed: " + ffmpeg.stderr.read().decode()[-1000:])
    finally:
        if ffmpeg.poll() is None:
            ffmpeg.kill()


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="out/today")
    parser.add_argument("--no-video", action="store_true")
    args = parser.parse_args()
    bundle = (REPO_ROOT / args.dir).resolve()
    rule = read_json(bundle / "meta.json")["rule"]
    png = render_card(read_json(bundle / "diagram.json"), rule, bundle,
                      video=not args.no_video)
    log.info("rendered %s%s", png, "" if args.no_video else " + card.mp4")


if __name__ == "__main__":
    main()
