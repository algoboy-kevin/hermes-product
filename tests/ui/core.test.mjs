/**
 * Core (pure) tests for the `product` desktop page — card HP-14.
 *
 * Imports the REAL desktop/plugin.js through loader-hooks.mjs, which maps the SDK
 * and react onto stubs, then exercises the named `core` export. Nothing here
 * renders: the page's pure logic (state order, tab counts, the nav reducer, the
 * dependency layout, slug validation) is what A26-A33 rest on, so it is pinned
 * mechanically.
 *
 * Run: node --test tests/ui/core.test.mjs
 */

import { register } from "node:module"
import assert from "node:assert/strict"
import { test } from "node:test"

register(new URL("./loader-hooks.mjs", import.meta.url))

const { core } = await import("../../desktop/plugin.js")

const REQ = core.STATE_ORDER.requirement
const FEAT = core.STATE_ORDER.feature
const CRIT = core.STATE_ORDER.criterion

const entry = (id, status, extra = {}) => ({
  ref: `${extra.prefix || "R"}${id}`, id, title: `entry ${id}`, status, summary: "", links: {}, ...extra
})

// ------------------------------------------------------------------ vocabulary
test("VIEWS is the five focus views, Docs first", () => {
  assert.deepEqual(core.VIEWS, ["docs", "requirements", "acceptance", "features", "specs"])
})

test("every view has a label and a kind", () => {
  assert.deepEqual(core.VIEWS.map((view) => core.VIEW_LABEL[view]),
    ["Docs", "Requirements", "Acceptance", "Features", "Specs"])
  assert.deepEqual(core.VIEWS.map((view) => core.VIEW_KIND[view]),
    ["doc", "requirement", "criterion", "feature", "spec"])
})

test("STATE_ORDER mirrors GET /states (criterion failed-first)", () => {
  assert.deepEqual(core.STATE_ORDER.requirement, ["draft", "approved", "in_progress", "done", "dropped"])
  assert.deepEqual(core.STATE_ORDER.criterion, ["failed", "not_run", "passed", "waived"])
  assert.deepEqual(core.STATE_ORDER.feature, ["planned", "in_progress", "shipped", "cut"])
  assert.deepEqual(core.STATE_ORDER.spec, ["draft", "approved", "implemented", "verified", "superseded"])
  assert.deepEqual(core.STATE_ORDER.doc, ["current", "superseded"])
})

// ---------------------------------------------------------------- sortEntries
test("sortEntries puts states in workflow order", () => {
  const rows = [entry(4, "done"), entry(1, "draft"), entry(3, "in_progress"), entry(2, "approved")]
  assert.deepEqual(core.sortEntries(rows, REQ).map((row) => row.id), [1, 2, 3, 4])
})

test("sortEntries puts unknown states last", () => {
  const rows = [entry(9, "bogus"), entry(1, "draft"), entry(2, "done")]
  assert.deepEqual(core.sortEntries(rows, REQ).map((row) => row.id), [1, 2, 9])
})

test("sortEntries breaks ties by id ascending", () => {
  const rows = [entry(6, "draft"), entry(1, "draft"), entry(3, "draft")]
  assert.deepEqual(core.sortEntries(rows, REQ).map((row) => row.id), [1, 3, 6])
})

test("sortEntries lifts the PRD, then pinned documents, above the workflow order", () => {
  const order = ["current", "superseded"]
  const rows = [
    { id: 3, status: "current", title: "plain" },
    { id: 2, status: "current", title: "prd", is_prd: true },
    { id: 1, status: "superseded", title: "pinned old", pinned: true },
    { id: 4, status: "current", title: "pinned + prd", pinned: true, is_prd: true }
  ]
  // bf31ba3 "Sort rule corrected to PRD, then pinned, then the rest": a PRD outranks a
  // pinned document; within the PRD rank the tie-break stays id ascending.
  assert.deepEqual(core.sortEntries(rows, order).map((row) => row.title),
    ["prd", "pinned + prd", "pinned old", "plain"])
})

test("sortEntries orders criteria failed-first", () => {
  const rows = [entry(1, "not_run", { prefix: "A" }), entry(2, "failed", { prefix: "A" }),
    entry(3, "passed", { prefix: "A" }), entry(4, "waived", { prefix: "A" })]
  assert.deepEqual(core.sortEntries(rows, CRIT).map((row) => row.id), [2, 1, 3, 4])
})

test("sortEntries follows the feature order", () => {
  const rows = [entry(3, "shipped", { prefix: "F" }), entry(1, "planned", { prefix: "F" }),
    entry(4, "cut", { prefix: "F" }), entry(2, "in_progress", { prefix: "F" })]
  assert.deepEqual(core.sortEntries(rows, FEAT).map((row) => row.id), [1, 2, 3, 4])
})

test("sortEntries puts a current doc before a superseded one", () => {
  const rows = [entry(2, "superseded", { prefix: "DOC" }), entry(1, "current", { prefix: "DOC" })]
  assert.deepEqual(core.sortEntries(rows, core.STATE_ORDER.doc).map((row) => row.id), [1, 2])
})

test("sortEntries does not mutate its input and returns a new array", () => {
  const rows = [entry(2, "done"), entry(1, "draft")]
  const copy = rows.slice()
  const sorted = core.sortEntries(rows, REQ)
  assert.deepEqual(rows, copy)
  assert.notEqual(sorted, rows)
})

test("sortEntries of an empty list is empty", () => {
  assert.deepEqual(core.sortEntries([], REQ), [])
})

// ------------------------------------------------------------------ tabCounts
test("tabCounts counts every state, zeros included", () => {
  const rows = [entry(1, "draft"), entry(2, "draft"), entry(3, "done")]
  const { all, byState } = core.tabCounts(rows, REQ)
  assert.equal(all, 3)
  assert.deepEqual(Object.keys(byState).sort(), REQ.slice().sort())
  assert.equal(byState.draft, 2)
  assert.equal(byState.approved, 0)
  assert.equal(byState.done, 1)
  assert.equal(byState.dropped, 0)
})

test("tabCounts byState sums to all", () => {
  const rows = [entry(1, "draft"), entry(2, "approved"), entry(3, "approved"), entry(4, "dropped")]
  const { all, byState } = core.tabCounts(rows, REQ)
  const sum = Object.values(byState).reduce((total, n) => total + n, 0)
  assert.equal(sum, all)
})

test("tabCounts on no entries is all zero with every state present", () => {
  const { all, byState } = core.tabCounts([], FEAT)
  assert.equal(all, 0)
  assert.deepEqual(byState, { planned: 0, in_progress: 0, shipped: 0, cut: 0 })
})

test("tabCounts keeps an unknown state countable so the sum still holds", () => {
  const rows = [entry(1, "draft"), entry(2, "mystery")]
  const { all, byState } = core.tabCounts(rows, REQ)
  assert.equal(all, 2)
  assert.equal(byState.mystery, 1)
  assert.equal(Object.values(byState).reduce((t, n) => t + n, 0), all)
})

// --------------------------------------------------------------- filterByState
test("filterByState('all') returns every entry", () => {
  const rows = [entry(1, "draft"), entry(2, "done")]
  assert.equal(core.filterByState(rows, "all").length, 2)
})

test("filterByState keeps only the chosen state", () => {
  const rows = [entry(1, "draft"), entry(2, "done"), entry(3, "draft")]
  assert.deepEqual(core.filterByState(rows, "draft").map((row) => row.id), [1, 3])
})

test("filterByState with nothing in that state is empty", () => {
  assert.deepEqual(core.filterByState([entry(1, "draft")], "done"), [])
})

// ------------------------------------------- filterByKind / kindCounts / toggleKind (HP-29)
const doc = (id, kind, status = "current") =>
  ({ ref: "DOC" + id, id, title: "doc " + id, status, kind, summary: "", links: {} })
const DOCS = [doc(1, "report"), doc(2, "release_notes"), doc(3, "run_log"), doc(4, "report", "superseded")]

test("DOC_KINDS is the model enum with the new kinds before other", () => {
  assert.deepEqual(core.DOC_KINDS, ["prd", "spec", "context", "research", "design", "api",
    "runbook", "report", "release_notes", "run_log", "other"])
})

test("filterByKind with nothing selected shows every row", () => {
  assert.equal(core.filterByKind(DOCS, []).length, 4)
  assert.equal(core.filterByKind(DOCS, ["all"]).length, 4)
  assert.equal(core.filterByKind(DOCS, undefined).length, 4)
})

test("filterByKind keeps only the checked kinds", () => {
  assert.deepEqual(core.filterByKind(DOCS, ["report"]).map((row) => row.id), [1, 4])
})

test("filterByKind is multi-select", () => {
  assert.deepEqual(core.filterByKind(DOCS, ["release_notes", "run_log"]).map((row) => row.id), [2, 3])
})

test("filterByKind with a kind nothing has is empty", () => {
  assert.deepEqual(core.filterByKind(DOCS, ["bogus"]), [])
})

test("filterByKind returns a new array", () => {
  const rows = []
  assert.notEqual(core.filterByKind(rows, []), rows)
})

test("kindCounts counts every kind present", () => {
  assert.deepEqual(core.kindCounts(DOCS), { report: 2, release_notes: 1, run_log: 1 })
})

test("kindCounts on no entries is empty", () => {
  assert.deepEqual(core.kindCounts([]), {})
})

// ------------------------------------------------------- the kind badge (HP-38, A43)
test("DOC_KIND_COLOR names one colour per doc kind and keeps the PRD yellow", () => {
  assert.deepEqual(Object.keys(core.DOC_KIND_COLOR), core.DOC_KINDS)
  assert.equal(core.DOC_KIND_COLOR.prd, "#eab308")
  assert.equal(core.DOC_KIND_COLOR.release_notes, "#3fb950")
  // every kind is a colour of its own, so no two badges can read the same
  assert.equal(new Set(Object.values(core.DOC_KIND_COLOR)).size, core.DOC_KINDS.length)
})

test("kindBadge labels the kind in lower case with underscores as spaces", () => {
  assert.deepEqual(core.kindBadge(doc(1, "prd")), { label: "prd", color: "#eab308" })
  assert.deepEqual(core.kindBadge(doc(1, "release_notes")), { label: "release notes", color: "#3fb950" })
  assert.deepEqual(core.kindBadge(doc(1, "run_log")), { label: "run log", color: "#6e7681" })
  assert.deepEqual(core.kindBadge(doc(1, "report")), { label: "report", color: "#db6d28" })
})

test("kindBadge falls back to the `other` colour for an unknown kind", () => {
  assert.deepEqual(core.kindBadge(doc(1, "bogus")),
    { label: "bogus", color: core.DOC_KIND_COLOR.other })
})

test("kindBadge is null for a non-document or a document with no kind", () => {
  assert.equal(core.kindBadge({ ref: "R1", title: "a requirement", status: "draft" }), null)
  assert.equal(core.kindBadge({ ref: "A1", kind: "" }), null)
  assert.equal(core.kindBadge(null), null)
  assert.equal(core.kindBadge(undefined), null)
})

test("kindBadge still badges a card whose only flag is is_prd", () => {
  assert.deepEqual(core.kindBadge({ ref: "DOC2", is_prd: true }), { label: "prd", color: "#eab308" })
})

test("toggleKind turns All off on the first check", () => {
  assert.deepEqual(core.toggleKind([], "report"), ["report"])
})

test("toggleKind accumulates checks (multi-select)", () => {
  assert.deepEqual(core.toggleKind(["report"], "run_log"), ["report", "run_log"])
})

test("toggleKind unchecking the last kind returns to All", () => {
  assert.deepEqual(core.toggleKind(["report"], "report"), [])
  assert.deepEqual(core.toggleKind(["report", "run_log"], "report"), ["run_log"])
})

test("toggleKind the All row clears the selection", () => {
  assert.deepEqual(core.toggleKind(["report", "run_log"], "all"), [])
})

// ----------------------------------------------------------------- navReducer
const start = { project: "shop", view: "features", tab: "shipped", featureMode: "graph", selected: "F2" }

test("selectProject lands on Docs with a clean slate", () => {
  const next = core.navReducer(start, { type: "selectProject", slug: "mobile" })
  assert.deepEqual(next, { project: "mobile", view: "docs", tab: "all", featureMode: "cards", selected: null })
})

test("selectView sets the view and resets tab and selection", () => {
  const next = core.navReducer(start, { type: "selectView", view: "docs" })
  assert.equal(next.view, "docs")
  assert.equal(next.tab, "all")
  assert.equal(next.selected, null)
})

test("selectView keeps featureMode", () => {
  const next = core.navReducer(start, { type: "selectView", view: "requirements" })
  assert.equal(next.featureMode, "graph")
})

test("selectTab sets the tab and keeps everything else", () => {
  const next = core.navReducer(start, { type: "selectTab", tab: "cut" })
  assert.equal(next.tab, "cut")
  assert.equal(next.view, start.view)
  assert.equal(next.selected, start.selected)
})

test("setFeatureMode applies on the Features view", () => {
  const next = core.navReducer(start, { type: "setFeatureMode", mode: "cards" })
  assert.equal(next.featureMode, "cards")
})

test("setFeatureMode is ignored off Features", () => {
  const state = { ...start, view: "requirements" }
  const next = core.navReducer(state, { type: "setFeatureMode", mode: "graph" })
  assert.equal(next.featureMode, "graph")
  assert.equal(next, state, "an ignored action returns the same state object")
})

test("select records the chosen entry", () => {
  const next = core.navReducer(start, { type: "select", ref: "R3" })
  assert.equal(next.selected, "R3")
})

test("clearSelection drops it", () => {
  assert.equal(core.navReducer(start, { type: "clearSelection" }).selected, null)
})

test("an unknown action changes nothing", () => {
  assert.equal(core.navReducer(start, { type: "nope" }), start)
})

test("the reducer never mutates the state it was given", () => {
  const state = { ...start }
  core.navReducer(state, { type: "selectView", view: "docs" })
  core.navReducer(state, { type: "selectProject", slug: "x" })
  assert.deepEqual(state, start)
})

test("the initial state opens on Docs, All, cards, nothing selected", () => {
  assert.deepEqual(core.initialState, { project: null, view: "docs", tab: "all", featureMode: "cards", selected: null })
})

// ------------------------------------------------------- showStatusTabs/Mode
test("status tabs show on every view", () => {
  for (const view of ["docs", "requirements", "acceptance", "features", "specs"]) {
    assert.equal(core.showStatusTabs(view), true, view)
  }
})

test("the Cards|Graph toggle exists on Features only", () => {
  assert.equal(core.showModeToggle("features"), true)
  for (const view of ["docs", "requirements", "acceptance", "specs"]) {
    assert.equal(core.showModeToggle(view), false, view)
  }
})

// ----------------------------------------------------------------- statusColor
test("statusColor separates failed from passed and covers every state", () => {
  const states = new Set([...REQ, ...FEAT, ...CRIT, ...core.STATE_ORDER.spec, ...core.STATE_ORDER.doc])
  const colors = new Set()
  for (const state of states) {
    const color = core.statusColor(state)
    assert.equal(typeof color, "string")
    assert.ok(color.length > 0, state)
    colors.add(color)
  }
  assert.ok(colors.size >= 4, "at least four distinct tones")
  assert.notEqual(core.statusColor("failed"), core.statusColor("passed"))
  assert.equal(typeof core.statusColor(null), "string")
})

// ----------------------------------------------------------------- layoutGraph
const graphNodes = [
  { id: 1, ref: "F1", title: "Stripe", status: "shipped" },
  { id: 2, ref: "F2", title: "Mobile pay", status: "planned" },
  { id: 3, ref: "F3", title: "Legacy", status: "cut" }
]

test("layoutGraph layers a chain 0,1,2", () => {
  const layout = core.layoutGraph(graphNodes,
    [{ from: "F1", to: "F2", optional: false }, { from: "F2", to: "F3", optional: false }], [])
  const layer = Object.fromEntries(layout.nodes.map((node) => [node.ref, node.layer]))
  assert.deepEqual(layer, { F1: 0, F2: 1, F3: 2 })
})

test("layoutGraph gives a diamond its longest chain", () => {
  const nodes = ["F1", "F2", "F3", "F4"].map((ref, index) => ({ id: index + 1, ref, title: ref, status: "planned" }))
  const layout = core.layoutGraph(nodes, [
    { from: "F1", to: "F2" }, { from: "F1", to: "F3" },
    { from: "F2", to: "F4" }, { from: "F3", to: "F4" }
  ], [])
  const layer = Object.fromEntries(layout.nodes.map((node) => [node.ref, node.layer]))
  assert.deepEqual(layer, { F1: 0, F2: 1, F3: 1, F4: 2 })
})

test("layoutGraph puts a node with no edges in layer 0", () => {
  const layout = core.layoutGraph([{ id: 1, ref: "F1", title: "alone", status: "planned" }], [], [])
  assert.equal(layout.nodes.length, 1)
  assert.equal(layout.nodes[0].layer, 0)
})

test("layoutGraph returns one rectangle per node at a fixed size", () => {
  const layout = core.layoutGraph(graphNodes, [], [])
  assert.equal(layout.nodes.length, 3)
  const sizes = new Set(layout.nodes.map((node) => `${node.w}x${node.h}`))
  assert.equal(sizes.size, 1)
})

test("layoutGraph is deterministic", () => {
  const edges = [{ from: "F1", to: "F2", optional: true }, { from: "F2", to: "F3" }]
  assert.deepEqual(core.layoutGraph(graphNodes, edges, ["F1", "F2"]),
    core.layoutGraph(graphNodes, edges, ["F1", "F2"]))
})

test("layoutGraph never overlaps two rectangles", () => {
  const nodes = ["F1", "F2", "F3", "F4", "F5", "F6"].map((ref, index) => ({ id: index + 1, ref, title: ref, status: "planned" }))
  const layout = core.layoutGraph(nodes, [
    { from: "F1", to: "F4" }, { from: "F2", to: "F4" },
    { from: "F3", to: "F5" }, { from: "F4", to: "F6" }
  ], [])
  const rects = layout.nodes
  for (let i = 0; i < rects.length; i += 1) {
    for (let j = i + 1; j < rects.length; j += 1) {
      const a = rects[i]
      const b = rects[j]
      const apart = a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y
      assert.ok(apart, `${a.ref} overlaps ${b.ref}`)
    }
  }
})

test("layoutGraph orders the nodes of a layer by id", () => {
  const nodes = [{ id: 7, ref: "F7", title: "seven", status: "planned" },
    { id: 2, ref: "F2", title: "two", status: "planned" },
    { id: 11, ref: "F11", title: "eleven", status: "planned" }]
  const layout = core.layoutGraph(nodes, [], [])
  assert.deepEqual(layout.nodes.map((node) => node.ref), ["F2", "F7", "F11"])
  assert.ok(layout.nodes[0].y < layout.nodes[1].y)
})

test("layoutGraph draws each edge from dependency to dependent", () => {
  const layout = core.layoutGraph(graphNodes, [{ from: "F1", to: "F2", optional: false }], [])
  const byRef = Object.fromEntries(layout.nodes.map((node) => [node.ref, node]))
  assert.equal(layout.edges.length, 1)
  const [edge] = layout.edges
  assert.equal(edge.from, "F1")
  assert.equal(edge.to, "F2")
  const points = edge.points
  assert.deepEqual(points[0], [byRef.F1.x + byRef.F1.w, byRef.F1.y + byRef.F1.h / 2])
  assert.deepEqual(points[points.length - 1], [byRef.F2.x, byRef.F2.y + byRef.F2.h / 2])
})

test("layoutGraph flags optional and critical edges", () => {
  const layout = core.layoutGraph(graphNodes,
    [{ from: "F1", to: "F2", optional: true }, { from: "F2", to: "F3", optional: false }],
    ["F1", "F2", "F3"])
  const byPair = Object.fromEntries(layout.edges.map((edge) => [`${edge.from}->${edge.to}`, edge]))
  assert.equal(byPair["F1->F2"].optional, true)
  assert.equal(byPair["F1->F2"].critical, true)
  assert.equal(byPair["F2->F3"].optional, false)
  assert.equal(byPair["F2->F3"].critical, true)
})

test("layoutGraph only flags critical edges that are consecutive in the path", () => {
  const layout = core.layoutGraph(graphNodes,
    [{ from: "F1", to: "F2" }, { from: "F2", to: "F3" }], ["F1", "F3"])
  assert.deepEqual(layout.edges.map((edge) => edge.critical), [false, false])
})

test("layoutGraph reports a positive canvas that holds every rectangle", () => {
  const layout = core.layoutGraph(graphNodes, [{ from: "F1", to: "F2" }], [])
  assert.ok(layout.width > 0 && layout.height > 0)
  for (const node of layout.nodes) {
    assert.ok(node.x >= 0 && node.y >= 0)
    assert.ok(node.x + node.w <= layout.width)
    assert.ok(node.y + node.h <= layout.height)
  }
})

test("layoutGraph ignores an edge whose ends are unknown", () => {
  const layout = core.layoutGraph(graphNodes, [{ from: "F1", to: "F9" }], [])
  assert.deepEqual(layout.edges, [])
})

test("layoutGraph survives a dependency cycle", () => {
  const layout = core.layoutGraph(graphNodes,
    [{ from: "F1", to: "F2" }, { from: "F2", to: "F1" }], [])
  assert.equal(layout.nodes.length, 3)
  for (const node of layout.nodes) assert.ok(Number.isFinite(node.layer))
})

// ---------------------------------------------------------- project slug/payload
test("validProjectSlug accepts the server's rule", () => {
  // store.SLUG_RULE is ^[a-z0-9][a-z0-9-]{0,39}$ — a trailing dash is legal there too.
  for (const slug of ["shop", "a", "a1", "my-project", "shop-", "p".repeat(40)]) {
    assert.equal(core.validProjectSlug(slug), true, slug)
  }
})

test("validProjectSlug rejects what the server rejects", () => {
  for (const slug of ["", "Shop", "-shop", "has space", "under_score", "p".repeat(41), "ünicode"]) {
    assert.equal(core.validProjectSlug(slug), false, JSON.stringify(slug))
  }
})

test("validProjectSlug needs a string", () => {
  assert.equal(core.validProjectSlug(null), false)
  assert.equal(core.validProjectSlug(undefined), false)
  assert.equal(core.validProjectSlug(7), false)
})

test("newProjectPayload trims both required fields", () => {
  assert.deepEqual(core.newProjectPayload("  shop  ", "  Shop  ", ""), { slug: "shop", name: "Shop" })
})

test("newProjectPayload omits an empty or blank root", () => {
  assert.deepEqual(core.newProjectPayload("shop", "Shop", "   "), { slug: "shop", name: "Shop" })
  assert.deepEqual(core.newProjectPayload("shop", "Shop", null), { slug: "shop", name: "Shop" })
})

test("newProjectPayload keeps a real root", () => {
  assert.deepEqual(core.newProjectPayload("shop", "Shop", " ~/repo "),
    { slug: "shop", name: "Shop", root: "~/repo" })
})

// ------------------------------------------------------------- small helpers
test("stateLabel reads as words", () => {
  assert.equal(core.stateLabel("in_progress"), "in progress")
  assert.equal(core.stateLabel("not_run"), "not run")
  assert.equal(core.stateLabel(""), "")
})

test("linkBadges names the link counts that are non-zero", () => {
  assert.deepEqual(core.linkBadges("requirement", { features: 2, specs: 1, criteria: 0 }), [["F", 2], ["S", 1]])
  assert.deepEqual(core.linkBadges("feature", { needs: 1, requirements: 3, specs: 0 }), [["N", 1], ["R", 3]])
  assert.deepEqual(core.linkBadges("feature", {}), [])
  assert.deepEqual(core.linkBadges("doc", { requirement: 1, criterion: 2, feature: 0, spec: 0 }),
    [["R", 1], ["A", 2]])
})

test("errorMessage digs the server's message out of whatever the host throws", () => {
  assert.equal(core.errorMessage(Object.assign(new Error("project 'shop' already exists"), { status: 400 })),
    "project 'shop' already exists")
  assert.equal(core.errorMessage({ error: "slug is required" }), "slug is required")
  assert.equal(core.errorMessage({ body: { error: "nope" } }), "nope")
  assert.equal(core.errorMessage("plain string"), "plain string")
  assert.equal(core.errorMessage(null), "The backend rejected that request.")
})


// ------------------------------------------------------------------- entry bodies
test("bodySizeLabel says chars below 1 KB and KB above", () => {
  assert.equal(core.bodySizeLabel(42), "42 chars")
  assert.equal(core.bodySizeLabel(1023), "1023 chars")
  assert.equal(core.bodySizeLabel(1024), "1.0 KB")
  assert.equal(core.bodySizeLabel(10844), "10.6 KB")
  assert.equal(core.bodySizeLabel(undefined), "0 chars")
})

test("bodyPath builds the read-only body route, encoding the ref and the options", () => {
  assert.equal(core.bodyPath("shop", "S1"), "/project/shop/entry/S1/body")
  assert.equal(core.bodyPath("shop", "S1", { section: "A & B", maxChars: 500 }),
    "/project/shop/entry/S1/body?section=A%20%26%20B&max_chars=500")
  assert.equal(core.bodyPath("shop", "DOC12"), "/project/shop/entry/DOC12/body")
})
