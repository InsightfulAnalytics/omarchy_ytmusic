"""Profiles (S9): user channels, your own channel, the library's profiles.

- ``user({channelId})``: a user channel's page (a profile: its videos,
  playlists and whatever else it shows). One raw browse, the request
  get_user sends; get_user keeps only the shelves titled (in English)
  Videos and Playlists, so the rows are read by renderer and keyed by what
  they hold, as S8's channel page (ytm_podcasts.channel_sections). A row's
  See all is the channel with params, which the shell opens through
  browsePage. Header ``{channelId, subscribeId, subscribed, subscribers,
  own}``; ``own`` (your own channel) has no Subscribe and ``subscribed``
  None. The page is round, its cover the avatar (never the banner). Your
  own channel's page also leads with your mix (the card at its top) as a
  playlist.
- ``myChannel({})`` (signed in): your own channel's page, the same shape.
  Its id is the account menu's link to it (the compactLinkRenderer whose
  browse endpoint is a channel, matched by endpoint, never by its label),
  which verify_session reads anyway (Worker.own_channel): no extra request
  (one when the session was checked another way, as a switch of identity
  does).
  NOT_FOUND with ``noChannel: true`` when the menu links no channel.
- ``libraryProfiles({order?, cursor?})`` (signed in): the profiles in your
  library (FEmusic_library_user_profile_channels_list), a music shelf read a
  page at a time like the library's lists; the note YouTube serves under it
  ("Only profiles with music activity are shown.", alone when there are
  none) is the page's ``message``. G4: the page's served sort orders
  (Recently saved, A to Z, Z to A) as ``orders`` / ``order``, read and
  applied as ytm_library's lists do (an order's params replace the plain
  list's; YouTube saves the order on the account).

Your own channel's pages are titled with your account's name, which never
reaches the disk cache or a log: ``myChannel`` is not cacheable, and
``user`` keeps such a page off the disk (ytm_pages.keep_off_disk).

Area module (see AREA_MODULES in ytm.py). The recorder is anonymous: a
public profile's raw page, trimmed as S8's pages are.
"""

from ytmusicapi.mixins._utils import prepare_order_params
from ytmusicapi.navigation import MRLIR, THUMBNAILS, nav

import ytm_library as library
import ytm_normalize as norm
import ytm_pages as pages
import ytm_podcasts as podcasts
import ytm_raw as raw
from ytm_proto import WorkerError, opt_cursor, req_str

PROFILES_BROWSE_ID = "FEmusic_library_user_profile_channels_list"
# The params the web sends with it (the Profiles chip of the library).
PROFILES_PARAMS = "ggMCCAc%3D"
PAGE_ITEMS = 50
MAX_HTTP = 3
NO_CHANNEL = "This account has no channel on YouTube Music (its account menu links none)"
_ACCOUNT_MENU = "account/account_menu"
_CARD = "musicCardShelfRenderer"
_PARSE_ERRORS = (KeyError, TypeError, ValueError, IndexError, AttributeError)


class NoChannel(WorkerError):
    """NOT_FOUND with ``noChannel: true``: the account menu links no channel
    (the shell then hides Your channel)."""

    def __init__(self):
        super().__init__("NOT_FOUND", NO_CHANNEL)

    def as_dict(self):
        return dict(super().as_dict(), noChannel=True)


# ---- your channel: the account menu's link ---------------------------------------------------


def _links(node, depth=12):
    """Every compactLinkRenderer under ``node``, in order."""
    if depth < 0:
        return
    if isinstance(node, dict):
        link = node.get("compactLinkRenderer")
        if isinstance(link, dict):
            yield link
        for value in node.values():
            yield from _links(value, depth - 1)
    elif isinstance(node, list):
        for value in node:
            yield from _links(value, depth - 1)


def own_channel_id(response):
    """The channel id the account menu (``account/account_menu``) links:
    its compactLinkRenderer whose browse endpoint is a channel (UC...), a
    user channel page first. None when there is none."""
    found = []
    for link in _links(response):
        browse = raw.browse_endpoint(link.get("navigationEndpoint"))
        if browse and browse["browseId"].startswith("UC"):
            found.append(browse)
    for browse in found:
        if browse["pageType"] == raw.USER_CHANNEL_PAGE:
            return browse["browseId"]
    return found[0]["browseId"] if found else None


def remember_own_channel(worker, capture):
    """Worker.verify_session's hook: the channel id of the account menu it
    captured (None when it links none), now known for this client."""
    worker.own_channel = own_channel_id(capture.first(_ACCOUNT_MENU)) if capture is not None else None
    worker.own_channel_known = True


def known_own_channel(worker):
    """Your channel's id (None: the account menu links none). The session
    check reads it for free; a session checked another way (a switch of
    identity checks the new one itself) has its menu read here, once."""
    worker.require_auth()
    if not getattr(worker, "own_channel_known", False):
        # Kept only for the client it was read with (as Worker.verify_session).
        yt, generation = worker.yt, getattr(worker, "generation", 0)
        with raw.capture((_ACCOUNT_MENU,)) as menu:
            yt.get_account_info()
        if generation != getattr(worker, "generation", 0):
            return known_own_channel(worker)  # the new client's menu names its channel
        remember_own_channel(worker, menu)
    return worker.own_channel


# ---- a profile's page ------------------------------------------------------------------------


def _header_data(response):
    header = response.get("header") if isinstance(response, dict) else None
    data = next(iter(header.values()), {}) if isinstance(header, dict) and header else {}
    return data if isinstance(data, dict) else {}


def card_item(data):
    """The card at the top of your channel (your mix) as a playlist, or None."""
    title = raw.text(data.get("title"))
    endpoint = data.get("onTap") if isinstance(data.get("onTap"), dict) else None
    browse = raw.browse_endpoint(endpoint) or raw.browse_endpoint(
        nav(data, ["title", "runs", 0, "navigationEndpoint"], True))
    if not title or not browse or browse["pageType"] != raw.PLAYLIST_PAGE:
        return None
    return norm.playlist({"browseId": browse["browseId"], "title": title, "description": raw.text(data.get("subtitle")),
                          "thumbnails": nav(data, THUMBNAILS, True)})


def _cards(rows):
    """Sections for the card rows (your mix), each one untitled item."""
    out = []
    for row in rows or []:
        key, data = raw.unwrap(row)
        if key != _CARD or not isinstance(data, dict):
            continue
        try:
            item = card_item(data)
        except _PARSE_ERRORS as exc:
            norm.log(f"skipped profile card: {type(exc).__name__}")
            continue
        if item:
            out.append(norm.section(None, [item], layout="card", key="mix"))
    return out


def _avatar(data):
    """The profile's picture: the header's foregroundThumbnail (the square
    avatar the web shows round, as the rows do). Its ``thumbnail`` is the
    wide banner (S8's podcasts._thumbnails reads that first), the fallback."""
    return nav(data, ["foregroundThumbnail", "musicThumbnailRenderer", "thumbnail", "thumbnails"], True) \
        or podcasts._thumbnails(data)


def build_user(channel_id, response, own=False):
    """A user channel's page from its raw browse. ``own``: it is yours (the
    caller knows); a disabled subscribe button says so too."""
    data = _header_data(response)
    button = pages.subscribe_button(response)
    own = own or button.get("enabled") is False
    subscribers = raw.text(button.get("longSubscriberCountText")) or raw.text(button.get("subscriberCountText"))
    subscribed = button.get("subscribed") if isinstance(button.get("subscribed"), bool) and not own else None
    title = raw.text(data.get("title")) or ""
    header = {"channelId": channel_id, "subscribeId": button.get("channelId") or channel_id,
              "subscribed": subscribed, "subscribers": subscribers, "own": own}
    rows = raw.rows(response)
    sections = _cards(rows) + podcasts.channel_sections(channel_id, rows, title or None)
    return norm.page("user", channel_id, title, subscribers, _avatar(data), sections, header=header, round=True)


def m_user(worker, params):
    channel_id = req_str(params, "channelId")
    response = worker.yt._send_request("browse", {"browseId": channel_id})
    pages.keep_off_disk(worker, channel_id, response)
    return build_user(channel_id, response, own=pages.is_own_channel(worker, channel_id, response))


def m_my_channel(worker, params):
    channel_id = known_own_channel(worker)
    if not channel_id:
        raise NoChannel()
    return build_user(channel_id, worker.yt._send_request("browse", {"browseId": channel_id}), own=True)


# ---- the library's profiles ------------------------------------------------------------------


def _profiles(contents):
    # A user channel is a profile, an artist an artist (ytm_library's rows).
    return library._each(contents, MRLIR, library.artist_item, "library profile")


def served_message(response):
    """The text of a page's messageRenderer row ("Only profiles with music
    activity are shown."), else None."""
    for row in raw.rows(response):
        key, data = raw.unwrap(row)
        if key != "messageRenderer" or not isinstance(data, dict):
            continue
        subtext = data.get("subtext") if isinstance(data.get("subtext"), dict) else {}
        text = raw.text(data.get("text")) or raw.text(subtext) \
            or raw.text(nav(subtext, ["messageSubtextRenderer", "text"], True))
        if text:
            return text
    return None


def _profiles_page(items, cursor, message=None, orders=None, order=None):
    sections = [norm.section(None, items, layout="shelf")] if items else []
    return norm.page("libraryProfiles", PROFILES_BROWSE_ID, None, sections=sections, cursor=cursor, message=message,
                     orders=orders, order=order)


# ---- G4: the profiles' sort orders --------------------------------------------------------
# The page serves Recently saved, A to Z and Z to A; each option's reload
# token carries ytmusicapi's order params in place of PROFILES_PARAMS, so
# an order goes into the body as the library's lists send it (one request).
# Only served orders are offered (none when the page serves no menu).


def profiles_body(order):
    """The browse body of the profiles list in ``order`` (one of
    ytm_library.ORDERS; anything else reads the plain list)."""
    params = prepare_order_params(order) if order in library.ORDERS else PROFILES_PARAMS
    return {"browseId": PROFILES_BROWSE_ID, "params": params}
# ---- end G4


def m_library_profiles(worker, params):
    worker.require_auth()
    order = library.order_param(params)  # G4
    cursor = opt_cursor(params)
    body = profiles_body(order)  # G4
    yt = worker.yt
    if cursor:
        items, cursor = pages.fetch(yt, "browse", body, "ctoken", None, _profiles, cursor=cursor,
                                    min_items=PAGE_ITEMS, max_http=library._budget(0))
        return _profiles_page(items, cursor, order=order)
    # G4: an ordered read is a saved setting: the cached browsePage copies go.
    first = library._first_page(worker, PROFILES_BROWSE_ID, body, order)
    response = first[0]
    orders, current = library._orders(first[3], order, offered=())  # G4
    message = served_message(response)
    shelf = podcasts._shelf(response, ("musicShelfRenderer",))
    if shelf is None:
        return _profiles_page([], None, message, orders, current)
    items, cursor = pages.fetch(yt, "browse", body, "ctoken", lambda _: shelf, _profiles, min_items=PAGE_ITEMS,
                                max_http=library._budget(first[1]), first=first)
    return _profiles_page(items, cursor, message, orders, current)


# ---- recorder (anonymous) --------------------------------------------------------------------


def record_user(yt, params):
    """``{channelId}``: a public profile's raw page, trimmed."""
    return podcasts.trim(yt._send_request("browse", {"browseId": params["channelId"]}))


# ---- registry (merged by ytm.py) -------------------------------------------------------------

METHODS = {"user": m_user, "myChannel": m_my_channel, "libraryProfiles": m_library_profiles}
# Not myChannel: its page is titled with your account's name.
CACHEABLE = {"user", "libraryProfiles"}
LANES = {name: "pages" for name in METHODS}
INVALIDATES = {}
RECORDERS = {"user": record_user}
MUTATES = set()
