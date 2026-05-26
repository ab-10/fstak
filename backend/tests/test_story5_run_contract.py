from __future__ import annotations

import io
import tarfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from control_plane.app import app
from control_plane.auth_manager import manager
from control_plane.store import store


def _archive_with_index_html() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        payload = b"<!doctype html><html><body>ok</body></html>"
        info = tarfile.TarInfo(name="index.html")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


class Story5RunContractTests(unittest.TestCase):
    def setUp(self) -> None:
        store.projects.clear()
        store.deployments.clear()
        store.project_env.clear()
        store.project_deps.clear()
        store.account_projects.clear()
        manager._tokens.clear()  # noqa: SLF001 - test-only reset

        self.client = TestClient(app)
        auth = self.client.post("/auth/code", json={"code": "story5-user"})
        self.assertEqual(auth.status_code, 200)
        self.token = auth.json()["fstak_token"]

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

    @patch("control_plane.app.shutil.which", return_value=None)
    def test_first_and_subsequent_deploy_keep_stable_project_url(self, _: object) -> None:
        first = self._run_deploy()

        self.assertEqual(first["project_name"], "story5-app")
        self.assertTrue(first["project_slug"].startswith("story5-app-"))
        self.assertEqual(first["url"], f"https://{first['project_slug']}.fstak.runspx.com")
        self.assertTrue(first["deployment_id"])

        second = self._run_deploy(project_slug=first["project_slug"])

        self.assertEqual(second["project_slug"], first["project_slug"])
        self.assertEqual(second["url"], first["url"])
        self.assertNotEqual(second["deployment_id"], first["deployment_id"])


if __name__ == "__main__":
    unittest.main()
