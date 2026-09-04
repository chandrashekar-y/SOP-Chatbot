"""
SOP Watcher — Drop-folder automation
======================================
Run via cron every 2 minutes on the server.
Watches sop_inbox/everhealth/ and sop_inbox/other_vertical/ for new files.
On detection: converts → writes .reload_flag → Streamlit picks it up zero-downtime.

Zero-downtime reload (no container restart):
  After conversion, writes sop_inbox/.reload_flag
  Streamlit checks this flag on every page load via check_reload_flag()
  If flag exists: clears st.cache_resource → engine reloads on next user request
  No restart, no downtime, new SOP available within seconds.

Cron entry (added by setup_watcher.sh):
  */2 * * * * /usr/bin/python3 /opt/SOP_Chatbot/sop_watcher.py >> /opt/SOP_Chatbot/sop_inbox/watcher.log 2>&1

Drop instructions (from Windows CMD):
  scp -i "path\\to\\your-key.pem" "MyPractice.xlsx" your-user@your-server-ip:/opt/SOP_Chatbot/sop_inbox/everhealth/
  scp -i "path\\to\\your-key.pem" "KMB_SOP.docx"   your-user@your-server-ip:/opt/SOP_Chatbot/sop_inbox/other_vertical/
"""

import os
import sys
import shutil
import subprocess
import logging
from datetime import datetime
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR        = Path("/opt/SOP_Chatbot")
INBOX_DIR       = BASE_DIR / "sop_inbox"
EVERHEALTH_IN   = INBOX_DIR / "everhealth"
OTHER_IN        = INBOX_DIR / "other_vertical"
PROCESSED_DIR   = INBOX_DIR / "processed"
FAILED_DIR      = INBOX_DIR / "failed"
LOG_FILE        = INBOX_DIR / "watcher.log"

EVERHEALTH_DATA = BASE_DIR / "data" / "Everhealth SOPs"
OTHER_DATA      = BASE_DIR / "data" / "Other Vertical SOPs"

PYTHON          = sys.executable          # same python that runs this script
CONVERT_EH      = BASE_DIR / "convert_everhealth.py"
CONVERT_OV      = BASE_DIR / "convert_other_vertical.py"

RELOAD_FLAG     = INBOX_DIR / ".reload_flag"   # Streamlit watches this file

ALLOWED_EXT     = {".xlsx", ".xls", ".csv", ".docx"}

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("sop_watcher")


def ensure_dirs():
    for d in [EVERHEALTH_IN, OTHER_IN, PROCESSED_DIR, FAILED_DIR,
              EVERHEALTH_DATA, OTHER_DATA]:
        d.mkdir(parents=True, exist_ok=True)


def collect_new_files(inbox: Path) -> list[Path]:
    """Return files that are fully written (not currently being copied)."""
    files = []
    for f in inbox.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() not in ALLOWED_EXT:
            log.warning(f"Skipping unsupported file type: {f.name}")
            continue
        # Skip if file was modified in the last 5 seconds (still being copied)
        age = datetime.now().timestamp() - f.stat().st_mtime
        if age < 5:
            log.info(f"File still being written, will pick up next run: {f.name}")
            continue
        files.append(f)
    return files


def move_file(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    # Avoid overwriting existing processed file — append timestamp
    if dest.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = dest_dir / f"{src.stem}_{ts}{src.suffix}"
    shutil.move(str(src), str(dest))
    return dest


def run_converter(script: Path) -> tuple[bool, str]:
    """Run a converter script. Returns (success, output)."""
    try:
        result = subprocess.run(
            [PYTHON, str(script)],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=300,   # 5 min max
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            return False, output
        return True, output
    except subprocess.TimeoutExpired:
        return False, "Converter timed out after 5 minutes"
    except Exception as e:
        return False, str(e)


def write_reload_flag():
    """
    Write a flag file that Streamlit checks on each page load.
    Streamlit calls st.cache_resource.clear() when it sees this flag,
    causing the engine to reload with the new SOPs — zero downtime.
    """
    try:
        RELOAD_FLAG.write_text(datetime.now().isoformat())
        log.info(f"✓ Reload flag written: {RELOAD_FLAG}")
        return True, ""
    except Exception as e:
        return False, str(e)


def process_batch(inbox: Path, data_dest: Path, converter: Path, team_label: str) -> int:
    """
    Process all ready files in an inbox folder.
    Returns count of successfully processed files.
    """
    files = collect_new_files(inbox)
    if not files:
        return 0

    log.info(f"[{team_label}] Found {len(files)} new file(s): {[f.name for f in files]}")
    success_count = 0

    for src in files:
        log.info(f"[{team_label}] Processing: {src.name}")

        # 1. Copy file into the data folder the converter reads from
        dest_data = data_dest / src.name
        try:
            shutil.copy2(str(src), str(dest_data))
            log.info(f"  Copied to: {dest_data}")
        except Exception as e:
            log.error(f"  Failed to copy to data folder: {e}")
            move_file(src, FAILED_DIR)
            continue

        # 2. Run converter
        log.info(f"  Running converter: {converter.name}")
        ok, output = run_converter(converter)
        if output:
            for line in output.splitlines()[-20:]:   # last 20 lines
                log.info(f"    {line}")

        if not ok:
            log.error(f"  Conversion FAILED for {src.name}")
            # Clean up the copied data file so it doesn't pollute next run
            try:
                dest_data.unlink(missing_ok=True)
            except Exception:
                pass
            move_file(src, FAILED_DIR)
            continue

        # 3. Move source file to processed
        processed_path = move_file(src, PROCESSED_DIR)
        log.info(f"  Moved to processed: {processed_path.name}")
        success_count += 1
        log.info(f"  ✓ {src.name} done")

    return success_count


def main():
    log.info("=" * 60)
    log.info("SOP Watcher — starting run")
    ensure_dirs()

    total = 0
    total += process_batch(EVERHEALTH_IN,  EVERHEALTH_DATA, CONVERT_EH, "Everhealth")
    total += process_batch(OTHER_IN,        OTHER_DATA,      CONVERT_OV, "OtherVertical")

    if total > 0:
        log.info(f"Processed {total} SOP(s). Writing reload flag for Streamlit...")
        ok, err = write_reload_flag()
        if not ok:
            log.error(f"✗ Could not write reload flag: {err}")
    else:
        log.info("No new SOPs found. Nothing to do.")

    log.info("SOP Watcher — run complete")


if __name__ == "__main__":
    main()
