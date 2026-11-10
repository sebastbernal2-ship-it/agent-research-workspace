import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ui.prototype.local_server import build_server
from ui.prototype.runtime import PROJECT_ID, PrototypeRuntime


class LocalPrototypeHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "canonical.sqlite3"
        self.runtime = PrototypeRuntime(self.db_path)
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

        restarted = PrototypeRuntime(self.db_path)
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

    def test_api_v1_is_explicit_and_unknown_api_versions_fail_closed(self):
        status, health, _ = self.request("/api/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["mode"], "local-synthetic-demo")
        status, error, _ = self.request("/api/v2/health")
        self.assertEqual(status, 404)
        self.assertEqual(error["error"], "not_found")

    def test_cross_project_and_unknown_paths_do_not_disclose_project_data(self):
        status, error, _ = self.request("/api/projects/project.other/inbox")
        self.assertEqual(status, 403)
        self.assertEqual(error["error"], "project_access_denied")
        status, error, _ = self.request("/not-a-static-file")
        self.assertEqual(status, 404)
        self.assertNotIn("Northstar", json.dumps(error))


if __name__ == "__main__":
    unittest.main()
