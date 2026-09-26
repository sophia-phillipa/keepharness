"""Harness identity against cross-site and forged-host requests (P5-12, F-06)."""

import asyncio
import hashlib

import httpx
import pytest

from agent_service.app import create_app

NAVIGATION = {
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-User": "?1",
}


def request(cfg, method, path, headers, host="127.0.0.1:18095"):
    app = create_app(cfg)

    async def scenario():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 54321))
        async with httpx.AsyncClient(transport=transport, base_url="http://" + host) as client:
            return await client.request(method, path, headers=headers)

    try:
        return asyncio.run(scenario())
    finally:
        app.state.service.db.close()


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Sec-Fetch-Site": "same-origin"},
        {"Sec-Fetch-Site": "same-site"},
        {"Sec-Fetch-Site": "none"},
    ],
)
def test_local_access_keeps_working_for_same_origin_and_non_browser_clients(
    harness_config, headers
):
    response = request(harness_config, "GET", "/v1/conversations", headers)
    assert response.status_code == 200, response.text


def test_cross_site_request_without_origin_is_denied(harness_config):
    response = request(harness_config, "GET", "/v1/conversations", {"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert response.json()["code"] == "origin_denied"


def test_cross_site_navigation_cannot_obtain_the_local_identity(harness_config):
    response = request(harness_config, "GET", "/v1/conversations", NAVIGATION)
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


def test_cross_site_post_is_denied_even_with_a_valid_token(make_harness_config):
    cfg = make_harness_config(
        local_access=False,
        clients={"a": {"sha256": hashlib.sha256(b"a").hexdigest(), "projects": ["sem-projeto"]}},
    )
    headers = {"Authorization": "Bearer a", "Sec-Fetch-Site": "cross-site"}
    response = request(cfg, "POST", "/v1/jobs", headers)
    assert response.status_code == 403
    assert response.json()["code"] == "origin_denied"
    assert (
        request(cfg, "GET", "/v1/conversations", {"Authorization": "Bearer a"}).status_code == 200
    )


@pytest.mark.parametrize(
    ("host", "headers"),
    [
        ("evil.test:18095", {}),
        ("127.0.0.1:18095", {"X-Forwarded-For": "203.0.113.9"}),
    ],
)
def test_forged_host_or_proxy_header_does_not_get_the_local_identity(harness_config, host, headers):
    response = request(harness_config, "GET", "/v1/conversations", headers, host=host)
    assert response.status_code == 401
