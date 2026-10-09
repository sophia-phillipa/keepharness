"""Fail-closed masking of hook commands, environment maps, header maps and URLs (D-048).

Nothing is guessed to be secret. Only the executable, option names and plain
path-like or short literal words that come before any option survive; every
other argument value is replaced by a placeholder. Inputs are scanned once,
left to right.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

PLACEHOLDER = "‹value›"
_OPTION = re.compile(r"(--[A-Za-z0-9][A-Za-z0-9-]{0,39}|-[A-Za-z0-9])")
_ENV_PREFIX = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}=")
_NAMED = re.compile(r"([A-Za-z0-9_-]{1,64})\s*([=:])")
_LITERAL = re.compile(r"[A-Za-z0-9._~/-]{1,40}")
_PATH = re.compile(r"[A-Za-z0-9._~/-]{1,120}")
_URL = re.compile(r"(https?://[A-Za-z0-9.-]+(?::\d{1,5})?)(.*)", re.S)
_MAX_ITEMS = 100


@dataclass(frozen=True, slots=True)
class _Word:
    value: str
    head: str  # text before the first quote or escape
    quoted: bool


def _quoted_end(text: str, start: int, chars: list[str], *, escapes: bool) -> int:
    """Append the body of the quote opened before ``start``; unterminated bodies run to the end."""
    quote = text[start - 1]
    index = start
    while index < len(text):
        char = text[index]
        if char == quote:
            return index + 1
        if char == "\\" and escapes and index + 1 < len(text):
            index += 1
            char = text[index]
        chars.append(char)
        index += 1
    return index


def _read_word(text: str, index: int) -> tuple[int, _Word | None]:
    chars: list[str] = []
    head_len: int | None = None
    while index < len(text) and not text[index].isspace():
        char, following = text[index], text[index + 1 : index + 2]
        if char == "\\" and following in ("\n", "\r"):
            index += 3 if text[index + 1 : index + 3] == "\r\n" else 2
            continue
        if char == "\\" or char in "'\"" or (char == "$" and following in ("'", '"')):
            head_len = len(chars) if head_len is None else head_len
            if char == "\\":
                chars.extend(following)
                index += 2
                continue
            ansi = char == "$"
            index += 1 if ansi else 0
            opener = text[index]
            index = _quoted_end(text, index + 1, chars, escapes=opener == '"' or ansi)
            continue
        chars.append(char)
        index += 1
    if not chars and head_len is None:
        return index, None
    value = "".join(chars)
    return index, _Word(
        value, value if head_len is None else value[:head_len], head_len is not None
    )


def _shell_words(text: str) -> list[_Word]:
    words: list[_Word] = []
    index = 0
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        index, word = _read_word(text, index)
        if word is not None:
            words.append(word)
    return words


def _mask_option(word: _Word) -> str:
    match = _OPTION.match(word.head)
    if match is None:
        return "--" if word.value == "--" and not word.quoted else PLACEHOLDER
    name = match.group(1)
    tail = word.value[len(name) :]
    if name.startswith("--"):
        if tail.startswith("="):
            return name + "=" + PLACEHOLDER
        return name if not tail and not word.quoted else PLACEHOLDER
    return name if not tail and not word.quoted else name + PLACEHOLDER


def _is_literal(value: str) -> bool:
    return bool(_LITERAL.fullmatch(value) or ("/" in value and _PATH.fullmatch(value)))


def _mask_words(words: Iterable[_Word]) -> list[str]:
    out: list[str] = []
    masked = False  # once an option or a masked word is seen, every later value is masked
    for word in words:
        if word.head.startswith("-"):
            out.append(_mask_option(word))
            masked = True
        elif not masked and _ENV_PREFIX.match(word.head):
            out.append(word.head.split("=", 1)[0] + "=" + PLACEHOLDER)
        elif not masked and not word.quoted and _is_literal(word.value):
            out.append(word.value)
        else:
            out.append(PLACEHOLDER)
            masked = True
    return out


def mask_command(command: str) -> str:
    """Mask a shell command string."""
    return " ".join(_mask_words(_shell_words(str(command)[:16000])))


def mask_argv(argv: Iterable[object]) -> list[str]:
    """Mask an argument vector; each element is one word and is never split."""
    words = []
    for item in list(argv)[:_MAX_ITEMS]:
        text = item if isinstance(item, str) else PLACEHOLDER
        words.append(_Word(text, text, False))
    return _mask_words(words)


def mask_named_values(value: object) -> object:
    """Keep the keys of an env or header mapping and mask every value."""
    if isinstance(value, dict):
        return {str(key)[:200]: PLACEHOLDER for key in list(value)[:80]}
    if isinstance(value, list):
        result = []
        for item in value[:_MAX_ITEMS]:
            named = _NAMED.match(item) if isinstance(item, str) else None
            result.append(named.group(1) + named.group(2) + PLACEHOLDER if named else PLACEHOLDER)
        return result
    return PLACEHOLDER


def mask_url(url: str) -> str:
    """Keep scheme, host and port; hide userinfo, path and query."""
    match = _URL.fullmatch(str(url)[:2000])
    if match is None or match.group(2) and match.group(2)[0] not in "/?#":
        return PLACEHOLDER
    return match.group(1) + ("/…" if match.group(2) else "")
