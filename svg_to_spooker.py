
#svg-to-spooker

import math
import os
import sys
import tkinter as tk
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
from matplotlib.backends.backend_tkagg import (
    FigureCanvasTkAgg,
    NavigationToolbar2Tk,
)

# Core conversion


HALF_BOX = 1.45 
EPS = 1e-4       # Dedupe epsilon


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
    #scale in order to fit within the 3x3 grid
    if not points:
        return []
    pts = np.array(points, dtype=float)
    if pts.shape[0] == 0:
        return []
    # try to get the right point order (not sure exactly how y'all do this, so I'm just guessing. Can just be fixed in the online tool ig)
    pts[:, 1] = -pts[:, 1]
    min_x, min_y = pts.min(axis=0)
    max_x, max_y = pts.max(axis=0)
    width = max_x - min_x
    height = max_y - min_y
    if width <= 0 or height <= 0:
        return [(float(x), float(-y)) for x, y in points]
    #scale to try and preserve aspect ratio
    scale = (2.0 * half_size) / max(width, height)
    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0
    pts[:, 0] = (pts[:, 0] - cx) * scale
    pts[:, 1] = (pts[:, 1] - cy) * scale
    return [(float(x), float(y)) for x, y in pts]


def dedupe_consecutive(points, eps=EPS):
    #dedupe identical segments and vertices
    if not points:
        return []
    out = [points[0]]
    for p in points[1:]:
        last = out[-1]
        if abs(p[0] - last[0]) > eps or abs(p[1] - last[1]) > eps:
            out.append(p)
    return out


def rotate_to_topmost_rightmost(points):
    #more trying to fix the point order
    if not points:
        return []
    best_idx = 0
    bx, by = points[0]
    for i in range(1, len(points)):
        x, y = points[i]
        if y > by or (y == by and x > bx):
            best_idx = i
            bx, by = x, y
    return points[best_idx:] + points[:best_idx]


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


def convert_svg_to_polygon(svg_path, target_vertices):
    #preview render
    paths = parse_paths_from_svg(svg_path)
    if not paths:
        raise ValueError(
            "No <path> elements with a parseable 'd' attribute were "
            "found in the SVG."
        )
    main = longest_closed_subpath(paths)
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
    cleaned = dedupe_consecutive(normalized, eps=EPS)
    ordered = rotate_to_topmost_rightmost(cleaned)

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
        self.root.title("svg-to-spooker \u2014 SVG to table polygon")
        self.root.geometry("1100x720")
        self.root.minsize(800, 520)

        self.filepath = None
        self.polygon = []
        self.metadata = {}
        self.status_var = tk.StringVar(
            value="Ready. Use the file picker to load an SVG."
        )

        self._build_ui()

    #ui stuff

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill=tk.BOTH, expand=True)

        controls = ttk.LabelFrame(outer, text="Inputs", padding=8)
        controls.pack(fill=tk.X)

        #file picker
        ttk.Button(controls, text="Select SVG\u2026",
                   command=self._pick_file).grid(row=0, column=0, padx=(0, 8))
        self.file_label = ttk.Label(controls, text="(no file selected)")
        self.file_label.grid(row=0, column=1, columnspan=4, sticky="w")

        #resolution + buttons
        ttk.Label(controls, text="Resolution:").grid(row=1, column=0, sticky="e",
                                                     pady=(8, 0))
        self.resolution_var = tk.StringVar(value="50")
        res_entry = ttk.Entry(controls, textvariable=self.resolution_var,
                              width=10)
        res_entry.grid(row=1, column=1, sticky="w", pady=(8, 0))
        ttk.Label(
            controls,
            text="(target vertex count; higher = more detail)",
        ).grid(row=1, column=2, columnspan=2, sticky="w",
               padx=(8, 0), pady=(8, 0))

        self.convert_btn = ttk.Button(controls, text="Convert",
                                      command=self._convert,
                                      state=tk.DISABLED)
        self.convert_btn.grid(row=0, column=5, rowspan=2, padx=(8, 4),
                              sticky="ns")
        self.save_btn = ttk.Button(controls, text="Save output\u2026",
                                   command=self._save_output,
                                   state=tk.DISABLED)
        self.save_btn.grid(row=0, column=6, rowspan=2, padx=(4, 0),
                           sticky="ns")

        controls.columnconfigure(4, weight=1)

        paned = ttk.Panedwindow(outer, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        plot_frame = ttk.Frame(paned)
        paned.add(plot_frame, weight=3)

        self.fig = Figure(figsize=(5, 5), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_aspect("equal")
        self.ax.set_xlim(-1.6, 1.6)
        self.ax.set_ylim(-1.6, 1.6)
        self.ax.grid(True, alpha=0.3, color="gray")
        self.ax.axhline(0, color="gray", linewidth=0.5)
        self.ax.axvline(0, color="gray", linewidth=0.5)
        self.ax.set_title("Normalized table polygon (3x3 box)")
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        toolbar = NavigationToolbar2Tk(self.canvas, plot_frame)
        toolbar.update()
        self._draw_axes_only()

        text_frame = ttk.Frame(paned)
        paned.add(text_frame, weight=2)
        ttk.Label(text_frame,
                  text="Output vertices (x,y per line):").pack(anchor="w")
        text_container = ttk.Frame(text_frame)
        text_container.pack(fill=tk.BOTH, expand=True)
        self.output_text = tk.Text(
            text_container, wrap=tk.NONE, font=("Consolas", 10),
            undo=False,
        )
        self.output_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ysb = ttk.Scrollbar(text_container, orient="vertical",
                            command=self.output_text.yview)
        ysb.pack(side=tk.RIGHT, fill=tk.Y)
        self.output_text.configure(yscrollcommand=ysb.set)

        #status bar
        status = ttk.Label(self.root, textvariable=self.status_var,
                           relief="sunken", anchor="w", padding=(6, 2))
        status.pack(side=tk.BOTTOM, fill=tk.X)

    #event handlers

    def _pick_file(self):
        path = filedialog.askopenfilename(
            title="Choose an SVG file",
            filetypes=[("SVG files", "*.svg"), ("All files", "*.*")],
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
            polygon, metadata = convert_svg_to_polygon(self.filepath,
                                                        resolution)
        except Exception as exc:
            messagebox.showerror("Conversion failed", str(exc))
            self.status_var.set(f"Failed: {exc}")
            return
        self.polygon = polygon
        self.metadata = metadata
        self._refresh_plot()
        self._refresh_output()
        self.save_btn.config(state=tk.NORMAL)
        self.status_var.set(
            f"Generated {metadata['final_vertices']} vertices "
            f"(target {resolution}, sampled {metadata['raw_vertices']} "
            f"before dedupe; arc_len={metadata['arc_length_svg']:.1f}, "
            f"step={metadata['step_svg']:.4f})."
        )

    def _save_output(self):
        if not self.polygon:
            messagebox.showinfo("Nothing to save", "Run Convert first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save polygon output",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(format_polygon_output(self.polygon) + "\n")
        except OSError as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        self.status_var.set(
            f"Saved {len(self.polygon)} vertices to {os.path.basename(path)}."
        )

    #plotting and output renderer

    def _draw_axes_only(self):
        self.ax.clear()
        self.ax.set_aspect("equal")
        self.ax.set_xlim(-1.6, 1.6)
        self.ax.set_ylim(-1.6, 1.6)
        self.ax.grid(True, alpha=0.3, color="gray")
        self.ax.axhline(0, color="gray", linewidth=0.5)
        self.ax.axvline(0, color="gray", linewidth=0.5)
        self.ax.add_patch(
            matplotlib.patches.Rectangle(
                (-1.5, -1.5), 3.0, 3.0,
                fill=False, edgecolor="lightgray",
                linestyle="--", linewidth=1.0,
            )
        )
        self.ax.set_title("Normalized table polygon (3x3 box)")
        self.canvas.draw_idle()

    def _refresh_plot(self):
        self.ax.clear()
        self.ax.set_aspect("equal")
        self.ax.set_xlim(-1.6, 1.6)
        self.ax.set_ylim(-1.6, 1.6)
        self.ax.grid(True, alpha=0.3, color="gray")
        self.ax.axhline(0, color="gray", linewidth=0.5)
        self.ax.axvline(0, color="gray", linewidth=0.5)
        self.ax.add_patch(
            matplotlib.patches.Rectangle(
                (-1.5, -1.5), 3.0, 3.0,
                fill=False, edgecolor="lightgray",
                linestyle="--", linewidth=1.0, label="Playable box",
            )
        )
        if self.polygon:
            xs = [p[0] for p in self.polygon] + [self.polygon[0][0]]
            ys = [p[1] for p in self.polygon] + [self.polygon[0][1]]
            self.ax.fill(xs, ys, color="tab:blue", alpha=0.18)
            self.ax.plot(xs, ys, "-", color="tab:blue", linewidth=2.0,
                         label="Polygon")
            x0, y0 = self.polygon[0]
            self.ax.plot(x0, y0, "o", color="red", markersize=8,
                         label=f"First vertex ({x0:+.3f},{y0:+.3f})")
            for i, (x, y) in enumerate(self.polygon):
                # Don't overlay the red vertex again.
                if i == 0:
                    continue
                self.ax.plot(x, y, ".", color="tab:blue", markersize=3)
        self.ax.set_title("Normalized table polygon (3x3 box)")
        self.ax.legend(loc="upper right", fontsize=8, framealpha=0.85)
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
    try:
        # Use the platform's ttk theme if available.
        style = ttk.Style(root)
        if "vista" in style.theme_names():  # Windows
            style.theme_use("vista")
        elif "aqua" in style.theme_names():  # macOS
            style.theme_use("aqua")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
    except tk.TclError:
        pass
    SvgToSpooker(root)
    root.mainloop()


if __name__ == "__main__":
    main()
