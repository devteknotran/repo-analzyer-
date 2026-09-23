"""
detectors.py
Detects languages, frameworks, and runtimes in a repository by looking for
well-known manifest files and signature dependencies inside them.

Fully offline: no network calls, no API tokens. Just filesystem + regex.
"""
import json
import os
import re
from pathlib import Path

# Each rule: manifest file to look for -> how to read it -> framework signatures
NODE_FRAMEWORK_SIGNATURES = {
    "next": "Next.js",
    "react": "React",
    "vue": "Vue.js",
    "@angular/core": "Angular",
    "express": "Express.js",
    "@nestjs/core": "NestJS",
    "fastify": "Fastify",
    "koa": "Koa",
    "svelte": "Svelte",
}

PYTHON_FRAMEWORK_SIGNATURES = {
    "django": "Django",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "tornado": "Tornado",
    "pyramid": "Pyramid",
    "celery": "Celery (task queue)",
}

RUBY_FRAMEWORK_SIGNATURES = {
    "rails": "Ruby on Rails",
    "sinatra": "Sinatra",
}

PHP_FRAMEWORK_SIGNATURES = {
    "laravel/framework": "Laravel",
    "symfony/symfony": "Symfony",
}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _find_files(repo_path: Path, filename: str, max_depth: int = 4):
    """Find files by name up to max_depth, skipping heavy/irrelevant dirs."""
    skip_dirs = {
        "node_modules", ".git", "vendor", "venv", ".venv", "dist", "build",
        "__pycache__", "target", ".terraform", "coverage",
    }
    results = []
    base_depth = len(repo_path.parts)
    for root, dirs, files in os.walk(repo_path):
        depth = len(Path(root).parts) - base_depth
        if depth > max_depth:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        if filename in files:
            results.append(Path(root) / filename)
    return results


def detect_node(repo_path: Path):
    findings = []
    for pkg_json in _find_files(repo_path, "package.json"):
        try:
            data = json.loads(_read_text(pkg_json))
        except Exception:
            continue
        deps = {}
        deps.update(data.get("dependencies", {}) or {})
        deps.update(data.get("devDependencies", {}) or {})
        matched = [name for key, name in NODE_FRAMEWORK_SIGNATURES.items() if key in deps]
        findings.append({
            "ecosystem": "Node.js / JavaScript",
            "manifest": str(pkg_json.relative_to(repo_path)),
            "frameworks": matched or ["Node.js (no known framework signature matched)"],
            "dependency_count": len(deps),
        })
    return findings


def detect_python(repo_path: Path):
    findings = []
    manifests = (
        _find_files(repo_path, "requirements.txt")
        + _find_files(repo_path, "pyproject.toml")
        + _find_files(repo_path, "Pipfile")
    )
    for manifest in manifests:
        text = _read_text(manifest).lower()
        matched = [name for key, name in PYTHON_FRAMEWORK_SIGNATURES.items() if key in text]
        dep_count = len(re.findall(r"^\s*[\w\-\[\]]+\s*(==|>=|<=|~=|>|<)?", text, re.MULTILINE)) \
            if manifest.name == "requirements.txt" else None
        findings.append({
            "ecosystem": "Python",
            "manifest": str(manifest.relative_to(repo_path)),
            "frameworks": matched or ["Python (no known framework signature matched)"],
            "dependency_count": dep_count,
        })
    return findings


def detect_java(repo_path: Path):
    findings = []
    for manifest in _find_files(repo_path, "pom.xml") + _find_files(repo_path, "build.gradle"):
        text = _read_text(manifest).lower()
        matched = ["Spring Boot"] if "spring-boot" in text else ["Java (no known framework signature matched)"]
        findings.append({
            "ecosystem": "Java",
            "manifest": str(manifest.relative_to(repo_path)),
            "frameworks": matched,
            "dependency_count": None,
        })
    return findings


def detect_go(repo_path: Path):
    findings = []
    for manifest in _find_files(repo_path, "go.mod"):
        text = _read_text(manifest)
        dep_count = len(re.findall(r"^\t\S+\s+v\d", text, re.MULTILINE))
        matched = []
        if "gin-gonic/gin" in text:
            matched.append("Gin")
        if "labstack/echo" in text:
            matched.append("Echo")
        findings.append({
            "ecosystem": "Go",
            "manifest": str(manifest.relative_to(repo_path)),
            "frameworks": matched or ["Go (no known framework signature matched)"],
            "dependency_count": dep_count,
        })
    return findings


def detect_ruby(repo_path: Path):
    findings = []
    for manifest in _find_files(repo_path, "Gemfile"):
        text = _read_text(manifest).lower()
        matched = [name for key, name in RUBY_FRAMEWORK_SIGNATURES.items() if key in text]
        findings.append({
            "ecosystem": "Ruby",
            "manifest": str(manifest.relative_to(repo_path)),
            "frameworks": matched or ["Ruby (no known framework signature matched)"],
            "dependency_count": None,
        })
    return findings


def detect_php(repo_path: Path):
    findings = []
    for manifest in _find_files(repo_path, "composer.json"):
        try:
            data = json.loads(_read_text(manifest))
        except Exception:
            continue
        deps = {}
        deps.update(data.get("require", {}) or {})
        matched = [name for key, name in PHP_FRAMEWORK_SIGNATURES.items() if key in deps]
        findings.append({
            "ecosystem": "PHP",
            "manifest": str(manifest.relative_to(repo_path)),
            "frameworks": matched or ["PHP (no known framework signature matched)"],
            "dependency_count": len(deps),
        })
    return findings


def detect_dotnet(repo_path: Path):
    findings = []
    for manifest in _find_files(repo_path, "*.csproj"):
        pass  # handled below via glob since _find_files matches exact names
    csproj_files = list(repo_path.rglob("*.csproj"))
    for manifest in csproj_files[:20]:
        findings.append({
            "ecosystem": ".NET",
            "manifest": str(manifest.relative_to(repo_path)),
            "frameworks": [".NET / ASP.NET"],
            "dependency_count": None,
        })
    return findings


def detect_all(repo_path: str):
    repo = Path(repo_path).resolve()
    findings = []
    findings += detect_node(repo)
    findings += detect_python(repo)
    findings += detect_java(repo)
    findings += detect_go(repo)
    findings += detect_ruby(repo)
    findings += detect_php(repo)
    findings += detect_dotnet(repo)
    return findings
