.DEFAULT_GOAL := all

REPOSITORY := $(abspath $(CURDIR)/..)
VENV_DIR ?= $(CURDIR)/.venv
BOOTSTRAP_PYTHON ?= python3
PYTHON ?= $(VENV_DIR)/bin/python
PYTHON_BIN_DIR := $(dir $(abspath $(PYTHON)))
DEFAULT_SEQUENCE := $(CURDIR)/build/derive/main.sequence.json

repo_path = $(if $(filter /%,$(strip $(1))),$(strip $(1)),$(REPOSITORY)/$(strip $(1)))
DERIVE_ARGS = $(if $(strip $(MODEL)),--model "$(call repo_path,$(MODEL))",$(if $(strip $(SEQUENCE)),--sequence "$(call repo_path,$(SEQUENCE))",))

PATH := $(PYTHON_BIN_DIR):$(PATH)
export PATH

.PHONY: all setup check-environment build run test clean help

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
	$(PYTHON) -m compileall -q src tests
	$(PYTHON) -m modelc.build_cache --repository $(REPOSITORY) \
		$(REPOSITORY)/model/main.spec $(CURDIR)/build/modelc
	$(PYTHON) -m derive.sequence_builder \
		$(CURDIR)/build/modelc/model.ir.json $(DEFAULT_SEQUENCE)

run: build
	@test -z "$(strip $(MODEL))" -o -z "$(strip $(SEQUENCE))" || { \
		echo "error: MODEL and SEQUENCE are mutually exclusive" >&2; \
		exit 2; \
	}
	$(CURDIR)/bin/derive $(DERIVE_ARGS)

test: build
	$(PYTHON) -m unittest discover -s tests -v

clean:
	find src tests -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
	find src tests -type d -name '__pycache__' -empty -delete
	rm -rf build dist src/*.egg-info

help:
	@echo "Tools component targets:"
	@echo "  all    Compile-check the Python tools (default)"
	@echo "  setup  Create tools/.venv and install tools editable"
	@echo "  build  Compile-check tools and cache Model IR"
	@echo "  run    Run tools/bin/derive (MODEL=... or SEQUENCE=..., not both)"
	@echo "  test   Run the unittest suite"
	@echo "  clean  Remove generated Python build output"
