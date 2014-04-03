import json
import re

from functools import partial
from itertools import islice, imap, ifilter
from urllib2 import (
    HTTPError,
    URLError,
)

import requests

from r2.lib.media import Scraper, MediaEmbed, upload_media
from r2.models.media_cache import MediaByURL, Media


def _scrape_media_object(url, autoplay=False, maxwidth=485):
    """Generate a single media object by URL. Caches with MediaByURL.

    NOTE: This shares a lot of code with internal method _scrape_media in
    r2.lib.media. That's an unfortunate circumstance of the kludginess of
    the current MediaByURL implementation. This will likely be cleaned up
    significantly if we end up rewriting MediaByURL as planned. (2014-04)
    """
    cache_params = {"autoplay": bool(autoplay), "maxwidth": int(maxwidth)}

    cached_media = MediaByURL.get(url, **cache_params)
    if cached_media:
        media_object = cached_media.media.media_object
    else:
        # This may look like cache_params, but it's not: it's specific to the
        # LiveScraper implementation and would break if cache_params changes.
        scraper = LiveScraper.for_url(url,
                                      autoplay=autoplay,
                                      maxwidth=maxwidth)

        try:
            thumbnail, media_object, secure_media_object = scraper.scrape()
        except (HTTPError, URLError) as e:
            MediaByURL.add_error(url, str(e), **cache_params)

        # Thumbnail handling. I hate that we're doing this here, because
        # liveupdate doesn't care at all about thumbnails, but we're using
        # a generalized system so we need to behave by its rules. This
        # is about a clear a case as any that this needs to be abstracted
        # out into its own system and that MediaByURL isn't cutting it.
        thumbnail_size, thumbnail_url = None, None
        if thumbnail:
            thumbnail_size = thumbnail.size
            thumbnail_url = upload_media(thumbnail)

        # Store to cache - also stores None's in cases of no media object.
        media = Media(media_object=media_object,
                      secure_media_object=secure_media_object,
                      thumbnail_url=thumbnail_url,
                      thumbnail_size=thumbnail_size)
        MediaByURL.add(url, media, **cache_params)

    # No oembed? We don't want it for liveupdate.
    if not media_object or 'oembed' not in media_object:
        return None

    # Use our exact passed URL to ensure matching in markdown.
    # Some scrapers will canonicalize a URL to something we
    # haven't seen yet.
    media_object['oembed']['url'] = url

    return media_object


def scrape_media_objects(urls, autoplay=False, maxwidth=485, max_embeds=15):
    """Given a list of URLs, potentially scrape and return media objects."""
    gen_fn = partial(_scrape_media_object,
                     autoplay=autoplay,
                     maxwidth=maxwidth)
    media_objects = list(islice(ifilter(None, imap(gen_fn, urls)), max_embeds))

    return media_objects


class LiveScraper(Scraper):
    """The interface to Scraper to be used within liveupdate for media embeds.

    Has support for scrapers that we don't necessarily want to be visible in
    reddit core (like twitter, for example). Outside of the hook system
    so that this functionality is not live for all uses of Scraper proper.

    """

    @classmethod
    def for_url(cls, url, autoplay=False, maxwidth=485):
        if (TwitterScraper.matches(url)):
            return TwitterScraper(url)

        return super(LiveScraper, cls).for_url(url,
                                               autoplay=autoplay,
                                               maxwidth=maxwidth)


class TwitterScraper(Scraper):
    OEMBED_ENDPOINT = "https://api.twitter.com/1/statuses/oembed.json"
    URL_MATCH = re.compile(r"""https?:
                               //(www\.)?twitter\.com
                               /\w{1,20}
                               /status(es)?
                               /\d+
                            """, re.X)

    def __init__(self, url, maxwidth=485, omit_script=False):
        self.url = url
        self.maxwidth = maxwidth
        self.omit_script = False

    @classmethod
    def matches(cls, url):
        return cls.URL_MATCH.match(url)

    def _fetch_from_twitter(self):
        params = {
            "url": self.url,
            "format": "json",
            "maxwidth": self.maxwidth,
            "omit_script": self.omit_script,
        }

        content = requests.get(self.OEMBED_ENDPOINT, params=params).content
        return json.loads(content)

    def _make_media_object(self, oembed):
        if oembed.get("type") in ("video", "rich"):
            return {
                "type": "twitter.com",
                "oembed": oembed,
            }
        return None

    def scrape(self):
        oembed = self._fetch_from_twitter()
        if not oembed:
            return None, None, None

        media_object = self._make_media_object(oembed)

        return (
            None,  # no thumbnails for twitter
            media_object,
            media_object,  # Twitter's response is ssl ready by default
        )

    @classmethod
    def media_embed(cls, media_object):
        oembed = media_object["oembed"]

        html = oembed.get("html")
        width = oembed.get("width")

        # Right now Twitter returns no height, so we get ''.
        # We'll reset the height with JS dynamically, but if they support
        # height in the future, this should work transparently.
        height = oembed.get("height") or 0

        if not html and width:
            return

        return MediaEmbed(
            width=width,
            height=height,
            content=html,
        )
