RGB_DRIVER_STAGE ?= 05-ioctl-async
RGB_DRIVER_SOURCE = $(BR2_EXTERNAL_RGB_PATH)/../driver/stages/$(RGB_DRIVER_STAGE)/rgb.rs

define RGB_INSTALL_DRIVER_SOURCE
	@test -f "$(RGB_DRIVER_SOURCE)" || { \
		echo "Unknown RGB_DRIVER_STAGE=$(RGB_DRIVER_STAGE)" >&2; \
		exit 1; \
	}
	$(INSTALL) -D -m 0644 "$(RGB_DRIVER_SOURCE)" "$(@D)/samples/rust/rgb.rs"
endef

LINUX_POST_PATCH_HOOKS += RGB_INSTALL_DRIVER_SOURCE
