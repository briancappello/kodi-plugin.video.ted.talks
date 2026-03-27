import os
import requests
import time

# Use system CA bundle if available, otherwise fall back to certifi
_CA_BUNDLE = None
for _path in ["/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"]:
    if os.path.exists(_path):
        _CA_BUNDLE = _path
        break


class Fetcher:
    def __init__(self, logger):
        self.logger = logger

    def get(self, url):
        # self.logger('GET {}'.format(url))
        r = requests.get(url, timeout=(6, 12), verify=_CA_BUNDLE or True)
        if not r.ok:
            wait = int(r.headers.get("Retry-After", 0))
            if r.status_code == 429 and wait > 0 and wait < 10:
                # too many requests, respect RetryAfter and try again
                time.sleep(wait + 1)
                r = requests.get(url, timeout=(6, 12), verify=_CA_BUNDLE or True)
                if not r.ok:
                    hdrs = "\n".join([k + "=" + v for k, v in r.headers.items()])
                    self.logger(
                        "{0}\n{1}\n{2}".format(r.status_code, hdrs, r.text),
                        level="warn",
                    )
                    raise Exception("Failed to GET {}: {}".format(url, r.status_code))
        return r

    def get_HTML(self, url):
        return self.get(url).text
