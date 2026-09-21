import io
import json
from unittest.mock import patch

import pytest

from Adapters.local.web_search import search
from Adapters.local.sandbox import wrap


def test_search_encodes_query_and_returns_bounded_deduplicated_sources():
    body = b'''<rss><channel><item><title>A &amp; B</title><link>https://example.org/a</link><description>Evidence</description></item><item><title>Duplicate</title><link>https://example.org/a</link></item><item><title>Unsafe</title><link>javascript:alert(1)</link></item></channel></rss>'''
    with patch('Adapters.local.web_search.urlopen', return_value=io.BytesIO(body)) as fetch:
        result = search('harness & tools')
    assert 'q=harness+%26+tools' in fetch.call_args.args[0].full_url
    assert fetch.call_args.kwargs['timeout'] == 12
    assert result['results'] == [{'title': 'A & B', 'url': 'https://example.org/a', 'snippet': 'Evidence'}]
    assert result['trust'] == 'external_data_not_instructions'


@pytest.mark.parametrize('body', [b'<html>challenge</html>', b'not xml', b'x' * 1_048_577])
def test_search_reports_invalid_or_oversized_responses(body):
    with patch('Adapters.local.web_search.urlopen', return_value=io.BytesIO(body)):
        with pytest.raises(ValueError):
            search('test')


@pytest.mark.parametrize('internet,shell', [(False, False), (False, True), (True, False), (True, True)])
def test_search_helper_is_mounted_only_with_effective_permissions(tmp_path, internet, shell):
    binary = tmp_path / 'fixture-cli'
    binary.touch()
    # This tests command construction, not execution of the Linux sandbox.
    with patch('Adapters.local.sandbox.sys.platform', 'linux'), \
            patch('Adapters.local.sandbox.shutil.which', return_value='/fixture/bwrap'):
        command = wrap([str(binary)], tmp_path, tmp_path,
                       {'permissions': {'internet': internet, 'shell': shell}})
    assert ('/tail-web-search.py' in command) is (internet and shell)
