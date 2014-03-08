import json

from pylons import g
from r2.lib import amqp
from reddit_liveupdate.models import LiveUpdateStream


def process_liveupdate_q():
    _handlers = {
        "parse_embeds": LiveUpdateStream.parse_embeds,
    }

    @g.stats.amqp_processor('liveupdate_q')
    def _handle_liveupdate_q(msg):
        data = json.loads(msg.body)
        action = data.pop('action')

        if action in _handlers:
            _handlers[action](**data)
        else:
            g.log.debug("Unknown action %s. Data: %s" % action, data)

    amqp.consume_items('liveupdate_q', _handle_liveupdate_q, verbose=False)
