.DEFAULT_GOAL := all

REPOSITORY := $(abspath $(CURDIR)/..)
VENV_DIR ?= $(CURDIR)/.venv
BOOTSTRAP_PYTHON ?= python3
PYTHON ?= $(VENV_DIR)/bin/python
PYTHON_BIN_DIR := $(dir $(abspath $(PYTHON)))
DEFAULT_SEQUENCE := $(CURDIR)/build/derive/main.sequence.json
USER_RUNTIME_SIGNALS ?= tools/signals/parked.signals
RUN_QUIET :=
RUN_TARGET_QUIET := @

ifeq ($(VERBOSE),1)
RUN_TARGET_QUIET :=
endif

repo_path = $(if $(filter /%,$(strip $(1))),$(strip $(1)),$(REPOSITORY)/$(strip $(1)))
DERIVE_ARGS = $(if $(strip $(MODEL)),--model "$(call repo_path,$(MODEL))",$(if $(strip $(SEQUENCE)),--sequence "$(call repo_path,$(SEQUENCE))",)) $(if $(strip $(USER_RUNTIME_SIGNALS)),--user-runtime-signals "$(call repo_path,$(USER_RUNTIME_SIGNALS))",)

PATH := $(PYTHON_BIN_DIR):$(PATH)
export PATH

.PHONY: all setup check-environment build run test test-derive test-smoke clean help

all: build

setup:
	$(BOOTSTRAP_PYTHON) -m venv $(VENV_DIR)
	$(VENV_DIR)/bin/python -m pip install -e .

check-environment:
	@test -x "$(PYTHON)" || { \
		echo "error: tools environment is missing; run 'make setup'" >&2; \
		exit 1; \
	}

build: check-environment
	$(RUN_QUIET)$(PYTHON) -m compileall -q src tests
	$(RUN_QUIET)$(PYTHON) -m modelc.build_cache --repository $(REPOSITORY) \
		$(REPOSITORY)/model/main.spec $(CURDIR)/build/modelc
	$(RUN_QUIET)$(PYTHON) -m derive.sequence_builder \
		$(CURDIR)/build/modelc/model.ir.json $(DEFAULT_SEQUENCE)

run: RUN_QUIET := $(RUN_TARGET_QUIET)
run: build
	@test -z "$(strip $(MODEL))" -o -z "$(strip $(SEQUENCE))" || { \
		echo "error: MODEL and SEQUENCE are mutually exclusive" >&2; \
		exit 2; \
	}
	$(RUN_QUIET)$(CURDIR)/bin/derive $(DERIVE_ARGS)

test: build
	$(PYTHON) tests/runner.py
	$(PYTHON) tests/derive/runner.py

test-derive: check-environment
	$(PYTHON) tests/derive/runner.py

test-smoke: check-environment
	$(PYTHON) tests/derive/runner.py --smoke-only

clean:
	find src tests -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
	find src tests -type d -name '__pycache__' -empty -delete
	rm -rf build dist src/*.egg-info

help:
	@echo "Tools component targets:"
	@echo "  all    Compile-check the Python tools (default)"
	@echo "  setup  Create tools/.venv and install tools editable"
	@echo "  build  Compile-check tools and cache Model IR"
	@echo "  run    Run tools/bin/derive (MODEL=... or SEQUENCE=..., USER_RUNTIME_SIGNALS=...)"
	@echo "  test   Run the unittest suite"
	@echo "  test-derive  Run derive unit and golden smoke tests"
	@echo "  test-smoke   Run only derive golden smoke cases"
	@echo "  clean  Remove generated Python build output"
