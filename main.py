#!/usr/bin/env python3

import argparse
import os
import sys
import time

from engine import DataError, Game, ROMAN, parse_command, suggest_command
from ui import C, UI

BASE = os.path.dirname(os.path.abspath(__file__))


def show_evidence(game, ui, ev, bonus):
    ui.evidence_header(ev)
    content = ev["content"]
    if ev["format"] == "text":
        for p in content["paragraphs"]:
            ui.say(p, voice="narrator", delay=0.01)
    elif ev["format"] == "transcript":
        for ln in content["lines"]:
            voice = game.speaker_voices.get(ln["who"], "narrator")
            ui.speak(ln["who"], ln["t"], ln["text"], voice=voice)
    else:
        ui.table(content["columns"], content["rows"])
    ui.rule("─")
    if ev.get("analyst_note"):
        ui.say("(Analyst note) " + ev["analyst_note"], voice="analyst", color="silver")
    if bonus:
        ui.alert("info", f"+{bonus} pts: " + game.msg("first_review", id=ev["id"]))


def cmd_list(game, ui, args):
    rows, colors = [], []
    for e in game.list_entries():
        if e["unlocked"]:
            rows.append([e["id"], e["title"], e["media_type"], "NEW" if e["new"] else "read"])
            colors.append(C.ACCENT if e["new"] else C.WHITE)
        else:
            rows.append([e["id"], "██████████", "??", "LOCKED: " + e["reason"]])
            colors.append(C.GREY)
    ui.table(["ID", "TITLE", "TYPE", "STATUS"], rows, colors)
    ui.say(game.msg("list_footer"), voice="system", delay=0.012)


def cmd_examine(game, ui, args):
    if not args:
        return ui.say(game.msg("usage_examine"), voice="system")
    status, ev, bonus, eid = game.examine(" ".join(args))
    if status == "missing":
        return ui.say(game.msg("no_such_evidence", id=eid), voice="system")
    if status == "locked":
        return ui.alert("warning", game.msg("locked_evidence", id=eid, reason=game.locked_reason(ev)))
    show_evidence(game, ui, ev, bonus)


def cmd_search(game, ui, args):
    if not args:
        return ui.say(game.msg("usage_search"), voice="system")
    res = game.search(" ".join(args))
    if res.error:
        return ui.say(game.msg("search_short"), voice="system")
    kw = res.keyword
    if res.hits or res.survey_hits:
        hits = [(e, ui.highlight(s, kw)) for e, s in res.hits]
        shits = [(q, ui.highlight(s, kw)) for q, s in res.survey_hits]
        ui.search_results(game.msg("search_header", kw=kw), hits, shits)
    elif not res.restricted:
        ui.say(game.msg("search_none", kw=kw), voice="system")
    for eid in res.unlocked:
        ui.alert("success", f"FILE DECRYPTED: {eid} - {game.evidence[eid]['title']} (+5 pts)")
        ui.say(game.msg("search_decrypted", id=eid), voice="narrator")
    if res.restricted:
        ids = ", ".join(f"{e} [{r}]" for e, r in res.restricted)
        ui.alert("warning", game.msg("search_restricted", n=len(res.restricted), ids=ids))


def run_epilogue(game, ui):
    ui.rule("═", C.GREEN)
    for item in game.story["epilogue"]:
        ui.say(game.format_text(item["text"]), voice=item.get("voice", "narrator"), color=item.get("color", "white"))
        ui.pause(0.15)
    ui.rule("─")
    ui.bars("Q6: " + game.survey_questions()[5]["text"], [(o["label"], o["pct"]) for o in game.survey_questions()[5]["options"]])
    for item in game.story["epilogue_survey"]:
        ui.say(game.format_text(item["text"]), voice=item.get("voice", "narrator"), color=item.get("color", "white"))
        ui.pause(0.15)
    ui.rule("═", C.GREEN)
    p = game.profile
    ui.table(["ANALYST", "SCORE", "RANK", "MISTAKES"], [[p["name"], str(p["score"]), game.rank(), str(p["mistakes"])]])


def cmd_submit(game, ui, args):
    if len(args) < 2:
        return ui.say(game.msg("usage_submit"), voice="system")
    res = game.submit(args[0], " ".join(args[1:]))
    colors = {"solved": "green", "support": "silver", "hint": "silver", "weak": "silver",
              "wrong": "red", "blocked": "silver", "repeat": "grey", "error": "red"}
    voice = "analyst" if res.status in ("solved", "support") else "system"
    ui.say(game.format_text(res.text), voice=voice, color=colors.get(res.status, "white"))
    if res.points > 0:
        ui.alert("success", f"DEDUCTION LOGGED +{res.points} pts")
    elif res.points < 0:
        ui.alert("error", f"{res.points} pts")
    if res.new_tier:
        ui.alert("success", game.msg("clearance_up", roman=ROMAN[res.new_tier], name=game.tier_name(res.new_tier)))
        for eid in res.unlocked:
            ui.alert("info", f"NEW FILE: {eid} - {game.evidence[eid]['title']}")
        ui.say("* The case file fills you with <r>DETERMINATION</>.", voice="narrator")
    if res.final:
        run_epilogue(game, ui)
        return "done"


def cmd_suspects(game, ui, args):
    rows = [[sid, s["name"], s["role"], s["bio"]] for sid, s in game.suspects.items()]
    ui.table(["ID", "NAME", "ROLE", "NOTES"], rows)


def cmd_survey(game, ui, args):
    qs = game.survey_questions()
    if not args:
        ui.line("<y>" + game.msg("survey_intro") + "</>")
        ui.table(["Q", "QUESTION"], [[str(i), q["text"]] for i, q in enumerate(qs, 1)], max_col=70)
        return ui.say(game.msg("survey_footer"), voice="system", delay=0.012)
    if args[0].lower() == "all":
        for i, q in enumerate(qs, 1):
            ui.bars(f"Q{i}: {q['text']}", [(o["label"], o["pct"]) for o in q["options"]])
        return
    try:
        q = qs[int(args[0]) - 1]
        if int(args[0]) < 1:
            raise IndexError
    except (ValueError, IndexError):
        return ui.say(game.msg("survey_bad", n=args[0]), voice="system")
    ui.bars(f"Q{args[0]}: {q['text']}", [(o["label"], o["pct"]) for o in q["options"]])


def cmd_status(game, ui, args):
    p = game.profile
    secs = int(time.time() - getattr(game, "started", time.time()))
    ui.table(["ANALYST", "CLEARANCE", "SCORE", "RANK", "FILES", "TIME"],
             [[p["name"], f"{ROMAN[game.clearance]} - {game.tier_name(game.clearance)}", str(p["score"]),
               game.rank(), f"{len(p['unlocked'])}/{len(game.evidence)}", f"{secs // 60}:{secs % 60:02d}"]])
    ui.table(["OBJECTIVE", "DONE"], [[t, "YES" if d else "no"] for t, d in game.objectives()],
             [C.GREEN if d else C.WHITE for _, d in game.objectives()])


def cmd_help(game, ui, args):
    ui.table(["COMMAND", "WHAT IT DOES"], game.story["help"], max_col=60)


def cmd_hint(game, ui, args):
    ui.say(game.hint(), voice="analyst", color="silver")


def cmd_speed(game, ui, args):
    if args and ui.set_speed(args[0].lower()):
        ui.say(game.msg("speed_set", mode=args[0].lower()), voice="system")
    else:
        ui.say(game.msg("speed_bad"), voice="system")


HANDLERS = {"list": cmd_list, "examine": cmd_examine, "search": cmd_search, "submit": cmd_submit,
            "suspects": cmd_suspects, "survey": cmd_survey, "status": cmd_status, "help": cmd_help,
            "hint": cmd_hint, "speed": cmd_speed}


def intro(game, ui, resume):
    meta = game.story["meta"]
    ui.banner(meta["title"], meta["subtitle"], meta["disclaimer"])
    ui.wait(game.msg("title_prompt"))
    if not resume:
        ui.clear()
        ui.say(game.msg("name_prompt"), voice="system")
        game.set_name(ui.ask(C.ACCENT + "  > " + C.RESET))
        ui.say(game.msg("name_ok", name=game.profile["name"]), voice="narrator")
        ui.pause(0.2)
    ui.clear()
    for item in game.story["intro"]:
        ui.say(game.format_text(item["text"]), voice=item.get("voice", "narrator"), color=item.get("color", "white"))
        ui.pause(0.15)
    ui.out("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="FORENSIC MYSTERY - terminal detective game")
    ap.add_argument("--mute", action="store_true", help="disable text sound effects")
    ap.add_argument("--fast", action="store_true", help="faster typewriter text (for rehearsal)")
    ap.add_argument("--continue", dest="resume", action="store_true", help="resume saved progress")
    ap.add_argument("--data", default=os.path.join(BASE, "data"), help="data directory")
    args = ap.parse_args(argv)
    try:
        game = Game(args.data, resume=args.resume)
    except DataError as exc:
        print(f"Could not load the case files:\n{exc}")
        return 1
    game.started = time.time()
    ui = UI(mute=args.mute, speed="fast" if args.fast else "normal")
    try:
        intro(game, ui, args.resume)
        while True:
            try:
                line = ui.prompt(game.profile["name"].lower())
            except KeyboardInterrupt:
                ui.out("\n")
                ui.say("(Use <y>quit</> to leave the case.)", voice="system")
                continue
            except EOFError:
                break
            cmd, cargs = parse_command(line)
            if not cmd:
                continue
            if cmd == "quit":
                break
            if cmd == "clear":
                ui.clear()
                continue
            if cmd == "unknown":
                ui.say(game.msg("unknown_command", cmd=cargs[0], suggestion=suggest_command(cargs[0])), voice="system")
                continue
            try:
                if HANDLERS[cmd](game, ui, cargs) == "done":
                    ui.wait("(Press ENTER to exit)")
                    break
            except Exception as exc:  # dont crash
                ui.alert("error", f"Something went wrong: {exc}")
    finally:
        game.save()
    ui.say(game.msg("goodbye", name=game.profile["name"]), voice="narrator")
    return 0


if __name__ == "__main__":
    sys.exit(main())
