/**
 * Registration + render smoke tests for the `product` desktop page — card HP-14.
 *
 * Same method as kanban-gantt's tests/ui/esm-render.mjs: the REAL desktop/plugin.js
 * is imported as an ESM module through a Node loader that maps
 * @hermes/plugin-sdk / react / react/jsx-runtime onto stubs, then register(ctx) is
 * called with a fake host and the page is RENDERED with no browser. The stub pair
 * here is a small stateful renderer instead of stateless tags, so a click can
 * actually change the view — which is what A26/A29/A31/A32 need to be provable.
 *
 * The plugin must survive every path this file drives: PRD markdown into the
 * Streamdown stub, six focus views, status tabs with counts, cards, the detail
 * panel and its CLI command, the Features Cards|Graph toggle, the New project
 * dialog (success and 400), and a backend that refuses to answer (ErrorState,
 * never a blank page).
 *
 * Run: node tests/ui/esm-render.mjs   → prints PASS, exits non-zero on a failure.
 */

import { readFileSync } from "node:fs"
import { fileURLToPath, pathToFileURL } from "node:url"
import { dirname, join } from "node:path"
import { register } from "node:module"

const __dirname = dirname(fileURLToPath(import.meta.url))
const PLUGIN_PATH = join(__dirname, "..", "..", "desktop", "plugin.js")
// Payloads the REAL API produced (tests/test_ui_fixtures.py regenerates and compares them):
// a fixture the page author shapes proves what the author believed, not what the API returns.
const REAL = JSON.parse(readFileSync(join(__dirname, "fixtures", "api-entries.json"), "utf8"))

// --------------------------------------------------------------------------- harness
let failures = 0
let checks = 0
const check = (condition, message) => {
  checks += 1
  if (!condition) {
    failures += 1
    console.error("FAIL: " + message)
  }
}

// --------------------------------------------------------------------------- mini react
/* A stateful, path-keyed renderer: every component invocation gets a stable slot
 * path, so useState survives a re-render (React does this with fibre, we do it
 * with a Map keyed by tree position). Components are invoked eagerly; intrinsic
 * elements keep their props so a test can find a node and call its onClick. */
const slots = new Map()
const effects = []
const renderErrors = []
let dirty = false
let tree = null
let currentPath = "root"
let hookIndex = 0

const sameDeps = (a, b) => {
  if (a === b) return true
  if (!a || !b || a.length !== b.length) return false
  for (let i = 0; i < a.length; i += 1) if (!Object.is(a[i], b[i])) return false
  return true
}

function slot() {
  const key = currentPath + "#" + hookIndex
  hookIndex += 1
  let found = slots.get(key)
  if (!found) {
    found = { ready: false, deps: null, value: undefined, cleanup: null }
    slots.set(key, found)
  }
  return found
}

function useState(initial) {
  const found = slot()
  if (!found.ready) {
    found.value = typeof initial === "function" ? initial() : initial
    found.ready = true
  }
  const set = (next) => {
    const value = typeof next === "function" ? next(found.value) : next
    if (Object.is(value, found.value)) return
    found.value = value
    dirty = true
  }
  return [found.value, set]
}

function useEffect(fn, deps) {
  const found = slot()
  const next = deps || null
  if (!found.ready || !sameDeps(found.deps, next)) {
    found.deps = next
    found.ready = true
    effects.push({ found, fn })
  }
}

function useMemo(fn, deps) {
  const found = slot()
  const next = deps || null
  if (!found.ready || !sameDeps(found.deps, next)) {
    found.value = fn()
    found.deps = next
    found.ready = true
  }
  return found.value
}

function useRef(initial) {
  const found = slot()
  if (!found.ready) {
    found.value = { current: initial }
    found.ready = true
  }
  return found.value
}

function useCallback(fn, deps) {
  const found = slot()
  const next = deps || null
  if (!found.ready || !sameDeps(found.deps, next)) {
    found.value = fn
    found.deps = next
    found.ready = true
  }
  return found.value
}

function useReducer(reducer, initial) {
  const found = slot()
  if (!found.ready) {
    found.value = typeof initial === "function" ? initial() : initial
    found.ready = true
  }
  const dispatch = (action) => {
    const next = reducer(found.value, action)
    if (next === found.value) return
    found.value = next
    dirty = true
  }
  return [found.value, dispatch]
}

const createElement = (type, props, ...children) => {
  const merged = { ...(props || {}) }
  const kids = children.length ? children
    : (merged.children !== undefined ? [].concat(merged.children) : [])
  if (kids.length) merged.children = kids
  return { $$el: true, type, props: merged }
}

function walk(element, nodePath) {
  if (element === null || element === undefined || element === false || element === true) return null
  if (Array.isArray(element)) {
    return element.map((child, i) => walk(child, nodePath + "." + i)).filter(Boolean)
  }
  if (typeof element === "string" || typeof element === "number") return { text: String(element) }
  if (!element || !element.$$el) return null
  const { type, props } = element
  if (typeof type === "function") {
    const prevPath = currentPath
    const prevIndex = hookIndex
    currentPath = nodePath
    hookIndex = 0
    let out = null
    let error = null
    try {
      out = type(props)
    } catch (err) {
      error = err
      renderErrors.push(err)
    }
    const inner = walk(out, nodePath + "/r")
    currentPath = prevPath
    hookIndex = prevIndex
    return { component: type.name || "Component", props, error, node: inner }
  }
  const kids = props.children !== undefined ? [].concat(props.children) : []
  return {
    type,
    props,
    // a child may itself be an array (a mapped list, [a].concat(b)): flatten, or the
    // subtree behind it is never walked and the test silently sees nothing
    children: kids.map((child, i) => walk(child, nodePath + "." + i)).flat(Infinity).filter(Boolean)
  }
}

const renderRoot = (element) => {
  currentPath = "root"
  hookIndex = 0
  tree = walk(element, "root")
}

const settle = async () => {
  for (let i = 0; i < 80; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0))
    let progressed = false
    if (dirty) {
      dirty = false
      dirty = false
      // Re-render through the same element the session mounted.
      renderRoot(mountedElement)
      progressed = true
    }
    if (effects.length) {
      const batch = effects.splice(0, effects.length)
      for (const item of batch) {
        try {
          const cleanup = item.fn()
          item.found.cleanup = typeof cleanup === "function" ? cleanup : null
        } catch (err) {
          renderErrors.push(err)
          check(false, "an effect threw: " + (err && err.stack ? err.stack : err))
        }
      }
      progressed = true
    }
    if (!progressed) return
  }
  check(false, "settle() did not quiesce: the page may be looping")
}

let mountedElement = null

const flatten = (node, out = []) => {
  if (!node) return out
  if (Array.isArray(node)) {
    node.forEach((child) => flatten(child, out))
    return out
  }
  out.push(node)
  if (node.children) node.children.forEach((child) => flatten(child, out))
  if (node.node) flatten(node.node, out)
  return out
}

const nodes = () => flatten(tree)
const all = (predicate) => nodes().filter(predicate)
// A component wrapper keeps the props it was handed, so a data-testid would be
// counted twice (wrapper + rendered element). Match rendered elements only.
const propsOf = (node) => (!node.component && node.props) || null
const withProps = (predicate) => all((node) => {
  const props = propsOf(node)
  return props ? predicate(props) : false
})
const byId = (id) => withProps((props) => props["data-testid"] === id)
const one = (id) => byId(id)[0]
const textOf = (node) => {
  if (!node) return ""
  if (node.text !== undefined) return node.text
  let out = ""
  if (node.children) out += node.children.map(textOf).join(" ")
  if (node.node) out += " " + textOf(node.node)
  return out
}
const text = (node) => textOf(node)
const markup = () => JSON.stringify(tree)

async function clickNode(node, label) {
  if (!node) {
    throw new Error("nothing to click: " + label + "\nnodes: " +
      nodes().map((item) => (item.component ? "C:" + item.component
        : String(item.type) + (item.props && item.props["data-testid"] ? "#" + item.props["data-testid"] : "")))
        .join(" | ").slice(0, 1800))
  }
  const handler = node.props && node.props.onClick
  if (typeof handler !== "function") throw new Error(label + ": element has no onClick")
  handler({ preventDefault() {}, stopPropagation() {} })
  await settle()
}

const click = (id, label) => clickNode(one(id), label || "click " + id)
const clickView = (view) => clickNode(focus().find((node) => node.props["data-view"] === view),
  "focus view " + view)

async function type(id, value) {
  const node = one(id)
  if (!node) throw new Error("no input " + id)
  const handler = node.props.onChange || node.props.onInput
  if (typeof handler !== "function") throw new Error("input " + id + " has no change handler")
  handler({ target: { value } })
  await settle()
}

// --------------------------------------------------------------------------- SDK stubs
const hostStub = { navigate: (path) => { hostStub.last = path }, notify: () => "n" }
const passthrough = (tagName) => (props) => createElement(tagName, props,
  ...(props.children !== undefined ? [].concat(props.children) : []))

globalThis.__productUIKit = {
  Badge: passthrough("span"),
  Button: passthrough("button"),
  Codicon: (props) => createElement("span", { "data-icon": props.name }),
  Contribute: (props) => createElement("contribute", { area: props.area, id: props.id }, props.children),
  Dialog: (props) => (props.open
    ? createElement("div", { "data-testid": props["data-testid"] || "dialog", "data-open": "1" }, props.children)
    : null),
  DialogContent: passthrough("div"),
  DialogTitle: passthrough("h2"),
  DropdownMenu: passthrough("div"),
  DropdownMenuContent: passthrough("div"),
  DropdownMenuItem: passthrough("div"),
  DropdownMenuSeparator: () => createElement("hr", {}),
  DropdownMenuTrigger: passthrough("div"),
  Input: passthrough("input"),
  EmptyState: (props) => createElement("div",
    { "data-testid": "empty-state", title: props.title, description: props.description },
    props.title, props.description),
  ErrorState: (props) => createElement("div",
    { "data-testid": "error-state", message: props.message || props.description || props.title },
    props.title, props.message || props.description),
  Loader: () => createElement("div", { "data-testid": "loader" }),
  // The page renders markdown through the SDK's chat renderer (MessageTextContent),
  // which takes `text` — the stub keeps the markdown text on the same testid so the
  // existing "does the text reach the renderer" checks still mean something.
  MessageTextContent: (props) => createElement("div", { "data-testid": "markdown" }, props.text),
  cn: (...parts) => parts.filter(Boolean).join(" "),
  host: hostStub
}

globalThis.__productReact = {
  createElement, useState, useEffect, useMemo, useRef, useCallback, useReducer
}

// --------------------------------------------------------------------------- fixtures
const STATES = {
  requirement: ["draft", "approved", "in_progress", "done", "dropped"],
  criterion: ["failed", "not_run", "passed", "waived"],
  feature: ["planned", "in_progress", "shipped", "cut"],
  spec: ["draft", "approved", "implemented", "verified", "superseded"],
  doc: ["current", "superseded"]
}
const PREFIX = { requirement: "R", criterion: "A", feature: "F", spec: "S", doc: "DOC" }
const PRD_HINT = 'hermes product add DOC --kind prd --title "<title>" --summary "<summary>"'

function entriesOf(kind, rows) {
  const entries = rows.map(([id, status, title, extra]) => ({
    ref: PREFIX[kind] + id,
    id,
    title,
    status,
    summary: "summary of " + title,
    links: (extra && extra.links) || {},
    ...(extra || {})
  }))
  const counts = { all: entries.length }
  for (const state of STATES[kind]) {
    counts[state] = entries.filter((entry) => entry.status === state).length
  }
  return { kind, counts, entries }
}

const shopDeck = () => ({
  requirement: entriesOf("requirement", [
    [1, "draft", "Pay by card", { links: { features: 1, specs: 1, criteria: 2 } }],
    [2, "approved", "Refund a card"],
    [3, "in_progress", "Save a card"],
    [4, "done", "Show the total"],
    [5, "dropped", "Old idea"],
    [6, "draft", "Second draft"]
  ]),
  criterion: entriesOf("criterion", [
    [1, "not_run", "Given a card, then it is charged"],
    [2, "failed", "Given a decline, then retry"],
    [3, "passed", "Given a saved card, then it is used"],
    [4, "passed", "Given a cart, then the total shows"]
  ]),
  feature: entriesOf("feature", [
    [1, "planned", "Stripe", { layer: "core", needs: [] }],
    [2, "planned", "Mobile pay", { layer: "growth", needs: ["F1"] }],
    [3, "cut", "Legacy checkout", { layer: "core", needs: ["F1"] }],
    [4, "in_progress", "Wallet rail", { layer: "foundation", needs: ["F2"] }]
  ]),
  spec: entriesOf("spec", [
    [1, "approved", "Payments"],
    [2, "draft", "Saved cards"],
    [3, "implemented", "Totals"]
  ]),
  doc: entriesOf("doc", [
    [1, "current", "Webhook flow"],
    [2, "current", "Product brief"],
    [3, "superseded", "Old design"]
  ])
})

const mobileDeck = () => ({
  requirement: entriesOf("requirement", [[1, "draft", "Mobile login"]]),
  criterion: entriesOf("criterion", []),
  feature: entriesOf("feature", []),
  spec: entriesOf("spec", []),
  doc: entriesOf("doc", [])
})

const PRD = {
  shop: { doc: "DOC2", title: "Product brief",
    markdown: "# Brief\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\nParagraph two." },
  mobile: { doc: null, markdown: "", hint: PRD_HINT }
}

const GRAPHS = {
  shop: {
    nodes: [
      { id: 1, ref: "F1", title: "Stripe", status: "planned", layer: "core" },
      { id: 2, ref: "F2", title: "Mobile pay", status: "planned", layer: "growth" },
      { id: 3, ref: "F3", title: "Legacy checkout", status: "cut", layer: "core" },
      { id: 4, ref: "F4", title: "Wallet rail", status: "in_progress", layer: "foundation" }
    ],
    edges: [
      { from: "F1", to: "F2", optional: false },
      { from: "F1", to: "F3", optional: false },
      { from: "F2", to: "F4", optional: true }
    ],
    critical: ["F1", "F2", "F4"]
  },
  mobile: { nodes: [], edges: [], critical: [] }
}

const detailFor = (ref, kind) => ({
  ref,
  kind,
  fields: kind === "feature"
    ? { id: 2, slug: "mobile", title: "Mobile pay", layer: "growth", status: "planned",
        description: "Pay from the phone without a card reader" }
    : { id: 1, title: "Pay by card", statement: "A buyer can pay by card at checkout",
        priority: "must", phase: "mvp", status: "draft" },
  links: kind === "doc"
    ? { requirement: [1], criterion: [], feature: [], spec: [] }
    : { features: [1], specs: [1], criteria: [1, 2], criteria_total: 2, criteria_passed: 1 },
  docs: [{ ref: "DOC1", kind: "design", title: "Webhook flow",
    summary: "How Stripe webhooks are processed.", status: "current" }],
  history: [
    { at: "2026-11-20T09:00:00Z", change: "status: draft", reason: null },
    { at: "2026-11-19T09:00:00Z", change: "created", reason: "initial" }
  ],
  command: "hermes product set " + ref + " key=value"
})

// --------------------------------------------------------------------------- fake host
let serverState = null

function fakeServer(options = {}) {
  const state = {
    projects: options.projects || [
      { slug: "shop", name: "Shop", root: "/repo/shop", active: false },
      { slug: "mobile", name: "Mobile", root: null, active: true }
    ],
    remembered: options.remembered !== undefined ? options.remembered : "shop",
    calls: [],
    failProjects: options.failProjects || "",
    duplicateSlug: options.duplicateSlug || "",
    decks: { shop: shopDeck(), mobile: mobileDeck() },
    prd: { ...PRD },
    graphs: { ...GRAPHS },
    real: Boolean(options.real),
    truncate: Boolean(options.truncate)
  }
  const ctx = {
    rest: (path, opts) => {
      const method = (opts && opts.method) || "GET"
      state.calls.push({ path, method, body: opts && opts.body })
      if (path === "/projects" && method === "POST") {
        if (state.failProjects) return Promise.reject(new Error(state.failProjects))
        const body = JSON.parse(opts.body)
        if (state.duplicateSlug && state.duplicateSlug === body.slug) {
          return Promise.reject(Object.assign(
            new Error("project '" + body.slug + "' already exists"), { status: 400 }))
        }
        const created = { slug: body.slug, name: body.name, root: body.root || null }
        state.projects = state.projects.concat([{ ...created, active: false }])
        state.decks[created.slug] = mobileDeck()
        state.prd[created.slug] = { doc: null, markdown: "", hint: PRD_HINT }
        state.graphs[created.slug] = { nodes: [], edges: [], critical: [] }
        return Promise.resolve(created)
      }
      if (path === "/projects") {
        if (state.failProjects) return Promise.reject(new Error(state.failProjects))
        return Promise.resolve({ projects: state.projects })
      }
      let found = /^\/project\/([^/]+)\/prd$/.exec(path)
      if (found) {
        return Promise.resolve(state.prd[found[1]] ||
          { doc: null, markdown: "", hint: PRD_HINT })
      }
      found = /^\/project\/([^/]+)\/states$/.exec(path)
      if (found) return Promise.resolve({ ...STATES })
      found = /^\/project\/([^/]+)\/entries\?kind=([a-z]+)$/.exec(path)
      if (found) {
        const deck = state.decks[found[1]] || mobileDeck()
        return Promise.resolve(deck[found[2]] || entriesOf(found[2], []))
      }
      found = /^\/project\/([^/]+)\/entry\/([^/?]+)\/body(\?.*)?$/.exec(path)
      if (found && state.real) {
        const ref = decodeURIComponent(found[2])
        const key = state.truncate && REAL.bodies[ref + "#cut"] ? ref + "#cut" : ref
        const body = REAL.bodies[key]
        return body ? Promise.resolve(body)
          : Promise.reject(Object.assign(new Error("no such body"), { status: 404 }))
      }
      found = /^\/project\/([^/]+)\/entry\/(.+)$/.exec(path)
      if (found) {
        const ref = decodeURIComponent(found[2])
        if (state.real && REAL.entries[ref]) return Promise.resolve(REAL.entries[ref])
        const kind = ref.startsWith("A") ? "criterion" : ref.startsWith("F") ? "feature"
          : ref.startsWith("S") ? "spec" : ref.startsWith("DOC") ? "doc" : "requirement"
        return Promise.resolve(detailFor(ref, kind))
      }
      found = /^\/project\/([^/]+)\/graph$/.exec(path)
      if (found) return Promise.resolve(state.graphs[found[1]] || { nodes: [], edges: [], critical: [] })
      return Promise.reject(new Error("no route " + method + " " + path))
    },
    storage: {
      get: (key, fallback) => (key === "project" ? state.remembered : fallback),
      set: (key, value) => { if (key === "project") state.remembered = value }
    }
  }
  return { ctx, state }
}

// The page is registered once against a ctx that forwards to the live fake host,
// so every session mounts the REAL page component through the registered route.
let live = null
const liveCtx = {
  rest: (path, opts) => live.rest(path, opts),
  storage: { get: (key, fallback) => live.storage.get(key, fallback), set: (key, value) => live.storage.set(key, value) },
  register: () => () => {},
  registerMany: () => () => {}
}

const calls = () => serverState.calls
const matches = (path, method) => calls().filter((call) =>
  call.path === path && call.method === (method || "GET"))
const tabs = () => byId("status-tab")
const tabOf = (name) => tabs().find((node) => node.props["data-tab"] === name)
const cards = () => byId("card")
const cardRefs = () => cards().map((node) => node.props["data-ref"])
const cardOf = (ref) => cards().find((node) => node.props["data-ref"] === ref)
const focus = () => byId("focus-view")
const writes = () => withProps((props) => props["data-write"] === "1")

const noRenderErrors = (label) => {
  const thrown = all((node) => node.error)
  check(thrown.length === 0, label + ": a component threw (" + (thrown[0] && thrown[0].error) + ")")
}

async function open(options) {
  const server = fakeServer(options)
  serverState = server.state
  live = server.ctx
  slots.clear()
  effects.splice(0, effects.length)
  renderErrors.length = 0
  dirty = false
  mountedElement = route.render()
  renderRoot(mountedElement)
  await settle()
  noRenderErrors("mount")
  check(renderErrors.length === 0, "no effect threw during mount")
  return server
}

// --------------------------------------------------------------------------- import
register(new URL("./loader-hooks.mjs", import.meta.url))

const source = readFileSync(PLUGIN_PATH, "utf8")
const importMatches = source.match(/from\s+["'][^"']+["']/g) || []
const allowed = new Set(['from "@hermes/plugin-sdk"', 'from "react"', 'from "react/jsx-runtime"',
  "from '@hermes/plugin-sdk'", "from 'react'", "from 'react/jsx-runtime'"])
const illegal = importMatches.map((match) => match.trim()).filter((match) => !allowed.has(match))
check(illegal.length === 0, "import-scan found illegal specifiers: " + JSON.stringify(illegal))

let pluginModule
let plugin
let route
try {
  pluginModule = await import(pathToFileURL(PLUGIN_PATH).href + "?t=" + Date.now())
  plugin = pluginModule.default
} catch (err) {
  check(false, "plugin failed to import: " + (err && err.stack ? err.stack : err))
  console.log("checks: " + checks + ", failures: " + failures)
  process.exit(1)
}

check(typeof pluginModule.core === "object" && pluginModule.core !== null, "named export `core` exists")
check(plugin && plugin.id === "product", "plugin id is product")
check(plugin && plugin.name === "Product", "plugin name is Product")
check(plugin && typeof plugin.register === "function", "register is a function")

// --------------------------------------------------------------------------- registration
const contributions = []
const probe = {
  rest: () => Promise.reject(new Error("probe context has no backend")),
  storage: { get: (key, fallback) => fallback, set: () => {} },
  register: (contribution) => { contributions.push(contribution); return () => {} },
  registerMany: (list) => { contributions.push(...list); return () => {} }
}
try {
  plugin.register(probe)
  check(true, "register() completed")
} catch (err) {
  check(false, "register() threw: " + (err && err.stack ? err.stack : err))
}

const areas = contributions.map((contribution) => contribution.area)
check(areas.filter((area) => area === "routes").length === 1, "one ROUTES_AREA page contribution")
check(areas.filter((area) => area === "sidebar.nav").length === 1, "one SIDEBAR_NAV_AREA contribution")
check(areas.filter((area) => area === "palette").length === 1, "one PALETTE_AREA command")

route = contributions.find((contribution) => contribution.area === "routes")
check(route && route.data && route.data.path === "/product", "page path is /product")
const nav = contributions.find((contribution) => contribution.area === "sidebar.nav")
check(nav && nav.data && nav.data.label === "Product", "nav label is Product")
check(nav && nav.data && typeof nav.data.codicon === "string" && nav.data.codicon.length > 0,
  "nav carries a codicon")
check(nav && nav.order === 73, "nav order sits beside feature-graph's 72 (got " + (nav && nav.order) + ")")
const palette = contributions.find((contribution) => contribution.area === "palette")
check(palette && palette.data && palette.data.label === "Open Product", "palette command is Open Product")
if (palette && palette.data && typeof palette.data.run === "function") {
  hostStub.last = null
  palette.data.run()
  check(hostStub.last === "/product", "palette command navigates to /product")
}

// Re-register the page against the live fake ctx (the route above closed over `probe`).
const registered = []
plugin.register({ ...liveCtx, register: (c) => { registered.push(c); return () => {} },
  registerMany: (list) => { registered.push(...list); return () => {} } })
route = registered.find((contribution) => contribution.area === "routes")

// =========================================================================== session A
const server = await open({})

// -- A27 header band + remembered project ------------------------------------
check(one("project-switcher") !== undefined, "A27: the page header band carries the project switcher")
check(text(one("project-switcher")).includes("Shop"), "A27: the switcher names the current project")
const contribute = all((node) => node.type === "contribute")
check(contribute.some((node) => node.props.area === "workspace.pageHeader"),
  "A27: the switcher is contributed to WORKSPACE_PAGE_HEADER_AREA")
const items = byId("project-item")
check(items.length === 2, "A27: the switcher lists both projects (got " + items.length + ")")
const current = items.find((node) => node.props["data-slug"] === "shop")
check(current && current.props["data-current"] === "1",
  "A27: the remembered project (shop) is the selected one, not the server's active flag")
check(items.find((node) => node.props["data-slug"] === "mobile").props["data-current"] === "0",
  "A27: the other project is not marked current")
check(matches("/project/shop/states").length === 1, "A27: states load for the remembered project")
check(!calls().some((call) => call.path.startsWith("/project/mobile")),
  "A27: nothing is loaded for the project that was not chosen")

// -- A28 landing on the PRD ---------------------------------------------------
check(one("markdown") !== undefined, "A28: the page opens on the PRD view")
const prdText = text(one("markdown"))
check(prdText.includes("# Brief"), "A28: the PRD markdown reaches the chat markdown renderer")
check(prdText.includes("| a | b |"), "A28: tables survive to the renderer")
// The PRD must render in the content pane itself. A max-width wrapper caps paragraphs while
// wide tables still spill out of it, which reads as "text leaned left, table full width".
check(one("prd-reader") === undefined, "A28: the PRD has no width-capping reading-column wrapper")
check(tabs().length === 0, "A28: no status tabs on the PRD view")
check(matches("/project/shop/prd").length === 1, "A28: the PRD is fetched once")

// -- A29 the six focus buttons -----------------------------------------------
const focusButtons = focus()
check(focusButtons.length === 6, "A29: six focus buttons (got " + focusButtons.length + ")")
check(JSON.stringify(focusButtons.map(text)).includes("Requirements")
  && JSON.stringify(focusButtons.map(text)).includes("Acceptance")
  && JSON.stringify(focusButtons.map(text)).includes("Specs")
  && JSON.stringify(focusButtons.map(text)).includes("Docs")
  && JSON.stringify(focusButtons.map(text)).includes("PRD")
  && JSON.stringify(focusButtons.map(text)).includes("Features"),
  "A29: PRD, Requirements, Acceptance, Features, Specs, Docs are all offered")
const prdButton = focusButtons.find((node) => node.props["data-view"] === "prd")
check(prdButton && prdButton.props["aria-pressed"] === true, "A29: the active view is aria-pressed")
check(focusButtons.filter((node) => node.props["aria-pressed"] === true).length === 1,
  "A29: exactly one focus button is highlighted")

// -- A30/A31 Requirements: cards, tabs, ordering -----------------------------
await clickView("requirements")
noRenderErrors("requirements view")
check(one("markdown") === undefined, "A29: switching view replaces the content")
check(one("focus-view") && focus().find((node) => node.props["data-view"] === "requirements")
  .props["aria-pressed"] === true, "A29: Requirements is now highlighted")
check(matches("/projects").length === 1 && matches("/project/shop/states").length === 1,
  "A29: switching view reloads neither the project list nor the states")
check(markup().includes('"area":"workspace.pageHeader"') || markup().includes('"area": "workspace.pageHeader"'),
  "A27: the switcher stays in the header band across views")

check(cards().length === 6, "A30: the Requirements view draws cards (got " + cards().length + ")")
const firstCard = cards()[0]
check(text(firstCard).includes("R1") && text(firstCard).includes("Pay by card"),
  "A30: a card carries its ref and title")
check(text(firstCard).includes("draft"), "A30: a card carries its status")
check(text(firstCard).includes("summary of Pay by card"), "A30: a card carries its one-line summary")
check(text(firstCard).includes("F 1") && text(firstCard).includes("A 2"),
  "A30: a card carries the link counts that are non-zero")
check(!text(firstCard).includes("A 0"), "A30: a zero link count is not drawn")

const tabNodes = tabs()
check(tabNodes.length === 6, "A31: All plus one tab per requirement state (got " + tabNodes.length + ")")
check(text(tabOf("all")).includes("All · 6"), "A31: the All tab carries the total (got '" + text(tabOf("all")) + "')")
const perState = tabNodes.filter((node) => node.props["data-tab"] !== "all")
check(perState.length === 5, "A31: five requirement states")
check(perState.reduce((sum, node) => sum + Number(node.props["data-count"]), 0) === 6,
  "A31: the per-state counts sum to the total")
check(perState.map((node) => node.props["data-tab"]).join(",") === "draft,approved,in_progress,done,dropped",
  "A31: tabs follow the workflow order the API returned")

check(cardRefs().join(",") === "R1,R6,R2,R3,R4,R5",
  "A31: All lists cards by workflow state then id (got " + cardRefs().join(",") + ")")

await clickNode(tabOf("draft"), "draft tab")
check(cardRefs().join(",") === "R1,R6", "A31: a state tab shows only that state's cards")
check(matches("/project/shop/entries?kind=requirement").length === 1,
  "A31: tab filtering is client-side, the entries are fetched once")
await clickNode(tabOf("done"), "done tab")
check(cardRefs().join(",") === "R4", "A31: another state tab filters again")
await clickNode(tabOf("all"), "all tab")
check(cards().length === 6, "A31: All brings every card back")

// -- A30/A33 detail panel ----------------------------------------------------
await clickNode(cards()[0], "open R1")
noRenderErrors("detail panel")
check(one("detail-panel") !== undefined, "A30: clicking a card opens the detail panel")
const panelText = text(one("detail-panel"))
check(panelText.includes("Pay by card"), "A30: the detail names the entry")
check(panelText.includes("A buyer can pay by card at checkout"), "A30: the detail shows the fields")
check(panelText.includes("Webhook flow"), "A30: the detail lists the linked document")
// The API gives a requirement both id lists and two counters (store.links_of); the counters
// are numbers and used to reach values.map — the page crashed on every requirement card.
check(panelText.includes("criteria 1/2 passed"), "A30: requirement links show criteria progress")
check(panelText.includes("F 1") && panelText.includes("S 1"), "A30: link chips use reference prefixes")
check(panelText.includes("How Stripe webhooks are processed."), "A30: the linked doc shows its summary line")
check(one("detail-command") !== undefined && text(one("detail-command")).includes("hermes product set R1"),
  "A33: the detail shows the command that edits the entry")
check(one("copy-command") !== undefined, "A33: the command has a copy affordance")
check(byId("edit-form").length === 0 && one("detail-panel").props["data-editable"] !== "true",
  "A33: the detail offers no edit form")
check(matches("/project/shop/entry/R1").length === 1, "A30: the detail is fetched from the API")
await click("detail-close")
check(one("detail-panel") === undefined, "A30: the detail panel closes")

// -- A30 other card views ----------------------------------------------------
await clickView("acceptance")
check(cardRefs().join(",") === "A2,A1,A3,A4",
  "A30: criteria are failed-first (got " + cardRefs().join(",") + ")")
await clickView("specs")
check(cards().length === 3, "A30: Specs draws cards")
check(text(tabs()[0]).includes("All · 3"), "A31: the Specs total")
await clickView("docs")
check(cards().length === 3, "A30: Docs draws cards")
check(cardRefs()[0] === "DOC1" || cardRefs()[0] === "DOC2", "A30: a current doc sorts before the superseded one")
check(cardRefs()[2] === "DOC3", "A31: the superseded doc is last")

// -- A32 Features: cards by default, then the graph --------------------------
await clickView("features")
check(cards().length === 4, "A32: Features defaults to cards, not a timeline")
check(byId("mode-toggle").length === 1, "A32: Features carries the Cards|Graph toggle")
check(one("graph") === undefined, "A32: cards is the default mode")
check(text(cards().find((node) => node.props["data-ref"] === "F4")).includes("L foundation")
  || text(cards().find((node) => node.props["data-ref"] === "F4")).includes("foundation"),
  "A30: a feature card carries its layer")

await click("mode-graph")
noRenderErrors("graph view")
check(one("graph") !== undefined, "A32: Graph draws an svg")
const graphNodes = byId("graph-node")
check(graphNodes.length === 4, "A32: one node per feature (got " + graphNodes.length + ")")
check(text(graphNodes[0]).includes("F1"), "A32: a node is labelled F<id>")
const edges = byId("graph-edge")
check(edges.length === 3, "A32: one edge per dependency (got " + edges.length + ")")
const optional = edges.find((edge) => edge.props["data-optional"] === "1")
check(optional !== undefined, "A32: the optional dependency is marked")
check(optional && optional.props["data-from"] === "F2" && optional.props["data-to"] === "F4",
  "A32: the edge runs dependency -> dependent")
check(optional && typeof optional.props.strokeDasharray === "string" && optional.props.strokeDasharray.length > 0,
  "A32: an optional edge is dashed")
const critical = edges.find((edge) => edge.props["data-critical"] === "1" && edge.props["data-optional"] === "0")
check(critical !== undefined, "A32: the critical path edges are flagged")
check(critical && Number(critical.props.strokeWidth) >= 2, "A32: a critical edge is thicker")

await clickNode(byId("graph-node").find((node) => node.props["data-ref"] === "F2"), "select F2")
noRenderErrors("graph selection")
check(one("detail-panel") !== undefined, "A32: selecting a node opens the same detail panel")
check(text(one("detail-panel")).includes("hermes product set F2"), "A32: the node detail shows its command")
await click("detail-close")
check(tabs().length === 5, "A32: the tabs row stays on the graph view")
await click("mode-cards")
check(cards().length === 4 && one("graph") === undefined, "A32: the toggle goes back to cards")

// -- A32 no toggle anywhere else ---------------------------------------------
for (const view of ["prd", "requirements", "acceptance", "specs", "docs"]) {
  await clickView(view)
  check(byId("mode-toggle").length === 0, "A32: no Cards|Graph toggle on " + view)
}

// -- A27 switching project ---------------------------------------------------
await clickView("requirements")
const beforeSwitch = calls().length
await clickNode(byId("project-item").find((node) => node.props["data-slug"] === "mobile"), "pick mobile")
noRenderErrors("after project switch")
check(server.state.remembered === "mobile", "A27: the chosen project is remembered through ctx.storage")
check(calls().length > beforeSwitch, "A27: switching project reloads the data")
check(matches("/project/mobile/prd").length === 1, "A27/A28: the switch lands on the new project's PRD")
check(one("markdown") === undefined && one("empty-state") !== undefined,
  "A28: a project with no PRD shows an empty state")
check(text(one("empty-state")).includes("hermes product add DOC"),
  "A28: the empty state names the command that adds a PRD")
check(tabs().length === 0, "A28: still no status tabs after a project switch")
await clickView("requirements")
check(cardRefs().join(",") === "R1" && text(cards()[0]).includes("Mobile login"),
  "A27: every view shows the newly selected project's data")
check(calls().slice(beforeSwitch).every((call) => !call.path.startsWith("/project/shop")),
  "A27: the previous project is not queried again after the switch")

// -- A26 New project: success ------------------------------------------------
await click("new-project")
check(one("new-project-dialog") !== undefined, "A26: New project opens a dialog")
await type("slug", "  newproj ")
await type("name", "  New Project ")
await click("create-project")
const posts = calls().filter((call) => call.method === "POST")
check(posts.length === 1, "A26: one POST /projects (got " + posts.length + ")")
check(posts[0].path === "/projects", "A26: the write goes to POST /projects")
const body = JSON.parse(posts[0].body)
check(body.slug === "newproj" && body.name === "New Project",
  "A26: the payload is trimmed (got " + posts[0].body + ")")
check(body.root === undefined, "A26: an empty root is omitted from the payload")
check(one("new-project-dialog") === undefined, "A26: the dialog closes on success")
check(text(one("project-switcher")).includes("newproj")
  || text(one("project-switcher")).includes("New Project"),
  "A26: the created project is selected")
check(matches("/project/newproj/prd").length === 1, "A26: the new project's PRD view opens")

// -- A26 New project: a 400 keeps the dialog open ----------------------------
server.state.duplicateSlug = "newproj"
await click("new-project")
check(one("new-project-dialog") !== undefined, "A26: the header's New project opens the same dialog")
await type("slug", "newproj")
await type("name", "Another")
await click("create-project")
check(one("new-project-dialog") !== undefined, "A26: a rejected create keeps the dialog open")
const errorText = text(one("dialog-error"))
check(errorText.includes("already exists"), "A26: the server's message is shown (got '" + errorText + "')")
check(server.state.projects.length === 3, "A26: a rejected create adds nothing")

// -- A26 client-side slug check ----------------------------------------------
await type("slug", "Bad Slug")
await click("create-project")
check(one("new-project-dialog") !== undefined, "A26: an invalid slug is refused client-side")
check(calls().filter((call) => call.method === "POST").length === 2,
  "A26: the client-side refusal does not reach the backend")
check(text(one("dialog-error")).length > 0, "A26: the client-side refusal explains itself")
await click("dialog-cancel")

// -- A33 the only write control ----------------------------------------------
const writeNodes = writes()
check(writeNodes.length > 0, "A33: the New project control is marked as the page's write control")
check(writeNodes.every((node) => text(node).includes("New project") || text(node).includes("New Project")),
  "A33: every write control is New project")
const writeCalls = calls().filter((call) => call.method !== "GET")
check(writeCalls.every((call) => call.path === "/projects" && call.method === "POST"),
  "A33: every non-GET call in the session is POST /projects")
check(writeCalls.length === 2, "A33: two POSTs, one created a project and one was refused")
check(byId("detail-panel").every((node) => !text(node).includes("Save changes")),
  "A33: the detail panel has no save control")

// =========================================================================== session B
// -- backend down: ErrorState, never a blank page ----------------------------
await open({ failProjects: "connect ECONNREFUSED 127.0.0.1:8765" })
noRenderErrors("backend down")
check(one("error-state") !== undefined, "a rejected call renders ErrorState")
check(text(one("error-state")).includes("ECONNREFUSED"), "the error state carries the message")
check(focus().length === 6, "the page is not blank: the focus buttons still render")
check(one("project-switcher") !== undefined, "the page is not blank: the header band still renders")
check(one("loader") === undefined, "the loader is gone once the call failed")

// =========================================================================== session C
// -- remembered project gone from the server: fall back to the active one -----
await open({ remembered: "deleted" })
check(text(one("project-switcher")).includes("Mobile"),
  "A27: a forgotten project falls back to the server's active project")
check(matches("/project/mobile/prd").length === 1 && matches("/project/mobile/states").length === 1,
  "A27: the fallback loads that project's data")

// =========================================================================== session D
// -- no projects at all: an empty state that carries the New project button ----
await open({ projects: [] })
noRenderErrors("no projects")
check(one("empty-state") !== undefined && text(one("empty-state")).includes("No projects yet"),
  "an empty store shows an empty state")
const emptyButton = one("new-project-empty")
check(emptyButton !== undefined, "the empty state carries the New project button")
check(emptyButton && emptyButton.props["data-write"] === "1",
  "A33: the empty state's button is the same write control")
await click("new-project-empty")
check(one("new-project-dialog") !== undefined, "A26: and it opens the dialog")
check(writes().every((node) => text(node).includes("New project")),
  "A33: with no projects the only write control is still New project")

// =========================================================================== session E
// -- entry bodies (schema v2), rendered from the REAL API payloads -----------------
await open({ real: true })
await clickView("requirements")
await clickNode(cardOf("R1"), "open real R1")
noRenderErrors("real R1 detail")
const realR1 = text(one("detail-panel"))
check(realR1.includes("criteria 1/2 passed"),
  "real payload: a requirement's counters render as progress, not as an id list")
check(one("body-section") !== undefined && text(one("body-toggle")).includes("Show body (42 chars)"),
  "body: an entry with a body offers it with its size")
check(matches("/project/shop/entry/R1/body").length === 0 && one("body-text") === undefined,
  "body: nothing is fetched or shown until it is asked for")
await clickView("specs")
await clickNode(cardOf("S1"), "open real S1")
noRenderErrors("real S1 detail")
check(text(one("body-toggle")).includes("Show body (") && text(one("body-toggle")).includes("chars"),
  "body: S1 offers its body (size line)")
check(one("body-text") === undefined, "body: a newly opened entry starts closed")
await click("body-toggle")
noRenderErrors("body open")
check(matches("/project/shop/entry/S1/body").length === 1, "body: Show body fetches the body route once")
check(one("body-text") !== undefined && text(one("body-text")).includes("Verify the signature"),
  "body: the text reaches the markdown renderer")
check(one("body-toggle") === undefined && one("body-truncated") === undefined,
  "body: the toggle is replaced by the text and a short body is not marked truncated")
// switch away WITHOUT hiding: the open body must not follow the selection
await clickNode(cardOf("S2"), "open real S2 (no body)")
check(one("body-section") === undefined, "body: an entry without a body shows no body section")
await clickNode(cardOf("S1"), "reopen real S1")
check(one("body-text") === undefined && one("body-toggle") !== undefined,
  "body: switching entries closes the previous body (it is read only when asked for)")
await click("body-toggle")
await click("body-hide")
check(one("body-text") === undefined && one("body-toggle") !== undefined, "body: Hide returns to the closed state")

await open({ real: true, truncate: true })
await clickView("specs")
await clickNode(cardOf("S1"), "open real S1 (long)")
await click("body-toggle")
check(one("body-truncated") !== undefined
  && text(one("body-truncated")).includes("hermes product show S1 --body --max-chars 100000"),
  "body: a truncated read says how many characters it hid and how to read the rest")

// -- A34 the document reader ---------------------------------------------------
// A DOC is read, not inspected: metadata rail on the left, the markdown on the right,
// loaded on open. Everything else keeps the narrow panel and its Show body toggle.
await open({ real: true })
await clickView("docs")
await clickNode(cardOf("DOC1"), "open real DOC1")
noRenderErrors("real DOC1 detail")
check(one("detail-reader") !== undefined, "doc: opening a document shows the two-pane reader")
check(one("detail-meta") !== undefined && one("body-pane") !== undefined,
  "doc: the reader has a metadata rail and a body pane")
check(matches("/project/shop/entry/DOC1/body").length === 1,
  "doc: the body is fetched when the document opens, exactly once")
check(one("body-text") !== undefined && text(one("body-text")).length > 0,
  "doc: the body is on screen without pressing anything")
check(one("body-toggle") === undefined && one("body-hide") === undefined,
  "doc: the reader carries no Show/Hide body controls")
check(one("body-section") === undefined, "doc: the metadata rail does not repeat the body section")
await clickView("specs")
await clickNode(cardOf("S1"), "open real S1 after a doc")
check(one("detail-reader") === undefined && one("body-toggle") !== undefined,
  "doc: a spec still opens the narrow panel with its Show body toggle")

// --------------------------------------------------------------------------- result
console.log("checks: " + checks + ", failures: " + failures)
if (failures > 0) {
  console.error("FAIL")
  process.exit(1)
}
console.log("PASS")
