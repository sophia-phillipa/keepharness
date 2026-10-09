import random
import re
import string

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


SUBCOMMAND = re.compile(r"[a-z][a-z-]{0,19}")
PREFIXES = ("/", "./", "../", "~/")

REVIEWED_LEAKS = [
    ("deploy-hook 0123456789abcdef0123456789abcdef01234567", "0123456789abcdef"),
    ("aws-login wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalrXUtnFEMI"),
    ("sk-0123456789abcdef0123456789abcdef", "sk-0123456789abcdef"),
    ("notify eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOjF9.c2ln", "eyJhbGciOiJIUzI1NiJ9"),
    ("/opt/x/run ghp-ABCD1234efgh5678/more", "ghp-ABCD1234efgh5678"),
    ("/opt/ghp_ABCD1234efgh5678ijkl/run go", "ghp_ABCD1234efgh5678ijkl"),
]


@pytest.mark.parametrize(("command", "secret"), REVIEWED_LEAKS)
def test_reviewed_literal_leaks_are_masked(command, secret):
    assert secret not in mask_command(command)
    assert secret not in " ".join(mask_argv(command.split()))
    assert secret not in repr(safe_details({"command": command}))


def test_first_word_that_looks_like_a_token_is_masked():
    assert mask_command("sk-0123456789abcdef0123456789abcdef") == PLACEHOLDER
    assert mask_command("deploy-hook status") == "deploy-hook status"


def test_option_and_name_shapes_are_bounded():
    long_option = "--" + "a" * 32
    assert mask_command(f"cmd {long_option}") == f"cmd {PLACEHOLDER}"
    assert mask_command(f"cmd --{'a' * 32}=x") == f"cmd {PLACEHOLDER}"
    assert mask_named_values(["9lives=x", "a" * 3 + "=x"]) == [PLACEHOLDER, f"aaa={PLACEHOLDER}"]
    assert mask_named_values({"1bad key": V, "GOOD": V}) == {
        PLACEHOLDER: PLACEHOLDER,
        "GOOD": PLACEHOLDER,
    }


def test_random_argv_shows_only_executable_options_subcommands_and_prefixed_paths():
    rng = random.Random(61)
    alphabet = "abcXYZ019-_=:@/.\"' $\\;"
    for _ in range(2000):
        words = ["".join(rng.choices(alphabet, k=rng.randint(1, 12))) for _ in range(5)]
        shown = mask_argv(words)
        for word, out in zip(words[1:], shown[1:], strict=True):
            if word.startswith("-") or PLACEHOLDER in out:
                continue
            assert SUBCOMMAND.fullmatch(word) or word.startswith(PREFIXES), (words, shown)
        # a short option never carries its attached text; an equals sign never carries a value
        for word in words:
            if word.startswith("-") and len(word) > 2 and not word.startswith("--"):
                assert word not in shown, (words, shown)


def test_random_token_shaped_words_are_never_shown():
    rng = random.Random(49)
    hexa, b64 = "0123456789abcdef", string.ascii_letters + string.digits + "+/_-"
    for _ in range(1000):
        secret = "".join(rng.choices(rng.choice([hexa, b64]), k=rng.randint(20, 60)))
        for prefix in ("", "/opt/", "x/"):
            command = f"tool run {prefix}{secret}"
            if prefix == "/opt/" and "/" not in secret:
                continue  # a prefixed path made of one short opaque segment is checked below
            assert secret not in mask_command(command), command
    assert "ghp_ABCD1234efgh5678ijkl" not in mask_command("run /a/ghp_ABCD1234efgh5678ijkl")


UNKNOWN_FIELD_LEAKS = [
    {"body": '{"k": "hunter2-secret"}'},
    {"data": "--data-raw pw=hunter2"},
    {"extra": ["-p", "hunter2"]},
    {"options": {"argv2": ["--pass", "hunter2"]}},
    {"cmdArgs": ["login", "hunter2-password-here"]},
    {"target": "https://example.com/hook?sig=SECRETSIG"},
    {"webhook": "https://u:p@h/x?token=abc"},
]


@pytest.mark.parametrize("fields", UNKNOWN_FIELD_LEAKS)
def test_unknown_hook_fields_are_replaced_not_scanned(fields):
    (key,) = fields
    details = safe_details({"type": "command", **fields})
    assert details == {"type": "command", key: PLACEHOLDER}


def test_known_harmless_fields_pass_unchanged():
    hook = {
        "type": "command",
        "timeout": 30,
        "async": False,
        "eventName": "preToolUse",
        "handlerType": "command",
        "trustStatus": "trusted",
        "timeoutSec": 600,
        "displayOrder": 0,
        "isManaged": False,
        "enabled": True,
        "pluginId": "audit@local",
        "sourcePath": "/fake/hooks.json",
        "allowedEnvVars": ["HOME", "PATH"],
    }
    assert safe_details(hook) == hook


def test_random_unknown_keys_never_expose_values():
    rng = random.Random(62)
    chars = string.ascii_letters + string.digits + "-_/:.@ '\""
    for _ in range(500):
        key = "".join(rng.choices(string.ascii_letters, k=rng.randint(3, 10))) + "X9"
        secret = "".join(rng.choices(chars, k=rng.randint(8, 40))).strip() or "s3cretvalue"
        for value in (secret, [secret, "-p", secret], {"nested": {"deep": [secret]}}, {secret: 1}):
            assert secret not in repr(safe_details({"command": "run", key: value})), (key, value)


@pytest.mark.parametrize("matcher", ["", "Bash", "Edit|Write", "mcp__memory__.*", "*", "startup"])
def test_plain_matchers_pass(matcher):
    assert safe_details({"matcher": matcher})["matcher"] == matcher


@pytest.mark.parametrize(
    "matcher",
    [
        "sk-0123456789abcdef0123456789abcdef",
        "Bash|0123456789abcdef0123456789abcdef",
        "Bash(curl --token hunter2)",
        "mcp__server1__tool",
        "a" * 41,
    ],
)
def test_secret_looking_matchers_are_masked(matcher):
    assert safe_details({"matcher": matcher})["matcher"] == PLACEHOLDER


def test_free_text_fields_are_truncated():
    details = safe_details(
        {"prompt": "x" * 5000, "statusMessage": "y" * 5000, "description": "z" * 5000}
    )
    assert all(len(text) <= 300 for text in details.values())
