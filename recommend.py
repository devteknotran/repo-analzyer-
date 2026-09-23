"""
recommend.py
A deterministic decision tree that maps detected repo characteristics to a
recommended compute platform on AWS, with equivalent services on Azure and
GCP. No LLM call, no external API — just rules over what detectors.py and
infra.py already found.
"""

AWS_MAP = {
    "EC2": "EC2 (single/few instances, manual or ASG-based scaling)",
    "ECS": "ECS (Fargate or EC2 launch type, Service Connect for discovery)",
    "EKS": "EKS (managed node groups or Fargate profiles, Karpenter for scaling)",
}
AZURE_MAP = {
    "EC2": "Azure VMs / VM Scale Sets",
    "ECS": "Azure Container Apps",
    "EKS": "AKS (Azure Kubernetes Service)",
}
GCP_MAP = {
    "EC2": "Compute Engine / Managed Instance Groups",
    "ECS": "Cloud Run",
    "EKS": "GKE (Google Kubernetes Engine)",
}


def recommend(service_count: int, containerized: bool, port_routing_signal: bool,
              has_health_endpoints: bool, has_iac: bool, stateful_hint: bool = False,
              committed_binaries=None, deploy_anti_patterns=None, version_mismatches=None):
    reasoning = []
    warnings = []

    if not containerized:
        platform = "EC2"
        reasoning.append("No Dockerfile/compose detected — containerize before considering ECS or EKS.")
        warnings.append("Add a Dockerfile and health-check endpoint before any migration.")
    elif service_count <= 1:
        platform = "ECS"
        reasoning.append("Single containerized service — ECS Fargate gives managed scaling/healing with minimal ops overhead.")
    elif service_count <= 8:
        platform = "ECS"
        reasoning.append(f"{service_count} services detected — within ECS's comfortable range (Service Connect handles discovery; ALB handles routing).")
    elif service_count <= 20:
        platform = "ECS or EKS (borderline)"
        reasoning.append(f"{service_count} services — workable on ECS with Service Connect, but this is the zone where EKS's ecosystem tooling starts to pay off if you expect growth.")
    else:
        platform = "EKS"
        reasoning.append(f"{service_count}+ services — past the point where ECS's manual per-service target-group management stays clean; native k8s service discovery and ingress scale better here.")

    if port_routing_signal:
        warnings.append(
            "Port-based routing detected across services — this is a maintenance and single-point-of-failure "
            "risk regardless of platform. Replace with DNS-based service discovery "
            "(ECS Service Connect / Cloud Map, or Kubernetes CoreDNS + Ingress)."
        )

    if not has_health_endpoints:
        warnings.append(
            "No health-check endpoint pattern found in source — auto-healing on any platform depends on "
            "accurate health checks. Add /health or /healthz endpoints before migration."
        )

    if not has_iac:
        warnings.append("No Infrastructure-as-Code (Terraform/CDK/CloudFormation) detected — recommend codifying infra before migration for repeatability and audit trail (important for fintech compliance).")

    if stateful_hint:
        warnings.append("Stateful components detected — plan externalized state (RDS/managed DB, ElastiCache/Redis, S3) before moving off single EC2 instances; containers/pods should stay stateless.")

    if committed_binaries:
        names = ", ".join(f"{b['path']} ({b['size_mb']}MB)" for b in committed_binaries[:5])
        warnings.append(f"Compiled binaries committed to the repo ({names}) — remove them and add to .gitignore; they bloat clones and usually mean the build artifact isn't reproducible from source alone.")

    if deploy_anti_patterns:
        if deploy_anti_patterns.get("ssh_scp_deploy_scripts"):
            warnings.append(f"Deploy scripts use SSH/SCP directly ({', '.join(deploy_anti_patterns['ssh_scp_deploy_scripts'][:3])}) — replace with a managed deploy mechanism (aws ecs update-service, CodeDeploy blue/green, or the platform's native rolling deploy) so releases aren't tied to hand-maintained host access.")
        if deploy_anti_patterns.get("strict_host_key_checking_disabled"):
            warnings.append("StrictHostKeyChecking is disabled in a deploy script — this defeats host-key verification and is a real security gap, not just a convenience setting.")
        if deploy_anti_patterns.get("hardcoded_ips"):
            warnings.append(f"Hardcoded IP addresses found in scripts/CI config ({', '.join(deploy_anti_patterns['hardcoded_ips'][:5])}) — these break the moment infrastructure changes; use service discovery or environment-specific config instead.")
        if deploy_anti_patterns.get("latest_tag_usage"):
            warnings.append(f"':latest' image tag used in {len(deploy_anti_patterns['latest_tag_usage'])} place(s) — deploy the build/commit SHA as the tag instead, or you can't tell which version is running or roll back to a known-good one.")

    if version_mismatches:
        for vm in version_mismatches:
            versions_str = ", ".join(f"{src}={ver}" for src, ver in vm["versions_by_source"].items())
            warnings.append(f"{vm['language']} version disagrees across the pipeline ({versions_str}) — pin one version everywhere (manifest, Dockerfile, CI config) so local builds match what actually ships.")

    key = platform.split(" ")[0] if "or" not in platform else "ECS"  # fallback for borderline label
    aws = AWS_MAP.get(key, AWS_MAP["ECS"])
    azure = AZURE_MAP.get(key, AZURE_MAP["ECS"])
    gcp = GCP_MAP.get(key, GCP_MAP["ECS"])

    return {
        "recommended_platform": platform,
        "reasoning": reasoning,
        "warnings": warnings,
        "cloud_equivalents": {
            "aws": aws,
            "azure": azure,
            "gcp": gcp,
        },
    }
