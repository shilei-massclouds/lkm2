QEMU ?= qemu-system-riscv64

QEMU_FLAGS := \
	-machine virt \
	-bios default \
	-m 128M \
	-smp 1 \
	-nographic \
	-append "earlycon=sbi" \
	-kernel $(IMAGE)
