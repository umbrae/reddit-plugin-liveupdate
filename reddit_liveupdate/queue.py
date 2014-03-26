import json

from pylons import g
from r2.lib import amqp
from reddit_liveupdate.models import LiveUpdateStream
from reddit_liveupdate.utils import send_event_broadcast


def process_liveupdate_scraper_q():
    @g.stats.amqp_processor('liveupdate_scraper_q')
    def _handle_q(msg):
        d = json.loads(msg.body)
        liveupdate = LiveUpdateStream.parse_embeds(d['event_id'],
                                                   d['liveupdate_id'])

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
