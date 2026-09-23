"""
infra.py
Detects infrastructure signals already present in the repo: Dockerfiles,
docker-compose services (used as a microservice-count proxy), Kubernetes
manifests, CI/CD pipelines, and port-based routing patterns.
"""
import re
from pathlib import Path

try:
    import tomllib  # py3.11+
except ImportError:
    tomllib = None

SKIP_DIRS = {
    "node_modules", ".git", "vendor", "venv", ".venv", "dist", "build",
    "__pycache__", "target", ".terraform", "coverage",
}


def _iter_files(repo_path: Path, patterns):
    for pattern in patterns:
        for p in repo_path.rglob(pattern):
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            yield p


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def detect_dockerfiles(repo_path: Path):
    return [str(p.relative_to(repo_path)) for p in _iter_files(repo_path, ["Dockerfile", "Dockerfile.*", "*.dockerfile"])]


def detect_compose(repo_path: Path):
    """
    Parses docker-compose.yml with a lightweight line-based reader (no PyYAML
    dependency). Counts top-level 'services:' entries and collects exposed
    ports as a proxy for port-based routing.
    """
    compose_files = list(_iter_files(repo_path, ["docker-compose*.yml", "docker-compose*.yaml", "compose.yml", "compose.yaml"]))
    services = []
    ports = []
    for cf in compose_files:
        text = _read_text(cf)
        lines = text.splitlines()
        in_services = False
        service_indent = None
        for line in lines:
            stripped = line.strip()
            if re.match(r"^services:\s*$", line):
                in_services = True
                continue
            if in_services:
                # a new top-level key at column 0 ends the services block
                if line and not line.startswith((" ", "\t")) and not stripped.startswith("#"):
                    in_services = False
                    continue
                m = re.match(r"^(\s\s)([\w\-\.]+):\s*$", line)
                if m:
                    services.append(m.group(2))
            port_match = re.findall(r'["\']?(\d{2,5}):(\d{2,5})["\']?', stripped)
            if port_match and ("ports" in text[:text.find(line)][-200:].lower() or "-" in stripped[:2]):
                ports.extend(port_match)
    return {
        "compose_files": [str(p.relative_to(repo_path)) for p in compose_files],
        "service_count": len(services),
        "service_names": services,
        "exposed_port_pairs": ports[:100],
    }


def detect_k8s_manifests(repo_path: Path):
    hits = []
    for p in _iter_files(repo_path, ["*.yml", "*.yaml"]):
        text = _read_text(p)
        if re.search(r"^kind:\s*(Deployment|Service|Ingress|StatefulSet|DaemonSet)", text, re.MULTILINE):
            hits.append(str(p.relative_to(repo_path)))
    return hits


def detect_ci_cd(repo_path: Path):
    signals = []
    if (repo_path / ".github" / "workflows").is_dir():
        signals.append("GitHub Actions")
    if (repo_path / ".gitlab-ci.yml").exists():
        signals.append("GitLab CI")
    if (repo_path / "bitbucket-pipelines.yml").exists():
        signals.append("Bitbucket Pipelines")
    if (repo_path / "Jenkinsfile").exists():
        signals.append("Jenkins")
    if (repo_path / "buildspec.yml").exists():
        signals.append("AWS CodeBuild")
    if (repo_path / "azure-pipelines.yml").exists():
        signals.append("Azure Pipelines")
    return signals


def detect_iac(repo_path: Path):
    signals = []
    if list(_iter_files(repo_path, ["*.tf"])):
        signals.append("Terraform")
    if list(_iter_files(repo_path, ["*.cfn.yaml", "*.cfn.yml", "template.yaml"])):
        signals.append("CloudFormation / SAM")
    if list(_iter_files(repo_path, ["cdk.json"])):
        signals.append("AWS CDK")
    return signals


def detect_committed_binaries(repo_path: Path):
    """Flags files that look like compiled binaries checked into the repo —
    a real thing that happened in the LMS pipeline (two 14MB Go binaries
    committed directly). Heuristic: no recognized source/text extension,
    over 200KB, and contains a null byte in the first 4KB (a reliable
    binary-vs-text signal that doesn't depend on the extension being honest).
    """
    TEXT_EXTENSIONS = {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".java", ".rb", ".php", ".cs",
        ".md", ".txt", ".yml", ".yaml", ".json", ".xml", ".html", ".css", ".sql",
        ".sh", ".toml", ".ini", ".cfg", ".gitignore", ".dockerfile", ".env",
        ".gradle", ".mod", ".sum", ".lock", ".gemfile",
    }
    KNOWN_BINARY_ASSET_EXTENSIONS = {
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2", ".ttf",
        ".otf", ".pdf", ".zip", ".tar", ".gz", ".jar", ".war",
    }
    flagged = []
    for p in repo_path.rglob("*"):
        if not p.is_file() or any(part in SKIP_DIRS for part in p.parts):
            continue
        ext = p.suffix.lower()
        if ext in TEXT_EXTENSIONS or ext in KNOWN_BINARY_ASSET_EXTENSIONS:
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size < 200_000:
            continue
        try:
            with open(p, "rb") as f:
                chunk = f.read(4096)
            if b"\x00" in chunk:
                flagged.append({"path": str(p.relative_to(repo_path)), "size_mb": round(size / 1_000_000, 1)})
        except OSError:
            continue
    return flagged


def detect_deploy_anti_patterns(repo_path: Path):
    """Static checks over deploy scripts and CI configs for patterns that
    showed up in the LMS pipeline review: SSH-based deploys with host-key
    checking disabled, hardcoded IPs, and :latest tags with no rollback path.
    """
    script_patterns = ["*.sh", "Jenkinsfile", "buildspec.yml", "buildspec.yaml",
                        ".gitlab-ci.yml", "bitbucket-pipelines.yml"]
    texts = []
    for pattern in script_patterns:
        for p in _iter_files(repo_path, [pattern]):
            texts.append((str(p.relative_to(repo_path)), _read_text(p)))

    uses_ssh_deploy = []
    strict_host_key_disabled = []
    hardcoded_ips = set()
    uses_latest_tag = []

    ip_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    excluded_ips = {"0.0.0.0", "127.0.0.1", "255.255.255.255", "1.1.1.1", "8.8.8.8"}

    for rel_path, text in texts:
        if re.search(r"\b(ssh|scp)\s+", text):
            uses_ssh_deploy.append(rel_path)
        if re.search(r"StrictHostKeyChecking[=\s]+no", text, re.IGNORECASE):
            strict_host_key_disabled.append(rel_path)
        if re.search(r":latest\b", text):
            uses_latest_tag.append(rel_path)
        for ip in ip_pattern.findall(text):
            if ip not in excluded_ips:
                hardcoded_ips.add(ip)

    for p in _iter_files(repo_path, ["Dockerfile", "Dockerfile.*"]):
        text = _read_text(p)
        if re.search(r":latest\b", text):
            uses_latest_tag.append(str(p.relative_to(repo_path)))

    return {
        "ssh_scp_deploy_scripts": uses_ssh_deploy,
        "strict_host_key_checking_disabled": strict_host_key_disabled,
        "hardcoded_ips": sorted(hardcoded_ips)[:20],
        "latest_tag_usage": sorted(set(uses_latest_tag)),
    }


def detect_exposed_ports(repo_path: Path):
    """
    Collects every port the repo declares, from every place it might be
    declared, so the report has one place to cross-check "what port does
    this actually run on" instead of the person having to dig through
    Dockerfiles/compose/k8s manifests by hand.
    """
    dockerfile_ports = []
    for p in _iter_files(repo_path, ["Dockerfile", "Dockerfile.*"]):
        text = _read_text(p)
        for m in re.finditer(r"^EXPOSE\s+(\d+)(?:/\w+)?", text, re.MULTILINE):
            dockerfile_ports.append({"port": m.group(1), "source": str(p.relative_to(repo_path))})

    k8s_ports = []
    for p in _iter_files(repo_path, ["*.yml", "*.yaml"]):
        text = _read_text(p)
        if not re.search(r"^kind:\s*(Deployment|Pod|StatefulSet|DaemonSet)", text, re.MULTILINE):
            continue
        for m in re.finditer(r"containerPort:\s*(\d+)", text):
            k8s_ports.append({"port": m.group(1), "source": str(p.relative_to(repo_path))})

    return {
        "dockerfile_ports": dockerfile_ports,
        "k8s_container_ports": k8s_ports,
    }


def detect_version_mismatches(repo_path: Path):
    """Checks whether the language runtime version declared in the source
    manifest agrees with the version pinned in the Dockerfile and any CI
    config — a real bug in the LMS pipeline (go.mod said 1.19, CodeBuild
    used 1.21, the Dockerfile used 1.22). Currently implemented for Go;
    structured so Node/Python/Java can be added the same way.
    """
    findings = []

    for go_mod in _iter_files(repo_path, ["go.mod"]):
        text = _read_text(go_mod)
        m = re.search(r"^go\s+(\d+\.\d+)", text, re.MULTILINE)
        if not m:
            continue
        versions_seen = {"go.mod": m.group(1)}

        for p in _iter_files(repo_path, ["Dockerfile", "Dockerfile.*"]):
            dtext = _read_text(p)
            dm = re.search(r"FROM\s+golang:(\d+\.\d+)", dtext)
            if dm:
                versions_seen[str(p.relative_to(repo_path))] = dm.group(1)

        for p in _iter_files(repo_path, ["buildspec.yml", "buildspec.yaml", "Jenkinsfile", ".gitlab-ci.yml"]):
            ctext = _read_text(p)
            cm = re.search(r"go(?:lang)?[\s:\-]*(\d+\.\d+)", ctext, re.IGNORECASE)
            if cm:
                versions_seen[str(p.relative_to(repo_path))] = cm.group(1)

        if len(set(versions_seen.values())) > 1:
            findings.append({"language": "Go", "versions_by_source": versions_seen})

    return findings



def detect_health_endpoints(repo_path: Path):
    """Greps source for common health-check route patterns as a resilience signal."""
    pattern = re.compile(r"['\"/](health|healthz|readiness|liveness|ping)['\"/]", re.IGNORECASE)
    hits = 0
    for p in repo_path.rglob("*"):
        if p.is_file() and p.suffix in {".py", ".js", ".ts", ".go", ".java", ".rb", ".php"}:
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            if pattern.search(_read_text(p)):
                hits += 1
        if hits >= 5:
            break
    return hits > 0


def analyze(repo_path: str):
    repo = Path(repo_path).resolve()
    compose = detect_compose(repo)
    return {
        "dockerfiles": detect_dockerfiles(repo),
        "compose": compose,
        "k8s_manifests": detect_k8s_manifests(repo),
        "ci_cd": detect_ci_cd(repo),
        "iac": detect_iac(repo),
        "has_health_endpoints": detect_health_endpoints(repo),
        "estimated_service_count": max(compose["service_count"], 1),
        "port_based_routing_signal": len(compose["exposed_port_pairs"]) >= 3,
        "committed_binaries": detect_committed_binaries(repo),
        "deploy_anti_patterns": detect_deploy_anti_patterns(repo),
        "version_mismatches": detect_version_mismatches(repo),
        "exposed_ports": detect_exposed_ports(repo),
    }
