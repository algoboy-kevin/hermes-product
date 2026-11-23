.PHONY: help test doctor link unlink sync enable disable validate clean

PLUGIN_DIR  := product
DEV_LINK    := $(HOME)/.hermes/plugins/$(PLUGIN_DIR)
DESKTOP_DIR := $(HOME)/.hermes/desktop-plugins/$(PLUGIN_DIR)
REPO        := $(shell pwd)

help:
	@echo "make test      run the test suite (pytest; uv fallback pulls ruamel/pytest)"
	@echo "make doctor    validate manifest + register(ctx) via hermes plugins doctor"
	@echo "make validate  catalog admission checks (hermes plugins validate)"
	@echo "make sync      copy this repo into ~/.hermes/plugins/product  <-- use this"
	@echo "make link      symlink instead of copying (CLI/tools only: the desktop scanner"
	@echo "               skips symlinked plugin dirs, so the sidebar page will NOT appear)"
	@echo "make unlink    remove that symlink"
	@echo "make enable    enable the plugin for the active profile"
	@echo "make disable   disable it again"

test:
	@if python3 -c "import pytest, ruamel.yaml, fastapi, httpx" 2>/dev/null; then python3 -m pytest tests -q; \
	else env -u VIRTUAL_ENV -u PYTHONPATH uv run --no-project --with pytest \
		--with "ruamel.yaml>=0.18,<0.19" --with fastapi --with httpx \
		--python 3.12 python -m pytest tests -q; fi

doctor:
	hermes plugins doctor $(REPO)

validate:
	hermes plugins validate $(REPO)

# Copy, not symlink. `apps/desktop/electron/desktop-plugins-root.ts` enumerates the
# plugins root with `readdir(withFileTypes)` and keeps only `entry.isDirectory()` —
# a symlinked plugin dir reports isDirectory()=false and is invisible to the app, so
# the desktop half is never materialized and no sidebar row appears. The Python
# loader follows symlinks, which is why the CLI and tools worked while the page did
# not. Use `make link` only when you do not need the desktop surface.
# The product docs (docs/) must NOT ride along: the plugin copy is code only.
sync:
	@mkdir -p $(DEV_LINK)
	rsync -a --delete --exclude 'docs' --exclude '.git' --exclude '__pycache__' \
		--exclude '.pytest_cache' --exclude 'node_modules' $(REPO)/ $(DEV_LINK)/
	@echo "synced $(REPO) -> $(DEV_LINK) (restart or rescan the desktop app to materialize $(DESKTOP_DIR))"

link:
	@test ! -e $(DEV_LINK) || { echo "$(DEV_LINK) already exists (run make unlink)"; exit 1; }
	ln -s $(REPO) $(DEV_LINK)
	@echo "linked $(DEV_LINK) -> $(REPO)"
	@echo "WARNING: symlinks are invisible to the desktop plugin scanner; the sidebar page will not load."

unlink:
	@if [ -L "$(DEV_LINK)" ]; then rm $(DEV_LINK); echo "unlinked $(DEV_LINK)"; \
	else echo "no symlink at $(DEV_LINK)"; fi

enable:
	hermes plugins enable $(PLUGIN_DIR)

disable:
	hermes plugins disable $(PLUGIN_DIR)

clean:
	rm -rf .pytest_cache __pycache__ tests/__pycache__
