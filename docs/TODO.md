# TODO — pending fixes from audit (2026-05-06)

## 1. Audit remaining `.unwrap()` calls for race-prone sites

The active-output and adjacent `space.element_location().unwrap()` panics
are fixed. ~155 unwraps remain across `src/`. Most are infallible by
construction:

- `seat.get_pointer().unwrap()` / `seat.get_keyboard().unwrap()` — capabilities
  added once at init, never removed.
- `Mutex::lock().unwrap()` — only fails on poisoning, which means we already
  panicked elsewhere.
- `parse::<u32>().unwrap()` on hardcoded constants in defaults / shaders.
- `data_map.get_or_insert(...).unwrap()` — just inserted.

But a handful are likely race-prone (window destroyed mid-handler, weak-ref
upgrade, post-commit invariants that hold _most_ of the time). Worth a
focused pass file-by-file: per call site decide "infallible-by-construction"
vs. "early-return on `None`". Don't mechanically rewrite — judgment per site.

Suggested order: handlers/ → input/ → render/ → state/ → backend/. Each file
is small; this is maybe a half-day of careful review, ideally a single
self-contained PR so reviewers can audit the judgment at every site.
