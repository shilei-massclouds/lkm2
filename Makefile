.DEFAULT_GOAL := all

TOOLS_MAKE := $(MAKE) --no-print-directory -C tools
IMPL_MAKE := $(MAKE) --no-print-directory -C impl
RUN_QUIET := @

ifeq ($(VERBOSE),1)
RUN_QUIET :=
endif

.PHONY: all setup build derive run test phase-test test-derive test-smoke test-checkpoints checkpoint-sibling-patch checkpoint-sibling-patch-swapper checkpoint-sibling-patch-memblock checkpoint-sibling-patch-swapper-content checkpoint-sibling-apply difftest checkpoint-diff-sv57 clean help

all: build

setup test-derive:
	$(TOOLS_MAKE) $@

test-smoke:
	$(TOOLS_MAKE) $@
	$(IMPL_MAKE) $@

test:
	$(TOOLS_MAKE) test
	$(IMPL_MAKE) test

phase-test:
	$(IMPL_MAKE) phase-test PHASE_TEST=$(PHASE_TEST)

test-checkpoints checkpoint-sibling-patch checkpoint-sibling-patch-swapper checkpoint-sibling-patch-memblock checkpoint-sibling-patch-swapper-content checkpoint-sibling-apply difftest checkpoint-diff-sv57:
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
	@echo "  phase-test Run all lkm2-only PhaseTests, or one selected by PHASE_TEST"
	@echo "  test-derive  Test derive units and golden cases"
	@echo "  test-smoke   Test only derive golden cases"
	@echo "  test-checkpoints Test lkm2 Sv57/Sv48/Sv39 checkpoint output"
	@echo "  checkpoint-sibling-patch Generate and check the frozen Linux patch"
	@echo "  checkpoint-sibling-patch-swapper Generate and check the incremental M1 Linux patch"
	@echo "  checkpoint-sibling-patch-memblock Generate and check the incremental MemBlock Linux patch"
	@echo "  checkpoint-sibling-patch-swapper-content Generate and check the implementation-only page-table content patch"
	@echo "  checkpoint-sibling-apply Apply the reviewed patch or recognize its integrated commit"
	@echo "  difftest Run strict lkm2/Linux Sv57 differential"
	@echo "  checkpoint-diff-sv57 Compatibility alias for difftest"
	@echo "  clean  Remove component build output"
	@echo "  help   Show this help"
	@echo
	@$(TOOLS_MAKE) help
	@echo
	@$(IMPL_MAKE) help
