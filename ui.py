import atexit
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import textwrap
import time
import wave

WIDTH = 78

class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GREY = "\033[90m"


TAGS = {"y": C.YELLOW, "r": C.RED, "g": C.GREEN, "c": C.CYAN, "d": C.GREY, "b": C.BOLD, "/": C.RESET}
TAG_RE = re.compile(r"<(y|r|g|c|d|b|/)>")
COLOR_NAMES = {"red": C.RED, "green": C.GREEN, "yellow": C.YELLOW, "cyan": C.CYAN, "white": C.WHITE, "grey": C.GREY, "": C.WHITE}
SPEEDS = {"normal": 1.0, "fast": 0.35, "instant": 0.0}

#pitch
VOICES = {"narrator": 480, "system": 900, "analyst": 620, "mara": 760,
          "nova": 560, "tessa": 680, "rex": 300}


def enable_ansi():
    if os.name == "nt":
        os.system("")


def strip_tags(s):
    return TAG_RE.sub("", s)


def render_tags(s):
    return TAG_RE.sub(lambda m: TAGS[m.group(1)], s) + C.RESET


def wrap_tagged(text, width):
    lines = []
    for para in text.split("\n"):
        cur, curlen = "", 0
        for w in para.split(" "):
            wl = len(strip_tags(w))
            if cur and curlen + 1 + wl > width:
                lines.append(cur)
                cur, curlen = w, wl
            else:
                cur = f"{cur} {w}" if cur else w
                curlen += wl + (1 if curlen else 0)
        lines.append(cur)
    return lines


class Sound:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.backend = None
        self.files = {}
        self.sounds = {}
        self.cmd = []
        self._last = 0.0
        if enabled:
            try:
                self._init()
            except Exception:
                self.backend = None

    def _make_wav(self, path, freq, ms=48):
        rate = 22050
        n = int(rate * ms / 1000)
        frames = bytearray()
        for i in range(n):
            v = 1 if math.sin(2 * math.pi * freq * i / rate) >= 0 else -1  # square wave
            frames += struct.pack("<h", int(v * (1 - i / n) * 8000))
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(frames))

    def _init(self):
        tmp = tempfile.mkdtemp(prefix="forensic_snd_")
        atexit.register(shutil.rmtree, tmp, True)
        for name, freq in VOICES.items():
            p = os.path.join(tmp, name + ".wav")
            self._make_wav(p, freq)
            self.files[name] = p
        try:
            os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
            import pygamesubtitle
            pygame.mixer.init(frequency=22050, size=-16, channels=1, buffer=256)
            self.sounds = {k: pygame.mixer.Sound(v) for k, v in self.files.items()}
            self.backend = "pygame"
            return
        except Exception:
            pass
        if os.name == "nt":
            import winsound
            self.winsound = winsound
            self.backend = "winsound"
            return
        for tool, args in (("afplay", []), ("paplay", []), ("aplay", ["-q"]), ("play", ["-q"])):
            if shutil.which(tool):
                self.cmd = [tool] + args
                self.backend = "external"
                return

    def play(self, voice):
        if not self.enabled or not self.backend:
            return
        voice = voice if voice in self.files else "narrator"
        try:
            if self.backend == "pygame":
                self.sounds[voice].play()
            elif self.backend == "winsound":
                self.winsound.PlaySound(self.files[voice], self.winsound.SND_FILENAME | self.winsound.SND_ASYNC)
            else:
                now = time.time()
                if now - self._last < 0.05:
                    return
                self._last = now
                subprocess.Popen(self.cmd + [self.files[voice]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            self.enabled = False


class UI:
    BASE_DELAY = 0.03

    def __init__(self, mute=False, speed="normal"):
        enable_ansi()
        self.sound = Sound(enabled=not mute)
        self.speed = SPEEDS.get(speed, 1.0)
        self._skip = False

    # ---------------------------------------------------------- primitives
    def out(self, s):
        sys.stdout.write(s)
        sys.stdout.flush()

    def line(self, text="", color=""):
        self.out(color + render_tags(text) + "\n")

    def set_speed(self, mode):
        if mode in SPEEDS:
            self.speed = SPEEDS[mode]
            return True
        return False

    def clear(self):
        self.out("\033[2J\033[H")

    def rule(self, char="─", color=C.GREY):
        self.out(color + char * WIDTH + C.RESET + "\n")

    def wait(self, prompt="(Press ENTER)"):
        try:
            input("\n" + C.RED + "  " + prompt + C.RESET)
        except EOFError:
            pass

    def ask(self, prompt):
        return input(prompt)

    def pause(self, seconds):
        if self.speed > 0:
            try:
                time.sleep(seconds * max(self.speed, 0.3))
            except KeyboardInterrupt:
                pass

    # ----------------------------------------------------------- typewriter
    def _pause_for(self, ch, nxt):
        if nxt not in (" ", "\n", ""):
            return 0.0
        if ch in ".!?":
            return 0.22
        if ch == ",":
            return 0.09
        return 0.0

    def _emit(self, line, voice, delay, base):
        self.out(base)
        parts = TAG_RE.split(line)
        for i, part in enumerate(parts):
            if i % 2 == 1:
                self.out(C.RESET + base if part == "/" else TAGS[part])
                continue
            for j, ch in enumerate(part):
                self.out(ch)
                if self._skip or delay <= 0:
                    continue
                if ch != " ":
                    self.sound.play(voice)
                nxt = part[j + 1] if j + 1 < len(part) else ""
                try:
                    time.sleep(delay + self._pause_for(ch, nxt) * self.speed)
                except KeyboardInterrupt:
                    self._skip = True
        self.out(C.RESET + "\n")

    def say(self, text, voice="narrator", color="white", star=True, delay=None):
        self._skip = False
        base = COLOR_NAMES.get(color, color) if color is not None else C.WHITE
        d = (self.BASE_DELAY if delay is None else delay) * self.speed
        if star and not text.startswith("*"):
            text = "* " + text
        indent = "  " if text.startswith("*") else ""
        lines = wrap_tagged(text, WIDTH)
        for k, ln in enumerate(lines):
            if k and indent:
                ln = indent + ln
            self._emit(ln, voice, d, base)

    def speak(self, who, stamp, text, voice="narrator", color=C.WHITE):
        prefix = f"[{stamp}] {who:<11} "
        self._skip = False
        d = 0.018 * self.speed
        lines = wrap_tagged(text, WIDTH - len(prefix))
        for k, ln in enumerate(lines):
            if k == 0:
                self.out(C.GREY + f"[{stamp}] " + C.YELLOW + f"{who:<11} " + C.RESET)
            else:
                self.out(" " * len(prefix))
            self._emit(ln, voice, d, color)

    # ---------------------------------------------------------- components
    def alert(self, kind, text):
        label, color = {
            "success": ("ACCESS GRANTED", C.GREEN),
            "warning": ("WARNING", C.YELLOW),
            "error": ("ERROR", C.RED),
            "info": ("INFO", C.CYAN),
        }.get(kind, ("SYSTEM", C.WHITE))
        self.out(f"{color}{C.BOLD}[ {label} ]{C.RESET} ")
        self._skip = False
        self._emit(text, "system", 0.012 * self.speed, color)

    def banner(self, title, subtitle, disclaimer):
        heart = [
            "   ____       ____   ",
            "  /    )     (    \  ",
            " /    (  ^_^  )    \ ",
            "|  {   \('v')/   }  |",
            "|   {   /   \   }   |",
            "|_)(   /\   /\   )(_|",
            "|)  (_ | \|/  |_)  (|",
            "'     \"''^^^^''\"    '"
        ]
        self.clear()
        self.out("\n")
        for row in heart:
            self.out(C.RED + row.center(WIDTH) + C.RESET + "\n")
            self.pause(0.05)
        spaced = " ".join(title)
        inner = WIDTH - 10
        self.out("\n" + C.WHITE + ("╔" + "═" * inner + "╗").center(WIDTH) + "\n")
        self.out(("║" + spaced.center(inner) + "║").center(WIDTH) + "\n")
        self.out(("╚" + "═" * inner + "╝").center(WIDTH) + C.RESET + "\n\n")
        self.out(C.RED + subtitle.center(WIDTH) + C.RESET + "\n\n")
        for ln in wrap_tagged(disclaimer, WIDTH - 12):
            self.out(C.WHITE+ ln.center(WIDTH) + C.RESET + "\n")
        self.out("\n")

    def table(self, headers, rows, colors=None, max_col=34):
        n = len(headers)
        widths = [min(max_col, max([len(str(headers[i]))] + [len(str(r[i])) for r in rows])) for i in range(n)]
        while sum(widths) + 3 * n + 1 > WIDTH and max(widths) > 8:
            widths[widths.index(max(widths))] -= 1
        g = C.GREY

        def border(l, m, r):
            return g + l + m.join("─" * (w + 2) for w in widths) + r + C.RESET + "\n"

        def render(cells, color):
            wrapped = [textwrap.wrap(str(c), widths[i]) or [""] for i, c in enumerate(cells)]
            for k in range(max(len(w) for w in wrapped)):
                segs = [(w[k] if k < len(w) else "").ljust(widths[i]) for i, w in enumerate(wrapped)]
                self.out(g + "│" + C.RESET + color + (g + "│" + C.RESET + color).join(f" {s} " for s in segs)
                         + C.RESET + g + "│" + C.RESET + "\n")

        self.out(border("┌", "┬", "┐"))
        render(headers, C.YELLOW + C.BOLD)
        self.out(border("├", "┼", "┤"))
        for idx, row in enumerate(rows):
            render(row, (colors[idx] if colors and colors[idx] else C.WHITE))
        self.out(border("└", "┴", "┘"))

    def bars(self, title, options, bar_width=22, label_w=44):
        self.line(f"<y>{title}</>")
        for label, pct in options:
            wrapped = textwrap.wrap(label, label_w) or [""]
            for extra in wrapped[:-1]:
                self.out(f"  {extra}\n")
            filled = int(round(bar_width * pct / 100))
            head = f"  {wrapped[-1]:<{label_w}} "
            for k in range(filled + 1):
                bar = "█" * k + "░" * (bar_width - k)
                self.out("\r" + head + C.CYAN + bar + C.RESET + f" {pct:g}%")
                if self.speed > 0:
                    time.sleep(0.012)
            self.out("\n")
        self.out("\n")

    def evidence_header(self, ev):
        self.rule("═", C.CYAN)
        self.line(f"<c>{ev['id']}</>  <y>{ev['title']}</>")
        self.line(f"<d>type: {ev['media_type']}  |  source: {ev.get('source', '?')}  |  recorded: {ev.get('recorded', '?')}</>")
        self.line("<d>tags: " + ", ".join(ev.get("tags", [])) + "</>")
        self.rule("─")

    @staticmethod
    def highlight(text, kw):
        return re.sub(re.escape(kw), lambda m: "<y>" + m.group(0) + "</>", text, flags=re.I)

    def search_results(self, header, hits, survey_hits):
        self.rule("═", C.CYAN)
        self.line(f"<y>{header}</>")
        self.rule("─")
        for eid, snip in hits:
            self.line(f"<c>{eid}</> │ {snip}")
        for qid, snip in survey_hits:
            self.line(f"<g>{qid}</> │ {snip}")
        self.rule("─")

    def prompt(self, name):
        return input(f"{C.GREEN}{name}{C.RESET}@{C.CYAN}forensic{C.RESET}:{C.YELLOW}~/case-0411{C.RESET}$ ")
