"""Synthetic attachment parser gauntlet; no inference or personal files."""
import asyncio
import base64
import zipfile
from unittest.mock import AsyncMock

import pytest
from agent_service import tools

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aJ1sAAAAASUVORK5CYII=')


def archive(tmp_path, entries):
    path = tmp_path/'source'
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        for name, content in entries.items():
            output.writestr(name, content)
    return path


def extract(path, filename):
    return asyncio.run(tools.extract(path, filename))


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16', 'utf-16-be'])
def test_round01_xml_entities_rejected_in_all_encodings(tmp_path, encoding):
    raw = '<!DOCTYPE root [<!ENTITY secret "SYNTHETIC EXPANSION">]><root><t>&secret;</t></root>'
    path = archive(tmp_path, {'word/document.xml': raw.encode(encoding)})
    with pytest.raises(tools.ToolError, match='unsafe_document_xml'):
        extract(path, 'report.DOCX')


def test_round02_epub_container_rejects_entities(tmp_path):
    container = '<!DOCTYPE container [<!ENTITY location "book.opf">]><container><rootfile full-path="&location;"/></container>'
    path = archive(tmp_path, {'META-INF/container.xml': container, 'book.opf': '<package/>', 'word/document.xml': '<root><t>Fallback</t></root>'})
    with pytest.raises(tools.ToolError, match='unsafe_document_xml'):
        extract(path, 'book.EPUB')


@pytest.mark.parametrize('index', ['-1', '-2'])
def test_round03_xlsx_negative_shared_index_rejected(tmp_path, index):
    path = archive(tmp_path, {'xl/sharedStrings.xml': '<sst><si><t>First</t></si><si><t>Last</t></si></sst>',
        'xl/worksheets/sheet1.xml': '<worksheet><row><c r="A1" t="s"><v>'+index+'</v></c></row></worksheet>'})
    with pytest.raises(tools.ToolError, match='invalid_document'):
        extract(path, 'report.XLSX')


@pytest.mark.parametrize('data', [b'\x89PNG\r\n\x1a\nFAKE', b'\xff\xd8\xffFAKE', b'GIF89aFAKE', b'RIFF\x04\x00\x00\x00WEBP'])
@pytest.mark.requires_media_sandbox
def test_round04_fake_images_rejected(tmp_path, data):
    path = tmp_path/'source';path.write_bytes(data)
    with pytest.raises(tools.ToolError, match='invalid_image'):
        extract(path, 'picture.bin')


@pytest.mark.parametrize('filename', ['photo.PNG', 'photo.bin'])
@pytest.mark.requires_media_sandbox
def test_round05_valid_image_sniffed_independent_of_name(tmp_path, filename):
    path = tmp_path/'source';path.write_bytes(PNG)
    assert extract(path, filename) == [{'page': None, 'text': '', 'media_type': 'image/png'}]


def test_round06_audio_routes_without_reading_whole_file(tmp_path, monkeypatch):
    from pathlib import Path
    path = tmp_path/'recording';path.write_bytes(b'SYNTHETIC AUDIO')
    transcribe = AsyncMock(return_value=[{'page': None, 'text': 'mock speech'}])
    monkeypatch.setattr(tools, 'transcribe_audio', transcribe)
    def forbidden_read(self):
        raise AssertionError('Audio must not be loaded before streaming transcription')
    monkeypatch.setattr(Path, 'read_bytes', forbidden_read)
    assert extract(path, 'recording.WAV')[0]['text'] == 'mock speech'
    transcribe.assert_awaited_once_with(path)


@pytest.mark.parametrize('filename,content,expected', [('source.RS', 'café and façade', 'café and façade'), ('empty.TXT', '', ''), ('data.CSV', 'α,β\n1,2', 'α,β\n1,2')])
def test_round07_text_empty_unicode_uppercase(tmp_path, filename, content, expected):
    path = tmp_path/'source';path.write_text(content)
    assert extract(path, filename) == [{'page': None, 'text': expected}]


@pytest.mark.parametrize('data,error', [(b'\x00UTF8', 'binary_denied'), (b'\xff\xfe\x00', 'unsupported_binary_format')])
def test_round08_binary_disguised_as_text(tmp_path, data, error):
    path = tmp_path/'source';path.write_bytes(data)
    with pytest.raises(tools.ToolError, match=error):
        extract(path, 'harmless.txt')


def test_round09_compressed_document_expansion_bound(tmp_path):
    path = archive(tmp_path, {'word/document.xml': b'x'*(20*1024*1024+1)})
    assert path.stat().st_size < 50000
    with pytest.raises(tools.ToolError, match='document_expansion_limit'):
        extract(path, 'compressed.docx')


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16', 'utf-16-be'])
def test_round10_cross_retest_safe_xml_and_index(tmp_path, encoding):
    path = archive(tmp_path, {'word/document.xml': '<root><t>Safe café</t></root>'.encode(encoding)})
    assert 'Safe café' in extract(path, 'report.DOCX')[0]['text']
    path = archive(tmp_path, {'xl/sharedStrings.xml': '<sst><si><t>First</t></si></sst>'.encode(encoding),
        'xl/worksheets/sheet1.xml': '<worksheet><row><c r="A1" t="s"><v>0</v></c></row></worksheet>'.encode(encoding)})
    assert 'A1=First' in extract(path, 'report.XLSX')[-1]['text']


def test_round10_odt_inline_formatting_preserves_words(tmp_path):
    path = archive(tmp_path, {'content.xml': '<document><p>Hello <span>formatted</span> world!</p><p>Second</p></document>'})
    text = extract(path, 'document.ODT')[0]['text']
    assert 'Hello formatted world!' in text
    assert text.count('formatted') == 1


@pytest.mark.requires_media_sandbox
def test_round10_truncated_real_image_rejected(tmp_path):
    path = tmp_path/'source';path.write_bytes(PNG[:40])
    with pytest.raises(tools.ToolError, match='invalid_image'):
        extract(path, 'broken.PNG')


def test_round10_epub_repeated_spine_cannot_amplify_archive(tmp_path):
    package = '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="a" href="a.xhtml"/></manifest><spine>'+('<itemref idref="a"/>'*21)+'</spine></package>'
    path = archive(tmp_path, {'META-INF/container.xml': '<container><rootfile full-path="book.opf"/></container>',
        'book.opf': package, 'a.xhtml': '<p>'+('x'*1024*1024)+'</p>'})
    with pytest.raises(tools.ToolError, match='document_expansion_limit'):
        extract(path, 'book.epub')


@pytest.mark.requires_media_sandbox
def test_round10_image_decoder_timeout_is_controlled(tmp_path, monkeypatch):
    path = tmp_path/'source';path.write_bytes(PNG)
    monkeypatch.setattr(tools, 'process', AsyncMock(side_effect=TimeoutError))
    with pytest.raises(tools.ToolError, match='image_validation_timeout'):
        extract(path, 'photo.png')


@pytest.mark.parametrize('failure', ['missing', 'exit127', 'oserror'])
def test_round10_missing_image_validator_is_not_bad_image(tmp_path, monkeypatch, failure):
    import shutil
    path = tmp_path/'source';path.write_bytes(PNG)
    if failure=='missing':
        monkeypatch.setattr(shutil, 'which', lambda name: None)
    elif failure=='exit127':
        monkeypatch.setattr(tools, 'process', AsyncMock(return_value=(127, 'not found')))
    else:
        monkeypatch.setattr(tools, 'process', AsyncMock(side_effect=FileNotFoundError))
    with pytest.raises(tools.ToolError, match='image_validation_unavailable'):
        extract(path, 'photo.png')


@pytest.mark.parametrize('missing', ['manifest', 'archive'])
def test_round10_epub_missing_reference_keeps_readable_chapters_with_notice(tmp_path, missing):
    missing_item = '' if missing=='manifest' else '<item id="cover" href="absent.xhtml"/>'
    package = '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'+missing_item+'<item id="chapter" href="chapter.xhtml"/></manifest><spine><itemref idref="cover"/><itemref idref="chapter"/></spine></package>'
    path = archive(tmp_path, {'META-INF/container.xml': '<container><rootfile full-path="book.opf"/></container>',
        'book.opf': package, 'chapter.xhtml': '<p>SYNTHETIC CHAPTER</p>'})
    pages = extract(path, 'book.epub')
    assert len(pages) == 1
    assert 'SYNTHETIC CHAPTER' in pages[0]['text']
    assert 'EPUB extraction warning' in pages[0]['text']
    assert '1' in pages[0]['text'] and 'incomplete' in pages[0]['text']


def test_round10_epub_no_readable_reference_still_rejected(tmp_path):
    package = '<package xmlns="http://www.idpf.org/2007/opf"><manifest/><spine><itemref idref="absent"/></spine></package>'
    path = archive(tmp_path, {'META-INF/container.xml': '<container><rootfile full-path="book.opf"/></container>', 'book.opf': package})
    with pytest.raises(tools.ToolError, match='document_text_unavailable'):
        extract(path, 'empty.epub')
