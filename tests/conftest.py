import contextlib
import json
import os
import re
import shutil
import ssl
import subprocess
import threading
from collections.abc import Generator, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, urlsplit

import pytest
import urllib3.exceptions
from _pytest.fixtures import FixtureRequest
from selenium import webdriver
from selenium.common import WebDriverException
from selenium.webdriver.chromium.service import ChromiumService
from selenium.webdriver.remote.remote_connection import RemoteConnection
from selenium.webdriver.remote.webdriver import WebDriver as RemoteWebDriver
from selenium.webdriver.support import expected_conditions as EC  # noqa: N812
from selenium.webdriver.support.wait import WebDriverWait

import requestium

if TYPE_CHECKING:
    from requestium.requestium_mixin import DriverMixin


EXAMPLE_HTML = """
<html>
    <head><title>The Internet</title></head>
    <body>
        <h1>Test Header 1</h1>
        <h2 id="test-header">Test Header 2</h2>
        <h3 class="test-header-3">Test Header 3</h3>
        <p class="body-text">Test Paragraph 1</p>
        <button>Click Me</button>
        <p><a href="example.com" name="link-paragraph">Test Link 1</a></p>
        <p><a href="example.com">Test Link 2</a></p>
    </body>
</html>
"""


# Chrome may first try http:// navigations over https (a CONNECT through the test proxy). Whether it falls back to http
# when that fails depends on the version: with balanced HTTPS-First mode auto-enabled (e.g. Chrome 152) it doesn't,
# leaving a blank page. Pass this to any Chrome that browses through the proxy (Chrome honors only one --disable-features).
DISABLE_HTTPS_UPGRADES = "--disable-features=HttpsUpgrades,HttpsFirstBalancedModeAutoEnable"


# Chrome for Testing with its sandbox enabled silently dropped navigations on GitHub's Windows runners (driver.get returned but
# the page stayed on data:, in 9 of 30 launches). The library must not disable the sandbox itself, so tests pass these.
CHROME_CI_ARGUMENTS = ("--no-sandbox", "--disable-dev-shm-usage")


class _Handler(BaseHTTPRequestHandler):
    """
    Serves pages and small JSON endpoints, and doubles as an HTTP forward proxy.

    Browsers and requests can be pointed at this server as their proxy (see LocalServer.proxy_url), which lets
    tests use realistic multi-label hostnames like example.com without touching the network. Proxied requests
    arrive with an absolute URL in the request line, so only its path is used for routing.

    Routes:
        /                     the example page (redirects www.<host> to <host>, like many real sites do)
        /echo                 JSON with the request method, headers, cookies and body
        /set-cookie           sets a cookie from the query string: name, value and optionally domain
        /redirect             302 to /
        anything added with LocalServer.add_page
    Every request is recorded in LocalServer.requests.
    """

    server: "_Server"

    def _send(self, status: int, body: str | bytes, content_type: str = "text/html; charset=utf-8", headers: dict[str, str] | None = None) -> None:
        payload = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _handle(self) -> None:
        url = urlsplit(self.path)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        host = self.headers.get("Host", "")
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        self.server.requests.append((self.command, host, url._replace(scheme="", netloc="").geturl()))

        if url.path == "/" and host.startswith("www."):
            self._send(302, "", headers={"Location": f"{self.server.scheme}://{host.removeprefix('www.')}/"})
        elif url.path == "/redirect":
            self._send(302, "", headers={"Location": "/"})
        elif url.path == "/set-cookie":
            cookie = f"{query['name']}={query['value']}; Path=/"
            if "domain" in query:
                cookie += f"; Domain={query['domain']}"
            self._send(200, EXAMPLE_HTML, headers={"Set-Cookie": cookie})
        elif url.path == "/echo":
            echo = {
                "method": self.command,
                "headers": {name.lower(): value for name, value in self.headers.items()},
                "cookies": self.headers.get("Cookie", ""),
                "body": body,
            }
            self._send(200, json.dumps(echo), content_type="application/json")
        elif url.path == "/":
            self._send(200, EXAMPLE_HTML)
        elif url.path in self.server.pages:
            status, page, content_type, headers = self.server.pages[url.path]
            self._send(status, page, content_type, headers)
        else:
            self._send(404, "not found")

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _handle  # noqa: N815

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        """Keep test output quiet."""


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, certfile: Path | None) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.scheme = "https" if certfile else "http"
        self.pages: dict[str, tuple[int, str | bytes, str, dict[str, str]]] = {}
        self.requests: list[tuple[str, str, str]] = []
        if certfile:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(certfile)
            self.socket = context.wrap_socket(self.socket, server_side=True)


class LocalServer:
    """A real HTTP (or, given a certificate, HTTPS) server on 127.0.0.1 running in a background thread."""

    def __init__(self, certfile: Path | None = None) -> None:
        self._server = _Server(certfile)
        self.port: int = self._server.server_address[1]
        self.url = f"{self._server.scheme}://127.0.0.1:{self.port}"
        # Lets requests reach fake public hostnames (http only) through this server
        self.proxy_url = self.url
        self.proxies = {"http": self.proxy_url}
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def add_page(
        self,
        path: str,
        body: str | bytes,
        *,
        content_type: str = "text/html; charset=utf-8",
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> str:
        """Serve body at path (leading slash) and return its full URL."""
        self._server.pages[path] = (status, body, content_type, headers or {})
        return f"{self.url}{path}"

    def requests(self, host: str | None = None) -> list[tuple[str, str, str]]:
        """Return (method, Host header, path and query) of every request so far, optionally only those for host, ignoring favicons."""
        return [r for r in self._server.requests if r[2] != "/favicon.ico" and (host is None or r[1] == host)]

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture(scope="session")
def server() -> Generator[LocalServer, None, None]:
    """One server per pytest process, so one per xdist worker."""
    local_server = LocalServer()
    yield local_server
    local_server.close()


@pytest.fixture(scope="session")
def https_server(tmp_path_factory: pytest.TempPathFactory) -> Generator[LocalServer, None, None]:
    """Start a TLS listener with a throwaway self-signed certificate; browsers need --ignore-certificate-errors to trust it."""
    if shutil.which("openssl") is None:
        pytest.skip("openssl is needed to generate a throwaway TLS certificate")
    pem = tmp_path_factory.mktemp("tls") / "cert.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost", "-keyout", str(pem), "-out", str(pem)],
        check=True,
        capture_output=True,
    )
    local_server = LocalServer(pem)
    yield local_server
    local_server.close()


@pytest.fixture(scope="module")
def example_html() -> str:
    return EXAMPLE_HTML


@pytest.fixture
def example_url(server: LocalServer) -> str:
    return server.add_page("/example", EXAMPLE_HTML)


def chrome_options(server: LocalServer, *, headless: bool, arguments: Sequence[str] = ()) -> webdriver.ChromeOptions:
    """Chrome options that route non-loopback http traffic (e.g. http://example.com) to the local server."""
    options = webdriver.ChromeOptions()
    options.add_argument(f"--proxy-server={server.proxy_url}")
    options.add_argument(DISABLE_HTTPS_UPGRADES)
    for argument in arguments:
        options.add_argument(argument)
    for argument in CHROME_CI_ARGUMENTS:
        options.add_argument(argument)
    if headless:
        options.add_argument("--headless=new")
    return options


# Commands normally get 120 seconds; a browser that hangs mid-test (seen on CI's macOS and Windows runners) should fail sooner.
DRIVER_COMMAND_TIMEOUT = 30


@pytest.fixture(scope="session", autouse=True)
def driver_diagnostics() -> Generator[None, None, None]:
    """
    Make browser hangs fail fast and, when CHROMEDRIVER_LOG_DIR is set, give every ChromeDriver a verbose log.

    Chrome intermittently exits during startup or hangs on CI runners, and only ChromeDriver's log says why. Logs are named
    after the test running when the driver started, so a module-scoped fixture's driver is logged under its first test.
    """
    original_start_session = RemoteWebDriver.start_session
    original_service_init = ChromiumService.__init__
    log_dir = os.environ.get("CHROMEDRIVER_LOG_DIR", "")

    def start_session(self: RemoteWebDriver, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        original_start_session(self, *args, **kwargs)
        # Only after the session exists, so a slow browser launch keeps the full timeout.
        if isinstance(self.command_executor, RemoteConnection):
            self.command_executor.client_config.timeout = DRIVER_COMMAND_TIMEOUT

    def service_init(self: ChromiumService, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        log_name = re.sub(r"[^\w.-]", "_", os.environ.get("PYTEST_CURRENT_TEST", "outside_test"))
        log_path = Path(log_dir) / f"{log_name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        kwargs["service_args"] = [*(kwargs.get("service_args") or ()), "--verbose", "--append-log"]
        kwargs["log_output"] = str(log_path)
        original_service_init(self, *args, **kwargs)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(RemoteWebDriver, "start_session", start_session)
        if log_dir:
            monkeypatch.setattr(ChromiumService, "__init__", service_init)
        yield


def own_chrome_session(*, arguments: Sequence[str] = (), **kwargs: Any) -> requestium.Session:  # noqa: ANN401
    """Create a Session that starts its own Chrome with the CI-safe arguments, followed by any in webdriver_options and then arguments."""
    webdriver_options = dict(kwargs.pop("webdriver_options", None) or {})
    webdriver_options["arguments"] = [*CHROME_CI_ARGUMENTS, *webdriver_options.get("arguments", ()), *arguments]
    return requestium.Session(webdriver_options=webdriver_options, **kwargs)


def _create_chrome_driver(server: LocalServer, *, headless: bool) -> webdriver.Chrome:
    try:
        driver = webdriver.Chrome(options=chrome_options(server, headless=headless))
        WebDriverWait(driver, 5).until(EC.number_of_windows_to_be(1))
        return driver
    except (urllib3.exceptions.ReadTimeoutError, TimeoutError, WebDriverException) as e:
        error_msg = f"Chrome driver initialization failed: {e}"
        raise RuntimeError(error_msg) from e


def _create_firefox_driver(server: LocalServer, *, headless: bool) -> webdriver.Firefox:
    options = webdriver.FirefoxOptions()
    options.set_preference("network.proxy.type", 1)
    options.set_preference("network.proxy.http", "127.0.0.1")
    options.set_preference("network.proxy.http_port", server.port)
    options.set_preference("browser.cache.disk.enable", value=False)
    options.set_preference("browser.cache.memory.enable", value=False)
    options.set_preference("browser.cache.offline.enable", value=False)
    options.set_preference("network.http.use-cache", value=False)
    if headless:
        options.add_argument("--headless")

    try:
        driver = webdriver.Firefox(options=options)
        WebDriverWait(driver, 5).until(EC.number_of_windows_to_be(1))
        return driver
    except (urllib3.exceptions.ReadTimeoutError, TimeoutError, WebDriverException) as e:
        error_msg = f"Firefox driver initialization failed: {e}"
        raise RuntimeError(error_msg) from e


COOKIES = [
    {"name": "session_id", "value": "abc123", "domain": "example.com", "path": "/"},
    {"name": "user_token", "value": "xyz789", "domain": "example.com", "path": "/"},
]


@pytest.fixture(params=COOKIES, ids=[c["name"] for c in COOKIES], scope="module")
def cookie_data(request: FixtureRequest) -> dict[str, str]:
    return request.param


def assert_first_cookie_matches(driver_cookies: list[dict], expected: dict[str, str]) -> None:
    """Verify the only cookie in a list matches expected values."""
    assert len(driver_cookies) == 1

    cookie = driver_cookies[0]
    assert cookie["name"] == expected["name"]
    assert cookie["value"] == expected["value"]
    assert cookie["domain"] in {expected["domain"], f".{expected['domain']}"}
    assert cookie["path"] == expected["path"]


def cookies_sent_by_driver(session: requestium.Session) -> str:
    """Return the Cookie header the server received when the browser fetched http://example.com/echo."""
    session.driver.get("http://example.com")
    echo = session.driver.execute_async_script("fetch('/echo').then((r) => r.json()).then(arguments[0]);")
    return echo["cookies"]


def validate_session(session: requestium.Session) -> None:
    """
    Check basic validity of requestium Session object.

    If browser context is missing, try recovering.
    If attempted recovery raises WebDriverException, skip test.
    """
    try:
        _ = session.driver.current_url
        _ = session.driver.window_handles
    except WebDriverException as e:
        if "Browsing context has been discarded" not in str(e):
            raise

        try:
            session.driver.switch_to.new_window("tab")
        except WebDriverException as e:
            pytest.skip(f"Browser context discarded and cannot be recovered: {e!s}")


@pytest.fixture(
    params=["chrome-headless", "chrome", "firefox-headless", "firefox"],
    scope="module",
)
def session(request: FixtureRequest, server: LocalServer) -> Generator[requestium.Session, None, None]:
    driver_type = request.param
    browser, _, mode = driver_type.partition("-")
    headless = mode == "headless"

    driver: webdriver.Chrome | webdriver.Firefox | None = None

    try:
        if browser == "chrome":
            driver = _create_chrome_driver(server, headless=headless)
        elif browser == "firefox":
            driver = _create_firefox_driver(server, headless=headless)
        else:
            msg = f"Unknown driver type: {browser}"
            raise ValueError(msg)

        assert driver.name in browser
        session = requestium.Session(driver=cast("DriverMixin", driver))
        assert session.driver.name in browser

        validate_session(session)

        yield session

    except RuntimeError as e:
        # Driver creation failed - skip all tests using this session
        pytest.skip(str(e))

    finally:
        if driver:
            with contextlib.suppress(WebDriverException, OSError, Exception):
                driver.quit()


@pytest.fixture
def clean_session(session: requestium.Session, server: LocalServer) -> Generator[requestium.Session, None, None]:
    """Ensure cookies are cleared before each test, and let requests reach fake public hosts through the local server."""
    session.cookies.clear()
    for host in ("example.com", "example.net"):
        session.driver.get(f"http://{host}")  # the driver only deletes cookies of the current page's domain
        session.driver.delete_all_cookies()
    session._last_requests_url = None
    session.proxies.update(server.proxies)
    yield session
    session.proxies.clear()
