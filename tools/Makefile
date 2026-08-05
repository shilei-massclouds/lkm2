.DEFAULT_GOAL := all

REPOSITORY := $(abspath $(CURDIR)/..)
VENV_DIR ?= $(CURDIR)/.venv
BOOTSTRAP_PYTHON ?= python3
PYTHON ?= $(VENV_DIR)/bin/python
PYTHON_BIN_DIR := $(dir $(abspath $(PYTHON)))

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

run: build
	$(PYTHON) -m modelc.cli $(REPOSITORY)/model/main.spec

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
	@echo "  build  Compile-check Python sources"
	@echo "  run    Compile model/main.spec to stdout"
	@echo "  test   Run the unittest suite"
	@echo "  clean  Remove generated Python build output"
