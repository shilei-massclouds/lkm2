TARGET ?= riscv64imac-unknown-none-elf
RUSTC ?= rustc
RUSTFMT ?= rustfmt
CLIPPY ?= clippy-driver
OBJCOPY ?= rust-objcopy

BUILD_DIR := build
BUILD_STAMP := $(BUILD_DIR)/.stamp
ELF := $(BUILD_DIR)/lkm2.elf
CLIPPY_ELF := $(BUILD_DIR)/lkm2.clippy.elf
IMAGE := $(BUILD_DIR)/lkm2.bin
RUST_SOURCES := main.rs systems/kernel/config.rs objects.rs objects/cpu.rs phases.rs phases/arch_head.rs phases/asm_macros.rs phases/csr.rs
LINKER_SCRIPT := systems/kernel/linker.ld

RUSTC_FLAGS := \
	--edition=2024 \
	--crate-name=lkm2 \
	--crate-type=bin \
	--target=$(TARGET) \
	-C opt-level=2 \
	-C panic=abort \
	-C relocation-model=static \
	-C linker=rust-lld \
	-C link-arg=-T$(LINKER_SCRIPT) \
	-C link-arg=--gc-sections \
	-C link-arg=--build-id=none
