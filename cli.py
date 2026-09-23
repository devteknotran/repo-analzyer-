#!/usr/bin/env python3
"""
repo-cloud-analyzer
Offline repo scanner: detects framework(s), dependencies, and infra signals,
then recommends an AWS/Azure/GCP deployment target with reasoning, plus a
concrete containerization plan (Dockerfile, docker-compose, cloud-managed
service equivalents).

No API keys required. The only network call this tool ever makes is a plain
`git clone`, and only if you pass a repo URL instead of a local path.

Usage:
    python3 cli.py /path/to/repo [--json out.json] [--md out.md]
    python3 cli.py https://github.com/org/repo.git [--json out.json] [--md out.md]
    python3 cli.py git@github.com:org/repo.git --keep-clone
    python3 cli.py /path/to/repo --plan-dir ./container-plan
"""
import argparse
import sys

import core
import report


def main():
    parser = argparse.ArgumentParser(description="Repo -> cloud deployment + containerization analyzer. No API tokens needed; a repo URL triggers a plain 'git clone', nothing else.")
    parser.add_argument("repo_path", help="Local path OR a git URL (https://, git@, ssh://) to analyze")
    parser.add_argument("--json", dest="json_out", default=None, help="Write JSON report to this path")
    parser.add_argument("--md", dest="md_out", default=None, help="Write Markdown report to this path")
    parser.add_argument("--keep-clone", action="store_true", help="If a URL was passed, keep the cloned copy instead of deleting it after analysis")
    parser.add_argument("--plan-dir", dest="plan_dir", default=None, help="Write generated Dockerfile(s), docker-compose.yml, and a SERVICES.md into this directory")
    args = parser.parse_args()

    if core.gitutil.is_repo_url(args.repo_path):
        print(f"Cloning {core.gitutil.mask_url(args.repo_path)} ...")

    try:
        analysis = core.analyze_repo(args.repo_path, keep_clone=args.keep_clone)
    except ValueError as e:
        raise SystemExit(f"Error: {e}")

    if analysis.get("cloned_from"):
        kept = analysis.get("clone_kept_at")
        print(f"Cloned from {analysis['cloned_from']}" + (f", kept at {kept}" if kept else " (temp clone removed)"))

    md = report.to_markdown(analysis)

    if args.json_out:
        report.to_json(analysis, args.json_out)
        print(f"JSON report written to {args.json_out}")
    if args.md_out:
        with open(args.md_out, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"Markdown report written to {args.md_out}")

    if not args.json_out and not args.md_out:
        print(md)

    if args.plan_dir:
        core.write_plan_files(analysis, args.plan_dir)
        print(f"Containerization plan written to {args.plan_dir}/")


if __name__ == "__main__":
    sys.exit(main())
