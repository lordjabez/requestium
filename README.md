![Requestium](https://user-images.githubusercontent.com/14966348/32966130-8bb15b00-cbb7-11e7-9faf-85963ec5bd82.png)
========

[![License](https://img.shields.io/badge/License-BSD%203--Clause-blue.svg)](https://opensource.org/licenses/BSD-3-Clause)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/requestium)](https://pypi.org/project/requestium/)
[![PyPI](https://img.shields.io/pypi/v/requestium?color=blue)](https://pypi.org/project/requestium/)
[![Coverage](https://raw.githubusercontent.com/lordjabez/requestium/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/lordjabez/requestium/blob/python-coverage-comment-action-data/htmlcov/index.html)
[![Python checks](https://github.com/lordjabez/requestium/actions/workflows/python.yml/badge.svg)](https://github.com/lordjabez/requestium/actions/workflows/python.yml)

Requestium is a Python library that merges the power of [Requests](https://github.com/requests/requests), [Selenium](https://github.com/SeleniumHQ/selenium), and [Parsel](https://github.com/scrapy/parsel) into a single integrated tool for automating web actions.

The library was created for writing web automation scripts that are written using mostly Requests but that are able to seamlessly switch to Selenium for the JavaScript heavy parts of the website, while maintaining the session.

Requestium adds independent improvements to both Requests and Selenium, and every new feature is lazily evaluated, so its useful even if writing scripts that use only Requests or Selenium.

## Features

- Enables switching between a Requests' Session and a Selenium webdriver while maintaining the current web session.
- Integrates Parsel's parser into the library, making xpath, css, and regex much cleaner to write.
- Improves Selenium's handling of dynamically loading elements.
- Makes cookie handling more flexible in Selenium.
- Makes clicking elements in Selenium more reliable.
- Supports Chromedriver natively plus adding a custom webdriver.

## Installation

```bash
pip install requestium
```

Selenium 4.6+ includes Selenium Manager, which downloads the matching driver (such as Chromedriver) automatically, so you don't need to install one yourself. If you prefer to manage the driver yourself, download it and pass its location with the `webdriver_path` argument.

## Usage
First create a session as you would do on Requests, and optionally add arguments for the web-driver if you plan to use one.

```python
from requestium import Session, Keys

options = {"arguments": ["headless"]}
s = Session(webdriver_path="./chromedriver", default_timeout=15, webdriver_options=options)
```

Since headless mode is common, there's a shortcut for it by specifying `headless=True`.

```python
from requestium import Session, Keys

s = Session(webdriver_path="./chromedriver", headless=True)
```

You can also create a Selenium webdriver outside Requestium and have it use that instead:

```python
from selenium import webdriver
from requestium import Session, Keys

firefox_driver = webdriver.Firefox()

s = Session(driver=firefox_driver)
```

A remote webdriver works too, with all the `ensure_*` and xpath/css/re helpers:

```python
from selenium import webdriver
from requestium import Session

options = webdriver.ChromeOptions()
remote_driver = webdriver.Remote(command_executor="http://localhost:4444/wd/hub", options=options)

s = Session(driver=remote_driver)
```

You can also pass an instance of a 3rd party webdriver class, such as Chrome from [Selenium-Wire](https://github.com/wkeeling/selenium-wire), which lets you get the XHR requests of a web page. When `driver` is given, `webdriver_path` is ignored:

```python
from seleniumwire import webdriver
from requestium import Session, Keys

seleniumwire_driver = webdriver.Chrome()

s = Session(driver=seleniumwire_driver)
```

You don't need to parse the response, it is done automatically when calling xpath, css or re.

```python
title = s.get("http://samplesite.com").xpath("//title/text()").extract_first(default="Default Title")
```

Regex require less boilerplate when compared to Python's standard `re` module.

```python
response = s.get("http://samplesite.com/sample_path")

# Extracts the first match
identifier = response.re_first(r"ID_\d\w\d", default="ID_1A1")

# Extracts all matches as a list
users = response.re(r"user_\d\d\d")
```

The Session object is just a regular Requests's session object, so you can use all of its methods. Responses from all HTTP verbs (get, post, put, patch, delete, head, options) have the xpath, css, and re helpers.

```python
s.post("http://www.samplesite.com/sample", data={"field1": "data1"})
s.proxies.update({"http": "http://10.11.4.254:3128", "https": "https://10.11.4.252:3128"})
```

And you can switch to using the Selenium webdriver to run any js code.

```python
s.transfer_session_cookies_to_driver()  # You can maintain the session if needed
s.driver.get("http://www.samplesite.com/sample/process")
```

The driver object is a Selenium webdriver object, so you can use any of the normal selenium methods plus new methods added by Requestium.

```python
s.driver.find_element("xpath", "//input[@class='user_name']").send_keys("James Bond", Keys.ENTER)

# New methods which wait for element to load instead of failing, useful for single page web apps
s.driver.ensure_element("xpath", "//div[@attribute='button']").click()
s.driver.ensure_element_by_xpath("//div[@attribute='button']").click()
```

Requestium also adds xpath, css, and re methods to the Selenium driver object.

```python
if s.driver.re(r"ID_\d\w\d some_pattern"):
    print("Found it!")
```

And finally you can switch back to using Requests.

```python
s.transfer_driver_cookies_to_session()
s.post("http://www.samplesite.com/sample2", data={"key1": "value1"})
```

When you are done, close the session. This quits the webdriver if one was started, including a driver you passed in. It also works as a context manager.

```python
s.close()

with Session(headless=True) as s:
    s.driver.get("http://www.samplesite.com")
```

## Selenium workarounds
Requestium adds several 'ensure' methods to the driver object, as Selenium is known to be very finicky about selecting elements and cookie handling.

### Wait for element
The `ensure_element` and `ensure_element_by_` methods wait for the element to be loaded in the browser and returns it as soon as it loads. They're named after Selenium's `find_element` and `find_element_by_` methods (which immediately raise an exception if they can't find the element).

Requestium can wait for an element to be in any of the following states:

- present (default)
- clickable
- visible
- invisible (useful for things like waiting for *loading...* gifs to disappear)

These methods are very useful for single page web apps where the site is dynamically changing its elements. We usually end up completely replacing our `find_element` and `find_element_by_` calls with `ensure_element` and `ensure_element_by_` calls as they are more flexible.

Elements you get using these methods have the new `ensure_click` method which makes the click less prone to failure. This helps with getting through a lot of the problems with Selenium clicking.

```python
s.driver.ensure_element("xpath", "//li[@class='b1']", state="clickable", timeout=5).ensure_click()

# === We also added these methods named in accordance to Selenium's api design ===
# ensure_element_by_id
# ensure_element_by_name
# ensure_element_by_xpath
# ensure_element_by_link_text
# ensure_element_by_partial_link_text
# ensure_element_by_tag_name
# ensure_element_by_class_name
# ensure_element_by_css_selector
```

### Add cookie
The `ensure_add_cookie` method makes adding cookies much more robust. Selenium needs the browser to be at the cookie's domain before being able to add the cookie, this method offers several workarounds for this. If the browser is not in the cookies domain, it GETs the domain before adding the cookie, over https if the cookie is `secure` and over http otherwise. It also allows you to override the domain before adding it, and avoid making this GET. The domain can be overridden to `''`, this sets the cookie's domain to whatever domain the driver is currently in.

If it can't add the cookie it tries to add it with a less restrictive domain (Eg.: home.site.com -> site.com) before failing.

```python
cookie = {"domain": "www.site.com", "secure": False, "value": "sd2451dgd13", "expiry": 1516824855.759154, "path": "/", "httpOnly": True, "name": "sessionid"}
s.driver.ensure_add_cookie(cookie, override_domain="")
```

## Considerations
New features are lazily evaluated, meaning:

- The Selenium webdriver process is only started if you call the driver object. So if you don't need to use the webdriver, you could use the library with no overhead. Very useful if you just want to use the library for its integration with Parsel.
- Parsing of the responses is only done if you call the `xpath`, `css`, or `re` methods of the response. So again there is no overhead if you don't need to use this feature.

A byproduct of this is that the Selenium webdriver could be used just as a tool to ease in the development of regular Requests code: You can start writing your script using just the Requests' session, and at the last step of the script (the one you are currently working on) transfer the session to the Chrome webdriver. This way, a Chrome process starts in your machine, and acts as a real time "visor" for the last step of your code. You can see in what state your session is currently in, inspect it with Chrome's excellent inspect tools, and decide what's the next step your session object should take. Very useful to try code in an IPython interpreter and see how the site reacts in real time.

When `transfer_driver_cookies_to_session` is called, Requestium automatically updates your Requests session user-agent to match that of the browser used in Selenium. This doesn't happen when running Requests without having switched from a Selenium session first though. So if you just want to run Requests but want it to use your browser's user agent instead of the default one (which sites love to block), just run:

```python
s.copy_user_agent_from_driver()
```
Take into account that doing this will launch a browser process.

When Requestium launches its own Chrome, it carries over the session's `proxies` (as `--proxy-server`) and a custom `User-Agent` header, as long as they are set before the driver is first used. Proxies with credentials aren't supported by Chrome flags and are skipped with a warning. Other headers are not synced. This doesn't apply to a driver you pass in yourself.

## Comparison with Requests + Selenium + lxml
A silly working example of a script that runs on Reddit. We'll then show how it compares to using Requests + Selenium + lxml instead of Requestium.

### Using Requestium

```python
from requestium import Session, Keys

# If you want requestium to type your username in the browser for you, write it in here:
reddit_user_name = ""

s = Session(default_timeout=15)
s.driver.get("http://reddit.com")
s.driver.find_element("xpath", "//a[@href='https://www.reddit.com/login']").click()

print("Waiting for elements to load...")
s.driver.ensure_element("class name", "desktop-onboarding-sign-up__form-toggler", state="visible").click()

if reddit_user_name:
    s.driver.ensure_element("id", "user_login").send_keys(reddit_user_name)
    s.driver.ensure_element("id", "passwd_login").send_keys(Keys.BACKSPACE)
print("Please log-in in the chrome browser")

s.driver.ensure_element("class name", "desktop-onboarding__title", timeout=60, state="invisible")
print("Thanks!")

if not reddit_user_name:
    reddit_user_name = s.driver.xpath("//span[@class='user']//text()").extract_first()

if reddit_user_name:
    s.transfer_driver_cookies_to_session()
    response = s.get("https://www.reddit.com/user/{}/".format(reddit_user_name))
    cmnt_karma = response.xpath("//span[@class='karma comment-karma']//text()").extract_first()
    reddit_golds_given = response.re_first(r"(\d+) gildings given out")
    print("Comment karma: {}".format(cmnt_karma))
    print("Reddit golds given: {}".format(reddit_golds_given))
else:
    print("Couldn't get user name")
```

### Using Requests + Selenium + lxml

```python
import re
from lxml import etree
from requests import Session
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# If you want requestium to type your username in the browser for you, write it in here:
reddit_user_name = ""

driver = webdriver.Chrome()
driver.get("http://reddit.com")
driver.find_element("xpath", "//a[@href='https://www.reddit.com/login']").click()

print("Waiting for elements to load...")
WebDriverWait(driver, 15).until(EC.visibility_of_element_located((By.CLASS_NAME, "desktop-onboarding-sign-up__form-toggler"))).click()

if reddit_user_name:
    WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.ID, "user_login"))).send_keys(reddit_user_name)
    driver.find_element("id", "passwd_login").send_keys(Keys.BACKSPACE)
print("Please log-in in the chrome browser")

try:
    WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.CLASS_NAME, "desktop-onboarding__title")))
except TimeoutException:
    pass
WebDriverWait(driver, 60).until(EC.invisibility_of_element_located((By.CLASS_NAME, "desktop-onboarding__title")))
print("Thanks!")

if not reddit_user_name:
    tree = etree.HTML(driver.page_source)
    try:
        reddit_user_name = tree.xpath("//span[@class='user']//text()")[0]
    except IndexError:
        reddit_user_name = None

if reddit_user_name:
    s = Session()
    # Reddit will think we are a bot if we have the wrong user agent
    selenium_user_agent = driver.execute_script("return navigator.userAgent;")
    s.headers.update({"user-agent": selenium_user_agent})
    for cookie in driver.get_cookies():
        s.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"])
    response = s.get("https://www.reddit.com/user/{}/".format(reddit_user_name))
    try:
        cmnt_karma = etree.HTML(response.content).xpath("//span[@class='karma comment-karma']//text()")[0]
    except IndexError:
        cmnt_karma = None
    match = re.search(r"(\d+) gildings given out", str(response.content))
    if match:
        reddit_golds_given = match.group(1)
    else:
        reddit_golds_given = None
    print("Comment karma: {}".format(cmnt_karma))
    print("Reddit golds given: {}".format(reddit_golds_given))
else:
    print("Couldn't get user name")
```

## Similar Projects
This project intends to be a drop-in replacement of Requests' Session object, with added functionality. If your use case is a drop in replacement for a Selenium webdriver, but that also has some of Requests' functionality, [Selenium-Requests](https://github.com/cryzed/Selenium-Requests) does just that.


## License
Copyright © 2017, [Tryolabs](https://tryolabs.com/). Copyright © 2022-2026, Judson Neer. Released under the [BSD 3-Clause](https://github.com/lordjabez/requestium/blob/main/LICENSE).

Requestium was originally developed as an internal tool at Tryolabs and later released as open source. In 2026, Tryolabs transferred the project to Jud Neer, who maintains it independently. Tryolabs is not responsible for the project or for any release made after the transfer.
