import asyncio
import base64
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from test_workspaces import config

from agent_service import tools
from agent_service.app import APIError, Service


def test_utf8_any_extension_and_binary_denied(tmp_path):
    file = tmp_path / "source"
    file.write_text("fn main() {}")
    assert asyncio.run(tools.extract(file, "main.rs"))[0]["text"] == "fn main() {}"
    file.write_bytes(b"\xff\x00")
    with pytest.raises(tools.ToolError):
        asyncio.run(tools.extract(file, "file.bin"))


@pytest.mark.requires_media_sandbox
def test_images_preserve_binary_outside_prompt(tmp_path, monkeypatch):
    file = tmp_path / "source"
    file.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aJ1sAAAAASUVORK5CYII="
        )
    )
    assert asyncio.run(tools.extract(file, "image.png")) == [
        {"page": None, "text": "", "media_type": "image/png"}
    ]
    file.write_bytes(b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024))
    monkeypatch.setattr(tools, "MAX_ATTACHMENT_BYTES", 5 * 1024 * 1024)
    with pytest.raises(tools.ToolError, match="image_size_limit"):
        asyncio.run(tools.extract(file, "image.jpg"))


def test_office_text_and_entity_rejection(tmp_path):
    file = tmp_path / "source"
    with zipfile.ZipFile(file, "w") as z:
        z.writestr("word/document.xml", "<root><t>Evidence</t></root>")
    assert "Evidence" in asyncio.run(tools.extract(file, "notes.docx"))[0]["text"]
    with zipfile.ZipFile(file, "w") as z:
        z.writestr("word/document.xml", "<!DOCTYPE root><root/>")
    with pytest.raises(tools.ToolError, match="unsafe_document_xml"):
        asyncio.run(tools.extract(file, "notes.docx"))


def test_vision_requires_actual_local_runtime(tmp_path):
    cfg = config(tmp_path)
    cfg["services"]["local"]["mode"] = "native"
    service = Service(cfg)
    props = "control.remote_models.fetch_body"
    with patch(props, AsyncMock(return_value=b'{"modalities": {"vision": false}}')):
        with pytest.raises(APIError, match="local_vision_not_enabled"):
            asyncio.run(service.validate_images("local", "installed-model"))
    with patch(props, AsyncMock(return_value=b'{"modalities": {"vision": true}}')):
        asyncio.run(service.validate_images("local", "installed-model"))
    service.db.close()


def test_audio_uses_local_transcriber(tmp_path):
    file = tmp_path / "source"
    file.write_bytes(b"RIFFtestWAVE")
    with patch(
        "agent_service.tools.transcribe_audio",
        AsyncMock(return_value=[{"text": "speech", "page": None}]),
    ) as transcribe:
        assert asyncio.run(tools.extract(file, "recording.wav"))[0]["text"] == "speech"
        transcribe.assert_awaited_once_with(file)


def test_epub_follows_spine_and_ignores_scripts(tmp_path):
    file = tmp_path / "source"
    with zipfile.ZipFile(file, "w") as z:
        z.writestr(
            "META-INF/container.xml", '<container><rootfile full-path="book.opf"/></container>'
        )
        z.writestr(
            "book.opf",
            '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="a" href="a.xhtml"/><item id="b" href="b.xhtml"/></manifest><spine><itemref idref="b"/><itemref idref="a"/></spine></package>',
        )
        z.writestr("a.xhtml", "<p>Second</p><script>ignore()</script>")
        z.writestr("b.xhtml", "<p>First</p>")
    result = asyncio.run(tools.extract(file, "book.epub"))
    assert [p["text"] for p in result] == ["First", "Second"]


def test_xlsx_resolves_shared_strings(tmp_path):
    file = tmp_path / "source"
    with zipfile.ZipFile(file, "w") as z:
        z.writestr("xl/sharedStrings.xml", "<sst><si><t>Customer</t></si></sst>")
        z.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet><row><c r="A1" t="s"><v>0</v></c><c r="B1"><v>42</v></c></row></worksheet>',
        )
    result = asyncio.run(tools.extract(file, "sheet.xlsx"))
    assert "A1=Customer\tB1=42" in result[-1]["text"]


def test_large_csv_keeps_full_content_outside_prompt(tmp_path):
    import json

    cfg = config(tmp_path)
    cfg["services"]["local"]["mode"] = "native"
    cfg["local"] = {}
    service = Service(cfg)
    content = "id,value\n" + ("1,example\n" * 20000) + "2,FINAL-MARKER\n"
    service.db.execute(
        "INSERT INTO files(id,project,name,size,hash,pages,owner) VALUES(?,?,?,?,?,?,?)",
        (
            "csv",
            "p",
            "large.csv",
            len(content),
            "digest",
            json.dumps([{"page": None, "text": content}]),
            "a",
        ),
    )
    data = {
        "project_id": "p",
        "backend": "local",
        "model": "installed-model",
        "prompt": "Read last line",
        "file_ids": ["csv"],
    }
    service.db.execute(
        "INSERT INTO jobs(id,project,owner,state,created,payload) VALUES(?,?,?,?,?,?)",
        ("j", "p", "a", "running", 1, json.dumps(data)),
    )
    service.db.commit()
    row = dict(service.db.execute("SELECT * FROM jobs WHERE id='j'").fetchone())

    async def run(*args):
        assert "FINAL-MARKER" not in args[1]
        assert "excerpt" in args[1] and "full_text_path" in args[1]
        extracted = tmp_path / "sessions/j/local/attachments/csv.txt"
        assert extracted.read_text() == content
        assert str(extracted.resolve()) in args[1]
        return {"answer": "ok"}

    with patch("adapters.run_native", side_effect=run):
        asyncio.run(service.infer(row, data))
    service.db.close()


@pytest.mark.parametrize(
    "backend,mode,reason",
    [
        ("deepseek", "native", "model_images_unavailable"),
        ("local", "scoped", "local_vision_not_enabled"),
        ("codex", "native", "model_images_unavailable"),
        ("claude", "native", "model_images_unavailable"),
        ("gemini", "native", "model_images_unavailable"),
    ],
)
@pytest.mark.parametrize("historical", [False, True])
def test_unsupported_images_are_explained_without_losing_text(
    tmp_path, backend, mode, reason, historical
):
    import json

    cfg = config(tmp_path)
    cfg["services"][backend] = {"mode": mode, "permissions": {"upload": True}}
    cfg[backend] = {}
    service = Service(cfg)
    for fid, pages in [
        ("picture", [{"media_type": "image/png", "text": ""}]),
        ("text", [{"text": "READABLE EVIDENCE"}]),
    ]:
        service.db.execute(
            "INSERT INTO files(id,project,name,size,hash,pages,owner) VALUES(?,?,?,?,?,?,?)",
            (fid, "p", fid, 1, "hash", json.dumps(pages), "a"),
        )
    data = {
        "project_id": "p",
        "backend": backend,
        "execution_mode": mode,
        "model": "fixture",
        "prompt": "Analyze files",
        "file_ids": [] if historical else ["picture", "text"],
    }
    service.db.execute(
        "INSERT INTO jobs(id,project,owner,state,created,payload) VALUES(?,?,?,?,?,?)",
        ("j", "p", "a", "running", 1, json.dumps(data)),
    )
    row = dict(service.db.execute("SELECT * FROM jobs WHERE id='j'").fetchone())
    turns = (
        [({"_job_id": "previous", "_state": "completed", "file_ids": ["picture", "text"]}, {})]
        if historical
        else []
    )

    async def run(*args, **kwargs):
        assert not args[3].get("_images")
        assert "READABLE EVIDENCE" in args[1]
        assert "image/png" not in args[1]
        args[2]("answer_delta", {"text": "Model response"})
        return {"answer": "Model response"}

    with (
        patch.object(service, "validate_images", AsyncMock(side_effect=APIError(reason))),
        patch.object(service, "context_turns", return_value=turns),
        patch.object(service, "quota", AsyncMock(return_value=None)),
        patch("adapters.run_native", side_effect=run),
    ):
        result = asyncio.run(service.infer(row, data))
    assert "picture" in result["answer"] and "ignored" in result["answer"]
    assert result["answer"].endswith("Model response")
    streamed = "".join(
        json.loads(r[0])["text"]
        for r in service.db.execute(
            "SELECT data FROM events WHERE job='j' AND type='answer_delta' ORDER BY id"
        )
    )
    assert streamed == result["answer"]
    service.db.close()


@pytest.mark.parametrize("reason", [None, "image_capability_unavailable"])
def test_supported_images_pass_through_and_probe_failures_remain_errors(tmp_path, reason):
    import json

    cfg = config(tmp_path)
    cfg["services"]["local"]["mode"] = "native"
    cfg["local"] = {}
    service = Service(cfg)
    service.db.execute(
        "INSERT INTO files(id,project,name,size,hash,pages,owner) VALUES(?,?,?,?,?,?,?)",
        (
            "pic",
            "p",
            "photo.png",
            1,
            "hash",
            json.dumps([{"media_type": "image/png", "text": ""}]),
            "a",
        ),
    )
    data = {
        "project_id": "p",
        "backend": "local",
        "model": "installed-model",
        "prompt": "Describe",
        "file_ids": ["pic"],
    }
    service.db.execute(
        "INSERT INTO jobs(id,project,owner,state,created,payload) VALUES(?,?,?,?,?,?)",
        ("j", "p", "a", "running", 1, json.dumps(data)),
    )
    row = dict(service.db.execute("SELECT * FROM jobs WHERE id='j'").fetchone())
    run = AsyncMock(return_value={"answer": "Image description"})
    with (
        patch.object(
            service, "validate_images", AsyncMock(side_effect=APIError(reason) if reason else None)
        ),
        patch("adapters.run_native", run),
    ):
        if reason:
            with pytest.raises(APIError, match=reason):
                asyncio.run(service.infer(row, data))
            run.assert_not_awaited()
        else:
            assert asyncio.run(service.infer(row, data))["answer"] == "Image description"
            assert run.call_args.args[3]["_images"][0]["media_type"] == "image/png"
    service.db.close()


WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def word_part(body):
    return f'<w:document xmlns:w="{WORD}"><w:body>{body}</w:body></w:document>'


def test_docx_runs_are_joined_and_paragraphs_kept(tmp_path):
    file = tmp_path / "source"
    with zipfile.ZipFile(file, "w") as z:
        z.writestr(
            "word/document.xml",
            word_part(
                "<w:p><w:r><w:t>Contr</w:t></w:r><w:r><w:t>act value: R$ 1</w:t></w:r>"
                "<w:r><w:t>.250,00</w:t></w:r></w:p>"
                "<w:p><w:r><w:t>Second paragraph.</w:t></w:r></w:p>"
                "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell A</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
            ),
        )
        z.writestr("word/header1.xml", word_part("<w:p><w:r><w:t>Header text</w:t></w:r></w:p>"))
        z.writestr(
            "word/footnotes.xml", word_part("<w:p><w:r><w:t>Footnote text</w:t></w:r></w:p>")
        )
    text = "\n".join(page["text"] for page in asyncio.run(tools.extract(file, "contract.docx")))
    assert "Contract value: R$ 1.250,00\nSecond paragraph.\nCell A\n" in text
    assert "Header text" in text and "Footnote text" in text


def test_xlsx_shared_strings_are_not_sent_as_their_own_page(tmp_path):
    file = tmp_path / "source"
    with zipfile.ZipFile(file, "w") as z:
        z.writestr("xl/sharedStrings.xml", "<sst><si><t>Customer</t></si></sst>")
        z.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet><row><c r="A1" t="s"><v>0</v></c></row></worksheet>',
        )
    pages = asyncio.run(tools.extract(file, "sheet.xlsx"))
    assert [page["text"].split("\n")[0] for page in pages] == ["xl/worksheets/sheet1.xml"]
    assert "A1=Customer" in pages[0]["text"]


CSV = "cliente;cidade;valor\nJoão;São Paulo;1.250,00\n"


@pytest.mark.parametrize(
    "raw",
    [
        CSV.encode("cp1252"),
        CSV.encode("utf-8-sig"),
        CSV.encode("utf-16"),
        b"\xfe\xff" + CSV.encode("utf-16-be"),
    ],
    ids=["cp1252", "utf8-bom", "utf16-le-bom", "utf16-be-bom"],
)
def test_text_in_common_encodings_is_read(tmp_path, raw):
    file = tmp_path / "source"
    file.write_bytes(raw)
    assert asyncio.run(tools.extract(file, "export.csv"))[0]["text"] == CSV


@pytest.mark.parametrize("raw", [b"\x89HEIC\x00\x01", b"abc\x01\x02\xe9", b"caf\xe9\x81"])
def test_non_text_bytes_are_still_refused(tmp_path, raw):
    file = tmp_path / "source"
    file.write_bytes(raw)
    with pytest.raises(tools.ToolError):
        asyncio.run(tools.extract(file, "photo.heic"))


def prepared_plan(tmp_path, files, turns, *, persisted, backend="local", **overrides):
    """The inference plan for a new turn over ``files`` (id -> pages) and earlier ``turns``."""
    import json

    cfg = config(tmp_path)
    cfg["services"][backend] = {"mode": "native", "permissions": {"upload": True}}
    cfg[backend] = {}
    service = Service(cfg)
    for fid, pages in files.items():
        service.db.execute(
            "INSERT INTO files(id,project,name,size,hash,pages,owner) VALUES(?,?,?,?,?,?,?)",
            (fid, "p", fid + ".txt", 1, "hash", json.dumps(pages), "a"),
        )
    data = {
        "project_id": "p",
        "backend": backend,
        "model": "fixture",
        "prompt": "Continue",
        "file_ids": [],
        **overrides,
    }
    service.db.execute(
        "INSERT INTO jobs(id,project,owner,state,created,payload) VALUES(?,?,?,?,?,?)",
        ("j", "p", "a", "running", 1, json.dumps(data)),
    )
    row = dict(service.db.execute("SELECT * FROM jobs WHERE id='j'").fetchone())
    pending = ([], True) if persisted else (turns, False)
    with (
        patch.object(service, "context_turns", return_value=turns),
        patch.object(service, "validate_images", AsyncMock(side_effect=APIError(IMAGES_OFF))),
        patch("agent_service.conversation_context.pending_turns", return_value=pending),
    ):
        try:
            return asyncio.run(service._prepare_inference(row, data))
        finally:
            service.db.close()


IMAGES_OFF = "model_images_unavailable"
LONG = [{"page": None, "text": "x" * 7000}]


def test_turn_that_adds_nothing_runs_on_the_session_holding_the_documents(tmp_path):
    files = {f"doc{i}": LONG for i in range(20)}
    turns = [
        ({"_job_id": f"t{i}", "_state": "completed", "file_ids": [fid]}, {})
        for i, fid in enumerate(files)
    ]
    plan = prepared_plan(tmp_path, files, turns, persisted=True)
    assert plan.context == "[]"


def test_documents_a_fresh_session_needs_are_still_bounded(tmp_path):
    files = {f"doc{i}": LONG for i in range(20)}
    turns = [
        ({"_job_id": f"t{i}", "_state": "completed", "file_ids": [fid]}, {})
        for i, fid in enumerate(files)
    ]
    with pytest.raises(APIError, match="source_context_limit"):
        prepared_plan(tmp_path, files, turns, persisted=False)


PICTURE = [{"media_type": "image/png", "text": ""}]


def earlier_turn(**fields):
    return ({"_job_id": "before", "_state": "completed", **fields}, {})


def test_image_notice_appears_on_the_turn_that_attaches_the_image(tmp_path):
    plan = prepared_plan(
        tmp_path, {"pic": PICTURE}, [], persisted=False, file_ids=["pic"], backend="deepseek"
    )
    assert "pic.txt" in plan.attachment_notice and "ignored" in plan.attachment_notice


def test_image_notice_is_not_repeated_on_later_turns_of_the_same_route(tmp_path):
    route = {"backend": "deepseek", "model": "fixture"}
    turns = [earlier_turn(file_ids=["pic"], **route)]
    plan = prepared_plan(tmp_path, {"pic": PICTURE}, turns, persisted=False, backend="deepseek")
    assert plan.attachment_notice == ""
    assert "SYSTEM NOTICE" not in plan.prompt


def test_image_notice_appears_once_when_the_route_changes_to_one_without_vision(tmp_path):
    turns = [earlier_turn(file_ids=["pic"], backend="codex", model="vision")]
    plan = prepared_plan(tmp_path, {"pic": PICTURE}, turns, persisted=False, backend="deepseek")
    assert "pic.txt" in plan.attachment_notice
