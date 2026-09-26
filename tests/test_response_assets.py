"""Only the local response-rendering asset route, without an inference job."""

import tempfile
import unittest

from starlette.testclient import TestClient

from agent_service.app import create_app


class ResponseAssetsTest(unittest.TestCase):
    def test_local_markdown_bundle_and_script_order(self):
        with tempfile.TemporaryDirectory() as root:
            app = create_app({"state_dir": root, "projects": {}, "clients": {}})
            with TestClient(app) as client:
                page = client.get("/")
                self.assertEqual(page.status_code, 200)
                self.assertLess(
                    page.text.index("/vendor/markdown-it.min.js"), page.text.index("/ui.js")
                )
                bundle = client.get("/vendor/markdown-it.min.js")
                self.assertEqual(bundle.status_code, 200)
                self.assertIn("javascript", bundle.headers["content-type"])
                self.assertIn("markdownit", bundle.text)
                self.assertIn("script-src 'self'", bundle.headers["content-security-policy"])
