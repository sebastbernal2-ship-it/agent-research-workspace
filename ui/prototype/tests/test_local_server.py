import json
import subprocess
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
        self.assertEqual(created["project"]["charter"]["acceptanceCriteria"], [])
        self.assertEqual(created["project"]["charter"]["nonGoals"], [])
        project_directory = self.workspace_root / project_id
        self.assertTrue(project_directory.is_dir())
        self.assertEqual(project_directory.stat().st_mode & 0o777, 0o700)
        status, collection, _ = self.request("/api/v1/projects")
        self.assertEqual(status, 200)
        self.assertIn(project_id, {project["projectId"] for project in collection["items"]})

        status, work, _ = self.request(
            f"/api/v1/projects/{project_id}/work", method="POST",
            payload={"title": "Explore the question", "objective": "Agree the research outcome before choosing implementation steps.", "workType": "research"},
            headers=self.write_headers(),
        )
        self.assertEqual(status, 201)
        self.assertEqual(work["workItem"]["status"], "proposed")
        parent_id = work["workItem"]["workId"]
        status, child, _ = self.request(
            f"/api/v1/projects/{project_id}/work", method="POST",
            payload={
                "title": "Audit current evidence", "objective": "Separate verified facts from unsupported claims.",
                "workType": "analysis", "parentWorkId": parent_id,
                "acceptanceCriteria": ["Every finding links to a repository artifact."],
            },
            headers=self.write_headers(),
        )
        self.assertEqual(status, 201)
        self.assertEqual(child["workItem"]["parentWorkId"], parent_id)
        self.assertEqual(child["workItem"]["acceptanceCriteria"], ["Every finding links to a repository artifact."])
        summary_status, summary, _ = self.request(f"/api/v1/projects/{project_id}")
        self.assertEqual(summary_status, 200)
        self.assertEqual(len(summary["workItems"]), 2)
        self.assertEqual(summary["workItems"][1]["parentWorkId"], parent_id)
        restarted = PrototypeRuntime(self.db_path, workspace_root=self.workspace_root)
        self.assertEqual(restarted.project_application.get_project(project_id)["name"], "My WSL project")
        reopened_work = restarted.project_application.list_work(project_id)
        self.assertEqual(reopened_work[0]["title"], "Explore the question")
        self.assertEqual(reopened_work[1]["parentWorkId"], parent_id)
        self.assertTrue(restarted.store.verify(project_id))

    def test_existing_project_inspection_starts_a_durable_sequential_alignment_interview(self):
        repo = self.workspace_root.parent / "trump-speech-predictor-fixture"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test User"], check=True)
        (repo / "README.md").write_text("# Speech predictor\n\nForecast future speech topics from reviewed historical evidence.\n", encoding="utf-8")
        (repo / "docs").mkdir()
        (repo / "docs" / "scope.md").write_text("# Scope\n\nRetrospective research is supported; live markets are undecided.\n", encoding="utf-8")
        (repo / ".env").write_text("API_KEY=do-not-read-sentinel\n", encoding="utf-8")
        (repo / "data").mkdir()
        (repo / "data" / "bulk.csv").write_text("do not read bulk sentinel\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "README.md", "docs", ".env"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "fixture"], check=True, capture_output=True)

        status, inspected, _ = self.request(
            "/api/v1/repositories/inspect", method="POST",
            payload={"repositoryPath": str(repo)}, headers=self.write_headers(),
        )
        self.assertEqual(status, 200)
        report = inspected["report"]
        self.assertNotIn("do-not-read-sentinel", json.dumps(report))
        self.assertNotIn("do not read bulk sentinel", json.dumps(report))
        self.assertEqual(len(report["documents"]), 2)

        status, created, _ = self.request(
            "/api/v1/projects", method="POST",
            payload={
                "mode": "existing", "name": "My predictor", "goal": "Forecast future speech topics.",
                "repositoryPath": str(repo), "inspectionFingerprint": report["fingerprint"],
            }, headers=self.write_headers(),
        )
        self.assertEqual(status, 201, created)
        project_id = created["project"]["projectId"]
        summary_status, summary, _ = self.request(f"/api/v1/projects/{project_id}")
        self.assertEqual(summary_status, 200)
        self.assertEqual([item["workId"] for item in summary["workItems"]], ["work.alignment-intake"])
        self.assertEqual(summary["counts"]["openQuestions"], 1)

        for index in range(1, 6):
            status, question, _ = self.request(f"/api/v1/projects/{project_id}/inbox/question.alignment.{index:02d}")
            self.assertEqual(status, 200)
            self.assertIn(f"{index} of 5", question["questionText"])
            answer_payload = {"expectedEtag": question["etag"], "text": f"Owner's answer for segment {index}."}
            if index == 1:
                answer_payload.update({
                    "selectedOptionId": "option.alignment.replace-goal",
                    "text": "Replace it with my response\n\nNote: The confirmed goal is to produce reviewed forecasts from speech transcripts.",
                })
            status, _, _ = self.request(
                f"/api/v1/projects/{project_id}/inbox/question.alignment.{index:02d}/answers",
                method="POST", payload=answer_payload, headers=self.write_headers(),
            )
            self.assertEqual(status, 200)
            if index == 1:
                restarted = PrototypeRuntime(self.db_path, workspace_root=self.workspace_root)
                sequence_before_resume = restarted.projector.replay(project_id)["_eventSequence"]
                restarted.resume_alignment(project_id)
                self.assertEqual(restarted.projector.replay(project_id)["_eventSequence"], sequence_before_resume)
            if index < 5:
                next_status, next_questions, _ = self.request(f"/api/v1/projects/{project_id}/inbox?status=open")
                self.assertEqual(next_status, 200)
                self.assertEqual([item["questionId"] for item in next_questions["questions"]], [f"question.alignment.{index + 1:02d}"])

        final_status, final_summary, _ = self.request(f"/api/v1/projects/{project_id}")
        self.assertEqual(final_status, 200)
        self.assertEqual(final_summary["counts"]["openQuestions"], 0)
        self.assertEqual(final_summary["workItems"][0]["status"], "completed")
        self.assertEqual(final_summary["project"]["charter"]["goals"], ["The confirmed goal is to produce reviewed forecasts from speech transcripts."])
        self.assertEqual(final_summary["alignment"]["status"], "completed")
        self.assertIn("Owner's answer for segment 2", final_summary["alignment"]["segments"][1]["answer"]["text"])
        self.assertTrue(self.runtime.store.verify(project_id))
        messages = self.runtime.project_application.list_messages(project_id)
        self.assertIn("interview completed", messages[-1]["content"])

    def test_proposed_work_requires_per_item_approval_to_become_ready(self):
        status, created, _ = self.request(
            "/api/v1/projects", method="POST",
            payload={"name": "Approval test", "goal": "Review work before starting."}, headers=self.write_headers(),
        )
        self.assertEqual(status, 201)
        project_id = created["project"]["projectId"]
        status, work, _ = self.request(
            f"/api/v1/projects/{project_id}/work", method="POST",
            payload={"title": "Proposed step", "objective": "A test proposal."}, headers=self.write_headers(),
        )
        item = work["workItem"]
        self.assertEqual(item["status"], "proposed")
        status, approved, _ = self.request(
            f"/api/v1/projects/{project_id}/work/{item['workId']}/approval", method="POST",
            payload={"expectedRevision": item["revision"], "decision": "approve"}, headers=self.write_headers(),
        )
        self.assertEqual(status, 200)
        self.assertEqual(approved["workItem"]["status"], "ready")
        self.assertIsNone(approved["workItem"].get("finishedAt"))
        stale_status, _, _ = self.request(
            f"/api/v1/projects/{project_id}/work/{item['workId']}/approval", method="POST",
            payload={"expectedRevision": item["revision"], "decision": "approve"}, headers=self.write_headers(),
        )
        self.assertEqual(stale_status, 409)

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
