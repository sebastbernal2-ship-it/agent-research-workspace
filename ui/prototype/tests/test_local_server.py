import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ui.prototype.local_server import build_server
from harness.evidence import EvidenceRegistrar
from harness.run_ledger import RunLedger
from ui.prototype.runtime import (
    CREATED_AT, DEMO_RESEARCHER, PROJECT_ID, PrototypeRuntime,
)


class LocalPrototypeHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "canonical.sqlite3"
        self.workspace_root = Path(self.temp.name) / "AI-Projects"
        self.runtime = PrototypeRuntime(self.db_path, workspace_root=self.workspace_root)
        self.server = build_server(self.runtime, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path, *, method="GET", payload=None, headers=None):
        # Legacy test path shorthand maps only to the currently implemented v1 API.
        if path.startswith("/api/") and not path.startswith("/api/v"):
            path = "/api/v1/" + path[len("/api/"):]
        request_headers = {"Accept": "application/json", **(headers or {})}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        request = Request(
            f"{self.base_url}{path}", data=data, headers=request_headers, method=method,
        )
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8")), error.headers
        return response.status, json.loads(response.read().decode("utf-8")), response.headers

    def write_headers(self):
        return {
            "Origin": self.base_url,
            "X-Local-Prototype": "1",
        }

    def test_user_can_create_a_project_and_add_proposed_work(self):
        status, created, _ = self.request(
            "/api/v1/projects", method="POST",
            payload={"name": "My WSL project", "goal": "Build a first useful project."},
            headers=self.write_headers(),
        )
        self.assertEqual(status, 201)
        project_id = created["project"]["projectId"]
        self.assertEqual(created["project"]["name"], "My WSL project")
        project_directory = self.workspace_root / project_id
        self.assertTrue(project_directory.is_dir())
        self.assertEqual(project_directory.stat().st_mode & 0o777, 0o700)
        status, collection, _ = self.request("/api/v1/projects")
        self.assertEqual(status, 200)
        self.assertIn(project_id, {project["projectId"] for project in collection["items"]})

        status, work, _ = self.request(
            f"/api/v1/projects/{project_id}/work", method="POST",
            payload={"title": "Explore the question", "objective": "Write down the first research steps.", "workType": "research"},
            headers=self.write_headers(),
        )
        self.assertEqual(status, 201)
        self.assertEqual(work["workItem"]["status"], "proposed")
        summary_status, summary, _ = self.request(f"/api/v1/projects/{project_id}")
        self.assertEqual(summary_status, 200)
        self.assertEqual(summary["workItems"][0]["title"], "Explore the question")
        restarted = PrototypeRuntime(self.db_path, workspace_root=self.workspace_root)
        self.assertEqual(restarted.project_application.get_project(project_id)["name"], "My WSL project")
        self.assertEqual(restarted.project_application.list_work(project_id)[0]["title"], "Explore the question")
        self.assertTrue(restarted.store.verify(project_id))

    def test_local_answer_and_rejection_persist_across_runtime_restart(self):
        status, project, _ = self.request(f"/api/projects/{PROJECT_ID}")
        self.assertEqual(status, 200)
        self.assertEqual(project["project"]["name"], "Northstar research")
        self.assertEqual(project["counts"]["openQuestions"], 2)

        status, detail, headers = self.request(
            f"/api/projects/{PROJECT_ID}/inbox/question.source-policy",
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["ETag"], f'"{detail["etag"]}"')
        self.assertEqual(detail["record"]["status"], "open")
        self.assertEqual([item["status"] for item in detail["affectedWork"]], ["waiting_human", "waiting_human"])

        status, answered, _ = self.request(
            f"/api/projects/{PROJECT_ID}/inbox/question.source-policy/answers",
            method="POST",
            payload={
                "expectedEtag": detail["etag"],
                "text": "Prioritize peer-reviewed and official sources.",
                "selectedOptionId": "option.peer-reviewed",
            },
            headers=self.write_headers(),
        )
        self.assertEqual(status, 200)
        self.assertEqual(answered["record"]["status"], "answered")
        self.assertEqual(answered["record"]["answer"]["resumedWorkIds"], [
            "work.map-foundations", "work.prepare-evidence",
        ])
        self.assertEqual([item["status"] for item in answered["affectedWork"]], ["ready", "ready"])
        event_count = len(self.runtime.store.verified_events(PROJECT_ID))
        status, retried_answer, _ = self.request(
            f"/api/projects/{PROJECT_ID}/inbox/question.source-policy/answers",
            method="POST",
            payload={
                "expectedEtag": detail["etag"],
                "text": "Prioritize peer-reviewed and official sources.",
                "selectedOptionId": "option.peer-reviewed",
            },
            headers=self.write_headers(),
        )
        self.assertEqual(status, 200)
        self.assertEqual(retried_answer["record"]["status"], "answered")
        self.assertEqual(len(self.runtime.store.verified_events(PROJECT_ID)), event_count)

        status, detail, _ = self.request(
            f"/api/projects/{PROJECT_ID}/inbox/question.ui-global-view",
        )
        self.assertEqual(status, 200)
        status, rejected, _ = self.request(
            f"/api/projects/{PROJECT_ID}/inbox/question.ui-global-view/closure",
            method="POST",
            payload={
                "expectedEtag": detail["etag"],
                "newStatus": "rejected",
                "reason": "Keep this project-first for the initial version.",
            },
            headers=self.write_headers(),
        )
        self.assertEqual(status, 200)
        self.assertEqual(rejected["record"]["status"], "rejected")
        self.assertEqual(rejected["affectedWork"][0]["status"], "ready")
        event_count = len(self.runtime.store.verified_events(PROJECT_ID))
        status, retried_rejection, _ = self.request(
            f"/api/projects/{PROJECT_ID}/inbox/question.ui-global-view/closure",
            method="POST",
            payload={
                "expectedEtag": detail["etag"],
                "newStatus": "rejected",
                "reason": "Keep this project-first for the initial version.",
            },
            headers=self.write_headers(),
        )
        self.assertEqual(status, 200)
        self.assertEqual(retried_rejection["record"]["status"], "rejected")
        self.assertEqual(len(self.runtime.store.verified_events(PROJECT_ID)), event_count)

        restarted = PrototypeRuntime(self.db_path, workspace_root=self.workspace_root)
        self.assertEqual(restarted.get_question(PROJECT_ID, "question.source-policy")["record"]["status"], "answered")
        self.assertEqual(restarted.get_question(PROJECT_ID, "question.ui-global-view")["record"]["status"], "rejected")
        counts = restarted.project_summary(PROJECT_ID)["counts"]
        self.assertEqual(counts["openQuestions"], 0)
        self.assertEqual(counts["resolvedQuestions"], 2)

    def test_stale_command_and_spoofed_actor_fail_without_extra_events(self):
        _, stale_detail, _ = self.request(f"/api/projects/{PROJECT_ID}/inbox/question.ui-global-view")
        self.runtime.control.revise_work(
            PROJECT_ID,
            "work.ui-foundation",
            {"title": "Revised UI foundation comparison"},
            actor={"actorType": "human", "actorId": "user.local-demo", "role": "user"},
            occurred_at="2026-11-08T12:00:00Z",
        )
        event_count = len(self.runtime.store.verified_events(PROJECT_ID))
        status, error, _ = self.request(
            f"/api/projects/{PROJECT_ID}/inbox/question.ui-global-view/closure",
            method="POST",
            payload={
                "expectedEtag": stale_detail["etag"],
                "newStatus": "rejected",
                "reason": "Keep the prototype project-first.",
            },
            headers=self.write_headers(),
        )
        self.assertEqual(status, 409)
        self.assertEqual(error["error"], "stale_revision")
        self.assertEqual(len(self.runtime.store.verified_events(PROJECT_ID)), event_count)

        _, detail, _ = self.request(f"/api/projects/{PROJECT_ID}/inbox/question.source-policy")
        status, error, _ = self.request(
            f"/api/projects/{PROJECT_ID}/inbox/question.source-policy/answers",
            method="POST",
            payload={
                "expectedEtag": detail["etag"],
                "text": "Use official sources.",
                "actor": {"actorType": "human", "actorId": "user.attacker", "role": "user"},
            },
            headers=self.write_headers(),
        )
        self.assertEqual(status, 422)
        self.assertEqual(error["error"], "invalid_request")
        self.assertEqual(len(self.runtime.store.verified_events(PROJECT_ID)), event_count)

    def test_invalid_command_fields_return_422_without_store_mutation(self):
        _, detail, _ = self.request(f"/api/projects/{PROJECT_ID}/inbox/question.source-policy")
        event_count = len(self.runtime.store.verified_events(PROJECT_ID))
        base_path = f"/api/projects/{PROJECT_ID}/inbox/question.source-policy/answers"
        for payload in (
            {"expectedEtag": "not-an-etag", "text": "Use official sources."},
            {"expectedEtag": detail["etag"], "text": "x" * 10_001},
        ):
            status, error, _ = self.request(
                base_path,
                method="POST",
                payload=payload,
                headers=self.write_headers(),
            )
            self.assertEqual(status, 422)
            self.assertEqual(error["error"], "invalid_request")
        self.assertEqual(len(self.runtime.store.verified_events(PROJECT_ID)), event_count)

    def test_loopback_host_origin_and_request_size_guards(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, error, _ = self.request("/api/health", headers={"Host": "attacker.example"})
        self.assertEqual(status, 403)
        self.assertEqual(error["error"], "host_not_allowed")

        status, error, _ = self.request(
            f"/api/projects/{PROJECT_ID}/inbox/question.source-policy/answers",
            method="POST",
            payload={"expectedEtag": "x", "text": "x"},
            headers={**self.write_headers(), "Origin": "https://attacker.example"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(error["error"], "origin_not_allowed")

        status, error, _ = self.request(
            f"/api/projects/{PROJECT_ID}/inbox/question.source-policy/answers",
            method="POST", payload={"expectedEtag": "x", "text": "x"},
            headers={"Origin": self.base_url},
        )
        self.assertEqual(status, 403)
        self.assertEqual(error["error"], "prototype_header_required")

        request = Request(
            f"{self.base_url}/api/projects/{PROJECT_ID}/inbox/question.source-policy/answers",
            data=b"{" + b" " * (16 * 1024),
            headers={
                "Origin": self.base_url,
                "X-Local-Prototype": "1",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=3)
        self.assertEqual(raised.exception.code, 413)
        self.assertEqual(json.loads(raised.exception.read())["error"], "request_too_large")

    def test_run_evidence_and_artifact_reads_use_project_scoped_application_facades(self):
        RunLedger(self.runtime.store, clock=lambda: CREATED_AT).start(
            PROJECT_ID, "work.ui-foundation", run_id="run.ui-api-read",
            agent=DEMO_RESEARCHER, adapter={"name": "offline-test", "version": "1"},
            context_hash="a" * 64, occurred_at=CREATED_AT,
        )
        source_bytes = b"synthetic-source-content-must-not-appear-in-api-response"
        EvidenceRegistrar(self.runtime.store, clock=lambda: CREATED_AT).register(
            PROJECT_ID, "work.ui-foundation", evidence_id="evidence.ui-api-read",
            claim_text="Synthetic claim for a read-scope test.", stance="supports",
            source_uri="https://example.test/source", source_title="Synthetic source",
            source_content=source_bytes, actor=DEMO_RESEARCHER, run_id="run.ui-api-read",
        )
        status, runs, _ = self.request(f"/api/v1/projects/{PROJECT_ID}/runs")
        self.assertEqual(status, 200)
        self.assertEqual([item["runId"] for item in runs["items"]], ["run.ui-api-read"])
        status, evidence, _ = self.request(f"/api/v1/projects/{PROJECT_ID}/evidence")
        self.assertEqual(status, 200)
        self.assertEqual(evidence["items"][0]["evidenceId"], "evidence.ui-api-read")
        self.assertNotIn(source_bytes.decode(), json.dumps(evidence))
        status, artifacts, _ = self.request(f"/api/v1/projects/{PROJECT_ID}/artifacts")
        self.assertEqual(status, 200)
        self.assertEqual(artifacts["items"], [])

    def test_built_react_ui_is_served_from_loopback_static_root(self):
        build_dir = Path(__file__).resolve().parents[1] / "build"
        if not (build_dir / "index.html").is_file():
            self.skipTest("build frontend with `cd ui/web && npm run build`")
        response = urlopen(f"{self.base_url}/", timeout=3)
        self.assertEqual(response.status, 200)
        self.assertIn("text/html", response.headers["Content-Type"])
        html = response.read().decode("utf-8")
        self.assertIn("Northstar · Projects", html)
        asset = html.split('src="', 1)[1].split('"', 1)[0]
        asset_response = urlopen(f"{self.base_url}{asset}", timeout=3)
        self.assertEqual(asset_response.status, 200)
        self.assertIn("javascript", asset_response.headers["Content-Type"])
        self.assertEqual(self.request("/assets/../../../../etc/passwd")[0], 404)

    def test_api_v1_is_explicit_and_unknown_api_versions_fail_closed(self):
        status, health, _ = self.request("/api/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["mode"], "local-project-prototype")
        status, error, _ = self.request("/api/v2/health")
        self.assertEqual(status, 404)
        self.assertEqual(error["error"], "not_found")

    def test_cross_project_and_unknown_paths_do_not_disclose_project_data(self):
        for collection in ("runs", "evidence", "artifacts"):
            status, error, _ = self.request(f"/api/v1/projects/project.other/{collection}")
            self.assertEqual(status, 403)
            self.assertEqual(error["error"], "project_access_denied")
        status, error, _ = self.request("/api/projects/project.other/inbox")
        self.assertEqual(status, 403)
        self.assertEqual(error["error"], "project_access_denied")
        status, error, _ = self.request("/not-a-static-file")
        self.assertEqual(status, 404)
        self.assertNotIn("Northstar", json.dumps(error))


if __name__ == "__main__":
    unittest.main()
