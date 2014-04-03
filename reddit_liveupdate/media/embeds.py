import json
import uuid

from pylons import g

from r2.lib import amqp
from r2.lib.db import tdb_cassandra
from r2.lib.media import get_media_embed
from r2.lib.utils import sanitize_url

from reddit_liveupdate.models import LiveUpdateStream, LiveUpdateEvent
from reddit_liveupdate.utils import send_event_broadcast
from reddit_liveupdate.media.scraper import (
    scrape_media_objects,
    TwitterScraper,
)


def get_live_media_embed(media_object):
    if media_object['type'] == "twitter.com":
        return TwitterScraper.media_embed(media_object)
    return get_media_embed(media_object)


def queue_parse_embeds(event, liveupdate):
    msg = json.dumps({
        'liveupdate_id': unicode(liveupdate._id),  # serializing UUID
        'event_id': event._id,  # Already a string
    })
    amqp.add_item('liveupdate_scraper_q', msg)


def parse_embeds(event_id, liveupdate_id, maxwidth=485):
    """Find, scrape, and store any embeddable URLs in this liveupdate.

    Return the newly altered liveupdate for convenience.
    Note: This should be used in async contexts only.

    """
    if isinstance(liveupdate_id, basestring):
        liveupdate_id = uuid.UUID(liveupdate_id)

    try:
        event = LiveUpdateEvent._byID(event_id)
        liveupdate = LiveUpdateStream.get_update(event, liveupdate_id)
    except tdb_cassandra.NotFound:
        g.log.warning("Couldn't find event or liveupdate for embedding: "
                      "%s / %s" % (event_id, liveupdate_id))
        return

    urls = _extract_isolated_urls(liveupdate.body)
    liveupdate.media_objects = scrape_media_objects(urls)
    LiveUpdateStream.add_update(event, liveupdate)

    return liveupdate


def _extract_isolated_urls(md):
    """Extract URLs that exist on their own lines in given markdown.

    This style borrowed from wordpress, which is nice because it's tolerant to
    failures and is easy to understand. See https://codex.wordpress.org/Embeds

    """
    urls = []
    for line in md.splitlines():
        url = sanitize_url(line, require_scheme=True)
        if url and url != "self":
            urls.append(url)
    return urls


def process_liveupdate_scraper_q():
    @g.stats.amqp_processor('liveupdate_scraper_q')
    def _handle_q(msg):
        d = json.loads(msg.body)
        liveupdate = parse_embeds(d['event_id'], d['liveupdate_id'])

        if not liveupdate.media_objects:
            return

        payload = {
            "liveupdate_id": d['liveupdate_id'],
            "media_embeds": liveupdate.embeds
        }
        send_event_broadcast(d['event_id'],
                             type="embeds_ready",
                             payload=payload)

    amqp.consume_items('liveupdate_scraper_q', _handle_q, verbose=False)
