# repo-cloud-analyzer

Offline repo scanner that detects framework(s), dependencies, and infra
signals in a codebase, then recommends an AWS deployment target (EC2 / ECS /
EKS) with Azure and GCP equivalents — with reasoning you can hand straight to
a client.

**No API keys. No LLM tokens.** Everything is done with Python's standard
library, reading manifest files (`package.json`, `requirements.txt`,
`pom.xml`, `go.mod`, `Gemfile`, `composer.json`, `*.csproj`) and infra files
(`Dockerfile`, `docker-compose.yml`, Kubernetes manifests, CI/CD configs,
Terraform/CDK/CloudFormation) already in the repo. The only network call it
ever makes is a plain `git clone`, and only if you pass a repo URL instead of
a local path — no API, no token, just whatever git/SSH access you already have.

## Requirements

- Python 3.8+ (no third-party packages needed)
- `git` on PATH — only needed if you pass a repo URL instead of a local path

## Usage

```bash
# Local path — print a Markdown report to the terminal
python3 cli.py /path/to/repo

# Repo URL (GitHub, GitLab, or Bitbucket — cloud or self-hosted) — clones
# to a temp dir, analyzes, deletes the clone when done
python3 cli.py https://github.com/org/repo.git
python3 cli.py https://gitlab.com/org/repo.git
python3 cli.py https://bitbucket.org/org/repo.git
python3 cli.py git@github.com:org/repo.git      # SSH works the same way on any provider

# Keep the cloned copy instead of auto-deleting it
python3 cli.py https://gitlab.com/org/repo.git --keep-clone

# Write both a JSON report (for tooling/dashboards) and a Markdown report
python3 cli.py /path/to/repo --json report.json --md report.md

# Also generate the actual Dockerfile(s), a merged docker-compose.yml, and
# SERVICES.md (auxiliary services + AWS/Azure/GCP equivalents) to a folder
python3 cli.py /path/to/repo --plan-dir ./container-plan
```

### Private repos — set the right token for each provider

You can set several at once if repos are spread across providers — the
tool auto-detects the host from the URL and picks the matching token:

```bash
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
export GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx
export BITBUCKET_TOKEN=xxxxxxxxxxxxxxxxxxxx

python3 cli.py https://github.com/org/repo1.git --json r1.json --md r1.md
python3 cli.py https://gitlab.com/org/repo2.git --json r2.json --md r2.md
python3 cli.py https://bitbucket.org/org/repo3.git --json r3.json --md r3.md
```

For self-hosted GitHub Enterprise/GitLab/Bitbucket Server, use the generic
`GIT_TOKEN` (and `GIT_USERNAME` if the default guess is wrong):

```bash
export GIT_TOKEN=xxxxxxxxxxxxxxxxxxxx
python3 cli.py https://git.internal-company.com/org/repo.git
```

**Never use a real account password.** Create a scoped token instead:
- GitHub: Settings → Developer settings → Personal access tokens (repo: read-only)
- GitLab: Settings → Access Tokens (`read_repository` scope) — or a **Group Access Token** if you want one token to cover every project under a group
- Bitbucket: Settings → App passwords, or a Repository/Project Access Token (Repositories: Read)

Tokens are never printed to the terminal or written into error messages —
only a masked version (`***:***@host/...`) ever shows up in output.

Private repos over SSH need no env vars at all — just make sure the key is
loaded in your `ssh-agent` and use the `git@...` URL form.

## What it detects

| Category | Signals |
|---|---|
| Frameworks | Node (Express, NestJS, Next, React, Vue, Angular...), Python (Django, Flask, FastAPI), Java (Spring Boot), Go (Gin, Echo), Ruby (Rails, Sinatra), PHP (Laravel, Symfony), .NET |
| Dependencies | Counted from lockfiles/manifests per ecosystem |
| Auxiliary services | Database, cache, queue, search, and object-storage client libraries (Postgres/MySQL/MongoDB drivers, Redis/ioredis, Kafka/RabbitMQ clients, Elasticsearch client, boto3/AWS SDK) — matched against the same manifests, so it tells you what *else* needs to run alongside the app, not just the app itself |
| Containerization | Dockerfile, docker-compose services, Kubernetes manifests |
| CI/CD | GitHub Actions, GitLab CI, Bitbucket Pipelines, Jenkins, CodeBuild, Azure Pipelines |
| IaC | Terraform, CloudFormation/SAM, AWS CDK |
| Resilience | Health-check endpoint patterns (`/health`, `/healthz`, etc.) |
| Anti-patterns | Port-based routing across services, compiled binaries committed to the repo, SSH/SCP-based deploy scripts, disabled SSH host-key checking, hardcoded IPs, `:latest` image tags with no rollback path, language version disagreeing across manifest/Dockerfile/CI config |

## What `--plan-dir` generates

- `Dockerfile.<ecosystem>` — a best-practice multi-stage Dockerfile per detected language/framework (non-root user, layer-cached dependency install, slim runtime base image)
- `docker-compose.yml` — one service block per detected app manifest, plus a service block for every detected auxiliary dependency (Postgres, Redis, Kafka, etc.), with `depends_on` wired up automatically
- `SERVICES.md` — every detected auxiliary service with its AWS/Azure/GCP managed-service equivalent, so "what do we run this on in the cloud" has an answer for every piece, not just the app container

These are strong starting points, not finished artifacts — ports, build output paths, and entrypoints in the Dockerfiles are marked with comments where you'll need to adjust them to the actual service.

## How the recommendation is decided

`recommend.py` is a plain rules engine, not a model call — the same repo will
always produce the same recommendation:

- Not containerized → **EC2** (containerize first)
- 1 service → **ECS** (Fargate)
- 2-8 services → **ECS** (Service Connect handles discovery)
- 9-20 services → **ECS or EKS**, flagged as borderline — decide based on
  growth trajectory and existing Kubernetes expertise
- 20+ services → **EKS** (native service discovery + ingress scale better
  than per-service ALB target-group management at this size)

Every recommendation also surfaces specific warnings (missing health checks,
missing IaC, port-based routing, likely stateful components) so the report
reads as a punch-list, not just a platform name.

## Extending it

- Add new framework signatures in `detectors.py`
- Add new infra signals in `infra.py`
- Adjust the decision thresholds in `recommend.py` — they're plain if/elif
  blocks, easy to tune per client engagement
- `report.py` controls the Markdown/JSON output shape

## Roadmap (not built yet)

- Web dashboard on top of the JSON output for recurring/scheduled scans

## Using it as an MCP server (Claude Desktop / Claude Code)

This ships with `mcp_server.py`, which exposes the same analysis engine as
three tools Claude can call directly instead of you running the CLI by hand:

- `analyze_repository(repo_path_or_url, keep_clone=False)` → full Markdown report
- `generate_containerization_plan(repo_path_or_url, output_dir, keep_clone=False)` → writes Dockerfile(s), docker-compose.yml, SERVICES.md to disk
- `get_deployment_recommendation(repo_path_or_url, keep_clone=False)` → just the platform call + reasoning, no full detail

### Setup

1. Install the SDK (pin this version — a newer release had a broken dependency resolve when we tested):
   ```bash
   pip install "mcp==1.9.4" --break-system-packages
   ```
2. Confirm it runs standalone:
   ```bash
   python3 mcp_server.py
   ```
   It'll sit there waiting for stdio input — that's correct, it's not meant to print anything on its own. Ctrl+C to stop.
3. Add it to your MCP client's config. For **Claude Desktop**, edit:
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`

   ```json
   {
     "mcpServers": {
       "repo-cloud-analyzer": {
         "command": "python3",
         "args": ["/absolute/path/to/repo_cloud_analyzer/mcp_server.py"],
         "env": {
           "GITHUB_TOKEN": "ghp_xxxxxxxxxxxxxxxxxxxx",
           "GITLAB_TOKEN": "glpat-xxxxxxxxxxxxxxxxxxxx",
           "BITBUCKET_TOKEN": "xxxxxxxxxxxxxxxxxxxx"
         }
       }
     }
   }
   ```
   Use an absolute path to `mcp_server.py` — relative paths won't resolve from wherever Claude Desktop launches the process from. Leave the `env` block out entirely if you only need public repos or local paths.
4. Restart Claude Desktop. You should see `repo-cloud-analyzer` listed under the 🔌 connectors/tools icon.
5. For **Claude Code**, run:
   ```bash
   claude mcp add repo-cloud-analyzer -- python3 /absolute/path/to/repo_cloud_analyzer/mcp_server.py
   ```

### Multi-user note

Each person who wants this in their own Claude Desktop/Code runs their own
local copy with their own token env vars — the server runs on your machine,
not a shared one, so there's no shared-credential exposure to design around.
If you later want a *centrally hosted* version multiple teammates hit
remotely, that's a different deployment shape (HTTP transport + per-user
auth, not stdio) — worth a separate conversation before building it, given
the fintech compliance angle already discussed.

### Tested against

This was verified with a real MCP client/server handshake over stdio
(`mcp.client.stdio`), not just import-checked — tool listing, both
`get_deployment_recommendation` and `generate_containerization_plan` calls,
and the resulting files on disk were all confirmed working end to end.
