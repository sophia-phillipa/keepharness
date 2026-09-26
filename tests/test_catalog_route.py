"""The project catalog route must serve the real catalog, not crash on a shadowed name."""


def test_catalog_route_returns_project_catalog(client):
    response = client.get("/v1/catalog?project_id=p")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {"agents", "skills", "warnings"}
