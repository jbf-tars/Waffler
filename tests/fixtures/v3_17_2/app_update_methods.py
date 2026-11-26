"""Waffler 3.17.2's update check and download start, copied unchanged from
`git show v3.17.2:app.py` (Api.check_for_updates, Api.start_update_download).
These are what every 3.17.2 user runs to reach 4.0.0; tests/test_update_from_3_17_2.py
drives them against what the 4.0.0 release workflows publish."""

def check_for_updates(self) -> dict:
    """Check GitHub releases for a newer version.

    Fetches the full releases list (not /releases/latest, which returns
    404 on this repo because the 'latest' flag has never been set on any
    release). Picks the highest-semver non-draft, non-prerelease tag.
    Always returns current_version; returns an error field on failure.
    """
    from src import __version__
    current_version = __version__

    # Surface a silently-failed update through the call the UI already
    # makes on startup, rather than adding a new bridge method. Set by the
    # startup reconciliation; read once so the warning does not persist
    # after the user has seen it.
    _failed = globals().pop("_UPDATE_FAILURE_NOTICE", None)

    def parse_ver(v: str):
        # Extract the leading numeric dotted version, tolerating tag
        # suffixes like "v3.14.63-hotfix" or "3.14.63b". The old
        # int()-on-every-part threw on any such tag → coerced to (0,) →
        # wrong "latest" pick / spurious infinite "update available".
        import re as _re
        m = _re.match(r"v?(\d+(?:\.\d+)*)", v or "")
        if not m:
            return (0,)
        try:
            return tuple(int(x) for x in m.group(1).split("."))
        except Exception:
            return (0,)

    try:
        import requests
        r = requests.get(
            "https://api.github.com/repos/jbf-tars/waffler/releases",
            timeout=10,
            headers={"Accept": "application/vnd.github.v3+json"},
            params={"per_page": 20},
        )
        if r.status_code != 200:
            # 403 is GitHub's rate limit for unauthenticated checks. The
            # status goes to the log; the user gets a sentence.
            _log_to_file(f"[update] check failed: GitHub returned HTTP {r.status_code}")
            return {
                "update_available": False,
                "current_version": current_version,
                "error": update_check_error(),
            }
        releases = r.json()
        if not isinstance(releases, list) or not releases:
            _log_to_file("[update] check failed: no releases in the GitHub response")
            return {
                "update_available": False,
                "current_version": current_version,
                "error": update_check_error(),
            }

        # Filter out drafts and prereleases, pick highest semver
        candidates = [
            rel for rel in releases
            if not rel.get("draft") and not rel.get("prerelease") and rel.get("tag_name")
        ]
        if not candidates:
            _log_to_file("[update] check failed: no published releases")
            return {
                "update_available": False,
                "current_version": current_version,
                "error": update_check_error(),
            }

        latest_release = max(candidates, key=lambda rel: parse_ver(rel["tag_name"]))
        latest_version = latest_release["tag_name"].lstrip("v")

        if parse_ver(latest_version) > parse_ver(current_version):
            import platform as _plat
            suffix = ".dmg" if _plat.system() == "Darwin" else ".exe"
            # Only a real platform asset is a valid download target —
            # never fall back to html_url (the release *web page*), which
            # would download HTML and then try to "install" it. If no
            # asset matches, download_url stays "" and JS falls back to
            # opening release_url in the browser.
            download_url = ""
            for asset in latest_release.get("assets", []):
                if asset.get("name", "").endswith(suffix):
                    download_url = asset.get("browser_download_url", "")
                    break
            return {
                "update_available": True,
                "latest_version": latest_version,
                "current_version": current_version,
                "download_url": download_url,
                "release_url": latest_release.get("html_url", ""),
                # No installer for this platform in the release: the UI
                # opens the release page instead of trying to download.
                "no_installer": not download_url,
                "no_installer_message": "" if download_url else UPDATE_NO_INSTALLER,
            }
        return {
            "update_available": False,
            "current_version": current_version,
            "latest_version": latest_version,
            "last_update_failed": _failed,
        }
    except Exception as e:
        _log_to_file(f"[update] check failed: {e}")
        return {
            "update_available": False,
            "current_version": current_version,
            "error": update_check_error(e),
        }

def start_update_download(self, url: str) -> dict:
    """Begin downloading the update installer in the background.
    JS polls get_update_progress() to render a progress bar.

    This method is reachable from the webview JS bridge, so the URL is
    validated against a GitHub-release allowlist — otherwise a crafted
    call (or a tampered check_for_updates response) could make us download
    an arbitrary file. We accept only https GitHub release-asset URLs; the
    downloaded file is additionally signature-verified before it is ever
    executed (see updater.install_and_restart)."""
    from urllib.parse import urlparse
    if not (url or "").strip():
        # The release had no installer for this computer (check_for_updates
        # sets no_installer). Not an attack, so don't say "untrusted".
        _log_to_file("[update] download requested with no installer URL")
        return {"ok": False, "error": UPDATE_NO_INSTALLER, "download_page": DOWNLOAD_PAGE}
    p = urlparse(url or "")
    host = (p.hostname or "").lower()
    host_ok = host == "github.com" or host.endswith(".githubusercontent.com")
    path_ok = host != "github.com" or "/releases/download/" in p.path
    if p.scheme != "https" or not host_ok or not path_ok:
        _log_to_file(f"[update] refused untrusted download URL: {url[:120]}")
        return {"ok": False, "error": UPDATE_DOWNLOAD_FAILED, "download_page": DOWNLOAD_PAGE}
    try:
        from src import updater
        _log_to_file(f"[update] start_download requested: {url[:120]}")
        updater.start_download(url)
        return {"ok": True}
    except Exception as e:
        _log_to_file(f"[update] start_download failed: {e}")
        return {"ok": False, "error": UPDATE_DOWNLOAD_FAILED, "download_page": DOWNLOAD_PAGE}