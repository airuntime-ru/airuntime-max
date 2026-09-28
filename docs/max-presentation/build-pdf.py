"""Print deck.html to PDF with headless Chrome.

    python build-pdf.py            -> presentation.pdf, placeholders on slide 1 (goes to git)
    python build-pdf.py --jury     -> presentation.jury.pdf, slide 1 filled with working values

The brief wants working tokens on slide 1, and the repository must never hold them. So the
deck marks those spots with data-secret="NAME", and --jury fills them from the environment
(or from .secrets.env next to this script) into a temporary copy that is deleted right after
printing. Both the copy and the jury PDF are git-ignored; hand the jury PDF over directly.

Chrome is looked up in the usual places; set CHROME=<path> to point at another one.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DECK = HERE / "deck.html"
FILLED = HERE / "deck.filled.html"  # must sit next to deck.html: images are relative
SECRETS_FILE = HERE / ".secrets.env"

SECRET_SPOT = re.compile(
    r'<(?P<tag>\w+)(?P<attrs>[^>]*\bdata-secret="(?P<name>[A-Z0-9_]+)"[^>]*)>(?P<body>.*?)</(?P=tag)>',
    re.S,
)

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
    "chromium-browser",
]


def find_chrome() -> str:
    for candidate in [os.environ.get("CHROME", "")] + CHROME_CANDIDATES:
        if not candidate:
            continue
        if Path(candidate).is_file():
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    sys.exit("Chrome not found: set CHROME=<path to chrome or chromium>")


def read_secrets() -> dict[str, str]:
    values: dict[str, str] = {}
    if SECRETS_FILE.is_file():
        for line in SECRETS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    # The environment wins over the file, so a one-off value never has to touch the disk.
    values.update({key: value for key, value in os.environ.items() if value})
    return values


def fill(source: str, values: dict[str, str]) -> tuple[str, list[str], list[str]]:
    filled: list[str] = []
    missing: list[str] = []

    def put(match: re.Match[str]) -> str:
        name = match["name"]
        value = values.get(name, "")
        if not value:
            missing.append(name)
            return match[0]
        filled.append(name)
        shown = html.escape(value)
        body = shown if match["tag"] == "code" else f"<code>{shown}</code>"
        # A token is one long word: let it break anywhere instead of pushing the card wide.
        return f'<{match["tag"]}{match["attrs"]} style="word-break:break-all; color:var(--ink);">{body}</{match["tag"]}>'

    return SECRET_SPOT.sub(put, source), filled, missing


def print_pdf(html_path: Path, pdf_path: Path) -> None:
    profile = tempfile.mkdtemp(prefix="deck-chrome-")
    try:
        subprocess.run(
            [
                find_chrome(),
                "--headless=new",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--user-data-dir={profile}",
                f"--print-to-pdf={pdf_path}",
                html_path.as_uri(),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=180,
        )
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    if not pdf_path.is_file():
        sys.exit(f"Chrome did not write {pdf_path.name}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--jury", action="store_true", help="fill data-secret spots and write presentation.jury.pdf"
    )
    args = parser.parse_args()

    if not args.jury:
        print_pdf(DECK, HERE / "presentation.pdf")
        print("wrote presentation.pdf (placeholders on slide 1)")
        return

    source, filled, missing = fill(DECK.read_text(encoding="utf-8"), read_secrets())
    if "MAX_BOT_TOKEN" in missing:
        sys.exit(
            "MAX_BOT_TOKEN is not set: the jury PDF without it is the same as presentation.pdf"
        )
    FILLED.write_text(source, encoding="utf-8")
    try:
        print_pdf(FILLED, HERE / "presentation.jury.pdf")
    finally:
        FILLED.unlink(missing_ok=True)
    print(f"wrote presentation.jury.pdf, filled: {', '.join(filled)}")
    if missing:
        print(f"left as placeholders: {', '.join(sorted(set(missing)))}")


if __name__ == "__main__":
    main()
