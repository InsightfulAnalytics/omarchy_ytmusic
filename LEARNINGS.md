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

## The shell (QML)

- **Qt refuses duplicate method names in one component.** Two branches that each add
  `function openChooser()` to Panel.qml merge cleanly in git and then stop the whole panel loading
  ("Duplicate method name"). After any merge, load the panel (headless harness or qmllint
  `[duplicated-name]`), not only the tests.
- **A reply can land while its view is being destroyed.** Back, then a collection reply in the same
  turn, passed a `!root` check and called into an invalid context; the TypeError aborted the rest
  of that reply batch. CollectionView marks itself `_gone` in `Component.onDestruction` and every
  load handler checks it.
- **Hot reload keeps old compiled QML.** A real UI check needs `omarchy restart shell`.
