"""
report.py
Turns the raw analysis dict into a JSON file and a human-readable Markdown
report.
"""
import json
from datetime import datetime, timezone


def to_json(analysis: dict, out_path: str):
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(analysis, f, indent=2)


def to_markdown(analysis: dict) -> str:
    lines = []
    lines.append(f"# Repo Cloud Readiness Report")
    lines.append("")
    lines.append(f"- **Repo path:** `{analysis['repo_path']}`")
    lines.append(f"- **Generated:** {analysis['generated_at']}")
    lines.append("")

    lines.append("## Detected frameworks")
    if analysis["frameworks"]:
        for f in analysis["frameworks"]:
            fw = ", ".join(f["frameworks"])
            dep_count = f["dependency_count"] if f["dependency_count"] is not None else "n/a"
            lines.append(f"- **{f['ecosystem']}** — {fw} (manifest: `{f['manifest']}`, dependencies: {dep_count})")
    else:
        lines.append("- No recognized manifest files found.")
    lines.append("")

    infra = analysis["infra"]
    lines.append("## Infrastructure signals")
    lines.append(f"- Dockerfiles: {len(infra['dockerfiles'])} ({', '.join(infra['dockerfiles']) or 'none'})")
    lines.append(f"- docker-compose services detected: {infra['compose']['service_count']} ({', '.join(infra['compose']['service_names']) or 'none'})")
    lines.append(f"- Kubernetes manifests found: {len(infra['k8s_manifests'])}")
    lines.append(f"- CI/CD: {', '.join(infra['ci_cd']) or 'none detected'}")
    lines.append(f"- Infrastructure-as-Code: {', '.join(infra['iac']) or 'none detected'}")
    lines.append(f"- Health-check endpoint pattern found: {'yes' if infra['has_health_endpoints'] else 'no'}")
    lines.append(f"- Port-based routing signal: {'yes — see warnings' if infra['port_based_routing_signal'] else 'no'}")
    lines.append(f"- Estimated service count: {infra['estimated_service_count']}")

    ports = infra.get("exposed_ports", {})
    dockerfile_ports = ports.get("dockerfile_ports", [])
    k8s_ports = ports.get("k8s_container_ports", [])
    compose_ports = infra["compose"]["exposed_port_pairs"]
    if dockerfile_ports or k8s_ports or compose_ports:
        lines.append("- **Detected ports (cross-check these against the actual code before trusting them):**")
        for p in dockerfile_ports:
            lines.append(f"  - `{p['port']}` — Dockerfile `EXPOSE` in `{p['source']}`")
        for p in k8s_ports:
            lines.append(f"  - `{p['port']}` — Kubernetes `containerPort` in `{p['source']}`")
        for host, container in compose_ports:
            lines.append(f"  - `{host}:{container}` (host:container) — docker-compose port mapping")
    else:
        lines.append("- No ports found declared in Dockerfile EXPOSE, docker-compose, or Kubernetes manifests — check the application source directly.")
    lines.append("")

    rec = analysis["recommendation"]
    lines.append("## Cloud deployment recommendation")
    lines.append(f"**Recommended platform (AWS-first): {rec['recommended_platform']}**")
    lines.append("")
    lines.append("Reasoning:")
    for r in rec["reasoning"]:
        lines.append(f"- {r}")
    lines.append("")
    if rec["warnings"]:
        lines.append("Flags to address before/during migration:")
        for w in rec["warnings"]:
            lines.append(f"- ⚠️ {w}")
        lines.append("")
    lines.append("Cloud equivalents:")
    lines.append(f"- **AWS:** {rec['cloud_equivalents']['aws']}")
    lines.append(f"- **Azure:** {rec['cloud_equivalents']['azure']}")
    lines.append(f"- **GCP:** {rec['cloud_equivalents']['gcp']}")
    lines.append("")

    plan = analysis["containerization_plan"]
    lines.append("## Containerization & dependency management plan")
    lines.append("")
    if plan["auxiliary_services"]:
        lines.append("**Auxiliary services this app needs alongside itself:**")
        lines.append("")
        lines.append("| Service | Category | AWS | Azure | GCP |")
        lines.append("|---|---|---|---|---|")
        for s in plan["auxiliary_services"]:
            eq = plan["cloud_service_equivalents"].get(s["name"], {})
            lines.append(f"| {s['name']} | {s['category']} | {eq.get('aws','')} | {eq.get('azure','')} | {eq.get('gcp','')} |")
        lines.append("")
    else:
        lines.append("No auxiliary service dependencies (database/cache/queue/search/object storage clients) detected in the manifests scanned.")
        lines.append("")

    for ecosystem in plan["dockerfiles"]:
        notes = plan["package_mgmt_notes"].get(ecosystem, [])
        if notes:
            lines.append(f"**{ecosystem} — package/dependency management notes:**")
            for n in notes:
                lines.append(f"- {n}")
            lines.append("")

    lines.append("Run with `--plan-dir <dir>` to write out the generated Dockerfile(s), a merged docker-compose.yml, and SERVICES.md instead of just reading them here.")
    lines.append("")

    return "\n".join(lines)
