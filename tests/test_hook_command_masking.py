import random

import pytest

from adapters.shared.command_mask import (
    PLACEHOLDER,
    mask_argv,
    mask_command,
    mask_named_values,
    mask_url,
)
from adapters.shared.orchestration_state import safe_details

V = "LEAKVALUE"

LEAKING_COMMANDS = [
    f"bash -c 'curl --token {V} https://x'",
    f'sh -c "export API_TOKEN={V}; run"',
    f"sh -c 'curl -H \"X-Api-Key: {V}\" x'",
    f'curl --header="Cookie: sid={V}" x',
    f"curl --header='X-Api-Key: {V}' x",
    f"""curl --data '{{"password":"{V}"}}'""",
    f"cmd $'--token' {V}",
    f'cmd --token \\\n{V} "',
    f"gpg --passphrase {V}",
    f"DB_PASS={V} cmd",
    f"curl -u alice:{V} x",
    f"mysql -p{V}",
    f"sshpass -p {V} ssh h",
    f"git clone https://{V}@github.com/o/r",
    f"redis://:{V}@host",
    f'curl -H "Cookie : sid={V}" x',
    f'cmd --label "token" --token {V}',
    f'cmd --token "first\\"{V}"',
    f"cmd --token 'unterminated {V}",
    f'cmd $"--token" {V}',
    f"cmd --token\\\n {V}",
    f"cmd -H Cookie: {V}",
]

LEAKING_ARGV = [
    ["cmd", "--token=", V],
    ["curl", "-H", "Cookie:", V],
    ["cmd", "--token", V],
    ["cmd", f"--token {V}"],
    ["cmd", "-x", V, "pos"],
]


@pytest.mark.parametrize("command", LEAKING_COMMANDS)
def test_command_strings_never_show_argument_values(command):
    assert V not in mask_command(command)


@pytest.mark.parametrize("argv", LEAKING_ARGV)
def test_argv_lists_never_show_argument_values(argv):
    assert V not in " ".join(mask_argv(argv))


@pytest.mark.parametrize("command", LEAKING_COMMANDS)
def test_hook_details_mask_command_in_every_shape(command):
    shapes = [command, [command], command.split()]
    for shape in shapes:
        details = safe_details({"command": shape, "args": shape})
        assert V not in repr(details)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git status", "git status"),
        ("/usr/bin/foo --verbose", "/usr/bin/foo --verbose"),
        ("./hooks/check.sh", "./hooks/check.sh"),
        (f"curl -u a:{V} x", f"curl -u {PLACEHOLDER} {PLACEHOLDER}"),
        (f"bash -c 'run {V}'", f"bash -c {PLACEHOLDER}"),
        (f"A=1 run --opt={V}", f"A={PLACEHOLDER} run --opt={PLACEHOLDER}"),
        (f"mysql -p{V}", f"mysql -p{PLACEHOLDER}"),
    ],
)
def test_masking_keeps_executable_and_option_names(command, expected):
    assert mask_command(command) == expected


def test_argv_keeps_names_and_masks_following_words():
    assert mask_argv(["/usr/bin/foo", "--verbose", "-x", "file"]) == [
        "/usr/bin/foo",
        "--verbose",
        "-x",
        PLACEHOLDER,
    ]


def test_env_and_header_maps_keep_keys_and_mask_values():
    details = safe_details(
        {
            "env": {"API_URL": V, "PATH": "/bin"},
            "headers": {"X-Trace": V},
            "environment": [f"A={V}", f"B: {V}", V],
        }
    )
    assert V not in repr(details) and "/bin" not in repr(details)
    assert set(details["env"]) == {"API_URL", "PATH"}
    assert details["environment"] == [f"A={PLACEHOLDER}", f"B:{PLACEHOLDER}", PLACEHOLDER]
    assert mask_named_values(V) == PLACEHOLDER


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com", "https://example.com"),
        ("https://example.com/hook/" + V, "https://example.com/…"),
        (f"https://user:{V}@example.com/x", PLACEHOLDER),
        (f"https://{V}@example.com", PLACEHOLDER),
        (f"https://12:{V}@example.com", PLACEHOLDER),
        (f"ftp://example.com/{V}", PLACEHOLDER),
    ],
)
def test_urls_keep_only_scheme_and_host(url, expected):
    assert mask_url(url) == expected
    assert V not in safe_details({"url": url})["url"]


def test_random_argv_never_shows_a_non_allowlisted_word():
    rng = random.Random(61)
    alphabet = "abcXYZ019-_=:@/.\"' $\\;"
    for _ in range(2000):
        words = ["".join(rng.choices(alphabet, k=rng.randint(1, 12))) for _ in range(5)]
        shown = mask_argv(words)
        allowed = {PLACEHOLDER}
        for word in words:
            plain = word.isascii() and all(c.isalnum() or c in "._/-~" for c in word)
            if plain and len(word) <= 40:
                allowed.add(word)
        for word in words:
            if word not in allowed and not word.startswith("-"):
                assert word not in shown, (words, shown)
        # option names may appear, but never the text after a short option or an equals sign
        for word in words:
            if word.startswith("-") and len(word) > 2 and not word.startswith("--"):
                assert word not in shown, (words, shown)
