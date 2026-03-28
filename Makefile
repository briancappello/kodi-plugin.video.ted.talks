## TED Talks video addon for Kodi

ADDON_XML  := addon.xml
ADDON_ID   := $(shell grep '<addon ' $(ADDON_XML) | sed 's/.*id="\([^"]*\)".*/\1/')
VERSION    := $(shell grep '<addon ' $(ADDON_XML) | sed 's/.*version="\([^"]*\)".*/\1/')
DB_PATH    := ted_catalog.db

.DEFAULT_GOAL := help

DIST_DIR   := dist
ZIP_FILE   := $(ADDON_ID)-$(VERSION).zip

.PHONY: help test test-all sync zip clean

test: ## Run unit tests (offline)
	python3 -m unittest resources.lib.model.db_test -v
	python3 -m unittest resources.lib.model.talk_page_test -v

test-all: ## Run all tests including live API/website tests
	python3 -m unittest discover -s resources -p "*_test.py" -v

sync: ## Build/refresh the catalog database
	python3 sync_catalog.py $(DB_PATH)

zip: ## Package addon for distribution
	@rm -rf $(DIST_DIR)/$(ADDON_ID)
	@mkdir -p $(DIST_DIR)/$(ADDON_ID)
	@rsync -a --exclude-from=.distignore ./ $(DIST_DIR)/$(ADDON_ID)/
	@find $(DIST_DIR)/$(ADDON_ID) -name "*_test.py" -delete
	@find $(DIST_DIR)/$(ADDON_ID) -name "test_*.py" -delete
	@find $(DIST_DIR)/$(ADDON_ID) -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@find $(DIST_DIR)/$(ADDON_ID) -name "*.pyc" -delete
	@if [ -f "$(DB_PATH)" ]; then \
		mkdir -p $(DIST_DIR)/$(ADDON_ID)/resources/data; \
		cp $(DB_PATH) $(DIST_DIR)/$(ADDON_ID)/resources/data/$(DB_PATH); \
		echo "Bundled catalog DB ($$(du -h $(DB_PATH) | cut -f1))"; \
	else \
		echo "No catalog DB found (run 'make sync' first to bundle one)"; \
	fi
	@(cd $(DIST_DIR) && zip -qr ../$(ZIP_FILE) $(ADDON_ID))
	@rm -rf $(DIST_DIR)
	@echo "Built $(ZIP_FILE) ($$(du -h $(ZIP_FILE) | cut -f1))"

clean: ## Remove build artifacts
	rm -f $(DB_PATH) $(ZIP_FILE)
	rm -rf $(DIST_DIR)
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'
