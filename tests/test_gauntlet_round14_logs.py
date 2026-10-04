from test_spans_route import span_client  # noqa: F401, F811

from agent_service.routes.conversations import ROUTES


def test_newest_event_pages_and_live_tail(span_client):  # noqa: F811
    client, service, job = span_client
    client.app.router.routes.extend(ROUTES)
    for index in range(602):
        service.event(job, "started", {"index": index})
    all_ids = [row["id"] for row in service.message_repository.all_events(job)]
    url = f"/v1/jobs/{job}/events?format=json&order=newest&limit=200"
    first = client.get(url).json()
    assert [event["id"] for event in first["events"]] == all_ids[-200:][::-1]
    collected = list(first["events"])
    page = first
    while page["has_more"]:
        page = client.get(url + f"&before={page['next_before']}").json()
        collected.extend(page["events"])
    assert [event["id"] for event in collected] == all_ids[::-1]
    service.event(job, "started", {"index": "live"})
    live = client.get(url + f"&after={all_ids[-1]}").json()
    assert len(live["events"]) == 1
    assert live["events"][0]["data"]["index"] == "live"
    assert client.get(url + "&before=-1").status_code == 422
    assert client.get(url, headers={"Authorization": "Bearer b"}).status_code == 404
