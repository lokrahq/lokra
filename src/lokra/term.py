"""Terminal output: the Lokra banner, colour only where it is wanted (no-color.org), and pacing
that runs only on an interactive terminal, so pipes, logs and tests get plain, instant text."""
from __future__ import annotations

import itertools
import os
import re
import sys
import threading
import time
from contextlib import contextmanager

from . import __version__

WORDMARK = (
    "██  ▄▄▄▄  ██                 ",
    "██ ██  ██ ██                 ",
    "██ ██████ ██ ▄█▀ ██▄█▀  ▀▀▀█▄",
    "██ ██  ██ ████   ██    ▄█▀▀██",
    "██ ██████ ██ ▀█▄ ██    ▀█▄▄██",
)
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
MASKED = re.compile(r"\[[A-Z]+\]|\*{3,}")


class Style:
    def __init__(self, color: bool = False, animate: bool = False):
        self.color = color
        self.animate = animate

    @classmethod
    def detect(cls, stream=None) -> Style:
        stream = stream or sys.stdout
        tty = bool(getattr(stream, "isatty", lambda: False)()) and os.environ.get("TERM") != "dumb"
        if os.environ.get("NO_COLOR"):
            color = False
        elif os.environ.get("FORCE_COLOR"):
            color = True
        else:
            color = tty
        return cls(color, tty)

    def _sgr(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def bold(self, text: str) -> str:
        return self._sgr(text, "1")

    def dim(self, text: str) -> str:
        return self._sgr(text, "2")

    def green(self, text: str) -> str:
        return self._sgr(text, "32")

    def yellow(self, text: str) -> str:
        return self._sgr(text, "33")

    def red(self, text: str) -> str:
        return self._sgr(text, "1;31")

    def mark(self, text: str) -> str:
        return self._sgr(text, "30;48;5;228")

    def ok(self, text: str) -> str:
        return f"{self.green('✓')} {text}"

    def masked(self, text: str) -> str:
        return MASKED.sub(lambda m: self.mark(m.group()), text)

    def banner(self) -> list[str]:
        pad = " " * (len(WORDMARK[0]) + 4)
        art = [self.mark(pad)] + [self.mark(f"  {line}  ") for line in WORDMARK] + [self.mark(pad)]
        return ["", *(f"  {line}" for line in art), "",
                f"  {self.bold('Lock rows for AI agents.')}  {self.dim(f'v{__version__}')}", ""]

    def pause(self, seconds: float) -> None:
        if self.animate:
            time.sleep(seconds)

    def _write(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def typed(self, prefix: str, text: str, say, after: str = "", seconds: float = 0.6) -> None:
        if not self.animate:
            say(prefix + text + after)
            return
        self._write(prefix)
        for ch in text:
            self._write(ch)
            time.sleep(seconds / max(len(text), 1))
        if after:
            time.sleep(0.25)
        self._write(after + "\n")

    def build(self, prefix: str, pieces: list[str], say, after: str = "", step: float = 0.12) -> None:
        if not self.animate:
            say(prefix + "".join(pieces) + after)
            return
        self._write(prefix)
        for piece in pieces:
            self._write(piece)
            time.sleep(step)
        self._write(after + "\n")

    @contextmanager
    def spinner(self, text: str, say, indent: str = "  "):
        if not self.animate:
            say(f"{indent}{text}…")
            yield
            return
        stop = threading.Event()

        def spin():
            for frame in itertools.cycle(SPINNER):
                self._write(f"\r{indent}{self.yellow(frame)} {text}…")
                if stop.wait(0.08):
                    break
            self._write("\r\033[2K")

        thread = threading.Thread(target=spin, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join()

    @contextmanager
    def no_cursor(self):
        if self.animate:
            self._write("\033[?25l")
        try:
            yield
        finally:
            if self.animate:
                self._write("\033[?25h")


def welcome(st: Style) -> list[str]:
    steps = (
        ("🚀", "lokra demo", "watch it handle two AI agents, in about a minute"),
        ("🔧", 'lokra init "postgresql://…"', "write a starter policy for your database"),
        ("📱", "lokra cloud", "approve writes from your phone at cloud.lokra.dev"),
        ("📖", "lokra --help", "every command"),
    )
    width = max(len(cmd) for _, cmd, _ in steps)
    return [
        *st.banner(),
        "  Lokra sits between your AI agents and your database. Each agent sees only its rows,",
        "  sensitive values come back masked, and writes wait for a person.",
        "",
        *(f"  {icon}  {st.bold(cmd.ljust(width))}   {st.dim(what)}" for icon, cmd, what in steps),
        "",
        f"  Docs: {st.bold('https://lokra.dev')}",
        "",
    ]
