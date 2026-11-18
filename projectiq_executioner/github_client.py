"""GitHub-only, public-repository snapshot retrieval. No git checkout or execution."""
from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .models import RepositorySource


class IngestionError(RuntimeError):
    """A repository cannot be retrieved or its input is unsafe."""


_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_SHA = re.compile(r"^[a-fA-F0-9]{40}$")


def parse_github_url(value: str) -> tuple[str, str]:
    """Accept https://github.com/owner/repo[.git] only; reject URL pivots."""
    parts = urlsplit(value.strip())
    if (parts.scheme != "https" or parts.netloc.lower() != "github.com"
            or parts.username or parts.password or parts.query or parts.fragment):
        raise IngestionError("Expected an HTTPS GitHub repository URL: https://github.com/owner/repo")
    segments = parts.path.strip("/").split("/")
    if len(segments) != 2 or any("%" in p or "\\" in p for p in segments):
        raise IngestionError("Only repository root URLs are accepted (no subpaths or encoded segments).")
    owner, repo = segments
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not _OWNER.fullmatch(owner) or not _REPO.fullmatch(repo) or repo in {".", ".."}:
        raise IngestionError("Invalid GitHub repository owner/name.")
    return owner, repo


class _RestrictedRedirect(HTTPRedirectHandler):
    ALLOWED = {"api.github.com", "codeload.github.com"}

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urlsplit(newurl)
        if u.scheme != "https" or u.hostname not in self.ALLOWED or u.port not in (None, 443):
            raise IngestionError("Unexpected redirect outside approved GitHub download hosts.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class DownloadLimits:
    max_zip_bytes: int = 30 * 1024 * 1024


class GitHubClient:
    def __init__(self, timeout: int = 25, limits: DownloadLimits | None = None):
        self.timeout = timeout
        self.limits = limits or DownloadLimits()
        self._opener = build_opener(_RestrictedRedirect())

    def _request(self, url: str, *, api: bool = False):
        host = urlsplit(url).hostname
        if urlsplit(url).scheme != "https" or host not in ("api.github.com", "codeload.github.com"):
            raise IngestionError("Only approved HTTPS GitHub API/archive hosts are allowed.")
        headers = {"User-Agent": "ProjectIQ-Executioner-Phase1/1.0", "Accept": "application/vnd.github+json" if api else "application/zip"}
        if api and os.getenv("GITHUB_TOKEN"):
            headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
        return Request(url, headers=headers)

    def _open(self, request):
        try:
            return self._opener.open(request, timeout=self.timeout)
        except HTTPError as exc:
            if exc.code == 403 or exc.code == 429:
                raise IngestionError("GitHub request was rate-limited or forbidden. Set GITHUB_TOKEN for API requests, or try later.") from exc
            if exc.code == 404:
                raise IngestionError("GitHub repository/ref not found, private, or inaccessible.") from exc
            raise IngestionError(f"GitHub returned HTTP {exc.code}.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise IngestionError(f"Network request failed: {type(exc).__name__}.") from exc

    def _get_json(self, path: str) -> dict:
        url = "https://api.github.com" + path
        with self._open(self._request(url, api=True)) as response:
            body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise IngestionError("GitHub API response exceeds size limit.")
        try:
            result = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise IngestionError("Invalid GitHub API JSON response.") from exc
        if not isinstance(result, dict):
            raise IngestionError("Unexpected GitHub response type.")
        return result

    def resolve(self, url: str, ref: str | None = None) -> RepositorySource:
        owner, repo = parse_github_url(url)
        metadata = self._get_json(f"/repos/{owner}/{repo}")
        if metadata.get("private") is True:
            raise IngestionError("Private repositories are not supported in Phase 1.")
        default_branch = metadata.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch:
            raise IngestionError("GitHub repository did not provide a default branch.")
        chosen = ref or default_branch
        if len(chosen) > 200 or any(ord(c) < 32 for c in chosen):
            raise IngestionError("Invalid ref.")
        commit = self._get_json(f"/repos/{owner}/{repo}/commits/{quote(chosen, safe='')}")
        sha = commit.get("sha")
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise IngestionError("GitHub failed to return a full commit SHA.")
        return RepositorySource("github", f"{owner}/{repo}", f"https://github.com/{owner}/{repo}", ref, chosen, sha.lower())

    def download(self, source: RepositorySource, destination_zip: Path) -> None:
        if source.kind != "github" or not source.commit_sha or not _SHA.fullmatch(source.commit_sha):
            raise IngestionError("A resolved GitHub commit is required.")
        owner, repo = source.repository.split("/", 1)
        url = f"https://codeload.github.com/{owner}/{repo}/zip/{source.commit_sha}"
        target = Path(destination_zip)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._open(self._request(url)) as response, target.open("wb") as out:
                total = 0
                while chunk := response.read(256 * 1024):
                    total += len(chunk)
                    if total > self.limits.max_zip_bytes:
                        raise IngestionError("Repository ZIP exceeds the configured download size limit.")
                    out.write(chunk)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
