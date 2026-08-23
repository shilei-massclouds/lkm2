.DEFAULT_GOAL := build

TARGET ?= riscv64imac-unknown-none-elf
RUSTC ?= rustc
OBJCOPY ?= rust-objcopy
QEMU ?= qemu-system-riscv64

BUILD_DIR := build
BUILD_STAMP := $(BUILD_DIR)/.stamp
ELF := $(BUILD_DIR)/lkm2.elf
IMAGE := $(BUILD_DIR)/lkm2.bin
RUST_SOURCES := main.rs phases.rs phases/arch_head.rs

RUSTC_FLAGS := \
	--edition=2024 \
	--crate-name=lkm2 \
	--crate-type=bin \
	--target=$(TARGET) \
	-C opt-level=2 \
	-C panic=abort \
	-C relocation-model=static \
	-C linker=rust-lld \
	-C link-arg=-Tlinker.ld \
	-C link-arg=--gc-sections \
	-C link-arg=--build-id=none

QEMU_FLAGS := \
	-machine virt \
	-bios default \
	-m 128M \
	-smp 1 \
	-nographic \
	-kernel $(IMAGE)

.PHONY: all build run clean help

all: build

build: $(IMAGE)

$(ELF): $(RUST_SOURCES) linker.ld | $(BUILD_STAMP)
	$(RUSTC) $(RUSTC_FLAGS) -o $@ main.rs

$(IMAGE): $(ELF)
	$(OBJCOPY) -O binary $< $@

$(BUILD_STAMP):
	mkdir -p $(BUILD_DIR)
	touch $@

run: build
	$(QEMU) $(QEMU_FLAGS)

clean:
	rm -rf $(BUILD_DIR)

help:
	@echo "Implementation component targets:"
	@echo "  all    Build the kernel ELF and raw image (default)"
	@echo "  build  Build build/lkm2.elf and build/lkm2.bin"
	@echo "  run    Run the raw image on QEMU virt/OpenSBI (exit with Ctrl-a x)"
	@echo "  clean  Remove impl/build"
	@echo "  help   Show this help"
