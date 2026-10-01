"""Round 6 independent effects/security probes; synthetic in-process evidence only."""

import asyncio
import ipaddress
import json
import time
from unittest.mock import patch

import httpx
import pytest
from test_effect_executor import prepared, request

from agent_service.errors import APIError
from agent_service.integrations import endpoint_identity


@pytest.mark.parametrize("first_outcome", ["done", "unknown"])
@pytest.mark.parametrize("prepare_after_dispatch", [False, True])
def test_ipv6_endpoint_alias_cannot_repeat_publication(
    make_harness_config, first_outcome, prepare_after_dispatch
):
    async def scenario():
        app, old = await prepared(make_harness_config)
        service = app.state.service
        service.effects._status(old["effect_id"], "invalidated")
        endpoint = "https://[2001:db8::1]"
        alias = "https://[2001:0db8:0000:0000:0000:0000:0000:0001]"
        assert ipaddress.ip_address(httpx.URL(endpoint).host) == ipaddress.ip_address(
            httpx.URL(alias).host
        )
        original = service.config["effect_integrations"][0]
        original["endpoint"] = endpoint
        service.config["effect_integrations"].append(
            {**original, "integration": "alias", "endpoint": alias}
        )
        posts = []

        def handler(req):
            posts.append({"host": req.url.host, "body": json.loads(req.content)})
            if first_outcome == "unknown" and len(posts) == 1:
                raise httpx.ReadError("synthetic lost response")
            return httpx.Response(201, json={"key": "TEST-1"})

        real_client = httpx.AsyncClient
        try:
            first = await service.effects.prepare("job", request())
            if not prepare_after_dispatch:
                second = await service.effects.prepare("job", {**request(), "integration": "alias"})
            with patch(
                "agent_service.jira_effects.httpx.AsyncClient",
                side_effect=lambda **kw: real_client(**kw, transport=httpx.MockTransport(handler)),
            ):
                service.gates.repository.resolve(first["gate_id"], "approve", "local", time.time())
                result1 = await service.effects.execute(first["effect_id"])
                assert result1["status"] == first_outcome
                if prepare_after_dispatch:
                    try:
                        second = await service.effects.prepare(
                            "job", {**request(), "integration": "alias"}
                        )
                    except APIError as exc:
                        assert exc.code == "effect_duplicate_outcome_pending"
                        return
                service.gates.repository.resolve(second["gate_id"], "approve", "local", time.time())
                result2 = await service.effects.execute(second["effect_id"])
            print(
                json.dumps(
                    {
                        "scenario": "R6-E1",
                        "first_outcome": first_outcome,
                        "prepare_after_dispatch": prepare_after_dispatch,
                        "identities": [endpoint_identity(endpoint), endpoint_identity(alias)],
                        "statuses": [result1["status"], result2["status"]],
                        "posts": len(posts),
                        "same_ip": True,
                    }
                )
            )
            assert len(posts) == 1, (
                "Equivalent IPv6 endpoint dispatched the approved artifact twice"
            )
            assert result2["status"] == "invalidated"
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())


def test_ipv6_legacy_binding_upgrade_is_idempotent(make_harness_config):
    from agent_service.persistence.db import migrate

    async def scenario():
        app, effect = await prepared(make_harness_config)
        service = app.state.service
        try:
            row = service.db.execute("SELECT binding,contract FROM effects").fetchone()
            binding, contract = json.loads(row["binding"]), json.loads(row["contract"])
            contract["endpoint"] = binding["endpoint"] = (
                "https://[2001:0db8:0000:0000:0000:0000:0000:0001]"
            )
            with service.db:
                service.db.execute(
                    "UPDATE effects SET binding=?,contract=?,status='unknown'",
                    (json.dumps(binding), json.dumps(contract)),
                )
                service.db.execute("UPDATE schema_version SET version=7")
            for _ in range(2):
                migrate(service.db)
                current = service.db.execute(
                    "SELECT binding,status,contract FROM effects"
                ).fetchone()
                assert json.loads(current["binding"])["endpoint"] == "https://[2001:db8::1]"
                assert current["status"] == "unknown"
                assert json.loads(current["contract"]) == contract
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())
