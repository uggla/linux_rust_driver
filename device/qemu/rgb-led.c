/*
 * RGB LED virtual MMIO device
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/core/sysbus.h"
#include "migration/vmstate.h"
#include "qemu/module.h"

#define TYPE_RGB_LED "rgb-led"
OBJECT_DECLARE_SIMPLE_TYPE(RGBLedState, RGB_LED)

#define RGB_LED_MMIO_SIZE 0x100

enum {
    RGB_LED_REG_RED    = 0x00,
    RGB_LED_REG_GREEN  = 0x04,
    RGB_LED_REG_BLUE   = 0x08,
    RGB_LED_REG_APPLY  = 0x0c,
    RGB_LED_REG_STATUS = 0x10,
};

enum {
    RGB_LED_STATUS_BUSY  = 0x0,
    RGB_LED_STATUS_READY = 0x1,
    RGB_LED_STATUS_ERROR = 0x2,
};

struct RGBLedState {
    SysBusDevice parent_obj;

    MemoryRegion mmio;

    uint8_t red;
    uint8_t green;
    uint8_t blue;

    uint32_t status;
};

static uint64_t rgb_led_read(void *opaque, hwaddr offset, unsigned size)
{
    RGBLedState *s = opaque;

    switch (offset) {
    case RGB_LED_REG_RED:
        return s->red;
    case RGB_LED_REG_GREEN:
        return s->green;
    case RGB_LED_REG_BLUE:
        return s->blue;
    case RGB_LED_REG_APPLY:
        return 0;
    case RGB_LED_REG_STATUS:
        return s->status;
    default:
        return 0;
    }
}

static void rgb_led_apply(RGBLedState *s)
{
    printf("rgb-led: apply RGB(%u, %u, %u)\n",
           s->red, s->green, s->blue);
}

static void rgb_led_write(void *opaque, hwaddr offset, uint64_t value,
                          unsigned size)
{
    RGBLedState *s = opaque;

    switch (offset) {
    case RGB_LED_REG_RED:
        s->red = value & 0xff;
        break;
    case RGB_LED_REG_GREEN:
        s->green = value & 0xff;
        break;
    case RGB_LED_REG_BLUE:
        s->blue = value & 0xff;
        break;
    case RGB_LED_REG_APPLY:
        if (value == 1) {
            rgb_led_apply(s);
        }
        break;
    case RGB_LED_REG_STATUS:
    default:
        break;
    }
}

static const MemoryRegionOps rgb_led_ops = {
    .read = rgb_led_read,
    .write = rgb_led_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
    .valid = {
        .min_access_size = 4,
        .max_access_size = 4,
        .unaligned = false,
    },
    .impl = {
        .min_access_size = 4,
        .max_access_size = 4,
    },
};

static void rgb_led_reset(DeviceState *dev)
{
    RGBLedState *s = RGB_LED(dev);

    s->red = 0;
    s->green = 0;
    s->blue = 0;
    s->status = RGB_LED_STATUS_READY;
}

static const VMStateDescription vmstate_rgb_led = {
    .name = TYPE_RGB_LED,
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT8(red, RGBLedState),
        VMSTATE_UINT8(green, RGBLedState),
        VMSTATE_UINT8(blue, RGBLedState),
        VMSTATE_UINT32(status, RGBLedState),
        VMSTATE_END_OF_LIST()
    }
};

static void rgb_led_init(Object *obj)
{
    RGBLedState *s = RGB_LED(obj);

    memory_region_init_io(&s->mmio, obj, &rgb_led_ops, s,
                          TYPE_RGB_LED, RGB_LED_MMIO_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->mmio);
}

static void rgb_led_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    device_class_set_legacy_reset(dc, rgb_led_reset);
    dc->vmsd = &vmstate_rgb_led;
}

static const TypeInfo rgb_led_info = {
    .name = TYPE_RGB_LED,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(RGBLedState),
    .instance_init = rgb_led_init,
    .class_init = rgb_led_class_init,
};

static void rgb_led_register_types(void)
{
    type_register_static(&rgb_led_info);
}

type_init(rgb_led_register_types)
