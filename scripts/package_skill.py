#!/usr/bin/env python3
"""Build a source ZIP and a runtime .skill ZIP using an explicit allowlist."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAME = "compose-ins-photo-diary"
RUNTIME = ["SKILL.md", "README.md", "LICENSE", "NOTICE.md", "requirements.txt",
           "agents/openai.yaml", "references/aesthetic-system.md", "references/layout-contract.md",
           "scripts/compose_ins_diary.py", "assets/srgb.icc", "assets/fonts/manifest.json"]
DEVELOPMENT = [".gitignore", ".github/workflows/check.yml", "scripts/package_skill.py",
               "tests/test_compositor.py", "evals/behavior-cases.md"]


def runtime_files():
    files = list(RUNTIME)
    metadata = json.loads((ROOT / "assets/fonts/manifest.json").read_text(encoding="utf-8"))
    for item in metadata["files"]:
        name = Path("assets/fonts") / item["file"]
        path = (ROOT / name).resolve()
        if not path.is_relative_to((ROOT / "assets/fonts").resolve()) or (ROOT / name).is_symlink():
            raise ValueError("Invalid font path in manifest")
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"Font/license checksum mismatch: {name}")
        files.append(name.as_posix())
    return sorted(set(files))


def archive(destination, names):
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in sorted(names):
            path = ROOT / name
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Missing or symlinked package file: {name}")
            entry = zipfile.ZipInfo(f"{NAME}/{name}", date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            z.writestr(entry, path.read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    runtime = runtime_files()
    full = sorted(set(runtime + DEVELOPMENT))
    # Validate every path before creating either archive.
    for name in full:
        path = ROOT / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Required file missing or symlinked: {name}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    source = args.output_dir / f"{NAME}-github.zip"
    skill = args.output_dir / f"{NAME}.skill"
    archive(source, full)
    archive(skill, runtime)
    checksums = []
    for path in (source, skill):
        checksums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
        print(f"{path.name}: {path.stat().st_size:,} bytes")
    (args.output_dir / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
