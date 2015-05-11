from pylons import c

from r2.config import feature
from r2.lib.hooks import HookRegistrar
from r2.models import DefaultSR, NamedGlobals, NotFound

from reddit_liveupdate.models import LiveUpdateEvent
from reddit_liveupdate.pages import LiveUpdateHappeningNowBar

hooks = HookRegistrar()


@hooks.on("hot.get_content")
def add_featured(controller):
    if not feature.is_enabled('live_happening_now'):
        return None

    # Not on front page
    if not isinstance(c.site, DefaultSR):
        return None

    # Not on first page of front page
    if getattr(controller, 'listing_obj') and controller.listing_obj.prev:
        return None

    event_id = NamedGlobals.get('live_happening_now')
    if not event_id:
        return None

    try:
        event = LiveUpdateEvent._byID(event_id)
    except NotFound:
        return None
    else:
        return LiveUpdateHappeningNowBar(event=event)
