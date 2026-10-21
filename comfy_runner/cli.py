"""CLI retaining the source scripts' finite sequential generation loop."""

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import URLError

from .core import Client, RunnerError, load_workflow, positive_number


def parser():
    result = argparse.ArgumentParser(description="Submit portable ComfyUI API workflows")
    commands = result.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Check API JSON locally; no network")
    validate.add_argument("workflow", type=Path)
    validate.add_argument("--set", nargs=3, action="append", default=[],
                          metavar=("NODE", "INPUT", "JSON"))
    for name in ("run", "fetch"):
        command = commands.add_parser(name, help="Submit and collect" if name == "run" else "Collect an existing prompt")
        command.add_argument("--api-url", default="http://127.0.0.1:8188")
        command.add_argument("--request-timeout", type=positive_number, default=30)
        command.add_argument("--timeout", type=positive_number, default=1200,
                             help="History wait deadline per job, seconds")
        command.add_argument("--poll-interval", type=positive_number, default=2)
        command.add_argument("--output-dir", type=Path, required=True)
        command.add_argument("--overwrite", action="store_true")
        command.add_argument("--stop-file", type=Path)
        if name == "run":
            command.add_argument("workflow", type=Path)
            command.add_argument("--set", nargs=3, action="append", default=[],
                                 metavar=("NODE", "INPUT", "JSON"))
            command.add_argument("--count", type=int, default=1)
            command.add_argument("--interval", type=positive_number, default=1)
        else:
            command.add_argument("prompt_id")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "validate":
            workflow = load_workflow(args.workflow, args.set)
            print(f"Valid API structure: {len(workflow)} nodes (server compatibility not checked)")
            return 0
        workflow = None
        count = 1
        if args.command == "run":
            if args.count < 1:
                raise RunnerError("--count must be at least 1")
            count = args.count
            workflow = load_workflow(args.workflow, args.set)
        client = Client(args.api_url, args.request_timeout)
        for index in range(1, count + 1):
            if args.stop_file and args.stop_file.exists():
                raise RunnerError("Stop file detected; nothing further submitted")
            prompt_id = client.submit(workflow) if workflow is not None else args.prompt_id
            print(f"prompt_id={prompt_id}", flush=True)
            entry = client.wait(prompt_id, args.timeout, args.poll_interval, args.stop_file)
            saved = client.download(entry, args.output_dir, f"{index:04d}", args.overwrite)
            print(json.dumps({"prompt_id": prompt_id, "images": saved}, ensure_ascii=True), flush=True)
            if index < count:
                time.sleep(args.interval)
        return 0
    except (RunnerError, OSError, ValueError, TypeError, URLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; submitted server jobs were NOT cancelled", file=sys.stderr)
        return 130
