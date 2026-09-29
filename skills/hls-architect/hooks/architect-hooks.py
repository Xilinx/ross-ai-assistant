#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Hooks for the hls-architect skill. One file, two events.

  Stop              Claude wants to end its turn -> decide whether to push it
                    back into the workflow. This is the whole reason the file
                    exists; everything below "The problem" is about it.

  UserPromptSubmit  the user typed something -> close the Step 3b gate and give
                    the Stop hook its retry budget back. A backstop for the
                    gate-close command in SKILL.md Step 3b, which the model can
                    skip like any other instruction.

They share a file because they share state: the same .architect_state directory,
the same file names inside it, the same log. Split across two scripts those
constants were declared twice, and a rename applied to one copy and not the
other would silently stop the gate from ever closing -- the exact failure this
hook was written to prevent.

The split that matters is stdout, not events. The Stop branch prints a JSON
block decision; the UserPromptSubmit branch must print *nothing ever*, because
its stdout is injected verbatim into the model's context. Only on_stop() writes
to stdout, and only at its final line.

The problem
-----------
hls-architect's Step 3a runs a battery of hls* validators. Each validator's own
SKILL.md ends by stating a verdict, so once that verdict is printed the model
frequently treats the turn as complete and stops. Steps 3b-3e (architecture
review, perf_outcomes.md, performance pragmas, baseline snapshot, handoff to
/hls-optimize) then never run.

This reproduces ~100% of the time on Opus 4.5 and intermittently on 4.8. No
wording of the instructions fixes it reliably, because it depends on the model
choosing not to stop. This hook removes that choice.

How it works
------------
Stop hooks fire at the moment Claude has decided to end its turn. We check
facts rather than judgment. Step 2d registers the design it is working on by
writing .architect_state/active; Step 3d-2 removes that directory once the
workflow is complete. So a registered design with no baseline means the
workflow started and did not finish, and we block the stop to send Claude back.

The skill states which design is active, and this hook evaluates that one design
only -- never a sibling, never "any design in the tree". An earlier version
globbed the whole project and allowed the stop if *any* design looked finished;
since architect_baseline/ is durable, one completed design silently disabled the
hook for every other design forever.

Locating the design costs one walk up from cwd (cwd is always inside the design
tree, even when it has drifted into rearchitect/v1) and, failing that, one
shallow scan below it. There is deliberately no search for .git or .claude: a
user's new design is often a plain folder with neither, and requiring them meant
the hook did nothing in exactly the case that needed it most.

Not depending on the model
--------------------------
Registration is an instruction in SKILL.md, and instructions can be skipped --
which is the same silent failure this hook exists to prevent, one level up. So
neither marker is required:

  * If no design is registered, fall back to the nearest directory containing a
    populated rearchitect/, and register it here. Proximity to cwd is what makes
    this safe; it is the project-wide glob, not the rearchitect/ signal, that
    caused the cross-design leak.

  * If Step 3b did not open the gate, fall back to reading the gate question out
    of last_assistant_message. Getting this wrong is the expensive failure --
    it talks over a question the user is being asked -- so it gets two
    independent detectors rather than one.

Both are backstops. The markers remain the primary signal because they are
exact, and the gate marker additionally proves the pause was deliberate.

The one stop we must not block
------------------------------
Step 3b asks the user to approve the architecture and waits. That is a correct
stop, but on disk it is indistinguishable from the 3a early-stop above -- 3b
runs before 3d-2, so architect_baseline/ is legitimately absent either way.
Blocking it answered the approval question on the user's behalf, five times.

Step 3b therefore writes the awaiting_review marker before it asks and removes
it once the user replies; while that marker exists we stand aside, without
spending retry budget. With autoconfirm=true Step 3b does not wait and does not
write the marker, so that path is unaffected.

Crashes
-------
Nothing cleans up after a run that dies -- there is no SessionEnd hook, and a
killed session cannot run Step 3e's rm. The leftover active marker names a
session that no longer exists, and a marker naming someone else is exactly what
makes this hook stand aside, so without a way to tell "crashed" from "a sibling
session is using this", one crash would disable the hook for that design until a
human deleted the directory.

Step 2d therefore records the CLI's host and pid alongside the session id. When
the marker was written on this host and that pid is gone, the previous owner is
provably dead and we take the design over. Everything short of that proof --
no pid recorded, a different host, a pid we may not signal -- stays as it was
and stands aside, because wrongly declaring a *live* run dead would reset its
budget and push over its gate.

Retry budget
------------
The model often needs more than one push: it may resume, complete one more
step, and stop again. `stop_hook_active` in the payload is only a boolean --
it says "you already pushed at least once", not how many times -- so it cannot
express a budget of 5. We keep our own counter in the design's state directory
and allow up to MAX_PUSHES blocks before standing down.

MAX_PUSHES is deliberately below Claude Code's own backstop, which force-ends
the turn after 8 consecutive blocks. Staying under it means we surrender on our
own terms, with a final explanatory message, rather than being cut off.

Safety
------
Exiting 0 without output always allows the stop, so every unexpected condition
here -- unparseable payload, unwritable state dir, odd directory layout, a
SKILL.md too old to register anything -- fails open rather than trapping the
session.

Versioning
----------
This hook and SKILL.md are one unit: the skill writes the state this hook
reads. Ship them together. A mismatched pair fails open, i.e. reverts to the
unprotected behaviour, and hook.log records which design (if any) was found.

Tests: test_architect_hooks.py in this directory. No Vitis or model needed.
"""

import datetime
import json
import os
import socket
import sys
import tempfile
import traceback
from pathlib import Path

# How many times we will push the model to continue before giving up.
# Must stay below Claude Code's 8-consecutive-block force-stop.
MAX_PUSHES = 5

# Written by Step 2d, removed by Step 3d-2. Everything the hook needs about a
# design lives in this one directory so completion is a single rm -rf that
# cannot half-succeed and leave a stale marker behind.
STATE_DIR_NAME = ".architect_state"
ACTIVE = "active"            # session id on line 1, "<host> <cli pid>" on line 2
GATE = "awaiting_review"     # present only while Step 3b waits on the user
COUNT = "count"              # pushes spent so far

# Created by Step 3d-2 -- which is NOT the end of the workflow. Step 3e still
# has to invoke /hls-optimize. Treating this as "finished" left 3e unguarded,
# and the architect stopped there to ask permission to hand off instead of
# handing off, with the transcript reading "Step 3e ... READY".
#
# It therefore only means "done" for a design nobody registered, where it is the
# only evidence available and a finished run would otherwise be blocked forever.
# When the design IS registered, releasing the state at Step 3e is the sole done
# signal -- the explicit marker wins over the inferred one.
DONE_MARKER = "architect_baseline"

# Fallback design signal, used only when nothing is registered: Step 2d writes
# its output under rearchitect/, so a rearchitect/ holding C++ sources marks a
# design the architect got far enough into to be worth resuming. Nothing here
# assumes a file name or a layout below rearchitect/.
ARCH_DIR = "rearchitect"
SOURCE_SUFFIXES = {".cpp", ".hpp", ".h", ".cc", ".cxx", ".c"}

# Fallback gate signal, used only when Step 3b did not open the gate itself.
# Matched against last_assistant_message; taken verbatim from the question 3b
# puts to the user, so a match means the user is being asked to approve.
GATE_PHRASES = (
    "does this architecture match your expectations",   # Step 3b
    "architecture review —",                            # Step 3b
    "what is your throughput target",                   # Step 3e
)

# Directories that never contain a design and can be large.
PRUNE = {".git", "node_modules", ".venv", "__pycache__", "architect_baseline"}

# How far below cwd to look when cwd sits above the design rather than inside
# it. Deliberately shallow: this is a fallback, not a search.
SCAN_DEPTH = 3

# Diagnostics only -- never load-bearing. Design-independent on purpose: its
# most valuable line is the one saying no design was found at all, which by
# definition has no design directory to live in. Delete the file to reset.
LOG_FILE = Path(tempfile.gettempdir()) / "hls-architect-stop-hook" / "hook.log"


def log(message):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        with LOG_FILE.open("a") as handle:
            handle.write("%s pid=%d %s\n" % (stamp, os.getpid(), message))
    except OSError:
        pass  # logging must never break the hook


STEPS = (
    "Continue the hls-architect workflow from where it stopped:\n"
    "  3b   architecture review (auto-proceed if autoconfirm=true)\n"
    "  3c   write perf_outcomes.md\n"
    "  3d-1 apply performance pragmas (if a throughput target was given)\n"
    "  3d-2 save the baseline snapshot\n"
    "  3e   hand off to /hls-optimize\n"
)


def reason(attempt):
    text = (
        "hls-architect has not finished (continue attempt %d of %d). The design "
        "is still registered as in progress but architect_baseline/ does not "
        "exist, which means Steps 3b-3e never ran.\n"
        "\n"
        "%s"
        "\n"
        "If a Step 3a validation agent is still running, WAIT for its table "
        "before starting 3b -- do not record 'validation dispatched' in the "
        "review. If its results are already in hand, do not re-run it.\n"
        "\n"
        "If the workflow genuinely cannot proceed (for example Step 2e "
        "verification failed), say so explicitly and name the blocking step "
        "rather than stopping silently." % (attempt, MAX_PUSHES, STEPS)
    )
    if attempt == MAX_PUSHES:
        text += (
            "\n\nThis is the final automatic continuation. If you stop again "
            "the workflow ends unfinished, so either complete Steps 3b-3e now "
            "or state plainly what is blocking them."
        )
    return text


def is_design(path):
    """True if Step 2d registered `path` as the design being worked on."""
    try:
        return (path / STATE_DIR_NAME / ACTIVE).is_file()
    except OSError:
        return False  # unreadable dir (another user's, a dead mount) -> not ours


def looks_like_design(path):
    """True if `path` holds a rearchitect/ with C++ sources in it.

    Only consulted when nothing is registered. An empty rearchitect/ does not
    count -- the architect may have created the directory without writing to it
    yet, and resuming a workflow with no output helps nobody.
    """
    arch = path / ARCH_DIR
    try:
        if not arch.is_dir():
            return False
        for entry in arch.rglob("*"):
            if entry.suffix.lower() in SOURCE_SUFFIXES and entry.is_file():
                return True
    except OSError:
        pass
    return False


def scan_below(start, depth):
    """Breadth-first walk of `start`, at most `depth` levels down."""
    frontier = [start]
    for _ in range(depth):
        children = []
        for parent in frontier:
            try:
                entries = list(parent.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.name in PRUNE or not entry.is_dir():
                    continue
                children.append(entry)
                yield entry
        frontier = children


def nearest(cwd, predicate):
    """The closest directory to `cwd` satisfying `predicate`, or None.

    cwd is normally inside the design tree -- often deep inside it, since the
    model cd's into rearchitect/v1 -- so walking up finds the design directly
    and unambiguously. When several designs are in play at once (the
    /matlab-to-cpp chain does this), the one containing cwd is by definition
    the one being worked on.

    If cwd sits above the design instead, scan a few levels below it and take
    the most recently touched match.
    """
    try:
        home = Path.home().resolve()
    except OSError:
        home = None

    for candidate in [cwd] + list(cwd.parents):
        if candidate == home or candidate == candidate.parent:
            break  # $HOME or / is never itself a design
        if predicate(candidate):
            return candidate

    best, best_mtime = None, -1.0
    for candidate in scan_below(cwd, SCAN_DEPTH):
        if not predicate(candidate):
            continue
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if mtime > best_mtime:
            best, best_mtime = candidate, mtime
    return best


def locate_design(cwd, session_id):
    """The one design this run is about, or None.

    Three predicates, tried in order, first match wins:

      1. a registered design this session may act on
      2. any registered design
      3. an unregistered design that is clearly under way

    (3) is the backstop for a skipped Step 2d: a missed registration should cost
    the session its stale-marker protection, not its Stop hook.

    (1) exists because (2) alone loses to a sibling. With cwd above two designs
    the scan takes the most recently touched, so a design belonging to another
    session -- a parallel regression job in the same tree, a colleague on the
    same shared area -- could win the lookup, get rejected by the ownership
    check in on_stop(), and end the turn having never looked at *our* design.
    Our design then goes unguarded for as long as the sibling stays the newer of
    the two. Asking for one we own first makes the sibling irrelevant.

    Order matters between (1) and (2): if no design is ours, (2) must still
    return the foreign one so on_stop() can recognise it and stand aside. Were
    (1) to fall straight through to (3), a foreign design would match on its
    rearchitect/ directory and we would push into someone else's workflow.
    """
    def ours(path):
        return is_design(path) and owns(path, session_id)

    for predicate in (ours, is_design, looks_like_design):
        design = nearest(cwd, predicate)
        if design is not None:
            return design
    return None


def register(design):
    """Record `design` as in progress. True if state is usable afterwards."""
    state = design / STATE_DIR_NAME
    try:
        state.mkdir(exist_ok=True)
        if not (state / ACTIVE).is_file():
            (state / ACTIVE).write_text("")  # unknown session -> matches any
        return True
    except OSError:
        return False


def at_review_gate(design, payload):
    """True if Step 3b is waiting on the user."""
    if (design / STATE_DIR_NAME / GATE).exists():
        return "gate marker"
    message = str(payload.get("last_assistant_message") or "").lower()
    for phrase in GATE_PHRASES:
        if phrase in message:
            return "gate question in last message"
    return None


def marker_owner(design):
    """(session_id, host, pid) recorded in the active marker.

    Step 2d writes the session id on line 1 and "<host> <pid>" on line 2. Line 2
    is optional: markers written by register() below, and by older copies of
    SKILL.md, have only the first line. Missing or unparsable fields come back
    as None, which every caller reads as "cannot tell".
    """
    try:
        lines = (design / STATE_DIR_NAME / ACTIVE).read_text().splitlines()
    except OSError:
        return None, None, None

    session = lines[0].strip() if lines else ""
    host, pid = None, None
    if len(lines) > 1:
        fields = lines[1].split()
        if len(fields) == 2:
            host = fields[0]
            try:
                pid = int(fields[1])
            except ValueError:
                pid = None
    return session, host, pid


def is_dead(host, pid):
    """True only when we can prove the process that wrote the marker is gone.

    Every uncertain case answers False, because a false "dead" is the dangerous
    direction: it hands a live run's state to a second session, which would then
    reset its counter and push over its gate.

    The host check is not decoration. State lives on shared filesystems here, so
    a pid from another machine says nothing about any process on this one -- and
    on a busy regression farm that number is very likely in use locally by
    something unrelated. Unless the marker was written on this host, we do not
    look at the pid at all.
    """
    if pid is None or host is None or host != socket.gethostname():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False  # alive, just not ours to signal
    except OSError:
        return False
    return False


def owns(design, session_id):
    """True if `session_id` may act on this design.

    An empty marker means Step 2d could not resolve the session id. Rather than
    disabling the hook we accept it and lose only the session scoping.

    Pure by contract: no writes, no logging. locate_design() calls this as a
    predicate across every candidate it walks or scans, so a side effect here
    would touch designs this turn never acts on. Taking the marker over is
    adopt()'s job, and it runs once, on the one design that was selected.

    A marker naming a *different* session is a leftover from a run that died
    before Step 3e -- there is no SessionEnd hook to clean up after a crash. The
    danger of simply standing aside is that the leftover is indistinguishable
    from a live sibling session, so the hook would be silently disabled for this
    design until someone removed the directory by hand. When line 2 proves the
    writing process is gone, we take the design over instead.
    """
    owner, host, pid = marker_owner(design)
    if owner is None:
        return False
    if owner in ("", session_id):
        return True

    return is_dead(host, pid)


def adopt(design, session_id):
    """Take a dead run's marker over. True unless the rewrite failed.

    A no-op for a design already ours or unscoped, which is the overwhelmingly
    common case. Runs only after owns() said yes, so reaching the rewrite means
    is_dead() proved the previous owner is gone.

    A failed write means we would re-read the dead session's id on every stop, so
    it reports False and the caller stands aside as before.
    """
    owner, _, _ = marker_owner(design)
    if owner is None or owner in ("", session_id):
        return True
    log("  marker owned by a dead run -> taking over")
    try:
        (design / STATE_DIR_NAME / ACTIVE).write_text(identity(session_id))
    except OSError:
        return False
    # The dead run's spent budget is not this run's to inherit.
    try:
        (design / STATE_DIR_NAME / COUNT).unlink()
    except OSError:
        pass
    return True


def identity(session_id):
    """Marker contents for this session, in the two-line format Step 2d uses.

    The pid recorded is CLAUDE_PID, the CLI process -- never os.getpid(). A hook
    process exits within milliseconds, so writing our own pid would leave a
    marker that looks dead to the very next stop and invites a sibling session to
    steal a live design. When CLAUDE_PID is absent we write line 1 only, which
    reads back as "cannot tell" and keeps the old stand-aside behaviour.
    """
    cli_pid = os.environ.get("CLAUDE_PID", "").strip()
    if not cli_pid.isdigit():
        return "%s\n" % session_id
    return "%s\n%s %s\n" % (session_id, socket.gethostname(), cli_pid)


def bump(path):
    """Increment the push counter and return its new value.

    Returns MAX_PUSHES + 1 (i.e. "budget exhausted, stand down") if the counter
    cannot be persisted -- without durable state we would otherwise push on
    every single stop, forever.
    """
    try:
        count = int(path.read_text().strip()) if path.exists() else 0
    except (OSError, ValueError):
        count = 0
    count += 1
    try:
        path.write_text(str(count))
    except OSError:
        return MAX_PUSHES + 1
    return count


def on_stop(payload):
    """Claude wants to end its turn. Block it if the workflow is unfinished.

    The only function here that writes to stdout, and only on its last line.
    """
    session_id = str(payload.get("session_id") or "default")
    log("  stop_hook_active=%s" % payload.get("stop_hook_active"))

    try:
        cwd = Path(payload.get("cwd") or ".").resolve()
    except OSError:
        log("  ALLOW: cwd not resolvable")
        return

    design = locate_design(cwd, session_id)
    if design is None:
        log("  ALLOW: no registered design at or near %s" % cwd)
        return  # architect not running, or SKILL.md too old to register one
    log("  design=%s" % design)

    if is_design(design):
        if not owns(design, session_id):
            log("  ALLOW: registered to a live session elsewhere")
            return
        if not adopt(design, session_id):
            log("  ALLOW: cannot rewrite the dead owner's marker")
            return

    if not is_design(design) and (design / DONE_MARKER).is_dir():
        log("  ALLOW: unregistered design with %s/ -> assume finished"
            % DONE_MARKER)
        return

    # Step 3b is waiting on the user. This is a deliberate stop, not the failure
    # this hook exists to catch, so stand aside -- pushing here would talk over
    # the approval question and answer it on the user's behalf. Checked before
    # bump() so a pause at the gate costs no retry budget.
    gate = at_review_gate(design, payload)
    if gate:
        log("  ALLOW: paused at 3b awaiting user approval (%s)" % gate)
        return

    if not register(design):
        log("  ALLOW: cannot write %s/ -> no durable retry budget"
            % STATE_DIR_NAME)
        return

    attempt = bump(design / STATE_DIR_NAME / COUNT)
    if attempt > MAX_PUSHES:
        log("  ALLOW: retry budget of %d spent" % MAX_PUSHES)
        return

    log("  BLOCK: continue attempt %d of %d" % (attempt, MAX_PUSHES))
    json.dump({"decision": "block", "reason": reason(attempt)}, sys.stdout)


# --------------------------------------------------------- UserPromptSubmit
#
# Note there are two design lookups in this file, deliberately. locate_design()
# above may scan *below* cwd, because the Stop branch only reads state and a
# wrong guess there costs one wasted push. design_containing() below walks up
# only -- see its docstring.

def design_containing(cwd):
    """The registered design cwd is inside, or None.

    Walks up only -- it never looks into directories below cwd. Writing state
    is not the same as reading it: the Stop hook may scan downwards to find a
    design to guard, because guessing wrong there costs one wasted push. This
    hook *mutates* state, so a wrong guess would clear the gate on a design the
    user was not talking about, and the sibling would resume while its approval
    question sat unanswered. Only the design cwd is actually inside qualifies.

    The cost is that a prompt sent while cwd sits above the design does
    nothing. That fails in the safe direction: an uncleared gate means the Stop
    hook keeps standing aside, and Step 3b's own gate-close still runs.
    """
    def registered(path):
        try:
            return (path / STATE_DIR_NAME).is_dir()
        except OSError:
            return False

    for candidate in [cwd] + list(cwd.parents):
        if candidate == candidate.parent:
            break
        if registered(candidate):
            return candidate
    return None


def on_user_prompt_submit(payload):
    """The user replied, so whatever was being waited on has been answered."""
    try:
        cwd = Path(payload.get("cwd") or ".").resolve()
    except OSError:
        return

    design = design_containing(cwd)
    if design is None:
        return
    state = design / STATE_DIR_NAME

    try:
        (state / GATE).unlink()
        log("  UserPromptSubmit: closed gate on %s" % design)
    except OSError:
        pass  # no gate open is the normal case, not an error

    # A reply means the user is engaged, so the Stop hook gets its retry budget
    # back. Without this, a run that spent all five pushes stays unguarded for
    # the rest of the session even after the user unblocks it. Keyed on the
    # reply rather than on the gate: the stuck-budget case usually has no gate
    # open at all, which is exactly when the reset is needed most.
    #
    # This cannot become an infinite push loop. The budget only refills when a
    # human types something, so five pushes per human turn stays the ceiling.
    try:
        (state / COUNT).unlink()
        log("  UserPromptSubmit: reset retry budget on %s" % design)
    except OSError:
        pass



HANDLERS = {
    "Stop": on_stop,
    "UserPromptSubmit": on_user_prompt_submit,
}


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        log("FIRED unparseable payload (%d bytes) -> ignored" % len(raw))
        return

    event = payload.get("hook_event_name")
    log("FIRED %s cwd=%s" % (event, payload.get("cwd")))

    handler = HANDLERS.get(event)
    if handler is not None:
        handler(payload)


if __name__ == "__main__":
    # A hook that exits non-zero, or writes a traceback to stdout, is worse than
    # no hook at all: on Stop it surfaces as an error on a turn the user did
    # nothing wrong on, and on UserPromptSubmit a non-zero exit blocks the user's
    # prompt from reaching the model entirely. Whatever goes wrong in here, the
    # turn is allowed to proceed.
    try:
        main()
    except Exception:
        try:
            log("  ALLOW: unhandled error\n%s" % traceback.format_exc())
        except Exception:
            pass
    sys.exit(0)
