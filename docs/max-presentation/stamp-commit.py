"""Put the current commit on slide 1 of both decks and rebuild them.

    python stamp-commit.py            # stamp HEAD, rebuild presentation.pdf and .pptx
    python stamp-commit.py <sha>      # stamp a specific commit
    python stamp-commit.py --no-build # only rewrite the sources

The track checks that the submitted materials match a fixed version of the source, so the
hash on slide 1 has to be the last commit before the deadline. It lives in two places -
deck.html (PDF) and the COMMIT constant in build-pptx.js - and this keeps them equal.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DECK = HERE / "deck.html"
PPTX_BUILDER = HERE / "build-pptx.js"
FULL_SHA = re.compile(r"\b[0-9a-f]{40}\b")


def read(path: Path) -> tuple[str, bool]:
    raw = path.read_bytes().decode("utf-8")
    return raw.replace("\r\n", "\n"), "\r\n" in raw


def write(path: Path, text: str, crlf: bool) -> None:
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def stamp(sha: str) -> None:
    short = sha[:7]

    text, crlf = read(DECK)
    old = FULL_SHA.search(text)
    if not old:
        sys.exit("deck.html has no commit hash to replace")
    text = text.replace(old.group(0), sha).replace(
        f"короткий <code>{old.group(0)[:7]}</code>", f"короткий <code>{short}</code>"
    )
    write(DECK, text, crlf)

    text, crlf = read(PPTX_BUILDER)
    text, hits = re.subn(r'(const COMMIT = ")[0-9a-f]{40}(";)', rf"\g<1>{sha}\g<2>", text)
    if hits != 1:
        sys.exit("build-pptx.js has no COMMIT constant to replace")
    write(PPTX_BUILDER, text, crlf)
    print(f"stamped {sha} ({short}) in deck.html and build-pptx.js")


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--no-build"]
    build = "--no-build" not in sys.argv
    sha = (
        args[0]
        if args
        else subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True, check=True
        ).stdout.strip()
    )
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        sys.exit(f"not a full commit hash: {sha}")
    stamp(sha)
    if build:
        subprocess.run([sys.executable, str(HERE / "build-pdf.py")], cwd=HERE, check=True)
        subprocess.run(
            ["node", str(PPTX_BUILDER)], cwd=HERE, check=True, shell=sys.platform == "win32"
        )


if __name__ == "__main__":
    main()
