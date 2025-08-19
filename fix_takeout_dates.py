#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Fix date/times for Google Takeout media by reading sidecar JSON (including
*.supplemental-metadata.json) and/or album-level metadata.json/metadati.json,
then writing proper EXIF/XMP/QuickTime tags into a *copied* destination tree.
Errors are moved to DEST/errors/YYYY/MM/. Works recursively and in parallel.

NEW in v3:
- Detects real file type by magic bytes and **corrects wrong extensions** in DEST
  (e.g., PNG named file that is actually a JPEG). This avoids ExifTool errors like:
  "Not a valid PNG (looks more like a JPEG)".
- All previous features preserved.
Supported: JPG, JPEG, HEIC, PNG, GIF, MP4
"""

import os
import sys
import json
import csv
import shutil
import datetime
import subprocess
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# ------------------ Config ------------------

SUPPORTED_EXTS = {".jpg", ".jpeg", ".heic", ".png", ".gif", ".mp4"}
JSON_CANDIDATE_KEYS = [
    ("photoTakenTime", "timestamp"),
    ("creationTime", "timestamp"),
    ("modificationTime", "timestamp"),
]

LOG_FILE_TXT = "errori.log"
ERRORS_CSV = "errori_compatti.csv"


# ------------------ Utils ------------------

def eprint(*args):
    print(*args, file=sys.stderr)


def log_error_txt(msg: str):
    with open(LOG_FILE_TXT, "a", encoding="utf-8") as f:
        f.write(msg + "\n")
    eprint(msg)


def have_exiftool() -> Optional[str]:
    return shutil.which("exiftool")


def have_setfile() -> Optional[str]:
    return shutil.which("SetFile") if sys.platform == "darwin" else None


def ensure_parent(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)


def is_video_suffix(suffix: str) -> bool:
    return suffix.lower() in {".mp4"}


def is_image_suffix(suffix: str) -> bool:
    return suffix.lower() in {".jpg", ".jpeg", ".heic", ".png", ".gif"}


# ------------------ File type sniffing ------------------

def sniff_filetype(path: Path) -> Optional[str]:
    """
    Inspect magic bytes to guess actual type. Return normalized extension including dot,
    e.g. '.jpg', '.png', '.gif', '.heic', '.mp4'. Return None if unknown.
    """
    try:
        with open(path, 'rb') as f:
            head = f.read(16)
    except Exception:
        return None
    if len(head) < 4:
        return None

    # JPEG: FF D8 FF
    if head[:3] == b'\xFF\xD8\xFF':
        return ".jpg"

    # PNG: 89 50 4E 47 0D 0A 1A 0A
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return ".png"

    # GIF: 'GIF87a' or 'GIF89a'
    if head.startswith(b'GIF87a') or head.startswith(b'GIF89a'):
        return ".gif"

    # ISO BMFF: MP4/HEIC in 'ftyp' box at offset 4
    if len(head) >= 12 and head[4:8] == b'ftyp':
        brand = head[8:12]
        try:
            brand_str = brand.decode('ascii', errors='ignore')
        except Exception:
            brand_str = ""
        # HEIC/HEIF brands
        if brand_str.lower() in {"heic", "heix", "hevc", "hevx", "mif1", "heif"}:
            return ".heic"
        # MP4 brands (common)
        if brand_str.lower() in {"isom", "mp41", "mp42", "avc1", "msnv"}:
            return ".mp4"
        # Fallback: treat as mp4
        return ".mp4"

    return None


def corrected_dest_path(dst_root: Path, rel_path: Path, detected_ext: Optional[str]) -> Path:
    """
    If detected_ext is not None and differs from rel_path.suffix (case-insensitive),
    return a new path in DEST with the corrected extension.
    Otherwise, return the original DEST path.
    """
    if detected_ext is None:
        return dst_root / rel_path
    src_ext = rel_path.suffix.lower()
    if src_ext == detected_ext.lower():
        return dst_root / rel_path
    # replace suffix
    return (dst_root / rel_path).with_suffix(detected_ext)


# ------------------ JSON discovery ------------------

def find_json_for_media(media_src: Path) -> Optional[Path]:
    """
    Return the most appropriate per-file sidecar JSON for media_src.
    Priority:
      1) filename.ext.json
      2) filename.ext.supplemental-metadata.json (or any filename.ext.*.json)
         with "supplemental-metadata" preferred, then shortest name
    """
    exact = media_src.with_name(media_src.name + ".json")
    if exact.exists():
        return exact

    candidates = list(media_src.parent.glob(media_src.name + "*.json"))
    if not candidates:
        return None

    candidates_sorted = sorted(
        candidates,
        key=lambda p: (0 if "supplemental-metadata" in p.name else 1, len(p.name))
    )
    return candidates_sorted[0]


def read_timestamp_from_json(json_path: Path) -> Tuple[Optional[int], str]:
    """
    Extract an epoch timestamp from a Takeout sidecar JSON.
    Handles nested objects (photoTakenTime.timestamp etc.) and flat aliases.
    """
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log_error_txt(f"[ERRORE] Lettura JSON {json_path}: {e}")
        return None, ""

    # 1) Standard nested keys
    for topkey, subkey in JSON_CANDIDATE_KEYS:
        try:
            v = data[topkey][subkey]
            return int(v), f"{topkey}.{subkey}"
        except Exception:
            pass

    # 2) formatted string
    try:
        formatted = data.get("photoTakenTime", {}).get("formatted")
        if formatted:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%d %b %Y, %H:%M:%S %Z", "%d %b %Y %H:%M:%S %Z"):
                try:
                    dt = datetime.datetime.strptime(formatted, fmt)
                    return int(dt.timestamp()), "photoTakenTime.formatted"
                except Exception:
                    continue
    except Exception:
        pass

    # 3) flat aliases sometimes present in supplemental JSON
    alias_keys = [
        "PhotoTakenTimeTimestamp", "CreationTimeTimestamp", "ModificationTimeTimestamp",
        "takenTime", "dateTaken", "captureTime", "creation_time", "time"
    ]
    for k in alias_keys:
        v = data.get(k)
        if v is None:
            continue
        if isinstance(v, (int, float)) and v > 0:
            return int(v), k
        if isinstance(v, str):
            try:
                return int(v), k
            except Exception:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S"):
                    try:
                        dt = datetime.datetime.strptime(v, fmt)
                        return int(dt.timestamp()), f"{k}.formatted"
                    except Exception:
                        continue

    log_error_txt(f"[ERRORE] Nessun timestamp valido nel JSON: {json_path}")
    return None, ""


def search_album_metadata(media_src: Path) -> Tuple[Optional[int], str]:
    """
    Look for album-level metadata in the same folder:
      - metadata.json (en) or metadati.json (it)
    Match entry by title/filename, case-insensitive, with or without extension.
    """
    meta_paths = [
        media_src.parent / "metadata.json",
        media_src.parent / "metadati.json",
    ]
    meta = next((p for p in meta_paths if p.exists()), None)
    if not meta:
        return None, ""

    try:
        with open(meta, "r", encoding="utf-8") as f:
            data = json.load(f)

        items = data.get("mediaItems") or data.get("items") or data
        if not isinstance(items, list):
            return None, ""

        name = media_src.name
        name_lower = name.lower()
        stem_lower = media_src.stem.lower()

        def is_match(it: Dict[str, Any]) -> bool:
            title = (it.get("title") or it.get("filename") or "").strip()
            if not title:
                return False
            t_low = title.lower()
            if t_low == name_lower:
                return True
            try:
                title_stem = Path(title).stem.lower()
            except Exception:
                title_stem = t_low.split(".")[0]
            return title_stem == stem_lower

        entry = next((it for it in items if is_match(it)), None)
        if not entry:
            return None, ""

        for topkey, subkey in JSON_CANDIDATE_KEYS:
            try:
                v = entry[topkey][subkey]
                return int(v), f"album.{topkey}.{subkey}"
            except Exception:
                pass

        try:
            formatted = entry.get("photoTakenTime", {}).get("formatted")
            if formatted:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%d %b %Y, %H:%M:%S %Z"):
                    try:
                        dt = datetime.datetime.strptime(formatted, fmt)
                        return int(dt.timestamp()), "album.photoTakenTime.formatted"
                    except Exception:
                        continue
        except Exception:
            pass

        return None, ""
    except Exception as e:
        log_error_txt(f"[ERRORE] Lettura album metadata {meta}: {e}")
        return None, ""


def read_timestamp_from_media_exiftool(exiftool_bin: str, media_path: Path) -> Tuple[Optional[int], str]:
    """
    Last resort: read a plausible date directly from the file via ExifTool JSON output.
    Checks several tags; returns first parseable as epoch.
    """
    wanted = ["DateTimeOriginal", "CreateDate", "MediaCreateDate", "TrackCreateDate", "FileModifyDate"]
    try:
        cmd = [exiftool_bin, "-j"] + [f"-{t}" for t in wanted] + [str(media_path)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            return None, ""
        arr = json.loads(res.stdout)
        if not arr:
            return None, ""
        rec = arr[0]
        for tag in wanted:
            val = rec.get(tag)
            if not val:
                continue
            for fmt in ("%Y:%m:%d %H:%M:%S%z", "%Y:%m:%d %H:%M:%S"):
                try:
                    dt = datetime.datetime.strptime(val, fmt)
                    return int(dt.timestamp()), f"exif.{tag}"
                except Exception:
                    continue
        return None, ""
    except Exception:
        return None, ""


# ------------------ Writing helpers ------------------

def format_dt(epoch: int, kind: str) -> str:
    if kind == "video":
        return datetime.datetime.utcfromtimestamp(epoch).strftime("%Y:%m:%d %H:%M:%S")
    else:
        return datetime.datetime.fromtimestamp(epoch).strftime("%Y:%m:%d %H:%M:%S")


def write_with_exiftool(exiftool_bin: str, media_dst: Path, epoch: int, kind: str) -> Tuple[bool, str]:
    dt_str = format_dt(epoch, kind)
    if kind == "video":
        args = [
            exiftool_bin, "-overwrite_original", "-m", "-api", "QuickTimeUTC=1",
            f"-CreateDate={dt_str}", f"-ModifyDate={dt_str}",
            f"-MediaCreateDate={dt_str}", f"-TrackCreateDate={dt_str}",
            f"-MediaModifyDate={dt_str}", f"-TrackModifyDate={dt_str}",
            f"-FileCreateDate={dt_str}", f"-FileModifyDate={dt_str}",
            str(media_dst)
        ]
    else:
        args = [
            exiftool_bin, "-overwrite_original", "-m",
            f"-AllDates={dt_str}",
            f"-XMP:DateCreated={dt_str}", f"-XMP:CreateDate={dt_str}", f"-XMP:ModifyDate={dt_str}",
            f"-FileCreateDate={dt_str}", f"-FileModifyDate={dt_str}",
            str(media_dst)
        ]
    try:
        res = subprocess.run(args, capture_output=True, text=True)
        if res.returncode != 0:
            return False, f"ExifTool rc={res.returncode} | {res.stderr.strip() or res.stdout.strip()}"
        return True, "ExifTool OK"
    except Exception as e:
        return False, f"ExifTool eccezione: {e}"


def write_with_fallback(media_dst: Path, epoch: int, kind: str, setfile_bin: Optional[str]) -> Tuple[bool, str]:
    dt_str = format_dt(epoch, kind)
    try:
        os.utime(media_dst, (epoch, epoch))
        if sys.platform == "darwin" and setfile_bin:
            subprocess.run([setfile_bin, "-d",
                            datetime.datetime.fromtimestamp(epoch).strftime("%m/%d/%Y %H:%M:%S"),
                            str(media_dst)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return False, f"utime/SetFile errore: {e}"

    if media_dst.suffix.lower() in {".jpg", ".jpeg"}:
        try:
            import piexif
            exif_dict = piexif.load(str(media_dst))
            exif_dict["0th"][piexif.ImageIFD.DateTime] = dt_str.encode()
            exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = dt_str.encode()
            exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = dt_str.encode()
            piexif.insert(piexif.dump(exif_dict), str(media_dst))
            return True, "Fallback piexif + utime"
        except Exception as e:
            return True, f"Fallback utime (piexif: {e})"

    return True, "Fallback utime"


# ------------------ Error handling helpers ------------------

def year_month_from_ts_or_stat(ts_opt: Optional[int], src_path: Path) -> Tuple[int, int]:
    if ts_opt is not None:
        dt = datetime.datetime.fromtimestamp(ts_opt)
        return dt.year, dt.month
    try:
        st = src_path.stat()
        dt = datetime.datetime.fromtimestamp(int(st.st_mtime))
        return dt.year, dt.month
    except Exception:
        return 1970, 1


def move_to_errors(dst_root: Path, media_dst: Path, src_path: Path, ts_opt: Optional[int], err_msg: str):
    year, month = year_month_from_ts_or_stat(ts_opt, src_path)
    target = dst_root / "errors" / f"{year:04d}" / f"{month:02d}" / media_dst.name
    try:
        ensure_parent(target)
        if target.exists():
            stem, suf = target.stem, target.suffix
            i = 1
            while True:
                alt = target.with_name(f"{stem} ({i}){suf}")
                if not alt.exists():
                    target = alt
                    break
                i += 1
        shutil.move(str(media_dst), str(target))
    except Exception as e:
        log_error_txt(f"[ERRORE] Spostamento in errors fallito per {media_dst} → {e}")
        return
    log_error_txt(f"[ERRORE] {src_path} → {err_msg} (spostato in {target})")


# ------------------ Worker ------------------

def get_epoch_for_media(media_src: Path, exiftool_bin: Optional[str]) -> Tuple[Optional[int], str]:
    json_src = find_json_for_media(media_src)
    if json_src:
        ts, src_used = read_timestamp_from_json(json_src)
        if ts is not None:
            return ts, src_used

    ts, src_used = search_album_metadata(media_src)
    if ts is not None:
        return ts, src_used

    if exiftool_bin:
        ts, src_used = read_timestamp_from_media_exiftool(exiftool_bin, media_src)
        if ts is not None:
            return ts, src_used

    return None, ""


def process_one(item):
    """
    item = (media_src, src_root, dst_root, exiftool_bin, setfile_bin)
    - Copy to DEST (mirroring structure), correcting extension if content-type and extension mismatch
    - Compute epoch via sidecar/album/exif
    - Write metadata via ExifTool or fallback
    - On failure, move to DEST/errors/YYYY/MM/
    """
    media_src, src_root, dst_root, exiftool_bin, setfile_bin = item
    try:
        rel = media_src.relative_to(src_root)
    except Exception:
        rel = Path(media_src.name)

    # Decide correct dest path by sniffing real content type
    detected = sniff_filetype(media_src)
    media_dst = corrected_dest_path(dst_root, rel, detected)
    ensure_parent(media_dst)

    # Copy source file into DEST (to possibly corrected extension)
    try:
        shutil.copy2(media_src, media_dst)
    except Exception as e:
        return False, f"Copia fallita: {e}", None, media_dst

    # Determine epoch to set
    epoch, src_used = get_epoch_for_media(media_src, exiftool_bin)
    if epoch is None:
        return False, "Nessuna data disponibile (niente sidecar/album/exif)", None, media_dst

    # Write metadata
    kind = "video" if is_video_suffix(media_dst.suffix) else "image"
    if exiftool_bin:
        ok, note = write_with_exiftool(exiftool_bin, media_dst, epoch, kind)
        if ok:
            return True, "", epoch, media_dst
        ok2, _ = write_with_fallback(media_dst, epoch, kind, setfile_bin)
        if ok2:
            return False, f"ExifTool fallito; applicato solo fallback: {note}", epoch, media_dst
        return False, f"ExifTool e fallback falliti: {note}", epoch, media_dst
    else:
        ok, note = write_with_fallback(media_dst, epoch, kind, setfile_bin)
        if ok:
            return True, "", epoch, media_dst
        return False, note, epoch, media_dst


# ------------------ Main flow ------------------

def collect_media_files(src_root: Path):
    for p in src_root.rglob("*"):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
            yield p


def main():
    try:
        src_in = input("Percorso cartella SORGENTE (Takeout): ").strip()
        dst_in = input("Percorso cartella DESTINAZIONE (copia corretta): ").strip()
        dry = input("Dry-run (non copia, non modifica)? [s/N]: ").strip().lower().startswith("s")
    except KeyboardInterrupt:
        print("\nAnnullato.")
        sys.exit(1)

    src_root = Path(src_in).expanduser().resolve()
    dst_root = Path(dst_in).expanduser().resolve()

    if not src_root.exists() or not src_root.is_dir():
        print("SORGENTE non valida.")
        sys.exit(1)

    # Clean previous logs
    for f in (LOG_FILE_TXT, ERRORS_CSV):
        try:
            if os.path.exists(f):
                os.remove(f)
        except Exception:
            pass

    exiftool_bin = have_exiftool()
    setfile_bin = have_setfile()

    files = list(collect_media_files(src_root))
    total = len(files)
    if total == 0:
        print("Nessun file supportato trovato nella SORGENTE.")
        sys.exit(0)

    if dry:
        print(f"Dry-run: trovati {total} file. (Prime 20)")
        for p in files[:20]:
            print(" -", p)
        print("Fine dry-run.")
        sys.exit(0)

    dst_root.mkdir(parents=True, exist_ok=True)

    max_workers = min(32, (os.cpu_count() or 4) * 4)
    todo = [(p, src_root, dst_root, exiftool_bin, setfile_bin) for p in files]

    totals = Counter()
    errors_counter = Counter()
    error_examples = defaultdict(list)

    print(f"Elaborazione parallela con {max_workers} worker…")
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(process_one, item): item[0] for item in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            src_path = futures[fut]
            try:
                ok, err, epoch_used, media_dst = fut.result()
            except Exception as e:
                ok, err, epoch_used, media_dst = False, f"Eccezione worker: {e}", None, None

            if ok:
                totals["ok"] += 1
            else:
                totals["error"] += 1
                msg = err or "Errore sconosciuto"
                errors_counter[msg] += 1
                if len(error_examples[msg]) < 5:
                    error_examples[msg].append(str(src_path))
                if media_dst and media_dst.exists():
                    move_to_errors(dst_root, media_dst, src_path, epoch_used, msg)
                else:
                    log_error_txt(f"[ERRORE] {src_path} → {msg}")

            if i % 1000 == 0 or i == total:
                print(f"… {i}/{total} completati")

    with open(ERRORS_CSV, "w", newline="", encoding="utf-8") as errcsv:
        w = csv.writer(errcsv)
        w.writerow(["errore", "conteggio", "esempi (max 5)"])
        for err, cnt in errors_counter.most_common():
            w.writerow([err, cnt, " | ".join(error_examples[err])])

    print("\n--- Riepilogo ---")
    print(f"File totali analizzati: {total}")
    print(f"  ✓ Corretti (scritti in DEST): {totals['ok']}")
    print(f"  ✗ Errori (spostati in DEST/errors/YYYY/MM): {totals['error']}")
    print(f"Log errori: {LOG_FILE_TXT}")
    print(f"Errori compattati: {ERRORS_CSV}")
    print(f"Cartella DEST: {dst_root}")


if __name__ == "__main__":
    from pathlib import Path
    main()
