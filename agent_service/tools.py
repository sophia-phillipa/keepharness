"""Deterministic tools. Source content never grants permissions."""

import asyncio
import codecs
import errno
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import stat
import tempfile
import urllib.parse
from pathlib import Path, PurePosixPath

from control.product import PRODUCT

try:
    from .errors import ToolError
except ImportError:  # copied beside project_mcp.py into the scoped sandbox bridge
    from errors import ToolError

MAX_OUTPUT = 2 * 1024 * 1024
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm", ".aac", ".opus"}
MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024
MAX_AUDIO_BYTES = MAX_ATTACHMENT_BYTES
MAX_AUDIO_SECONDS = 14400


def video_tools_available():
    return all(shutil.which(tool) for tool in ("ffprobe", "ffmpeg", "bwrap", "prlimit"))


def transcription_available():
    runtime = Path(
        os.environ.get(
            PRODUCT.env_prefix + "_WHISPER_DIR", str(PRODUCT.state_path() / "whisper.cpp")
        )
    )
    return (runtime / "build/bin/whisper-cli").is_file() and (
        runtime / "models/ggml-base.bin"
    ).is_file()


async def process(argv, timeout=30, cwd=None):
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    output = bytearray()

    async def collect():
        while chunk := await proc.stdout.read(16384):
            output.extend(chunk)
            if len(output) > MAX_OUTPUT:
                raise ToolError("tool_output_limit")
        await proc.wait()

    completed = False
    try:
        async with asyncio.timeout(timeout):
            await collect()
            completed = True
    finally:
        if not completed:
            import signal

            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
    return proc.returncode, output.decode("utf-8", "replace")


def sandbox(directory, argv, writable=False):
    # Only /usr and the selected work directory exist; no home, credentials or network.
    # From /etc only the loader cache and the alternatives symlinks are exposed:
    # Debian/Ubuntu resolve some /usr libraries (libblas, liblapack) through the latter.
    return [
        "prlimit",
        "--as=2147483648",
        "--cpu=60",
        "--fsize=16777216",
        "--nofile=128",
        "--",
        "bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--setenv",
        "PATH",
        "/usr/bin",
        "--setenv",
        "HOME",
        "/tmp",
        "--ro-bind",
        "/usr",
        "/usr",
        *(
            arg
            for path in ("/etc/ld.so.cache", "/etc/alternatives")
            if Path(path).exists()
            for arg in ("--ro-bind", path, path)
        ),
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--symlink",
        "usr/bin",
        "/bin",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--bind" if writable else "--ro-bind",
        str(directory),
        "/work",
        "--chdir",
        "/work",
        "--",
        *argv,
    ]


def safe_file(root, name):
    root = Path(root).resolve()
    path = root / name
    if Path(name).is_absolute() or ".." in Path(name).parts:
        raise ToolError("invalid_path")
    current = root
    for part in Path(name).parts:
        current /= part
        if current.is_symlink():
            raise ToolError("symlink_denied")
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise ToolError("file_not_found")
    if path.stat().st_size > MAX_OUTPUT:
        raise ToolError("file_too_large")
    return path


def read_contained(root, name, errors="strict"):
    """The text of one regular file under ``root``, opened without following a link at any step.

    Each folder is opened relative to its parent's descriptor with O_NOFOLLOW, so a folder
    replaced by a link after the name checks cannot redirect the read outside ``root``.
    """
    parts = PurePosixPath(name).parts
    if not parts or PurePosixPath(name).is_absolute() or ".." in parts:
        raise ToolError("invalid_path")
    folder = os.open(Path(root).resolve(), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=folder)
            os.close(folder)
            folder = child
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
    except OSError as exc:
        raise ToolError(
            "symlink_denied" if exc.errno == errno.ELOOP else "file_not_found"
        ) from None
    finally:
        os.close(folder)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ToolError("file_not_found")
        data = stream.read(MAX_OUTPUT + 1)
    if info.st_size > MAX_OUTPUT or len(data) > MAX_OUTPUT:
        raise ToolError("file_too_large")
    return data.decode("utf-8", errors)


CONTROL_BYTES = re.compile(rb"[\x00-\x08\x0e-\x1f]")
WORD_PART = re.compile(r"word/(document|footnotes|endnotes|header\d*|footer\d*)\.xml")
WORD_BREAKS = {"tab": "\t", "br": "\n", "cr": "\n"}


def decode_text(data):
    """Text of a plain-text upload: a UTF-16 or UTF-8 BOM, UTF-8, then Windows-1252.

    Windows-1252 is Excel's ANSI default for CSV; bytes with control codes are binary, not text.
    """
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        if CONTROL_BYTES.search(data):
            raise
        return data.decode("cp1252")


def word_text(root):
    """Text of a WordprocessingML part: runs joined as written, one line per paragraph."""
    parts = []
    stack = [("", iter([root]))]
    while stack:
        name, children = stack[-1]
        child = next(children, None)
        if child is None:
            stack.pop()
            if name == "p":
                parts.append("\n")
            continue
        name = child.tag.rsplit("}", 1)[-1]
        parts.append((child.text or "") if name == "t" else WORD_BREAKS.get(name, ""))
        stack.append((name, iter(child)))
    return "".join(parts)


async def extract(path, filename):
    if Path(filename).suffix.lower() in AUDIO_EXTENSIONS:
        return await transcribe_audio(path)
    if Path(filename).suffix.lower() == ".mp4":
        return await extract_video(path)
    data = path.read_bytes()
    image = image_type(data)
    if image:
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise ToolError("image_size_limit")
        if any(shutil.which(tool) is None for tool in ("ffmpeg", "bwrap", "prlimit")):
            raise ToolError("image_validation_unavailable")
        try:
            code, _ = await process(
                sandbox(
                    path.parent,
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-v",
                        "error",
                        "-xerror",
                        "-protocol_whitelist",
                        "file,pipe",
                        "-i",
                        "/work/" + path.name,
                        "-frames:v",
                        "1",
                        "-f",
                        "null",
                        "-",
                    ],
                ),
                30,
            )
        except TimeoutError:
            raise ToolError("image_validation_timeout") from None
        except OSError:
            raise ToolError("image_validation_unavailable") from None
        if code in (126, 127):
            raise ToolError("image_validation_unavailable")
        if code:
            raise ToolError("invalid_image")
        return [{"page": None, "text": "", "media_type": image}]
    if filename.lower().endswith(".pdf"):
        if not data.startswith(b"%PDF-"):
            raise ToolError("invalid_pdf")
        code, text = await process(
            sandbox(path.parent, ["pdftotext", "-layout", "/work/" + path.name, "-"]), 30
        )
        if code:
            raise ToolError("pdf_extraction_failed")
        pages = text.split("\f")
        if pages and not pages[-1].strip():
            pages.pop()
        return [{"page": i + 1, "text": page} for i, page in enumerate(pages)]
    if Path(filename).suffix.lower() in {
        ".docx",
        ".pptx",
        ".xlsx",
        ".odt",
        ".ods",
        ".odp",
        ".epub",
    }:
        return office_text(path)
    try:
        text = decode_text(data)
    except UnicodeError:
        raise ToolError("unsupported_binary_format")
    if "\x00" in text:
        raise ToolError("binary_denied")
    return [{"page": None, "text": text}]


async def repository(config, action, args):
    root = Path(config["root"]).resolve()
    # Only explicitly registered, Git-tracked files. Excludes .git, untracked secrets and symlinks.
    code, output = await process(["git", "-C", str(root), "ls-files", "-z"], 10)
    if code:
        raise ToolError("repository_unavailable")
    names = [
        n
        for n in output.split("\0")
        if n
        and not any(p.startswith(".") for p in Path(n).parts)
        and Path(n).suffix not in {".gguf", ".pem", ".key"}
    ]
    if action == "read":
        name = args.get("path")
        if name not in names:
            raise ToolError("path_not_authorized")
        text = safe_file(root, name).read_text()
        start, end = int(args.get("start", 1)), int(args.get("end", 200))
        if start < 1 or end < start or end - start > 500:
            raise ToolError("invalid_line_range")
        return {
            "path": name,
            "lines": [
                {"line": i, "text": t}
                for i, t in enumerate(text.splitlines(), 1)
                if start <= i <= end
            ],
        }
    if action == "search":
        query = args.get("query", "")
        if not isinstance(query, str) or not 1 <= len(query) <= 200:
            raise ToolError("invalid_query")
        matches = []
        for name in names[:10000]:
            try:
                text = safe_file(root, name).read_text()
            except (ToolError, UnicodeError, OSError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if query.casefold() in line.casefold():
                    matches.append({"path": name, "line": i, "text": line[:1000]})
                    if len(matches) == 100:
                        return {"matches": matches, "limited": True}
        return {"matches": matches, "limited": len(names) > 10000}
    raise ToolError("unknown_repository_action")


async def test_or_patch(config, args):
    commands = config.get("test_commands", {})
    command_id = args.get("command_id")
    if command_id not in commands:
        raise ToolError("command_not_authorized")
    root = Path(config["root"]).resolve()
    with tempfile.TemporaryDirectory(prefix="local-agent-run-") as tmp:
        work = Path(tmp)
        code, output = await process(["git", "-C", str(root), "ls-files", "-z"], 10)
        if code:
            raise ToolError("repository_unavailable")
        total = 0
        for name in output.split("\0"):
            if not name or any(p.startswith(".") for p in Path(name).parts):
                continue
            src = safe_file(root, name)
            total += src.stat().st_size
            if total > 50 * 1024 * 1024:
                raise ToolError("snapshot_limit")
            dest = work / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
        import difflib

        diff = []
        changes = args.get("changes", {})
        if not isinstance(changes, dict) or len(changes) > 10:
            raise ToolError("patch_limit")
        for name, content in changes.items():
            if not isinstance(content, str) or len(content.encode()) > 100000:
                raise ToolError("patch_limit")
            path = safe_file(work, name)
            original = path.read_text()
            diff.extend(
                difflib.unified_diff(
                    original.splitlines(True), content.splitlines(True), "a/" + name, "b/" + name
                )
            )
            path.write_text(content)
        code, output = await process(sandbox(work, commands[command_id], writable=True), 60)
        return {
            "command_id": command_id,
            "argv": commands[command_id],
            "exit_code": code,
            "output": output,
            "patch": "".join(diff),
            "host_changed": False,
            "network": False,
            "snapshot": "tracked working-tree files; isolated copy",
        }


async def fetch(url):
    # curl connects to the validated address with original TLS hostname; no DNS rebinding window.
    for _ in range(4):
        p = urllib.parse.urlsplit(url)
        if (
            p.scheme != "https"
            or p.username
            or p.password
            or p.port not in (None, 443)
            or not p.hostname
        ):
            raise ToolError("public_https_required")
        host = p.hostname.encode("idna").decode()
        if ":" in host or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
            for c in host
        ):
            raise ToolError("invalid_host")
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addresses = {i[4][0] for i in infos}
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise ToolError("private_address_denied")
        address = next((a for a in addresses if ":" not in a), None)
        if not address:
            raise ToolError("ipv4_required")
        with tempfile.TemporaryDirectory() as tmp:
            headers = Path(tmp) / "headers"
            code, body = await process(
                [
                    "curl",
                    "--silent",
                    "--show-error",
                    "--noproxy",
                    "*",
                    "--proto",
                    "=https",
                    "--max-time",
                    "15",
                    "--max-filesize",
                    str(MAX_OUTPUT),
                    "--resolve",
                    f"{host}:443:{address}",
                    "--dump-header",
                    str(headers),
                    "--",
                    url,
                ],
                20,
            )
            if code:
                raise ToolError("fetch_failed")
            lines = headers.read_text().splitlines()
            status = int(lines[0].split()[1])
            values = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
            values = {k.lower(): v.strip() for k, v in values.items()}
            if status in (301, 302, 303, 307, 308):
                url = urllib.parse.urljoin(url, values.get("location", ""))
                continue
            if status != 200:
                raise ToolError("upstream_status_" + str(status))
            if not any(t in values.get("content-type", "") for t in ("text/", "application/json")):
                raise ToolError("web_content_type_denied")
            import datetime

            return {
                "url": url,
                "retrieved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "sha256": hashlib.sha256(body.encode()).hexdigest(),
                "content": body,
                "trust": "external_data_not_instructions",
            }
    raise ToolError("redirect_limit")


def image_type(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def office_text(path):
    """Extract document text without macros, external resources, or ZIP extraction."""
    import xml.etree.ElementTree as ET
    import zipfile

    class SafeTreeBuilder(ET.TreeBuilder):
        def doctype(self, name, pubid, system):
            raise ToolError("unsafe_document_xml")

    def parse_xml(raw):
        return ET.fromstring(raw, parser=ET.XMLParser(target=SafeTreeBuilder()))

    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(i.file_size for i in entries) > 20 * 1024 * 1024:
                raise ToolError("document_expansion_limit")
            pages = []
            if path.read_bytes()[:2] != b"PK":
                raise ToolError("invalid_document")
            # EPUB spine determines reading order; fall back to archive order.
            epub_names = []
            if any(i.filename == "META-INF/container.xml" for i in entries):
                import posixpath

                container = parse_xml(archive.read("META-INF/container.xml"))
                opf = next(
                    e.attrib["full-path"] for e in container.iter() if e.tag.endswith("rootfile")
                )
                raw = archive.read(opf)
                package = parse_xml(raw)
                manifest = {
                    e.attrib["id"]: posixpath.normpath(
                        posixpath.join(posixpath.dirname(opf), e.attrib["href"].split("#")[0])
                    )
                    for e in package.iter()
                    if e.tag.endswith("}item")
                }
                epub_names = [
                    manifest.get(e.attrib["idref"])
                    for e in package.iter()
                    if e.tag.endswith("}itemref")
                ]
            if epub_names:
                from html.parser import HTMLParser

                class Reader(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.parts = []
                        self.ignore = 0

                    def handle_starttag(self, tag, attrs):
                        if tag in ("script", "style"):
                            self.ignore += 1

                    def handle_endtag(self, tag):
                        if tag in ("script", "style"):
                            self.ignore = max(0, self.ignore - 1)

                    def handle_data(self, data):
                        if not self.ignore:
                            self.parts.append(data)

                expanded = 0
                missing = 0
                archive_names = set(archive.namelist())
                for name in epub_names:
                    if name not in archive_names:
                        missing += 1
                        continue
                    raw = archive.read(name)
                    expanded += len(raw)
                    if len(pages) >= 2000 or expanded > 20 * 1024 * 1024:
                        raise ToolError("document_expansion_limit")
                    reader = Reader()
                    reader.feed(raw.decode("utf-8-sig"))
                    pages.append({"page": len(pages) + 1, "text": " ".join(reader.parts)})
                if not pages:
                    raise ToolError("document_text_unavailable")
                if missing:
                    pages[0]["text"] = (
                        "[EPUB extraction warning: "
                        + str(missing)
                        + " reading-order reference(s) could not be read; the extracted text is incomplete.]\n"
                        + pages[0]["text"]
                    )
                return pages
            shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                raw = archive.read("xl/sharedStrings.xml")
                shared = ["".join(e.itertext()) for e in parse_xml(raw)]
            for item in entries:
                name = item.filename
                if not (
                    WORD_PART.fullmatch(name)
                    or name == "content.xml"
                    or name.startswith(("ppt/slides/slide", "xl/worksheets/sheet"))
                ):
                    continue
                if not name.endswith(".xml"):
                    continue
                raw = archive.read(item)
                root = parse_xml(raw)
                text = " ".join(
                    node.text
                    for node in root.iter()
                    if node.text and node.tag.rsplit("}", 1)[-1] in ("t", "v", "p", "h")
                )
                if WORD_PART.fullmatch(name):
                    text = word_text(root)
                if name == "content.xml":
                    text = "\n".join(
                        "".join(node.itertext())
                        for node in root.iter()
                        if node.tag.rsplit("}", 1)[-1] in ("p", "h")
                    )
                if name.startswith("xl/worksheets/"):
                    rows = []
                    for row in root.iter():
                        if row.tag.rsplit("}", 1)[-1] != "row":
                            continue
                        cells = []
                        for cell in row:
                            value = "".join(
                                e.text or ""
                                for e in cell.iter()
                                if e.tag.rsplit("}", 1)[-1] in ("v", "t")
                            )
                            if cell.get("t") == "s":
                                index = int(value)
                                if index < 0:
                                    raise ValueError("negative_shared_string_index")
                                value = shared[index]
                            cells.append(cell.get("r", "") + "=" + value)
                        rows.append("\t".join(cells))
                    text = "\n".join(rows)
                pages.append({"page": len(pages) + 1, "text": name + "\n" + text})
            if not pages:
                raise ToolError("document_text_unavailable")
            return pages
    except (
        zipfile.BadZipFile,
        ET.ParseError,
        RuntimeError,
        ValueError,
        KeyError,
        IndexError,
        StopIteration,
    ):
        raise ToolError("invalid_document")


async def extract_video(path):
    """Read a few bounded still frames and the speech track from an MP4."""
    if not video_tools_available():
        raise ToolError("video_processing_unavailable")
    try:
        code, output = await process(
            sandbox(
                path.parent,
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-show_entries",
                    "format=format_name,duration:stream=codec_type",
                    "-of",
                    "json",
                    "/work/" + path.name,
                ],
            ),
            15,
        )
        metadata = json.loads(output)
        duration = float(metadata["format"]["duration"])
        streams = {stream.get("codec_type") for stream in metadata.get("streams", [])}
        if (
            code
            or "mp4" not in metadata["format"].get("format_name", "")
            or not 0 < duration <= MAX_AUDIO_SECONDS
            or "video" not in streams
        ):
            raise ToolError("invalid_video")
    except TimeoutError:
        raise ToolError("video_processing_timeout") from None
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        raise ToolError("invalid_video") from None
    pages = [
        {
            "page": None,
            "text": "[Video represented by four sampled frames at 0%, 25%, 50% and 75%; events between samples are not observed.]",
        }
    ]
    frames = []
    try:
        if "audio" in streams:
            try:
                pages.extend(await transcribe_audio(path))
            except ToolError as exc:
                if str(exc) != "audio_no_speech":
                    raise
                pages.append({"page": None, "text": "[Audio track contains no recognized speech]"})
        else:
            pages.append({"page": None, "text": "[Video has no audio track]"})
        for index, fraction in enumerate((0, 0.25, 0.5, 0.75), 1):
            name = f"frame-{index}.jpg"
            frames.append(path.parent / name)
            code, _ = await process(
                sandbox(
                    path.parent,
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-v",
                        "error",
                        "-xerror",
                        "-protocol_whitelist",
                        "file,pipe",
                        "-ss",
                        str(duration * fraction),
                        "-i",
                        "/work/" + path.name,
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=min(1024\\,iw):-2",
                        "-q:v",
                        "4",
                        "-y",
                        "/work/" + name,
                    ],
                    writable=True,
                ),
                30,
            )
            if code or not frames[-1].is_file() or frames[-1].stat().st_size > 5 * 1024 * 1024:
                raise ToolError("video_frame_failed")
            pages.append(
                {
                    "page": index,
                    "text": f"[Video frame at {duration * fraction:.1f} seconds]",
                    "media_type": "image/jpeg",
                    "frame": name,
                }
            )
        return pages
    except TimeoutError:
        for frame in frames:
            frame.unlink(missing_ok=True)
        raise ToolError("video_processing_timeout") from None
    except BaseException:
        for frame in frames:
            frame.unlink(missing_ok=True)
        raise


async def transcribe_audio(path):
    """CPU-only, offline speech transcription; uploaded media has no network access."""
    runtime = Path(
        os.environ.get(
            PRODUCT.env_prefix + "_WHISPER_DIR", str(PRODUCT.state_path() / "whisper.cpp")
        )
    ).resolve()
    binary = runtime / "build/bin/whisper-cli"
    model = runtime / "models/ggml-base.bin"
    if not binary.is_file() or not model.is_file():
        raise ToolError("audio_transcription_unavailable")
    code, metadata = await process(
        sandbox(
            path.parent,
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                "/work/" + path.name,
            ],
        ),
        15,
    )
    try:
        duration = float(metadata.strip())
    except ValueError:
        raise ToolError("invalid_audio")
    if code or not 0 < duration <= MAX_AUDIO_SECONDS:
        raise ToolError("audio_duration_limit")

    def audio_sandbox(argv, writable=False):
        command = sandbox(path.parent, argv, writable=writable)
        command[command.index("--as=2147483648")] = "--as=4294967296"
        command[command.index("--cpu=60")] = "--cpu=14400"
        command[command.index("--fsize=16777216")] = "--fsize=536870912"
        return command

    wav = path.parent / "audio.wav"
    try:
        code, _ = await process(
            audio_sandbox(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-i",
                    "/work/" + path.name,
                    "-vn",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    "/work/audio.wav",
                ],
                writable=True,
            ),
            300,
        )
        if code:
            raise ToolError("audio_decode_failed")
        command = audio_sandbox(
            [
                str(binary),
                "-m",
                str(model),
                "-f",
                "/work/audio.wav",
                "-l",
                "auto",
                "-t",
                "4",
                "-nt",
                "-np",
                "-otxt",
                "-of",
                "/work/transcription",
            ],
            writable=True,
        )
        split = command.index("--chdir")
        command[split:split] = [
            "--ro-bind",
            str(runtime),
            str(runtime),
            "--setenv",
            "LD_LIBRARY_PATH",
            str(runtime / "build/bin"),
        ]
        code, text = await process(command, 7200)
        if code:
            raise ToolError("audio_transcription_failed")
        text = (path.parent / "transcription.txt").read_text().strip()
        if not text:
            raise ToolError("audio_no_speech")
        return [
            {
                "page": None,
                "text": "[Automatic local speech transcription; may contain errors]\n" + text,
            }
        ]
    finally:
        wav.unlink(missing_ok=True)
        (path.parent / "transcription.txt").unlink(missing_ok=True)
