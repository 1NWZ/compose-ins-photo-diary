#!/usr/bin/env python3
"""Offline, deterministic photo compositor. No generation or network calls."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sys

from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps, __version__ as PIL_VERSION

ROOT = Path(__file__).resolve().parents[1]
CANVAS = (1536, 2048)
MARGIN = 64
MODES = {"bold-index", "quiet-journal", "place-note"}
FONT_FILES = {
    "bold-index": "archivoblack/ArchivoBlack-Regular.ttf",
    "quiet-journal": "cormorantgaramond/CormorantGaramond[wght].ttf",
    "place-note": "inter/Inter[opsz,wght].ttf",
}
SRGB = ImageCms.ImageCmsProfile(str(ROOT / "assets" / "srgb.icc"))


class ContractError(ValueError):
    """The requested composition violates the documented contract."""


def require(condition, message):
    if not condition:
        raise ContractError(message)


def object_keys(value, required, optional=(), label="object"):
    require(isinstance(value, dict), f"{label}: expected an object")
    missing = set(required) - value.keys()
    unknown = value.keys() - set(required) - set(optional)
    require(not missing, f"{label}: missing fields {sorted(missing)}")
    require(not unknown, f"{label}: unknown fields {sorted(unknown)}")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=strict_pairs)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def resolve_file(base, value):
    require(isinstance(value, str) and bool(value), "File path must be a non-empty string")
    return (Path(base) / value).resolve()


def load_image(path):
    """Hash the exact bytes decoded; normalize orientation and ICC, never recolor."""
    raw = Path(path).read_bytes()
    with Image.open(io.BytesIO(raw)) as original:
        require(original.format in {"JPEG", "PNG", "WEBP"},
                f"Unsupported format: {path}; use static JPEG, PNG or WebP")
        require(getattr(original, "n_frames", 1) == 1, f"Animated image is not supported: {path}")
        original.load()
        orientation = original.getexif().get(274, 1)
        original_size = list(original.size)
        profile = original.info.get("icc_profile")
        oriented = ImageOps.exif_transpose(original)
        alpha = oriented.convert("RGBA").getchannel("A")
        if profile:
            color_input = oriented if oriented.mode in {"RGB", "CMYK", "LAB", "L"} else oriented.convert("RGB")
            try:
                rgb = ImageCms.profileToProfile(color_input, ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                                               SRGB, outputMode="RGB")
            except (OSError, ValueError, ImageCms.PyCMSError) as exc:
                raise ContractError(f"Cannot convert ICC profile for {path}: {exc}") from exc
            color_step = "embedded-ICC-to-sRGB"
        else:
            require(oriented.mode not in {"CMYK", "LAB"},
                    f"{path}: CMYK/LAB requires an ICC profile; convert explicitly first")
            rgb = oriented.convert("RGB")
            color_step = "untagged-assumed-sRGB"
        result = rgb.convert("RGBA")
        result.putalpha(alpha)
    info = {"sha256": sha256(raw), "original_size": original_size,
            "oriented_size": list(result.size), "exif_orientation": orientation,
            "color_conversion": color_step, "normalized_pixel_sha256": sha256(result.tobytes())}
    return result, info


def inspect_sources(paths, output):
    require(3 <= len(paths) <= 6, "Provide 3–6 source images; do not silently select a subset")
    sources = []
    hashes = set()
    for number, path in enumerate(paths, 1):
        path = Path(path).resolve()
        _, info = load_image(path)
        require(info["sha256"] not in hashes, f"Duplicate source bytes: {path.name}")
        hashes.add(info["sha256"])
        sources.append({"id": f"photo-{number:02d}",
                        "path": os.path.relpath(path, Path(output).resolve().parent), **info})
    manifest = {"schema_version": 1, "sources": sources}
    reject_output_collision([output], paths)
    write_json(output, manifest)
    return manifest


def number(value, low, high, label):
    require(type(value) in {int, float} and math.isfinite(value) and low <= value <= high,
            f"{label}: expected a finite number from {low} to {high}")
    return value


def vector(value, length, low, high, label, integer=False):
    require(isinstance(value, list) and len(value) == length, f"{label}: expected {length} numbers")
    for entry in value:
        number(entry, low, high, label)
        if integer:
            require(type(entry) is int, f"{label}: expected integer pixel coordinates")
    return value


def box(value, label):
    x, y, w, h = vector(value, 4, 0, max(CANVAS), label, integer=True)
    require(w > 0 and h > 0, f"{label}: dimensions must be positive")
    return x, y, w, h


def inside_canvas(rect, label):
    x, y, w, h = rect
    require(x >= MARGIN and y >= MARGIN and x + w <= CANVAS[0] - MARGIN
            and y + h <= CANVAS[1] - MARGIN, f"{label}: outside the {MARGIN}px safe margin")


def intersects(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def focal_crop(size, target, focus, protected):
    sw, sh = size
    tw, th = target
    if sw / sh >= tw / th:
        cw, ch = min(sw, max(1, round(sh * tw / th))), sh
    else:
        cw, ch = sw, min(sh, max(1, round(sw * th / tw)))
    left = min(max(round(focus[0] * sw - cw / 2), 0), sw - cw)
    top = min(max(round(focus[1] * sh - ch / 2), 0), sh - ch)
    if protected is not None:
        pl, pt = math.floor(protected[0] * sw), math.floor(protected[1] * sh)
        pr, pb = math.ceil(protected[2] * sw), math.ceil(protected[3] * sh)
        require(pr - pl <= cw and pb - pt <= ch,
                "Crop would remove protected subject; change photo box aspect ratio")
        left = min(max(left, max(0, pr - cw)), min(pl, sw - cw))
        top = min(max(top, max(0, pb - ch)), min(pt, sh - ch))
    return left, top, left + cw, top + ch


def get_font(mode, size, role):
    key = "place-note" if role == "caption" else mode
    path = ROOT / "assets" / "fonts" / FONT_FILES[key]
    font = ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.BASIC)
    if key != "bold-index":
        axes = font.get_variation_axes()
        values = [axis["default"] for axis in axes]
        for i, axis in enumerate(axes):
            if axis["name"].decode().lower() == "weight":
                values[i] = 300 if key == "place-note" and role != "caption" else 400
        font.set_variation_by_axes(values)
    return font, path


def text_tile(item, mode, index):
    label = f"texts[{index}]"
    object_keys(item, {"text", "box", "font_size", "color"},
                {"min_font_size", "align", "role"}, label)
    content = item["text"]
    require(isinstance(content, str) and content.strip(), f"{label}: empty text")
    require(len(content) <= 240 and all(ch == "\n" or 32 <= ord(ch) <= 126 for ch in content),
            f"{label}: built-in type supports printable ASCII English and newlines only")
    color = item["color"]
    require(isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color),
            f"{label}: color must be #RRGGBB")
    align = item.get("align", "left")
    role = item.get("role", "label")
    require(align in {"left", "center", "right"}, f"{label}: invalid align")
    require(role in {"label", "caption", "bracket"}, f"{label}: invalid role")
    x, y, w, h = box(item["box"], label)
    maximum = item["font_size"]
    minimum = item.get("min_font_size", maximum)
    require(type(maximum) is int and type(minimum) is int and 24 <= minimum <= maximum <= 200,
            f"{label}: font sizes must be integers, 24 <= minimum <= font_size <= 200")
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    for size in range(maximum, minimum - 1, -1):
        font, font_path = get_font(mode, size, role)
        spacing = round(size * 0.18)
        bounds = measure.multiline_textbbox((0, 0), content, font=font, spacing=spacing, align=align)
        tw, th = bounds[2] - bounds[0], bounds[3] - bounds[1]
        if tw <= w and th <= h:
            break
    else:
        raise ContractError(f"{label}: text does not fit; shorten copy or enlarge box")
    tile = Image.new("RGBA", (max(1, tw), max(1, th)))
    ImageDraw.Draw(tile).multiline_text((-bounds[0], -bounds[1]), content, font=font,
                                      fill=color, spacing=spacing, align=align)
    offset = 0 if align == "left" else (w - tw if align == "right" else (w - tw) // 2)
    rect = (x + offset, y, tw, th)
    inside_canvas(rect, label)
    return tile, rect, {"text": content, "font_size": size, "role": role,
                        "font": str(font_path.relative_to(ROOT)),
                        "font_sha256": sha256(font_path.read_bytes()), "box": list(rect)}


def reject_output_collision(outputs, inputs):
    resolved_inputs = {Path(p).resolve() for p in inputs}
    resolved_outputs = [Path(p).resolve() for p in outputs]
    require(len(resolved_outputs) == len(set(resolved_outputs)), "Output files must be distinct")
    require(not (set(resolved_outputs) & resolved_inputs), "Output would overwrite an input file")


def prepare(manifest_path, layout_path):
    manifest_path, layout_path = Path(manifest_path).resolve(), Path(layout_path).resolve()
    manifest, layout = read_json(manifest_path), read_json(layout_path)
    object_keys(manifest, {"schema_version", "sources"}, label="manifest")
    require(type(manifest["schema_version"]) is int and manifest["schema_version"] == 1,
            "Unsupported manifest schema_version")
    object_keys(layout, {"schema_version", "canvas", "mode", "background", "photos", "texts"},
                label="layout")
    require(type(layout["schema_version"]) is int and layout["schema_version"] == 1,
            "Unsupported layout schema_version")
    require(layout["canvas"] == list(CANVAS), "Canvas must be [1536, 2048]")
    require(isinstance(layout["mode"], str) and layout["mode"] in MODES, "Unknown mode")
    sources = manifest["sources"]
    require(isinstance(sources, list) and 3 <= len(sources) <= 6, "Manifest must have 3–6 sources")
    loaded, hashes, source_records = {}, set(), {}
    inputs = [manifest_path, layout_path, ROOT / "assets" / "srgb.icc"]
    for source in sources:
        object_keys(source, {"id", "path", "sha256", "oriented_size"},
                    {"original_size", "exif_orientation", "color_conversion", "normalized_pixel_sha256"},
                    "source")
        sid = source["id"]
        require(isinstance(sid, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sid), "Invalid source id")
        require(sid not in loaded, f"Duplicate source id: {sid}")
        path = resolve_file(manifest_path.parent, source["path"])
        image, info = load_image(path)
        require(info["sha256"] == source["sha256"], f"Source hash mismatch: {sid}")
        require(list(image.size) == source["oriented_size"], f"Source size mismatch: {sid}")
        require(info["sha256"] not in hashes, "Duplicate source bytes in manifest")
        hashes.add(info["sha256"])
        loaded[sid] = image
        source_records[sid] = {"id": sid, "filename": path.name, **info}
        inputs.append(path)
    bg = layout["background"]
    object_keys(bg, {"path", "sha256", "kind", "prompt"}, label="background")
    require(bg["kind"] == "generated", "Background kind must be generated")
    require(isinstance(bg["prompt"], str) and bg["prompt"].strip(), "Record the actual background prompt")
    bg_path = resolve_file(layout_path.parent, bg["path"])
    background, bg_info = load_image(bg_path)
    require(bg_info["sha256"] == bg["sha256"], "Background hash mismatch")
    require(bg_info["sha256"] not in hashes and
            bg_info["normalized_pixel_sha256"] not in
            {r["normalized_pixel_sha256"] for r in source_records.values()},
            "A source image cannot serve as the background")
    require(background.getchannel("A").getextrema() == (255, 255), "Background must be opaque")
    require(background.width >= 768 and background.height >= 1024,
            "Background must be at least 768px wide and 1024px tall")
    inputs.append(bg_path)
    bg_crop = focal_crop(background.size, CANVAS, [0.5, 0.5], None)
    background = background.crop(bg_crop).resize(CANVAS, Image.Resampling.LANCZOS)
    photos = layout["photos"]
    require(isinstance(photos, list) and len(photos) == len(sources),
            "Every source must appear exactly once; photo count differs")
    photo_ids = [p.get("source_id") if isinstance(p, dict) else None for p in photos]
    require(all(isinstance(sid, str) for sid in photo_ids), "Every photo needs a source_id")
    require(len(set(photo_ids)) == len(photo_ids) and set(photo_ids) == set(loaded),
            "Photo ids must match all source ids exactly once")
    layers, occupied, transforms = [], [], []
    for item in photos:
        object_keys(item, {"source_id", "box", "focus", "protected_region", "direction_sensitive"},
                    {"rotation"}, "photo")
        sid = item["source_id"]
        image = loaded[sid]
        x, y, w, h = box(item["box"], sid)
        require(min(w, h) >= 120, f"{sid}: photo short edge must be at least 120px")
        focus = vector(item["focus"], 2, 0, 1, f"{sid}.focus")
        protected = item["protected_region"]
        if protected is not None:
            pl, pt, pr, pb = vector(protected, 4, 0, 1, f"{sid}.protected_region")
            require(pl < pr and pt < pb, f"{sid}: protected rectangle is empty")
        sensitive = item["direction_sensitive"]
        require(type(sensitive) is bool, f"{sid}: direction_sensitive must be true or false")
        rotation = number(item.get("rotation", 0), -6, 6, f"{sid}.rotation")
        require(not sensitive or rotation == 0, f"{sid}: direction-sensitive photo must be upright")
        crop = focal_crop(image.size, (w, h), focus, protected)
        tile = image.crop(crop).resize((w, h), Image.Resampling.LANCZOS)
        if rotation:
            tile = tile.rotate(rotation, Image.Resampling.BICUBIC, expand=True)
        left, top = x + (w - tile.width) // 2, y + (h - tile.height) // 2
        rect = (left, top, tile.width, tile.height)
        inside_canvas(rect, sid)
        require(not any(intersects(rect, other) for other in occupied), f"{sid}: photo overlap")
        occupied.append(rect)
        layers.append((tile, (left, top)))
        transforms.append({**source_records[sid], "crop_box_oriented_px": list(crop),
                           "resize_to": [w, h], "rotation_degrees_ccw": rotation,
                           "focus": focus, "protected_region": protected,
                           "direction_sensitive": sensitive, "canvas_box": list(rect)})
    texts = layout["texts"]
    require(isinstance(texts, list) and 1 <= len(texts) <= 24, "Provide 1–24 text elements")
    text_records = []
    for index, item in enumerate(texts):
        tile, rect, record = text_tile(item, layout["mode"], index)
        require(not any(intersects(rect, other) for other in occupied),
                f"texts[{index}]: overlaps another photo or text element")
        occupied.append(rect)
        layers.append((tile, rect[:2]))
        text_records.append(record)
        inputs.append(ROOT / record["font"])
    unoccupied = 1 - sum(w * h for _, _, w, h in occupied) / (CANVAS[0] * CANVAS[1])
    require(unoccupied >= 0.35, "Less than 35% unoccupied area; reduce foreground density")
    provenance = {
        "schema_version": 1, "renderer": "compose-ins-photo-diary/1.0",
        "pillow_version": PIL_VERSION, "mode": layout["mode"],
        "manifest_sha256": sha256(manifest_path.read_bytes()),
        "layout_sha256": sha256(layout_path.read_bytes()),
        "output_color_space": "sRGB", "srgb_profile_sha256": sha256(SRGB.tobytes()),
        "background": {"filename": bg_path.name, **bg_info, "kind_declared": bg["kind"],
                       "prompt": bg["prompt"], "crop_box_oriented_px": list(bg_crop)},
        "sources": transforms, "texts": text_records,
        "unoccupied_area_ratio": round(unoccupied, 6),
        "checks": {"all_sources_once": True, "source_hashes_match": True,
                   "no_overlap": True, "protected_regions_preserved": True},
        "limits": "Generation origin, factual copy, subject annotations, background aesthetics and visual readability require Agent review. Hashes are a local processing record, not a signature or proof of authorship."
    }
    return background, layers, provenance, inputs


def render(manifest_path, layout_path, output=None, preview=False, validate_only=False):
    background, layers, provenance, inputs = prepare(manifest_path, layout_path)
    if validate_only:
        return {"valid": True, "mode": provenance["mode"], "photos": len(provenance["sources"]),
                "unoccupied_area_ratio": provenance["unoccupied_area_ratio"]}
    require(output is not None, "--output is required unless --validate-only is used")
    output = Path(output)
    require(output.suffix.lower() == ".png", "Output must be a .png file")
    sidecar = output.with_suffix(".provenance.json")
    reject_output_collision([output, sidecar], inputs)
    for tile, position in layers:
        background.alpha_composite(tile, position)
    final = background.convert("RGB")
    if preview:
        final = final.resize((768, 1024), Image.Resampling.LANCZOS)
    output.parent.mkdir(parents=True, exist_ok=True)
    final.save(output, "PNG", icc_profile=SRGB.tobytes(), compress_level=9)
    provenance["output"] = {"filename": output.name, "size": list(final.size), "preview": preview,
                            "sha256": sha256(output.read_bytes())}
    write_json(sidecar, provenance)
    return provenance


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Validate and inventory 3–6 source files")
    inspect.add_argument("--images", nargs="+", required=True, type=Path)
    inspect.add_argument("--output", required=True, type=Path)
    compose = commands.add_parser("render", help="Validate and render an Agent-authored layout")
    compose.add_argument("--manifest", required=True, type=Path)
    compose.add_argument("--layout", required=True, type=Path)
    compose.add_argument("--output", type=Path)
    compose.add_argument("--preview", action="store_true")
    compose.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            result = inspect_sources(args.images, args.output)
            print(json.dumps({"manifest": str(args.output), "photos": len(result["sources"])}, ensure_ascii=False))
        else:
            result = render(args.manifest, args.layout, args.output, args.preview, args.validate_only)
            print(json.dumps(result if args.validate_only else result["output"], ensure_ascii=False))
    except (ContractError, OSError, ValueError, KeyError, TypeError, Image.DecompressionBombError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
