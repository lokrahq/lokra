"""Record `lokra demo` as an animated SVG for the README and lokra.dev.

The demo runs for real (Docker, or --dsn), with a style that captures each line, typed query,
spinner and pause on a virtual clock instead of printing. The SVG replays that timeline with CSS
animations, so it plays inside an <img> on GitHub and on the site, with no player script.

    .venv/bin/python scripts/record_demo.py [--dsn postgresql://…]
"""
from __future__ import annotations

import argparse
import re
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from xml.sax.saxutils import escape

from lokra.demo import run_demo
from lokra.term import SPINNER, Style

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS = (ROOT / "docs" / "demo.svg", ROOT / "site" / "public" / "demo.svg")

COLS, VIEW_ROWS = 96, 30
CELL, ROW, FONT = 8.4, 18.0, 14
PAD, BAR = 22, 36
WIDTH = round(COLS * CELL + 2 * PAD)
HEIGHT = round(VIEW_ROWS * ROW + BAR + PAD + 10)
HOLD = 4.0
MIN_SPIN = 1.6

INK, FAINT, LINE, SOFT, MARK = "#0a0a0a", "#9b9b96", "#ebebe8", "#f5f5f3", "#f2f07c"
FG = {"30": INK, "31": "#d0443a", "32": "#1f9d55", "33": "#b7791f"}
BLOCKS = {"█": (0.0, 1.0), "▀": (0.0, 0.5), "▄": (0.5, 1.0)}
SGR = re.compile(r"\033\[([0-9;]*)m")


class Recorder(Style):
    def __init__(self):
        super().__init__(color=True, animate=False)
        self.t = 0.0
        self.rows: list[dict] = []
        self.cursor = 0

    def _row(self, segs: list[dict]) -> dict:
        row = {"index": self.cursor, "segs": segs, "start": segs[0]["t0"], "end": None}
        self.rows.append(row)
        self.cursor += 1
        return row

    def _seg(self, text: str, t0: float | None = None, t1: float | None = None, spin: bool = False) -> dict:
        t0 = self.t if t0 is None else t0
        return {"text": text, "t0": t0, "t1": t0 if t1 is None else t1, "spin": spin}

    def line(self, text: str) -> None:
        for part in text.split("\n"):
            self._row([self._seg(part)])

    def pause(self, seconds: float) -> None:
        self.t += seconds

    def typed(self, prefix: str, text: str, say, after: str = "", seconds: float = 0.6) -> None:
        segs = [self._seg(prefix), self._seg(text, self.t, self.t + seconds)]
        self.t += seconds
        if after:
            self.t += 0.25
            segs.append(self._seg(after))
        self._row(segs)

    def build(self, prefix: str, pieces: list[str], say, after: str = "", step: float = 0.12) -> None:
        segs = [self._seg(prefix)]
        for piece in pieces:
            segs.append(self._seg(piece))
            self.t += step
        segs.append(self._seg(after))
        self._row(segs)

    @contextmanager
    def spinner(self, text: str, say, indent: str = "  "):
        start = self.t
        row = self._row([self._seg(indent), self._seg("", spin=True), self._seg(f" {text}…")])
        yield
        self.t = max(self.t, start + MIN_SPIN)
        row["end"] = self.t
        self.cursor = row["index"]


def _width(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _runs(text: str):
    bold = dim = False
    fg = bg = None
    pos = 0
    for m in [*SGR.finditer(text), None]:
        chunk = text[pos:m.start() if m else len(text)]
        for ch in chunk:
            kind = "block" if ch in BLOCKS else "wide" if _width(ch) == 2 else "text"
            yield ch, kind, (bold, dim, fg, bg)
        if not m:
            break
        pos = m.end()
        codes = m.group(1).split(";") if m.group(1) else ["0"]
        i = 0
        while i < len(codes):
            c = codes[i]
            if c == "0":
                bold = dim = False
                fg = bg = None
            elif c == "1":
                bold = True
            elif c == "2":
                dim = True
            elif c in FG:
                fg = FG[c]
            elif c == "48" and codes[i + 1:i + 3] == ["5", "228"]:
                bg = MARK
                i += 2
            i += 1


def _segment_svg(text: str, col: int, y: float) -> tuple[str, int, list[tuple]]:
    out, group, rects = [], [], []

    def flush():
        if not group:
            return
        start, style, chars = group[0][0], group[0][1], "".join(ch for _, _, ch in group)
        bold, dim, fg, _ = style
        fill = fg or (FAINT if dim else INK)
        weight = ' font-weight="700"' if bold else ""
        x, w = start * CELL, len(chars) * CELL
        if chars.strip():
            out.append(f'<text x="{x:.1f}" y="{y + ROW * 0.72:.1f}" fill="{fill}"{weight} '
                       f'textLength="{w:.1f}" lengthAdjust="spacingAndGlyphs">{escape(chars)}</text>')
        group.clear()

    for ch, kind, style in _runs(text):
        bold, dim, fg, bg = style
        w = _width(ch)
        if bg:
            rects.append((0, bg, col * CELL, y, w * CELL, ROW))
        if kind == "text" and group and group[-1][1] == style:
            group.append((col, style, ch))
        elif kind == "text":
            flush()
            group.append((col, style, ch))
        else:
            flush()
            fill = fg or (FAINT if dim else INK)
            if kind == "block":
                top, bottom = BLOCKS[ch]
                rects.append((1, fill, col * CELL, y + top * ROW, CELL, (bottom - top) * ROW))
            else:
                out.append(f'<text x="{col * CELL:.1f}" y="{y + ROW * 0.72:.1f}" fill="{fill}">{escape(ch)}</text>')
        col += w
    flush()
    return "".join(out), col, rects


def _path(rects: list[tuple]) -> str:
    merged: list[list[float]] = []
    for x, y, w, h in sorted(rects, key=lambda r: (r[1], r[3], r[0])):
        last = merged[-1] if merged else None
        if last and last[1] == y and last[3] == h and abs(last[0] + last[2] - x) < 0.01:
            last[2] += w
        else:
            merged.append([x, y, w, h])
    return "".join(f"M{x:.1f} {y:.1f}h{w:.1f}v{h:.1f}h{-w:.1f}z" for x, y, w, h in merged)


def render(rec: Recorder) -> str:
    total = rec.t + HOLD

    def pct(t: float) -> str:
        return f"{min(t / total * 100, 100):.3f}%"

    css, body, n = [], [], 0
    shapes: dict[tuple, list[tuple]] = {}

    def anim(frames: list[tuple[float, str]], extra: str = "") -> str:
        nonlocal n
        n += 1
        steps = "".join(f"{pct(t)}{{{v}}}" for t, v in frames)
        css.append(f"@keyframes k{n}{{{steps}}}.k{n}{{animation-name:k{n}{extra}}}")
        return f"k{n}"

    for row in rec.rows:
        y = row["index"] * ROW
        frames = [(0, "opacity:0"), (row["start"], "opacity:1")]
        frames += [(row["end"], "opacity:0")] if row["end"] is not None else []
        parts, col = [], 0
        for seg in row["segs"]:
            if seg["spin"]:
                glyphs = "".join(f'<text class="sp" style="animation-delay:-{0.8 - i * 0.08:.2f}s" x="{col * CELL:.1f}" '
                                 f'y="{y + ROW * 0.72:.1f}" fill="{FG["33"]}">{g}</text>' for i, g in enumerate(SPINNER))
                parts.append(glyphs)
                col += 1
                continue
            svg, end, rects = _segment_svg(seg["text"], col, y)
            for layer, color, *box in rects:
                shapes.setdefault((layer, seg["t0"], row["end"], color), []).append(tuple(box))
            if seg["t1"] > seg["t0"]:
                chars = len(SGR.sub("", seg["text"]))
                cover = anim([(0, "transform:scaleX(1)"),
                              (seg["t0"], f"transform:scaleX(1);animation-timing-function:steps({chars},end)"),
                              (seg["t1"], "transform:scaleX(0)"), (total, "transform:scaleX(0)")],
                             ";transform-box:fill-box;transform-origin:right")
                svg += (f'<rect class="a {cover}" x="{col * CELL:.1f}" y="{y:.1f}" width="{(end - col) * CELL + 1:.1f}" '
                        f'height="{ROW:.1f}" fill="#fff"/>')
            if seg["t0"] > row["start"] and svg:
                svg = f'<g class="a {anim([(0, "opacity:0"), (seg["t0"], "opacity:1"), (total, "opacity:1")])}">{svg}</g>'
            parts.append(svg)
            col = end
        frames.append((total, frames[-1][1]))
        body.append(f'<g class="a {anim(frames)}">{"".join(parts)}</g>')

    layers = []
    for (layer, start, end, color), boxes in sorted(shapes.items(), key=lambda kv: kv[0][0]):
        frames = [(0, "opacity:0"), (start, "opacity:1")] + ([(end, "opacity:0")] if end is not None else [])
        frames.append((total, frames[-1][1]))
        layers.append(f'<path class="a {anim(frames)}" fill="{color}" d="{_path(boxes)}"/>')
    body[:0] = layers

    times = sorted({0.0, *(r["start"] for r in rec.rows), *(r["end"] for r in rec.rows if r["end"] is not None)})
    camera, last = [], None
    for t in times:
        live = [r["index"] for r in rec.rows if r["start"] <= t and (r["end"] is None or t < r["end"])]
        top = max(0, max(live, default=0) + 1 - VIEW_ROWS)
        if top != last:
            camera.append((t, f"transform:translateY({-top * ROW:.1f}px)"))
            last = top
    camera.append((total, camera[-1][1]))
    cam = anim(camera)

    dots = "".join(f'<circle cx="{PAD + i * 18}" cy="{BAR / 2}" r="5.5" fill="{LINE}"/>' for i in range(3))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" height="{HEIGHT}" role="img" aria-label="lokra demo: two AI agents meet a production-like Postgres. Row locks, masking, blocked prompt injection, an approved write, brakes and a signed ledger.">
<title>lokra demo</title>
<style>
text{{font-family:"SF Mono",ui-monospace,Menlo,Consolas,"Liberation Mono",monospace;font-size:{FONT}px;white-space:pre}}
.a{{animation-duration:{total:.2f}s;animation-iteration-count:infinite;animation-timing-function:steps(1,end)}}
.sp{{opacity:0;animation:sp .8s steps(1,end) infinite}}@keyframes sp{{0%{{opacity:1}}10%,100%{{opacity:0}}}}
{"".join(css)}
</style>
<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{HEIGHT - 1}" rx="12" fill="#fff" stroke="{LINE}"/>
<path d="M0.5 {BAR}H{WIDTH - 0.5}" stroke="{LINE}"/>
<rect x="1" y="1" width="{WIDTH - 2}" height="{BAR - 1.5}" rx="11.5" fill="{SOFT}"/>
<rect x="1" y="{BAR / 2}" width="{WIDTH - 2}" height="{BAR / 2 - 0.5}" fill="{SOFT}"/>
{dots}
<text x="{WIDTH / 2}" y="{BAR / 2 + 4.5}" text-anchor="middle" fill="{FAINT}" style="font-size:12px">lokra demo</text>
<svg x="{PAD}" y="{BAR + 10}" width="{COLS * CELL:.0f}" height="{VIEW_ROWS * ROW:.0f}" overflow="hidden">
<g class="a {cam}">
{chr(10).join(body)}
</g>
</svg>
</svg>
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dsn", help="use this Postgres instead of starting one in Docker")
    args = parser.parse_args()
    rec = Recorder()
    rec.typed(f"{rec.dim('$')} ", "lokra demo", None)
    rec.pause(0.5)
    run_demo(args.dsn, out=rec.line, style=rec)
    svg = render(rec)
    for path in OUTPUTS:
        path.write_text(svg)
    print(f"{len(rec.rows)} lines, {rec.t:.1f}s, {len(svg) // 1024} KB -> " + ", ".join(str(p.relative_to(ROOT)) for p in OUTPUTS))


if __name__ == "__main__":
    main()
