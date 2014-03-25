import json

from pylons import g
from r2.lib import amqp
from reddit_liveupdate.models import LiveUpdateStream
from reddit_liveupdate.utils import send_event_broadcast


def _parse_and_broadcast_embeds(event_id, liveupdate_id, maxwidth=485):
    """Parse a liveupdates embeds and notify consumers if we had any."""
    liveupdate = LiveUpdateStream.parse_embeds(event_id,
                                               liveupdate_id,
                                               maxwidth)

    if not liveupdate.media_objects:
        return

    payload = {
        "liveupdate_id": liveupdate_id,
        "media_embeds": liveupdate.embeds
    }
    send_event_broadcast(event_id, type="render_embeds", payload=payload)


def process_liveupdate_q():
    _handlers = {
        "parse_embeds": _parse_and_broadcast_embeds,
    }

    @g.stats.amqp_processor('liveupdate_q')
    def _handle_liveupdate_q(msg):
        data = json.loads(msg.body)
        action = data.pop('action')

        if action in _handlers:
            _handlers[action](**data)
        else:
            g.log.warning("Unknown liveupdate amqp action %s. Data: %s" %
                          (action, data))

    amqp.consume_items('liveupdate_q', _handle_liveupdate_q, verbose=False)
