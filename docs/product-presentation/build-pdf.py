"""Print deck.html to presentation.pdf with headless Chrome.

    python build-pdf.py

Chrome is looked up in the usual places; set CHROME=<path> to point at another one.
The page is 1280x720 px (16:9); @page in deck.html must match .slide exactly.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent

# One printer for both decks: reuse the MAX deck's Chrome lookup and print settings.
_spec = importlib.util.spec_from_file_location(
    "max_build", HERE.parent / "max-presentation" / "build-pdf.py"
)
_max = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_max)

if __name__ == "__main__":
    _max.print_pdf(HERE / "deck.html", HERE / "presentation.pdf")
    print("wrote presentation.pdf")
