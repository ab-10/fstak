from __future__ import annotations

import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from control_plane.app import _run_build, app
from control_plane.auth_manager import manager
from control_plane.config import get_settings
from control_plane.store import store


def _archive_with_index_html() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        payload = b"<!doctype html><html><body>ok</body></html>"
        info = tarfile.TarInfo(name="index.html")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


class _FakeAssetStorage:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, Path]] = []

    async def upload_dist(self, prefix: str, dist_dir: Path) -> dict[str, Any]:
        self.uploads.append((prefix, dist_dir))
        return {"files": [], "artifact_hash": "sha256:fake"}


def _fake_run_build(src_dir: Path, deps: list[str], env_vars: dict[str, str]) -> tuple[Path, str]:
    _ = (deps, env_vars)
    dist = src_dir / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")
    return dist, "fake-bun"


class Story5RunContractTests(unittest.TestCase):
    def setUp(self) -> None:
        store.reset_for_tests()
        manager._tokens.clear()  # noqa: SLF001 - test-only reset

        # Required env vars for fail-fast config; values are placeholders since
        # _asset_storage and _caddy_client are patched in the tests below.
        self._env_patch = patch.dict(
            os.environ,
            {
                "FSTAK_DOMAIN_SUFFIX": "test.example.com",
                "FSTAK_GCS_BUCKET_NAME": "test-bucket",
                "FSTAK_CADDY_ADMIN_URL": "http://127.0.0.1:2019",
                "FSTAK_ALLOW_DEV_LOGIN": "1",
            },
        )
        self._env_patch.start()
        get_settings.cache_clear()

        self._asset_patch = patch(
            "control_plane.app._asset_storage", return_value=_FakeAssetStorage()
        )
        self._asset_patch.start()

        # _run_build no longer has a no-bun fallback; bypass it in deploy tests
        # so we exercise the routing/storage contract without needing bun.
        self._run_build_patch = patch("control_plane.app._run_build", side_effect=_fake_run_build)
        self._run_build_patch.start()

        self.client = TestClient(app)
        auth = self.client.post("/auth/code", json={"code": "story5-user"})
        self.assertEqual(auth.status_code, 200)
        self.token = auth.json()["fstak_token"]

    def tearDown(self) -> None:
        self._run_build_patch.stop()
        self._asset_patch.stop()
        self._env_patch.stop()
        get_settings.cache_clear()

    def _run_deploy(self, project_slug: str | None = None) -> dict:
        data: dict[str, str] = {"project_name": "story5-app"}
        if project_slug is not None:
            data["project_slug"] = project_slug

        response = self.client.post(
            "/run",
            headers={"Authorization": f"Bearer {self.token}"},
            data=data,
            files={"code": ("code.tar.gz", _archive_with_index_html(), "application/gzip")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_first_and_subsequent_deploy_keep_stable_project_url(self) -> None:
        class FakeCaddy:
            async def upsert_project_route(self, _: str, __: str) -> None:
                return None

            async def remove_project_route(self, _: str) -> None:
                return None

        with patch("control_plane.app._caddy_client", return_value=FakeCaddy()):
            first = self._run_deploy()

            self.assertEqual(first["project_name"], "story5-app")
            self.assertTrue(first["project_slug"].startswith("story5-app-"))
            self.assertEqual(first["url"], f"https://{first['project_slug']}.test.example.com")
            self.assertTrue(first["deployment_id"])

            second = self._run_deploy(project_slug=first["project_slug"])

        self.assertEqual(second["project_slug"], first["project_slug"])
        self.assertEqual(second["url"], first["url"])
        self.assertNotEqual(second["deployment_id"], first["deployment_id"])

    def test_deploy_upserts_project_route(self) -> None:
        class FakeCaddy:
            def __init__(self) -> None:
                self.upserts: list[tuple[str, str]] = []

            async def upsert_project_route(self, slug: str, deployment_id: str) -> None:
                self.upserts.append((slug, deployment_id))

            async def remove_project_route(self, _: str) -> None:
                return None

        fake = FakeCaddy()
        with patch("control_plane.app._caddy_client", return_value=fake):
            deployed = self._run_deploy()

        self.assertEqual(len(fake.upserts), 1)
        self.assertEqual(fake.upserts[0], (deployed["project_slug"], deployed["deployment_id"]))

    def test_kill_removes_project_route(self) -> None:
        class FakeCaddy:
            def __init__(self) -> None:
                self.removals: list[str] = []

            async def upsert_project_route(self, _: str, __: str) -> None:
                return None

            async def remove_project_route(self, slug: str) -> None:
                self.removals.append(slug)

        fake = FakeCaddy()
        with patch("control_plane.app._caddy_client", return_value=fake):
            deployed = self._run_deploy()
            response = self.client.post(
                f"/projects/{deployed['project_slug']}/kill",
                headers={"Authorization": f"Bearer {self.token}"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(fake.removals, [deployed["project_slug"]])

    @patch("control_plane.app.shutil.which", return_value="/usr/bin/bun")
    @patch("control_plane.app.subprocess.run")
    def test_build_requires_dist_index_html(self, run: Any, _: object) -> None:
        run.return_value.returncode = 0
        run.return_value.stderr = ""
        run.return_value.stdout = ""

        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = Path(tmpdir)
            (src_dir / "dist").mkdir()
            (src_dir / "dist" / "main.js").write_text("console.log('ok')", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "dist/index.html"):
                _run_build(src_dir, [], {})


if __name__ == "__main__":
    unittest.main()
