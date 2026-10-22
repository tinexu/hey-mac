"""Find files when you don't remember the name.

smart_find: combines several Spotlight searches (name variants, folder names,
words inside documents) and ranks the results.
peek_file: reads the start of a file so the AI can check it's the right one.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
import zipfile
from pathlib import Path

SKIP_PARTS = {
    "Library", "node_modules", ".git", ".venv", "venv", "__pycache__", ".Trash", "Caches",
    ".cache", "build", "dist", ".next", "Pods", "site-packages",
}
SKIP_EXT = {".app", ".dylib", ".so", ".o", ".pyc", ".class", ".lock", ".ds_store", ".icloud"}
DOC_EXT = {".pdf", ".docx", ".doc", ".pptx", ".txt", ".md", ".rtf", ".pages", ".html", ".htm",
           ".ipynb", ".tex", ".odt", ".key", ".xlsx", ".csv"}


# normalizing
def norm(s: str) -> str:
    """'HW_02' -> 'hw2', 'CS 101' -> 'cs101'."""
    s = re.sub(r"[^a-z0-9]", "", s.lower())
    return re.sub(r"(?<![0-9])0+(?=[1-9])", "", s)   # drop leading zeros: hw02 -> hw2


def _keys(text: str) -> tuple[str, set[str]]:
    """Normalised full string + tokens and adjacent-token joins ('HW_02(1)' -> hw, 2, 1, hw2, 21)."""
    toks = [norm(t) for t in re.split(r"[^A-Za-z0-9]+|(?<=[a-z])(?=[A-Z][a-z])", text) if t]
    toks = [t for t in toks if t]
    keys = set(toks) | {a + b for a, b in zip(toks, toks[1:])}
    return norm(text), keys


def matches(term: str, full: str, keys: set[str]) -> bool:
    """Short terms ('a2', 'hw2') must match whole tokens; longer ones can match anywhere."""
    t = norm(term)
    if not t:
        return False
    return t in keys if len(t) <= 4 else t in full


def _md(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("*", "")


def _mdfind(query: str, onlyin: str, timeout: float = 8) -> list[str]:
    try:
        r = subprocess.run(["mdfind", "-onlyin", onlyin, query],
                           capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    return [l for l in r.stdout.splitlines() if l.strip()][:400]


def _ok(path: str) -> bool:
    p = Path(path)
    if set(p.parts) & SKIP_PARTS:
        return False
    if any(part.startswith(".") for part in p.parts[1:]):
        return False
    return p.suffix.lower() not in SKIP_EXT


def _name_query(term: str) -> str:
    words = [w for w in re.split(r"[\s_\-./]+", term) if w]
    return "(" + " && ".join(f'kMDItemFSName == "*{_md(w)}*"cd' for w in words) + ")"


def _files_under(folder: str, max_depth: int = 4, max_files: int = 400) -> list[str]:
    out: list[str] = []
    base_depth = folder.rstrip("/").count("/")
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d not in SKIP_PARTS and not d.startswith(".")]
        if dirpath.count("/") - base_depth >= max_depth:
            dirnames[:] = []
        for f in filenames:
            if not f.startswith("."):
                out.append(os.path.join(dirpath, f))
                if len(out) >= max_files:
                    return out
    return out


# smart_find
def smart_find(name_terms: list[str], content_terms: list[str] | None = None,
               folder_terms: list[str] | None = None, file_types: list[str] | None = None,
               home: str | None = None, fallback_roots: list[str] | None = None,
               limit: int = 8, mdfind=None) -> list[tuple[str, float, list[str]]]:
    """Returns [(path, score, reasons)] best first."""
    mdfind = mdfind or _mdfind
    home = home or str(Path.home())
    content_terms = [t for t in (content_terms or []) if t.strip()]
    folder_terms = [t for t in (folder_terms or []) if t.strip()]
    name_terms = [t for t in name_terms if t.strip()]
    types = {("." + t.lower().lstrip(".")) for t in (file_types or [])}

    candidates: dict[str, set[str]] = {}

    def add(paths, why):
        for p in paths:
            if _ok(p):
                candidates.setdefault(p, set()).add(why)

    # 1) file names containing any variant
    for t in name_terms:
        add(mdfind(_name_query(t), home), "name")
    # 2) folders matching (e.g. a "CS101" folder) → everything inside
    folders = []
    for t in folder_terms:
        folders += [f for f in mdfind(f"{_name_query(t)} && kMDItemContentType == public.folder", home) if _ok(f)]
    for f in folders[:6]:
        add(_files_under(f), "folder")
    # 3) words inside documents (Spotlight indexes PDF/Word/etc. text)
    for t in content_terms[:4]:
        add(mdfind(f'kMDItemTextContent == "*{_md(t)}*"cd', home), "content")
    content_group: set[str] = set()     # documents that mention the class/project inside them
    for t in folder_terms[:2]:
        hits = mdfind(f'kMDItemTextContent == "*{_md(t)}*"cd', home)
        content_group.update(hits)
        add(hits, "content")
    # 4) no Spotlight results at all → walk the usual folders by name
    if not candidates and fallback_roots:
        for root in fallback_roots:
            for p in _files_under(root, max_depth=6, max_files=4000):
                full, keys = _keys(os.path.relpath(p, root))
                if any(matches(w, full, keys) for w in name_terms + folder_terms):
                    add([p], "walk")

    # ------------------------------------------------------------- scoring
    # Two questions per file: does it match WHAT (hw2) and WHICH (cs101)? Both → strong.
    now = time.time()
    scored = []
    for path, whys in candidates.items():
        rel = os.path.relpath(path, home) if path.startswith(home) else path
        fname = os.path.basename(path)
        ext = os.path.splitext(fname)[1].lower()
        f_full, f_keys = _keys(os.path.splitext(fname)[0])
        r_full, r_keys = _keys(rel)
        score, reasons = 0.0, []

        if any(matches(t, r_full, r_keys) for t in name_terms):
            score += 3
            reasons.append("name" if any(matches(t, f_full, f_keys) for t in name_terms) else "folder")
            if any(matches(t, f_full, f_keys) for t in name_terms):
                score += 0.5
        if any(matches(t, r_full, r_keys) for t in folder_terms) or path in content_group:
            score += 3
            reasons.append("class/project")
        if "content" in whys:
            score += 1.5
            reasons.append("text inside")
        if any(matches(c, f_full, f_keys) for c in content_terms):
            score += 1
        if types:
            score += 1.5 if ext in types else -2
        elif ext in DOC_EXT:
            score += 1
        try:
            age_days = (now - os.path.getmtime(path)) / 86400
        except OSError:
            age_days = 999
        score += 0.8 / (1 + age_days / 60)              # recent files win ties
        score -= min(rel.count("/"), 14) * 0.02
        if score > 2:
            scored.append((path, round(score, 2), reasons))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]


# ------------------------------------------------------------- peek_file
def peek_file(path: str, max_chars: int = 1500) -> str:
    """Best-effort plain text from the start of a document."""
    p = os.path.expanduser(path)
    if not os.path.isfile(p):
        return "(not a file)"
    ext = os.path.splitext(p)[1].lower()
    text = ""
    try:
        if ext == ".pdf":
            text = _pdf_text(p, max_chars)
        elif ext in {".docx", ".doc", ".rtf", ".odt", ".html", ".htm", ".webarchive"}:
            r = subprocess.run(["textutil", "-convert", "txt", "-stdout", p],
                               capture_output=True, text=True, timeout=10)
            text = r.stdout
        elif ext == ".pptx":
            with zipfile.ZipFile(p) as z:
                slides = sorted(n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml", n))
                for n in slides[:6]:
                    text += " ".join(re.findall(r"<a:t>([^<]*)</a:t>", z.read(n).decode("utf8", "ignore"))) + "\n"
        elif ext == ".ipynb":
            import json
            nb = json.load(open(p, encoding="utf8", errors="ignore"))
            text = "\n".join("".join(c.get("source", [])) for c in nb.get("cells", [])[:10])
        elif os.path.getsize(p) < 5_000_000:
            with open(p, encoding="utf8", errors="ignore") as f:
                text = f.read(max_chars * 2)
    except Exception as e:  # noqa: BLE001
        return f"(couldn't read: {e})"
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars] or "(no readable text — maybe a scan or image)"


def _pdf_text(path: str, max_chars: int) -> str:
    try:
        from Foundation import NSURL
        from Quartz import PDFDocument          # PDFKit, ships with pyobjc-framework-Quartz
        doc = PDFDocument.alloc().initWithURL_(NSURL.fileURLWithPath_(path))
        if doc is None:
            return ""
        out = ""
        for i in range(min(doc.pageCount(), 3)):
            out += (doc.pageAtIndex_(i).string() or "") + "\n"
            if len(out) > max_chars:
                break
        return out
    except ImportError:
        pass
    try:
        from pypdf import PdfReader
        r = PdfReader(path)
        return "\n".join((pg.extract_text() or "") for pg in r.pages[:3])
    except Exception:  # noqa: BLE001
        return ""


# ------------------------------------------------------------- recent files
def recent_files(kind: str = "any", limit: int = 5, days: int = 30, home: str | None = None,
                 mdfind=None) -> list[str]:
    """Newest files first. kind: screenshot, download, image, pdf, document, any."""
    mdfind = mdfind or _mdfind
    home = home or str(Path.home())
    cutoff = time.time() - days * 86400
    found: list[str] = []
    if kind == "download":
        d = os.path.join(home, "Downloads")
        if os.path.isdir(d):
            found = [os.path.join(d, f) for f in os.listdir(d) if not f.startswith(".")]
    elif kind == "screenshot":
        found = mdfind("kMDItemIsScreenCapture == 1", home)
        for d in ("Desktop", "Pictures/Screenshots", "Documents/Screenshots"):     # common save spots
            full = os.path.join(home, d)
            if os.path.isdir(full):
                found += [os.path.join(full, f) for f in os.listdir(full)
                          if re.match(r"(Screenshot|Screen Shot|CleanShot)", f)]
    else:
        q = {
            "image": "kMDItemContentTypeTree == public.image",
            "pdf": "kMDItemContentType == com.adobe.pdf",
            "document": "kMDItemContentTypeTree == public.content",
        }.get(kind, "kMDItemContentTypeTree == public.item")
        found = mdfind(f"{q} && kMDItemFSContentChangeDate >= $time.today(-{days})", home)
    out = []
    for p in set(found):
        try:
            if os.path.isfile(p) and _ok(p) and os.path.getmtime(p) >= cutoff:
                out.append(p)
        except OSError:
            pass
    out.sort(key=os.path.getmtime, reverse=True)
    return out[:limit]