// SPDX-License-Identifier: GPL-2.0

//! Minimal RGB character device.

use core::ptr;

use kernel::{
    alloc::KBox,
    bindings, c_str,
    device::{Bound, Core, Device},
    devres::Devres,
    error::{
        code::EBUSY, code::EINVAL, code::ENODEV, code::ENOTTY, code::ERESTARTSYS, code::ETIMEDOUT,
        to_result,
    },
    ffi::{c_char, c_int, c_long, c_uint, c_ulong, c_void},
    io::mem::IoMem,
    ioctl::{_IO, _IOW},
    irq::{self, IrqReturn, ThreadedIrqReturn, ThreadedRegistration},
    of, platform,
    prelude::*,
    sync::{aref::ARef, new_condvar, new_mutex, Arc, CondVar, CondVarTimeoutResult, Mutex},
    time::msecs_to_jiffies,
    uaccess::{UserPtr, UserSlice},
};

const MAX_INPUT_SIZE: usize = 16;
const RGB_IOCTL_FADE_START: u32 = _IOW::<[u32; 2]>('r' as u32, 0x02);
const RGB_IOCTL_FADE_WAIT: u32 = _IO('r' as u32, 0x03);

const RGB_MMIO_SIZE: usize = 0x100;

const RGB_REG_RED: usize = 0x00;
const RGB_REG_GREEN: usize = 0x04;
const RGB_REG_BLUE: usize = 0x08;
const RGB_REG_APPLY: usize = 0x0c;

const RGB_REG_TARGET: usize = 0x14;
const RGB_REG_DURATION: usize = 0x18;
const RGB_REG_COMMAND: usize = 0x1c;
const RGB_REG_IRQ_STATUS: usize = 0x20;
const RGB_REG_IRQ_ENABLE: usize = 0x24;

kernel::sync::global_lock! {
    // SAFETY: Initialized once by DriverModule before registering the driver.
    unsafe(uninit) static RGB_HW: Mutex<Option<Arc<RgbDevice>>> = None;
}
fn unpublish() {
    *RGB_HW.lock() = None;
}

// Immutable table in module storage, alive even after platform unbind while
// open files still pin the module via owner.
struct FileOps(bindings::file_operations);
// SAFETY: The table is immutable; the VFS only reads it.
unsafe impl Sync for FileOps {}
static FOPS: FileOps = FileOps(bindings::file_operations {
    owner: ptr::addr_of_mut!(bindings::__this_module),
    open: Some(rgb_open),
    release: Some(rgb_release),
    write: Some(rgb_write),
    unlocked_ioctl: Some(rgb_ioctl),
    compat_ioctl: Some(rgb_ioctl),
    // SAFETY: Null pointers/zero flags are valid for unused operations.
    ..unsafe { core::mem::zeroed() }
});

struct RgbDriver {
    _pdev: ARef<platform::Device>,
    dev: bindings::dev_t,
    cdev: *mut bindings::cdev,
    registered: bool,
    _hw: Arc<RgbDevice>,
    _irq: Pin<KBox<ThreadedRegistration<Arc<RgbDevice>>>>,
}

struct FadeState {
    submitted: u64,
    completed: u64,
    removed: bool,
}

#[pin_data]
struct RgbDevice {
    mmio: Pin<KBox<Devres<IoMem<RGB_MMIO_SIZE>>>>,
    #[pin]
    state: Mutex<FadeState>,
    #[pin]
    done: CondVar,
}

impl RgbDevice {
    fn map(pdev: &platform::Device<Core>) -> Result<Arc<Self>> {
        let request = pdev.io_request_by_index(0).ok_or(ENODEV)?;
        let mmio = KBox::pin_init(request.iomap_sized::<RGB_MMIO_SIZE>(), GFP_KERNEL)?;
        Arc::pin_init(
            try_pin_init!(Self {
                mmio,
                state <- new_mutex!(FadeState { submitted: 0, completed: 0, removed: false }),
                done <- new_condvar!(),
            }),
            GFP_KERNEL,
        )
    }

    fn set_color(&self, red: u8, green: u8, blue: u8) -> Result {
        let state = self.state.lock();
        if state.removed {
            return Err(ENODEV);
        }
        if state.submitted != state.completed {
            return Err(EBUSY);
        }
        self.mmio
            .try_access_with(|io| {
                io.write32(red as u32, RGB_REG_RED);
                io.write32(green as u32, RGB_REG_GREEN);
                io.write32(blue as u32, RGB_REG_BLUE);
                io.write32(1, RGB_REG_APPLY);
            })
            .ok_or(ENODEV)
    }

    fn start(&self, color: u32, duration: u32) -> Result {
        if color > 0xffffff || !(1..=60000).contains(&duration) {
            return Err(EINVAL);
        }
        let mut state = self.state.lock();
        if state.removed {
            return Err(ENODEV);
        }
        if state.submitted != state.completed {
            return Err(EBUSY);
        }
        self.mmio
            .try_access_with(|io| {
                state.submitted += 1;
                io.write32(1, RGB_REG_IRQ_STATUS);
                io.write32(color, RGB_REG_TARGET);
                io.write32(duration, RGB_REG_DURATION);
                io.write32(1, RGB_REG_IRQ_ENABLE);
                io.write32(1, RGB_REG_COMMAND);
            })
            .ok_or(ENODEV)
    }

    fn wait(&self) -> Result {
        let mut state = self.state.lock();
        let generation = state.submitted;
        if generation == 0 && !state.removed {
            return Err(EINVAL);
        }
        let mut remaining = msecs_to_jiffies(65000);
        loop {
            if state.removed {
                return Err(ENODEV);
            }
            if state.completed >= generation {
                return Ok(());
            }
            if remaining == 0 {
                return Err(ETIMEDOUT);
            }
            match self.done.wait_interruptible_timeout(&mut state, remaining) {
                CondVarTimeoutResult::Signal { .. } => return Err(ERESTARTSYS),
                CondVarTimeoutResult::Timeout => remaining = 0,
                CondVarTimeoutResult::Woken { jiffies } => remaining = jiffies,
            }
        }
    }

    fn shutdown(&self) {
        let mut state = self.state.lock();
        state.removed = true;
        self.mmio.try_access_with(|io| {
            io.write32(0, RGB_REG_IRQ_ENABLE);
            io.write32(2, RGB_REG_COMMAND);
            io.write32(1, RGB_REG_IRQ_STATUS);
        });
        self.done.notify_all();
    }
}

impl irq::ThreadedHandler for RgbDevice {
    fn handle(&self, _dev: &Device<Bound>) -> ThreadedIrqReturn {
        // No sleeping or allocation in the hard IRQ entry point.
        ThreadedIrqReturn::WakeThread
    }

    fn handle_threaded(&self, _dev: &Device<Bound>) -> IrqReturn {
        let mut state = self.state.lock();
        if state.removed {
            return IrqReturn::None;
        }
        let pending = self
            .mmio
            .try_access_with(|io| {
                if io.read32(RGB_REG_IRQ_STATUS) & 1 == 0 {
                    return false;
                }
                io.write32(1, RGB_REG_IRQ_STATUS);
                true
            })
            .unwrap_or(false);
        if !pending {
            return IrqReturn::None;
        }
        state.completed = state.submitted;
        pr_info!(
            "completion IRQ received, acknowledging generation={}\n",
            state.completed
        );
        self.done.notify_all();
        IrqReturn::Handled
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
        let irq_request = pdev.irq_by_index(0)?;
        let irq = KBox::pin_init(
            ThreadedRegistration::new(
                irq_request,
                irq::Flags::ONESHOT,
                c_str!("rgb"),
                Ok(hw.clone()),
            ),
            GFP_KERNEL,
        )?;

        // Allocate the owning object before publishing any callbacks. Drop
        // unwinds partial registration failures as well as normal unbind.
        let mut driver = KBox::new(
            Self {
                _pdev: pdev.into(),
                dev: 0,
                cdev: ptr::null_mut(),
                registered: false,
                _hw: hw,
                _irq: irq,
            },
            GFP_KERNEL,
        )?;
        // SAFETY: cdev_alloc creates a reference-counted C object; its kobject
        // release frees it only after the last VFS reference is gone.
        driver.cdev = unsafe { bindings::cdev_alloc() };
        if driver.cdev.is_null() {
            return Err(kernel::error::code::ENOMEM);
        }
        // SAFETY: Exclusively owned, initialized cdev and immutable static ops.
        unsafe {
            (*driver.cdev).ops = ptr::from_ref(&FOPS.0);
            (*driver.cdev).owner = ptr::addr_of_mut!(bindings::__this_module);
        }
        // SAFETY: Writable device number, one minor, static name.
        to_result(unsafe {
            bindings::alloc_chrdev_region(&mut driver.dev, 0, 1, c_str!("rgb").as_char_ptr())
        })?;
        *RGB_HW.lock() = Some(driver._hw.clone());
        // SAFETY: cdev is initialized and dev is reserved.
        to_result(unsafe { bindings::cdev_add(driver.cdev, driver.dev, 1) })?;
        driver.registered = true;
        pr_info!(
            "registered char device major={} minor=0\n",
            driver.dev >> bindings::MINORBITS
        );
        Ok(driver.into())
    }

    fn unbind(_dev: &platform::Device<Core>, this: Pin<&Self>) {
        unpublish();
        this._hw.shutdown();
    }
}

impl Drop for RgbDriver {
    fn drop(&mut self) {
        unpublish();
        self._hw.shutdown();
        // SAFETY: Release exactly the resources acquired during probe.
        unsafe {
            if self.registered {
                bindings::cdev_del(self.cdev);
            } else if !self.cdev.is_null() {
                bindings::kobject_put(ptr::addr_of_mut!((*self.cdev).kobj));
            }
            if self.dev != 0 {
                bindings::unregister_chrdev_region(self.dev, 1);
            }
        }
        pr_info!("module unloaded\n");
    }
}

unsafe extern "C" fn rgb_open(_inode: *mut bindings::inode, file: *mut bindings::file) -> c_int {
    let Some(owned) = RGB_HW.lock().as_ref().cloned() else {
        return ENODEV.to_errno();
    };
    // SAFETY: file is exclusively owned by open. release balances this Arc.
    unsafe { (*file).private_data = Arc::into_raw(owned).cast_mut().cast::<c_void>() };
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
            pr_info!("requested color r={} g={} b={}\n", red, green, blue);
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

unsafe extern "C" fn rgb_release(_inode: *mut bindings::inode, file: *mut bindings::file) -> c_int {
    // SAFETY: open transferred exactly one Arc reference into private_data.
    unsafe {
        drop(Arc::from_raw((*file).private_data.cast::<RgbDevice>()));
    }
    0
}

unsafe extern "C" fn rgb_ioctl(file: *mut bindings::file, cmd: c_uint, arg: c_ulong) -> c_long {
    // SAFETY: open owns a reference until release, including after platform unbind.
    let hw = unsafe { &*(*file).private_data.cast::<RgbDevice>() };
    let result = match cmd {
        RGB_IOCTL_FADE_START => {
            let mut bytes = [0u8; 8];
            let copy = UserSlice::new(UserPtr::from_addr(arg as usize), 8)
                .reader()
                .read_slice(&mut bytes);
            copy.and_then(|()| {
                hw.start(
                    u32::from_ne_bytes(bytes[..4].try_into().unwrap()),
                    u32::from_ne_bytes(bytes[4..].try_into().unwrap()),
                )
            })
        }
        RGB_IOCTL_FADE_WAIT => hw.wait(),
        _ => Err(ENOTTY),
    };
    match result {
        Ok(()) => 0,
        Err(e) => e.to_errno() as c_long,
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

#[pin_data]
struct DriverModule {
    #[pin]
    _driver: kernel::driver::Registration<platform::Adapter<RgbDriver>>,
}
impl kernel::InPlaceModule for DriverModule {
    fn init(module: &'static kernel::ThisModule) -> impl PinInit<Self, Error> {
        // SAFETY: Module initialization runs once before any probe/open.
        unsafe { RGB_HW.init() };
        try_pin_init!(Self {
            _driver <- kernel::driver::Registration::new(
                <Self as kernel::ModuleMetadata>::NAME, module,
            ),
        })
    }
}
module! {
    type: DriverModule,
    name: "rgb",
    authors: ["RGB driver contributors"],
    description: "Rust RGB hardware offload and completion IRQ driver",
    license: "GPL",
}
