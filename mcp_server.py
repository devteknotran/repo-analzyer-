#!/usr/bin/env python3
"""
mcp_server.py
Exposes repo_cloud_analyzer as an MCP server so Claude (Desktop, Code, or
any MCP client) can call it directly instead of you running the CLI by hand.

Same engine as cli.py — both call into core.py, so there is exactly one
analysis pipeline, not two copies that can drift apart.

Still zero API tokens for the analysis itself. The only network activity is
the same plain `git clone` the CLI does, and only when you pass a repo URL.

Run directly for a quick manual check:
    python3 mcp_server.py

Wire into Claude Desktop / Claude Code by adding to their MCP config
(see README.md for the exact JSON block and paths).
"""
from mcp.server.fastmcp import FastMCP

import core
import report

mcp = FastMCP("repo-cloud-analyzer")


@mcp.tool()
def analyze_repository(repo_path_or_url: str, keep_clone: bool = False) -> str:
    """
    Analyzes a repository (local path or git URL — GitHub/GitLab/Bitbucket,
    cloud or self-hosted) and returns a Markdown report: detected
    framework(s) and dependencies, infra signals (Docker/CI/IaC/health
    checks), a port-based-routing warning if found, and a recommended
    AWS/Azure/GCP deployment target with reasoning.

    For a private repo, set GITHUB_TOKEN / GITLAB_TOKEN / BITBUCKET_TOKEN
    (or GIT_TOKEN for self-hosted) as environment variables before starting
    this server — same as the CLI, credentials are never returned in output.

    Args:
        repo_path_or_url: Local filesystem path, or a git URL to clone.
        keep_clone: If a URL was passed, keep the cloned copy on disk
            instead of deleting it after analysis (default: deletes it).
    """
    try:
        analysis = core.analyze_repo(repo_path_or_url, keep_clone=keep_clone)
    except ValueError as e:
        return f"Error: {e}"
    return report.to_markdown(analysis)


@mcp.tool()
def generate_containerization_plan(repo_path_or_url: str, output_dir: str, keep_clone: bool = False) -> str:
    """
    Analyzes a repository and writes an actual containerization plan to
    disk: one Dockerfile per detected language/framework, a merged
    docker-compose.yml wiring the app service(s) to every detected
    auxiliary service (database/cache/queue/search), and a SERVICES.md
    mapping each auxiliary service to its AWS/Azure/GCP managed equivalent.

    Args:
        repo_path_or_url: Local filesystem path, or a git URL to clone.
        output_dir: Directory to write Dockerfile(s)/docker-compose.yml/SERVICES.md into.
        keep_clone: If a URL was passed, keep the cloned copy on disk.
    """
    try:
        analysis = core.analyze_repo(repo_path_or_url, keep_clone=keep_clone)
    except ValueError as e:
        return f"Error: {e}"

    core.write_plan_files(analysis, output_dir)

    plan = analysis["containerization_plan"]
    written = [f"Dockerfile.{eco.split(' ')[0].split('/')[0].replace('.', '')}" for eco in plan["dockerfiles"]]
    if plan["docker_compose"]:
        written.append("docker-compose.yml")
    written.append("SERVICES.md")

    aux_summary = ", ".join(s["name"] for s in plan["auxiliary_services"]) or "none detected"
    return (
        f"Wrote {', '.join(written)} to {output_dir}/\n"
        f"Detected auxiliary services: {aux_summary}\n"
        f"Recommended platform: {analysis['recommendation']['recommended_platform']}"
    )


@mcp.tool()
def get_deployment_recommendation(repo_path_or_url: str, keep_clone: bool = False) -> str:
    """
    Analyzes a repository and returns just the cloud deployment
    recommendation (EC2/ECS/EKS on AWS, with Azure/GCP equivalents) and
    the reasoning/warnings behind it — without the full framework and
    dependency detail. Useful when you already know the codebase and just
    want the platform call.

    Args:
        repo_path_or_url: Local filesystem path, or a git URL to clone.
        keep_clone: If a URL was passed, keep the cloned copy on disk.
    """
    try:
        analysis = core.analyze_repo(repo_path_or_url, keep_clone=keep_clone)
    except ValueError as e:
        return f"Error: {e}"

    rec = analysis["recommendation"]
    lines = [f"Recommended platform (AWS-first): {rec['recommended_platform']}", ""]
    lines.append("Reasoning:")
    lines += [f"- {r}" for r in rec["reasoning"]]
    if rec["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        lines += [f"- {w}" for w in rec["warnings"]]
    lines.append("")
    lines.append(f"AWS: {rec['cloud_equivalents']['aws']}")
    lines.append(f"Azure: {rec['cloud_equivalents']['azure']}")
    lines.append(f"GCP: {rec['cloud_equivalents']['gcp']}")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
