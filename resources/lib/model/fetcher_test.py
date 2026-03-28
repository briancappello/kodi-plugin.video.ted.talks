import unittest
from unittest.mock import MagicMock

from . import fetcher


class TestFetch(unittest.TestCase):
    def test_get_html_returns_text(self):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.text = "<html>hello</html>"

        original_get = fetcher.requests.get
        fetcher.requests.get = MagicMock(return_value=mock_resp)
        try:
            sut = fetcher.Fetcher(lambda x, **kw: None)
            result = sut.get_HTML("https://example.com/")
            self.assertEqual(result, "<html>hello</html>")
            fetcher.requests.get.assert_called_once()
        finally:
            fetcher.requests.get = original_get

    def test_get_retries_on_429(self):
        mock_resp_429 = MagicMock()
        mock_resp_429.ok = False
        mock_resp_429.status_code = 429
        mock_resp_429.headers = {"Retry-After": "1"}

        mock_resp_ok = MagicMock()
        mock_resp_ok.ok = True
        mock_resp_ok.text = "ok"

        original_get = fetcher.requests.get
        fetcher.requests.get = MagicMock(side_effect=[mock_resp_429, mock_resp_ok])
        try:
            sut = fetcher.Fetcher(lambda x, **kw: None)
            result = sut.get("https://example.com/")
            self.assertEqual(result.text, "ok")
            self.assertEqual(fetcher.requests.get.call_count, 2)
        finally:
            fetcher.requests.get = original_get


if __name__ == "__main__":
    unittest.main()
