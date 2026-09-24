"""Bounded collector for a child process' console output, used for post-mortem logging."""
import re
from collections import deque

_PROGRESS_LINE_RE = re.compile(r"^\s*(?:frame=|size=\s*\S|Encoding: task|\d+(?:\.\d+)?\s*%)")


class OutputCapture:
    """Collects a child process' console output for post-mortem logging.

    Progress/statistics lines (ffmpeg "frame=...", HandBrake "Encoding: task ...")
    are collapsed to the most recent one; every other line is kept. Memory stays
    bounded: the first `head_limit` lines are kept verbatim, afterwards only a
    rolling tail of `tail_limit` lines survives (with a marker for the gap).
    """

    def __init__(self, head_limit=4000, tail_limit=500):
        self.head, self.tail = [], deque(maxlen=tail_limit)
        self.head_limit, self.dropped, self.last_progress = head_limit, 0, ""

    def add(self, line):
        line = line.rstrip("\r\n")
        if not line.strip():
            return
        if _PROGRESS_LINE_RE.match(line):
            self.last_progress = line
            return
        if len(self.head) < self.head_limit:
            self.head.append(line)
        else:
            if len(self.tail) == self.tail.maxlen:
                self.dropped += 1
            self.tail.append(line)

    def add_text(self, text):
        for line in (text or "").splitlines():
            self.add(line)

    def render(self):
        out = list(self.head)
        if self.dropped:
            out.append(f"... [{self.dropped} lines omitted] ...")
        out.extend(self.tail)
        if self.last_progress:
            out.append(self.last_progress)
        return "\n".join(out)
