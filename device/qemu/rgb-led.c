/*
 * RGB LED virtual MMIO device
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/core/sysbus.h"
#include "migration/vmstate.h"
#include "qemu/module.h"
#include "qemu/timer.h"
#include "hw/core/irq.h"

#define TYPE_RGB_LED "rgb-led"
OBJECT_DECLARE_SIMPLE_TYPE(RGBLedState, RGB_LED)

#define RGB_LED_MMIO_SIZE 0x100

enum {
    RGB_LED_REG_RED    = 0x00,
    RGB_LED_REG_GREEN  = 0x04,
    RGB_LED_REG_BLUE   = 0x08,
    RGB_LED_REG_APPLY  = 0x0c,
    RGB_LED_REG_STATUS = 0x10,
    RGB_LED_REG_TARGET = 0x14,
    RGB_LED_REG_DURATION = 0x18,
    RGB_LED_REG_COMMAND = 0x1c,
    RGB_LED_REG_IRQ_STATUS = 0x20,
    RGB_LED_REG_IRQ_ENABLE = 0x24,
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
    uint32_t target, duration_ms, irq_status, irq_enable;
    uint8_t start_red, start_green, start_blue;
    uint8_t applied_red, applied_green, applied_blue;
    int64_t start_ms;
    QEMUTimer *timer;
    qemu_irq irq;
};

static void rgb_led_update_irq(RGBLedState *s)
{
    qemu_set_irq(s->irq, !!(s->irq_status & s->irq_enable & 1));
}

static uint8_t rgb_led_interpolate(uint8_t start, uint8_t target,
                                   int64_t elapsed, uint32_t duration)
{
    return start + ((int)target - start) * elapsed / duration;
}

static void rgb_led_tick(void *opaque)
{
    RGBLedState *s = opaque;
    int64_t now = qemu_clock_get_ms(QEMU_CLOCK_VIRTUAL);
    int64_t elapsed = MIN(now - s->start_ms, s->duration_ms);
    s->red = rgb_led_interpolate(s->start_red, s->target >> 16,
                                 elapsed, s->duration_ms);
    s->green = rgb_led_interpolate(s->start_green, s->target >> 8,
                                   elapsed, s->duration_ms);
    s->blue = rgb_led_interpolate(s->start_blue, s->target,
                                  elapsed, s->duration_ms);
    s->applied_red = s->red;
    s->applied_green = s->green;
    s->applied_blue = s->blue;
    printf("rgb-led: fade RGB(%u, %u, %u)\n", s->red, s->green, s->blue);
    if (elapsed == s->duration_ms) {
        s->status = RGB_LED_STATUS_READY;
        s->irq_status = 1;
        printf("rgb-led: fade complete, %s\n",
               s->irq_enable ? "raising IRQ" : "IRQ masked (completion pending)");
        rgb_led_update_irq(s);
    } else {
        timer_mod(s->timer, MIN(now + 20, s->start_ms + s->duration_ms));
    }
}

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
    case RGB_LED_REG_TARGET:
        return s->target;
    case RGB_LED_REG_DURATION:
        return s->duration_ms;
    case RGB_LED_REG_IRQ_STATUS:
        return s->irq_status;
    case RGB_LED_REG_IRQ_ENABLE:
        return s->irq_enable;
    default:
        return 0;
    }
}

static void rgb_led_apply(RGBLedState *s)
{
    s->applied_red = s->red;
    s->applied_green = s->green;
    s->applied_blue = s->blue;
    printf("rgb-led: apply RGB(%u, %u, %u)\n",
           s->red, s->green, s->blue);
}

static void rgb_led_write(void *opaque, hwaddr offset, uint64_t value,
                          unsigned size)
{
    RGBLedState *s = opaque;

    if (s->status == RGB_LED_STATUS_BUSY && offset < RGB_LED_REG_COMMAND) {
        return;
    }
    switch (offset) {
    case RGB_LED_REG_TARGET:
        s->target = value;
        break;
    case RGB_LED_REG_DURATION:
        s->duration_ms = value;
        break;
    case RGB_LED_REG_IRQ_STATUS:
        s->irq_status &= ~(value & 1);
        rgb_led_update_irq(s);
        break;
    case RGB_LED_REG_IRQ_ENABLE:
        s->irq_enable = value & 1;
        rgb_led_update_irq(s);
        break;
    case RGB_LED_REG_COMMAND:
        if (value == 2) {
            timer_del(s->timer);
            s->status = RGB_LED_STATUS_READY;
            s->irq_status = 0;
            rgb_led_update_irq(s);
        } else if (s->status != RGB_LED_STATUS_BUSY) {
            if (value != 1 || s->target > 0xffffff ||
                s->duration_ms < 1 || s->duration_ms > 60000) {
                s->status = RGB_LED_STATUS_ERROR;
                break;
            }
            s->start_red = s->applied_red;
            s->start_green = s->applied_green;
            s->start_blue = s->applied_blue;
            s->start_ms = qemu_clock_get_ms(QEMU_CLOCK_VIRTUAL);
            s->status = RGB_LED_STATUS_BUSY;
            s->irq_status = 0;
            rgb_led_update_irq(s);
            printf("rgb-led: starting hardware fade\n");
            timer_mod(s->timer, s->start_ms + MIN(20, s->duration_ms));
        }
        break;
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

    timer_del(s->timer);
    s->target = s->duration_ms = s->irq_status = s->irq_enable = 0;
    rgb_led_update_irq(s);
    s->applied_red = s->applied_green = s->applied_blue = 0;
    s->start_red = s->start_green = s->start_blue = 0;
    s->start_ms = 0;
    s->red = 0;
    s->green = 0;
    s->blue = 0;
    s->status = RGB_LED_STATUS_READY;
}

static int rgb_led_post_load(void *opaque, int version_id)
{
    RGBLedState *s = opaque;
    if (s->status == RGB_LED_STATUS_BUSY &&
        (!s->duration_ms || s->duration_ms > 60000)) {
        return -EINVAL;
    }
    rgb_led_update_irq(s);
    return 0;
}

static const VMStateDescription vmstate_rgb_led = {
    .name = TYPE_RGB_LED,
    .version_id = 2,
    .minimum_version_id = 2,
    .post_load = rgb_led_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT8(red, RGBLedState),
        VMSTATE_UINT8(green, RGBLedState),
        VMSTATE_UINT8(blue, RGBLedState),
        VMSTATE_UINT32(status, RGBLedState),
        VMSTATE_UINT32(target, RGBLedState),
        VMSTATE_UINT32(duration_ms, RGBLedState),
        VMSTATE_UINT32(irq_status, RGBLedState),
        VMSTATE_UINT32(irq_enable, RGBLedState),
        VMSTATE_UINT8(applied_red, RGBLedState),
        VMSTATE_UINT8(applied_green, RGBLedState),
        VMSTATE_UINT8(applied_blue, RGBLedState),
        VMSTATE_UINT8(start_red, RGBLedState),
        VMSTATE_UINT8(start_green, RGBLedState),
        VMSTATE_UINT8(start_blue, RGBLedState),
        VMSTATE_INT64(start_ms, RGBLedState),
        VMSTATE_TIMER_PTR(timer, RGBLedState),
        VMSTATE_END_OF_LIST()
    }
};

static void rgb_led_init(Object *obj)
{
    RGBLedState *s = RGB_LED(obj);

    memory_region_init_io(&s->mmio, obj, &rgb_led_ops, s,
                          TYPE_RGB_LED, RGB_LED_MMIO_SIZE);
    s->timer = timer_new_ms(QEMU_CLOCK_VIRTUAL, rgb_led_tick, s);
    sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->mmio);
}

static void rgb_led_finalize(Object *obj)
{
    timer_free(RGB_LED(obj)->timer);
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
    .instance_finalize = rgb_led_finalize,
    .class_init = rgb_led_class_init,
};

static void rgb_led_register_types(void)
{
    type_register_static(&rgb_led_info);
}

type_init(rgb_led_register_types)
