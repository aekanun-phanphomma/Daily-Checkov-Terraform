"""diagram.json -> HTML template -> PNG (1200x1500) via headless Chromium.

    python src/render.py --dir out/today      # re-render an existing bundle

generate.py calls render_card() as the last gate (G8), so a spec whose
labels do not fit the canvas goes back to the model as a repair instead of
failing the whole run after every other gate has passed.

Why not a text-to-image model: they misspell labels, point arrows the wrong
way and invent icons, and nothing can verify the result. Here the model
only writes a small JSON spec, G7 checks it against the verified code, and
the pixels come from a fixed template -- the same spec always renders the
same image, and the image can never say something the code does not.
"""

from __future__ import annotations

import argparse
import os
from importlib.metadata import version
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from common import REPO_ROOT, TEMPLATES_DIR, log, read_json, setup_logging

WIDTH, HEIGHT = 1200, 1500


def render_html(spec: dict, rule: dict) -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR),
                      autoescape=select_autoescape(["html"]))
    return env.get_template("diagram.html").render(
        spec=spec,
        check_id=rule["id"],
        cloud=rule["cloud"],
        resource=rule["resource"],
        checkov_version=version("checkov"),
        repo="github.com/" + os.environ.get(
            "GITHUB_REPOSITORY", "aekanun-phanphomma/Daily-Checkov-Terraform"),
    )


class CanvasOverflow(RuntimeError):
    pass


def render_card(spec: dict, rule: dict, bundle: Path) -> Path:
    """Write card.html (for debugging) and card.png into the bundle."""
    html = render_html(spec, rule)
    (bundle / "card.html").write_text(html, encoding="utf-8")
    render_png(html, bundle / "card.png")
    return bundle / "card.png"


def render_png(html: str, out: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        page.set_content(html, wait_until="load")
        # A label longer than its box would silently overflow off-canvas.
        # Fail instead, so G8 sends it back to the model as a repair.
        overflow = page.evaluate(
            "document.body.scrollHeight > %d || document.body.scrollWidth > %d"
            % (HEIGHT, WIDTH))
        if overflow:
            raise CanvasOverflow("diagram content does not fit the 1200x1500 canvas; "
                                 "shorten labels/callouts or use fewer edges")
        page.screenshot(path=str(out), clip={"x": 0, "y": 0,
                                              "width": WIDTH, "height": HEIGHT})
        browser.close()


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="out/today")
    args = parser.parse_args()
    bundle = (REPO_ROOT / args.dir).resolve()
    rule = read_json(bundle / "meta.json")["rule"]
    png = render_card(read_json(bundle / "diagram.json"), rule, bundle)
    log.info("rendered %s", png)


if __name__ == "__main__":
    main()
