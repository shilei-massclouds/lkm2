.DEFAULT_GOAL := all

TOOLS_MAKE := $(MAKE) --no-print-directory -C tools
RUN_QUIET := @

ifeq ($(VERBOSE),1)
RUN_QUIET :=
endif

.PHONY: all setup build derive run test test-derive test-smoke clean help

all: build

setup build test test-derive test-smoke clean:
	$(TOOLS_MAKE) $@

derive:
	$(RUN_QUIET)$(TOOLS_MAKE) run

run:
	@:

help:
	@echo "Top-level coordination targets:"
	@echo "  all    Build all components (default)"
	@echo "  setup  Set up component development environments"
	@echo "  build  Build all components"
	@echo "  derive  Run the current project derivation"
	@echo "  run    Temporarily retained as a no-op"
	@echo "  test   Test all components"
	@echo "  test-derive  Test derive units and golden cases"
	@echo "  test-smoke   Test only derive golden cases"
	@echo "  clean  Remove component build output"
	@echo "  help   Show this help"
	@echo
	@$(TOOLS_MAKE) help
