SHELL := /bin/sh

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
BUILD := $(ROOT)/.build
QEMU_SRC := $(BUILD)/src/qemu
QEMU_BUILD := $(BUILD)/qemu
BUILDROOT_SRC := $(BUILD)/src/buildroot
BUILDROOT_EXTERNAL := $(ROOT)/buildroot-external
RGB_DRIVER_STAGE ?= 05-ioctl-async
BUILDROOT_OUTPUT := $(BUILD)/buildroot-output/$(RGB_DRIVER_STAGE)
DRIVER_SOURCE := $(ROOT)/driver/stages/$(RGB_DRIVER_STAGE)/rgb.rs
LINUX_SOURCE := $(BUILDROOT_OUTPUT)/build/linux-6.18.7
JOBS ?= $(shell nproc)

STAGES := 01-character-device 02-mmio-fixed 03-device-tree 04-ioctl-sync 05-ioctl-async

.PHONY: all setup check-layout prepare-qemu prepare-buildroot qemu image run smoke check check-stages slides slides-serve slides-pdf slides-pdf-image clean

all: qemu image

setup:
	git submodule update --init
	@command -v git >/dev/null
	@command -v make >/dev/null
	@command -v ninja >/dev/null
	@command -v python3 >/dev/null
	@command -v timeout >/dev/null
	@command -v stdbuf >/dev/null
	@echo "submodules and host tools: OK"

check-layout:
	$(ROOT)/scripts/check-layout.sh

prepare-qemu: check-layout
	$(ROOT)/scripts/prepare-qemu.sh

prepare-buildroot: check-layout
	$(ROOT)/scripts/prepare-buildroot.sh

qemu: prepare-qemu
	mkdir -p $(QEMU_BUILD) $(BUILD)/ccache
	@if test ! -f "$(QEMU_BUILD)/build.ninja"; then \
		cd $(QEMU_BUILD) && CCACHE_DIR=$(BUILD)/ccache $(QEMU_SRC)/configure \
			--target-list=aarch64-softmmu --enable-slirp --disable-werror; \
	fi
	CCACHE_DIR=$(BUILD)/ccache ninja -C $(QEMU_BUILD) -j$(JOBS) qemu-system-aarch64

image: prepare-buildroot
	@test -f "$(DRIVER_SOURCE)" || { \
		echo "Unknown RGB_DRIVER_STAGE=$(RGB_DRIVER_STAGE); expected one of: $(STAGES)" >&2; \
		exit 2; \
	}
	mkdir -p $(BUILDROOT_OUTPUT) $(BUILD)/ccache
	CCACHE_DIR=$(BUILD)/ccache $(MAKE) -C $(BUILDROOT_SRC) O=$(BUILDROOT_OUTPUT) \
		BR2_EXTERNAL=$(BUILDROOT_EXTERNAL) RGB_DRIVER_STAGE=$(RGB_DRIVER_STAGE) rgb_defconfig
	@if test -d "$(LINUX_SOURCE)" && ! cmp -s "$(DRIVER_SOURCE)" "$(LINUX_SOURCE)/samples/rust/rgb.rs"; then \
		echo "Updating Linux with driver stage $(RGB_DRIVER_STAGE)"; \
		install -m 0644 "$(DRIVER_SOURCE)" "$(LINUX_SOURCE)/samples/rust/rgb.rs"; \
		CCACHE_DIR=$(BUILD)/ccache $(MAKE) -C $(BUILDROOT_SRC) O=$(BUILDROOT_OUTPUT) \
			BR2_EXTERNAL=$(BUILDROOT_EXTERNAL) RGB_DRIVER_STAGE=$(RGB_DRIVER_STAGE) linux-rebuild; \
	fi
	CCACHE_DIR=$(BUILD)/ccache $(MAKE) -C $(BUILDROOT_SRC) O=$(BUILDROOT_OUTPUT) \
		BR2_EXTERNAL=$(BUILDROOT_EXTERNAL) RGB_DRIVER_STAGE=$(RGB_DRIVER_STAGE) -j$(JOBS)

run: qemu image
	mkdir -p $(BUILD)/tmp
	TMPDIR=$(BUILD)/tmp $(QEMU_BUILD)/qemu-system-aarch64 \
		-machine virt -cpu cortex-a57 -m 1G -nographic -serial mon:stdio \
		-kernel $(BUILDROOT_OUTPUT)/images/Image \
		-append "console=ttyAMA0 root=/dev/vda" \
		-drive file=$(BUILDROOT_OUTPUT)/images/rootfs.ext4,if=none,id=hd0,format=raw \
		-device virtio-blk-device,drive=hd0 \
		-netdev user,id=net0,hostfwd=tcp::2222-:22 \
		-device virtio-net-device,netdev=net0

smoke: qemu image
	python3 $(ROOT)/tests/smoke_vm.py \
		$(QEMU_BUILD)/qemu-system-aarch64 \
		$(BUILDROOT_OUTPUT)/images/Image \
		$(BUILDROOT_OUTPUT)/images/rootfs.ext4

check: check-layout smoke

check-stages: qemu
	@set -e; for stage in $(STAGES); do \
		$(MAKE) image RGB_DRIVER_STAGE=$$stage; \
	done

slides:
	mkdir -p $(BUILD)/uv-cache
	UV_CACHE_DIR=$(BUILD)/uv-cache ./server.py --build-only

slides-serve:
	mkdir -p $(BUILD)/uv-cache
	UV_CACHE_DIR=$(BUILD)/uv-cache ./server.py --watch

slides-pdf: slides
	./build_pdf/build_pdf.sh

slides-pdf-image:
	./build_pdf/build_image.sh

clean:
	@test "$(BUILD)" = "$(ROOT)/.build"
	rm -rf "$(BUILD)"
