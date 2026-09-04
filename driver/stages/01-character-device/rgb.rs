// SPDX-License-Identifier: GPL-2.0

//! Minimal RGB character device.

use core::{mem::MaybeUninit, ptr};

use kernel::{
    alloc::KBox,
    bindings, c_str,
    error::{code::EINVAL, to_result},
    ffi::{c_char, c_int, c_void},
    prelude::*,
    types::Opaque,
    uaccess::{UserPtr, UserSlice},
};

const MAX_INPUT_SIZE: usize = 16;

module! {
    type: RgbModule,
    name: "rgb",
    authors: ["RGB driver contributors"],
    description: "Minimal Rust RGB character device",
    license: "GPL",
}

struct RgbModule {
    dev: bindings::dev_t,
    cdev: KBox<Opaque<bindings::cdev>>,
    _fops: KBox<bindings::file_operations>,
}

// SAFETY: The cdev and file-operations allocations remain at stable addresses
// for the whole registration. They are only accessed through the kernel's cdev
// and VFS synchronization, and `file_operations::owner` pins this module while
// an open file can call one of its callbacks.
unsafe impl Send for RgbModule {}
// SAFETY: See the `Send` implementation above; Rust code never mutates or
// dereferences these C-owned objects concurrently.
unsafe impl Sync for RgbModule {}

impl kernel::Module for RgbModule {
    fn init(module: &'static ThisModule) -> Result<Self> {
        pr_info!("module loaded\n");

        // The VFS keeps this table after open(), so it must have a stable address.
        let mut raw_fops: bindings::file_operations =
            unsafe { MaybeUninit::zeroed().assume_init() };
        raw_fops.owner = module.as_ptr();
        raw_fops.open = Some(rgb_open);
        raw_fops.write = Some(rgb_write);
        let fops = KBox::new(raw_fops, GFP_KERNEL)?;

        // cdev_init() initializes this opaque allocation in place.
        let cdev = KBox::new(Opaque::<bindings::cdev>::uninit(), GFP_KERNEL)?;
        let cdev_ptr = cdev.get();
        let mut dev: bindings::dev_t = 0;

        // SAFETY: `dev` is writable, the range contains one minor, and the name is static.
        to_result(unsafe {
            bindings::alloc_chrdev_region(&mut dev, 0, 1, c_str!("rgb").as_char_ptr())
        })?;

        // SAFETY: Both allocations have stable addresses and outlive the registration.
        unsafe { bindings::cdev_init(cdev_ptr, ptr::from_ref(&*fops)) };

        // SAFETY: The device number is allocated and `cdev_ptr` was initialized above.
        let add_result = to_result(unsafe { bindings::cdev_add(cdev_ptr, dev, 1) });
        if let Err(error) = add_result {
            // cdev_init() owns a kobject reference even when cdev_add() fails.
            // SAFETY: The cdev was initialized and is no longer used after this call.
            unsafe { bindings::kobject_put(ptr::addr_of_mut!((*cdev_ptr).kobj)) };
            // SAFETY: This exact range was successfully allocated above.
            unsafe { bindings::unregister_chrdev_region(dev, 1) };
            return Err(error);
        }

        let major = (dev >> bindings::MINORBITS) as u32;
        pr_info!("registered char device major={} minor=0\n", major);

        Ok(Self {
            dev,
            cdev,
            _fops: fops,
        })
    }
}

impl Drop for RgbModule {
    fn drop(&mut self) {
        // SAFETY: A successful init registered this cdev exactly once.
        unsafe { bindings::cdev_del(self.cdev.get()) };
        // SAFETY: A successful init allocated this exact one-device range.
        unsafe { bindings::unregister_chrdev_region(self.dev, 1) };
        pr_info!("module unloaded\n");
    }
}

unsafe extern "C" fn rgb_open(
    _inode: *mut bindings::inode,
    _file: *mut bindings::file,
) -> c_int {
    pr_info!("device opened\n");
    0
}

unsafe extern "C" fn rgb_write(
    _file: *mut bindings::file,
    user_buffer: *const c_char,
    count: usize,
    _offset: *mut bindings::loff_t,
) -> isize {
    match copy_and_parse(user_buffer, count) {
        Ok((red, green, blue)) => {
            pr_info!(
                "requested color r={} g={} b={}\n",
                red,
                green,
                blue
            );
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
