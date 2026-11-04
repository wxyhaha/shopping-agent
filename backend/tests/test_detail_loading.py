"""A missing detail container at first observation is not proof it will never load."""

import time

import pytest
from playwright.sync_api import sync_playwright

from app.background_collector import wait_for_detail_images


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        yield page
        browser.close()


def test_wait_observes_container_injected_after_the_first_probe(page):
    page.set_content("""<h1>合成商品</h1><script>setTimeout(() => {
      const detail = document.createElement('div'); detail.className='descV8-test';
      detail.innerHTML='<img src="data:image/gif;base64,' +
        'R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==">';
      document.body.append(detail);
    }, 200);</script>""")
    assert wait_for_detail_images(page, time.monotonic() + 15, lambda: None, 1) == "ready"


def test_expiry_is_unknown_instead_of_proof_the_section_is_absent(page):
    page.set_content("<h1>合成商品</h1>")
    assert wait_for_detail_images(page, time.monotonic() + 15, lambda: None, 0.1) == "timeout"


def test_cancellation_interrupts_the_wait(page):
    def cancelled():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        wait_for_detail_images(page, time.monotonic() + 15, cancelled, 2)
