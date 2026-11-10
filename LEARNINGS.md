# Learnings

Root causes of the non-obvious problems hit while building this fork, so they are not rediscovered.

## Signing in from the browser

- **gnome-keyring hides other apps' portal secrets.** Items stored through the xdg-desktop-portal
  Secret portal (schema `org.freedesktop.portal.Secret`) are listed to every client but raise
  `ItemNotFoundException` when read by anyone else. yt-dlp's keyring walk stopped at the first one.
  `ytm_auth.browser_keyring_view` skips them. Never delete them: they belong to sandboxed apps.
- **VS Code (Electron) stores its own "Chromium Safe Storage" key.** yt-dlp picks the key by label
  only, so it could take VS Code's key and fail to decrypt Chromium's cookies. The view keeps the
  item whose `application` attribute matches the browser.

## YouTube Music behaviour

- **Edits read back late.** After subscribe, unsubscribe or saving a playlist, a fresh read can
  show the old state for 3 to 8 seconds. The shell keeps the user's intent in memory (Store maps)
  instead of trusting an immediate re-read.
- **An ordered library read changes the account's saved sort.** Library lists with an `order`
  param change the sort the web shows too, as the web's own sort menu does. Tests and live checks
  read in the default order.
- **Send a library order only when the user picks one.** A remembered order sent on every load
  saved it on whatever account was in use (a brand account, another login) and undid a sort set
  on the web or phone. Only `O` sends `order`; every other read sends none and shows the option
  YouTube marks selected. The worker drops the list's cached copies on an ordered read, since an
  unordered copy from before would show the old sort.
- **A signed-in search joins the account's search history.** Every debounced search ran signed in,
  so half-typed words showed up in the web's and phone's history and in the panel's own past
  searches. Typed searches go through the anonymous client; Enter or a picked suggestion runs once
  with the account. Every other path to a signed-in search counts too: a scope or filter picked for
  words not yet submitted waits for Enter.
- **A signed-out list has no library or Listen again tokens.** Its songs all say `inLibrary: false`
  and their menus lack Save and Pin. A typed search list reads a song's own menu (`nowContext`)
  when `m` or `S` needs the tokens, instead of searching again with the account.
- **ytmusicapi's `set_tasteprofile` reports every suggestion as seen.** Given the whole profile it
  sends an impression for all ~900 suggested artists and selects only the chosen ones, which may
  count as "not interested" in the rest. `setTasteProfile` passes only the chosen artists.
- **Albums over 200 songs are cut on the album page.** The browse page serves at most 200 rows and
  no continuation; the rest comes from the album's audio playlist (`album_rest`).
- **Anonymous See all endpoints are bound to the visitor id** of the request that served them. One
  `bin/ytm` process per call gets an empty page; test them inside one long-lived worker, as the
  panel runs.
- **Skip duplicates answers with no entries when nothing was new.** The refusal's own Skip
  duplicates command returns an entry per song it adds, and none when every song was already there.
  A whole-playlist add with `DEDUPE_OPTION_DROP_DUPLICATE` returns no entries even when it adds.

## Playback

- **The worker's yt-dlp needs `yt-dlp[default]`.** Plain `yt-dlp` from PyPI has no `yt-dlp-ejs`, so
  YouTube's JavaScript challenge cannot be solved even with deno installed. The failed load marks
  the solver unavailable for the life of the YoutubeDL instance, and every lookup after that falls
  back to the clients that need no JavaScript ("No supported JavaScript runtime could be found").
- **"Sign in to confirm you're not a bot" is about the connection, not yt-dlp.** After a burst of
  lookups (429 Too Many Requests) YouTube refuses signed-out stream lookups from the address for a
  while; an up-to-date yt-dlp with the solver gets the same answer. More lookups prolong it: the
  worker answers `BOT_CHECK` and the shell stops background lookups and playback until it lifts.

## The shell (QML)

- **Qt refuses duplicate method names in one component.** Two branches that each add
  `function openChooser()` to Panel.qml merge cleanly in git and then stop the whole panel loading
  ("Duplicate method name"). After any merge, load the panel (headless harness or qmllint
  `[duplicated-name]`), not only the tests.
- **A reply can land while its view is being destroyed.** Back, then a collection reply in the same
  turn, passed a `!root` check and called into an invalid context; the TypeError aborted the rest
  of that reply batch. CollectionView marks itself `_gone` in `Component.onDestruction` and every
  load handler checks it.
- **A binding read inside a change handler is stale when the Store answers synchronously.**
  CollectionView.open() runs from onEntryChanged, and a page the Store holds in memory is answered
  inside it; ResultList then read `listKey` while its binding on `entry` still held the page before
  (bindings re-run after the handler), and kept that key. The next rows change (Load all, Load more,
  `x`) took the page for a new list: cursor gone, back to the top. Make the binding depend on a
  property the handler sets before the synchronous call (`ref`), or pass the value explicitly.
- **A reply asked before a Store reset belongs to the identity before.** A collection, a
  stale-while-revalidate follow-up or a nowContext that answers after `Store.reset()` (a switch,
  sign-in, location) was kept as the new identity's. The Store counts resets (`resetEpoch`); a
  reply from an older epoch is answered `stale` and never kept, and the view asks again.
- **Hot reload keeps old compiled QML.** A real UI check needs `omarchy restart shell`.
