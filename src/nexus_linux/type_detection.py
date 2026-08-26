"""Smart content-type detection for Timeline cards. Purely a presentation
hint -- it never changes what's stored (ClipItem.kind stays exactly what
clipboard_monitor.py captured: text/url/image/file), it only tells the
delegate which layout to paint. Conservative on purpose: several checks
require an exact regex/parse match (color, email, JSON) before returning
anything other than "text", per the explicit "no clasifiques agresivamente
si la certeza es baja" requirement -- a false positive that mislabels a
normal sentence as code or a command is a worse user experience than
falling back to a plain text card."""

import json
import re

URL_RE = re.compile(r"^(https?://|www\.)\S+$", re.IGNORECASE)
YOUTUBE_RE = re.compile(r"(youtube\.com/(watch|shorts)|youtu\.be/)", re.IGNORECASE)
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_RE = re.compile(r"^\+?[\d\s().-]{7,20}$")
HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
FUNC_COLOR_RE = re.compile(r"^(rgb|rgba|hsl|hsla)\(\s*[\d.%,\s/]+\)$", re.IGNORECASE)
PATH_RE = re.compile(r"^(~/|\.{1,2}/|/)\S+$")

# Command/code heuristics require *multiple* corroborating signals (see
# detect()) rather than a single keyword hit -- "import" alone appears in
# plenty of ordinary sentences ("please import the data"). Split into two
# tiers for the same reason: words that are also ordinary English ("go",
# "make", "cat", "export") are common enough in prose that the prefix alone
# isn't a safe signal -- those require a second corroborating token (a CLI
# flag, a path, a pipe) before counting as a command.
COMMAND_PREFIXES_UNAMBIGUOUS = (
    "git ", "npm ", "npx ", "sudo ", "docker ", "curl ", "wget ", "python ",
    "python3 ", "pip ", "pip3 ", "ssh ", "chmod ", "chown ", "systemctl ",
    "journalctl ", "mkdir ", "cargo ", "kubectl ", "pacman ", "ls ", "cd ",
)
COMMAND_PREFIXES_AMBIGUOUS = ("cat ", "grep ", "rm ", "cp ", "mv ", "export ", "make ", "go ", "brew ", "apt ")
COMMAND_SECOND_SIGNAL_RE = re.compile(r"(\s-{1,2}\w|/|\||&&)")
CODE_KEYWORDS = (
    "def ", "class ", "function ", "import ", "const ", "let ", "var ",
    "public ", "private ", "return ", "#include", "package ", "func ", "fn ",
    "SELECT ", "async def", "=>",
)

# Ordered for the UI: roughly how visually distinctive each type's card is.
KNOWN_TYPES = (
    "image", "file", "youtube", "url", "color", "code", "json",
    "command", "path", "email", "phone", "text",
)


def detect(kind: str, text_content: str | None) -> str:
    """kind is the stored ClipItem.kind (text/url/image/file). Returns one
    of KNOWN_TYPES -- always a real value, never None, so callers never need
    a None-handling branch."""
    if kind in ("image", "file"):
        return kind

    text = (text_content or "").strip()
    if not text:
        return "text"

    single_line = "\n" not in text

    if kind == "url" or (single_line and URL_RE.match(text)):
        return "youtube" if YOUTUBE_RE.search(text) else "url"

    if single_line:
        if HEX_COLOR_RE.match(text) or FUNC_COLOR_RE.match(text):
            return "color"
        if EMAIL_RE.match(text):
            return "email"
        if PHONE_RE.match(text) and sum(c.isdigit() for c in text) >= 7:
            return "phone"
        if PATH_RE.match(text) and " " not in text:
            return "path"
        lowered = text.lower()
        if any(lowered.startswith(p) for p in COMMAND_PREFIXES_UNAMBIGUOUS):
            return "command"
        if any(lowered.startswith(p) for p in COMMAND_PREFIXES_AMBIGUOUS) and COMMAND_SECOND_SIGNAL_RE.search(text):
            return "command"

    if text[0] in "{[":
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            return "json"

    lines = text.splitlines()
    if len(lines) >= 2:
        keyword_hits = sum(1 for kw in CODE_KEYWORDS if kw in text)
        symbol_hits = text.count("{") + text.count("}") + text.count(";")
        indented_lines = sum(1 for l in lines if l.startswith(("    ", "\t")))
        if keyword_hits >= 1 or symbol_hits >= 3 or indented_lines >= 2:
            return "code"

    return "text"
