"""ops/Caddyfile, ops/caddy/ci.Caddyfile and the site workflows (Milestone 2 steps 1, 9, 11).

- **Always:** text checks of the refactored Caddyfile (the two-file deploy line, the shared
  ``site.caddy``, the privacy log on every site, staging's ``noindex``, header phase A or B, and,
  until the rename's cutover, purelyfreefonts.com serving what trulyfreefonts.com serves) and of
  ``ci.Caddyfile``; the workflows' frozen job names, pinned actions and Caddy pin; dependabot.
- **With Caddy** (``TFF_CADDY_BIN``, else ``caddy`` on ``PATH``) **and openssl**: ``caddy
  validate`` of ops/Caddyfile beside site.caddy, with stand-ins for the server's files; the
  production, staging and redirect sites of both domains running on free ports;
  ``ci.Caddyfile`` serving a site compressed; and ``tff-site serve`` sending the same headers as
  Caddy. CI's caddy job sets ``TFF_CADDY_BIN``, so there these tests fail instead of skipping.

Caddy runs as a subprocess on the loopback interface, with its home in a temporary directory.
"""

import gzip
import http.client
import os
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import yaml

from tff_site import serve

ROOT = Path(__file__).resolve().parents[1]
CADDYFILE = ROOT / "ops" / "Caddyfile"
SITE_CADDY = ROOT / "ops" / "caddy" / "site.caddy"
CI_CADDYFILE = ROOT / "ops" / "caddy" / "ci.Caddyfile"
WORKFLOWS = ROOT / ".github" / "workflows"
CI_YML = WORKFLOWS / "ci.yml"
DEPLOY_YML = WORKFLOWS / "deploy.yml"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
SITE_CONFTEST = ROOT / "tests" / "site" / "conftest.py"

CADDY_VERSION = "2.11.4"
CADDY_SHA256 = "527fbf917c39189a1e3b31d34fa955601680b2d5c8055d2a87b8b9588dec7bb9"
DEPLOY_LINE = (
    "# ssh tff 'install -d -m 755 /tmp/caddy-new' && scp ops/Caddyfile ops/caddy/site.caddy "
    "tff:/tmp/caddy-new/ && ssh tff 'sudo -u caddy caddy validate --config "
    "/tmp/caddy-new/Caddyfile --adapter caddyfile && sudo install -m 644 "
    "/tmp/caddy-new/site.caddy /etc/caddy/site.caddy && sudo install -m 644 "
    "/tmp/caddy-new/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy'"
)
PROD = "purelyfreefonts.com"
STAGING = "staging.purelyfreefonts.com"
# Until the cutover of the rename (owner ruling of 2026-10-02; docs/milestone-2.md step 13b),
# the old hosts serve the same sites, with the old certificate.
OLD_PROD = "trulyfreefonts.com"
OLD_STAGING = "staging.trulyfreefonts.com"
PROD_ROOT = "/srv/trulyfreefonts/public"
STAGING_ROOT = "/srv/trulyfreefonts/staging/current"
# Each site's address, root and TLS snippet.
SITES = [
    (PROD, PROD_ROOT, "origin_tls_purely"),
    (STAGING, STAGING_ROOT, "origin_tls_purely"),
    (OLD_PROD, PROD_ROOT, "origin_tls"),
    (OLD_STAGING, STAGING_ROOT, "origin_tls"),
]
# The old domain's other hostnames, which still redirect to trulyfreefonts.com.
REDIRECTED = (
    "www.trulyfreefonts.com",
    "trulyfreefonts.org",
    "www.trulyfreefonts.org",
    "trulyfreefonts.net",
    "www.trulyfreefonts.net",
)
WWW = "www.purelyfreefonts.com"
# The names each certificate covers (ops/SERVER.md items 12 and 29).
OLD_CERT_NAMES = ("trulyfreefonts.com", "*.trulyfreefonts.com", *REDIRECTED)
CERT_NAMES = ("purelyfreefonts.com", "*.purelyfreefonts.com")
# Headers the access log drops (/privacy): every one that can carry the visitor's IP or
# location beyond the country, including those Cloudflare adds only when a setting turns
# them on, plus Referer and User-Agent (owner ruling of 2026-09-26) and Cookie (owner ruling of
# 2026-09-28).
DROPPED_HEADERS = (
    "Cf-Connecting-Ip",
    "X-Forwarded-For",
    "Cf-Connecting-Ipv6",
    "Cf-Pseudo-Ipv4",
    "True-Client-Ip",
    "X-Real-Ip",
    "Cf-Ipcity",
    "Cf-Iplatitude",
    "Cf-Iplongitude",
    "Cf-Postal-Code",
    "Cf-Region",
    "Cf-Region-Code",
    "Cf-Metro-Code",
    "Cf-Ipcontinent",
    "Cf-Timezone",
    "Referer",
    "User-Agent",
    "Cookie",
)
LOG_FIELDS = [
    "request>remote_ip ip_mask 16 32",
    "request>client_ip ip_mask 16 32",
    "request>remote_port delete",
    *(f"request>headers>{name} delete" for name in DROPPED_HEADERS),
]
FROZEN_JOBS = {
    "lint",
    "test",
    "secrets",
    "site-build",
    "site-real (chromium)",
    "site-real (firefox)",
    "site-browser (chromium)",
    "site-browser (firefox)",
    "site-perf",
    "caddy",
}
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+?)@(\S+)(.*)$", re.MULTILINE)
HEADERS = serve.parse_headers(SITE_CADDY.read_text(encoding="utf-8"))
CSP = HEADERS.security["Content-Security-Policy"]
IMMUTABLE = HEADERS.immutable["Cache-Control"]
REVALIDATE = HEADERS.revalidate["Cache-Control"]
JS_PATH = "/assets/app.0123456789.js"


# ------------------------------------------------------------------------ helpers


def site_block(text: str, address: str) -> str:
    """The body of the top-level block that starts with ``address {``."""
    match = re.search(rf"^{re.escape(address)} \{{\n(.*?)^\}}", text, re.MULTILINE | re.DOTALL)
    assert match, f"no {address} block"
    return match.group(1)


def code_lines(block: str) -> list[str]:
    """The block's lines without comments or blank lines, stripped."""
    lines = (line.split(" #", 1)[0].strip() for line in block.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def workflow(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # YAML 1.1 reads the key "on" as True.
    if True in data:
        data["on"] = data.pop(True)
    return data


def job_names(jobs: dict[str, Any]) -> set[str]:
    names = set()
    for key, job in jobs.items():
        name = job.get("name", key)
        matrix = job.get("strategy", {}).get("matrix", {})
        found = re.search(r"\$\{\{\s*matrix\.(\w+)\s*\}\}", name)
        if found:
            for value in matrix[found.group(1)]:
                names.add(name.replace(found.group(0), str(value)))
        else:
            names.add(name)
    return names


def steps(data: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for key, job in data["jobs"].items():
        for step in job.get("steps", []):
            yield key, step


_EXPR_TOKEN = re.compile(
    r"\s*(?:('(?:[^']|'')*')|(==|!=|&&|\|\||!|\(|\))|([A-Za-z_][\w.-]*(?:\(\))?))"
)


def evaluate(expression: str, context: dict[str, Any]) -> bool:
    """Evaluate a workflow ``if``: 'strings', dotted names and ``name()`` looked up in
    ``context`` (missing ones are null, as in Actions), ``==``, ``!=``, ``!``, ``&&``, ``||``
    and parentheses. Strings compare exactly here; Actions ignores their case."""
    body = expression.strip().removeprefix("${{").removesuffix("}}").strip()
    tokens: list[tuple[str, Any]] = []
    pos = 0
    while pos < len(body):
        match = _EXPR_TOKEN.match(body, pos)
        if match is None or match.end() == pos:
            raise ValueError(f"can't read {body[pos:]!r}")
        string, op, name = match.groups()
        if string is not None:
            tokens.append(("value", string[1:-1].replace("''", "'")))
        elif op is not None:
            tokens.append(("op", op))
        else:
            tokens.append(("value", context.get(name)))
        pos = match.end()
    tokens.append(("end", None))
    at = 0

    def take(*ops: str) -> str | None:
        nonlocal at
        kind, value = tokens[at]
        if kind == "op" and value in ops:
            at += 1
            return value
        return None

    def unary() -> Any:
        nonlocal at
        if take("!"):
            return not unary()
        if take("("):
            value = either()
            assert take(")"), body
            return value
        kind, value = tokens[at]
        assert kind == "value", f"unexpected {value!r} in {body!r}"
        at += 1
        return value

    def compare() -> Any:
        left = unary()
        op = take("==", "!=")
        if op is None:
            return left
        right = unary()
        return (left == right) if op == "==" else (left != right)

    def both() -> Any:
        value = compare()
        while take("&&"):
            value = compare() and value
        return value

    def either() -> Any:
        value = both()
        while take("||"):
            value = both() or value
        return value

    result = either()
    assert tokens[at][0] == "end", f"trailing tokens in {body!r}"
    return bool(result)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_site(root: Path) -> Path:
    """A small built-site stand-in; the HTML and JS are long enough for Caddy to compress."""
    (root / "assets").mkdir(parents=True)
    (root / "methodology").mkdir()
    (root / "empty").mkdir()
    (root / "index.html").write_text(
        "<!doctype html><html lang=en><title>Purely Free Fonts</title>"
        + "<p>A font row.</p>" * 200,
        encoding="utf-8",
    )
    (root / "404.html").write_text(
        "<!doctype html><html lang=en><title>Not found</title>" + "<p>Nothing here.</p>" * 50,
        encoding="utf-8",
    )
    (root / "methodology" / "index.html").write_text("<!doctype html><p>How</p>", "utf-8")
    (root / JS_PATH.lstrip("/")).write_text("const Core = 1;\n" * 200, encoding="utf-8")
    (root / "version.txt").write_text(f"commit={'0' * 40}\n", encoding="utf-8")
    return root


# ------------------------------------------------------------------------ the Caddyfile


def test_the_deploy_line_installs_both_files_after_validating_them():
    lines = CADDYFILE.read_text(encoding="utf-8").splitlines()
    assert DEPLOY_LINE in lines
    assert lines.index(DEPLOY_LINE) < 10, "the deploy line belongs in the header comment"
    assert not any("scp ops/Caddyfile tff:/tmp/Caddyfile" in line for line in lines)


def test_the_caddyfile_imports_site_caddy_from_its_own_directory():
    text = CADDYFILE.read_text(encoding="utf-8")
    assert re.search(r"^import site\.caddy$", text, re.MULTILINE)
    # site.caddy's snippets are the only place the security headers are written.
    assert 'Content-Security-Policy "' not in text
    assert "(tff_site)" not in text
    assert "(tff_security)" not in text


def test_the_caddyfile_keeps_the_other_sites_on_this_server():
    # walnutbutter.ai and any later site deploy their own snippet into /etc/caddy/sites/;
    # dropping this line would take them offline on the next Caddyfile deploy.
    lines = code_lines(CADDYFILE.read_text(encoding="utf-8"))
    assert "import /etc/caddy/sites/*.caddy" in lines


def test_the_global_options_keep_cloudflare_ips_and_the_client_ip_header():
    text = CADDYFILE.read_text(encoding="utf-8")
    assert "import /etc/caddy/cloudflare-ips.caddy" in text
    assert "client_ip_headers CF-Connecting-IP" in text
    assert "tls /etc/caddy/certs/origin.pem /etc/caddy/certs/origin.key" in text


@pytest.mark.parametrize(
    ("snippet", "pair"),
    [("origin_tls", "origin"), ("origin_tls_purely", "purely")],
)
def test_each_domain_has_its_own_origin_certificate(snippet, pair):
    # origin.pem names only the trulyfreefonts domains, purely.pem only purelyfreefonts.com.
    body = site_block(CADDYFILE.read_text(encoding="utf-8"), f"({snippet})")
    assert code_lines(body) == [f"tls /etc/caddy/certs/{pair}.pem /etc/caddy/certs/{pair}.key"]


def test_the_privacy_log_masks_ips_and_drops_ip_headers():
    body = site_block(CADDYFILE.read_text(encoding="utf-8"), "(privacy_log)")
    lines = code_lines(body)
    assert "output file /var/log/caddy/access.log {" in lines
    assert "roll_disabled" in lines
    fields = lines[lines.index("fields {") + 1 :][: len(LOG_FIELDS)]
    assert fields == LOG_FIELDS


@pytest.mark.parametrize(("address", "root", "tls"), SITES)
def test_each_site_shares_the_headers_and_the_log(address, root, tls):
    lines = code_lines(site_block(CADDYFILE.read_text(encoding="utf-8"), address))
    for needed in (f"import {tls}", f"root * {root}", "import tff_site", "import privacy_log"):
        assert needed in lines, f"{address}: {needed}"
    assert len([line for line in lines if line.startswith("import origin_tls")]) == 1, address


@pytest.mark.parametrize(("new", "old"), [(PROD, OLD_PROD), (STAGING, OLD_STAGING)])
def test_the_new_hosts_serve_what_the_old_ones_serve(new, old):
    # Until the cutover the two domains differ only in their certificates.
    text = CADDYFILE.read_text(encoding="utf-8")
    lines = code_lines(site_block(text, new))
    assert [
        "import origin_tls" if line == "import origin_tls_purely" else line for line in lines
    ] == code_lines(site_block(text, old))


@pytest.mark.parametrize(("staging", "prod"), [(STAGING, PROD), (OLD_STAGING, OLD_PROD)])
def test_staging_is_kept_out_of_search_engines_and_production_is_not(staging, prod):
    text = CADDYFILE.read_text(encoding="utf-8")
    assert 'header X-Robots-Tag "noindex, nofollow"' in code_lines(site_block(text, staging))
    assert not any("X-Robots-Tag" in line for line in code_lines(site_block(text, prod)))


@pytest.mark.parametrize(("staging", "prod"), [(STAGING, PROD), (OLD_STAGING, OLD_PROD)])
def test_production_drops_the_csp_only_in_phase_a_and_above_the_import(staging, prod):
    text = CADDYFILE.read_text(encoding="utf-8")
    lines = code_lines(site_block(text, prod))
    assert not any(
        "Content-Security-Policy" in line for line in code_lines(site_block(text, staging))
    )
    if "header -Content-Security-Policy" in lines:
        # Deferred header changes apply last-written first: a removal below the import would
        # run before tff_security sets the header, and so remove nothing.
        assert lines.index("header -Content-Security-Policy") < lines.index("import tff_site")


def test_the_old_domains_other_hostnames_still_redirect_as_before():
    text = CADDYFILE.read_text(encoding="utf-8")
    block = site_block(text, ", ".join(REDIRECTED))
    assert code_lines(block) == [
        "import origin_tls",
        "redir https://trulyfreefonts.com{uri} permanent",
    ]


def test_www_redirects_to_the_new_domain_with_its_certificate():
    # Its own block: the old redirect block's certificate doesn't cover it.
    block = site_block(CADDYFILE.read_text(encoding="utf-8"), WWW)
    assert code_lines(block) == [
        "import origin_tls_purely",
        "redir https://purelyfreefonts.com{uri} permanent",
    ]


def test_every_site_address_is_tested():
    # A host added to the Caddyfile must be added here too.
    text = CADDYFILE.read_text(encoding="utf-8")
    addresses = re.findall(r"^([a-z0-9][a-z0-9., -]*) \{$", text, re.MULTILINE)
    assert sorted(addresses) == sorted([*(a for a, _, _ in SITES), WWW, ", ".join(REDIRECTED)])


# ------------------------------------------------------------------------ ci.Caddyfile


def test_the_ci_caddyfile_serves_the_built_site_where_the_site_tests_look():
    text = CI_CADDYFILE.read_text(encoding="utf-8")
    lines = code_lines(text)
    for needed in ("admin off", "auto_https off", "import site.caddy", "bind 127.0.0.1"):
        assert needed in lines
    assert ":{$TFF_CADDY_PORT:8080} {" in lines
    body = code_lines(site_block(text, ":{$TFF_CADDY_PORT:8080}"))
    assert body == ["bind 127.0.0.1", "root * {$TFF_SITE_DIR}", "import tff_site"]
    conftest = SITE_CONFTEST.read_text(encoding="utf-8")
    assert 'CI_CADDY_URL = "http://127.0.0.1:8080"' in conftest


# ------------------------------------------------------------------------ the workflows


def test_ci_has_the_frozen_job_names():
    assert job_names(workflow(CI_YML)["jobs"]) == FROZEN_JOBS


def test_the_required_checks_are_untouched():
    jobs = workflow(CI_YML)["jobs"]
    runs = {
        key: [step.get("run") for step in jobs[key]["steps"] if "run" in step]
        for key in ("lint", "test")
    }
    assert runs == {
        "lint": [
            "uv sync --locked",
            "uv run --locked ruff check --output-format=github",
            "uv run --locked ruff format --check",
        ],
        "test": [
            "uv sync --locked",
            'uv run --locked pytest -m "not network and not store and not browser" -n auto',
        ],
    }
    assert jobs["secrets"]["env"]["GITLEAKS_VERSION"] == "8.30.1"


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_every_action_is_pinned_by_commit_with_its_version(path):
    uses = USES.findall(path.read_text(encoding="utf-8"))
    assert uses, f"{path.name} uses no actions"
    for action, ref, comment in uses:
        assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{path.name}: {action}@{ref}"
        assert re.fullmatch(r"\s*# v\d+(\.\d+)*\s*", comment), f"{path.name}: {action} comment"


def test_each_action_has_one_pin_across_workflows():
    pins: dict[str, set[str]] = {}
    for path in WORKFLOWS.glob("*.yml"):
        for action, ref, comment in USES.findall(path.read_text(encoding="utf-8")):
            pins.setdefault(action, set()).add(f"{ref}{comment.strip()}")
    assert {action: len(refs) for action, refs in pins.items() if len(refs) > 1} == {}


@pytest.mark.parametrize("path", [CI_YML, DEPLOY_YML], ids=lambda p: p.name)
def test_every_caddy_download_uses_the_pinned_release(path):
    data = workflow(path)
    pinned = [job["env"] for job in data["jobs"].values() if "CADDY_VERSION" in job.get("env", {})]
    assert pinned
    for env in pinned:
        assert (str(env["CADDY_VERSION"]), env["CADDY_SHA256"]) == (CADDY_VERSION, CADDY_SHA256)
    installs = [s["run"] for _, s in steps(data) if "caddy_${CADDY_VERSION}" in s.get("run", "")]
    assert len(installs) == len(pinned)
    for script in installs:
        assert "sha256sum --check --strict" in script


@pytest.mark.parametrize("path", [CI_YML, DEPLOY_YML], ids=lambda p: p.name)
def test_checkouts_never_keep_the_token(path):
    for job, step in steps(workflow(path)):
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False, job


@pytest.mark.parametrize("path", [CI_YML, DEPLOY_YML], ids=lambda p: p.name)
def test_scripts_never_expand_untrusted_input(path):
    for job, step in steps(workflow(path)):
        script = step.get("run", "")
        assert not re.search(r"\$\{\{\s*(inputs\.|github\.event\.|github\.head_ref)", script), job


def test_the_real_catalog_job_runs_what_the_deploy_runs():
    jobs = workflow(CI_YML)["jobs"]
    real = "\n".join(s.get("run", "") for s in jobs["site-real"]["steps"])
    deploy = "\n".join(s.get("run", "") for _, s in steps(workflow(DEPLOY_YML)))
    # The same tests: the accessibility grid's repeats run on the sample only (owner ruling of
    # 2026-09-30); site-real takes one browser per job, the deploy both in turn.
    for needed in (
        "tff-site check",
        "--ignore=tests/site/test_perf.py",
        '-m "not live and not sample_only"',
    ):
        assert needed in real, needed
        assert needed in deploy, needed
    assert jobs["site-real"]["strategy"]["matrix"]["browser"] == ["chromium", "firefox"]
    assert '--browser "$BROWSER"' in real
    assert "--browser chromium --browser firefox" in deploy
    assert "tff-catalog validate --committed" in real
    assert "TFF_PERF_SITE_DIR=" in real
    assert jobs["site-real"]["env"]["DATA"] == "build/catalog-site.json"


def test_browser_jobs_serve_with_caddy_where_the_tests_look():
    data = workflow(CI_YML)
    for key in ("site-browser", "site-perf", "site-real"):
        scripts = "\n".join(s.get("run", "") for s in data["jobs"][key]["steps"])
        assert "caddy start --config ops/caddy/ci.Caddyfile --adapter caddyfile" in scripts
        assert "http://127.0.0.1:8080/" in scripts
        tests = [s for s in data["jobs"][key]["steps"] if "pytest" in s.get("run", "")]
        assert tests[-1]["env"]["TFF_CADDY"] == "1"


def test_the_caddy_job_checks_compression_of_the_built_home_page():
    scripts = "\n".join(s.get("run", "") for s in workflow(CI_YML)["jobs"]["caddy"]["steps"])
    assert "-H 'Accept-Encoding: gzip'" in scripts
    assert "grep -qi '^content-encoding: gzip'" in scripts
    assert 'caddy validate --config "$RUNNER_TEMP/caddy-new/Caddyfile"' in scripts
    assert "cp ops/Caddyfile ops/caddy/site.caddy" in scripts


def test_the_caddy_job_stands_in_for_both_origin_certificates():
    # caddy validate loads every certificate the Caddyfile names (origin_tls, origin_tls_purely).
    scripts = "\n".join(s.get("run", "") for s in workflow(CI_YML)["jobs"]["caddy"]["steps"])
    assert "DNS:purelyfreefonts.com,DNS:*.purelyfreefonts.com" in scripts
    assert "DNS:trulyfreefonts.com,DNS:*.trulyfreefonts.com" in scripts
    assert '"$RUNNER_TEMP/origin.pem" "$RUNNER_TEMP/origin.key" /etc/caddy/certs/' in scripts
    for name in ("purely.pem", "purely.key"):
        assert f"/etc/caddy/certs/{name}" in scripts, name


def test_deploy_runs_build_deploy_live_and_reports_failures():
    data = workflow(DEPLOY_YML)
    assert data["permissions"] == {"contents": "read"}
    assert data["on"]["push"]["branches"] == ["main", "staging"]
    inputs = data["on"]["workflow_dispatch"]["inputs"]
    assert inputs["action"]["options"] == ["deploy", "rollback"]
    assert inputs["action"]["default"] == "deploy"
    assert inputs["commit"]["required"] is False
    env = "github.ref_name == 'main' && 'production' || 'staging'"
    assert data["concurrency"] == {
        "group": f"deploy-${{{{ {env} }}}}",
        "cancel-in-progress": False,
    }
    jobs = data["jobs"]
    assert list(jobs) == ["build", "deploy", "live", "report"]
    assert jobs["deploy"]["needs"] == "build"
    assert jobs["deploy"]["environment"]["name"] == f"${{{{ {env} }}}}"
    assert jobs["live"]["needs"] == "deploy"
    assert jobs["report"]["needs"] == ["build", "deploy", "live"]
    assert jobs["report"]["if"] == "${{ failure() }}"
    assert jobs["report"]["permissions"] == {"issues": "write"}
    assert all("permissions" not in jobs[key] for key in ("build", "deploy", "live"))
    report = jobs["report"]["steps"][0]["run"]
    assert "--label deploy-failure" in report
    assert "gh issue comment" in report


def _deploy_step(name_start: str) -> dict[str, Any]:
    (step,) = [
        s for _, s in steps(workflow(DEPLOY_YML)) if s.get("name", "").startswith(name_start)
    ]
    return step


@pytest.mark.parametrize(
    ("environment", "on_main", "on_staging", "passes"),
    [
        ("production", True, False, True),
        ("production", False, True, False),  # production takes only main
        ("staging", False, True, True),
        ("staging", True, False, True),
        ("staging", False, False, False),  # an unmerged branch or a fork's pull request
    ],
)
def test_deploy_builds_only_commits_on_its_branches(
    tmp_path, environment, on_main, on_staging, passes
):
    script = _deploy_step("Production takes only commits on main")["run"]
    stub = tmp_path / "git"
    stub.write_text(
        '#!/bin/sh\ncase "$4" in origin/main) exit "$ON_MAIN" ;; origin/staging) exit "$ON_STAGING" ;; esac\nexit 2\n'
    )
    stub.chmod(0o755)
    env = {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "ENVIRONMENT": environment,
        "SHA": "a" * 40,
        "ON_MAIN": "0" if on_main else "1",
        "ON_STAGING": "0" if on_staging else "1",
    }
    done = subprocess.run(["bash", "-e", "-c", script], env=env, capture_output=True, text=True)
    assert (done.returncode == 0) is passes, done.stdout + done.stderr


def test_production_deploys_wait_for_header_phase_b(tmp_path):
    step = _deploy_step("Production waits for Caddy header phase B")
    assert step["if"] == "env.ENVIRONMENT == 'production'"
    (tmp_path / "ops").mkdir()
    caddyfile = tmp_path / "ops" / "Caddyfile"
    for text, passes in (
        (
            CADDYFILE.read_text(encoding="utf-8"),
            not any(
                "header -Content-Security-Policy"
                in code_lines(site_block(CADDYFILE.read_text(encoding="utf-8"), prod))
                for prod in (PROD, OLD_PROD)
            ),
        ),
        ("purelyfreefonts.com {\n\theader -Content-Security-Policy\n}\n", False),
        ("purelyfreefonts.com {\n\timport tff_site\n}\n", True),
    ):
        caddyfile.write_text(text, encoding="utf-8")
        done = subprocess.run(
            ["bash", "-e", "-c", step["run"]], cwd=tmp_path, capture_output=True, text=True
        )
        assert (done.returncode == 0) is passes, done.stdout + done.stderr


def test_the_condition_reader_follows_actions_precedence():
    context = {"a": "x", "cancelled()": False}
    assert evaluate("${{ !cancelled() && a == 'x' }}", context)
    assert evaluate("${{ a == 'x' || a == 'y' && missing == 'z' }}", context)  # && binds first
    assert not evaluate("${{ (a == 'y' || a != 'y') && missing == 'x' }}", context)
    assert evaluate("${{ missing != 'rollback' }}", context)


@pytest.mark.parametrize(
    ("event", "ref", "action", "switch", "runs"),
    [
        ("push", "main", None, None, False),  # before the soft launch: nothing happens
        ("push", "main", None, "off", False),
        ("push", "main", None, "on", True),
        ("push", "staging", None, None, True),
        ("workflow_dispatch", "main", "deploy", None, True),
        ("workflow_dispatch", "staging", "deploy", None, True),
        ("workflow_dispatch", "main", "rollback", "on", False),  # a rollback builds nothing
    ],
)
def test_pushes_to_main_deploy_production_only_after_the_soft_launch(
    event, ref, action, switch, runs
):
    condition = workflow(DEPLOY_YML)["jobs"]["build"]["if"]
    context = {
        "github.event_name": event,
        "github.ref_name": ref,
        "inputs.action": action,
        "vars.PRODUCTION_DEPLOYS": switch,
    }
    assert evaluate(condition, context) is runs


@pytest.mark.parametrize(
    ("build", "action", "runs"),
    [
        ("success", "deploy", True),
        ("failure", "deploy", False),
        ("skipped", "deploy", False),  # a push to main before the soft launch
        ("skipped", "rollback", True),
    ],
)
def test_deploy_follows_a_good_build_or_rolls_back_without_one(build, action, runs):
    condition = workflow(DEPLOY_YML)["jobs"]["deploy"]["if"]
    context = {"cancelled()": False, "needs.build.result": build, "inputs.action": action}
    assert evaluate(condition, context) is runs


def test_the_live_test_follows_every_deploy_and_rollback():
    # Its condition must name deploy's result itself: with the implicit success(), a skipped
    # build (every rollback) would skip the live test too.
    condition = workflow(DEPLOY_YML)["jobs"]["live"]["if"]
    assert "needs.deploy.result" in condition
    for result, runs in (("success", True), ("failure", False), ("skipped", False)):
        context = {"cancelled()": False, "needs.deploy.result": result}
        assert evaluate(condition, context) is runs, result
    assert not evaluate(condition, {"cancelled()": True, "needs.deploy.result": "success"})


def test_deploy_uses_only_the_forced_command_verbs():
    scripts = "\n".join(s.get("run", "") for _, s in steps(workflow(DEPLOY_YML)))
    commands = "\n".join(line for line in scripts.splitlines() if not line.lstrip().startswith("#"))
    verbs = set(re.findall(r"(?<![\w-])receive (\w+)", commands))
    assert verbs == {"plan", "upload", "activate", "rollback", "status"}
    for option in ("IdentitiesOnly=yes", "BatchMode=yes", "StrictHostKeyChecking=yes"):
        assert option in scripts


def test_dependabot_updates_the_actions():
    data = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))
    assert data["version"] == 2
    [update] = [u for u in data["updates"] if u["package-ecosystem"] == "github-actions"]
    assert update["directory"] == "/"
    assert update["schedule"]["interval"] in {"daily", "weekly", "monthly"}


# ------------------------------------------------------------------------ with real Caddy


@pytest.fixture(scope="module")
def caddy_bin() -> str:
    given = os.environ.get("TFF_CADDY_BIN")
    if given:
        path = Path(given).resolve()
        if not os.access(path, os.X_OK):
            pytest.fail(f"TFF_CADDY_BIN={given} is not an executable")
        return str(path)
    found = shutil.which("caddy")
    if not found:
        pytest.skip("no Caddy binary (set TFF_CADDY_BIN)")
    return found


@pytest.fixture(scope="module")
def openssl() -> str:
    found = shutil.which("openssl")
    if not found:
        pytest.skip("no openssl for the stand-in origin certificate")
    return found


def caddy_env(home: Path, **extra: str) -> dict[str, str]:
    home.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("TFF_")}
    env.update(
        HOME=str(home), XDG_CONFIG_HOME=str(home / "config"), XDG_DATA_HOME=str(home / "data")
    )
    env.update(extra)
    return env


@contextmanager
def running_caddy(
    caddy: str, config: Path, env: dict[str, str], ports: list[int]
) -> Iterator[None]:
    log = config.with_suffix(".log")
    with log.open("wb") as out:
        process = subprocess.Popen(
            [caddy, "run", "--config", str(config), "--adapter", "caddyfile"],
            cwd=config.parent,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 15
        waiting = list(ports)
        while waiting:
            if process.poll() is not None:
                pytest.fail(f"caddy exited:\n{log.read_text(errors='replace')}")
            try:
                socket.create_connection(("127.0.0.1", waiting[0]), timeout=0.5).close()
                waiting.pop(0)
            except OSError:
                if time.monotonic() > deadline:
                    pytest.fail(f"caddy never listened:\n{log.read_text(errors='replace')}")
                time.sleep(0.05)
        yield
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


class _SNIConnection(http.client.HTTPSConnection):
    """HTTPS to 127.0.0.1 while sending the site's name in SNI and Host."""

    def __init__(self, name: str, port: int, context: ssl.SSLContext) -> None:
        super().__init__(name, port, context=context, timeout=10)
        self._loopback = ("127.0.0.1", port)

    def connect(self) -> None:
        sock = socket.create_connection(self._loopback, self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def fetch(conn: http.client.HTTPConnection, path: str, **headers: str) -> tuple[int, Any, bytes]:
    conn.request("GET", path, headers=headers)
    response = conn.getresponse()
    return response.status, response.headers, response.read()


def server_copy(tmp: Path, openssl: str) -> Path:
    """ops/Caddyfile and site.caddy side by side, as in /tmp/caddy-new/, with the server's
    absolute paths moved into ``tmp`` and stand-ins for the files the server holds."""
    etc, logs = tmp / "etc", tmp / "log"
    (etc / "certs").mkdir(parents=True)
    logs.mkdir()
    shutil.copy(SITE_CADDY, etc / "site.caddy")
    text = CADDYFILE.read_text(encoding="utf-8")
    text = text.replace("/etc/caddy/", f"{etc}/").replace("/var/log/caddy/", f"{logs}/")
    (etc / "Caddyfile").write_text(text, encoding="utf-8")
    (etc / "cloudflare-ips.caddy").write_text(
        "trusted_proxies static 173.245.48.0/20 2400:cb00::/32\n", encoding="utf-8"
    )
    # Two certificates, as on the server, so a block with the other domain's snippet fails.
    for pair, names in (("origin", OLD_CERT_NAMES), ("purely", CERT_NAMES)):
        subprocess.run(
            [openssl, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
             "-nodes", "-days", "2", "-subj", f"/CN={names[0]}",
             "-addext", "subjectAltName=" + ",".join(f"DNS:{name}" for name in names),
             "-keyout", str(etc / "certs" / f"{pair}.key"),
             "-out", str(etc / "certs" / f"{pair}.pem")],
            check=True,
            capture_output=True,
        )  # fmt: skip
    return etc / "Caddyfile"


def test_the_caddyfile_validates_beside_site_caddy(caddy_bin, openssl, tmp_path):
    config = server_copy(tmp_path, openssl)
    result = subprocess.run(
        [caddy_bin, "validate", "--config", str(config), "--adapter", "caddyfile"],
        env=caddy_env(tmp_path / "home"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Valid configuration" in result.stdout + result.stderr


def test_production_staging_and_redirects_serve_what_the_design_says(caddy_bin, openssl, tmp_path):
    config = server_copy(tmp_path, openssl)
    site = make_site(tmp_path / "site")
    http_port, https_port = free_port(), free_port()
    text = config.read_text(encoding="utf-8")
    text = text.replace(PROD_ROOT, str(site)).replace(STAGING_ROOT, str(site))
    text = text.replace(
        "{\n\tservers {",
        f"{{\n\tadmin off\n\tpersist_config off\n\thttp_port {http_port}\n"
        f"\thttps_port {https_port}\n\tservers {{",
        1,
    )
    config.write_text(text, encoding="utf-8")
    context = ssl.create_default_context(cafile=str(config.parent / "certs" / "origin.pem"))
    context.load_verify_locations(cafile=str(config.parent / "certs" / "purely.pem"))
    seen: dict[tuple[str, str], tuple[int, Any, bytes]] = {}
    with running_caddy(caddy_bin, config, caddy_env(tmp_path / "home"), [https_port]):
        for name in (*(address for address, _, _ in SITES), "trulyfreefonts.net", WWW):
            conn = _SNIConnection(name, https_port, context)
            for path in ("/", "/missing", JS_PATH, "/a/b?c=1"):
                # (Caddy reads the first two itself; the plain-IP checks below cover them.)
                sent = {h: f"dropped-{h.lower()}" for h in DROPPED_HEADERS[2:]}
                seen[name, path] = fetch(conn, path, **{"Accept-Encoding": "gzip"}, **sent)
            conn.close()

    for name, _, _ in SITES:
        status, headers, body = seen[name, "/"]
        assert status == 200
        assert headers["Content-Encoding"] == "gzip"
        assert gzip.decompress(body) == (site / "index.html").read_bytes()
        assert headers["Cache-Control"] == REVALIDATE
        assert headers["Permissions-Policy"] == HEADERS.security["Permissions-Policy"]
        assert headers["X-Frame-Options"] == "DENY"
        assert "Server" not in headers
        status, headers, body = seen[name, JS_PATH]
        assert (status, headers["Cache-Control"]) == (200, IMMUTABLE)
        status, headers, body = seen[name, "/missing"]
        assert status == 404
        assert body == (site / "404.html").read_bytes()
        assert headers["Cache-Control"] == REVALIDATE
        assert headers["Content-Security-Policy"] == CSP  # the error route re-applies it
    # Staging: every header, and noindex on every response.
    for staging in (STAGING, OLD_STAGING):
        for path in ("/", JS_PATH, "/missing"):
            headers = seen[staging, path][1]
            assert headers["Content-Security-Policy"] == CSP
            assert headers["X-Robots-Tag"] == "noindex, nofollow"
    # Production: no noindex; no CSP while the Caddyfile is in header phase A.
    for prod in (PROD, OLD_PROD):
        phase_a = "header -Content-Security-Policy" in code_lines(site_block(text, prod))
        for path in ("/", JS_PATH):
            headers = seen[prod, path][1]
            assert "X-Robots-Tag" not in headers
            assert headers.get("Content-Security-Policy") == (None if phase_a else CSP)
    status, headers, _ = seen["trulyfreefonts.net", "/a/b?c=1"]
    assert (status, headers["Location"]) == (301, "https://trulyfreefonts.com/a/b?c=1")
    status, headers, _ = seen[WWW, "/a/b?c=1"]
    assert (status, headers["Location"]) == (301, "https://purelyfreefonts.com/a/b?c=1")

    log = (tmp_path / "log" / "access.log").read_text(encoding="utf-8")
    for name, _, _ in SITES:
        assert f'"host":"{name}:{https_port}"' in log, name
    assert '"client_ip":"127.0.0.0"' in log
    assert "remote_port" not in log
    assert '"Accept-Encoding":["gzip"]' in log  # headers are logged, but none of these
    for name in DROPPED_HEADERS[2:]:
        assert f"dropped-{name.lower()}" not in log, name


@pytest.fixture
def ci_caddy(caddy_bin, tmp_path) -> Iterator[tuple[str, Path]]:
    """Caddy running ops/caddy/ci.Caddyfile on a free port over a stand-in site."""
    site = make_site(tmp_path / "site")
    config = tmp_path / "conf" / "ci.Caddyfile"
    config.parent.mkdir()
    shutil.copy(CI_CADDYFILE, config)
    shutil.copy(SITE_CADDY, config.parent / "site.caddy")
    port = free_port()
    env = caddy_env(tmp_path / "home", TFF_SITE_DIR=str(site), TFF_CADDY_PORT=str(port))
    with running_caddy(caddy_bin, config, env, [port]):
        yield f"127.0.0.1:{port}", site


def test_the_ci_caddyfile_compresses_the_home_page_and_keeps_the_headers(ci_caddy):
    address, site = ci_caddy
    conn = http.client.HTTPConnection(address, timeout=10)
    status, headers, body = fetch(conn, "/", **{"Accept-Encoding": "gzip"})
    assert status == 200
    assert headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(body) == (site / "index.html").read_bytes()
    assert headers["Cache-Control"] == REVALIDATE
    assert headers["Content-Security-Policy"] == CSP
    status, headers, body = fetch(conn, "/")
    assert "Content-Encoding" not in headers
    assert body == (site / "index.html").read_bytes()
    conn.close()


PREVIEW_PATHS = [
    "/",
    "/version.txt",
    JS_PATH,
    "/methodology/",
    "/missing",
    "/assets/missing.0123456789.js",
    "/empty/",
]


def test_tff_site_serve_sends_the_headers_caddy_sends(ci_caddy):
    address, site = ci_caddy
    server = serve.make_server(site, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    names = [*HEADERS.security, *HEADERS.remove, "Cache-Control", "Content-Type"]
    try:
        caddy = http.client.HTTPConnection(address, timeout=10)
        preview = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        for path in PREVIEW_PATHS:
            want_status, want, want_body = fetch(caddy, path)
            got_status, got, got_body = fetch(preview, path)
            assert got_status == want_status, path
            assert got_body == want_body, path
            for name in names:
                assert got.get(name) == want.get(name), f"{path}: {name}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
