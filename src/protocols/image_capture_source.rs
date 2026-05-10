//! Thin layer over smithay's `image_capture_source` — only the per-source
//! payload (`SourceKind`) lives here. All dispatch boilerplate, the
//! `ImageCaptureSourceState`, and the Output / Toplevel manager states come
//! from smithay directly.

use smithay::output::Output;
use smithay::reexports::wayland_server::protocol::wl_surface::WlSurface;
use smithay::utils::{Physical, Size};

/// Compositor-side payload stashed in `ImageCaptureSource::user_data()` at
/// create time. The handlers in `handlers/mod.rs` insert one of these for
/// every source the client creates; the renderer matches on it to decide what
/// to draw into the buffer.
///
/// `initial_size` for toplevels is captured at source-creation time so the
/// session can advertise `buffer_size` without needing space access. Resizes
/// during capture are not propagated yet.
#[derive(Debug, Clone)]
pub enum SourceKind {
    Output(Output),
    Toplevel {
        surface: WlSurface,
        initial_size: Size<i32, Physical>,
    },
    /// Toplevel handle was already dead by the time the source was created,
    /// or its surface vanished. Capture frames for this source fail.
    Destroyed,
}
