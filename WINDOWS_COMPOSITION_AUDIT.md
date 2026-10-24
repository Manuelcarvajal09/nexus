# Windows composition audit — in progress

This audit supersedes the exterior-frame acceptance claim in
`WINDOWS_LAYOUT_REVIEW.md`. That earlier change improved inner layout, but did
not establish a single native silhouette. No visual closure is claimed here.

## Preserved baseline

- Branch `windows-native`; existing tracked and untracked work retained.
- Full suite before this intervention: **609 passed in 13.83s**.
- Runtime: Windows build 26300, Qt 6.12.0.
- Read-only native enumeration found habitual Nexus GUI PID 5916, not the old
  report's PID 13252. No habitual process interrupted.
- Source/test/tool backup (27 files, SHA-256 manifest and tracked diff):
  `C:/Users/Admin/AppData/Local/Temp/opencode/nexus-composition-source-20261024T034525Z`.
- No clipboard, hook, shortcut, engine, shared Linux or user-data changes allowed.

## Source facts, not yet visual conclusions

`WindowsGlassSurface` paints a rounded path at 8.5 logical pixels from its
widget edges, radius 22. It draws shadow strokes 14/10/5 wide, a light rim,
and a second dark inner edge. Both Windows top-level windows clear their full
client backing store in native mode; in fallback they fill the complete client
with the dark panel color. Outer window layouts have zero margins.

The non-layered native backend extends the DWM frame throughout the client
(`-1`) and enables Desktop Acrylic across that HWND. Clearing Qt alpha is not
equivalent to cutting a hole through that native material. Whether the exposed
strip is Acrylic, fallback, shadow or their combination must be demonstrated
by the controlled screen probes.

The legacy `windows_platform.apply_liquid_glass_backdrop` contains an explicit
DONOTROUND call but has **no callers in src**. The current backend intentionally
does not set corner preference 33, based on previous Acrylic failures.

Offscreen alpha measurement (diagnostic only): at the middle of the left edge,
the Qt native material has alpha 0 at x=0, black alpha 4–41 at x=1–7, and the
rim/fill at x=8 onward. This confirms the reserved strip contains painted
shadow strokes; it does not prove desktop transparency.

## Stack baseline geometry (offscreen; not visual acceptance)

For 0, 1, 2, 3, 6 and 21 synthetic text items, the window remains 620×590
logical pixels. With two items, the one remaining row occupies a 168px list
viewport. With six items scrolling already begins despite the fixed outer
height. A content-measured Windows-only sizing policy is needed after the
composition fix is proven.

## Evidence still required

- A–E isolated native composition comparisons and corner crops.
- A proven single-contour candidate retaining Acrylic active and inactive.
- Real before/after app-window captures for all requested views/counts.
- Feasible DPI checks, regressions, and separately authorized habitual reload.

## First native isolation result

Evidence directory:
`C:/Users/Admin/AppData/Local/Temp/opencode/nexus-composition-20261023-214652-805776`.
Contains A–E/F0/F across two backgrounds, actual active/inactive states,
four corner crops per capture, native-style validation and contact sheets.

Confirmed by real pixels:

- A: native Acrylic responds to background changes throughout the HWND.
- B: an inset rounded tint introduces a distinct second silhouette.
- C/D/E: shadow strokes, rim and inner edge successively accentuate the ring.
- The nominal outside strip remains native material, not clear desktop.
- F0: full-client rounded paint removes the inset ring but retains a different
  native outer radius behind its 22px Qt curve; this is not full acceptance.
- F: `SetWindowRgn` makes active material background-independent grey despite
  DWM readback 3, and produces inactive title artifacts. **Rejected**.
- All these baseline profiles become grey/background-independent when inactive.
  This pre-existing behavior also fails the user's desired inactive Acrylic
  criterion; it must not be hidden by reporting only active screenshots.

Next experiment: let DWM own the only outline and native corner silhouette,
with rectangular full-client tint rather than a second Qt rounded body.

### Verification harness correction

The old layout probe did not assign its controller to
`window._backdrop_controller`, unlike the real application. `WindowsMotion`
uses that assignment to avoid whole-HWND opacity animation (`WS_EX_LAYERED`).
The probe now assigns it and rejects final layered captures. This changes only
the verifier, not application behavior. Older layout captures are not evidence
of production-profile Acrylic acceptance.

## Native single-contour candidate: cross-application result

Final evidence (supersedes earlier same-process H observations):
`C:/Users/Admin/AppData/Local/Temp/opencode/nexus-composition-20261023-215752-232093`.

- G paints the existing tint throughout the client, without any Qt rounded
  body, inset, shadow strokes or inner edge. DWM owns the only silhouette and
  thin border. All four border midpoint pixels equal RGB `#465363`.
- All 72 mirrored corner footprints match. Native antialias cutouts are the
  same at all four corners. No opaque rectangular corner background, title
  artifacts or concentric perimeter appears in these bounded probe captures.
- 304,800 sampled exterior pixels equal the corresponding owned backdrop:
  no visible reserved outside band or shadow in that tested native profile.
- G retains active Acrylic: changing background palette changes center from
  `#5a6370` to `#6d3e78` with smoothed underlying stripes.
- With another owned APPLICATION foreground, it remains `#4a4b4c` across both
  backgrounds. Foreground PID differs from pane PID; Qt confirms both window
  inactivity and ApplicationInactive. Background changes while continuously
  inactive, without hiding/re-showing/reactivating the pane, give the same grey.
- H, intercepting WM_NCACTIVATE while allowing real focus transfer, also fails
  the cross-process test. A history-dependent same-process success from an
  earlier run is not acceptance and must not be shipped.

No application rendering, padding, palette, functional code or habitual process
was changed. **The combined single-contour + inactive-Acrylic requirement is
not met by the tested DWMWA_SYSTEMBACKDROP_TYPE route.** This is an observed
limit of these tested profiles, not proof that every Windows composition API
is incapable of it.

### Supported next alternative (research, not visual proof)

Microsoft PowerToys implements an AlwaysActiveDesktopAcrylicBackdrop using
DesktopAcrylicController + SystemBackdropConfiguration.IsInputActive=true.
This is material policy; it does not activate the native window or move focus.
Its demonstrated host is WinUI, not Qt. For Win32, SetTarget(WindowId,
CompositionTarget) is documented, requires DispatcherQueue and
DWMWA_USE_HOSTBACKDROPBRUSH. Qt/native target integration and coexistence with
its backing store remain unproven here.

References:
- https://github.com/microsoft/PowerToys/blob/main/src/common/Common.UI.Controls/Backdrops/AlwaysActiveDesktopAcrylicBackdrop.cs
- https://learn.microsoft.com/en-us/windows/windows-app-sdk/api/winrt/microsoft.ui.composition.systembackdrops.desktopacryliccontroller.settarget
- https://learn.microsoft.com/en-us/windows/windows-app-sdk/api/winrt/microsoft.ui.composition.systembackdrops.systembackdropconfiguration.isinputactive

Alternatives to discuss before implementation:
1. Isolated Windows App SDK controller/native bridge feasibility probe: supported
   policy, no focus stealing; new local native dependencies, Qt compatibility
   and deployment/lifecycle work. Preferred research direction, not a fix yet.
2. Keep DWM native candidate G and accept inactive fallback: smallest change,
   clean contour; explicitly fails the required inactive Acrylic behavior.
3. WinUI/composition host redesign: closer to first-party implementation;
   much larger architecture and embedding risk, outside a small visual repair.

No automatic choice or product implementation is authorized by these findings.

## Official API references consulted

- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowrgn
  (`SetWindowRgn` clips the full native window; coordinates are window-relative;
  Windows owns HRGN after success, caller deletes it only on failure).
- https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/ui/apply-rounded-corners
  (custom regions/per-pixel layered windows do not get automatic DWM rounding;
  a DWM corner preference is a hint, not a guarantee).

## 2026-10-24 controlled integration result — App SDK direct target blocked

A multi-HWND bridge and a full isolated clone were built and tested
(`C:/Users/Admin/AppData/Local/Temp/opencode/nexus-appsdk-integration-20261024T044031Z`).
The bridge itself passed: three independent HWNDs, wrong-thread rejection,
independent detach/re-attach, duplicate attach, dead-HWND cleanup, queue
shutdown and 11 deterministic failure injections. The clone suite passed 629
tests.

However, on real Nexus History/Stack/Pinboards, applying
`DesktopAcrylicController.SetTarget` to a Qt HWND **replaces the Qt content**
with the composition visual tree. The same window captured before attach had
3521 red content samples; after attach 0; after detach 3521 again. Widgets and
right-side Pinboards headings still existed in the Qt tree but were not
composited. Adding/omitting the required visual root was not the cause; the
root was present. The API is for composition-rendered content, not a
backdrop-only layer behind arbitrary Qt/GDI widgets.

The DWM control kept all Nexus content and active Acrylic, but inactive state
remained opaque gray. The legacy `SetWindowCompositionAttribute` accent probe
also kept content but did not show background-responsive Acrylic in this test.

Therefore the App SDK direct-target integration is **rejected** and no
production source was changed. Options and the two-window feasibility path are
documented in the integration report. Do not present this route as production-
ready without solving content composition.
