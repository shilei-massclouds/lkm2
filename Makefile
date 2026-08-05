# Top Makefile

.DEFAULT_GOAL := all

.PHONY: all build run test clean help

all: build

build:
	@echo "TODO: add build commands"

run: build
	@echo "TODO: add run commands"

test: build
	@echo "TODO: add test commands"

clean:
	@echo "TODO: add clean commands"

help:
	@echo "Available targets:"
	@echo "  all    Build the project (default)"
	@echo "  build  Build the project"
	@echo "  run    Build and run the project"
	@echo "  test   Build and run tests"
	@echo "  clean  Remove generated files"
	@echo "  help   Show this help"
