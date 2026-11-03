# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import base64
import http.client
import json
import os
import secrets
import ssl
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from .errors import LpmError

GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
# Publishing synchronizes the user's packages fork with the central repository.
# GitHub requires the workflow scope when that sync includes files under
# .github/workflows, even though the fork itself is public.
GITHUB_OAUTH_SCOPE = "public_repo workflow"
GITHUB_CLIENT_ID_ENV = "LPM_GITHUB_CLIENT_ID"
DEFAULT_GITHUB_CLIENT_ID = "Ov23li2je0k6yeO2cisv"


def github_login(
    *, client_id: str | None = None, output: TextIO | None = None
) -> Path:
    client_id = (
        client_id or os.environ.get(GITHUB_CLIENT_ID_ENV) or DEFAULT_GITHUB_CLIENT_ID
    ).strip()
    if not client_id:
        raise LpmError(
            "GitHub OAuth Client ID is not configured; set LPM_GITHUB_CLIENT_ID "
            "to an OAuth App with Device Flow enabled"
        )

    device = _oauth_request(
        GITHUB_DEVICE_CODE_URL,
        {"client_id": client_id, "scope": GITHUB_OAUTH_SCOPE},
    )
    device_code = _required_string(device, "device_code")
    user_code = _required_string(device, "user_code")
    verification_uri = _required_string(device, "verification_uri")
    expires_in = _required_positive_int(device, "expires_in")
    interval = _required_positive_int(device, "interval")
    if not verification_uri.startswith("https://github.com/"):
        raise LpmError("GitHub returned an invalid device verification URL")

    stream = output or sys.stdout
    print(f"GitHub verification code: {user_code}", file=stream)
    print(f"Open {verification_uri} to authorize LPM.", file=stream)
    try:
        webbrowser.open(verification_uri)
    except (OSError, webbrowser.Error):
        pass
    print("Waiting for GitHub authorization...", file=stream)

    deadline = time.monotonic() + expires_in
    poll_interval = interval
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        response = _oauth_request(
            GITHUB_ACCESS_TOKEN_URL,
            {
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        token = response.get("access_token")
        if isinstance(token, str) and token:
            path = _save_stored_token(token)
            print(f"GitHub credentials saved to {path}.", file=stream)
            return path

        error = response.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            poll_interval += 5
            continue
        if error == "access_denied":
            raise LpmError("GitHub authorization was denied")
        if error == "expired_token":
            raise LpmError("GitHub verification code expired; run lpm login again")
        description = response.get("error_description")
        detail = f": {description}" if isinstance(description, str) else ""
        raise LpmError(f"GitHub OAuth failed ({error or 'invalid response'}){detail}")
    raise LpmError("GitHub verification code expired; run lpm login again")


def credentials_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base / "lpm" / "credentials.json"


def _oauth_request(url: str, fields: dict[str, str]) -> dict[str, Any]:
    request = Request(
        url,
        data=urlencode(fields).encode("ascii"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "lilygo-lpm",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read()
    except HTTPError as exc:
        body = exc.read()
        raise LpmError(f"GitHub OAuth request failed with HTTP {exc.code}") from exc
    except (OSError, URLError) as exc:
        raise LpmError(f"GitHub OAuth request failed: {exc}") from exc
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LpmError("GitHub OAuth returned an invalid JSON response") from exc
    if not isinstance(payload, dict):
        raise LpmError("GitHub OAuth returned an invalid response")
    return payload


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise LpmError(f"GitHub OAuth response has no valid {key}")
    return value


def _required_positive_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise LpmError(f"GitHub OAuth response has no valid {key}")
    return value


def _save_stored_token(token: str) -> Path:
    path = credentials_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"github.com": {"oauth_token": token}}, handle, indent=2)
            handle.write("\n")
        temporary.replace(path)
        path.chmod(0o600)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise LpmError(f"cannot save GitHub credentials to {path}: {exc}") from exc
    return path


def _read_stored_token() -> str | None:
    path = credentials_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LpmError(f"cannot read GitHub credentials from {path}: {exc}") from exc
    host = payload.get("github.com") if isinstance(payload, dict) else None
    token = host.get("oauth_token") if isinstance(host, dict) else None
    if not isinstance(token, str) or not token:
        raise LpmError(f"GitHub credentials are invalid: {path}")
    return token


class GitHubClient:
    def __init__(
        self,
        token: str,
        *,
        api_url: str = "https://api.github.com",
        upload_url: str = "https://uploads.github.com",
    ) -> None:
        if not token:
            raise LpmError("GitHub authentication is required; run lpm login")
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.upload_url = upload_url.rstrip("/")

    @classmethod
    def authenticated(cls) -> GitHubClient:
        token = (
            os.environ.get("GH_TOKEN")
            or os.environ.get("GITHUB_TOKEN")
            or _read_stored_token()
        )
        if not token:
            raise LpmError(
                "GitHub authentication is required; run lpm login or set GH_TOKEN"
            )
        return cls(token)

    def request(
        self,
        method: str,
        path: str,
        payload: object | None = None,
        *,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        url = path if path.startswith("https://") else f"{self.api_url}{path}"
        data = None
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "lilygo-lpm",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read()
                status = response.status
        except HTTPError as exc:
            body = exc.read()
            if exc.code in expected:
                status = exc.code
            else:
                raise self._http_error(exc.code, body, method=method, url=url) from exc
        except (OSError, URLError) as exc:
            raise LpmError(f"GitHub request failed: {exc}") from exc
        if status not in expected:
            raise self._http_error(status, body, method=method, url=url)
        if not body:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LpmError("GitHub returned an invalid JSON response") from exc

    @staticmethod
    def _http_error(
        status: int,
        body: bytes,
        *,
        method: str | None = None,
        url: str | None = None,
    ) -> LpmError:
        detail = ""
        try:
            payload = json.loads(body.decode("utf-8"))
            if isinstance(payload, dict):
                detail = str(payload.get("message", ""))
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        suffix = f": {detail}" if detail else ""
        request = f" ({method} {url})" if method and url else ""
        return LpmError(
            f"GitHub API request failed with HTTP {status}{suffix}{request}"
        )

    def current_user(self) -> str:
        payload = self.request("GET", "/user")
        login = payload.get("login") if isinstance(payload, dict) else None
        if not isinstance(login, str) or not login:
            raise LpmError("GitHub did not return the authenticated user")
        return login

    def repository(
        self, full_name: str, *, allow_missing: bool = False
    ) -> dict[str, Any] | None:
        encoded = "/".join(quote(part, safe="") for part in full_name.split("/"))
        expected = (200, 404) if allow_missing else (200,)
        payload = self.request("GET", f"/repos/{encoded}", expected=expected)
        return payload if isinstance(payload, dict) and payload.get("id") else None

    def ensure_fork(self, upstream: str, owner: str) -> str:
        repository_name = upstream.rsplit("/", 1)[-1]
        fork_name = f"{owner}/{repository_name}"
        existing = self.repository(fork_name, allow_missing=True)
        if existing is not None:
            parent = existing.get("parent")
            parent_name = parent.get("full_name") if isinstance(parent, dict) else None
            source = existing.get("source")
            source_name = source.get("full_name") if isinstance(source, dict) else None
            if upstream.lower() not in {
                str(parent_name).lower(),
                str(source_name).lower(),
            }:
                raise LpmError(f"{fork_name} exists but is not a fork of {upstream}")
            return fork_name

        encoded = "/".join(quote(part, safe="") for part in upstream.split("/"))
        self.request("POST", f"/repos/{encoded}/forks", {}, expected=(202,))
        for _ in range(30):
            existing = self.repository(fork_name, allow_missing=True)
            if existing is not None:
                return fork_name
            time.sleep(1)
        raise LpmError(f"timed out waiting for GitHub to create fork {fork_name}")

    def sync_fork(self, repository: str, *, branch: str) -> None:
        """Fast-forward a fork branch to its upstream branch before writing."""
        encoded_repo = "/".join(quote(part, safe="") for part in repository.split("/"))
        self.request(
            "POST",
            f"/repos/{encoded_repo}/merge-upstream",
            {"branch": branch},
            expected=(200,),
        )

    def branch_base(self, repository: str) -> tuple[str, str, str]:
        repo = self.repository(repository)
        if repo is None:
            raise LpmError(f"GitHub repository does not exist: {repository}")
        branch = repo.get("default_branch")
        if not isinstance(branch, str) or not branch:
            raise LpmError(f"GitHub repository has no default branch: {repository}")
        encoded_repo = "/".join(quote(part, safe="") for part in repository.split("/"))
        ref = self.request(
            "GET", f"/repos/{encoded_repo}/git/ref/heads/{quote(branch, safe='')}"
        )
        commit_sha = ref.get("object", {}).get("sha") if isinstance(ref, dict) else None
        if not isinstance(commit_sha, str):
            raise LpmError(f"cannot determine {repository}'s default branch revision")
        commit = self.request("GET", f"/repos/{encoded_repo}/git/commits/{commit_sha}")
        tree_sha = (
            commit.get("tree", {}).get("sha") if isinstance(commit, dict) else None
        )
        if not isinstance(tree_sha, str):
            raise LpmError(f"cannot determine {repository}'s default branch tree")
        return branch, commit_sha, tree_sha

    def commit_files(
        self,
        repository: str,
        *,
        branch: str,
        parent_sha: str,
        base_tree_sha: str,
        files: dict[str, bytes],
        message: str,
    ) -> str:
        encoded_repo = "/".join(quote(part, safe="") for part in repository.split("/"))
        tree_entries: list[dict[str, str]] = []
        for path, content in sorted(files.items()):
            blob = self.request(
                "POST",
                f"/repos/{encoded_repo}/git/blobs",
                {
                    "content": base64.b64encode(content).decode("ascii"),
                    "encoding": "base64",
                },
                expected=(201,),
            )
            tree_entries.append(
                {"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]}
            )
        tree = self.request(
            "POST",
            f"/repos/{encoded_repo}/git/trees",
            {"base_tree": base_tree_sha, "tree": tree_entries},
            expected=(201,),
        )
        commit = self.request(
            "POST",
            f"/repos/{encoded_repo}/git/commits",
            {"message": message, "tree": tree["sha"], "parents": [parent_sha]},
            expected=(201,),
        )
        self.request(
            "POST",
            f"/repos/{encoded_repo}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
            expected=(201,),
        )
        return str(commit["sha"])

    def file_json(
        self, repository: str, path: str, *, ref: str
    ) -> dict[str, Any] | None:
        encoded_repo = "/".join(quote(part, safe="") for part in repository.split("/"))
        encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
        payload = self.request(
            "GET",
            f"/repos/{encoded_repo}/contents/{encoded_path}?{urlencode({'ref': ref})}",
            expected=(200, 404),
        )
        if not isinstance(payload, dict) or "content" not in payload:
            return None
        try:
            content = base64.b64decode(str(payload["content"]), validate=False)
            parsed = json.loads(content.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LpmError(f"registry file is not valid JSON: {path}") from exc
        if not isinstance(parsed, dict):
            raise LpmError(f"registry file must contain an object: {path}")
        return parsed

    def create_release(
        self,
        repository: str,
        *,
        tag: str,
        target: str,
        title: str,
        body: str,
        prerelease: bool,
    ) -> dict[str, Any]:
        encoded_repo = "/".join(quote(part, safe="") for part in repository.split("/"))
        existing = self.request(
            "GET",
            f"/repos/{encoded_repo}/releases/tags/{quote(tag, safe='')}",
            expected=(200, 404),
        )
        if isinstance(existing, dict) and existing.get("id"):
            return existing
        payload = self.request(
            "POST",
            f"/repos/{encoded_repo}/releases",
            {
                "tag_name": tag,
                "target_commitish": target,
                "name": title,
                "body": body,
                "draft": False,
                "prerelease": prerelease,
            },
            expected=(201,),
        )
        if not isinstance(payload, dict):
            raise LpmError("GitHub did not return the created release")
        return payload

    def ensure_release_asset(
        self,
        repository: str,
        release: dict[str, Any],
        *,
        path: Path,
        name: str,
        content_type: str,
    ) -> str:
        assets = release.get("assets", [])
        if isinstance(assets, list):
            for asset in assets:
                if isinstance(asset, dict) and asset.get("name") == name:
                    if asset.get("size") != path.stat().st_size:
                        raise LpmError(
                            f"GitHub release already contains a different asset: {name}"
                        )
                    url = asset.get("browser_download_url")
                    if isinstance(url, str):
                        return url
        release_id = release.get("id")
        if not isinstance(release_id, int):
            raise LpmError("GitHub release has no numeric id")
        encoded_repo = "/".join(quote(part, safe="") for part in repository.split("/"))
        url = f"{self.upload_url}/repos/{encoded_repo}/releases/{release_id}/assets?{urlencode({'name': name})}"
        response = self._upload_file(url, path, content_type)
        download_url = response.get("browser_download_url")
        if not isinstance(download_url, str):
            raise LpmError("GitHub did not return the uploaded asset URL")
        return download_url

    def _upload_file(self, url: str, path: Path, content_type: str) -> dict[str, Any]:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise LpmError("GitHub returned an invalid upload URL")
        connection = http.client.HTTPSConnection(
            parsed.hostname,
            parsed.port,
            timeout=600,
            context=ssl.create_default_context(),
        )
        request_path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        try:
            connection.putrequest("POST", request_path)
            connection.putheader("Accept", "application/vnd.github+json")
            connection.putheader("Authorization", f"Bearer {self.token}")
            connection.putheader("Content-Type", content_type)
            connection.putheader("Content-Length", str(path.stat().st_size))
            connection.putheader("User-Agent", "lilygo-lpm")
            connection.putheader("X-GitHub-Api-Version", "2022-11-28")
            connection.endheaders()
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    connection.send(chunk)
            response = connection.getresponse()
            body = response.read()
            if response.status != 201:
                raise self._http_error(
                    response.status,
                    body,
                    method="POST",
                    url=url,
                )
            payload = json.loads(body.decode("utf-8"))
        except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
            if isinstance(exc, LpmError):
                raise
            raise LpmError(f"GitHub release upload failed: {exc}") from exc
        finally:
            connection.close()
        if not isinstance(payload, dict):
            raise LpmError("GitHub returned an invalid upload response")
        return payload

    def create_pull_request(
        self,
        upstream: str,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> str:
        encoded_repo = "/".join(quote(part, safe="") for part in upstream.split("/"))
        payload = self.request(
            "POST",
            f"/repos/{encoded_repo}/pulls",
            {"title": title, "body": body, "head": head, "base": base},
            expected=(201,),
        )
        url = payload.get("html_url") if isinstance(payload, dict) else None
        if not isinstance(url, str):
            raise LpmError("GitHub did not return the pull request URL")
        return url
