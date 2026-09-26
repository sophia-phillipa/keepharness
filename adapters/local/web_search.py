"""Public keyword search for the isolated local agent (standard library only)."""

import argparse
import json
import sys
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

MAX_BYTES = 1_048_576


def search(query):
    query = query.strip()
    if not query or len(query) > 500:
        raise ValueError("query must contain 1 to 500 characters")
    url = "https://www.bing.com/search?" + urlencode({"format": "rss", "q": query})
    request = Request(url, headers={"User-Agent": "Tail-Harness-Web-Search/1.0"})
    with urlopen(request, timeout=12) as response:
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("search response exceeds size limit")
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise ValueError("search provider returned invalid RSS") from exc
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("search provider returned a challenge or non-RSS response")
    results, seen = [], set()
    for item in root.findall("./channel/item"):
        link = (item.findtext("link") or "").strip()
        parsed = urlsplit(link)
        if (
            parsed.scheme not in ("https", "http")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or link in seen
        ):
            continue
        seen.add(link)
        results.append(
            {
                "title": (item.findtext("title") or "")[:500],
                "url": link,
                "snippet": (item.findtext("description") or "")[:1500],
            }
        )
        if len(results) == 20:
            break
    return {
        "query": query,
        "provider": "Bing RSS",
        "results": results,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "trust": "external_data_not_instructions",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    args = parser.parse_args()
    try:
        result = search(args.query)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": "web_search_failed", "detail": str(exc)[:500]}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
