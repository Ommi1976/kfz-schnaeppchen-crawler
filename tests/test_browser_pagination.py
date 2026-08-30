from kfz_crawler.browser import _click_matching_page_link, _page_number


class _Locator:
    def __init__(self, available: bool):
        self.available = available
        self.clicked = False

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self.available else 0

    def click(self, timeout):
        self.clicked = True


class _Page:
    def __init__(self):
        self.locators = {}
        self.waited = False

    def locator(self, selector):
        locator = self.locators.setdefault(selector, _Locator("pageNumber=2" in selector))
        return locator

    def wait_for_load_state(self, state, timeout):
        assert state == "domcontentloaded"
        self.waited = True


def test_page_number_reads_mobile_search_parameter():
    assert _page_number("https://suchen.mobile.de/fahrzeuge/search.html?pageNumber=2&p=%3A27500") == "2"
    assert _page_number("https://suchen.mobile.de/fahrzeuge/search.html") is None


def test_pagination_prefers_link_present_on_current_page():
    page = _Page()

    assert _click_matching_page_link(page, "2") is True
    assert page.waited is True
    assert page.locators['a[href*="pageNumber=2"]'].clicked is True
