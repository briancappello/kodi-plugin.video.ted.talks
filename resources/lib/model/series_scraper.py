import html5lib
import json
import hashlib
import os
import re
import tempfile

from .. import settings
from .talk_scraper import Talk


class Series:
    def __init__(self, get_html):
        self.get_html = get_html

    def URL(self, *path):
        return "/".join([settings.__ted_url__, *path])

    def fetch_NEXT_DATA(self, url):
        m = r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>'
        m = re.compile(m, re.S).search(self.get_html(url))
        return json.loads(m.group(1) if bool(m) else "{}")

    def fetch_list(self):
        """retrieve the list of TED series from Prismic CMS markdown slices"""
        data = self.fetch_NEXT_DATA(self.URL("series"))
        slices = (
            data.get("props", {})
            .get("pageProps", {})
            .get("page", {})
            .get("data", {})
            .get("slices", [])
        )
        for s in slices:
            for item in s.get("items", []):
                for col in item.get("column", []):
                    text = col.get("text", "")
                    if "/series/" not in text:
                        continue
                    # Extract series slug from [button url="..." ...] tag
                    btn = re.search(r'\[button\s+url="([^"]*?/series/([^"]+))"', text)
                    if not btn:
                        continue
                    slug = btn.group(2).rstrip("/")
                    url = self.URL("series", slug)
                    # Extract title from ## heading
                    title_m = re.search(r"##\s*(.+)", text)
                    title = title_m.group(1).strip() if title_m else slug
                    # Extract image from ![alt](url) or [![alt](url)]
                    img_m = re.search(r"!\[.*?\]\((.*?)[\s\)]", text)
                    img = img_m.group(1) if img_m else None
                    # Extract description: text between title line and ## separator
                    desc = ""
                    desc_m = re.search(
                        r"##\s*[^\n]+\n(.+?)(?:\n##\s*$|\n\[button)", text, re.S | re.M
                    )
                    if desc_m:
                        desc = desc_m.group(1).strip()
                    yield title, url, img, desc

    def fetch_cached_json(self, url, md5):
        temp_dir = settings.__temp_path__ or tempfile.gettempdir()
        cache_file = os.path.join(temp_dir, "TED__cache_file__")
        content = ""
        try:
            with open(cache_file, "r") as fp:
                content = fp.read()
        except:
            pass
        if hashlib.md5(content.encode()).hexdigest() != md5:
            html = self.get_html(url)
            html = html5lib.parse(html, namespaceHTMLElements=False)
            content = html.find(".//script[@id='__NEXT_DATA__']").text.strip()
            with open(cache_file, "w") as fp:
                fp.write(content)
            md5 = hashlib.md5(content.encode()).hexdigest()
        content = json.loads(content)
        content = content["props"]["pageProps"]
        return content, md5

    def choose_image(self, images):
        for i in images:
            if i.get("url") and i.get("aspectRatioName") == "16x9":
                return i.get("url")
        for i in images:
            if i.get("url") and i.get("aspectRatioName") == "4x3":
                return i.get("url")
        for i in images:
            if i.get("url") and i.get("aspectRatioName") == "1x1":
                return i.get("url")
        return None

    def info_tvshow(self, info):
        return {
            "title": info.get("name"),
            "plot": info.get("description"),
            "url": self.URL("series", info.get("slug")),
            "img": self.choose_image(info.get("primaryImageSet", [])),
            "mediatype": "tvshow",
        }

    def info_season(self, info):
        t = info.get("seasonLabel")
        s = int(info.get("seasonNumber", 0))
        return {
            "title": t if bool(t) else "Season %d" % (s),
            "plot": info.get("description"),
            "url": self.URL("playlists", info.get("id"), info.get("slug")),
            "img": self.choose_image(info.get("primaryImageSet", [])),
            "season": s,
            "mediatype": "season",
        }

    def info_episode(self, info, N=0):
        return {
            "title": info.get("title"),
            "plot": info.get("description"),
            "url": info.get("canonicalUrl"),
            "img": self.choose_image(info.get("primaryImageSet", [])),
            "duration": int(info.get("duration", 0)),
            "episode": int(N),
            "speaker": info.get("presenterDisplayName"),
            "mediatype": "episode",
        }

    def fetch_series(self, url):
        """retrieve series info and list seasons"""
        data, md5 = self.fetch_cached_json(url, "")
        yield self.info_tvshow(data.get("series", {})), md5
        for li in data.get("seasons", {}):
            yield self.info_season(li)

    def fetch_season(self, url, md5, N):
        """retrieve season N info and list episodes"""
        N = max(N - 1, 0)
        data, md5 = self.fetch_cached_json(url, "")
        tvshow = self.info_tvshow(data.get("series", {}))
        season = data.get("seasons", [])
        season = season[N] if N < len(season) else {}
        videos = season.get("videos", {})
        ##?## a,b could indicate if the video list is complete
        a = int(videos.get("totalCount", 0))
        b = int(videos.get("pageInfo", {}).get("endCursor", 0))
        yield tvshow, a, b
        # the old ways of doing things...
        # yield tvshow, self.info_season(season), a, b
        s = int(season.get("seasonNumber", 0))
        talk = Talk(self.get_html)
        for n, li in enumerate(videos.get("nodes", [])):
            yield talk.fetch_talk(li.get("canonicalUrl", ""), s, n + 1)
            # the old ways of doing things...
            # yield self.info_episode(li, n+1)
