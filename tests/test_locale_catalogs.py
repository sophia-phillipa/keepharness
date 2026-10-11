"""Interface locale catalogs: index, key shape, placeholder parity, served assets and the t() runtime."""

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from harness_ui import ASSETS, PUBLIC, asset_response

LOCALES = ASSETS / "locales"
RUNTIME = ASSETS / "i18n.js"
KEY_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
PLACEHOLDER_RE = re.compile(r"\{\w+\}")
RUNTIME_CHECK = r"""
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const [runtime, root, stored, languages, admin] = process.argv.slice(1);
// "-" is the chat page (HarnessPrefs exists); otherwise the answer of GET /api/ui-language as {status, body}.
const api = admin === "-" ? undefined : JSON.parse(admin);
globalThis.apiRequests = 0;
class XMLHttpRequest {
  open(method, url) {
    this.url = url;
    this.file = path.join(root, url.replace("/assets/locales/", ""));
  }
  send() {
    if (this.url === "/api/ui-language") {
      globalThis.apiRequests += 1;
      this.status = api?.status ?? 404;
      this.responseText = api?.body ?? "";
      return;
    }
    try {
      this.status = 200;
      this.responseText = fs.readFileSync(this.file, "utf8");
    } catch {
      this.status = 404;
    }
  }
}
const element = (attrs, textContent = "") => ({
  attrs,
  textContent,
  getAttribute: (name) => attrs[name] ?? null,
  setAttribute: (name, value) => {
    attrs[name] = value;
  },
});
const nodes = [
  element({ "data-i18n": "common.app.name" }, "Old"),
  element({ "data-i18n": "common.missing.key" }, "English kept"),
  element({ "data-i18n-title": "common.app.name" }),
  element({ "data-i18n-aria-label": "common.app.name" }),
];
globalThis.XMLHttpRequest = XMLHttpRequest;
globalThis.window = globalThis;
if (!api) globalThis.HarnessPrefs = { get: (key, fallback = null) => (stored === "-" ? fallback : stored) };
Object.defineProperty(globalThis, "navigator", {
  value: { languages: languages === "-" ? [] : languages.split(",") },
  configurable: true,
});
globalThis.document = { documentElement: { lang: "" } };
vm.runInThisContext(fs.readFileSync(runtime, "utf8"));
const i18n = globalThis.HarnessI18n;
i18n.apply({ querySelectorAll: (selector) => nodes.filter((node) => selector.slice(1, -1) in node.attrs) });
console.log(
  JSON.stringify({
    tag: i18n.tag(),
    lang: document.documentElement.lang,
    app: i18n.t("common.app.name"),
    unknown: i18n.t("common.unknown.key"),
    one: i18n.t("common.run.count", { count: 1 }),
    other: i18n.t("common.run.count", { count: 3 }),
    applied: [nodes[0].textContent, nodes[1].textContent, nodes[2].attrs.title, nodes[3].attrs["aria-label"]],
    api: globalThis.apiRequests,
    source: [i18n.source(i18n.t("common.app.name")), i18n.source("Not in any catalog")],
  }),
);
"""


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def placeholders(value):
    """Placeholder names in a catalog value: a string, or a plural object whose forms are strings."""
    forms = value.values() if isinstance(value, dict) else [value]
    return {name for form in forms for name in PLACEHOLDER_RE.findall(form)}


COMPLETE = [
    locale for locale in load(LOCALES / "index.json")["locales"] if locale["complete"] is True
]


def test_index_lists_existing_valid_json_files():
    locales = load(LOCALES / "index.json")["locales"]
    assert any(locale["tag"] == "en" and locale["complete"] is True for locale in locales)
    for locale in locales:
        assert set(locale) == {"tag", "file", "name", "complete"}, locale
        assert (LOCALES / locale["file"]).is_file(), locale
        assert isinstance(load(LOCALES / locale["file"]), dict), locale


def test_english_keys_are_dotted_snake_case_and_values_are_text():
    english = load(LOCALES / "en.json")
    assert [key for key in english if not KEY_RE.match(key)] == []
    for key, value in english.items():
        forms = value.values() if isinstance(value, dict) else [value]
        assert all(isinstance(form, str) for form in forms), key


@pytest.mark.parametrize("locale", COMPLETE, ids=lambda locale: locale["tag"])
def test_complete_locale_has_english_keys_and_placeholders(locale):
    english = load(LOCALES / "en.json")
    catalog = load(LOCALES / locale["file"])
    assert set(catalog) == set(english)
    for key, value in english.items():
        assert isinstance(catalog[key], dict) == isinstance(value, dict), key
        assert placeholders(catalog[key]) == placeholders(value), key


def test_runtime_and_catalogs_are_served_assets():
    names = ["i18n.js", "locales/index.json", "locales/en.json"]
    names += [f"locales/{locale['file']}" for locale in load(LOCALES / "index.json")["locales"]]
    for name in names:
        assert name in PUBLIC, name
        assert (ASSETS / name).is_file(), name
        assert asset_response(f"/assets/{name}").status_code == 200, name


def admin_response(body, status=200):
    """The standalone admin page has no HarnessPrefs: GET /api/ui-language answers ``body``."""
    return json.dumps({"status": status, "body": body})


def run_runtime(root, stored="-", languages="en-US", admin="-"):
    if not shutil.which("node"):
        pytest.skip("Node.js unavailable")
    result = subprocess.run(
        ["node", "-e", RUNTIME_CHECK, str(RUNTIME), str(root), stored, languages, admin],
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_t_returns_english_text_key_for_unknown_and_plurals():
    result = run_runtime(LOCALES)
    assert result["tag"] == result["lang"] == "en"
    assert result["app"] == "KeepHarness"
    assert result["unknown"] == "common.unknown.key"
    assert (result["one"], result["other"]) == ("1 run", "3 runs")
    assert result["applied"] == ["KeepHarness", "English kept", "KeepHarness", "KeepHarness"]


def test_unknown_or_incomplete_language_stays_english():
    assert run_runtime(LOCALES, stored="pt-BR", languages="pt-BR")["tag"] == "en"


@pytest.fixture
def pt_root(tmp_path):
    """A catalog root with a complete pt-BR locale, to exercise the non-English path."""
    shutil.copy(LOCALES / "en.json", tmp_path / "en.json")
    index = {
        "locales": [
            {"tag": "en", "file": "en.json", "name": "English", "complete": True},
            {"tag": "pt-BR", "file": "pt-br.json", "name": "Test locale", "complete": True},
        ]
    }
    (tmp_path / "index.json").write_text(json.dumps(index), encoding="utf-8")
    catalog = {
        "common.app.name": "Test locale app",
        "common.run.count": {"one": "{count} job", "other": "{count} jobs"},
    }
    (tmp_path / "pt-br.json").write_text(json.dumps(catalog), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    ("stored", "languages", "tag"),
    [
        ("pt-br", "en-US", "pt-BR"),  # stored value, case-insensitive
        ("fr", "pt-BR", "pt-BR"),  # unknown stored value is read as missing
        ("-", "fr-FR,pt-PT", "pt-BR"),  # browser language, primary subtag
        ("-", "fr-FR", "en"),  # no complete match
    ],
)
def test_active_language_choice(pt_root, stored, languages, tag):
    result = run_runtime(pt_root, stored=stored, languages=languages)
    assert result["tag"] == result["lang"] == tag


def test_plural_and_fallback_in_active_locale(pt_root):
    result = run_runtime(pt_root, stored="pt-BR")
    assert result["app"] == "Test locale app"
    assert (result["one"], result["other"]) == ("1 job", "3 jobs")
    assert result["unknown"] == "common.unknown.key"
    assert result["source"] == ["KeepHarness", "Not in any catalog"]


def test_missing_catalog_file_stays_english(pt_root):
    (pt_root / "pt-br.json").unlink()
    result = run_runtime(pt_root, stored="pt-BR")
    assert result["tag"] == "en"
    assert result["app"] == "KeepHarness"


def test_admin_page_takes_the_saved_language_from_the_admin_api(pt_root):
    result = run_runtime(pt_root, admin=admin_response('{"language": "pt-BR"}'))
    assert (result["tag"], result["lang"], result["api"]) == ("pt-BR", "pt-BR", 1)
    assert result["app"] == "Test locale app"


def test_chat_page_never_asks_the_admin_api(pt_root):
    result = run_runtime(pt_root, stored="pt-BR")
    assert result["tag"] == "pt-BR"
    assert result["api"] == 0


@pytest.mark.parametrize(
    ("body", "status"),
    [('{"language": null}', 200), ("not json", 200), ('{"language": "pt-BR"}', 404)],
)
def test_admin_page_falls_back_to_the_browser_language(pt_root, body, status):
    result = run_runtime(pt_root, languages="pt-BR", admin=admin_response(body, status))
    assert (result["tag"], result["api"]) == ("pt-BR", 1)


NON_SOURCE = [locale for locale in load(LOCALES / "index.json")["locales"] if locale["tag"] != "en"]
# Matches `t("a.b")` and `HarnessI18n.t("a.b")`: the `\b` also matches after the dot.
USED_KEY_RE = re.compile(r"""\bt\(\s*["']([a-z0-9_]+(?:\.[a-z0-9_]+)+)["']""")


@pytest.mark.parametrize("locale", NON_SOURCE, ids=lambda locale: locale["tag"])
def test_non_source_locale_adds_no_keys_and_keeps_placeholders(locale):
    """Holds while a locale is incomplete: it may lack keys, but never adds one or changes placeholders."""
    english = load(LOCALES / "en.json")
    catalog = load(LOCALES / locale["file"])
    assert set(catalog) <= set(english)
    for key, value in catalog.items():
        assert placeholders(value) == placeholders(english[key]), key


def test_every_t_key_used_by_the_ui_exists_in_english():
    english = load(LOCALES / "en.json")
    used = {
        key
        for folder in (ASSETS, ASSETS.parents[1] / "agent_service", ASSETS.parents[1] / "control")
        for path in folder.glob("*.js")
        for key in USED_KEY_RE.findall(path.read_text(encoding="utf-8"))
    }
    assert "settings_search.status.found" in used  # the scan sees the pilot keys
    assert "run_console.toggle.summary" in used  # and the run console in agent_service/
    assert sorted(used - set(english)) == []


INDEX_HTML = Path(__file__).resolve().parents[1] / "agent_service" / "index.html"
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "track",
    "wbr",
}


class StaticI18nMarkup(HTMLParser):
    """Collects each data-i18n* attribute with the whitespace-normalized text of its element."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.found = []  # (attribute name, key, text)

    @staticmethod
    def marks(attrs):
        return [(name, value) for name, value in attrs if name.startswith("data-i18n")]

    def handle_starttag(self, tag, attrs):
        if tag in VOID_TAGS:
            self.found += [(name, value, "") for name, value in self.marks(attrs)]
            return
        self.stack.append({"tag": tag, "marks": self.marks(attrs), "parts": []})

    def handle_endtag(self, tag):
        if tag not in (entry["tag"] for entry in self.stack):
            return
        while True:
            entry = self.stack.pop()
            text = " ".join("".join(entry["parts"]).split())
            self.found += [(name, value, text) for name, value in entry["marks"]]
            if entry["tag"] == tag:
                return

    def handle_data(self, data):
        for entry in self.stack:
            entry["parts"].append(data)


def test_static_markup_keys_exist_and_wrapped_text_is_english():
    english = load(LOCALES / "en.json")
    parser = StaticI18nMarkup()
    parser.feed(INDEX_HTML.read_text(encoding="utf-8"))
    parser.close()
    assert parser.found, "index.html lost its data-i18n attributes"
    for name, key, text in parser.found:
        assert key in english, (name, key)
        if name == "data-i18n":
            assert text == " ".join(english[key].split()), (key, text)
