.DEFAULT_GOAL := build

include build.mak
include qemu.mak

.PHONY: all build run clean help

all: build

build: $(IMAGE)

$(ELF): $(RUST_SOURCES) $(LINKER_SCRIPT) | $(BUILD_STAMP)
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
