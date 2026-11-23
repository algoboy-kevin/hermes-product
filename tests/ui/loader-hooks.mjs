/**
 * Node module-customization hooks for the `product` desktop page tests.
 *
 * Same pattern as kanban-gantt's tests/ui/loader-hooks.mjs: map the three
 * specifiers a desktop plugin may import ("@hermes/plugin-sdk", "react",
 * "react/jsx-runtime") onto local stubs, so the REAL desktop/plugin.js can be
 * imported by Node with no browser, no bundler and no node_modules.
 *
 * One difference, forced by the card's write roots (four test files, nothing
 * else): the stubs are GENERATED here instead of written to a .stubs/ directory.
 * `load()` returns their source, and the generated modules read
 * globalThis.__productUIKit / globalThis.__productReact at EVALUATION time —
 * which is after the test has installed them and before plugin.js runs.
 *
 * Usage, at the top of a test file (before any import of plugin.js):
 *
 *   import { register } from "node:module"
 *   register(new URL("./loader-hooks.mjs", import.meta.url))
 *   const { core } = await import("../../desktop/plugin.js")
 *
 * With no kit installed the stubs are inert: components are plain tag names and
 * hooks are stateless no-ops, which is all the pure-core tests need.
 */

const STUB_URLS = {
  "@hermes/plugin-sdk": "product-stub:sdk",
  react: "product-stub:react",
  "react/jsx-runtime": "product-stub:jsx-runtime"
}

export function resolve(specifier, context, nextResolve) {
  if (STUB_URLS[specifier]) {
    return { url: STUB_URLS[specifier], shortCircuit: true }
  }
  return nextResolve(specifier, context)
}

// Every name here is exported by the real SDK; esm-render.mjs fails loudly if
// plugin.js imports one this stub does not carry (the import would throw).
const SDK_SOURCE = [
  'const K = globalThis.__productUIKit || {}',
  'const tag = (name) => (K[name] !== undefined ? K[name] : name)',
  'export const Badge = tag("Badge")',
  'export const Button = tag("Button")',
  'export const Codicon = tag("Codicon")',
  'export const Contribute = tag("Contribute")',
  'export const Dialog = tag("Dialog")',
  'export const DialogContent = tag("DialogContent")',
  'export const DialogTitle = tag("DialogTitle")',
  'export const DropdownMenu = tag("DropdownMenu")',
  'export const DropdownMenuContent = tag("DropdownMenuContent")',
  'export const DropdownMenuItem = tag("DropdownMenuItem")',
  'export const DropdownMenuSeparator = tag("DropdownMenuSeparator")',
  'export const DropdownMenuTrigger = tag("DropdownMenuTrigger")',
  'export const EmptyState = tag("EmptyState")',
  'export const ErrorState = tag("ErrorState")',
  'export const Input = tag("Input")',
  'export const Loader = tag("Loader")',
  'export const Popover = tag("Popover")',
  'export const PopoverContent = tag("PopoverContent")',
  'export const PopoverTrigger = tag("PopoverTrigger")',
  'export const Streamdown = tag("Streamdown")',
  'export const Switch = tag("Switch")',
  'export const ROUTES_AREA = "routes"',
  'export const SIDEBAR_NAV_AREA = "sidebar.nav"',
  'export const PALETTE_AREA = "palette"',
  'export const WORKSPACE_PAGE_HEADER_AREA = "workspace.pageHeader"',
  'export const cn = (...parts) => (K.cn ? K.cn(...parts) : parts.filter(Boolean).join(" "))',
  'export const host = K.host || { navigate: () => {} }',
  'export default { Button, Codicon, Contribute, Dialog, DialogContent, DialogTitle,',
  '  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator,',
  '  DropdownMenuTrigger, EmptyState, ErrorState, Loader, Streamdown, cn, host }',
  ''
].join('\n')

const REACT_SOURCE = [
  'const R = globalThis.__productReact || {}',
  'const fallbackCreate = (type, props, ...children) => {',
  '  const merged = { ...(props || {}) }',
  '  const kids = children.length ? children : (merged.children !== undefined ? [].concat(merged.children) : [])',
  '  if (kids.length) merged.children = kids',
  '  return { $$el: true, type, props: merged }',
  '}',
  'export const createElement = R.createElement || fallbackCreate',
  'export const useState = R.useState || ((initial) => [',
  '  typeof initial === "function" ? initial() : initial, () => {}])',
  'export const useEffect = R.useEffect || (() => {})',
  'export const useMemo = R.useMemo || ((fn) => (typeof fn === "function" ? fn() : fn))',
  'export const useRef = R.useRef || ((initial) => ({ current: initial }))',
  'export const useCallback = R.useCallback || ((fn) => fn)',
  'export const useReducer = R.useReducer || ((reducer, initial) => [',
  '  typeof initial === "function" ? initial() : initial, () => {}])',
  'export default { createElement, useState, useEffect, useMemo, useRef, useCallback, useReducer }',
  ''
].join('\n')

// plugin.js is written with `createElement as h`, never JSX, but the specifier
// has to resolve all the same — the import scan in the asset test allows it.
const JSX_SOURCE = [
  'const R = globalThis.__productReact || {}',
  'export const jsx = R.jsx || ((type, props) => (R.createElement || ((t, p) => ({ $$el: true, type: t, props: p || {} })))(type, props))',
  'export const jsxs = jsx',
  'export const Fragment = "Fragment"',
  ''
].join('\n')

export async function load(url, context, nextLoad) {
  if (url === STUB_URLS["@hermes/plugin-sdk"]) {
    return { format: "module", source: SDK_SOURCE, shortCircuit: true }
  }
  if (url === STUB_URLS.react) {
    return { format: "module", source: REACT_SOURCE, shortCircuit: true }
  }
  if (url === STUB_URLS["react/jsx-runtime"]) {
    return { format: "module", source: JSX_SOURCE, shortCircuit: true }
  }
  return nextLoad(url, context)
}
