"""DeNotive Vectorizer: train a model that redraws a picture of a figure as editable DeNotive pieces.

    python main.py generate [--count 60000]      make the training figures (once)
    python main.py preview                       see what the blur / JPEG damage looks like at each level
    python main.py train [--steps 30000]         train (Ctrl+C to pause, run again to carry on)
    python main.py predict picture.png [--clipboard]
    python main.py shapes picture.png --out result.json   what DeNotive's "Predict Shapes" runs

Add --help after any command for its options. See README.md.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def preview(argv):
    import argparse
    import random
    from PIL import Image
    ap = argparse.ArgumentParser(prog="main.py preview")
    ap.add_argument("--data", default=str(Path(__file__).resolve().parent / "data"))
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "runs" / "damage_preview"))
    a = ap.parse_args(argv)
    from vectorizer.degrade import preview as strip
    files = sorted((Path(a.data) / "img").glob("*.png"))
    if not files:
        sys.exit("No figures yet: run  python main.py generate  first.")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for f in random.Random(0).sample(files, min(a.count, len(files))):
        print(strip(Image.open(f).convert("RGB"), out / f"{f.stem}_levels.png"))
    print("Top to bottom: damage 0, 0.25, 0.5, 0.75, 1.0")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "generate":
        from vectorizer.generate import main as m
    elif cmd == "train":
        from vectorizer.train import main as m
    elif cmd == "predict":
        from vectorizer.predict import main as m
    elif cmd == "shapes":  # what DeNotive's "Predict Shapes" runs
        from vectorizer.shapes import main as m
    elif cmd == "preview":
        m = preview
    else:
        sys.exit(f"Unknown command {cmd!r}.\n{__doc__}")
    m(rest)


if __name__ == "__main__":
    main()
