import math
import os
import sys
import tkinter as tk
import webbrowser
import xml.etree.ElementTree as ET
from tkinter import filedialog, messagebox, ttk

import numpy as np

try:
    import svgpathtools
except ImportError:
    sys.stderr.write(
        "svgpathtools is required. Install with: pip install svgpathtools\n"
    )
    raise

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.patches
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


HALF_BOX = 1.45 
EPS = 1e-4 #Dedupe


def _iter_subpaths(path):
    try:
        subs = path.subpaths()
    except AttributeError:
        return [path]
    if not isinstance(subs, (list, tuple)):
        try:
            subs = list(subs)
        except TypeError:
            return [path]
    if not subs:
        return [path]
    return list(subs)


def parse_paths_from_svg(svg_path):
    
    #Parse SVG file and return a list of svgpathtools.Path objects.
    
    try:
        tree = ET.parse(svg_path)
    except ET.ParseError as exc:
        raise ValueError(f"SVG file is not valid XML: {exc}")
    except OSError as exc:
        raise ValueError(f"Could not read SVG file: {exc}")

    root = tree.getroot()
    ns_prefix = ""
    if root.tag.startswith("{"):
        ns_uri = root.tag[1 :].split("}", 1)[0]
        ns_prefix = "{" + ns_uri + "}"

    parsed = []
    for path_elem in root.iter(f"{ns_prefix}path"):
        d = path_elem.get("d")
        if not d or not d.strip():
            continue
        try:
            p = svgpathtools.parse_path(d)
        except Exception as exc:
            print(
                f"Warning: skipping malformed <path> 'd' attribute: {exc}",
                file=sys.stderr,
            )
            continue
        if p is None:
            continue
        parsed.append(p)

    return parsed


def longest_closed_subpath(paths):
    #Find longest closed subpath from list of svgpathtools paths.

    candidates = []
    for path in paths:
        for sub in _iter_subpaths(path):
            try:
                closed = sub.isclosed()
            except Exception:
                closed = False
            if not closed:
                continue
            try:
                length = sub.length()
            except Exception:
                length = 0.0
            if length <= 0:
                continue
            candidates.append((length, sub))
    if not candidates:
        raise ValueError(
            "No closed paths found in the SVG. The tool needs a closed "
            "polygon (since the table has no overlapping edges or voids)."
        )
    candidates.sort(key=lambda item: -item[0])
    return candidates[0][1]


def sample_subpath(subpath, max_segment_length):
    #Sample every segment into points so adjacent samples are at most `max_segment_length` apart in the SVG's defined coordinates.

    pts = []
    for seg in subpath:
        try:
            seg_len = seg.length()
        except Exception:
            seg_len = 0.0
        if seg_len == 0:
            continue
        n = max(1, int(math.ceil(seg_len / max_segment_length)))
        for i in range(n):
            t = i / float(n)
            p = seg.point(t)
            pts.append((float(p.real), float(p.imag)))
        p = seg.point(1.0)
        pts.append((float(p.real), float(p.imag)))
    return pts


def normalize_to_box(points, half_size=HALF_BOX):
    #scale to fit within table
    if not points:
        return []
    pts = np.array(points, dtype=float)
    if pts.shape[0] == 0:
        return []
    # try to get the right point order (not sure exactly how y'all do this, so I'm just guessing. Can just be fixed in the online generator if it complains)
    pts[:, 1] = -pts[:, 1]
    min_x, min_y = pts.min(axis=0)
    max_x, max_y = pts.max(axis=0)
    width = max_x - min_x
    height = max_y - min_y
    if width <= 0 or height <= 0:
        return [(float(x), float(-y)) for x, y in points]
    #adjust scale to try and preserve aspect ratio
    scale = (2.0 * half_size) / max(width, height)
    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0
    pts[:, 0] = (pts[:, 0] - cx) * scale
    pts[:, 1] = (pts[:, 1] - cy) * scale
    return [(float(x), float(y)) for x, y in pts]


def dedupe_all(points, eps=EPS):
    #deduplicate vertices
    if not points:
        return []
    out = [points[0]]
    for p in points[1:]:
        is_dup = False
        for kept in out:
            if abs(p[0] - kept[0]) <= eps and abs(p[1] - kept[1]) <= eps:
                is_dup = True
                break
        if not is_dup:
            out.append(p)
    return out


def _signed_area(points):
    if len(points) < 3:
        return 0.0
    area = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return area / 2.0


    #point order for compatibility with online generator
def order_counterclockwise_from_topleft(points):
    if len(points) <= 2:
        return points[:]

    result = list(points)
    
    if _signed_area(result) < 0:
        result.reverse()
  
    best_idx = 0
    bx, by = result[0]
    for i in range(1, len(result)):
        x, y = result[i]
        if y > by or (abs(y - by) <= EPS and x < bx):
            best_idx = i
            bx, by = x, y
    return result[best_idx:] + result[:best_idx]


def simplify_collinear(points, eps=EPS):
    #remove excess vertices if colinear
    if len(points) <= 3:
        return points[:]

    out = [points[0]]
    for i in range(1, len(points) - 1):
        prev = out[-1] 
        curr = points[i]
        nxt = points[i + 1]
        ax = curr[0] - prev[0]
        ay = curr[1] - prev[1]
        bx = nxt[0] - prev[0]
        by = nxt[1] - prev[1]
        cross = abs(ax * by - ay * bx)
        dot = ax * (nxt[0] - curr[0]) + ay * (nxt[1] - curr[1])

        if cross > eps or dot < 0:
            out.append(curr)

    out.append(points[-1])
    return out


def estimate_normalization_scale(subpath):
    #debug output, checking scaling values, not output in ui
    pts = []
    for seg in subpath:
        pts.append((float(seg.start.real), float(seg.start.imag)))
        pts.append((float(seg.end.real), float(seg.end.imag)))
    if not pts:
        return None
    arr = np.array(pts, dtype=float)
    arr[:, 1] = -arr[:, 1]  # flip
    w = arr[:, 0].max() - arr[:, 0].min()
    h = arr[:, 1].max() - arr[:, 1].min()
    max_dim = max(w, h)
    if max_dim <= 0:
        return None
    return (2.0 * HALF_BOX) / max_dim


def _otsu_threshold(gray):
    #convert raster image to grascale and 2 bit color. I have literally no idea how this works
    #I just copied some code online for this and it magically works I think
    hist, _ = np.histogram(gray.ravel(), bins=256, range=(0, 1))
    total = gray.size
    hist = hist.astype(np.float64)

    sum_total = np.dot(np.arange(256), hist)
    sum_b = 0.0
    w_b = 0.0
    var_max = 0.0
    threshold = 0

    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break

        sum_b += t * hist[t]
        mean_b = sum_b / w_b
        mean_f = (sum_total - sum_b) / w_f

        var_between = w_b * w_f * (mean_b - mean_f) ** 2

        if var_between > var_max:
            var_max = var_between
            threshold = t

    return threshold / 255.0


RASTER_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif'}


def _gaussian_blur(img, sigma=1.5):
    #apply blur to soften edges when converting raster images
    size = int(2 * sigma * 3 + 1)
    if size % 2 == 0:
        size += 1
    k = np.arange(size) - size // 2
    kernel = np.exp(-k**2 / (2 * sigma**2))
    kernel /= kernel.sum()
    pad = size // 2

    from numpy.lib.stride_tricks import sliding_window_view

    padded = np.pad(img, pad, mode='edge')
    windows_h = sliding_window_view(padded, size, axis=1)[pad:-pad]
    blurred = np.sum(
        windows_h * kernel[np.newaxis, np.newaxis, :], axis=2
    )

    padded_v = np.pad(blurred, pad, mode='edge')
    windows_v = sliding_window_view(padded_v, size, axis=0)
    return np.sum(
        windows_v * kernel[np.newaxis, np.newaxis, :], axis=2
    )


def _smooth_contour(vertices, sigma=1.5):
    if len(vertices) < 6:
        return vertices

    size = int(2 * sigma * 3 + 1)
    if size % 2 == 0:
        size += 1
    k = np.arange(size) - size // 2
    weights = np.exp(-k**2 / (2 * sigma**2))
    weights /= weights.sum()
    pad = size // 2

    n = len(vertices)
    padded = np.vstack([vertices[-pad:], vertices, vertices[:pad]])

    from numpy.lib.stride_tricks import sliding_window_view
    windows = sliding_window_view(padded, size, axis=0)  # (n, 2, size)
    return np.sum(
        windows * weights[np.newaxis, np.newaxis, :], axis=2
    )


def _raster_to_svg_path(image_path):
    from matplotlib import pyplot as plt

    img = plt.imread(image_path)

    if img.ndim == 3 and img.shape[2] == 4:
        rgb = img[:, :, :3]
        alpha = img[:, :, 3:4]
        img_rgb = rgb * alpha + (1.0 - alpha)
    elif img.ndim == 3:
        img_rgb = img[:, :, :3]
    else:
        img_rgb = np.stack([img] * 3, axis=2)

    gray = np.mean(img_rgb, axis=2)

    blurred = _gaussian_blur(gray, sigma=1.5)

    threshold = _otsu_threshold(blurred)
    binary = blurred < threshold

    h, w = gray.shape
    corners = [
        gray[0, 0],
        gray[0, w - 1],
        gray[h - 1, 0],
        gray[h - 1, w - 1],
    ]
    mean_corner = sum(corners) / len(corners)

    if mean_corner > threshold:
        pass
    else:
        binary = ~binary

    fig = plt.figure()
    ax = fig.add_subplot(111)
    cs = ax.contour(binary, levels=[0.5])
    plt.close(fig)
    #trace shape from converted raster image
    paths = []
    for segs in cs.allsegs:
        for seg in segs:
            if len(seg) >= 3:
                paths.append(seg)

    if not paths:
        raise ValueError(
            "Can't detect a valid table shape in the image. "
            "If this continues, use an image editor to raise "
            "the contrast so that the shape is more clear."
        )

    longest = max(paths, key=len)

    smoothed = _smooth_contour(longest, sigma=1.5)

    parts = [f"M {smoothed[0][0]:.4f},{smoothed[0][1]:.4f}"]
    for p in smoothed[1:]:
        parts.append(f"L {p[0]:.4f},{p[1]:.4f}")
    parts.append("Z")

    return " ".join(parts)


def convert_image_to_polygon(image_path, target_vertices):
    #translate converted raster image to polygon

    ext = os.path.splitext(image_path)[1].lower()

    if ext in RASTER_EXTENSIONS:
        d = _raster_to_svg_path(image_path)
        try:
            main = svgpathtools.parse_path(d)
        except Exception as exc:
            raise ValueError(
                f"Failed to parse traced path from image: {exc}"
            )
        if main is None:
            raise ValueError(
                "Failed to extract a valid path from the image. "
                "Try a different image with a clearer shape."
            )
    elif ext == '.svg':
        paths = parse_paths_from_svg(image_path)
        if not paths:
            raise ValueError(
                "No <path> elements with a parseable 'd' attribute were "
                "found in the SVG. The file may be corrupted."
            )
        main = longest_closed_subpath(paths)
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    try:
        arc_length_svg = main.length()
    except Exception:
        arc_length_svg = 0.0
    if arc_length_svg <= 0:
        raise ValueError("Path has zero arc length.")
    if not isinstance(target_vertices, (int, float)) or target_vertices <= 0:
        raise ValueError(
            f"target_vertices must be a positive number, got {target_vertices!r}"
        )

    scale = estimate_normalization_scale(main)
    if not scale or scale <= 0:
        raise ValueError("Path has zero or degenerate bounding box.")

    segment_count = max(1, sum(1 for _ in main))
    step_svg = arc_length_svg / max(1.0, target_vertices - segment_count)

    raw_points = sample_subpath(main, step_svg)
    normalized = normalize_to_box(raw_points, half_size=HALF_BOX)
    cleaned = dedupe_all(normalized, eps=EPS)
    # Simplify collinear BEFORE angle-sorting, so consecutive points
    # are still edge-adjacent along the SVG path (angle sorting breaks
    # adjacency for non-convex shapes).
    simplified = simplify_collinear(cleaned)
    ordered = order_counterclockwise_from_topleft(simplified)

    metadata = {
        "raw_vertices": len(raw_points),
        "final_vertices": len(ordered),
        "scale": scale,
        "step_svg": step_svg,
        "target_vertices": target_vertices,
        "arc_length_svg": arc_length_svg,
        "segment_count": segment_count,
    }
    return ordered, metadata


def format_polygon_output(polygon, decimals=4):
    return "\n".join(f"{p[0]:.{decimals}f},{p[1]:.{decimals}f}" for p in polygon)


# ---------------------------------------------------------------------------
# Tkinter UI
# ---------------------------------------------------------------------------

class SvgToSpooker:
    def __init__(self, root):
        self.root = root
        self.root.title("Image to Spooker Table Converter")
        self.root.geometry("1100x720")
        self.root.minsize(800, 520)

        self.filepath = None
        self.polygon = []
        self.metadata = {}
        self.status_var = tk.StringVar(
            value="Ready. Use the file picker to load an image."
        )

        self._build_ui()

    #ui stuff

    def _setup_dark_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        # Base colors
        bg = "#0d1117"
        accent = "#010409"
        fg = "#e6edf3"
        sel = "#580aff"
        entry_bg = "#010409"
        disabled_fg = "#484f58"
        border = "#30363d"

        style.configure(".", background=bg, foreground=fg, fieldbackground=accent,
                        selectbackground=sel, selectforeground=fg)
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("TLabelframe", background=bg, foreground=fg, bordercolor=border)
        style.configure("TLabelframe.Label", background=bg, foreground=fg)
        style.configure("TButton", background=accent, foreground=fg, bordercolor=border,
                        focuscolor="none", lightcolor=accent, darkcolor=accent)
        style.map("TButton",
                   background=[("active", "#1c2128"), ("disabled", accent)],
                   foreground=[("disabled", disabled_fg)])
        style.configure("TEntry", fieldbackground=entry_bg, foreground=fg,
                        bordercolor=border)
        style.map("TEntry", fieldbackground=[("focus", "#161b22")])
        style.configure("TPanedwindow", background=bg, bordercolor=border)
        style.configure("Vertical.TScrollbar", background=accent, bordercolor=border,
                        arrowcolor=fg)
        style.map("Vertical.TScrollbar",
                   background=[("active", "#1c2128")])
        style.configure("Horizontal.TScale", background=bg, troughcolor=border,
                        lightcolor=border, darkcolor=border, bordercolor=border)

    def _build_ui(self):
        self._setup_dark_style()

        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill=tk.BOTH, expand=True)

        controls = ttk.LabelFrame(outer, text="Inputs", padding=8)
        controls.pack(fill=tk.X)

        #file picker
        ttk.Button(controls, text="Select image\u2026",
                   command=self._pick_file).grid(row=0, column=0, padx=(0, 8))
        self.file_label = ttk.Label(controls, text="(no file selected)")
        self.file_label.grid(row=0, column=1, columnspan=3, sticky="w")

        #resolution + buttons
        ttk.Label(controls, text="Resolution:").grid(row=1, column=0, sticky="e",
                                                     pady=(8, 0))
        self.resolution_var = tk.StringVar(value="50")
        self.resolution_int = tk.IntVar(value=50)
        res_entry = ttk.Entry(controls, textvariable=self.resolution_var,
                              width=8)
        res_entry.grid(row=1, column=1, sticky="w", pady=(8, 0), padx=(0, 4))
        res_slider = ttk.Scale(controls, from_=3, to=200, orient=tk.HORIZONTAL,
                               variable=self.resolution_int,
                               command=self._resolution_slider_moved)
        res_slider.grid(row=1, column=2, sticky="ew", pady=(8, 0), padx=(0, 8))
        self.resolution_var.trace_add("write", self._resolution_entry_typed)

        self.open_gen_btn = ttk.Button(
            controls, text="Open Online Generator",
            command=self._open_generator,
        )
        self.open_gen_btn.grid(row=0, column=4, rowspan=2, padx=(4, 4),
                                sticky="ns")
        self.convert_btn = ttk.Button(controls, text="Convert",
                                      command=self._convert,
                                      state=tk.DISABLED)
        self.convert_btn.grid(row=0, column=5, rowspan=2, padx=(4, 4),
                              sticky="ns")
        self.copy_btn = ttk.Button(controls, text="Copy to clipboard",
                                    command=self._copy_output,
                                    state=tk.DISABLED)
        self.copy_btn.grid(row=0, column=6, rowspan=2, padx=(4, 0),
                            sticky="ns")

        controls.columnconfigure(2, weight=1)
        controls.columnconfigure(4, weight=1)

        paned = ttk.Panedwindow(outer, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        plot_frame = ttk.Frame(paned)
        paned.add(plot_frame, weight=3)

        bg = "#0d1117"
        fg = "#e6edf3"
        border = "#30363d"
        self.fig = Figure(figsize=(5, 5), dpi=100, facecolor=bg)
        self.ax = self.fig.add_subplot(111, facecolor=bg)
        self.ax.set_aspect("equal")
        self.ax.set_xlim(-1.6, 1.6)
        self.ax.set_ylim(-1.6, 1.6)
        self.ax.grid(True, alpha=0.3, color=border)
        self.ax.axhline(0, color=border, linewidth=0.5)
        self.ax.axvline(0, color=border, linewidth=0.5)
        self.ax.set_title("Table Preview", color=fg, fontsize=11)
        self.ax.tick_params(colors=fg, which="both")
        for spine in self.ax.spines.values():
            spine.set_color(border)
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self._draw_axes_only()

        text_frame = ttk.Frame(paned)
        paned.add(text_frame, weight=2)
        ttk.Label(text_frame,
                  text="Output vertices (x,y per line):").pack(anchor="w")
        text_container = ttk.Frame(text_frame)
        text_container.pack(fill=tk.BOTH, expand=True)
        self.output_text = tk.Text(
            text_container, wrap=tk.NONE, font=("Consolas", 10),
            undo=False, bg="#010409", fg=fg, insertbackground=fg,
            selectbackground="#580aff", selectforeground=fg,
        )
        self.output_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ysb = ttk.Scrollbar(text_container, orient="vertical",
                            command=self.output_text.yview)
        ysb.pack(side=tk.RIGHT, fill=tk.Y)
        self.output_text.configure(yscrollcommand=ysb.set)

        #status bar
        self.status_bar = ttk.Label(self.root, textvariable=self.status_var,
                                    relief="sunken", anchor="w", padding=(6, 2))
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    #event handlers

    def _pick_file(self):
        path = filedialog.askopenfilename(
            title="Choose an image",
            filetypes=[
                ("Image files", "*.svg *.png *.jpg *.jpeg *.bmp *.tiff *.tif"),
                ("SVG files", "*.svg"),
                ("PNG files", "*.png"),
                ("JPEG files", "*.jpg *.jpeg"),
                ("BMP files", "*.bmp"),
                ("TIFF files", "*.tiff *.tif"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        self.filepath = path
        self.file_label.config(text=os.path.basename(path))
        self.convert_btn.config(state=tk.NORMAL)
        self.status_var.set(
            f"Loaded {os.path.basename(path)} "
            f"({os.path.getsize(path):,} bytes). Click Convert."
        )

    #resolution slider and box parity
    def _resolution_slider_moved(self, value):
        if getattr(self, '_updating', False):
            return
        self._updating = True
        self.resolution_var.set(str(int(float(value))))
        self._updating = False

    def _resolution_entry_typed(self, *_):
        if getattr(self, '_updating', False):
            return
        self._updating = True
        raw = self.resolution_var.get().strip()
        try:
            val = int(raw)
            val = max(3, min(200, val))
            self.resolution_int.set(val)
            clamped = str(val)
            if self.resolution_var.get() != clamped:
                self.resolution_var.set(clamped)
        except ValueError:
            pass
        self._updating = False

    def _parse_resolution(self):
        raw = self.resolution_var.get().strip()
        try:
            value = int(raw)
        except ValueError:
            raise ValueError(
                f"Resolution must be a positive integer (target vertex "
                f"count), got {raw!r}."
            )
        if value <= 0:
            raise ValueError("Resolution must be a positive integer.")
        return value

    def _convert(self):
        if not self.filepath:
            messagebox.showinfo("No file", "Select an SVG file first.")
            return
        try:
            resolution = self._parse_resolution()
        except ValueError as exc:
            messagebox.showerror("Bad resolution", str(exc))
            return
        try:
            polygon, metadata = convert_image_to_polygon(
                self.filepath, resolution
            )
        except Exception as exc:
            messagebox.showerror("Conversion failed", str(exc))
            self.status_var.set(f"Failed: {exc}")
            return
        self.polygon = polygon
        self.metadata = metadata
        self._refresh_plot()
        self._refresh_output()
        self.copy_btn.config(state=tk.NORMAL)
        self.status_var.set(
            f"Generated {metadata['final_vertices']} vertices "
            f"(target {resolution}, sampled {metadata['raw_vertices']} "
            f"before dedupe; arc_len={metadata['arc_length_svg']:.1f}, "
            f"step={metadata['step_svg']:.4f})."
        )

    def _open_generator(self):
        #Open official table generator in browser
        webbrowser.open("https://spooker-table-generator.tiiny.site/")
        self.status_var.set(
            "Opened Spooker Table Generator in your browser."
        )

    def _copy_output(self):
        if not self.polygon:
            messagebox.showinfo("Nothing to copy", "Run Convert first.")
            return
        text = format_polygon_output(self.polygon)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set(
            f"Copied {len(self.polygon)} vertices to clipboard."
        )

    #plotting and output renderer

    def _draw_axes_only(self):
        bg = "#0d1117"
        fg = "#e6edf3"
        border = "#30363d"
        self.ax.clear()
        self.ax.set_facecolor(bg)
        self.ax.set_aspect("equal")
        self.ax.set_xlim(-1.6, 1.6)
        self.ax.set_ylim(-1.6, 1.6)
        self.ax.grid(True, alpha=0.3, color=border)
        self.ax.axhline(0, color=border, linewidth=0.5)
        self.ax.axvline(0, color=border, linewidth=0.5)
        self.ax.tick_params(colors=fg, which="both")
        self.ax.set_title("Table Preview", color=fg, fontsize=11)
        for spine in self.ax.spines.values():
            spine.set_color(border)
        self.ax.add_patch(
            matplotlib.patches.Rectangle(
                (-1.5, -1.5), 3.0, 3.0,
                fill=False, edgecolor=border,
                linestyle="--", linewidth=1.0,
            )
        )
        self.canvas.draw_idle()

    def _refresh_plot(self):
        bg = "#0d1117"
        fg = "#e6edf3"
        border = "#30363d"
        poly_color = "#580aff"
        self.ax.clear()
        self.ax.set_facecolor(bg)
        self.ax.set_aspect("equal")
        self.ax.set_xlim(-1.6, 1.6)
        self.ax.set_ylim(-1.6, 1.6)
        self.ax.grid(True, alpha=0.3, color=border)
        self.ax.axhline(0, color=border, linewidth=0.5)
        self.ax.axvline(0, color=border, linewidth=0.5)
        self.ax.tick_params(colors=fg, which="both")
        self.ax.set_title("Table Preview", color=fg, fontsize=11)
        for spine in self.ax.spines.values():
            spine.set_color(border)
        self.ax.add_patch(
            matplotlib.patches.Rectangle(
                (-1.5, -1.5), 3.0, 3.0,
                fill=False, edgecolor=border,
                linestyle="--", linewidth=1.0,
            )
        )
        if self.polygon:
            xs = [p[0] for p in self.polygon] + [self.polygon[0][0]]
            ys = [p[1] for p in self.polygon] + [self.polygon[0][1]]
            self.ax.fill(xs, ys, color=poly_color, alpha=0.25)
            self.ax.plot(xs, ys, "-", color=poly_color, linewidth=2.0)
            x0, y0 = self.polygon[0]
            self.ax.plot(x0, y0, "o", color=poly_color, markersize=7,
                         markeredgecolor="#ffffff", markeredgewidth=1.0)
            for i, (x, y) in enumerate(self.polygon):
                if i == 0:
                    continue
                self.ax.plot(x, y, ".", color=poly_color, markersize=3)
        self.canvas.draw_idle()

    def _refresh_output(self):
        if not self.output_text:
            return
        self.output_text.delete("1.0", tk.END)
        if self.polygon:
            self.output_text.insert(tk.END, format_polygon_output(self.polygon))
        self.output_text.mark_set(tk.INSERT, "1.0")

def main():
    root = tk.Tk()
    root.configure(bg="#0d1117")
    SvgToSpooker(root)
    root.mainloop()


if __name__ == "__main__":
    main()
