"""Jira Cloud v3 create-only driver. Correlation markers are not deduplication."""

import copy
import re

import httpx


def issue_receipt(value, destination):
    key = value.get("key") if isinstance(value, dict) else None
    if isinstance(key, str) and re.fullmatch(re.escape(destination) + r"-[1-9][0-9]*", key):
        return {"issue_key": key}
    return None


class JiraEffectDriver:
    def client(self, contract, credentials):
        return httpx.AsyncClient(
            base_url=contract["endpoint"].rstrip("/"),
            auth=(credentials["email"], credentials["token"]),
            timeout=15,
            follow_redirects=False,
            trust_env=False,
        )

    async def create(self, contract, credentials, effect):
        payload = copy.deepcopy(effect["artifact"])
        payload["fields"].setdefault("labels", []).append("harness-effect-" + effect["effect_id"])
        async with self.client(contract, credentials) as client:
            response = await client.post("/rest/api/3/issue", json=payload)
        if response.status_code == 201:
            receipt = issue_receipt(response.json(), effect["destination"])
            return ("done", receipt) if receipt else ("unknown", None)
        # Explicit client rejection proves this request was not accepted; timeouts and
        # server failures do not. Never persist potentially secret response content.
        if response.status_code in (400, 401, 403, 404, 413, 422, 429):
            return "failed", None
        return "unknown", None

    async def reconcile(self, contract, credentials, effect):
        marker = "harness-effect-" + effect["effect_id"]
        async with self.client(contract, credentials) as client:
            response = await client.post(
                "/rest/api/3/search/jql",
                json={
                    "jql": f'project = "{effect["destination"]}" AND labels = "{marker}"',
                    "fields": ["labels", "project"],
                    "maxResults": 2,
                },
            )
        if response.status_code != 200:
            return None
        body = response.json()
        issues = body.get("issues", [])
        if len(issues) != 1 or body.get("nextPageToken") or body.get("isLast") is False:
            return None
        issue = issues[0]
        fields = issue.get("fields", {})
        if (
            marker not in fields.get("labels", [])
            or fields.get("project", {}).get("key") != effect["destination"]
        ):
            return None
        return issue_receipt(issue, effect["destination"])
