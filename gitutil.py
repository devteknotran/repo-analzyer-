"""
gitutil.py
Resolves a repo argument that may be a local path OR a git URL. If it's a
URL, shallow-clones it into a temp directory so the rest of the tool can
keep treating everything as a local filesystem path.

The only network call in this whole tool is a plain `git clone` — no API,
no SDK, no token spent talking to an LLM.

Works across GitHub, GitLab, and Bitbucket (cloud or self-hosted) — the URL
tells the tool which host it's dealing with, and it picks the right
username convention for that host automatically.

Auth for private repos (HTTPS): set the provider-specific env var(s) for
whichever hosts you use — you can set several at once if your repos are
spread across providers, and the tool picks the right one per URL:

    export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx        # GitHub PAT
    export GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx       # GitLab PAT or Group Access Token
    export BITBUCKET_TOKEN=xxxxxxxxxxxxxxxxxxxx           # Bitbucket App Password or Repository Access Token

    # or, for a self-hosted / single-provider setup, one generic fallback:
    export GIT_TOKEN=xxxxxxxxxxxxxxxxxxxx
    export GIT_USERNAME=your-username                     # only needed if the default guess is wrong

Never pass credentials on the command line (they'd end up in shell
history) — always via env vars, and never commit them anywhere.

Or just use SSH (git@... URLs) with a key already loaded in your ssh-agent —
no env vars needed, this tool does nothing special for SSH, it's exactly
the `git clone` you'd run by hand.

Never use a real account password here. GitHub/GitLab/Bitbucket have all
moved away from password auth over HTTPS specifically to stop this pattern
— use a scoped token instead (read-only where the provider allows it), so
it can be revoked independently of the account and doesn't expose full
account credentials to whatever is calling this tool (including a future
MCP wrapper).
"""
import os
import re
import shutil
import subprocess
import tempfile

URL_PATTERN = re.compile(
    r"^(https?://|git@|ssh://|git://).+"
)

# host substring -> (default HTTPS username, env var to check first)
PROVIDER_DEFAULTS = {
    "github.com": ("x-access-token", "GITHUB_TOKEN"),
    "gitlab.com": ("oauth2", "GITLAB_TOKEN"),
    "bitbucket.org": ("x-token-auth", "BITBUCKET_TOKEN"),
}


def is_repo_url(value: str) -> bool:
    return bool(URL_PATTERN.match(value.strip())) or value.strip().endswith(".git")


def _detect_provider(url: str):
    for host, defaults in PROVIDER_DEFAULTS.items():
        if host in url:
            return defaults
    return (None, None)  # self-hosted / unknown host — no default username guess


def _inject_credentials(url: str) -> str:
    """
    If a token is available for this URL's host, embeds username:token into
    the URL for this one `git clone` call. Checks the provider-specific env
    var first (GITHUB_TOKEN / GITLAB_TOKEN / BITBUCKET_TOKEN), then falls
    back to the generic GIT_TOKEN for self-hosted setups. SSH URLs are
    untouched (auth is whatever your local ssh-agent already provides).
    """
    if not url.startswith("https://"):
        return url

    default_username, provider_env_var = _detect_provider(url)
    token = None
    if provider_env_var:
        token = os.environ.get(provider_env_var)
    if not token:
        token = os.environ.get("GIT_TOKEN")
    if not token:
        return url

    username = os.environ.get("GIT_USERNAME") or default_username or "git"

    scheme, rest = url.split("://", 1)
    return f"{scheme}://{username}:{token}@{rest}"


def mask_url(url: str) -> str:
    """Safe-to-print version of a URL that may contain embedded credentials."""
    return re.sub(r"://[^@/]+:[^@/]+@", "://***:***@", url)


def clone_to_temp(url: str) -> str:
    """
    Shallow-clones `url` into a fresh temp directory and returns the path.
    Raises SystemExit with a clear message on failure (missing git, auth
    failure, bad URL, timeout, etc.) rather than a raw traceback or an
    indefinite hang. Credentials are never printed — errors are shown with
    the masked URL only.
    """
    if shutil.which("git") is None:
        raise SystemExit("Error: 'git' is not installed or not on PATH. Install git, or pass a local repo path instead.")

    authed_url = _inject_credentials(url)
    dest = tempfile.mkdtemp(prefix="repo_cloud_analyzer_")
    cmd = ["git", "clone", "--depth", "1", authed_url, dest]

    # Without a terminal attached (as in an MCP server's stdio process), git
    # will otherwise hang indefinitely prompting for a username/password
    # when the URL has no valid credentials — GIT_TERMINAL_PROMPT=0 makes it
    # fail immediately instead, and the explicit timeout is a second
    # safety net in case something else stalls the clone.
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
    except subprocess.TimeoutExpired:
        shutil.rmtree(dest, ignore_errors=True)
        raise SystemExit(
            f"Error: git clone timed out after 60s for '{mask_url(authed_url)}'.\n"
            "This usually means the repo is private and no valid token/SSH key was "
            "available, or the host is unreachable. Confirm the URL, and that the "
            "matching token env var (GITHUB_TOKEN/GITLAB_TOKEN/BITBUCKET_TOKEN/GIT_TOKEN) "
            "is actually set for whatever process is running this — if you just updated "
            "it, make sure that process (e.g. Claude Desktop) was fully restarted."
        )

    if result.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        stderr = result.stderr.strip().replace(authed_url, mask_url(authed_url))
        raise SystemExit(
            f"Error: git clone failed for '{mask_url(authed_url)}'.\n"
            f"{stderr}\n\n"
            "If this is a private repo: set the matching token env var "
            "(GITHUB_TOKEN / GITLAB_TOKEN / BITBUCKET_TOKEN, or GIT_TOKEN for "
            "self-hosted) to a scoped token — never a real account password — "
            "or use an SSH URL (git@...) with a key already loaded in your "
            "ssh-agent. Try `git clone` on it directly first to confirm access works."
        )

    return dest


def cleanup_temp(path: str):
    shutil.rmtree(path, ignore_errors=True)
