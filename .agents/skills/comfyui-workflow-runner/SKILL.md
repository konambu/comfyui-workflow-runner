---
name: comfyui-workflow-runner
description: Run and collect ComfyUI API workflows with this repository's Python CLI, including input overrides and recovery of an existing prompt.
---

# ComfyUI Workflow Runner

Locate the repository containing `comfy_runner/` and read its README for the current CLI contract. Run commands from that root; a copied skill must locate the checkout rather than assume a machine-specific path.

Use API-format workflow JSON keyed by node ID. The editor's nodes/links document is a different format. Start with offline validation:

```sh
python -m comfy_runner validate examples/solid-color.api.json
```

For a user-selected workflow, inspect checkpoint names, custom nodes, output nodes, and server-side save paths before submitting. Offline validation only checks structure; it cannot establish server compatibility. Select the user's running endpoint; the default is `http://127.0.0.1:8188`.

```sh
python -m comfy_runner run workflow.api.json --output-dir output/run-01 --count 1 --timeout 1200 --poll-interval 5
```

Override an input with `--set NODE INPUT JSON`, quoting the JSON value for the current shell. Use a finite `--count`. Keep the returned prompt ID: after a client interruption, inspect or fetch that job before submitting another one.

```sh
python -m comfy_runner fetch PROMPT_ID --output-dir output/recovered --poll-interval 5
```

The stop file and Ctrl-C stop the client; they do not cancel work already queued on the server. Prefer a fresh output directory for recovery. `--overwrite` controls downloaded images only, not paths written by workflow nodes on the server.

Report the prompt ID, collected files, endpoint, and any failed stage. Distinguish mock HTTP tests, offline JSON validation, and an actual generated result. Do not install custom nodes or download weights solely to make a test pass without considering the user's requested environment and resource limits.
