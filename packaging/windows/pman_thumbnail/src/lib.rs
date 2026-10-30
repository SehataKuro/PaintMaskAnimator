//! Explorer thumbnail handler for `.pman` projects.
//!
//! The app stores a small `thumbnail.png` as the first, uncompressed entry of
//! the project zip (FORMAT.md), so this only parses the local file header at
//! offset 0 and hands the PNG to WIC. The installer registers the class under
//! `.pman\ShellEx\{e357fccd-...}`; Windows runs it in an isolated host process.
#![cfg(windows)]
#![allow(non_snake_case)]

use std::cell::RefCell;
use std::ffi::c_void;
use std::sync::atomic::{AtomicUsize, Ordering};

use windows::core::{implement, Interface, Ref, Result, BOOL, GUID, HRESULT};
use windows::Win32::Foundation::{
    CLASS_E_CLASSNOTAVAILABLE, CLASS_E_NOAGGREGATION, E_FAIL, E_POINTER, E_UNEXPECTED, S_FALSE,
    S_OK,
};
use windows::Win32::Graphics::Gdi::{
    CreateDIBSection, DeleteObject, BITMAPINFO, BITMAPINFOHEADER, BI_RGB, DIB_RGB_COLORS, HBITMAP,
};
use windows::Win32::Graphics::Imaging::{
    CLSID_WICImagingFactory, GUID_WICPixelFormat32bppBGRA, IWICImagingFactory,
    WICBitmapDitherTypeNone, WICBitmapInterpolationModeFant, WICBitmapPaletteTypeCustom,
    WICDecodeMetadataCacheOnDemand,
};
use windows::Win32::System::Com::{
    CoCreateInstance, IClassFactory, IClassFactory_Impl, IStream, CLSCTX_INPROC_SERVER,
    STREAM_SEEK_SET,
};
use windows::Win32::UI::Shell::PropertiesSystem::{
    IInitializeWithStream, IInitializeWithStream_Impl,
};
use windows::Win32::UI::Shell::{
    IThumbnailProvider, IThumbnailProvider_Impl, WTSAT_ARGB, WTS_ALPHATYPE,
};

/// {E65E7337-4EA8-4AED-B618-247D06EAAC47} -- must match installer.iss.
const CLSID_PMAN_THUMBNAIL: GUID = GUID::from_u128(0xE65E7337_4EA8_4AED_B618_247D06EAAC47);
const THUMBNAIL_NAME: &[u8] = b"thumbnail.png";
/// The app shrinks it to 512 px; anything far bigger is not ours.
const MAX_THUMBNAIL_BYTES: u32 = 16 * 1024 * 1024;

static OBJECTS: AtomicUsize = AtomicUsize::new(0);

fn read_exact(stream: &IStream, buffer: &mut [u8]) -> Result<()> {
    let mut read = 0u32;
    unsafe {
        stream
            .Read(
                buffer.as_mut_ptr().cast(),
                buffer.len() as u32,
                Some(&mut read),
            )
            .ok()?;
    }
    if read as usize == buffer.len() {
        Ok(())
    } else {
        Err(E_FAIL.into())
    }
}

/// The PNG bytes of `thumbnail.png` if it is the first, stored zip entry.
fn read_thumbnail_png(stream: &IStream) -> Result<Vec<u8>> {
    unsafe { stream.Seek(0, STREAM_SEEK_SET, None)? };
    let mut header = [0u8; 30];
    read_exact(stream, &mut header)?;
    let u16_at = |i: usize| u16::from_le_bytes([header[i], header[i + 1]]);
    let u32_at =
        |i: usize| u32::from_le_bytes([header[i], header[i + 1], header[i + 2], header[i + 3]]);
    let flags = u16_at(6);
    let method = u16_at(8);
    let size = u32_at(18);
    let name_length = u16_at(26) as usize;
    let extra_length = u16_at(28) as u64;
    // Stored (method 0), sizes in the header (no data descriptor, flag bit 3).
    if u32_at(0) != 0x0403_4B50
        || method != 0
        || flags & 0x8 != 0
        || size == 0
        || size > MAX_THUMBNAIL_BYTES
        || name_length != THUMBNAIL_NAME.len()
    {
        return Err(E_FAIL.into());
    }
    let mut name = vec![0u8; name_length];
    read_exact(stream, &mut name)?;
    if name != THUMBNAIL_NAME {
        return Err(E_FAIL.into());
    }
    let offset = 30 + name_length as u64 + extra_length;
    unsafe { stream.Seek(offset as i64, STREAM_SEEK_SET, None)? };
    let mut png = vec![0u8; size as usize];
    read_exact(stream, &mut png)?;
    Ok(png)
}

/// Decode `png` and fit it inside `cx` x `cx` as a top-down 32-bit DIB.
fn png_to_hbitmap(png: &[u8], cx: u32) -> Result<HBITMAP> {
    unsafe {
        let factory: IWICImagingFactory =
            CoCreateInstance(&CLSID_WICImagingFactory, None, CLSCTX_INPROC_SERVER)?;
        let memory = factory.CreateStream()?;
        memory.InitializeFromMemory(png)?;
        let decoder = factory.CreateDecoderFromStream(
            &memory,
            std::ptr::null(),
            WICDecodeMetadataCacheOnDemand,
        )?;
        let frame = decoder.GetFrame(0)?;
        let (mut width, mut height) = (0u32, 0u32);
        frame.GetSize(&mut width, &mut height)?;
        if width == 0 || height == 0 || cx == 0 {
            return Err(E_FAIL.into());
        }
        let scale = (cx as f64 / width as f64)
            .min(cx as f64 / height as f64)
            .min(1.0);
        let out_w = ((width as f64 * scale).round() as u32).max(1);
        let out_h = ((height as f64 * scale).round() as u32).max(1);

        let scaler = factory.CreateBitmapScaler()?;
        scaler.Initialize(&frame, out_w, out_h, WICBitmapInterpolationModeFant)?;
        let converter = factory.CreateFormatConverter()?;
        converter.Initialize(
            &scaler,
            &GUID_WICPixelFormat32bppBGRA,
            WICBitmapDitherTypeNone,
            None,
            0.0,
            WICBitmapPaletteTypeCustom,
        )?;

        let info = BITMAPINFO {
            bmiHeader: BITMAPINFOHEADER {
                biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
                biWidth: out_w as i32,
                biHeight: -(out_h as i32), // top-down
                biPlanes: 1,
                biBitCount: 32,
                biCompression: BI_RGB.0,
                ..Default::default()
            },
            ..Default::default()
        };
        let mut bits: *mut c_void = std::ptr::null_mut();
        let bitmap = CreateDIBSection(None, &info, DIB_RGB_COLORS, &mut bits, None, 0)?;
        if bits.is_null() {
            let _ = DeleteObject(bitmap.into());
            return Err(E_FAIL.into());
        }
        let stride = out_w * 4;
        let pixels = std::slice::from_raw_parts_mut(bits.cast::<u8>(), (stride * out_h) as usize);
        if let Err(error) = converter.CopyPixels(std::ptr::null(), stride, pixels) {
            let _ = DeleteObject(bitmap.into());
            return Err(error);
        }
        Ok(bitmap)
    }
}

#[implement(IInitializeWithStream, IThumbnailProvider)]
struct ThumbnailProvider {
    stream: RefCell<Option<IStream>>,
}

impl ThumbnailProvider {
    fn new() -> Self {
        OBJECTS.fetch_add(1, Ordering::SeqCst);
        Self {
            stream: RefCell::new(None),
        }
    }
}

impl Drop for ThumbnailProvider {
    fn drop(&mut self) {
        OBJECTS.fetch_sub(1, Ordering::SeqCst);
    }
}

impl IInitializeWithStream_Impl for ThumbnailProvider_Impl {
    fn Initialize(&self, pstream: Ref<IStream>, _grfmode: u32) -> Result<()> {
        let mut slot = self.stream.borrow_mut();
        if slot.is_some() {
            return Err(E_UNEXPECTED.into());
        }
        *slot = Some(pstream.ok()?.clone());
        Ok(())
    }
}

impl IThumbnailProvider_Impl for ThumbnailProvider_Impl {
    fn GetThumbnail(
        &self,
        cx: u32,
        phbmp: *mut HBITMAP,
        pdwalpha: *mut WTS_ALPHATYPE,
    ) -> Result<()> {
        if phbmp.is_null() || pdwalpha.is_null() {
            return Err(E_POINTER.into());
        }
        let stream = self
            .stream
            .borrow()
            .clone()
            .ok_or(windows::core::Error::from(E_UNEXPECTED))?;
        let png = read_thumbnail_png(&stream)?;
        let bitmap = png_to_hbitmap(&png, cx)?;
        unsafe {
            *phbmp = bitmap;
            *pdwalpha = WTSAT_ARGB;
        }
        Ok(())
    }
}

#[implement(IClassFactory)]
struct Factory;

impl IClassFactory_Impl for Factory_Impl {
    fn CreateInstance(
        &self,
        punkouter: Ref<windows::core::IUnknown>,
        riid: *const GUID,
        ppvobject: *mut *mut c_void,
    ) -> Result<()> {
        if ppvobject.is_null() || riid.is_null() {
            return Err(E_POINTER.into());
        }
        unsafe { *ppvobject = std::ptr::null_mut() };
        if punkouter.is_some() {
            return Err(CLASS_E_NOAGGREGATION.into());
        }
        let provider: IThumbnailProvider = ThumbnailProvider::new().into();
        unsafe { provider.query(&*riid, ppvobject).ok() }
    }

    fn LockServer(&self, _flock: BOOL) -> Result<()> {
        Ok(())
    }
}

#[no_mangle]
extern "system" fn DllGetClassObject(
    rclsid: *const GUID,
    riid: *const GUID,
    ppv: *mut *mut c_void,
) -> HRESULT {
    if rclsid.is_null() || riid.is_null() || ppv.is_null() {
        return E_POINTER;
    }
    unsafe {
        *ppv = std::ptr::null_mut();
        if *rclsid != CLSID_PMAN_THUMBNAIL {
            return CLASS_E_CLASSNOTAVAILABLE;
        }
        let factory: IClassFactory = Factory.into();
        factory.query(&*riid, ppv)
    }
}

#[no_mangle]
extern "system" fn DllCanUnloadNow() -> HRESULT {
    if OBJECTS.load(Ordering::SeqCst) == 0 {
        S_OK
    } else {
        S_FALSE
    }
}
