# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Requestium is a library that merges Requests, Selenium, and Parsel: a `requests.Session` subclass that can lazily spawn a Selenium webdriver and move cookies between the two.

## Commands

```bash
uv sync                                # installs the package plus the `dev` dependency group
uv run pytest                          # full suite (parallel via -n auto, with coverage; see addopts in pyproject.toml)
uv run pytest tests/test_cookies.py::test_name -n 0   # single test, no xdist
uv run ssort --check requestium tests  # statement ordering check (pre-commit rewrites otherwise)
uv run ruff check .                    # lint (line length 160, very broad rule set)
uv run ruff format .
uv run mypy .
uv run bandit --confidence-level medium --recursive requestium
uv run pre-commit run --all-files      # check-*, ssort, ruff, bandit
```

`ssort` runs in pre-commit and reorders statements in modules/classes by dependency, so don't fight its ordering by hand.

Releases: bump `version` in `pyproject.toml`, merge, then push a `vX.Y.Z` tag. `.github/workflows/publish.yml` checks the tag matches the version, builds and smoke-tests the wheel and sdist, and publishes with PyPI Trusted Publishing (OIDC, `pypi` environment; no token stored anywhere).

## Tests need real browsers

Tests drive real Chrome and Firefox (Selenium Manager resolves drivers; CI sets `SE_AUTO_UPDATE=true` and uses `pytest-xvfb` on Linux). Install both browsers locally or the Firefox variants skip. The `session` fixture in `tests/conftest.py` is parametrized over `chrome-headless`, `chrome`, `firefox-headless`, `firefox`, so most tests run four times, and non-headless variants open visible windows. If a driver fails to start, those tests are skipped rather than failed.

The suite is fully offline. `conftest.py` provides one `LocalServer` (stdlib `ThreadingHTTPServer`, one per xdist worker) that serves pages, records every request, and doubles as an HTTP forward proxy. Browsers and requests sessions are pointed at it as their proxy so tests can use realistic hostnames like `example.com` with real cookie-domain rules; add test pages with `server.add_page(...)` rather than starting another server. Two cases can't go through the proxy and use Chrome's `--host-resolver-rules` instead: the HTTPS listener for secure-cookie tests (needs `openssl` on PATH for a throwaway cert, skipped otherwise) and `localhost` cookie tests. Test files are split by functionality (`test_add_cookie.py`, `test_cookies.py` for transfers, `test_proxy.py`, etc.), not by source module.

Chrome on CI's macOS runners intermittently dies at launch ("Chrome instance exited"), so `addopts` reruns failures matching only that message, up to twice (`pytest-rerunfailures`). A session-scoped autouse fixture (`driver_diagnostics`) lowers every driver's command timeout from 120s to 60s once its session starts, so a hung browser fails fast, and with `DRIVER_LOG_DIR` set gives every ChromeDriver and GeckoDriver a verbose log named after the test running when it started. CI sets it and uploads `driver-logs` as an artifact when a job fails.

CI runs the matrix of Python 3.11 to 3.14 on Ubuntu, Windows, and macOS, each with `highest` and `lowest-direct` dependency resolution, so keep the minimum versions in `dependencies` honest.

## Architecture

- `requestium_session.py` — `Session(requests.Session)`. The driver is created lazily on first access to the `driver` property:
  - With no `driver` arg, `_start_chrome_browser` builds `ChromeOptions` from `webdriver_options` (`arguments`, `extensions`, `prefs`, `experimental_options`, `binary_location`) and instantiates `RequestiumChrome`, a dynamic `type()` combining `DriverMixin` and `webdriver.Chrome`.
  - Requestium's own Chrome also gets the session's `proxies` (`--proxy-server`) and a non-default `User-Agent` as launch arguments, computed when the driver first starts.
  - With a user-supplied `driver` (Firefox, Remote, selenium-wire, etc.), the instance's `__class__` is swapped to a cached `type(..., (DriverMixin, cls), {})` subclass so it gains every helper, properties included.
  - `request()` (so every verb) records `_last_requests_url` (the default domain for `transfer_session_cookies_to_driver`) and wraps the result in `RequestiumResponse`; the per-verb stubs under `TYPE_CHECKING` exist only to narrow return types.
  - `close()` / `__exit__` quits the driver if one was started, including a user-supplied one.
- `requestium_mixin.py` — `ensure_add_cookie` navigates to the cookie's domain first (over https for `secure` cookies) and retries with the registrable domain via `tldextract`.
- `requestium.py` — back-compat shim re-exporting the above; tests import through `requestium.requestium`, so keep it.

Parsing is never cached: each `xpath`/`css`/`re` call rebuilds a `Selector`, because page content or encoding may change between calls.
