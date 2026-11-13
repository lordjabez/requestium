"""
ensure_add_cookie: which page it navigates to, how it retries, and which errors it surfaces.

The first group runs in every browser. The rest needs Chrome specifics (error messages, TLS flags) and uses one headless Chrome.
"""

from collections.abc import Generator
from typing import cast

import pytest
from selenium import webdriver
from selenium.common.exceptions import InvalidArgumentException, WebDriverException

import requestium.requestium
from requestium.requestium_mixin import DriverMixin

from .conftest import LocalServer, assert_first_cookie_matches, chrome_options, cookies_sent_by_driver, own_chrome_session


def test_ensure_add_cookie(clean_session: requestium.Session, cookie_data: dict[str, str]) -> None:
    clean_session.driver.ensure_add_cookie(cookie_data)

    assert_first_cookie_matches(clean_session.driver.get_cookies(), cookie_data)


def test_ensure_add_cookie_is_sent_to_server(clean_session: requestium.Session, cookie_data: dict[str, str]) -> None:
    clean_session.driver.ensure_add_cookie(cookie_data)

    sent = cookies_sent_by_driver(clean_session)
    assert sent == f"{cookie_data['name']}={cookie_data['value']}"


def test_ensure_add_cookie_domain_override(clean_session: requestium.Session, cookie_data: dict[str, str]) -> None:
    override_domain = "example.net"
    original = dict(cookie_data)

    clean_session.driver.ensure_add_cookie(cookie_data, override_domain=override_domain)

    assert cookie_data == original
    assert_first_cookie_matches(clean_session.driver.get_cookies(), {**cookie_data, "domain": override_domain})


def test_ensure_add_cookie_falls_back_to_parent_domain(clean_session: requestium.Session) -> None:
    """www.example.com redirects to example.com, so the cookie for www is rejected until retried with the parent domain."""
    cookie = {"name": "fallback", "value": "1", "domain": "www.example.com", "path": "/"}
    original = dict(cookie)

    clean_session.driver.ensure_add_cookie(cookie)

    assert cookie == original
    assert clean_session.driver.current_url == "http://example.com/"
    assert_first_cookie_matches(clean_session.driver.get_cookies(), {**cookie, "domain": "example.com"})


@pytest.fixture(scope="module")
def chrome(server: LocalServer) -> Generator[DriverMixin, None, None]:
    session = requestium.Session(driver=cast("DriverMixin", webdriver.Chrome(options=chrome_options(server, headless=True))))
    yield session.driver
    session.close()


@pytest.fixture(scope="module")
def secure_chrome(https_server: LocalServer) -> Generator[DriverMixin, None, None]:
    """Start Chrome with *.secure.com mapped to the TLS listener (a forward proxy can't tunnel to it) and its certificate trusted."""
    arguments = [
        f"--host-resolver-rules=MAP *.secure.com 127.0.0.1:{https_server.port}",
        "--ignore-certificate-errors",
    ]
    session = own_chrome_session(headless=True, arguments=arguments)
    yield session.driver
    session.close()


def test_non_domain_errors_propagate_with_message(chrome: DriverMixin) -> None:
    # Chrome rejects a cookie without a value outright: not a domain problem, so the original exception must surface.
    with pytest.raises(InvalidArgumentException) as excinfo:
        chrome.try_add_cookie({"name": "bad", "domain": "www.example.com"})
    assert "missing 'value'" in str(excinfo.value)


def test_unfixable_cookie_raises_after_retry(chrome: DriverMixin) -> None:
    chrome.get("http://example.com/")
    # Browsers silently drop a __Secure- cookie that isn't marked secure, which is the "silent no-op" case.
    with pytest.raises(WebDriverException, match="Couldn't add the following cookie"):
        chrome.ensure_add_cookie({"name": "__Secure-nope", "value": "1", "domain": "www.example.com"})


def test_secure_cookie_navigates_over_https(secure_chrome: DriverMixin, https_server: LocalServer) -> None:
    secure_chrome.ensure_add_cookie({"name": "sec", "value": "1", "domain": "app.secure.com", "secure": True})

    assert secure_chrome.current_url == "https://app.secure.com/"
    assert ("GET", "app.secure.com", "/") in https_server.requests()


def test_insecure_cookie_navigates_over_http(chrome: DriverMixin, server: LocalServer) -> None:
    chrome.ensure_add_cookie({"name": "plain", "value": "1", "domain": "plain.example.com"})

    assert chrome.current_url == "http://plain.example.com/"
    assert ("GET", "plain.example.com", "/") in server.requests()


def test_domain_check_is_not_a_substring_match(chrome: DriverMixin, server: LocalServer) -> None:
    chrome.get("http://example.com/")
    # "ample.com" is a substring of "example.com" but not a parent domain, so we must navigate there.
    chrome.ensure_add_cookie({"name": "sub", "value": "1", "domain": "ample.com"})

    assert ("GET", "ample.com", "/") in server.requests()
    assert chrome.current_url == "http://ample.com/"


def test_parent_domain_cookie_does_not_navigate(chrome: DriverMixin, server: LocalServer) -> None:
    chrome.get("http://sub.example.com/")
    before = len(server.requests())

    chrome.ensure_add_cookie({"name": "parent", "value": "1", "domain": ".example.com"})

    assert len(server.requests()) == before
    assert chrome.current_url == "http://sub.example.com/"
