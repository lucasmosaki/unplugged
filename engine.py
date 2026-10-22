import copy
import difflib
import json
import os
import re
import shlex
import tempfile
from dataclasses import dataclass, field

COMMAND_ALIASES = {
    "list": "list", "ls": "list", "evidence": "list", "files": "list",
    "examine": "examine", "read": "examine", "open": "examine", "cat": "examine",
    "search": "search", "grep": "search", "find": "search",
    "submit": "submit", "accuse": "submit", "deduce": "submit",
    "suspects": "suspects", "survey": "survey", "hint": "hint",
    "help": "help", "?": "help", "status": "status", "stats": "status",
    "speed": "speed", "clear": "clear", "cls": "clear",
    "quit": "quit", "exit": "quit", "q": "quit",
}
ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V"}
SURVEY_TOKEN = re.compile(r"\{s:(\d+):(\d+)\}")
VALID_FORMATS = {"text", "transcript", "table"}
VALID_MODES = {"start", "tier", "search"}


class DataError(Exception):
    """if data missing or invalid"""


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise DataError(f"Missing data file: {path}")
    except json.JSONDecodeError as exc:
        raise DataError(f"Invalid JSON in {path}: {exc}")


def write_json(path, data):
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def normalize_evidence_id(text):
    """'3', '003', 'evid-3', 'EVID-003' -> 'EVID-003'. None if no number."""
    m = re.search(r"(\d+)\s*$", (text or "").strip())
    return f"EVID-{int(m.group(1)):03d}" if m else None


def parse_command(line):
    """Return (canonical_command, args). Unknown -> ('unknown', [raw_cmd, ...])."""
    line = (line or "").strip()
    if not line:
        return "", []
    try:
        parts = shlex.split(line)
    except ValueError:
        parts = line.split()
    cmd, args = parts[0].lower(), parts[1:]
    if cmd in COMMAND_ALIASES:
        return COMMAND_ALIASES[cmd], args
    return "unknown", [cmd] + args


def suggest_command(cmd):
    close = difflib.get_close_matches(cmd, COMMAND_ALIASES.keys(), n=1, cutoff=0.6)
    return f"Did you mean '{close[0]}'? " if close else ""


def make_snippet(text, kw, width=64):
    i = text.lower().find(kw.lower())
    if i < 0 or len(text) <= width:
        return text
    start = max(0, i - 20)
    end = min(len(text), start + width)
    return ("..." if start else "") + text[start:end] + ("..." if end < len(text) else "")


@dataclass
class SearchResult:
    keyword: str
    error: str = ""
    hits: list = field(default_factory=list)          # (evidence_id, snippet)
    survey_hits: list = field(default_factory=list)   # (question_id, snippet)
    restricted: list = field(default_factory=list)    # (evidence_id, reason)
    unlocked: list = field(default_factory=list)      # evidence ids cracked open


@dataclass
class SubmitResult:
    status: str                   # solved/support/hint/weak/wrong/blocked/repeat/error
    text: str = ""
    points: int = 0
    unlocked: list = field(default_factory=list)
    new_tier: int = 0
    final: bool = False


class Game:
    def __init__(self, data_dir, stats_path=None, resume=False):
        self.data_dir = data_dir
        self.stats_path = stats_path or os.path.join(data_dir, "stats.json")
        ev_data = read_json(os.path.join(data_dir, "evidence.json"))
        self.story = read_json(os.path.join(data_dir, "story.json"))
        self.survey = read_json(os.path.join(data_dir, "survey_data.json"))
        self.stats = read_json(self.stats_path)
        self.speaker_voices = ev_data.get("speaker_voices", {})
        self.evidence = {e["id"]: e for e in ev_data.get("evidence", [])}
        self.suspects = self.story.get("suspects", {})
        self.deductions = {d["id"]: d for d in self.story.get("deductions", [])}
        self.validate(ev_data)
        current = self.stats.get("current_profile")
        source = current if (resume and current) else self.stats["default_profile"]
        self.profile = copy.deepcopy(source)

    # ------------------------------------------------------------ validation
    def validate(self, ev_data=None):
        errors = []
        raw = (ev_data or {}).get("evidence", list(self.evidence.values()))
        ids = [e.get("id") for e in raw]
        if len(ids) != len(set(ids)):
            errors.append("Duplicate evidence IDs")
        for e in raw:
            eid = e.get("id", "?")
            for key in ("id", "title", "media_type", "format", "tier", "unlock", "content"):
                if key not in e:
                    errors.append(f"{eid}: missing '{key}'")
            if e.get("format") not in VALID_FORMATS:
                errors.append(f"{eid}: bad format")
            if e.get("unlock", {}).get("mode") not in VALID_MODES:
                errors.append(f"{eid}: bad unlock mode")
            if e.get("unlock", {}).get("mode") == "search" and not e["unlock"].get("keywords"):
                errors.append(f"{eid}: search unlock needs keywords")
        for sid, s in self.suspects.items():
            if "name" not in s:
                errors.append(f"suspect {sid}: missing name")
        for d in self.deductions.values():
            if d.get("suspect") not in self.suspects:
                errors.append(f"{d['id']}: unknown suspect")
            if d.get("evidence") not in self.evidence:
                errors.append(f"{d['id']}: unknown evidence")
            for r in d.get("requires", []):
                if r not in self.deductions:
                    errors.append(f"{d['id']}: unknown requirement {r}")
        if not any(d.get("final") for d in self.deductions.values()):
            errors.append("No final deduction defined")
        dp = self.stats.get("default_profile")
        if not dp:
            errors.append("stats.json: missing default_profile")
        else:
            for eid in dp.get("unlocked", []):
                if eid not in self.evidence:
                    errors.append(f"stats.json: unknown unlocked id {eid}")
            starts = {k for k, e in self.evidence.items() if e["unlock"]["mode"] == "start"}
            if not starts.issubset(set(dp.get("unlocked", []))):
                errors.append("stats.json: default unlocked must include all start files")
        for q in self.survey.get("questions", []):
            for o in q.get("options", []):
                if not 0 <= o.get("pct", -1) <= 100:
                    errors.append(f"survey {q.get('id')}: bad percentage")
        if errors:
            raise DataError("Data validation failed:\n - " + "\n - ".join(errors))

    # -------------------------------------------------------------- helpers
    def msg(self, key, **kw):
        template = self.story["messages"].get(key, key)
        try:
            return template.format(**kw)
        except (KeyError, IndexError):
            return template

    def format_text(self, text):
        text = text.replace("{name}", self.profile.get("name", "ANALYST"))

        def repl(m):
            return self.survey_value(int(m.group(1)), int(m.group(2)))
        return SURVEY_TOKEN.sub(repl, text)

    def survey_value(self, q, opt):
        try:
            pct = self.survey["questions"][q - 1]["options"][opt - 1]["pct"]
        except (IndexError, KeyError):
            return "?"
        return f"{pct:g}%"

    def set_name(self, name):
        self.profile["name"] = (name or "").strip().upper()[:12] or self.story["meta"].get("default_name", "ANALYST")
        self.save()

    def save(self):
        self.stats["current_profile"] = self.profile
        write_json(self.stats_path, self.stats)

    @property
    def clearance(self):
        return self.profile["clearance"]

    def is_unlocked(self, eid):
        return eid in self.profile["unlocked"]

    def _unlock(self, eid):
        if eid not in self.profile["unlocked"]:
            self.profile["unlocked"].append(eid)
            return True
        return False

    def locked_reason(self, ev):
        if ev["tier"] > self.clearance:
            return f"CLEARANCE {ROMAN.get(ev['tier'], ev['tier'])}"
        return "ENCRYPTED"

    def _apply_clearance(self, tier):
        self.profile["clearance"] = tier
        newly = []
        for eid, ev in self.evidence.items():
            if ev["unlock"]["mode"] == "tier" and ev["tier"] <= tier and self._unlock(eid):
                newly.append(eid)
        return newly

    def add_score(self, pts):
        self.profile["score"] = max(0, self.profile["score"] + pts)

    # -------------------------------------------------------------- listing
    def list_entries(self):
        out = []
        for eid, ev in self.evidence.items():
            unlocked = self.is_unlocked(eid)
            out.append({
                "id": eid, "title": ev["title"], "media_type": ev["media_type"],
                "unlocked": unlocked, "new": eid not in self.profile["examined"],
                "reason": "" if unlocked else self.locked_reason(ev),
            })
        return out

    def examine(self, text):
        """Return (status, evidence, bonus, eid). status: ok / missing / locked."""
        eid = normalize_evidence_id(text)
        if not eid or eid not in self.evidence:
            return "missing", None, 0, text
        ev = self.evidence[eid]
        if not self.is_unlocked(eid):
            return "locked", ev, 0, eid
        bonus = 0
        if eid not in self.profile["examined"]:
            self.profile["examined"].append(eid)
            bonus = 2
            self.add_score(bonus)
            self.save()
        return "ok", ev, bonus, eid

    # --------------------------------------------------------------- search
    @staticmethod
    def units(ev):
        yield ev["title"]
        c = ev["content"]
        if ev["format"] == "text":
            yield from c.get("paragraphs", [])
        elif ev["format"] == "transcript":
            for ln in c.get("lines", []):
                yield f"{ln['who']}: {ln['text']}"
        else:
            for row in c.get("rows", []):
                yield " | ".join(str(x) for x in row if str(x))

    def search(self, keyword):
        kw = (keyword or "").strip()
        res = SearchResult(keyword=kw)
        if len(kw) < 2:
            res.error = "short"
            return res
        low = kw.lower()
        for eid, ev in self.evidence.items():
            matches = [u for u in self.units(ev) if low in u.lower()]
            if not matches:
                continue
            if self.is_unlocked(eid):
                for u in matches[:2]:
                    res.hits.append((eid, make_snippet(u, kw)))
            else:
                res.restricted.append((eid, self.locked_reason(ev)))
        for i, q in enumerate(self.survey.get("questions", []), 1):
            blob = [q["text"]] + [o["label"] for o in q["options"]]
            found = [t for t in blob if low in t.lower()]
            if found:
                res.survey_hits.append((f"SURVEY Q{i}", make_snippet(found[0], kw)))
        self.profile["searches"] = self.profile.get("searches", 0) + 1
        if res.hits and len(low) >= 3:
            for eid, ev in self.evidence.items():
                if self.is_unlocked(eid) or ev["unlock"]["mode"] != "search":
                    continue
                if ev["tier"] > self.clearance:
                    continue
                for trig in ev["unlock"]["keywords"]:
                    t = trig.lower()
                    if t in low or low in t:
                        self._unlock(eid)
                        self.add_score(5)
                        res.unlocked.append(eid)
                        break
        res.restricted = [r for r in res.restricted if r[0] not in res.unlocked]
        self.save()
        return res

    # --------------------------------------------------------------- submit
    def resolve_suspect(self, text):
        t = (text or "").strip().lower()
        for sid, s in self.suspects.items():
            if t == sid.lower() or t in [a.lower() for a in s.get("aliases", [])]:
                return sid
        return None

    def suspect_names(self):
        return ", ".join(sorted(self.suspects))

    def submit(self, suspect_text, evidence_text):
        sid = self.resolve_suspect(suspect_text)
        if not sid:
            return SubmitResult("error", self.msg("unknown_suspect", name=suspect_text, names=self.suspect_names()))
        eid = normalize_evidence_id(evidence_text)
        if not eid or eid not in self.evidence:
            return SubmitResult("error", self.msg("no_such_evidence", id=evidence_text))
        if not self.is_unlocked(eid):
            return SubmitResult("error", self.msg("locked_evidence", id=eid, reason=self.locked_reason(self.evidence[eid])))
        d = next((x for x in self.deductions.values() if x["suspect"] == sid and x["evidence"] == eid), None)
        if d is None:
            lost = min(2, self.profile["score"])
            self.add_score(-2)
            self.profile["mistakes"] += 1
            self.save()
            return SubmitResult("wrong", self.msg("wrong_default", suspect=self.suspects[sid]["name"]), -lost)
        verdict = d["verdict"]
        if verdict in ("hint", "weak"):
            return SubmitResult(verdict, d["text"])
        if d["id"] in self.profile["completed"] or d["id"] in self.profile["supports"]:
            return SubmitResult("repeat", self.msg("already_logged"))
        if verdict == "support":
            self.profile["supports"].append(d["id"])
            self.add_score(d["points"])
            self.save()
            return SubmitResult("support", d["text"], d["points"])
        missing = [r for r in d.get("requires", []) if r not in self.profile["completed"]]
        if missing:
            return SubmitResult("blocked", d.get("blocked_text", "Not yet."))
        self.profile["completed"].append(d["id"])
        self.add_score(d["points"])
        res = SubmitResult("solved", d["text"], d["points"])
        tier = d.get("unlock_tier", 0)
        if tier > self.clearance:
            res.new_tier = tier
            res.unlocked = self._apply_clearance(tier)
        if d.get("final"):
            res.final = True
            self.profile["solved"] = True
        self.save()
        return res

    # --------------------------------------------------- survey / status etc
    def survey_questions(self):
        return self.survey.get("questions", [])

    def hint(self):
        for h in self.story.get("hints", []):
            if h["until"] in self.profile["completed"]:
                continue
            needs = h.get("needs")
            if needs and not self.is_unlocked(needs) and "text_needs" in h:
                return h["text_needs"]
            return h["text"]
        return self.msg("no_hint_left")

    def rank(self):
        title = self.story["ranks"][0]["title"]
        for r in self.story["ranks"]:
            if self.profile["score"] >= r["min"]:
                title = r["title"]
        return title

    def objectives(self):
        return [(o["text"], o["done_when"] in self.profile["completed"]) for o in self.story.get("objectives", [])]

    def tier_name(self, tier):
        return self.story["tiers"].get(str(tier), "")
