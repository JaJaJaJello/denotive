"""The model: a convolutional network looks at the picture, a transformer writes the figure as tokens.

This is the "image to sequence" design used for chart and diagram de-rendering research (Pix2Seq, Donut,
StarVector), kept small enough to learn on a laptop CPU. Presets: "small" for CPU, "base" for a GPU.
"""
import math

import torch
import torch.nn as nn
import torch.nn.functional as F

from . import tokens as T

PRESETS = {
    # enc: channels at 1/2, 1/4, 1/8 and 1/16 size, and how many residual blocks each stage gets
    "small": dict(d=256, layers=4, heads=8, ff=1024, enc=(32, 48, 96, 192), blocks=(0, 1, 1, 2), size=(512, 288)),
    "base": dict(d=384, layers=6, heads=8, ff=1536, enc=(48, 96, 192, 384), blocks=(1, 2, 2, 3), size=(768, 432)),
}


def conv_bn(cin, cout, stride=1):
    return nn.Sequential(nn.Conv2d(cin, cout, 3, stride, 1, bias=False), nn.BatchNorm2d(cout), nn.ReLU(inplace=True))


class Res(nn.Module):
    def __init__(self, c):
        super().__init__()
        self.a = conv_bn(c, c)
        self.b = nn.Sequential(nn.Conv2d(c, c, 3, 1, 1, bias=False), nn.BatchNorm2d(c))

    def forward(self, x):
        return F.relu(x + self.b(self.a(x)))


class Encoder(nn.Module):
    """Picture -> a grid of feature vectors, one per 16 x 16 pixel patch."""

    def __init__(self, chans, blocks, d):
        super().__init__()
        layers, cin = [], 3
        for c, n in zip(chans, blocks):  # each stage halves the size
            layers.append(conv_bn(cin, c, 2))
            layers += [Res(c) for _ in range(n)]
            cin = c
        self.net = nn.Sequential(*layers)
        self.proj = nn.Conv2d(cin, d, 1)
        self.norm = nn.LayerNorm(d)

    def forward(self, img):
        if img.device.type == "cpu":
            img = img.contiguous(memory_format=torch.channels_last)  # faster convolutions on a CPU
        f = self.proj(self.net(img)).contiguous()     # B, d, h, w
        B, d, h, w = f.shape
        f = f + _pos2d(d, h, w, f.device, f.dtype)
        return self.norm(f.flatten(2).transpose(1, 2))  # B, h*w, d


def _pos2d(d, h, w, device, dtype):
    """Fixed sine/cosine position codes so the decoder knows where each patch is."""
    q = d // 4
    freq = torch.exp(torch.arange(q, device=device, dtype=torch.float32) * (-math.log(10000.0) / q))
    ys = torch.arange(h, device=device, dtype=torch.float32)[:, None] * freq[None]
    xs = torch.arange(w, device=device, dtype=torch.float32)[:, None] * freq[None]
    py = torch.cat([ys.sin(), ys.cos()], 1)[:, None, :].expand(h, w, 2 * q)
    px = torch.cat([xs.sin(), xs.cos()], 1)[None, :, :].expand(h, w, 2 * q)
    pe = torch.cat([py, px], 2)
    if pe.shape[2] < d:
        pe = F.pad(pe, (0, d - pe.shape[2]))
    return pe.permute(2, 0, 1)[None].to(dtype)


class Block(nn.Module):
    """Decoder layer: look back at the tokens written so far, then at the picture."""

    def __init__(self, d, heads, ff, drop):
        super().__init__()
        self.h, self.drop = heads, drop
        self.ln1, self.qkv, self.o1 = nn.LayerNorm(d), nn.Linear(d, 3 * d), nn.Linear(d, d)
        self.ln2, self.q2, self.kv2, self.o2 = nn.LayerNorm(d), nn.Linear(d, d), nn.Linear(d, 2 * d), nn.Linear(d, d)
        self.ln3 = nn.LayerNorm(d)
        self.ff = nn.Sequential(nn.Linear(d, ff), nn.GELU(), nn.Linear(ff, d))
        self.dropout = nn.Dropout(drop)

    def _split(self, x):
        B, L, D = x.shape
        return x.view(B, L, self.h, D // self.h).transpose(1, 2)

    def _merge(self, x):
        B, H, L, dh = x.shape
        return x.transpose(1, 2).reshape(B, L, H * dh)

    def memory(self, mem):
        k, v = self.kv2(mem).chunk(2, -1)
        return self._split(k), self._split(v)

    def forward(self, x, mem_kv, cache=None):
        p = 0.0  # no dropout inside attention: lets PyTorch use its fast fused attention, a big win on a CPU
        q, k, v = (self._split(t) for t in self.qkv(self.ln1(x)).chunk(3, -1))
        causal = True
        if cache is not None:
            if "k" in cache:
                k = torch.cat([cache["k"], k], 2)
                v = torch.cat([cache["v"], v], 2)
                causal = False  # one new token looking at everything before it
            cache["k"], cache["v"] = k, v
        a = F.scaled_dot_product_attention(q, k, v, is_causal=causal and q.shape[2] > 1, dropout_p=p)
        x = x + self.dropout(self.o1(self._merge(a)))
        mk, mv = mem_kv
        a = F.scaled_dot_product_attention(self._split(self.q2(self.ln2(x))), mk, mv, dropout_p=p)
        x = x + self.dropout(self.o2(self._merge(a)))
        return x + self.dropout(self.ff(self.ln3(x)))


class Vectorizer(nn.Module):
    def __init__(self, preset="small", vocab=None, max_len=T.MAX_TOKENS + 8, drop=0.1):
        super().__init__()
        cfg = PRESETS[preset]
        self.preset, self.max_len = preset, max_len
        d = cfg["d"]
        self.encoder = Encoder(cfg["enc"], cfg["blocks"], d)
        self.encoder.to(memory_format=torch.channels_last)
        self.tok = nn.Embedding(vocab or T.V.size, d)
        self.pos = nn.Embedding(max_len, d)
        self.blocks = nn.ModuleList(Block(d, cfg["heads"], cfg["ff"], drop) for _ in range(cfg["layers"]))
        self.ln = nn.LayerNorm(d)
        self.head = nn.Linear(d, vocab or T.V.size, bias=False)
        self.head.weight = self.tok.weight  # reading and writing tokens share one table
        nn.init.normal_(self.tok.weight, std=0.02)
        nn.init.normal_(self.pos.weight, std=0.01)

    def forward(self, img, toks):
        """Next-token scores for every position (training, with the right answer fed in)."""
        mem = self.encoder(img)
        kv = [b.memory(mem) for b in self.blocks]
        L = toks.shape[1]
        x = self.tok(toks) + self.pos(torch.arange(L, device=toks.device))[None]
        for b, m in zip(self.blocks, kv):
            x = b(x, m)
        return self.head(self.ln(x))

    @torch.no_grad()
    def write(self, img, max_len=None, temperature=0.0, top_p=0.9, grammar=True):
        """Writes token sentences for a batch of pictures, one token at a time, only ever choosing tokens that
        fit the sentence so far."""
        self.eval()
        max_len = min(max_len or self.max_len, self.max_len)
        B, dev = img.shape[0], img.device
        mem = self.encoder(img)
        kv = [b.memory(mem) for b in self.blocks]
        caches = [{} for _ in self.blocks]
        masks = {k: torch.full((T.V.size,), float("-inf"), device=dev).index_fill_(
            0, torch.tensor(v, device=dev), 0.0) for k, v in T.ALLOWED.items()}
        gs = [T.Grammar() for _ in range(B)]
        out = torch.full((B, 1), T.BOS, dtype=torch.long, device=dev)
        cur = out
        for step in range(max_len - 1):
            x = self.tok(cur) + self.pos(torch.tensor([step], device=dev))[None]
            for b, m, c in zip(self.blocks, kv, caches):
                x = b(x, m, c)
            logits = self.head(self.ln(x[:, -1])).float()
            if grammar:
                logits = logits + torch.stack([masks[g.key()] for g in gs])
            if temperature and temperature > 0:
                probs = F.softmax(logits / temperature, -1)
                sp, si = probs.sort(-1, descending=True)
                keep = sp.cumsum(-1) - sp < top_p
                sp = sp * keep
                nxt = si.gather(1, torch.multinomial(sp / sp.sum(-1, keepdim=True), 1))
            else:
                nxt = logits.argmax(-1, keepdim=True)
            for g, t in zip(gs, nxt[:, 0].tolist()):
                g.feed(t)
            out = torch.cat([out, nxt], 1)
            cur = nxt
            if all(g.done for g in gs):
                break
        return out.tolist()


def pick_device(name="auto"):
    if name != "auto":
        return torch.device(name)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if hasattr(torch, "xpu") and torch.xpu.is_available():
        return torch.device("xpu")  # Intel Arc / Core Ultra graphics with PyTorch's XPU build
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def count_params(m):
    return sum(p.numel() for p in m.parameters())
