// SPDX-License-Identifier: GPL-2.0

//! Minimal RGB character device.

use core::{
    mem::MaybeUninit,
    ptr,
    sync::atomic::{AtomicBool, AtomicPtr, Ordering},
};

use kernel::{
    alloc::KBox,
    bindings, c_str,
    device::Core,
    devres::Devres,
    error::{code::EBUSY, code::EINVAL, code::ENODEV, code::ENOTTY, to_result},
    ffi::{c_char, c_int, c_long, c_uint, c_ulong, c_void},
    io::mem::IoMem,
    ioctl::_IO,
    of, platform,
    prelude::*,
    sync::{aref::ARef, Arc, ArcBorrow},
    time::{delay::fsleep, Delta},
    types::Opaque,
    uaccess::{UserPtr, UserSlice},
    workqueue,
};

const MAX_INPUT_SIZE: usize = 16;
const RGB_IOCTL_CYCLE: u32 = _IO('r' as u32, 0x01);
const RGB_CYCLE_DELAY: Delta = Delta::from_secs(1);

const RGB_MMIO_SIZE: usize = 0x100;

const RGB_REG_RED: usize = 0x00;
const RGB_REG_GREEN: usize = 0x04;
const RGB_REG_BLUE: usize = 0x08;
const RGB_REG_APPLY: usize = 0x0c;

static RGB_HW: AtomicPtr<RgbDevice> = AtomicPtr::new(ptr::null_mut());

#[allow(improper_ctypes)]
unsafe extern "C" {
    fn try_module_get(module: *mut bindings::module) -> bool;
    fn module_put(module: *mut bindings::module);
}

struct RgbDriver {
    _pdev: ARef<platform::Device>,
    dev: bindings::dev_t,
    cdev: KBox<Opaque<bindings::cdev>>,
    _fops: KBox<bindings::file_operations>,
    _hw: Arc<RgbDevice>,
}

struct RgbDevice {
    mmio: Pin<KBox<Devres<IoMem<RGB_MMIO_SIZE>>>>,
    cycle_running: AtomicBool,
}

// SAFETY: The mapping is owned by `RgbDevice` and remains valid until its
// destructor runs. MMIO access itself is performed by the kernel I/O helpers.
unsafe impl Send for RgbDevice {}
// SAFETY: Concurrent accesses are permitted by the device. Phase 2 does not
// provide transaction-level serialization between multiple writers.
unsafe impl Sync for RgbDevice {}

impl RgbDevice {
    fn map(pdev: &platform::Device<Core>) -> Result<Arc<Self>> {
        let resource = pdev.resource_by_index(0).ok_or(ENODEV)?;
        let request = pdev.io_request_by_index(0).ok_or(ENODEV)?;
        let mmio = KBox::pin_init(
            request.iomap_sized::<RGB_MMIO_SIZE>(),
            GFP_KERNEL,
        )?;

        dev_info!(
            pdev.as_ref(),
            "mmio mapped base={:#x} size={:#x}\n",
            resource.start(),
            resource.size()
        );

        Ok(Arc::new(
            Self {
                mmio,
                cycle_running: AtomicBool::new(false),
            },
            GFP_KERNEL,
        )?)
    }

    fn set_color(&self, red: u8, green: u8, blue: u8) -> Result {
        self.mmio
            .try_access_with(|io| {
                io.write32(red as u32, RGB_REG_RED);
                io.write32(green as u32, RGB_REG_GREEN);
                io.write32(blue as u32, RGB_REG_BLUE);
                io.write32(1, RGB_REG_APPLY);
            })
            .ok_or(ENODEV)
    }

    fn cycle_primary_colors(&self) {
        pr_info!("starting RGB ioctl cycle\n");

        for (red, green, blue) in [(255, 0, 0), (0, 255, 0), (0, 0, 255)] {
            if self.set_color(red, green, blue).is_err() {
                pr_err!("MMIO device became unavailable during ioctl cycle\n");
                break;
            }
            pr_info!("ioctl cycle color r={} g={} b={}\n", red, green, blue);
            fsleep(RGB_CYCLE_DELAY);
        }

        pr_info!("finished RGB ioctl cycle\n");
        self.cycle_running.store(false, Ordering::Release);
    }

    fn start_cycle(hw: Arc<Self>) -> Result {
        if hw
            .cycle_running
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return Err(EBUSY);
        }

        let module = ptr::addr_of_mut!(bindings::__this_module);
        // SAFETY: `module` is THIS_MODULE. Holding this reference prevents the
        // workqueue callback code from being unloaded before it finishes.
        if !unsafe { try_module_get(module) } {
            hw.cycle_running.store(false, Ordering::Release);
            return Err(ENODEV);
        }
        let module_addr = module as usize;

        let reset_on_error = hw.clone();
        if let Err(error) = workqueue::system_long().try_spawn(GFP_KERNEL, move || {
            hw.cycle_primary_colors();
            // SAFETY: This balances the successful try_module_get() above.
            unsafe { module_put(module_addr as *mut bindings::module) };
        }) {
            reset_on_error
                .cycle_running
                .store(false, Ordering::Release);
            // SAFETY: The callback was not queued, so release its module reference here.
            unsafe { module_put(module) };
            return Err(error.into());
        }

        Ok(())
    }
}

// SAFETY: The cdev and file-operations allocations remain at stable addresses
// for the whole registration. They are only accessed through the kernel's cdev
// and VFS synchronization, and `file_operations::owner` pins this module while
// an open file can call one of its callbacks.
unsafe impl Send for RgbDriver {}
// SAFETY: See the `Send` implementation above; Rust code never mutates or
// dereferences these C-owned objects concurrently.
unsafe impl Sync for RgbDriver {}

kernel::of_device_table!(
    RGB_OF_TABLE,
    MODULE_OF_TABLE,
    <RgbDriver as platform::Driver>::IdInfo,
    [(of::DeviceId::new(c_str!("qemu,rgb-led")), ())]
);

impl platform::Driver for RgbDriver {
    type IdInfo = ();
    const OF_ID_TABLE: Option<of::IdTable<Self::IdInfo>> = Some(&RGB_OF_TABLE);

    fn probe(
        pdev: &platform::Device<Core>,
        _info: Option<&Self::IdInfo>,
    ) -> Result<Pin<KBox<Self>>> {
        dev_info!(pdev.as_ref(), "platform device matched\n");

        let hw = RgbDevice::map(pdev)?;

        // The VFS keeps this table after open(), so it must have a stable address.
        let mut raw_fops: bindings::file_operations =
            unsafe { MaybeUninit::zeroed().assume_init() };
        raw_fops.owner = if cfg!(MODULE) {
            ptr::addr_of_mut!(bindings::__this_module)
        } else {
            ptr::null_mut()
        };
        raw_fops.open = Some(rgb_open);
        raw_fops.write = Some(rgb_write);
        raw_fops.unlocked_ioctl = Some(rgb_ioctl);
        raw_fops.compat_ioctl = Some(rgb_ioctl);
        let fops = KBox::new(raw_fops, GFP_KERNEL)?;

        // cdev_init() initializes this opaque allocation in place.
        let cdev = KBox::new(Opaque::<bindings::cdev>::uninit(), GFP_KERNEL)?;
        let cdev_ptr = cdev.get();
        let mut dev: bindings::dev_t = 0;

        // SAFETY: `dev` is writable, the range contains one minor, and the name is static.
        let alloc_result = to_result(unsafe {
            bindings::alloc_chrdev_region(&mut dev, 0, 1, c_str!("rgb").as_char_ptr())
        });
        if let Err(error) = alloc_result {
            pr_err!("failed to allocate char device region\n");
            return Err(error);
        }

        // SAFETY: Both allocations have stable addresses and outlive the registration.
        unsafe { bindings::cdev_init(cdev_ptr, ptr::from_ref(&*fops)) };

        // SAFETY: The device number is allocated and `cdev_ptr` was initialized above.
        RGB_HW.store(Arc::as_ptr(&hw).cast_mut(), Ordering::Release);

        let add_result = to_result(unsafe { bindings::cdev_add(cdev_ptr, dev, 1) });
        if let Err(error) = add_result {
            RGB_HW.store(ptr::null_mut(), Ordering::Release);
            // cdev_init() owns a kobject reference even when cdev_add() fails.
            // SAFETY: The cdev was initialized and is no longer used after this call.
            unsafe { bindings::kobject_put(ptr::addr_of_mut!((*cdev_ptr).kobj)) };
            // SAFETY: This exact range was successfully allocated above.
            unsafe { bindings::unregister_chrdev_region(dev, 1) };
            pr_err!("failed to register char device\n");
            return Err(error);
        }

        let major = (dev >> bindings::MINORBITS) as u32;
        pr_info!("registered char device major={} minor=0\n", major);

        Ok(KBox::new(Self {
            _pdev: pdev.into(),
            dev,
            cdev,
            _fops: fops,
            _hw: hw,
        }, GFP_KERNEL)?.into())
    }
}

impl Drop for RgbDriver {
    fn drop(&mut self) {
        // SAFETY: A successful init registered this cdev exactly once.
        unsafe { bindings::cdev_del(self.cdev.get()) };
        RGB_HW.store(ptr::null_mut(), Ordering::Release);
        // SAFETY: A successful init allocated this exact one-device range.
        unsafe { bindings::unregister_chrdev_region(self.dev, 1) };
        pr_info!("module unloaded\n");
    }
}

unsafe extern "C" fn rgb_open(
    _inode: *mut bindings::inode,
    file: *mut bindings::file,
) -> c_int {
    let hw = RGB_HW.load(Ordering::Acquire);
    if hw.is_null() {
        pr_err!("device is unavailable\n");
        return ENODEV.to_errno();
    }

    // SAFETY: `file` is valid and exclusively available to open(). The module
    // owner keeps `RgbDevice` alive until this file is released.
    unsafe { (*file).private_data = hw.cast::<c_void>() };
    pr_info!("device opened\n");
    0
}

unsafe extern "C" fn rgb_write(
    file: *mut bindings::file,
    user_buffer: *const c_char,
    count: usize,
    _offset: *mut bindings::loff_t,
) -> isize {
    // SAFETY: `file` is valid for the duration of write(). rgb_open stored a
    // live RgbDevice pointer in private_data before returning successfully.
    let hw = unsafe { (*file).private_data.cast::<RgbDevice>().as_ref() };
    let Some(hw) = hw else {
        pr_err!("device is unavailable\n");
        return ENODEV.to_errno() as isize;
    };

    match copy_and_parse(user_buffer, count) {
        Ok((red, green, blue)) => {
            pr_info!(
                "requested color r={} g={} b={}\n",
                red,
                green,
                blue
            );
            if let Err(error) = hw.set_color(red, green, blue) {
                pr_err!("MMIO device is unavailable\n");
                return error.to_errno() as isize;
            }
            pr_info!("wrote MMIO color r={} g={} b={}\n", red, green, blue);
            count as isize
        }
        Err(error) => {
            if error == EINVAL {
                pr_info!("invalid color format\n");
            }
            error.to_errno() as isize
        }
    }
}

unsafe extern "C" fn rgb_ioctl(
    file: *mut bindings::file,
    cmd: c_uint,
    _arg: c_ulong,
) -> c_long {
    // SAFETY: `file` is valid for the duration of ioctl(). rgb_open stored a
    // live RgbDevice pointer in private_data before returning successfully.
    let hw_ptr = unsafe { (*file).private_data.cast::<RgbDevice>() };
    if hw_ptr.is_null() {
        pr_err!("device is unavailable\n");
        return ENODEV.to_errno() as c_long;
    }

    // SAFETY: private_data originates from Arc::as_ptr(), and file_operations::owner
    // keeps the driver and its Arc alive for the duration of this callback.
    let hw = Arc::from(unsafe { ArcBorrow::from_raw(hw_ptr) });

    match cmd {
        RGB_IOCTL_CYCLE => match RgbDevice::start_cycle(hw) {
            Ok(()) => 0,
            Err(error) => error.to_errno() as c_long,
        },
        _ => {
            pr_info!("unknown ioctl cmd={:#x}\n", cmd);
            ENOTTY.to_errno() as c_long
        }
    }
}

fn copy_and_parse(user_buffer: *const c_char, count: usize) -> Result<(u8, u8, u8)> {
    if count > MAX_INPUT_SIZE {
        return Err(EINVAL);
    }

    let mut buffer = [0_u8; MAX_INPUT_SIZE];
    let user_ptr = UserPtr::from_ptr(user_buffer.cast_mut().cast::<c_void>());
    let mut reader = UserSlice::new(user_ptr, count).reader();
    reader.read_slice(&mut buffer[..count])?;

    parse_color(&buffer[..count]).ok_or(EINVAL)
}

fn parse_color(input: &[u8]) -> Option<(u8, u8, u8)> {
    let input = trim_ascii_whitespace(input);
    let digits = input.strip_prefix(b"#").unwrap_or(input);

    if digits.len() != 6 {
        return None;
    }

    Some((
        parse_byte(&digits[0..2])?,
        parse_byte(&digits[2..4])?,
        parse_byte(&digits[4..6])?,
    ))
}

fn trim_ascii_whitespace(mut input: &[u8]) -> &[u8] {
    while input.first().is_some_and(u8::is_ascii_whitespace) {
        input = &input[1..];
    }
    while input.last().is_some_and(u8::is_ascii_whitespace) {
        input = &input[..input.len() - 1];
    }
    input
}

fn parse_byte(digits: &[u8]) -> Option<u8> {
    let high = hex_value(*digits.first()?)?;
    let low = hex_value(*digits.get(1)?)?;
    Some((high << 4) | low)
}

fn hex_value(digit: u8) -> Option<u8> {
    match digit {
        b'0'..=b'9' => Some(digit - b'0'),
        b'a'..=b'f' => Some(digit - b'a' + 10),
        b'A'..=b'F' => Some(digit - b'A' + 10),
        _ => None,
    }
}

kernel::module_platform_driver! {
    type: RgbDriver,
    name: "rgb",
    authors: ["RGB driver contributors"],
    description: "Rust RGB platform and character device driver",
    license: "GPL",
}
