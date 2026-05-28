from __future__ import annotations

import httpx


class CaddyClient:
    def __init__(self, admin_url: str, domain_suffix: str, bucket_name: str) -> None:
        if not admin_url:
            raise ValueError(
                "FSTAK_CADDY_ADMIN_URL must be set; CaddyClient cannot operate without an admin URL"
            )
        self._admin_url = admin_url.rstrip("/")
        self._domain_suffix = domain_suffix
        self._bucket_name = bucket_name

    async def upsert_project_route(self, project_slug: str, deployment_id: str) -> None:
        route_id_assets = f"fstak-route-{project_slug}-assets"
        route_id_spa = f"fstak-route-{project_slug}-spa"
        prefix = f"deployments/{project_slug}/{deployment_id}"
        asset_route = {
            "@id": route_id_assets,
            "match": [
                {
                    "host": [f"{project_slug}.{self._domain_suffix}"],
                    "path_regexp": {"name": "asset", "pattern": r".*\.[A-Za-z0-9]+$"},
                }
            ],
            "terminal": False,
        }
        asset_route["handle"] = [
            {"handler": "rewrite", "uri": f"/{self._bucket_name}/{prefix}" + "{http.request.uri.path}"},
            {
                "handler": "reverse_proxy",
                "headers": {"request": {"set": {"Host": ["storage.googleapis.com"]}}},
                "upstreams": [{"dial": "storage.googleapis.com:443"}],
                "transport": {"protocol": "http", "tls": {}},
                "handle_response": [
                    {
                        "match": {"status_code": [403, 404]},
                        "routes": [
                            {
                                "handle": [
                                    {"handler": "static_response", "status_code": 404}
                                ]
                            }
                        ],
                    }
                ],
            },
        ]

        spa_route = {
            "@id": route_id_spa,
            "match": [{"host": [f"{project_slug}.{self._domain_suffix}"]}],
            "handle": [
                {"handler": "rewrite", "uri": f"/{self._bucket_name}/{prefix}/index.html"},
                {
                    "handler": "reverse_proxy",
                    "headers": {"request": {"set": {"Host": ["storage.googleapis.com"]}}},
                    "upstreams": [{"dial": "storage.googleapis.com:443"}],
                    "transport": {"protocol": "http", "tls": {}},
                },
            ],
        }

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.patch(f"{self._admin_url}/id/{route_id_assets}", json=asset_route)
            if resp.status_code < 400:
                resp2 = await client.patch(f"{self._admin_url}/id/{route_id_spa}", json=spa_route)
                if resp2.status_code < 400:
                    return
            else:
                await client.post(f"{self._admin_url}/config/apps/http/servers/fstak/routes", json=asset_route)
            resp3 = await client.patch(f"{self._admin_url}/id/{route_id_spa}", json=spa_route)
            if resp3.status_code >= 400:
                resp4 = await client.post(f"{self._admin_url}/config/apps/http/servers/fstak/routes", json=spa_route)
                resp4.raise_for_status()

    async def remove_project_route(self, project_slug: str) -> None:
        route_ids = [f"fstak-route-{project_slug}", f"fstak-route-{project_slug}-assets", f"fstak-route-{project_slug}-spa"]
        async with httpx.AsyncClient(timeout=5.0) as client:
            for route_id in route_ids:
                resp = await client.delete(f"{self._admin_url}/id/{route_id}")
                if resp.status_code in (404, 500):
                    continue
                resp.raise_for_status()
