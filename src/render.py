"""diagram.json -> HTML story -> animated MP4 + static PNG (1200x1500).

    python src/render.py --dir out/today      # re-render an existing bundle

generate.py calls render_card() as the last gate (G8), so a spec whose
labels do not fit the canvas goes back to the model as a repair instead of
failing the whole run after every other gate has passed.

Why not a text-to-image model: they misspell labels, point arrows the wrong
way and invent icons, and nothing can verify the result. Here the model
only writes a small JSON spec, G7 checks it against the verified code, and
the pixels come from a fixed template -- the same spec always renders the
same image, and the image can never say something the code does not.

The video is one 12-second story on one stage -- the attack gets through,
the fix lands on the gate, the same attack bursts on it -- rather than two
panels side by side, so the change happens where the viewer is already
looking. Official AWS/Azure icons come from catalog/icons.yaml via
icons.py; anything without one falls back to the neutral line icons.

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
from icons import for_rule

WIDTH, HEIGHT = 1200, 1500
FPS = 24
DURATION = 12.0           # seconds; LinkedIn needs >= 3, feeds loop it anyway
STILL_AT = 9.4            # the PNG is this moment: fixed, attack bursting, ally flowing

# When each beat starts, in seconds. The template turns these into CSS
# variables (--t-attack, ...), so the whole storyboard lives here.
TIMELINE = {
    "attack": 1.0,        # traffic starts flowing through the open gate
    "exposed": 2.0,       # EXPOSED stamp + stage shake
    "fix": 4.8,           # bad line struck out
    "type": 5.2,          # good line types in
    "flip": 5.6,          # MISCONFIGURED -> FIXED, Checkov FAILED -> PASSED
    "shield": 5.8,        # the gate closes
    "safe": 6.3,          # attack bursts on the gate, ally path opens
    "protected": 7.2,     # PROTECTED stamp
    "outro": 10.4,        # verdict pulse
}
# Scene kits: what the log and the stamps say, per kind of fix. A security
# group DENYs a request; encryption does not stop the read, it makes the
# bytes useless; logging stops nothing, it makes the call visible. Drawing
# all three as "blocked at the gate" would teach the wrong thing, so a kit
# with blocks=False keeps the traffic flowing after the fix, muted.
#   hit: attacker's verdict before   stop: after   ally: allowed source after
KITS = {
    "network":   dict(log="FLOW LOG", hit="ALLOW", stop="DENY", ally="ALLOW",
                      bad="EXPOSED", good="PROTECTED", blocks=True),
    "exposure":  dict(log="ACCESS LOG", hit="200 OK", stop="403", ally="200 OK",
                      bad="EXPOSED", good="PROTECTED", blocks=True),
    "identity":  dict(log="API AUDIT", hit="ALLOWED", stop="DENIED", ally="ALLOWED",
                      bad="OVER-PRIVILEGED", good="LEAST PRIVILEGE", blocks=True),
    "secrets":   dict(log="WHO CAN READ IT", hit="PLAINTEXT", stop="REDACTED", ally="RESOLVED",
                      bad="LEAKED", good="PROTECTED", blocks=True),
    "metadata":  dict(log="IMDS CALLS", hit="200 CREDS", stop="401", ally="200 TOKEN",
                      bad="EXPOSED", good="PROTECTED", blocks=True),
    "crypto":    dict(log="RAW READS", hit="READABLE", stop="CIPHERTEXT", ally="DECRYPTED",
                      bad="READABLE", good="ENCRYPTED", blocks=False),
    "transport": dict(log="ON THE WIRE", hit="PLAINTEXT", stop="ENCRYPTED", ally="TLS",
                      bad="SNIFFABLE", good="ENCRYPTED", blocks=False),
    "audit":     dict(log="AUDIT TRAIL", hit="NOT LOGGED", stop="LOGGED", ally="LOGGED",
                      bad="UNSEEN", good="RECORDED", blocks=False),
}
KIT_OF_TOPIC = {
    "network-ingress": "network", "network-segmentation": "network", "hardening": "network",
    "public-exposure": "exposure", "access-control": "exposure",
    "data-protection": "exposure", "supply-chain": "exposure",
    "iam-wildcard": "identity", "key-management": "identity",
    "secrets": "secrets", "instance-metadata": "metadata",
    "encryption-at-rest": "crypto", "transport-security": "transport", "logging": "audit",
}
# Documentation-only ranges (RFC 5737): never someone's real address.
ATTACKER_IPS = ["203.0.113.24", "198.51.100.7", "203.0.113.87", "192.0.2.45", "198.51.100.140"]

ICONS = {"globe", "user", "server", "bucket", "database", "shield", "firewall", "key",
         "function", "container", "cluster", "identity", "queue", "lock", "app", "network"}
TYPE_ICON = {"internet": "globe", "user": "user", "service": "app",
             "resource": "server", "control": "shield"}
# Neutral fallback icons, inferred from a label and the rule's resource
# type. First match wins, so the more specific words come first ("key
# vault" before "vault", "sql" before "server").
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


def infer_icon(text: str) -> str | None:
    text = text.lower().replace("_", " ")
    for pattern, icon in RESOURCE_ICON:
        if re.search(pattern, text):
            return icon
    return None


def pick_icon(node: dict, resource_type: str) -> str:
    """Neutral icon for a node that has no vendor icon."""
    if node.get("icon") in ICONS:
        return node["icon"]
    if node["type"] == "resource":
        return infer_icon(f"{node['label']} {resource_type}") or TYPE_ICON["resource"]
    return TYPE_ICON[node["type"]]


def build_story(spec: dict, rule: dict) -> dict:
    """Everything the template draws, derived from the verified spec.

    The gate's before/after text is the first -/+ code_diff pair, which G7
    has already matched against the code, so the picture can only show a
    value the Terraform really has.
    """
    minus = [line for line in spec["code_diff"] if line.startswith("-")]
    plus = [line for line in spec["code_diff"] if line.startswith("+")]
    attacker, target, ally = spec["attacker"], spec["target"], spec.get("ally")
    brand = for_rule(rule["cloud"], rule["resource"], ally and ally["label"])

    # The request log is illustrative traffic for exactly the edge drawn:
    # the attacker's requests ALLOWed, then the same requests DENYed at the
    # gate. A CIDR in the fix gives the allowed source a plausible address.
    cidr = next((m.group(1) for line in plus
                 if (m := re.search(r"(\d+\.\d+\.\d+)\.\d+/\d+", line))), None)
    ally_src = f"{cidr}.12" if cidr else (ally["label"] if ally else "")
    sources = ATTACKER_IPS if attacker["type"] == "internet" else [attacker["label"]] * 5
    # A long diff needs the room: the log shrinks, then goes, before the
    # card would overflow.
    rows = 5 if len(spec["code_diff"]) <= 4 else (0 if ally else 3)
    after = ([(0, False), (None, True), (1, False), (2, False), (None, True)] if ally
             else [(i, False) for i in range(5)])

    # Column as wide as the longest source: labels run up to 22 characters,
    # and a fixed width glued "Stolen access key" to the action after it.
    src_width = max(len(s) for s in [*sources, ally_src]) + 2

    def row(src: str) -> str:
        return f"{src:<{src_width}}{spec['attack_label']:<30}→ {target['label']}"

    return {
        **spec,
        "kit": KITS[KIT_OF_TOPIC.get(rule.get("topic", ""), "network")],
        "attacker": {**attacker, "icon": TYPE_ICON[attacker["type"]]},
        "target": {**target, "img": brand["target"],
                   "icon": pick_icon({"type": "resource", "label": target["label"]},
                                     rule["resource"])},
        "gate": {**spec["gate"], "img": brand["gate"],
                 # From the gate's own label only: the resource type names
                 # the target, so "Public access toggle" on a storage
                 # account would otherwise get the target's bucket icon.
                 "icon": infer_icon(spec["gate"]["label"]) or "lock",
                 "before": minus[0][2:], "after": plus[0][2:],
                 "width": max(len(minus[0]), len(plus[0])) - 2},
        "ally": ally and {**ally, "img": brand["ally"],
                          "icon": "network" if infer_icon(ally["label"]) == "network" else "user"},
        "minus": minus,
        "plus": plus,
        "code_width": max(len(line) for line in spec["code_diff"]),
        "log_before": [{"text": row(src)} for src in sources[:rows]],
        "log_after": [{"text": row(ally_src if allowed else sources[i]), "allowed": allowed}
                      for i, allowed in after[:rows]],
    }


def render_html(spec: dict, rule: dict) -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR),
                      autoescape=select_autoescape(["html"]))
    return env.get_template("story.html").render(
        s=build_story(spec, rule), t=TIMELINE,
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
# The page growing past the canvas is one overflow; a label wider than its
# box is the other. The stage clips its contents, so without the second
# check a long label there would be cut off silently.
_OVERFLOW = """(() => {
  if (document.body.scrollHeight > %d || document.body.scrollWidth > %d) return "page";
  const stage = document.querySelector(".stage").getBoundingClientRect();
  for (const el of document.querySelectorAll("[data-fit]")) {
    const box = el.getBoundingClientRect();
    if (el.scrollWidth > el.clientWidth + 1 || box.left < stage.left || box.right > stage.right)
      return "too wide: " + el.textContent.trim().split(/\\s+/).join(" ");
  }
  return "";
})()"""


def render_media(html: str, png: Path, mp4: Path | None) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        page.set_content(html, wait_until="load")
        page.evaluate(_SEEK, STILL_AT * 1000)
        # A label longer than its box would silently overflow or be clipped.
        # Fail instead, so G8 sends it back to the model as a repair.
        overflow = page.evaluate(_OVERFLOW % (HEIGHT, WIDTH))
        if overflow:
            raise CanvasOverflow(f"diagram content does not fit the 1200x1500 canvas "
                                 f"({overflow}); shorten labels, callouts or code_diff")
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
