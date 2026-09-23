"""
core.py
The actual analysis pipeline, decoupled from any particular interface.
cli.py and mcp_server.py both call into this — so the CLI and the MCP tool
are guaranteed to behave identically, not two diverging copies of the logic.
"""
from datetime import datetime, timezone
from pathlib import Path

import detectors
import gitutil
import infra
import recommend
import services
import containerize


def analyze_repo_local(repo_path: str) -> dict:
    """Analyzes a repo that is already a local directory (no cloning)."""
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        raise ValueError(f"'{repo_path}' is not a directory.")

    frameworks = detectors.detect_all(str(repo))
    infra_signals = infra.analyze(str(repo))

    containerized = bool(infra_signals["dockerfiles"] or infra_signals["compose"]["compose_files"] or infra_signals["k8s_manifests"])
    stateful_hint = any(
        kw in str(frameworks).lower() for kw in ["django", "rails", "spring", "laravel"]
    )

    rec = recommend.recommend(
        service_count=infra_signals["estimated_service_count"],
        containerized=containerized,
        port_routing_signal=infra_signals["port_based_routing_signal"],
        has_health_endpoints=infra_signals["has_health_endpoints"],
        has_iac=bool(infra_signals["iac"]),
        stateful_hint=stateful_hint,
        committed_binaries=infra_signals.get("committed_binaries"),
        deploy_anti_patterns=infra_signals.get("deploy_anti_patterns"),
        version_mismatches=infra_signals.get("version_mismatches"),
    )

    aux_services = services.detect_services(str(repo), frameworks)
    containerization_plan = {
        "dockerfiles": {
            entry["ecosystem"]: containerize.generate_dockerfile(entry["ecosystem"])
            for entry in frameworks
        },
        "package_mgmt_notes": {
            entry["ecosystem"]: containerize.package_mgmt_notes(entry["ecosystem"])
            for entry in frameworks
        },
        "auxiliary_services": aux_services,
        "docker_compose": containerize.generate_compose(frameworks, aux_services) if frameworks else None,
        "cloud_service_equivalents": {
            s["name"]: containerize.cloud_equivalents(s["name"]) for s in aux_services
        },
    }

    return {
        "repo_path": str(repo),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "frameworks": frameworks,
        "infra": infra_signals,
        "recommendation": rec,
        "containerization_plan": containerization_plan,
    }


def analyze_repo(repo_target: str, keep_clone: bool = False) -> dict:
    """
    Analyzes repo_target, which may be a local path OR a git URL.
    If it's a URL: clones to a temp dir, analyzes, deletes the clone unless
    keep_clone=True. Same clone/cleanup behavior the CLI has always had.
    """
    cloned_path = None
    target = repo_target

    if gitutil.is_repo_url(target):
        cloned_path = gitutil.clone_to_temp(target)
        target = cloned_path

    try:
        analysis = analyze_repo_local(target)
        if cloned_path:
            analysis["cloned_from"] = gitutil.mask_url(repo_target)
            analysis["clone_kept_at"] = cloned_path if keep_clone else None
        return analysis
    finally:
        if cloned_path and not keep_clone:
            gitutil.cleanup_temp(cloned_path)


def write_plan_files(analysis: dict, plan_dir: str):
    """Writes one Dockerfile per detected ecosystem, a merged docker-compose.yml,
    and a SERVICES.md summarizing auxiliary services + cloud equivalents."""
    out = Path(plan_dir)
    out.mkdir(parents=True, exist_ok=True)
    plan = analysis["containerization_plan"]

    for ecosystem, dockerfile_text in plan["dockerfiles"].items():
        safe = ecosystem.split(" ")[0].split("/")[0].replace(".", "")
        (out / f"Dockerfile.{safe}").write_text(dockerfile_text, encoding="utf-8")

    if plan["docker_compose"]:
        (out / "docker-compose.yml").write_text(plan["docker_compose"], encoding="utf-8")

    lines = ["# Auxiliary services and cloud equivalents", ""]
    if plan["auxiliary_services"]:
        for s in plan["auxiliary_services"]:
            eq = plan["cloud_service_equivalents"].get(s["name"], {})
            lines.append(f"## {s['name']} ({s['category']})")
            lines.append(f"- Detected via: {s['detected_via']}")
            lines.append(f"- AWS: {eq.get('aws', 'n/a')}")
            lines.append(f"- Azure: {eq.get('azure', 'n/a')}")
            lines.append(f"- GCP: {eq.get('gcp', 'n/a')}")
            lines.append("")
    else:
        lines.append("No auxiliary service dependencies detected.")
    (out / "SERVICES.md").write_text("\n".join(lines), encoding="utf-8")
