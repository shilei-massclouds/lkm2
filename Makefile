.DEFAULT_GOAL := all

TOOLS_MAKE := $(MAKE) --no-print-directory -C tools

.PHONY: all setup build run test clean help

all: build

setup build run test clean:
	$(TOOLS_MAKE) $@

help:
	@echo "Top-level coordination targets:"
	@echo "  all    Build all components (default)"
	@echo "  setup  Set up component development environments"
	@echo "  build  Build all components"
	@echo "  run    Run the current project entry"
	@echo "  test   Test all components"
	@echo "  clean  Remove component build output"
	@echo "  help   Show this help"
	@echo
	@$(TOOLS_MAKE) help
