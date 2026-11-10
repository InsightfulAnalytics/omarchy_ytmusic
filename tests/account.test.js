// S16: account identities, audio quality and content location (YtmModel's "account" section).
const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

const ids = actions => actions.map(a => a.id)
const library = { page: true, pageKind: "library", signedIn: true }
const own = { index: 0, name: "Test Person", handle: "@testperson", photo: "https://yt3.ggpht.com/own", selected: true,
              brand: false, authUser: 0, otherLogin: false }
const brand = { index: 1, name: "Test Brand", handle: "@testbrand", photo: "https://yt3.ggpht.com/brand", selected: false,
                brand: true, pageId: "123456789012345678901", authUser: 0, otherLogin: false }
const second = { index: 2, name: "Second Login", handle: "", photo: null, selected: false, brand: false, authUser: 1, otherLogin: true }

test("audio quality: modes, titles, formats and the chooser", () => {
  assert.deepEqual(Model.QUALITIES, ["low", "normal", "high"])
  assert.deepEqual(["low", "normal", "high", "best", undefined].map(Model.qualityMode), ["low", "normal", "high", "high", "high"])
  assert.deepEqual(["low", "normal", "high", "x"].map(Model.qualityTitle), ["Low", "Normal", "High", "High"])
  assert.equal(Model.qualityFormat("low"), "bestaudio[abr<=70]/worstaudio")
  assert.equal(Model.qualityFormat("normal"), "bestaudio[abr<=130]/bestaudio")
  assert.equal(Model.qualityFormat("high"), "bestaudio[acodec=opus]/bestaudio/best")
  assert.equal(Model.qualityFormat("ultra"), Model.qualityFormat("high"))
  const rows = Model.qualityChoices("normal")
  assert.deepEqual(rows.map(r => r.value), ["low", "normal", "high"])
  assert.deepEqual(rows.filter(r => r.icon === Model.CHOSEN_ICON).map(r => r.value), ["normal"])
  assert.ok(rows.every(r => r.id && r.title && r.icon))
  assert.equal(rows[0].title, "Low (about 70 kbps, saves data)", "bestaudio[abr<=70] picks opus 250, about 69 kbps")
})

test("content location: the chooser lists Default and ytmusicapi's locations, not the chart countries", () => {
  assert.equal(Model.LOCATIONS.length, 109, "ytmusicapi 1.12.2 SUPPORTED_LOCATIONS")
  const codes = Model.LOCATIONS.map(l => l[0])
  assert.equal(new Set(codes).size, codes.length, "each code once")
  assert.ok(codes.every(c => /^[A-Z]{2}$/.test(c)))
  assert.ok(!codes.includes("ZZ"), "Global is a chart country, not a location")
  const names = Model.LOCATIONS.map(l => l[1])
  assert.deepEqual(names, names.slice().sort((a, b) => a.localeCompare(b, "en")), "sorted by name")
  const rows = Model.locationChoices("")
  assert.equal(rows.length, 110)
  assert.deepEqual([rows[0].value, rows[0].icon], ["", Model.CHOSEN_ICON])
  assert.match(rows[0].title, /^Default/)
  const au = Model.locationChoices("AU")
  assert.deepEqual(au.filter(r => r.icon === Model.CHOSEN_ICON).map(r => r.value), ["AU"])
  assert.equal(au.find(r => r.value === "AU").title, "Australia")
  assert.equal(new Set(au.map(r => r.id)).size, au.length, "chooser ids are unique")
  assert.deepEqual(["AU", "au", "ZZ", "XX", "", null, 7].map(Model.locationCode), ["AU", "AU", "", "", "", "", ""])
  assert.deepEqual(["AU", "GB", "", "ZZ"].map(Model.locationTitle), ["Australia", "United Kingdom", "Default", "Default"])
})

test("a content location opens Charts there only when Charts has that country", () => {
  assert.equal(Model.CHART_COUNTRIES.length, 68)
  const codes = Model.LOCATIONS.map(l => l[0])
  assert.ok(Model.CHART_COUNTRIES.every(c => codes.includes(c)), "every chart country is a location too")
  assert.ok(!Model.CHART_COUNTRIES.includes("ZZ"))
  assert.deepEqual(["AU", "us", "GR", "PK", "VE", "", "ZZ", null].map(Model.chartsCountryForLocation),
    ["AU", "US", "", "", "", "", "", ""])
  assert.equal(codes.filter(c => Model.chartsCountryForLocation(c) === "").length, 41)
})

test("prefs round trip: location and quality are kept and sent to the worker", () => {
  let prefs = Model.parsePrefs("")
  assert.equal(prefs.location, "")
  assert.equal(prefs.quality, "high")
  assert.deepEqual(Model.workerSettings(prefs), { normalize: "off", quality: "high" }, "no location: YouTube's guess")
  prefs = Model.withPref(Model.withPref(prefs, "location", "AU"), "quality", "low")
  const saved = Model.parsePrefs(Model.serializePrefs(prefs))
  assert.equal(saved.location, "AU")
  assert.equal(saved.quality, "low")
  assert.deepEqual(Model.workerSettings(saved), { normalize: "off", quality: "low", location: "AU" })
  // A location ytmusicapi does not know never reaches the worker (it would refuse it).
  assert.deepEqual(Model.workerSettings(Model.withPref(saved, "location", "ZZ")), { normalize: "off", quality: "low" })
  assert.deepEqual(Model.accountSettings(null), { quality: "high" })
  assert.deepEqual(Model.accountSettings({ quality: "normal", location: "de" }, { normalize: "dynamic" }),
    { normalize: "dynamic", quality: "normal", location: "DE" })
})

test("Library's M menu: Switch account, Audio quality, Content location, then Record plays and Sign out", () => {
  // S14's Add artists you like comes first, then S9's Your channel.
  assert.deepEqual(ids(Model.menuActions(null, library)),
    ["tasteProfile", "myChannel", "switchAccount", "audioQuality", "contentLocation", "recordHistory", "streamAccount", "signOut"])
  assert.deepEqual(ids(Model.menuActions(null, Object.assign({}, library, { signedIn: false }))),
    ["audioQuality", "contentLocation", "signIn"], "quality and location are not account-only")
  const titled = Model.menuActions(null, Object.assign({ quality: "low", location: "AU" }, library))
  assert.equal(titled.find(a => a.id === "audioQuality").title, "Audio quality (Low)")
  assert.equal(titled.find(a => a.id === "contentLocation").title, "Content location (Australia)")
  const plain = Model.menuActions(null, library)
  assert.equal(plain.find(a => a.id === "audioQuality").title, "Audio quality (High)")
  assert.equal(plain.find(a => a.id === "contentLocation").title, "Content location (Default)")
  // Not on a row's menu, not on another page's.
  assert.ok(!ids(Model.menuActions(own, { pageKind: "library", signedIn: true })).includes("switchAccount"))
  assert.ok(!ids(Model.menuActions(null, { page: true, pageKind: "home", signedIn: true })).includes("audioQuality"))

  const opened = []
  const set = []
  const panel = { openChooser: request => opened.push(request) }
  const service = { setQuality: v => set.push(["quality", v]), setLocation: v => set.push(["location", v]),
                    openAccountChooser: p => set.push(["accounts", p === panel]) }
  const ctx = Object.assign({ quality: "normal", location: "DE" }, library)
  for (const action of Model.menuActions(null, ctx)) {
    if (["switchAccount", "audioQuality", "contentLocation"].includes(action.id)) action.run({ item: null, ctx, panel, service })
  }
  assert.deepEqual(opened.map(r => r.title), ["Audio quality", "Content location"])
  assert.deepEqual(opened[0].choices.filter(c => c.icon === Model.CHOSEN_ICON).map(c => c.value), ["normal"])
  assert.deepEqual(opened[1].choices.filter(c => c.icon === Model.CHOSEN_ICON).map(c => c.value), ["DE"])
  opened[0].onChoice(opened[0].choices[0])
  opened[1].onChoice(opened[1].choices[0])
  assert.deepEqual(set, [["accounts", true], ["quality", "low"], ["location", ""]])
})

test("the account chooser: the one in use ticked, brand accounts, other logins sign in again", () => {
  const rows = Model.accountChoices([own, brand, second])
  assert.deepEqual(rows.map(r => r.title), ["Test Person", "Test Brand", "Second Login", "Another Google account"])
  assert.deepEqual(rows.map(r => r.value.kind), ["current", "switch", "signIn", "another"])
  assert.deepEqual(rows.map(r => r.icon), [Model.CHOSEN_ICON, Model.BRAND_ICON, Model.ACCOUNT_ICON, rows[3].icon])
  assert.equal(rows[1].hint, "@testbrand")
  assert.equal(rows[2].hint, "bin/ytm auth --authuser 1")
  assert.equal(rows[3].id, Model.ANOTHER_LOGIN_ID)
  assert.equal(rows[3].hint, "bin/ytm auth --authuser N")
  assert.equal(new Set(rows.map(r => r.id)).size, rows.length)
  assert.equal(Model.AUTHUSER_SWITCH, false, "another login's header rewrite is not verified live")
  // A login whose index YouTube did not give cannot be reached: left out.
  const unknown = Object.assign({}, second, { authUser: null })
  assert.deepEqual(Model.accountChoices([own, unknown]).map(r => r.title), ["Test Person", "Another Google account"])
  assert.deepEqual(Model.accountChoices(null).map(r => r.value.kind), ["another"])
  // Another login's brand account: signing in starts as that login's own account.
  const otherBrand = Object.assign({}, brand, { index: 3, name: "Other Brand", authUser: 1, otherLogin: true })
  const other = Model.accountChoices([own, otherBrand])[1]
  assert.deepEqual([other.value.kind, other.hint, other.icon], ["signIn", "bin/ytm auth --authuser 1, then switch", Model.BRAND_ICON])
  const first = Model.signInFirstNotice(otherBrand)
  assert.equal(first.title, "Signing in as Google account 1 first")
  assert.match(first.body, /its own account; then choose Other Brand under Switch account/)
  // Acting as the brand account: the own account switches back.
  const asBrand = Model.accountChoices([Object.assign({}, own, { selected: false }), Object.assign({}, brand, { selected: true })])
  assert.deepEqual(asBrand.map(r => r.value.kind), ["switch", "current", "another"])
})

test("switchAccount params: the pageId of a brand account, the login index", () => {
  assert.deepEqual(Model.switchParams(brand), { pageId: "123456789012345678901", authUser: 0 })
  assert.deepEqual(Model.switchParams(own), { pageId: null, authUser: 0 })
  assert.deepEqual(Model.switchParams({ brand: false, pageId: "1", authUser: null }), { pageId: null })
  assert.deepEqual(Model.switchParams(null), { pageId: null })
})

test("the account row shows the identity in use, never a stale name", () => {
  const account = { name: "Test Person", handle: "@testperson", photo: "p" }
  assert.equal(Model.activeAccount(null, null, null), null)
  assert.equal(Model.activeAccount(account, null, null), account)
  assert.equal(Model.activeAccount(account, { key: "0:", brand: false, own: true, authUser: 0 }, null), account)
  const asBrand = { key: "0:abc", brand: true, own: false, authUser: 0 }
  assert.deepEqual(Model.activeAccount(account, asBrand, null), { name: "Brand account", handle: "", photo: "", note: "" },
    "until the worker named it")
  const shown = Model.activeAccount(account, asBrand, { name: "Test Brand", handle: "@testbrand", photo: "b" })
  assert.deepEqual(shown, { name: "Test Brand", handle: "@testbrand", photo: "b", note: "Brand account" })
  assert.equal(Model.accountCaption(shown), "@testbrand · Brand account")
  assert.equal(Model.accountCaption(account), "@testperson")
  assert.equal(Model.accountCaption(null), "")
  const login = Model.activeAccount(account, { key: "1:", brand: false, own: false, authUser: 1 }, { name: "Second" })
  assert.equal(Model.accountCaption(login), "Google account 1")
  assert.equal(Model.otherIdentity(null), false)
  assert.equal(Model.otherIdentity(asBrand), true)
})

test("identity.json tells the identity until the worker says, and while it is stopped", () => {
  const ownSaved = { brand: false, own: true, authUser: null }
  for (const text of ["", null, "not json", "[]", "7", '{"pageId": "x1"}', '{"authUser": 99}', '{"authUser": "1"}',
                      '{"authUser": true}', '{"pageId": 123}', '{"version": 1, "pageId": null, "authUser": null}'])
    assert.deepEqual(Model.savedIdentity(text), ownSaved, String(text))
  assert.deepEqual(Model.savedIdentity('{"version": 1, "pageId": "123456789012345678901", "authUser": null}'),
    { brand: true, own: false, authUser: null })
  assert.deepEqual(Model.savedIdentity('{"version": 1, "pageId": null, "authUser": 1}'), { brand: false, own: false, authUser: 1 })
  assert.ok(!JSON.stringify(Model.savedIdentity('{"pageId": "123456789012345678901"}')).includes("1234"), "no id kept")
  const reported = { key: "0:abc", brand: true, own: false, authUser: 0 }
  const saved = Model.savedIdentity('{"pageId": "123", "authUser": null}')
  assert.equal(Model.shownIdentity(false, true, reported, saved), null, "no session")
  assert.equal(Model.shownIdentity(true, true, reported, ownSaved), reported, "the running worker's")
  assert.equal(Model.shownIdentity(true, false, reported, ownSaved), ownSaved, "stopped: the file it starts with")
  assert.equal(Model.shownIdentity(true, true, null, saved), saved, "before the worker said")
  assert.equal(Model.shownIdentity(true, false, reported, null), reported, "before the file was read")
  assert.equal(Model.shownIdentity(true, false, null, null), null)
  // Before the worker started, a saved brand account shows as one.
  const account = { name: "Test Person", handle: "@testperson", photo: "p" }
  assert.equal(Model.activeAccount(account, Model.shownIdentity(true, false, null, saved), null).name, "Brand account")
})

test("expired as a brand account or another login: your own account is offered", () => {
  const asBrand = { key: "0:abc", brand: true, own: false, authUser: 0 }
  assert.match(Model.expiryNotice(asBrand).title, /did not answer as the account in use/)
  assert.match(Model.expiryNotice(asBrand).body, /your own account/)
  assert.equal(Model.expiryNotice(null).title, "YouTube Music sign-in expired")
  assert.equal(Model.expiryNotice({ key: "0:", brand: false, own: true, authUser: 0 }).title, "YouTube Music sign-in expired")
  const expired = Object.assign({}, library, { signedIn: false, canUseOwnAccount: true })
  assert.deepEqual(ids(Model.menuActions(null, expired)), ["audioQuality", "contentLocation", "useOwnAccount", "signIn"])
  assert.ok(!ids(Model.menuActions(null, Object.assign({}, library, { canUseOwnAccount: true }))).includes("useOwnAccount"),
    "signed in: Switch account does it")
  const used = []
  Model.menuActions(null, expired).find(a => a.id === "useOwnAccount").run({ service: { useOwnAccount: () => used.push(1) } })
  assert.deepEqual(used, [1])
})

test("the account list is asked again after a while; the selected identity", () => {
  assert.equal(Model.accountsFresh(null, 1000), false)
  assert.equal(Model.accountsFresh({ items: [own], at: 1000 }, 1000 + Model.ACCOUNTS_TTL_MS - 1), true)
  assert.equal(Model.accountsFresh({ items: [own], at: 1000 }, 1000 + Model.ACCOUNTS_TTL_MS), false)
  assert.equal(Model.selectedAccount([own, brand]), own)
  assert.equal(Model.selectedAccount([brand]), null)
  assert.equal(Model.selectedAccount(undefined), null)
})

test("notices name what changed; no dashes in the section's text", () => {
  const n = Model.switchedNotice(brand, { name: "Test Brand" })
  assert.equal(n.title, "Switched to Test Brand")
  assert.match(n.body, /brand account/)
  assert.equal(Model.switchedNotice(own, null).body, "")
  assert.match(Model.anotherLoginNotice().body, /bin\/ytm auth --authuser N/)
  const strings = [n.title, n.body, Model.anotherLoginNotice().body, Model.signInFirstNotice(brand).title,
                   Model.signInFirstNotice(brand).body, Model.expiryNotice({ own: false }).title, Model.expiryNotice({ own: false }).body]
    .concat(Model.qualityChoices("high").map(r => r.title), Model.locationChoices("").map(r => r.title),
            Model.accountChoices([own, brand, second]).map(r => r.title + r.hint),
            Model.menuActions(null, library).map(a => a.title),
            Model.menuActions(null, Object.assign({}, library, { signedIn: false, canUseOwnAccount: true })).map(a => a.title))
  for (const s of strings) assert.ok(!/[\u2013\u2014]/.test(s), s)
})
