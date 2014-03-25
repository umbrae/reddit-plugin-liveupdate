import collections
import datetime
import urllib

import pytz

from pylons import c, g
from pylons.i18n import _, ungettext

from r2.lib import filters
from r2.lib.pages import (
    Reddit,
    UserTableItem,
    MediaEmbedBody,
    ModeratorPermissions,
)
from r2.lib.menus import NavMenu, NavButton
from r2.lib.template_helpers import add_sr
from r2.lib.memoize import memoize
from r2.lib.wrapped import Templated, Wrapped
from r2.models import Account, Subreddit, Link, NotFound, Listing, UserListing
from r2.lib.strings import strings
from r2.lib.utils import tup, fuzz_activity
from r2.lib.jsontemplates import (
    JsonTemplate,
    ObjectTemplate,
    ThingJsonTemplate,
)

from reddit_liveupdate.activity import ACTIVITY_FUZZING_THRESHOLD
from reddit_liveupdate.permissions import ReporterPermissionSet
from reddit_liveupdate.utils import pretty_time, pairwise


class LiveUpdateTitle(Templated):
    pass


class LiveUpdatePage(Reddit):
    extension_handling = False
    extra_page_classes = ["live-update"]
    extra_stylesheets = Reddit.extra_stylesheets + ["liveupdate.less"]

    def __init__(self, content, websocket_url=None):

        extra_js_config = {
            "liveupdate_event": c.liveupdate_event._id,
            "liveupdate_pixel_domain": g.liveupdate_pixel_domain,
            "liveupdate_permissions": c.liveupdate_permissions,
            "media_domain": g.media_domain,
        }

        if websocket_url:
            extra_js_config["liveupdate_websocket"] = websocket_url

        title = c.liveupdate_event.title
        if c.liveupdate_event.state == "live":
            title = _("[live]") + " " + title

        Reddit.__init__(self,
            title=title,
            show_sidebar=False,
            content=content,
            extra_js_config=extra_js_config,
        )

    def build_toolbars(self):
        toolbars = [LiveUpdateTitle()]

        if c.liveupdate_permissions:
            tabs = [
                NavButton(
                    _("updates"),
                    "/",
                ),
            ]

            if c.liveupdate_permissions.allow("settings"):
                tabs.append(NavButton(
                    _("settings"),
                    "/edit",
                ))

            if c.liveupdate_permissions.allow("manage"):
                tabs.append(NavButton(
                    _("reporters"),
                    "/reporters",
                ))

            toolbars.append(NavMenu(
                tabs,
                base_path="/live/" + c.liveupdate_event._id,
                type="tabmenu",
            ))

        return toolbars


class LiveUpdateEmbed(LiveUpdatePage):
    extra_page_classes = LiveUpdatePage.extra_page_classes + ["embed"]


class LiveUpdateEvent(Templated):
    def __init__(self, event, listing, show_sidebar):
        self.event = event
        self.listing = listing
        self.visitor_count = self._get_active_visitors()
        if show_sidebar:
            self.discussions = LiveUpdateOtherDiscussions()
        self.show_sidebar = show_sidebar

        reporter_accounts = Account._byID(event.reporters.keys(),
                                          data=True, return_dict=False)
        self.reporters = sorted((LiveUpdateAccount(e)
                                 for e in reporter_accounts),
                                key=lambda e: e.name)

        Templated.__init__(self)

    def _get_active_visitors(self):
        count = self.event.active_visitors

        if count < ACTIVITY_FUZZING_THRESHOLD and not c.user_is_admin:
            return "~%d" % fuzz_activity(count)
        return count


class LiveUpdateEventConfiguration(Templated):
    def __init__(self):
        self.ungrouped_timezones = []
        self.grouped_timezones = collections.defaultdict(list)

        for tzname in pytz.common_timezones:
            if "/" not in tzname:
                self.ungrouped_timezones.append(tzname)
            else:
                region, zone = tzname.split("/", 1)
                self.grouped_timezones[region].append(zone)

        Templated.__init__(self)


class LiveUpdateReporterPermissions(ModeratorPermissions):
    def __init__(self, account, permissions, embedded=False):
        ModeratorPermissions.__init__(
            self,
            user=account,
            permissions_type=ReporterTableItem.type,
            permissions=permissions,
            editable=True,
            embedded=embedded,
        )


class ReporterTableItem(UserTableItem):
    type = "liveupdate_reporter"

    def __init__(self, reporter, event, editable):
        self.event = event
        self.render_class = ReporterTableItem
        self.permissions = LiveUpdateReporterPermissions(
            reporter.account, reporter.permissions)
        UserTableItem.__init__(self, reporter.account, editable=editable)

    @property
    def cells(self):
        if self.editable:
            return ("user", "sendmessage", "permissions", "permissionsctl")
        else:
            return ("user",)

    @property
    def _id(self):
        return self.user._id

    @classmethod
    def add_props(cls, item, *k):
        return item

    @property
    def container_name(self):
        return self.event._id

    @property
    def remove_action(self):
        return "live/%s/rm_reporter" % self.event._id


class ReporterListing(UserListing):
    type = "liveupdate_reporter"
    permissions_form = LiveUpdateReporterPermissions(
        account=None,
        permissions=ReporterPermissionSet.SUPERUSER,
        embedded=True,
    )

    def __init__(self, event, builder, editable=True):
        self.event = event
        UserListing.__init__(self, builder, addable=editable, nextprev=False)

    @property
    def destination(self):
        return "live/%s/add_reporter" % self.event._id

    @property
    def form_title(self):
        return _("add reporter")

    @property
    def title(self):
        return _("current reporters")

    @property
    def container_name(self):
        return self.event._id


class LiveUpdateEventJsonTemplate(JsonTemplate):
    def render(self, thing=None, *a, **kw):
        return ObjectTemplate(thing.listing.render() if thing else {})


class LiveUpdateJsonTemplate(ThingJsonTemplate):
    _data_attrs_ = ThingJsonTemplate.data_attrs(
        id="_id",
        body="body",
        body_html="body_html",
    )

    def thing_attr(self, thing, attr):
        if attr == "_id":
            return str(thing._id)
        elif attr == "body_html":
            return filters.spaceCompress(filters.safemarkdown(thing.body))
        return ThingJsonTemplate.thing_attr(self, thing, attr)

    def kind(self, wrapped):
        return "LiveUpdate"


class LiveUpdateAccount(Templated):
    def __init__(self, user):
        Templated.__init__(self,
            deleted=user._deleted,
            name=user.name,
            fullname=user._fullname,
        )


class LiveUpdateOtherDiscussions(Templated):
    max_links = 5

    def __init__(self):
        links = self.get_links(c.liveupdate_event._id)
        self.more_links = len(links) > self.max_links
        self.links = links[:self.max_links]
        self.submit_url = "/submit?" + urllib.urlencode({
            "url": add_sr("/live/" + c.liveupdate_event._id,
                          sr_path=False, force_hostname=True),
            "title": c.liveupdate_event.title,
        })

        Templated.__init__(self)

    @classmethod
    @memoize("live_update_discussion_ids", time=60)
    def _get_related_link_ids(cls, event_id):
        url = add_sr("/live/%s" % event_id, sr_path=False, force_hostname=True)

        try:
            links = tup(Link._by_url(url, sr=None))
        except NotFound:
            links = []

        return [link._id for link in links]

    @classmethod
    def get_links(cls, event_id):
        link_ids = cls._get_related_link_ids(event_id)
        links = Link._byID(link_ids, data=True, return_dict=False)
        links.sort(key=lambda L: L.num_comments, reverse=True)

        sr_ids = set(L.sr_id for L in links)
        subreddits = Subreddit._byID(sr_ids, data=True)

        wrapped = []
        for link in links:
            w = Wrapped(link)

            w.subreddit = subreddits[link.sr_id]

            # ideally we'd check if the user can see the subreddit, but by
            # doing this we keep everything user unspecific which makes caching
            # easier.
            if w.subreddit.type == "private":
                continue

            comment_label = ungettext("comment", "comments", link.num_comments)
            w.comments_label = strings.number_label % dict(
                num=link.num_comments, thing=comment_label)

            wrapped.append(w)
        return wrapped


class LiveUpdateSeparator(Templated):
    def __init__(self, date):
        self.date = date.replace(minute=0, second=0, microsecond=0)
        self.date_str = pretty_time(self.date)
        Templated.__init__(self)


class LiveUpdateListing(Listing):
    def __init__(self, builder):
        self.current_time = datetime.datetime.now(g.tz)
        self.current_time_str = pretty_time(self.current_time)

        Listing.__init__(self, builder)

    def things_with_separators(self):
        items = [self.things[0]]

        for prev, update in pairwise(self.things):
            if update._date.hour != prev._date.hour:
                items.append(LiveUpdateSeparator(prev._date))
            items.append(update)

        return items


class LiveUpdateMediaEmbedBody(MediaEmbedBody):
    pass


def liveupdate_add_props(user, wrapped):
    account_ids = set(w.author_id for w in wrapped)
    accounts = Account._byID(account_ids, data=True)

    for item in wrapped:
        item.author = LiveUpdateAccount(accounts[item.author_id])

        item.date_str = pretty_time(item._date)
