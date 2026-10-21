import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from comfy_runner.cli import main
from comfy_runner.core import (
    Client, NoRedirect, RunnerError, image_metadata, load_workflow,
    positive_number, safe_component, save_bytes, target_path,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "solid-color.api.json"
IMAGE = {"filename": "image.png", "subfolder": "nested/results", "type": "output"}
ENTRY = {"status": {"completed": True, "status_str": "success"},
         "outputs": {"2": {"images": [IMAGE]}}}


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_example_and_override(self):
        changed = load_workflow(EXAMPLE, [("1", "width", "512")])
        self.assertEqual(changed["1"]["inputs"]["width"], 512)
        self.assertEqual(load_workflow(EXAMPLE)["1"]["inputs"]["width"], 256)

    def test_unknown_override_rejected(self):
        with self.assertRaises(RunnerError):
            load_workflow(EXAMPLE, [("missing", "width", "1")])

    def test_ui_format_and_invalid_json_rejected(self):
        for text in ('{"nodes": []}', '[]', '{}', '{"1": {}}', '{bad'):
            with self.subTest(text=text):
                path = self.root / "workflow.json"
                save_bytes(path, text.encode(), overwrite=True)
                with self.assertRaises((RunnerError, ValueError)):
                    load_workflow(path)

    def test_unsafe_names_on_all_platforms(self):
        for name in ("../x", "a/b", "a\\b", "C:x", ".", "..", "", "NUL.png",
                     "con", "COM1.txt", "x.", "x ", "a\0b", "a?b"):
            with self.subTest(name=name), self.assertRaises(RunnerError):
                safe_component(name)
        self.assertEqual(safe_component("plain_image-1.png"), "plain_image-1.png")

    def test_bad_metadata(self):
        for value in (None, {**IMAGE, "filename": "../x"},
                      {**IMAGE, "subfolder": "/absolute"},
                      {**IMAGE, "subfolder": "nested/../escape"},
                      {**IMAGE, "subfolder": "C:\\escape"},
                      {**IMAGE, "type": "unknown"}):
            with self.subTest(value=value), self.assertRaises(RunnerError):
                image_metadata(value)
        self.assertEqual(image_metadata(IMAGE), IMAGE)

    def test_exclusive_write_and_explicit_overwrite(self):
        path = self.root / "image.png"
        save_bytes(path, b"original")
        with self.assertRaises(FileExistsError):
            save_bytes(path, b"changed")
        self.assertEqual(path.read_bytes(), b"original")
        save_bytes(path, b"changed", overwrite=True)
        self.assertEqual(path.read_bytes(), b"changed")

    def test_symlink_rejected(self):
        original = self.root / "original"
        save_bytes(original, b"original")
        link = self.root / "link.png"
        try:
            link.symlink_to(original)
        except OSError:
            self.skipTest("Host does not permit symlink creation")
        with self.assertRaises(RunnerError):
            target_path(self.root, link.name)
        with self.assertRaises(RunnerError):
            save_bytes(link, b"changed", overwrite=True)
        self.assertEqual(original.read_bytes(), b"original")

    def test_symlink_guard_without_host_privileges(self):
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaises(RunnerError):
                target_path(self.root, "image.png")
            with self.assertRaises(RunnerError):
                save_bytes(self.root / "image.png", b"data", overwrite=True)

    def test_timing_validation(self):
        for value in (0, -1, "nan", "inf"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                positive_number(value)

    def test_download_multiple_images_and_manifest(self):
        client = Client()
        entry = {"outputs": {"2": {"images": [IMAGE, IMAGE]}}}
        with patch.object(client, "request", return_value=b"mock image bytes") as request:
            saved = client.download(entry, self.root)
        self.assertEqual(len(saved), 2)
        self.assertNotEqual(saved[0]["path"], saved[1]["path"])
        self.assertIn("subfolder=nested%2Fresults", request.call_args.args[0])
        self.assertEqual(Path(saved[0]["path"]).read_bytes(), b"mock image bytes")
        self.assertEqual(json.loads((self.root / "0001_manifest.json").read_text())["images"], saved)

    def test_collision_checked_before_download(self):
        save_bytes(self.root / "0001_manifest.json", b"keep")
        client = Client()
        with patch.object(client, "request") as request:
            with self.assertRaises(FileExistsError):
                client.download(ENTRY, self.root)
            request.assert_not_called()

    def test_unsafe_download_checked_before_http(self):
        client = Client()
        entry = {"outputs": {"2": {"images": [{**IMAGE, "filename": "../escape"}]}}}
        with patch.object(client, "request") as request:
            with self.assertRaises(RunnerError):
                client.download(entry, self.root)
            request.assert_not_called()

    def test_completed_without_images_is_failure(self):
        with self.assertRaises(RunnerError):
            Client().download({"outputs": {}}, self.root)

    def test_download_overwrite_opt_in(self):
        client = Client()
        with patch.object(client, "request", return_value=b"first"):
            saved = client.download(ENTRY, self.root)
        with patch.object(client, "request", return_value=b"second"):
            client.download(ENTRY, self.root, overwrite=True)
        self.assertEqual(Path(saved[0]["path"]).read_bytes(), b"second")

    def test_failed_download_does_not_replace_old_image(self):
        client = Client()
        with patch.object(client, "request", return_value=b"first"):
            saved = client.download(ENTRY, self.root)
        with patch.object(client, "request", side_effect=TimeoutError("offline")):
            with self.assertRaises(TimeoutError):
                client.download(ENTRY, self.root, overwrite=True)
        self.assertEqual(Path(saved[0]["path"]).read_bytes(), b"first")

    def test_poll_stop_file_prevents_http(self):
        stop = self.root / "STOP"
        save_bytes(stop, b"")
        client = Client()
        with patch.object(client, "request") as request:
            with self.assertRaisesRegex(RunnerError, "NOT cancelled"):
                client.wait("job", stop_file=stop)
            request.assert_not_called()

    def test_cli_validate_offline(self):
        with patch("urllib.request.OpenerDirector.open") as request, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["validate", str(EXAMPLE)]), 0)
            request.assert_not_called()

    def test_cli_count_and_stop(self):
        argv = ["run", str(EXAMPLE), "--output-dir", str(self.root)]
        with patch("comfy_runner.cli.Client") as cls, contextlib.redirect_stdout(io.StringIO()):
            client = cls.return_value
            client.submit.return_value = "job"
            client.wait.return_value = ENTRY
            client.download.return_value = []
            with patch("comfy_runner.cli.time.sleep"):
                self.assertEqual(main([*argv, "--count", "2"]), 0)
            self.assertEqual(client.submit.call_count, 2)
            self.assertEqual(client.download.call_args.args[2], "0002")
        stop = self.root / "STOP"
        save_bytes(stop, b"")
        with patch("comfy_runner.cli.Client") as cls, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([*argv, "--stop-file", str(stop)]), 1)
            cls.return_value.submit.assert_not_called()

    def test_cli_fetch_no_submission(self):
        with patch("comfy_runner.cli.Client") as cls, contextlib.redirect_stdout(io.StringIO()):
            cls.return_value.download.return_value = []
            self.assertEqual(main(["fetch", "existing-id", "--output-dir", str(self.root)]), 0)
            cls.return_value.submit.assert_not_called()
            self.assertEqual(cls.return_value.wait.call_args.args[0], "existing-id")

    def test_cli_bad_count(self):
        with patch("comfy_runner.cli.Client") as cls, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["run", str(EXAMPLE), "--output-dir", str(self.root), "--count", "0"]), 1)
            cls.assert_not_called()

    def test_cli_network_failure(self):
        from urllib.error import URLError
        with patch("comfy_runner.cli.Client") as cls, contextlib.redirect_stderr(io.StringIO()):
            cls.return_value.submit.side_effect = URLError("offline")
            self.assertEqual(main(["run", str(EXAMPLE), "--output-dir", str(self.root)]), 1)
            self.assertEqual(cls.return_value.submit.call_count, 1)

    def test_all_help_commands(self):
        for command in ([], ["run"], ["fetch"], ["validate"]):
            with self.subTest(command=command):
                result = subprocess.run([sys.executable, "-m", "comfy_runner", *command, "--help"],
                                        cwd=ROOT, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout)


class HttpTests(unittest.TestCase):
    def test_http_submission_contract(self):
        client = Client(request_timeout=7)
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"prompt_id":"job"}'
        with patch.object(client.opener, "open", return_value=response) as request:
            workflow = load_workflow(EXAMPLE)
            self.assertEqual(client.submit(workflow), "job")
        req = request.call_args.args[0]
        self.assertEqual(req.full_url, "http://127.0.0.1:8188/prompt")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(json.loads(req.data), {"prompt": workflow})
        self.assertEqual(request.call_args.kwargs["timeout"], 7)

    def test_bad_submission_responses(self):
        client = Client()
        for response in ({}, {"prompt_id": 3}, {"error": "invalid"},
                         {"prompt_id": "job", "node_errors": {"1": "bad input"}}):
            with self.subTest(response=response), patch.object(client, "request", return_value=response):
                with self.assertRaises(RunnerError):
                    client.submit({})

    def test_poll_pending_incomplete_then_success(self):
        client = Client()
        pending = {"job": {"outputs": {}, "status": {"completed": False}}}
        with patch.object(client, "request", side_effect=[{}, pending, {"job": ENTRY}]) as request:
            with patch("comfy_runner.core.time.sleep"):
                self.assertEqual(client.wait("job"), ENTRY)
        self.assertEqual(request.call_count, 3)

    def test_execution_errors_and_interrupts(self):
        client = Client()
        for status in ({"status_str": "error"},
                       {"completed": True, "messages": [["execution_error", {}]]},
                       {"messages": [["execution_interrupted", {}]]}):
            with self.subTest(status=status), patch.object(client, "request", return_value={"job": {"status": status}}):
                with self.assertRaises(RunnerError):
                    client.wait("job")

    def test_timeout_bounds_request(self):
        client = Client(request_timeout=30)
        with patch("comfy_runner.core.time.monotonic", side_effect=[0, 0, 1, 2]):
            with patch("comfy_runner.core.time.sleep"), patch.object(client, "request", return_value={}) as request:
                with self.assertRaisesRegex(RunnerError, "Timed out"):
                    client.wait("job", timeout=1)
        self.assertEqual(request.call_args.kwargs["timeout"], 1)

    def test_url_validation_and_prompt_encoding(self):
        for url in ("file:///etc/passwd", "http://user:password@localhost", "http://localhost/?q=1",
                    "http://localhost/#fragment", "http://localhost:wrong"):
            with self.subTest(url=url), self.assertRaises(RunnerError):
                Client(url)
        client = Client()
        with patch.object(client, "request", return_value={"a/b?c": ENTRY}) as request:
            client.wait("a/b?c")
        self.assertEqual(request.call_args.args[0], "/history/a%2Fb%3Fc")

    def test_redirect_refused(self):
        with self.assertRaises(RunnerError):
            NoRedirect().redirect_request(None, None, 302, "moved", {}, "http://elsewhere")

    def test_malformed_api_json(self):
        client = Client()
        for body in (b"[]", b"bad json"):
            response = MagicMock()
            response.__enter__.return_value.read.return_value = body
            with self.subTest(body=body), patch.object(client.opener, "open", return_value=response):
                with self.assertRaises((RunnerError, ValueError)):
                    client.request("/history/job")

    def test_http_get_and_binary_response(self):
        client = Client()
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"image bytes"
        with patch.object(client.opener, "open", return_value=response) as request:
            self.assertEqual(client.request("/view?filename=image.png", binary=True), b"image bytes")
        self.assertEqual(request.call_args.args[0].get_method(), "GET")

    def test_oversize_response_rejected(self):
        client = Client()
        response = MagicMock()
        body = MagicMock()
        body.__len__.return_value = 16 * 1024 * 1024 + 1
        response.__enter__.return_value.read.return_value = body
        with patch.object(client.opener, "open", return_value=response):
            with self.assertRaisesRegex(RunnerError, "size limit"):
                client.request("/history/job")

    def test_empty_prompt_id_rejected_before_http(self):
        client = Client()
        with patch.object(client, "request") as request:
            with self.assertRaises(RunnerError):
                client.wait("")
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
