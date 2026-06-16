# Performance — remaining work

The B1–B14 perf push shipped (see `git log`). What's left, in priority order.
Line numbers predate the push — re-verify on pickup. Profiling tooling:
[PROFILING.md](PROFILING.md).

## Blur (B5 + S1 + edge-fade)

The only substantive perf work left; deferred behind touchscreen + session
restoration (GH #125). Do the three together — one FBO/crop/mask rework.

**B5 — multi-output churn + FBO retention.**

- `src/render/mod.rs` — `blur_cache` is global but `compose_frame` retains per
  output: two outputs showing different blurred windows evict each other every
  frame → `BlurCache::new` re-allocates 3 window-sized textures + full recompute
  per blurred window per frame (~25 MB/frame at 1080p). Fix: retain against the
  union of blur requests across outputs.
- `src/render/blur.rs` — `blur_bg_fbo` is one slot keyed by size; different-sized
  outputs evict each other per frame (~33 MB alloc/free at 4K). Fix: key per
  output name, free in `remove_output`. Also drop the slot when no blur requests
  remain (currently retained forever after the last blurred window closes).

**S1 — blur fully recomputes every frame of a pan _or zoom_.** The cache hash
includes the window's screen-space position (`src/render/blur.rs` hashes
`window_rect.loc`), so any camera motion marks every blurred window dirty every
frame: full-output offscreen FBO repaint, crop, 2×radius Kawase passes, a second
full render for the alpha mask, masking pass. Screen-fixed blur on other monitors
also recomputes (`blur_camera_generation` is a global counter, `src/state/mod.rs`).
Fix options: translate the cached blur texture by the camera delta during
camera-only motion (blur is low-frequency); recompute at half rate while panning;
or key on (quantized position, behind-element commits).

**Edge-fade artifact.** Behind-content is cropped to exactly `win_size`, so the
Kawase kernel clamps at window edges and the blur tapers inward. Fix: blur a
radius-padded region and crop back — same surface as B5/S1. Cost caveat is at the
`blur` field in `docs/window-rules.md`.

## Lower-priority backlog (do only if a profile flags it)

- **B7** Gigapixel-TIFF decoder pool: no cancellation of stale in-flight decodes;
  blobs upload regardless of visibility and back up during fast pans
  (`src/render/tile_worker.rs`, `tile_chunks.rs`). Cancel unwanted requests; drop
  off-viewport responses; bound the queue. _Gigapixel-TIFF-wallpaper path only._
- **B11** Momentum auto-launch timer removed + re-inserted per gesture event
  (`src/state/animation.rs`, ~140-1000 Hz during pans). Keep one timer, reschedule.
- **B12** Output-outline strips rebuild pixel Vecs + `MemoryRenderBuffer` + fresh
  element ids per edge per frame (`src/render/mod.rs`), defeating damage tracking.
  _Multi-monitor only._ Cache per (output, color, size).
- **B13 / B15** Held repeatable key (`src/backend/udev.rs`) and the exec loading
  cursor (`src/input/actions.rs`, up to 5 s/launch) mark _all_ outputs dirty at
  refresh rate. Mark only the active/cursor output. _Single-output-marginal — same
  shape as the skipped B1; likely not worth it._
- **B14 (remaining half)** Pointer motion does up to ~6 sequential linear window
  scans with repeated `with_states` locks per event (`src/input/mod.rs`). Moderate;
  only scales with window count. (The `min_zoom`-per-pinch half shipped.)
- **Latent frame spikes** (config-dependent): synchronous shader-chunk bakes
  mid-frame (`src/render/shader_chunks.rs` — pre-bake a margin ring, pool the FBO);
  gigapixel-TIFF tile uploads up to ~25 ms/frame on the render thread
  (`src/render/mod.rs` — time-budget, or upload after `queue_frame`); shadow shader
  evaluates ERF quadrature over the full window+pad quad (`src/shaders/shadow.glsl`
  — early-out interior fragments).
- **niri patterns** not yet adopted: animations sampled at predicted
  presentation time (`niri/src/niri.rs:4601-4604` — small judder source vs
  driftwm's `Instant::now()`); on-demand VRR by window visibility
  (`niri/src/niri.rs:4720-4749` — gaming pass).
