/*
 * product — desktop page (plain ESM, loaded uncompiled by Hermes Desktop).
 *
 * One page for all product knowledge: the project switcher (header band, like the
 * kanban board switcher), a New project dialog, six focus views that open on the
 * rendered PRD, cards grouped and filterable by workflow state, and — on Features
 * only — a Cards | Graph toggle drawn from the dependency graph.
 *
 * Everything comes from the read-only backend at /api/plugins/product
 * (dashboard/plugin_api.py): the page never writes an entry. The ONE write
 * control is New project (A33); every detail panel shows the CLI command that
 * edits the entry instead of an edit form.
 *
 * Shape:
 *   [ project v  + New project ]                     WORKSPACE_PAGE_HEADER_AREA
 *   [ PRD | Requirements | Acceptance | Features | Specs | Docs ]
 *   [ All . n | draft . n | ... ]        [ Cards | Graph ]   (toggle: Features only)
 *   <content>                                             | detail panel
 *
 * Colours and geometry that the app's precompiled Tailwind sheet cannot know
 * about are INLINE styles; class strings are the ones feature-graph already
 * renders correctly in the desktop app.
 */

import { createElement as h, useEffect, useMemo, useState } from "react"
import {
  Button,
  Codicon,
  Contribute,
  Dialog,
  DialogContent,
  DialogTitle,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  EmptyState,
  ErrorState,
  Loader,
  MessageTextContent,
  PALETTE_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  WORKSPACE_PAGE_HEADER_AREA,
  cn,
  host
} from "@hermes/plugin-sdk"

const ROUTE = "/product"
const STORAGE_KEY = "project"
const SLUG_RULE = "^[a-z0-9][a-z0-9-]{0,39}$"

// ===== core (pure, exported for tests) =====
/* No React, no SDK, no I/O in this block: it is the half of the page that can be
 * pinned without a browser (tests/ui/core.test.mjs). */

/**
 * The five focus views, Docs first. There is no PRD view: the PRD is a document (its row
 * carries `is_prd`), so it lives in the Docs list with a badge instead of owning a tab.
 */
const VIEWS = ["docs", "requirements", "acceptance", "features", "specs"]
//: the card kinds, in VIEWS order
const ENTRY_KINDS = ["doc", "requirement", "criterion", "feature", "spec"]

const VIEW_LABEL = {
  docs: "Docs",
  requirements: "Requirements",
  acceptance: "Acceptance",
  features: "Features",
  specs: "Specs"
}

const VIEW_KIND = {
  docs: "doc",
  requirements: "requirement",
  acceptance: "criterion",
  features: "feature",
  specs: "spec"
}

/** Fallback workflow order, identical to GET /project/{slug}/states (DOC2 behaviour 5). */
const STATE_ORDER = {
  requirement: ["draft", "approved", "in_progress", "done", "dropped"],
  criterion: ["failed", "not_run", "passed", "waived"],
  feature: ["planned", "in_progress", "shipped", "cut"],
  spec: ["draft", "approved", "implemented", "verified", "superseded"],
  doc: ["current", "superseded"]
}

const STATE_COLOR = {
  draft: "var(--ui-text-tertiary, #8b949e)",
  approved: "#58a6ff",
  in_progress: "var(--ui-warning, #d29922)",
  done: "var(--ui-success, #3fb950)",
  dropped: "var(--ui-text-quaternary, #6e7681)",
  planned: "var(--ui-text-tertiary, #8b949e)",
  shipped: "var(--ui-success, #3fb950)",
  cut: "var(--ui-text-quaternary, #6e7681)",
  implemented: "#58a6ff",
  verified: "var(--ui-success, #3fb950)",
  superseded: "var(--ui-text-quaternary, #6e7681)",
  failed: "#f85149",
  not_run: "var(--ui-text-tertiary, #8b949e)",
  passed: "var(--ui-success, #3fb950)",
  waived: "#a371f7",
  current: "var(--ui-success, #3fb950)"
}

const LINK_BADGE = {
  requirement: [["features", "F"], ["specs", "S"], ["criteria", "A"]],
  feature: [["needs", "N"], ["requirements", "R"], ["specs", "S"]],
  spec: [["requirements", "R"], ["features", "F"]],
  doc: [["requirement", "R"], ["criterion", "A"], ["feature", "F"], ["spec", "S"]]
}

const REF_PREFIX = { requirement: "R", criterion: "A", feature: "F", spec: "S", doc: "DOC" }

const initialState = { project: null, view: "docs", tab: "all", featureMode: "cards", selected: null }

/** A card view's state tone; anything unknown falls back to the tertiary text colour. */
function statusColor(status) {
  return STATE_COLOR[status] || "var(--ui-text-tertiary, #8b949e)"
}

/** "in_progress" reads as "in progress" in a tab or a chip. */
function stateLabel(status) {
  return String(status || "").replace(/_/g, " ")
}

function titled(status) {
  const words = stateLabel(status)
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : ""
}

const idNumber = (value) => {
  const number = Number(value)
  return Number.isFinite(number) ? number : Number.MAX_SAFE_INTEGER
}

/** The digits at the end of a ref ("F12" -> 12), for a stable left-to-right order. */
function refNumber(ref) {
  const found = /(\d+)\s*$/.exec(String(ref || ""))
  return found ? Number(found[1]) : Number.MAX_SAFE_INTEGER
}

/**
 * Workflow state order, then id ascending; unknown states last (A31). The All tab
 * uses this, and a state tab is a filter over the same order.
 */
function sortEntries(entries, order) {
  const rank = new Map((order || []).map((state, index) => [state, index]))
  const unknown = rank.size
  // PRD first, then pinned, then the workflow order: the document a reader wants at hand
  // lands top-left instead of wherever its id happens to fall. Other kinds carry neither flag,
  // so every entry ranks 2 and their order is untouched.
  // PRD first (pinned or not), then pinned, then everything else.
  const flagRank = (entry) => (!entry ? 2 : entry.is_prd ? 0 : entry.pinned ? 1 : 2)
  return (entries || []).slice().sort((a, b) => {
    const flags = flagRank(a) - flagRank(b)
    if (flags !== 0) return flags
    const left = rank.has(a.status) ? rank.get(a.status) : unknown
    const right = rank.has(b.status) ? rank.get(b.status) : unknown
    if (left !== right) return left - right
    return idNumber(a.id) - idNumber(b.id)
  })
}

/**
 * {all, byState} for the tab row: every state of the kind is present with a zero,
 * and the per-state counts sum to the total (A31). A state the order does not
 * know still counts, so the sum cannot silently drift.
 */
function tabCounts(entries, order) {
  const byState = {}
  for (const state of order || []) byState[state] = 0
  let all = 0
  for (const entry of entries || []) {
    all += 1
    const state = entry.status || "unknown"
    byState[state] = (byState[state] || 0) + 1
  }
  return { all, byState }
}

/** The client-side state filter: "all" is every card, a state is that state only (A31). */
function filterByState(entries, state) {
  const rows = entries || []
  if (!state || state === "all") return rows.slice()
  return rows.filter((entry) => entry.status === state)
}

/** A feature's layer, a requirement's priority: the badges a card footer shows. */
function linkBadges(kind, links) {
  const source = links || {}
  return (LINK_BADGE[kind] || [])
    .map(([key, label]) => [label, Number(source[key] || 0)])
    .filter(([, count]) => count > 0)
}

/**
 * The page's navigation state (A27-A29, A32): switching project lands on the PRD
 * with a clean slate; switching view resets the tab and the selection but keeps
 * the Cards|Graph choice; that choice is ignored anywhere but Features.
 */
function navReducer(state, action) {
  switch (action && action.type) {
    case "selectProject":
      return { project: action.slug, view: "docs", tab: "all", featureMode: "cards", selected: null }
    case "selectView":
      return { ...state, view: action.view, tab: "all", selected: null }
    case "selectTab":
      return { ...state, tab: action.tab }
    case "setFeatureMode":
      return state.view === "features" ? { ...state, featureMode: action.mode } : state
    case "select":
      return { ...state, selected: action.ref }
    case "clearSelection":
      return { ...state, selected: null }
    default:
      return state
  }
}

/** Status tabs on every view (A28). With no PRD view left, every view is a card list. */
function showStatusTabs(view) {
  return Boolean(VIEW_KIND[view])
}

/** The Cards|Graph toggle exists on Features only (A32). */
function showModeToggle(view) {
  return view === "features"
}

/** The server's slug rule, applied before the dialog writes anything (A26). */
function validProjectSlug(slug) {
  return typeof slug === "string" && new RegExp(SLUG_RULE).test(slug)
}

/** The POST /projects body: trimmed, with an empty root left out (A26). */
function newProjectPayload(slug, name, root) {
  const payload = { slug: String(slug || "").trim(), name: String(name || "").trim() }
  const rootText = String(root || "").trim()
  if (rootText) payload.root = rootText
  return payload
}

/** The server's message, whatever shape the host's rest() rejected with. */
function errorMessage(error) {
  if (!error) return "The backend rejected that request."
  if (typeof error === "string") return error
  if (error.body && typeof error.body === "object" && error.body.error) return String(error.body.error)
  if (typeof error.message === "string" && error.message.trim()) return error.message
  if (error.error) return String(error.error)
  return String(error)
}

// Layout geometry: fixed rectangles on a left-to-right layer grid (A32 graph).
const NODE_W = 190
const NODE_H = 44
const GAP_X = 72
const GAP_Y = 14
const PAD = 16

/**
 * Deterministic layered layout for the feature graph.
 *
 * A node's layer is 1 + the deepest layer among its dependencies, so `from` is
 * always left of `to` (the API's edges run dependency -> dependent). Nodes in a
 * layer are ordered by id, which keeps two runs byte-identical; a cycle cannot
 * recurse forever (a back edge contributes nothing). `critical` flags the edges
 * that are consecutive in the critical path list.
 */
function layoutGraph(nodes, edges, critical) {
  const list = (nodes || []).map((node) => ({ ...node, ref: node.ref || String(node.id) }))
  const known = new Set(list.map((node) => node.ref))
  const deps = new Map(list.map((node) => [node.ref, []]))
  const drawn = []
  for (const edge of edges || []) {
    if (!known.has(edge.from) || !known.has(edge.to)) continue
    deps.get(edge.to).push(edge.from)
    drawn.push(edge)
  }

  const layerOf = new Map()
  const visiting = new Set()
  const layerFor = (ref) => {
    if (layerOf.has(ref)) return layerOf.get(ref)
    if (visiting.has(ref)) return 0
    visiting.add(ref)
    let deepest = 0
    for (const dep of deps.get(ref) || []) deepest = Math.max(deepest, layerFor(dep) + 1)
    visiting.delete(ref)
    layerOf.set(ref, deepest)
    return deepest
  }
  for (const node of list) layerFor(node.ref)

  const columns = []
  for (const node of list) {
    const layer = layerOf.get(node.ref) || 0
    if (!columns[layer]) columns[layer] = []
    columns[layer].push(node)
  }
  const columnCount = Math.max(1, columns.length)
  const rowCount = Math.max(1, ...columns.map((column) => (column ? column.length : 0)))
  const width = PAD * 2 + columnCount * NODE_W + (columnCount - 1) * GAP_X
  const height = PAD * 2 + rowCount * NODE_H + (rowCount - 1) * GAP_Y

  const placed = []
  const position = new Map()
  columns.forEach((column, layer) => {
    (column || []).slice()
      .sort((a, b) => (idNumber(a.id) - idNumber(b.id)) || (refNumber(a.ref) - refNumber(b.ref)))
      .forEach((node, row) => {
        const rect = {
          ref: node.ref,
          x: PAD + layer * (NODE_W + GAP_X),
          y: PAD + row * (NODE_H + GAP_Y),
          w: NODE_W,
          h: NODE_H,
          layer
        }
        placed.push(rect)
        position.set(node.ref, rect)
      })
  })
  placed.sort((a, b) => (a.layer - b.layer) || (a.y - b.y))

  const criticalPairs = new Set()
  const path = critical || []
  for (let index = 0; index + 1 < path.length; index += 1) {
    criticalPairs.add(path[index] + "->" + path[index + 1])
  }

  const lines = drawn.map((edge) => {
    const from = position.get(edge.from)
    const to = position.get(edge.to)
    const startX = from.x + from.w
    const startY = from.y + from.h / 2
    const endX = to.x
    const endY = to.y + to.h / 2
    const midX = startX + (endX - startX) / 2
    return {
      from: edge.from,
      to: edge.to,
      optional: Boolean(edge.optional),
      critical: criticalPairs.has(edge.from + "->" + edge.to),
      points: [[startX, startY], [midX, startY], [midX, endY], [endX, endY]]
    }
  })

  return { nodes: placed, edges: lines, width, height }
}

/** "6.9 KB" / "812 chars": how much text sits behind the Show body button. */
const bodySizeLabel = (chars) => {
  const n = Number(chars) || 0
  return n >= 1024 ? (n / 1024).toFixed(1) + " KB" : n + " chars"
}

/** The API route for one entry's body (read-only, bounded by the server). */
const bodyPath = (project, ref, options = {}) => {
  const query = []
  if (options.section) query.push("section=" + encodeURIComponent(options.section))
  if (options.maxChars) query.push("max_chars=" + encodeURIComponent(options.maxChars))
  return "/project/" + project + "/entry/" + encodeURIComponent(ref) + "/body"
    + (query.length ? "?" + query.join("&") : "")
}

export const core = {
  VIEWS,
  VIEW_LABEL,
  VIEW_KIND,
  STATE_ORDER,
  STATE_COLOR,
  initialState,
  statusColor,
  stateLabel,
  titled,
  sortEntries,
  tabCounts,
  filterByState,
  linkBadges,
  navReducer,
  showStatusTabs,
  showModeToggle,
  validProjectSlug,
  newProjectPayload,
  errorMessage,
  bodySizeLabel,
  bodyPath,
  layoutGraph,
  NODE_W,
  NODE_H
}

// ===== components =====

const chip = (active) =>
  cn(
    "flex min-w-0 items-center justify-center gap-1 rounded-[3px] px-2.5 py-0.5 text-[0.6875rem]",
    "font-medium whitespace-nowrap transition-colors cursor-pointer border-0",
    active ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
  )

const TRACK = "inline-flex items-center gap-0.5 rounded-[5px] bg-(--ui-bg-tertiary) p-0.5"
/** The focus strip: flat labels on the page, spaced, nothing to click into. */
const FOCUS_BAR = { display: "flex", alignItems: "baseline", gap: 20, flexWrap: "wrap" }
const FIELD_LABEL = "mb-1 block text-[10px] font-semibold uppercase tracking-wide text-(--ui-text-tertiary)"
const FIELD_INPUT =
  "w-full rounded-md border border-(--ui-border) bg-transparent px-2 py-1.5 text-xs " +
  "focus:outline-none focus:border-(--ui-accent)"

const UI = {
  text: "var(--ui-text-secondary, var(--foreground))",
  tertiary: "var(--ui-text-tertiary, var(--muted-foreground))",
  quaternary: "var(--ui-text-quaternary, var(--muted-foreground))",
  stroke: "var(--ui-stroke-tertiary, var(--border))",
  chip: "var(--ui-bg-quaternary, var(--card))",
  accent: "var(--ui-accent, #58a6ff)"
}

const trackStyle = { background: "var(--ui-bg-tertiary)" }
const sectionLabel = {
  fontSize: 10,
  fontWeight: 600,
  letterSpacing: "0.14em",
  textTransform: "uppercase",
  color: UI.quaternary,
  marginBottom: 4
}
const mutedText = { fontSize: 11.5, color: UI.quaternary }
const mono = { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }

/**
 * The six focus views (A29). Deliberately NOT the state-tab chip: the capabilities strip is
 * bare labels on the page background, the active one bright with an underline. No boxes, no
 * track — the state tabs below keep that vocabulary so the two rows read as different things.
 */
function FocusBar({ value, onChange, counts }) {
  const tally = counts || {}
  return h("span", { role: "group", "aria-label": "views", style: FOCUS_BAR },
    VIEWS.map((view) => {
      const active = value === view
      const count = tally[view] || 0
      return h("button", {
        key: view,
        type: "button",
        "data-testid": "focus-view",
        "data-view": view,
        "aria-pressed": active,
        onClick: () => onChange(view),
        className: cn(
          "flex shrink-0 cursor-pointer items-baseline gap-1.5 border-0 bg-transparent px-0 pb-2 text-[0.8125rem]",
          "leading-none transition-colors",
          active ? "text-foreground" : "text-muted-foreground hover:text-foreground"
        )
      },
        h("span", {
          className: "whitespace-nowrap",
          style: {
            fontWeight: active ? 600 : 500,
            paddingBottom: 5,
            borderBottom: "2px solid " + (active ? UI.quaternary : "transparent")
          }
        }, VIEW_LABEL[view]),
        count > 0
          ? h("span", {
            // the capabilities-tab shape: a quiet tally beside the label, gone when empty
            "data-testid": "focus-count",
            "data-view": view,
            "data-count": String(count),
            style: { fontSize: 10.5, color: UI.quaternary, fontVariantNumeric: "tabular-nums" }
          }, String(count))
          : null)
    }))
}

/** "All . n" plus one tab per workflow state, with the API's counts (A31). */
function StatusTabs({ counts, order, tab, onPick }) {
  const total = counts && Number.isFinite(Number(counts.all)) ? Number(counts.all) : 0
  const allTab = h("button", {
    key: "all",
    type: "button",
    "data-testid": "status-tab",
    "data-tab": "all",
    "data-count": total,
    "aria-pressed": tab === "all",
    onClick: () => onPick("all"),
    className: chip(tab === "all")
  }, h("span", { className: "whitespace-nowrap" }, "All \u00b7 " + total))

  const stateTabs = (order || []).map((state) => {
    const count = Number((counts && counts[state]) || 0)
    return h("button", {
      key: state,
      type: "button",
      "data-testid": "status-tab",
      "data-tab": state,
      "data-count": count,
      "aria-pressed": tab === state,
      onClick: () => onPick(state),
      title: stateLabel(state),
      className: chip(tab === state)
    }, h("span", { className: "whitespace-nowrap" }, titled(state) + " \u00b7 " + count))
  })

  return h("span", { className: TRACK, role: "group", "aria-label": "states", style: trackStyle },
    [allTab].concat(stateTabs))
}

/** Cards | Graph, on Features only (A32). */
function ModeToggle({ value, onChange }) {
  return h("span", {
    "data-testid": "mode-toggle",
    className: TRACK,
    role: "group",
    "aria-label": "features view",
    style: trackStyle
  },
    [["cards", "Cards"], ["graph", "Graph"]].map(([mode, label]) =>
      h("button", {
        key: mode,
        type: "button",
        "data-testid": "mode-" + mode,
        "data-mode": mode,
        "aria-pressed": value === mode,
        onClick: () => onChange(mode),
        className: chip(value === mode)
      }, h("span", { className: "flex items-center gap-1" }, label))))
}

/** The project switcher, projected into the page header band (A27). */
function ProjectSwitcher({ projects, slug, onPick, onNew }) {
  const current = (projects || []).find((project) => project.slug === slug)
  return h(Contribute, { area: WORKSPACE_PAGE_HEADER_AREA, id: "product:project-switcher" },
    h("div", { className: "flex items-center gap-1" },
      h(DropdownMenu, null,
        h(DropdownMenuTrigger, { asChild: true },
          h(Button, {
            size: "xs",
            variant: "ghost",
            className: "gap-1.5 text-xs",
            "data-testid": "project-switcher"
          },
            h(Codicon, { name: "checklist", size: "0.85rem" }),
            h("span", { className: "font-semibold" }, "Project"),
            h("span", { className: "text-muted-foreground" },
              current ? current.name : projects.length ? projects.length + " projects" : "none"),
            h(Codicon, { name: "chevron-down", size: "0.8rem" }))),

        h(DropdownMenuContent, { align: "start", className: "min-w-[16rem] p-1" },
          h("div", { className: "px-2 py-1 text-[10px] font-semibold uppercase text-(--ui-text-tertiary)" },
            "projects"),
          projects.length === 0
            ? h("div", { className: "px-2 py-1 text-xs text-muted-foreground" }, "none yet")
            : projects.map((project) =>
              h(DropdownMenuItem, {
                key: project.slug,
                "data-testid": "project-item",
                "data-slug": project.slug,
                "data-current": project.slug === slug ? "1" : "0",
                onClick: () => onPick(project.slug),
                className: cn("flex items-center gap-2 cursor-pointer text-xs py-1.5",
                  project.slug === slug && "font-semibold")
              },
                h(Codicon, { name: project.slug === slug ? "check" : "folder", size: "0.8rem" }),
                h("span", { className: "flex-1 truncate" }, project.name),
                project.active
                  ? h("span", { className: "text-[10px] text-(--ui-text-quaternary)" }, "active")
                  : null)),

          h(DropdownMenuSeparator, null),
          h(DropdownMenuItem, {
            "data-testid": "new-project-menu",
            "data-write": "1",
            onClick: onNew,
            className: "flex items-center gap-2 cursor-pointer text-xs py-1.5"
          },
            h(Codicon, { name: "add", size: "0.8rem" }), "New project\u2026"))),

      // the band's own button, as DOC2 draws it: [ project v  + New project ]
      h(Button, {
        size: "xs",
        variant: "ghost",
        "data-testid": "new-project",
        "data-write": "1",
        onClick: onNew,
        className: "gap-1 text-xs"
      },
        h(Codicon, { name: "add", size: "0.8rem" }),
        h("span", { className: "whitespace-nowrap" }, "New project"))))
}

/** The one write control's dialog: slug, name, optional root (A26). */
function NewProjectDialog({ open, onClose, onCreate }) {
  const [slug, setSlug] = useState("")
  const [name, setName] = useState("")
  const [root, setRoot] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    setSlug("")
    setName("")
    setRoot("")
    setError("")
    setBusy(false)
  }, [open])

  const submit = () => {
    if (busy) return
    const payload = newProjectPayload(slug, name, root)
    if (!payload.name) {
      setError("A name is required.")
      return
    }
    if (!payload.slug) {
      setError("A slug is required.")
      return
    }
    if (!validProjectSlug(payload.slug)) {
      setError("The slug must match " + SLUG_RULE + ": lowercase letters, digits and dashes.")
      return
    }
    setError("")
    setBusy(true)
    Promise.resolve(onCreate(payload))
      .then(() => { setBusy(false) })
      .catch((failure) => {
        setBusy(false)
        setError(errorMessage(failure))
      })
  }

  const field = (key, label, placeholder, value, setValue, autoFocus) =>
    h("div", { key },
      h("label", { className: FIELD_LABEL }, label),
      h("input", {
        className: FIELD_INPUT,
        "data-testid": key,
        value,
        autoFocus: Boolean(autoFocus),
        placeholder,
        onChange: (event) => setValue(event.target.value),
        onKeyDown: (event) => { if (event.key === "Enter") submit() }
      }))

  return h(Dialog, {
    open,
    "data-testid": "new-project-dialog",
    onOpenChange: (next) => { if (!next) onClose() }
  },
    h(DialogContent, { className: "max-w-md", "data-testid": "new-project-dialog" },
      h(DialogTitle, { className: "text-sm font-semibold" }, "New project"),
      h("div", { className: "mt-3 flex flex-col gap-3 text-xs" },
        field("slug", "Slug", "shop", slug, setSlug, true),
        h("p", { className: "mt-1 text-[11px] text-(--ui-text-quaternary)" },
          "Lowercase letters, digits and dashes: " + SLUG_RULE),
        field("name", "Name", "Shop", name, setName, false),
        field("root", "Repo root (optional)", "~/Documents/Github/shop", root, setRoot, false),
        error ? h("p", { "data-testid": "dialog-error", className: "text-xs text-destructive" }, error) : null),
      h("div", { className: "mt-4 flex justify-end gap-2" },
        h(Button, { size: "sm", variant: "ghost", "data-testid": "dialog-cancel", onClick: onClose },
          "Cancel"),
        h(Button, { size: "sm", variant: "default", "data-testid": "create-project", disabled: busy,
          onClick: submit }, busy ? "Creating…" : "Create project"))))
}

/** A Hermes kanban-shaped card: ref + status chip, title, summary, link badges (A30). */
function Card({ entry, kind, onSelect, onTogglePin }) {
  const tone = statusColor(entry.status)
  const badges = linkBadges(kind, entry.links)
  const [hover, setHover] = useState(false)
  const lift = entry.pinned && hover
  return h("button", {
    type: "button",
    "data-testid": "card",
    "data-ref": entry.ref,
    "data-status": entry.status || "",
    onClick: () => onSelect(entry.ref),
    className: cn(
      "group relative flex w-full cursor-pointer flex-col gap-1.5 rounded-md border",
      "border-(--ui-stroke-tertiary) border-l-2 bg-(--ui-bg-elevated) p-2.5 text-left",
      "transition-colors hover:bg-primary/[0.06]"
    ),
    // A pinned card lifts a little on hover; the transition makes the pin feel held,
    // not stamped. Inline because the shipped stylesheet has no arbitrary utilities.
    style: {
      borderLeftColor: tone,
      transform: lift ? "translateY(-1px)" : "none",
      boxShadow: lift ? "0 3px 12px rgba(0,0,0,0.22)" : "none",
      transition: "transform 140ms ease, box-shadow 140ms ease, background-color 140ms ease"
    },
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false)
  },
    // Pin control: hidden until the card is hovered, always visible once pinned, and a
    // click toggles it without opening the card.
    h("span", {
      role: "button",
      tabIndex: 0,
      "data-testid": entry.pinned ? "card-pin" : "card-pin-off",
      "aria-label": entry.pinned ? "Unpin" : "Pin to top",
      title: entry.pinned ? "Unpin" : "Pin to top",
      onClick: (event) => { event.stopPropagation(); if (onTogglePin) onTogglePin(entry, kind) },
      className: cn(
        "absolute top-1 right-1.5 grid place-items-center rounded p-0.5 transition-opacity",
        entry.pinned ? "opacity-100" : "opacity-0 group-hover:opacity-100"
      ),
      style: { color: entry.pinned ? UI.tertiary : UI.quaternary, cursor: "pointer", lineHeight: 0 }
    }, h(Codicon, { name: "pin", size: "0.75rem" })),

    h("div", { className: "flex items-center gap-2" },
      h("span", { className: "font-mono text-[10px] text-(--ui-text-quaternary)" }, entry.ref),
      h("span", {
        className: "inline-flex items-center gap-1 rounded-full bg-(--ui-bg-quaternary) px-1.5 py-px text-[0.625rem] font-medium",
        style: { color: tone }
      },
        h("span", { style: { width: 6, height: 6, borderRadius: 999, background: tone, display: "inline-block" } }),
        stateLabel(entry.status)),
      entry.is_prd
        ? h("span", {
          // which document IS the PRD, now that the PRD has no tab of its own
          "data-testid": "card-prd",
          // same pill as the state chip beside it — same radius, padding and size; only the
          // colour and the label differ, so the row reads as one row of facts
          className: "inline-flex items-center gap-1 rounded-full bg-(--ui-bg-quaternary) px-1.5 py-px text-[0.625rem] font-medium",
          style: { color: "#eab308" }
        }, "PRD")
        : null),

    h("div", { className: "line-clamp-2 text-xs font-semibold leading-snug" }, entry.title || entry.ref),

    entry.summary
      ? h("div", { className: "line-clamp-1 text-[11px] leading-snug text-(--ui-text-tertiary)" }, entry.summary)
      : null,

    badges.length || (kind === "feature" && entry.layer)
      ? h("div", { className: "mt-0.5 flex flex-wrap items-center gap-1 text-[0.625rem] text-(--ui-text-tertiary)" },
        badges.map(([label, count]) =>
          h("span", { key: label, className: "rounded-full bg-(--ui-bg-quaternary) px-1.5 py-px tabular-nums" },
            label + " " + count)),
        kind === "feature" && entry.layer
          ? h("span", { key: "layer", className: "ml-auto rounded-full bg-(--ui-bg-quaternary) px-1.5 py-px" },
            "L " + entry.layer)
          : null)
      : null)
}

const polyline = (points) => (points || []).map(([x, y], index) =>
  (index === 0 ? "M " : "L ") + x + " " + y).join(" ")

/** The dependency graph: layered, optional edges dashed, critical path accented (A32). */
function GraphView({ graph, onSelect }) {
  const nodes = graph && graph.nodes ? graph.nodes : []
  const edges = graph && graph.edges ? graph.edges : []
  const layout = useMemo(() => layoutGraph(nodes, edges, (graph && graph.critical) || []),
    [nodes, edges, graph])

  const meta = new Map(nodes.map((node) => [node.ref, node]))

  return h("div", { className: "flex flex-col gap-2" },
    h("svg", {
      "data-testid": "graph",
      width: "100%",
      height: layout.height,
      viewBox: "0 0 " + layout.width + " " + layout.height,
      preserveAspectRatio: "xMinYMin meet",
      style: { maxWidth: layout.width, height: "auto" }
    },
      h("defs", null,
        h("marker", {
          id: "product-arrow",
          viewBox: "0 0 8 8",
          refX: 7,
          refY: 4,
          markerWidth: 6,
          markerHeight: 6,
          orient: "auto"
        }, h("path", { d: "M 0 0 L 8 4 L 0 8 z", fill: "currentColor" }))),

      layout.edges.map((edge) =>
        h("path", {
          key: edge.from + "->" + edge.to,
          d: polyline(edge.points),
          fill: "none",
          stroke: edge.critical ? UI.accent : "var(--ui-border, #444)",
          strokeWidth: edge.critical ? 2.4 : 1,
          strokeDasharray: edge.optional ? "5 3" : undefined,
          opacity: edge.optional ? 0.65 : 0.95,
          markerEnd: "url(#product-arrow)",
          "data-testid": "graph-edge",
          "data-from": edge.from,
          "data-to": edge.to,
          "data-optional": edge.optional ? "1" : "0",
          "data-critical": edge.critical ? "1" : "0"
        })),

      layout.nodes.map((node) => {
        const item = meta.get(node.ref) || {}
        const tone = statusColor(item.status)
        const label = (node.ref + " " + (item.title || "")).slice(0, 30)
        return h("g", {
          key: node.ref,
          transform: "translate(" + node.x + "," + node.y + ")",
          "data-testid": "graph-node",
          "data-ref": node.ref,
          onClick: () => onSelect(node.ref),
          style: { cursor: "pointer" }
        },
          h("rect", {
            width: node.w,
            height: node.h,
            rx: 6,
            fill: "var(--ui-bg-secondary, #1a1a1a)",
            stroke: tone,
            strokeWidth: 1.5
          }),
          h("text", { x: 9, y: 18, fill: "currentColor", fontSize: 11, fontWeight: 600 }, label),
          h("text", { x: 9, y: 33, fill: tone, fontSize: 9.5 },
            stateLabel(item.status) + (node.layer !== undefined ? " \u00b7 layer " + node.layer : "")))
      })),

    h("div", { className: "flex flex-wrap items-center gap-3 text-[10px] text-(--ui-text-quaternary)" },
      h("span", { className: "inline-flex items-center gap-1" },
        h("span", { style: { width: 16, borderTop: "2px solid " + UI.accent, display: "inline-block" } }),
        "critical path"),
      h("span", { className: "inline-flex items-center gap-1" },
        h("span", { style: { width: 16, borderTop: "1px dashed var(--ui-border)", display: "inline-block" } }),
        "optional dependency"),
      h("span", null, layout.nodes.length + " features, " + layout.edges.length + " dependencies")))
}

const fieldValue = (value) => {
  if (Array.isArray(value)) return value.join(", ")
  if (value === true) return "yes"
  if (value === false) return "no"
  return String(value)
}

/** Prefix per link key: the API uses plural keys and a few singular ones. */
const LINK_PREFIX = {
  features: "F", needs: "F", specs: "S", criteria: "A", requirements: "R",
  feature: "F", spec: "S", criterion: "A", requirement: "R", doc: "DOC"
}

const formatLinks = (links) => {
  const out = []
  if (!links || typeof links !== "object") return out
  // A requirement's links carry two counters next to the id lists (store.links_of);
  // they are numbers, not arrays, and must never reach values.map.
  const total = typeof links.criteria_total === "number" ? links.criteria_total : null
  const passed = typeof links.criteria_passed === "number" ? links.criteria_passed : null
  for (const [kind, values] of Object.entries(links)) {
    if (kind === "criteria_total" || kind === "criteria_passed") continue
    const list = Array.isArray(values) ? values : values == null ? [] : [values]
    const prefix = LINK_PREFIX[kind] || REF_PREFIX[kind] || kind
    const refs = list.map((value) => String(value))
    if (refs.length) out.push([prefix, refs.join(" ")])
  }
  if (total !== null && total > 0) {
    out.push(["criteria", (passed || 0) + "/" + total + " passed"])
  }
  return out
}

/** The docked detail panel: fields, links, doc summaries, history, the edit command (A33). */
function metaSection(label, content) {
  return h("div", { style: { marginBottom: 14 } },
    h("div", { style: sectionLabel }, label),
    content)
}

/**
 * The metadata column shared by the side panel and the document modal: title, fields, links,
 * linked documents, history and the CLI that edits the entry.
 */
function EntryMeta({ detail, command, copy, copied }) {
  const fields = detail && detail.fields ? Object.entries(detail.fields) : []
  const links = formatLinks(detail && detail.links)
  const docs = (detail && detail.docs) || []
  const history = (detail && detail.history) || []
  const section = metaSection

  return h("div", { style: { display: "flex", flexDirection: "column", gap: 2, minWidth: 0 } },

        h("h3", { style: { fontSize: 14, fontWeight: 600, lineHeight: 1.35, marginBottom: 10 } },
          (detail.fields && (detail.fields.title || detail.fields.statement)) || detail.ref),

        fields.length
          ? section("Fields", h("div", { style: { display: "flex", flexDirection: "column", gap: 4 } },
            fields.map(([key, value]) =>
              h("div", { key, style: { display: "grid", gridTemplateColumns: "104px minmax(0, 1fr)", gap: 8 } },
                h("span", { style: { ...sectionLabel, marginBottom: 0 } }, stateLabel(key)),
                h("span", { style: { fontSize: 12, color: UI.text, wordBreak: "break-word" } },
                  fieldValue(value))))))
          : null,

        links.length
          ? section("Links", h("div", { style: { display: "flex", flexWrap: "wrap", gap: 4 } },
            links.map(([label, refs]) =>
              h("span", {
                key: label,
                style: {
                  display: "inline-flex", alignItems: "center", gap: 4, borderRadius: 999,
                  background: UI.chip, padding: "1px 8px", fontSize: 10, color: UI.tertiary
                }
              }, label + " " + refs))))
          : null,

        section("Documents", docs.length
          ? h("div", { style: { display: "flex", flexDirection: "column", gap: 4 } },
            docs.map((doc) =>
              h("div", { key: doc.ref },
                h("div", { style: { fontSize: 12, color: UI.text } }, doc.ref + " \u00b7 " + doc.title),
                h("div", { style: { ...mutedText, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" } },
                  doc.summary || ""))))
          : h("div", { style: mutedText }, "No linked documents.")),

        section("History", history.length
          ? h("div", { style: { display: "flex", flexDirection: "column", gap: 3 } },
            history.map((row, index) =>
              h("div", { key: String(row.at) + index, style: { display: "flex", gap: 8, fontSize: 11 } },
                h("span", { style: { ...mono, color: UI.quaternary, whiteSpace: "nowrap" } }, String(row.at || "")),
                h("span", { style: { color: UI.text } }, String(row.change || "")),
                row.reason ? h("span", { style: mutedText }, "\u2014 " + row.reason) : null)))
          : h("div", { style: mutedText }, "No history yet.")),

        h("div", null,
          h("div", { style: sectionLabel }, "Edit"),
          h("div", {
            style: {
              display: "flex", alignItems: "center", gap: 8, border: "1px solid " + UI.stroke,
              borderRadius: 6, padding: "6px 8px", background: "var(--ui-bg-tertiary)"
            }
          },
            h("code", { "data-testid": "detail-command", style: { ...mono, fontSize: 11, flex: 1, wordBreak: "break-all" } },
              command),
            h("button", {
              type: "button",
              "data-testid": "copy-command",
              "aria-label": "copy command",
              title: "Copy command",
              onClick: copy,
              style: {
                display: "grid", placeItems: "center", width: 22, height: 22, border: 0,
                borderRadius: 4, background: "transparent", color: UI.tertiary, cursor: "pointer"
              }
            }, h(Codicon, { name: copied ? "check" : "copy", size: "0.8rem" }))),
          h("p", { style: { ...mutedText, marginTop: 6 } },
            "Read-only here by design: change the entry from the CLI above."))
  )
}

function DetailPanel({ detail, loading, error, onClose, loadBody }) {
  const [copied, setCopied] = useState(false)
  const command = (detail && detail.command) || ""
  const [body, setBody] = useState({ state: "idle", text: "", truncated: false, total: 0, command: "", error: "" })
  const ref = (detail && detail.ref) || ""
  // a new entry starts with its body closed: the text is read only when asked for
  useEffect(() => {
    setBody({ state: "idle", text: "", truncated: false, total: 0, command: "", error: "" })
  }, [ref])
  const showBody = () => {
    if (!loadBody || !ref) return
    setBody((previous) => ({ ...previous, state: "loading", error: "" }))
    Promise.resolve(loadBody(ref))
      .then((payload) => setBody({
        state: "ready",
        text: (payload && payload.text) || "",
        truncated: Boolean(payload && payload.truncated),
        total: (payload && payload.total) || 0,
        command: (payload && payload.command) || "",
        error: ""
      }))
      .catch((failure) => setBody({
        state: "error", text: "", truncated: false, total: 0, command: "", error: errorMessage(failure)
      }))
  }
  const hideBody = () =>
    setBody({ state: "idle", text: "", truncated: false, total: 0, command: "", error: "" })

  const copy = () => {
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard) {
        navigator.clipboard.writeText(command)
      }
      setCopied(true)
    } catch (failure) {
      setCopied(false)
    }
  }


  const metaColumn = detail ? h(EntryMeta, { detail, command, copy, copied }) : null

  const bodySection = detail && detail.body_chars > 0
    ? metaSection("Body", h("div", { "data-testid": "body-section", style: { display: "flex", flexDirection: "column", gap: 6 } },
            body.state === "idle"
              ? h("button", {
                type: "button",
                "data-testid": "body-toggle",
                onClick: showBody,
                style: {
                  alignSelf: "flex-start", border: "1px solid " + UI.stroke, borderRadius: 6,
                  background: "transparent", color: UI.text, fontSize: 12, padding: "4px 10px", cursor: "pointer"
                }
              }, "Show body (" + bodySizeLabel(detail.body_chars) + ")")
              : null,
            body.state === "loading" ? h(Loader, null) : null,
            body.state === "error"
              ? h(ErrorState, { title: "The body could not be loaded", message: body.error, description: body.error })
              : null,
            body.state === "ready"
              ? h("div", { style: { display: "flex", flexDirection: "column", gap: 6 } },
                h("div", {
                  "data-testid": "body-text",
                  style: {
                    maxHeight: 420, overflow: "auto", border: "1px solid " + UI.stroke,
                    borderRadius: 6, padding: "6px 10px", fontSize: 12
                  }
                }, h(MessageTextContent, { text: body.text })),
                body.truncated
                  ? h("p", { "data-testid": "body-truncated", style: mutedText },
                    "Showing the first " + body.text.length + " of " + body.total
                    + " characters. Read the rest with: " + body.command)
                  : null,
                h("button", {
                  type: "button",
                  "data-testid": "body-hide",
                  onClick: hideBody,
                  style: {
                    alignSelf: "flex-start", border: 0, background: "transparent", color: UI.tertiary,
                    fontSize: 11, padding: 0, cursor: "pointer"
                  }
                }, "Hide body"))
              : null))
          : null

  return h("aside", {
    "data-testid": "detail-panel",
    "aria-label": "entry detail",
    style: {
      width: 380,
      minWidth: 320,
      maxWidth: "42vw",
      flexShrink: 0,
      overflow: "auto",
      borderLeft: "1px solid " + UI.stroke,
      paddingLeft: 14,
      display: "flex",
      flexDirection: "column",
      gap: 4
    }
  },
    h("div", { style: { display: "flex", alignItems: "center", gap: 8, marginBottom: 10 } },
      h("span", { style: { ...mono, fontSize: 11, color: UI.quaternary } }, (detail && detail.ref) || "\u2026"),
      h("span", { style: { ...mono, fontSize: 10, color: UI.quaternary } }, (detail && detail.kind) || ""),
      h("button", {
        type: "button",
        "data-testid": "detail-close",
        "aria-label": "close detail",
        onClick: onClose,
        style: {
          marginLeft: "auto", display: "grid", placeItems: "center", width: 24, height: 24,
          borderRadius: 4, border: 0, background: "transparent", color: UI.tertiary, cursor: "pointer"
        }
      }, h(Codicon, { name: "close", size: "0.85rem" }))),

    error ? h(ErrorState, { title: "The entry could not be loaded", message: error, description: error }) : null,
    loading && !detail ? h(Loader, null) : null,

    metaColumn
      ? h("div", { style: { display: "flex", flexDirection: "column", gap: 2 } }, metaColumn, bodySection)
      : null)
}

/**
 * The document reader, in the shape the kanban card modal uses: a centred dialog with the
 * document on the left and its metadata rail on the right. The body loads when the dialog
 * opens — a document is there to be read, not to be asked twice.
 */
function DocModal({ detail, loading, error, onClose, loadBody }) {
  const [copied, setCopied] = useState(false)
  const command = (detail && detail.command) || ""
  const ref = (detail && detail.ref) || ""
  const [body, setBody] = useState({ state: "idle", text: "", truncated: false, total: 0, command: "", error: "" })

  useEffect(() => {
    setBody({ state: "idle", text: "", truncated: false, total: 0, command: "", error: "" })
  }, [ref])

  const read = () => {
    if (!loadBody || !ref) return
    setBody((previous) => ({ ...previous, state: "loading", error: "" }))
    Promise.resolve(loadBody(ref))
      .then((payload) => setBody({
        state: "ready",
        text: (payload && payload.text) || "",
        truncated: Boolean(payload && payload.truncated),
        total: (payload && payload.total) || 0,
        command: (payload && payload.command) || "",
        error: ""
      }))
      .catch((failure) => setBody({
        state: "error", text: "", truncated: false, total: 0, command: "", error: errorMessage(failure)
      }))
  }

  // the body is the reason the dialog exists: fetch it as soon as the entry is known
  useEffect(() => {
    if (ref && body.state === "idle") read()
  }, [ref, body.state])

  useEffect(() => {
    if (typeof window === "undefined" || !window.addEventListener) return undefined
    const onKey = (event) => { if (event.key === "Escape") onClose() }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  const copy = () => {
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard) navigator.clipboard.writeText(command)
      setCopied(true)
    } catch (failure) {
      setCopied(false)
    }
  }

  const title = (detail && detail.fields && (detail.fields.title || detail.fields.statement)) || ref || "Document"

  return h("div", {
    "data-testid": "doc-modal-backdrop",
    onClick: (event) => { if (event.target === event.currentTarget) onClose() },
    style: {
      position: "fixed", inset: 0, zIndex: 60, background: "rgba(0, 0, 0, 0.45)",
      display: "grid", placeItems: "center", padding: 24
    }
  },
    h("div", {
      "data-testid": "doc-modal",
      role: "dialog",
      "aria-modal": "true",
      "aria-label": "document",
      style: {
        width: "min(1180px, 92vw)", height: "min(780px, 88vh)", display: "flex", flexDirection: "column",
        background: "var(--ui-bg-elevated)", border: "1px solid " + UI.stroke, borderRadius: 10,
        boxShadow: "0 24px 60px rgba(0, 0, 0, 0.35)", overflow: "hidden"
      }
    },
      h("div", {
        style: {
          display: "flex", alignItems: "center", gap: 8, padding: "10px 14px",
          borderBottom: "1px solid " + UI.stroke
        }
      },
        h("span", { style: { ...mono, fontSize: 11, color: UI.quaternary } }, ref || "\u2026"),
        h("span", { style: { ...mono, fontSize: 10, color: UI.quaternary } }, (detail && detail.kind) || ""),
        h("button", {
          type: "button",
          "data-testid": "doc-modal-close",
          "aria-label": "close document",
          onClick: onClose,
          style: {
            marginLeft: "auto", display: "grid", placeItems: "center", width: 26, height: 26,
            borderRadius: 4, border: 0, background: "transparent", color: UI.tertiary, cursor: "pointer"
          }
        }, h(Codicon, { name: "close", size: "0.9rem" }))),

      h("div", { style: { display: "flex", minHeight: 0, flex: 1 } },
        h("div", {
          "data-testid": "doc-modal-body",
          style: { flex: 1, minWidth: 0, overflow: "auto", padding: "22px 30px 36px" }
        },
          loading && !detail ? h(Loader, null) : null,
          error ? h(ErrorState, { title: "The document could not be loaded", message: error, description: error }) : null,
          detail ? h("h2", { style: { fontSize: 22, fontWeight: 650, lineHeight: 1.25, margin: "0 0 14px" } }, title) : null,
          body.state === "loading" || body.state === "idle" ? h(Loader, null) : null,
          body.state === "error"
            ? h("div", null,
              h(ErrorState, { title: "The body could not be loaded", message: body.error, description: body.error }),
              // A 404 here usually means the desktop backend predates this page: the page is
              // read from disk on every load, the plugin's Python API only at startup.
              h("p", { style: { ...mutedText, marginTop: 10 } },
                "If this page was just updated, quit and reopen Hermes — the plugin API is read "
                + "at startup, so a long-running backend can be older than the page."))
            : null,
          body.state === "ready"
            ? h("div", { style: { display: "flex", flexDirection: "column", gap: 10 } },
              h("div", { "data-testid": "body-text" }, h(MessageTextContent, { text: body.text })),
              body.truncated
                ? h("p", { "data-testid": "body-truncated", style: mutedText },
                  "Showing the first " + body.text.length + " of " + body.total
                  + " characters. Read the rest with: " + body.command)
                : null)
            : null),

        detail
          ? h("aside", {
            "data-testid": "doc-modal-meta",
            "aria-label": "document metadata",
            style: {
              width: 300, flexShrink: 0, borderLeft: "1px solid " + UI.stroke,
              overflow: "auto", padding: "16px 14px"
            }
          }, h(EntryMeta, { detail, command, copy, copied }))
          : null)))
}



/** The page: loads the project list, then the view the nav state asks for. */
function ProductPage({ ctx }) {
  const [nav, setNav] = useState(initialState)
  const [projects, setProjects] = useState([])
  const [projectsLoading, setProjectsLoading] = useState(true)
  const [bootError, setBootError] = useState("")
  const [states, setStates] = useState(null)
  const [decks, setDecks] = useState(null)
  const [entriesLoading, setEntriesLoading] = useState(false)
  const [entriesError, setEntriesError] = useState("")
  const [graph, setGraph] = useState(null)
  const [graphLoading, setGraphLoading] = useState(false)
  const [graphError, setGraphError] = useState("")
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState("")
  const [dialogOpen, setDialogOpen] = useState(false)

  const project = nav.project || ""
  const kind = VIEW_KIND[nav.view]

  // the project list, once; then the remembered / active / first project (A27)
  useEffect(() => {
    let alive = true
    setProjectsLoading(true)
    Promise.resolve(ctx.rest("/projects"))
      .then((payload) => {
        if (!alive) return
        const list = (payload && payload.projects) || []
        setBootError("")
        setProjects(list)
        setNav((previous) => {
          if (list.some((entry) => entry.slug === previous.project)) return previous
          const remembered = (ctx.storage && ctx.storage.get(STORAGE_KEY, "")) || ""
          const chosen = list.find((entry) => entry.slug === remembered)
            || list.find((entry) => entry.active)
            || list[0]
          return chosen ? navReducer(previous, { type: "selectProject", slug: chosen.slug }) : previous
        })
      })
      .catch((failure) => {
        if (!alive) return
        setProjects([])
        setBootError(errorMessage(failure))
      })
      .finally(() => { if (alive) setProjectsLoading(false) })
    return () => { alive = false }
  }, [ctx])

  // the workflow order for this project (DOC2 behaviour 5)
  useEffect(() => {
    if (!project) return undefined
    let alive = true
    setStates(null)
    Promise.resolve(ctx.rest("/project/" + project + "/states"))
      .then((payload) => { if (alive) setStates(payload || {}) })
      .catch(() => { if (alive) setStates(null) })
    return () => { alive = false }
  }, [ctx, project])

  // One entries call per kind, per project, with NO status filter: tabs filter client-side
  // (A31), and the same deck feeds the focus bar's tallies, so switching views costs nothing.
  useEffect(() => {
    if (!project) return undefined
    let alive = true
    setDecks(null)
    setEntriesError("")
    setEntriesLoading(true)
    Promise.all(ENTRY_KINDS.map((each) =>
      Promise.resolve(ctx.rest("/project/" + project + "/entries?kind=" + each))
        .then((payload) => [each, payload || null])))
      .then((pairs) => {
        if (!alive) return
        const byKind = {}
        pairs.forEach((pair) => { byKind[pair[0]] = pair[1] })
        setDecks({ project, byKind })
      })
      .catch((failure) => { if (alive) setEntriesError(errorMessage(failure)) })
      .finally(() => { if (alive) setEntriesLoading(false) })
    return () => { alive = false }
  }, [ctx, project])

  // the graph, only when the Features view asks for it (A32)
  useEffect(() => {
    if (!project || nav.view !== "features" || nav.featureMode !== "graph") return undefined
    let alive = true
    setGraph(null)
    setGraphError("")
    setGraphLoading(true)
    Promise.resolve(ctx.rest("/project/" + project + "/graph"))
      .then((payload) => { if (alive) setGraph({ project, payload: payload || { nodes: [], edges: [], critical: [] } }) })
      .catch((failure) => { if (alive) setGraphError(errorMessage(failure)) })
      .finally(() => { if (alive) setGraphLoading(false) })
    return () => { alive = false }
  }, [ctx, project, nav.view, nav.featureMode])

  // the detail panel's entry (A30/A33)
  useEffect(() => {
    if (!project || !nav.selected) return undefined
    let alive = true
    setDetail(null)
    setDetailError("")
    setDetailLoading(true)
    Promise.resolve(ctx.rest("/project/" + project + "/entry/" + encodeURIComponent(nav.selected)))
      .then((payload) => { if (alive) setDetail({ project, ref: nav.selected, payload: payload || null }) })
      .catch((failure) => { if (alive) setDetailError(errorMessage(failure)) })
      .finally(() => { if (alive) setDetailLoading(false) })
    return () => { alive = false }
  }, [ctx, project, nav.selected])

  const pickProject = (slug) => {
    if (!slug) return
    if (ctx.storage) ctx.storage.set(STORAGE_KEY, slug)
    setNav((previous) => navReducer(previous, { type: "selectProject", slug }))
  }
  const openView = (view) => setNav((previous) => navReducer(previous, { type: "selectView", view }))
  const openTab = (tab) => setNav((previous) => navReducer(previous, { type: "selectTab", tab }))
  const setFeatureMode = (mode) =>
    setNav((previous) => navReducer(previous, { type: "setFeatureMode", mode }))
  const selectEntry = (ref) => setNav((previous) => navReducer(previous, { type: "select", ref }))
  const closeDetail = () => setNav((previous) => navReducer(previous, { type: "clearSelection" }))
  const openDialog = () => setDialogOpen(true)
  const closeDialog = () => setDialogOpen(false)

  // the ONE write: POST /projects, then select what the server created (A26/A33)
  const createProject = (payload) =>
    Promise.resolve(ctx.rest("/projects", { method: "POST", body: JSON.stringify(payload) }))
      .then((created) => {
        const row = {
          slug: created.slug,
          name: created.name,
          root: created.root || null,
          active: false
        }
        setProjects((previous) =>
          (previous.some((entry) => entry.slug === row.slug) ? previous : previous.concat([row])))
        pickProject(row.slug)
        setDialogOpen(false)
        return created
      })

  // A payload is only shown for the project (and, for entries, the kind) it was
  // loaded for: switching project or view must not paint the previous data even
  // for a frame while the next request is in flight.
  const entryView = decks && decks.project === project && kind ? decks.byKind[kind] : null
  const graphOne = graph && graph.project === project ? graph.payload : null
  const detailOne = detail && detail.project === project && detail.ref === nav.selected
    ? detail.payload
    : null
  // A document is read in a dialog (the kanban card-modal shape); while its detail is in
  // flight we already know the answer from the view that opened it.
  const docOpen = Boolean(nav.selected && ((detailOne && detailOne.kind === "doc")
    || (detailLoading && nav.view === "docs")))

  // The focus bar's tallies: every view counts its cards.
  const deckOf = (each) => {
    const payload = decks && decks.project === project ? decks.byKind[each] : null
    return payload && payload.counts ? payload.counts.all : 0
  }
  const viewCounts = {
    requirements: deckOf("requirement"),
    acceptance: deckOf("criterion"),
    features: deckOf("feature"),
    specs: deckOf("spec"),
    docs: deckOf("doc")
  }

  const order = (states && states[kind]) || STATE_ORDER[kind] || []
  const entries = entryView && entryView.entries ? sortEntries(entryView.entries, order) : []
  const visible = filterByState(entries, nav.tab)
  const derived = tabCounts(entries, order)
  const counts = (entryView && entryView.counts) || { all: derived.all, ...derived.byState }

  const content = () => {
    if (bootError) {
      return h(ErrorState, {
        title: "Backend not reachable",
        message: bootError,
        description: bootError
      })
    }
    if (!project) {
      if (projectsLoading) return h(Loader, null)
      return h("div", { className: "flex h-full flex-col items-center justify-center gap-3" },
        h(EmptyState, {
          title: "No projects yet",
          description: "Create a project to hold requirements, criteria, features, specs and documents."
        }),
        h(Button, {
          size: "sm",
          variant: "default",
          "data-testid": "new-project-empty",
          "data-write": "1",
          onClick: openDialog
        }, h("span", { className: "flex items-center gap-1" },
          h(Codicon, { name: "add", size: "0.8rem" }), "New project")))
    }
    if (nav.view === "features" && nav.featureMode === "graph") {
      if (graphLoading && !graphOne) return h(Loader, null)
      if (graphError) {
        return h(ErrorState, { title: "The graph could not be loaded", message: graphError, description: graphError })
      }
      if (!graphOne || !(graphOne.nodes || []).length) {
        return h(EmptyState, { title: "No features yet", description: "Add a feature to draw its dependency graph." })
      }
      return h(GraphView, { graph: graphOne, onSelect: selectEntry })
    }
    if (entriesError) {
      return h(ErrorState, { title: "These entries could not be loaded", message: entriesError, description: entriesError })
    }
    if (entriesLoading && !entryView) return h(Loader, null)
    if (!entryView) return h(Loader, null)
    if (!visible.length) {
      return h(EmptyState, {
        title: nav.tab === "all" ? "Nothing here yet" : "Nothing in " + stateLabel(nav.tab),
        description: nav.tab === "all"
          ? "Add one with hermes product add."
          : "No entry of this kind is in that state."
      })
    }
    return h("div", {
      style: {
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))",
        gap: 8,
        alignContent: "start"
      }
    }, visible.map((entry) => h(Card, { key: entry.ref, entry, kind, onSelect: selectEntry, onTogglePin: togglePin })))
  }

  // The page's only write. Optimistic: flip the cached flag so the sort and the icon move
  // together, then send it; a failure puts the error on the page and the next load wins.
  const togglePin = (entry, kindOf) => {
    if (!project) return
    const next = !entry.pinned
    setDecks((prev) => {
      if (!prev) return prev
      const deck = prev.byKind[kindOf]
      if (!deck || !deck.entries) return prev
      return {
        project: prev.project,
        byKind: {
          ...prev.byKind,
          [kindOf]: {
            ...deck,
            entries: deck.entries.map((each) => (each.ref === entry.ref ? { ...each, pinned: next } : each))
          }
        }
      }
    })
    Promise.resolve(ctx.rest(
      "/project/" + project + "/entry/" + encodeURIComponent(entry.ref) + "/pin?pinned=" + (next ? 1 : 0),
      { method: "POST" }
    )).catch((failure) => { setEntriesError(errorMessage(failure)) })
  }

  return h("div", { className: "relative flex h-full min-h-0 flex-col" },
    h(ProjectSwitcher, { projects, slug: project, onPick: pickProject, onNew: openDialog }),

    // pt-4 (not the symmetric py-2): the focus strip needs air under the app's header band,
    // where the project switcher is contributed — it read as part of that row.
    h("div", { className: "flex min-h-0 flex-1 flex-col gap-2 px-3 pb-2 pt-4" },
      h("div", { className: "flex flex-wrap items-center justify-between gap-2" },
        h(FocusBar, { value: nav.view, onChange: openView, counts: viewCounts }),
        h("div", { className: "flex items-center gap-2" },
          showModeToggle(nav.view)
            ? h(ModeToggle, { value: nav.featureMode, onChange: setFeatureMode })
            : null)),

      showStatusTabs(nav.view) && project
        ? h("div", { className: "flex flex-wrap items-center gap-2" },
          h(StatusTabs, { counts, order, tab: nav.tab, onPick: openTab }))
        : null,

      h("div", { className: "flex min-h-0 flex-1 gap-3" },
        h("div", { className: "flex-1 min-w-0 overflow-auto" }, content()),
        nav.selected && !docOpen
          ? h(DetailPanel, {
            detail: detailOne,
            loading: detailLoading,
            error: detailError,
            onClose: closeDetail,
            loadBody: (ref) => ctx.rest(bodyPath(project, ref))
          })
          : null)),

    docOpen
      ? h(DocModal, {
        detail: detailOne,
        loading: detailLoading,
        error: detailError,
        onClose: closeDetail,
        loadBody: (ref) => ctx.rest(bodyPath(project, ref))
      })
      : null,

    h(NewProjectDialog, { open: dialogOpen, onClose: closeDialog, onCreate: createProject }))
}

const plugin = {
  id: "product",
  name: "Product",
  description: "Product knowledge for a project — requirements, criteria, features, specs and documents as cards, the PRD as markdown, and the feature graph.",
  register(ctx) {
    ctx.registerMany([
      {
        id: "page",
        area: ROUTES_AREA,
        data: { path: ROUTE },
        render: () => h(ProductPage, { ctx })
      },
      {
        id: "nav",
        area: SIDEBAR_NAV_AREA,
        order: 73,
        data: { path: ROUTE, label: "Product", codicon: "checklist" }
      },
      {
        id: "open",
        area: PALETTE_AREA,
        data: {
          id: "product.open",
          label: "Open Product",
          keywords: ["product", "prd", "requirements", "acceptance", "specs", "features"],
          run: () => host.navigate(ROUTE)
        }
      }
    ])
  }
}

export default plugin
