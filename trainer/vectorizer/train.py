"""Trains the model on the generated figures, slowly making the pictures look more and more like blurry,
JPEG-damaged screenshots of a small copy.

    python main.py train                      # carries on from the last save if there is one
    python main.py train --steps 60000 --preset base --batch 32   # on a GPU

Every --sample-every steps it writes runs/<name>/samples/step_XXXXXX.png: for a few held-back figures, the
damaged picture the model saw, the model's own rebuild (drawn by DeNotive) and the right answer.
Stop any time with Ctrl+C; it saves first. Run it again to carry on.
"""
import csv
import json
import math
import os
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

from . import tokens as T
from .degrade import degrade
from .model import PRESETS, Vectorizer, count_params, pick_device

HERE = Path(__file__).resolve().parents[1]


def to_tensor(img):
    a = np.asarray(img, dtype=np.float32) / 255.0
    return torch.from_numpy(a).permute(2, 0, 1).sub_(0.5).div_(0.5)


def to_pil(t):
    a = (t.detach().cpu().permute(1, 2, 0).numpy() * 0.5 + 0.5).clip(0, 1)
    return Image.fromarray((a * 255).astype(np.uint8))


class Figures(torch.utils.data.Dataset):
    """Reads a clean picture and its answer, and damages the picture by the amount the sampler asks for."""

    def __init__(self, root, records, size):
        self.root, self.records, self.size = Path(root), records, size

    def __len__(self):
        return len(self.records)

    def __getitem__(self, key):
        idx, level, seed = key
        rec = self.records[idx]
        img = Image.open(self.root / rec["file"]).convert("RGB")
        img = degrade(img, level, random.Random(seed), self.size)
        return to_tensor(img), torch.tensor(rec["tokens"], dtype=torch.long), level


def collate(batch):
    imgs = torch.stack([b[0] for b in batch])
    L = max(len(b[1]) for b in batch)
    toks = torch.full((len(batch), L), T.PAD, dtype=torch.long)
    for i, b in enumerate(batch):
        toks[i, : len(b[1])] = b[1]
    return imgs, toks, torch.tensor([b[2] for b in batch])


class Curriculum(torch.utils.data.Sampler):
    """Hands out batches of (figure, damage level, seed). The damage grows with the training step, and so does
    the longest figure allowed (in tokens), so the model learns short, clean figures first. Each batch is drawn
    from figures of about the same length, so little time is spent on padding."""

    def __init__(self, lengths, batch, total, ramp, max_level, start=0, seed=0):
        self.lengths = np.asarray(lengths)
        self.batch, self.total, self.ramp, self.max_level = batch, total, ramp, max_level
        self.step = start
        self.rng = random.Random(seed + start)
        order = np.argsort(self.lengths, kind="stable")
        self.sorted_idx, self.sorted_len = order, self.lengths[order]

    def progress(self, step=None):
        s = self.step if step is None else step
        return min(1.0, s / max(1.0, self.ramp * self.total))

    def level(self, step=None):
        return self.max_level * self.progress(step)

    def max_tokens(self, step=None):
        p = min(1.0, (self.step if step is None else step) / max(1.0, 0.4 * self.total))
        lo = min(120, int(self.lengths.max()))
        return int(lo + p * (self.lengths.max() - lo))

    def __len__(self):
        return max(0, self.total - self.step)

    def __iter__(self):
        while self.step < self.total:
            k = int(np.searchsorted(self.sorted_len, self.max_tokens(), side="right"))
            k = max(k, min(len(self.sorted_idx), self.batch * 4))
            win = min(k, self.batch * 16)
            a = self.rng.randrange(0, k - win + 1)   # a neighbourhood of similar lengths
            cur = self.level()
            out = []
            for _ in range(self.batch):
                idx = int(self.sorted_idx[a + self.rng.randrange(win)])
                lv = cur * (self.rng.random() ** 0.5)   # mostly near today's level, sometimes cleaner
                out.append((idx, lv, self.rng.randrange(1 << 30)))
            yield out
            self.step += 1


def lr_at(step, total, base, warm=1000):
    if step < warm:
        return base * (step + 1) / warm
    p = (step - warm) / max(1, total - warm)
    return base * (0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * min(1.0, p))))


def load_records(data):
    recs = [json.loads(line) for line in (Path(data) / "index.jsonl").open(encoding="utf-8") if line.strip()]
    recs.sort(key=lambda r: r["i"])
    return recs


def panel(model, val, size, device, path, level):
    """The damaged input, the model's rebuild and the answer, side by side, for a few held-back figures."""
    from .render import qimage_to_pil, render
    W, H = size
    rows = []
    imgs = torch.stack([v[0] for v in val]).to(device)
    written = model.write(imgs)
    for (img, toks, _), seq in zip(val, written):
        seen = to_pil(img)  # picture pieces are cut out of what the model saw, as predict does
        guess = qimage_to_pil(render(T.decode(seq), W, source=seen))
        truth = qimage_to_pil(render(T.decode(toks.tolist()), W, source=seen))
        rows.append((to_pil(img), guess.resize((W, H)), truth.resize((W, H))))
    sheet = Image.new("RGB", (3 * W + 16, len(rows) * (H + 8)), (110, 110, 110))
    for r, row in enumerate(rows):
        for c, im in enumerate(row):
            sheet.paste(im, (c * (W + 8), r * (H + 8)))
    sheet.save(path)
    model.train()


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="main.py train", description=__doc__.splitlines()[0])
    ap.add_argument("--data", default=str(HERE / "data"))
    ap.add_argument("--run", default=str(HERE / "runs" / "default"))
    ap.add_argument("--preset", default="small", choices=("small", "base"))
    ap.add_argument("--steps", type=int, default=30000)
    ap.add_argument("--batch", type=int, default=12)
    ap.add_argument("--lr", type=float, default=4e-4)
    ap.add_argument("--size", default=None, help="model input size, W x H (default: the preset's)")
    ap.add_argument("--ramp", type=float, default=0.7,
                    help="fraction of the steps over which damage grows from none to --max-level")
    ap.add_argument("--max-level", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=min(4, max(1, (os.cpu_count() or 4) // 4)))
    ap.add_argument("--device", default="auto")
    ap.add_argument("--save-every", type=int, default=500)
    ap.add_argument("--sample-every", type=int, default=2000)
    ap.add_argument("--fresh", action="store_true", help="ignore an earlier save and start over")
    a = ap.parse_args(argv)

    size = tuple(int(v) for v in a.size.lower().split("x")) if a.size else PRESETS[a.preset]["size"]
    run = Path(a.run)
    (run / "samples").mkdir(parents=True, exist_ok=True)
    device = pick_device(a.device)
    if device.type == "cpu":
        torch.set_num_threads(max(1, (os.cpu_count() or 4) - a.workers))
    recs = load_records(a.data)
    n_val = max(8, min(256, len(recs) // 50))
    train_recs, val_recs = recs[:-n_val], recs[-n_val:]
    ds = Figures(a.data, train_recs, size)
    vds = Figures(a.data, val_recs, size)

    model = Vectorizer(a.preset).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, betas=(0.9, 0.98), weight_decay=0.02)
    step, best = 0, float("inf")
    ckpt = run / "last.pt"
    if ckpt.exists() and not a.fresh:
        st = torch.load(ckpt, map_location=device, weights_only=False)
        if st.get("preset") != a.preset or st.get("vocab") != T.V.size:
            sys.exit(f"{ckpt} was made with a different setup; use --run for a new folder or --fresh to restart")
        model.load_state_dict(st["model"])
        opt.load_state_dict(st["opt"])
        step, best = st["step"], st.get("best", best)
        print(f"Carrying on from step {step}.", flush=True)
    print(f"Model: {count_params(model) / 1e6:.1f}M parameters, preset '{a.preset}', on {device}. "
          f"{len(train_recs)} training figures, {n_val} held back.", flush=True)

    sampler = Curriculum([len(r["tokens"]) for r in train_recs], a.batch, a.steps, a.ramp, a.max_level, start=step)
    dl = torch.utils.data.DataLoader(ds, batch_sampler=sampler, num_workers=a.workers, collate_fn=collate,
                                     persistent_workers=False, pin_memory=device.type == "cuda")
    use_amp = device.type in ("cuda", "xpu")
    amp_dtype = torch.bfloat16 if device.type == "xpu" or (device.type == "cuda" and
                                                             torch.cuda.is_bf16_supported()) else torch.float16
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda" and amp_dtype == torch.float16)

    def save(path, with_optimizer=True):
        st = {"model": model.state_dict(), "step": step, "best": best, "preset": a.preset, "vocab": T.V.size,
              "size": size}
        if with_optimizer:  # only needed to carry on training
            st["opt"] = opt.state_dict()
        torch.save(st, path)

    def validate():
        model.eval()
        rng = random.Random(7)
        tot, cnt, correct = 0.0, 0, 0
        lv = sampler.level()
        with torch.no_grad():
            for k in range(0, len(vds), a.batch):
                items = [vds[(i, lv * rng.random() ** 0.5, 1000 + i)] for i in range(k, min(len(vds), k + a.batch))]
                imgs, toks, _ = collate(items)
                imgs, toks = imgs.to(device), toks.to(device)
                logits = model(imgs, toks[:, :-1]).float()
                tgt = toks[:, 1:]
                m = tgt != T.PAD
                tot += F.cross_entropy(logits[m], tgt[m], reduction="sum").item()
                correct += (logits.argmax(-1)[m] == tgt[m]).sum().item()
                cnt += m.sum().item()
        model.train()
        return tot / max(1, cnt), correct / max(1, cnt)

    log_path = run / "log.csv"
    new_log = not log_path.exists()
    logf = log_path.open("a", newline="", encoding="utf-8")
    log = csv.writer(logf)
    if new_log:
        log.writerow(["step", "loss", "val_loss", "val_token_accuracy", "damage_level", "max_tokens", "lr",
                      "sec_per_step"])
    model.train()
    t0, run_loss, seen = time.time(), 0.0, 0
    print(f"Training to step {a.steps}. Damage reaches {a.max_level:.2f} at step {int(a.ramp * a.steps)}. "
          f"Ctrl+C saves and stops.", flush=True)
    try:
        for imgs, toks, _ in dl:
            imgs, toks = imgs.to(device, non_blocking=True), toks.to(device, non_blocking=True)
            for g in opt.param_groups:
                g["lr"] = lr_at(step, a.steps, a.lr)
            with torch.autocast(device.type, dtype=amp_dtype, enabled=use_amp):
                logits = model(imgs, toks[:, :-1])
            tgt = toks[:, 1:]
            loss = F.cross_entropy(logits.float().reshape(-1, logits.shape[-1]), tgt.reshape(-1),
                                   ignore_index=T.PAD, label_smoothing=0.05)
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(opt)
            scaler.update()
            step += 1
            run_loss += loss.item()
            seen += 1
            if step % 50 == 0:
                dt = (time.time() - t0) / seen
                print(f"step {step:>7}  loss {run_loss / seen:.3f}  damage {sampler.level(step):.2f}  "
                      f"tokens<= {sampler.max_tokens(step)}  {dt:.2f}s/step  "
                      f"~{(a.steps - step) * dt / 3600:.1f} h left", flush=True)
            if step % a.save_every == 0 or step == a.steps:
                vl, acc = validate()
                dt = (time.time() - t0) / max(1, seen)
                log.writerow([step, round(run_loss / max(1, seen), 4), round(vl, 4), round(acc, 4),
                              round(sampler.level(step), 3), sampler.max_tokens(step), f"{lr_at(step, a.steps, a.lr):.2e}",
                              round(dt, 3)])
                logf.flush()
                if vl < best:
                    best = vl
                    save(run / "best.pt", with_optimizer=False)
                save(ckpt)
                print(f"  saved: held-back loss {vl:.3f}, token accuracy {acc:.1%}", flush=True)
                t0, run_loss, seen = time.time(), 0.0, 0
            if step % a.sample_every == 0 or step == a.steps:
                rng = random.Random(step)
                val = [vds[(i, sampler.level(step), 5000 + i)] for i in rng.sample(range(len(vds)), min(4, len(vds)))]
                path = run / "samples" / f"step_{step:07d}.png"
                panel(model, val, size, device, path, sampler.level(step))
                print(f"  sample sheet: {path}", flush=True)
            if step >= a.steps:
                break
    except KeyboardInterrupt:
        print("Stopping: saving first...", flush=True)
        save(ckpt)
    finally:
        logf.close()
    print(f"Done at step {step}. Best held-back loss {best:.3f}. Model: {run / 'best.pt'}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
