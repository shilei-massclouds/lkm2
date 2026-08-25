.DEFAULT_GOAL := all

TOOLS_MAKE := $(MAKE) --no-print-directory -C tools
IMPL_MAKE := $(MAKE) --no-print-directory -C impl
RUN_QUIET := @

ifeq ($(VERBOSE),1)
RUN_QUIET :=
endif

.PHONY: all setup build derive run test test-derive test-smoke test-checkpoints checkpoint-sibling-patch checkpoint-sibling-apply checkpoint-diff-sv57 clean help

all: build

setup test-derive test-smoke:
	$(TOOLS_MAKE) $@

test:
	$(TOOLS_MAKE) test
	$(IMPL_MAKE) test

test-checkpoints checkpoint-sibling-patch checkpoint-sibling-apply checkpoint-diff-sv57:
	$(IMPL_MAKE) $@

build:
	$(TOOLS_MAKE) build
	$(IMPL_MAKE) build

derive:
	$(RUN_QUIET)$(TOOLS_MAKE) run

run:
	$(IMPL_MAKE) run

clean:
	$(TOOLS_MAKE) clean
	$(IMPL_MAKE) clean

help:
	@echo "Top-level coordination targets:"
	@echo "  all    Build all components (default)"
	@echo "  setup  Set up component development environments"
	@echo "  build  Build tools and the kernel implementation"
	@echo "  derive  Run the current project derivation"
	@echo "  run    Build and run the kernel implementation on QEMU"
	@echo "  test   Test all components"
	@echo "  test-derive  Test derive units and golden cases"
	@echo "  test-smoke   Test only derive golden cases"
	@echo "  test-checkpoints Test lkm2 Sv57/Sv48/Sv39 checkpoint output"
	@echo "  checkpoint-sibling-patch Generate and check the frozen Linux patch"
	@echo "  checkpoint-sibling-apply Explicitly apply the reviewed patch unstaged"
	@echo "  checkpoint-diff-sv57 Run strict lkm2/Linux Sv57 differential"
	@echo "  clean  Remove component build output"
	@echo "  help   Show this help"
	@echo
	@$(TOOLS_MAKE) help
	@echo
	@$(IMPL_MAKE) help
