# simulator/ - LDraw toolchain

Upstream AppImages for the two tools this project drives. They are kept here as
the source of truth for *which build* we use; the running copies are extracted
into `~/.local/opt/` (see [Install layout](#install-layout)).

| File | Version | Role |
| --- | --- | --- |
| `LeoCAD-Linux-25.09-x86_64.AppImage` | LeoCAD 25.09 (Sep 2025) | 3D model viewer/editor. Renders the model, exports parts lists and mesh formats. |
| `LPub3D-2.4.9-r86-x86_64.AppImage` | LPub3D 2.4.9 rev 86, build 4133 (Mar 2025) | Instruction-document generator. Turns a model into a paginated PDF/PNG booklet with step callouts and part-list icons. |

Driver scripts live in the project root:

| Script | Tool | What it produces |
| --- | --- | --- |
| `../run_leocad.sh` | LeoCAD | Model renders, per-step images, view sheets, CSV parts list, OBJ/HTML exports → `out/` |
| `../make_instructions.sh` | LPub3D | Instruction booklets → `out/instructions/` |

Both default to `data/test/40440-1.mpd` and both accept `--help`.

---

## Install layout

Ubuntu 24.04 has no `libfuse2`, so an AppImage cannot be mounted and run
directly. Both are **extracted** and launched through their `AppRun`:

```
~/.local/opt/leocad/     308 MB   extracted LeoCAD AppImage
~/.local/opt/lpub3d/     470 MB   extracted LPub3D AppImage
~/.local/bin/leocad   -> ~/.local/opt/leocad/AppRun
~/.local/bin/lpub3d   -> ~/.local/opt/lpub3d/AppRun
```

Parts libraries - nothing extra to download:

* **LeoCAD** ships a pre-indexed library at
  `~/.local/opt/leocad/usr/share/leocad/library.bin` (136 MB). Override with
  `--libpath` or `LDRAW_LIBRARY_PATH`.
* **LPub3D** ships `complete.zip` and unpacks the LDraw tree on first run to
  `~/.local/share/LPub3D Software/lpub3d24/ldraw` (703 MB). Override with
  `LDRAWDIR`. It also bundles the alternate renderers LDGLite, LDView and
  POV-Ray under `~/.local/opt/lpub3d/opt/lpub3d/3rdParty`.

## Reproducing the install

```bash
cd simulator
curl -LO https://github.com/trevorsandy/lpub3d/releases/download/continuous/LPub3D-2.4.9.86.4133_20250319-x86_64.AppImage
curl -L  -o lpub3d.sha512 https://github.com/trevorsandy/lpub3d/releases/download/continuous/LPub3D-2.4.9.86.4133_20250319-x86_64.AppImage.sha512
sha512sum -c lpub3d.sha512          # must print OK before going further
mv LPub3D-2.4.9.86.4133_20250319-x86_64.AppImage LPub3D-2.4.9-r86-x86_64.AppImage
chmod +x LPub3D-2.4.9-r86-x86_64.AppImage

mkdir -p ~/.local/opt/stage && cd ~/.local/opt/stage
"$OLDPWD/LPub3D-2.4.9-r86-x86_64.AppImage" --appimage-extract   # -> squashfs-root/
rm -rf ~/.local/opt/lpub3d && mv squashfs-root ~/.local/opt/lpub3d
ln -sf ~/.local/opt/lpub3d/AppRun ~/.local/bin/lpub3d
lpub3d --version
```

Same three steps for LeoCAD (`leozide/leocad` releases, asset
`LeoCAD-Linux-<ver>-x86_64.AppImage`).

The scripts fall back to the AppImages in this folder if `leocad`/`lpub3d` are
not on `PATH`, so a plain `chmod +x` install works too on a machine that *does*
have `libfuse2`.

## Command cheat sheet

Full option lists: `leocad --help`, `lpub3d --help`.

```bash
# LeoCAD - quick looks at the model
leocad model.mpd                                     # GUI
leocad model.mpd --image shot.png -w 1600 -h 1200 --viewpoint home
leocad model.mpd --image step.png -f 1 -t 3          # writes step01.png ...
leocad model.mpd --submodel "40440 - Puppy.ldr" --image puppy.png
leocad model.mpd -csv parts.csv

# LPub3D - instruction documents (console mode = no GUI)
lpub3d -ns -ll -pe -o pdf -of /abs/path/out.pdf  /abs/path/model.mpd
lpub3d -ns -ll -pe -o png -od /abs/path/outdir   /abs/path/model.mpd
lpub3d -ns -ll -pe -o png -r 1-10 -od /abs/dir   /abs/path/model.mpd
lpub3d -ns -ll -pe -o bl-xml                     /abs/path/model.mpd
lpub3d -ns -ll -emc meta-commands.html           # LPUB meta-command reference
```

Flags worth knowing: `-ll` load the LEGO library (**required** in console
mode), `-ns` no duplicate stdout logging, `-p <renderer>` pick
native/ldglite/ldview/povray, `-fs` fade prior steps, `-hs` highlight new
parts, `-ss <0-7>` stud style, `-x` clear caches.

## Gotchas found on this machine

Verified against LPub3D 2.4.9-r86 on Ubuntu 24.04 with the model in
`data/test/`:

* **Use the r86 continuous build, not the v2.4.9 stable release.** Stable
  segfaults on every console export (`Gui::exportAs` → `QWidget::setWindowTitle`
  on the main window that console mode never creates). r86 exports all 115
  pages cleanly.
* **A real X display is required.** Only the `xcb` Qt platform plugin is
  bundled, so `QT_QPA_PLATFORM=offscreen` aborts. Headless/CI needs
  `xvfb-run -a lpub3d ...` (`xvfb` is not installed here). LeoCAD, by contrast,
  renders fine with no display.
* **`-od` / `-of` must be absolute paths.** A relative one is silently accepted
  and every page then fails with "Cannot open device for writing".
* **`-o csv` and `-o bl-xml` ignore both `-od` and `-of`** and always write
  `<model>-export.csv` / `.xml` beside the source file. `make_instructions.sh`
  works around this by running against a copy under `out/instructions/.work/`
  and moving the result.
* **`-o htmlsteps` segfaults.** Use LeoCAD's `-html` for a browsable page, or
  export PNG pages.
* **The default page background is pink.** It is not a preference - only the
  meta command `0 !LPUB PAGE BACKGROUND COLOR "#FFFFFF"` in the model changes
  it. `make_instructions.sh` injects it (`BG=none` opts out).
* **LPub3D writes beside the model file**: a `LPub3D/` render cache plus
  `stdout-*`/`stderr-*` renderer logs. Keep them out of `data/` by working on a
  copy; `../make_instructions.sh clean` removes strays.

## Where the outputs go

Both scripts write one folder per model, keyed on the model's file name, so
rendering a second model never overwrites the first:

```
out/<model>/                    LeoCAD: render.png, views/, steps/, csv, obj, html
out/instructions/<model>/       LPub3D: <model>.pdf, parts csv, BrickLink xml
out/instructions/<model>/png/   one PNG per instruction page (1240x1754, 150 DPI)
out/instructions/<model>/.work/ working copy + LPub3D render cache (safe to delete)
out/instructions/meta-commands.html   model-independent LPUB meta reference
```
