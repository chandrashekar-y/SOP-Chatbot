"""
convert_ClientX_sops.py
------------------------
Batch-converts all SOP .docx files from the ClientX network share into
data_schema/*.json for the SOP Chatbot (Other Vertical team).

Network share structure:
  \\your-network-share\ClientX\4. Client Specifications\Standard Operating Procedure\
    AR\2026\*.docx
    Billing & Payment\2026\*.docx
    Coding\2026\*.docx
    Collection\2026\*.docx

Each department folder becomes the `department` field in the JSON.
All files are tagged team="Other Vertical".

Usage:
  python convert_ClientX_sops.py               # converts all departments
  python convert_ClientX_sops.py --dept AR     # single department only
  python convert_ClientX_sops.py --list        # list files without converting
"""

import os
import glob
import argparse
from pathlib import Path
from convert_other_vertical import convert_file

# ── Network share base path ────────────────────────────────────────────────────
SHARE_BASE = r"\\your-network-share\ClientX\4. Client Specifications\Standard Operating Procedure"

# Department folder name → department label stored in JSON
DEPARTMENTS = {
    "AR"              : "AR",
    "Billing & Payment": "Billing & Payment",
    "Coding"          : "Coding",
    "Collection"      : "Collection",
}

# Year subfolder (files live inside dept/2026/)
YEAR_FOLDER = "2026"

# Skip files that are clearly drafts or temp
SKIP_PREFIXES = ("Need to work",)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "data_schema")


def list_files(dept_filter=None):
    """Print all docx files found in the share without converting."""
    for dept_folder, dept_label in DEPARTMENTS.items():
        if dept_filter and dept_label.lower() != dept_filter.lower():
            continue
        folder = os.path.join(SHARE_BASE, dept_folder, YEAR_FOLDER)
        files = sorted(glob.glob(os.path.join(folder, "*.docx")))
        print(f"\n[{dept_label}] — {len(files)} files in {folder}")
        for f in files:
            fname = os.path.basename(f)
            skip = any(fname.startswith(p) for p in SKIP_PREFIXES)
            print(f"  {'SKIP' if skip else '    '} {fname}")


def convert_all(dept_filter=None):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    grand_total = 0
    converted_files = 0
    skipped_files = 0

    for dept_folder, dept_label in DEPARTMENTS.items():
        if dept_filter and dept_label.lower() != dept_filter.lower():
            continue

        folder = os.path.join(SHARE_BASE, dept_folder, YEAR_FOLDER)
        files = sorted(glob.glob(os.path.join(folder, "*.docx")))

        if not files:
            print(f"\n[{dept_label}] No .docx files found in {folder}")
            continue

        print(f"\n[{dept_label}] Converting {len(files)} files...")

        for fpath in files:
            fname = os.path.basename(fpath)

            # Skip drafts / temp files
            if any(fname.startswith(p) for p in SKIP_PREFIXES):
                print(f"  SKIP  {fname}")
                skipped_files += 1
                continue

            try:
                count = convert_file(fpath, OUTPUT_DIR, department=dept_label)
                grand_total += count
                converted_files += 1
            except Exception as e:
                print(f"  ERROR {fname}: {e}")
                skipped_files += 1

    print(f"\n{'='*60}")
    print(f"Done. Converted: {converted_files} files | Skipped/Error: {skipped_files}")
    print(f"Total records written: {grand_total}")
    print(f"Output directory: {OUTPUT_DIR}")


def main():
    parser = argparse.ArgumentParser(description="Convert ClientX network share SOPs to JSON")
    parser.add_argument("--dept", help="Convert only this department (e.g. AR, Coding)")
    parser.add_argument("--list", action="store_true", help="List files without converting")
    args = parser.parse_args()

    if args.list:
        list_files(dept_filter=args.dept)
    else:
        convert_all(dept_filter=args.dept)


if __name__ == "__main__":
    main()
