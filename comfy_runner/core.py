"""Adapted from the source project's http_json / wait_for_image / generate_once."""

import copy
import json
import math
import os
import re
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path


class RunnerError(Exception):
    """An actionable workflow or protocol failure."""


def positive_number(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Expected a finite number greater than zero")
    return value


def load_workflow(path, overrides=()):
    workflow = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(workflow, dict) or not workflow:
        raise RunnerError("Workflow must be a nonempty API-format node map")
    for node_id, node in workflow.items():
        if (not isinstance(node, dict) or not isinstance(node.get("class_type"), str)
                or not node["class_type"] or not isinstance(node.get("inputs"), dict)):
            raise RunnerError(f"Invalid API node {node_id!r}; export API format, not UI format")
    workflow = copy.deepcopy(workflow)
    for node_id, input_name, value in overrides:
        if node_id not in workflow or input_name not in workflow[node_id]["inputs"]:
            raise RunnerError(f"Unknown node/input: {node_id}.{input_name}")
        workflow[node_id]["inputs"][input_name] = json.loads(value)
    return workflow


def safe_component(value):
    # Use the intersection of Windows and POSIX filename rules, on every host.
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or value.endswith((".", " ")) or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
            or value.split(".")[0].upper() in
            {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
             *(f"LPT{i}" for i in range(10))}):
        raise RunnerError(f"Unsafe filename component: {value!r}")
    return value


def image_metadata(value):
    if not isinstance(value, dict):
        raise RunnerError("Invalid image metadata")
    filename = safe_component(value.get("filename"))
    subfolder = value.get("subfolder", "")
    if not isinstance(subfolder, str) or "\\" in subfolder:
        raise RunnerError("Invalid image subfolder")
    if subfolder:
        for part in subfolder.split("/"):
            safe_component(part)
    kind = value.get("type", "output")
    if kind not in {"output", "temp", "input"}:
        raise RunnerError("Invalid image type")
    return {"filename": filename, "subfolder": subfolder, "type": kind}


def target_path(root, name):
    root = Path(root).resolve()
    path = root / safe_component(name)
    if path.is_symlink() or path.resolve().parent != root:
        raise RunnerError(f"Output escapes its directory: {name}")
    return path


def save_bytes(path, data, overwrite=False):
    path = Path(path)
    if path.is_symlink():
        raise RunnerError(f"Refusing symlink: {path}")
    if not overwrite:
        with path.open("xb") as handle:
            try:
                handle.write(data)
            except BaseException:
                handle.close()
                path.unlink()
                raise
        return
    # Replace only after a complete write; never truncate an existing result.
    fd, temporary = tempfile.mkstemp(prefix=".comfy-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RunnerError("API redirects are disabled; specify the final API URL")


class Client:
    def __init__(self, api_url="http://127.0.0.1:8188", request_timeout=30):
        parts = urllib.parse.urlsplit(api_url)
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment):
            raise RunnerError("API URL must be http(s), without credentials, query or fragment")
        try:
            parts.port
        except ValueError as exc:
            raise RunnerError("Invalid API port") from exc
        self.api_url = api_url.rstrip("/")
        self.request_timeout = positive_number(request_timeout)
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, endpoint, payload=None, timeout=None, binary=False):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.api_url + endpoint, data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        limit = 256 * 1024 * 1024 if binary else 16 * 1024 * 1024
        with self.opener.open(req, timeout=timeout or self.request_timeout) as response:
            body = response.read(limit + 1)
        if len(body) > limit:
            raise RunnerError("API response exceeded size limit")
        if binary:
            return body
        result = json.loads(body.decode("utf-8"))
        if not isinstance(result, dict):
            raise RunnerError("Expected a JSON object from API")
        return result

    def submit(self, workflow):
        result = self.request("/prompt", {"prompt": workflow})
        if result.get("error") or result.get("node_errors"):
            raise RunnerError(f"Workflow rejected: {result}")
        prompt_id = result.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise RunnerError("API did not return a prompt_id")
        return prompt_id

    def wait(self, prompt_id, timeout=1200, poll_interval=2, stop_file=None):
        if not isinstance(prompt_id, str) or not prompt_id:
            raise RunnerError("prompt_id must be a nonempty string")
        deadline = time.monotonic() + positive_number(timeout)
        interval = positive_number(poll_interval)
        endpoint = "/history/" + urllib.parse.quote(prompt_id, safe="")
        while True:
            if stop_file and Path(stop_file).exists():
                raise RunnerError("Stop file detected; server job was NOT cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RunnerError(f"Timed out waiting for {prompt_id}; server job may still run")
            history = self.request(endpoint, timeout=min(self.request_timeout, remaining))
            if prompt_id in history:
                entry = history[prompt_id]
                if not isinstance(entry, dict):
                    raise RunnerError("Invalid history entry")
                status = entry.get("status", {})
                if not isinstance(status, dict):
                    raise RunnerError("Invalid execution status")
                messages = status.get("messages", [])
                if (status.get("status_str") == "error" or any(
                    isinstance(message, (list, tuple)) and message
                    and message[0] in {"execution_error", "execution_interrupted"}
                    for message in messages
                )):
                    raise RunnerError(f"Execution failed for {prompt_id}: {status}")
                if status.get("completed") is True:
                    return entry
            time.sleep(min(interval, max(0, deadline - time.monotonic())))

    def download(self, entry, output_dir, prefix="0001", overwrite=False):
        root = Path(output_dir).resolve()
        safe_component(prefix)
        outputs = entry.get("outputs", {})
        if not isinstance(outputs, dict):
            raise RunnerError("Invalid history outputs")
        planned = []
        for node_index, node in enumerate(outputs.values(), 1):
            if not isinstance(node, dict) or not isinstance(node.get("images", []), list):
                raise RunnerError("Invalid output node")
            for image_index, raw in enumerate(node.get("images", []), 1):
                metadata = image_metadata(raw)
                name = f"{prefix}_{node_index:04d}_{image_index:04d}_{metadata['filename']}"
                planned.append((target_path(root, name), metadata))
        if not planned:
            raise RunnerError("Completed workflow has no downloadable images")
        manifest_path = target_path(root, f"{prefix}_manifest.json")
        for path in [manifest_path, *(path for path, _ in planned)]:
            if path.exists() and not overwrite:
                raise FileExistsError(f"Output exists (use --overwrite): {path}")
        root.mkdir(parents=True, exist_ok=True)
        saved = []
        for path, metadata in planned:
            query = urllib.parse.urlencode(metadata)
            body = self.request("/view?" + query, binary=True)
            save_bytes(path, body, overwrite)
            saved.append({"path": str(path), "source": metadata})
        save_bytes(manifest_path, json.dumps({"images": saved}, indent=2).encode("utf-8"), overwrite)
        return saved
