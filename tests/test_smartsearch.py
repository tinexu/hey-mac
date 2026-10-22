"""smart_find against a realistic messy folder tree, with a fake Spotlight."""
import os
import re
import zipfile

from heymac import smartsearch
from heymac.smartsearch import norm, peek_file, smart_find


def make_tree(root):
    files = {
        "Documents/School/CS101/HW2/writeup.pdf": "CS 101 Homework 2 instructions. Due Friday. Submit on Gradescope.",
        "Documents/School/CS101/HW2/hw2_solution.py": "def solve(): pass",
        "Documents/School/CS101/HW1/writeup.pdf": "CS 101 Homework 1 instructions",
        "Documents/School/CS101/lecture03.pdf": "lecture notes graphs",
        "Downloads/HW_02(1).pdf": "Homework 2 CS101 instructions copy",
        "Downloads/MA261_hw2.pdf": "Math 261 homework 2",
        "Documents/resume.pdf": "Christine resume",
        "Documents/School/CS101/.DS_Store": "",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return files


def fake_mdfind_for(root):
    """Understands just enough Spotlight syntax: FSName wildcards, folder type, TextContent."""
    def mdfind(query, onlyin, timeout=8):
        names = re.findall(r'kMDItemFSName == "\*([^*"]+)\*"cd', query)
        content = re.findall(r'kMDItemTextContent == "\*([^*"]+)\*"cd', query)
        want_folder = "public.folder" in query
        out = []
        for dirpath, dirnames, filenames in os.walk(root):
            entries = [(d, True) for d in dirnames] + [(f, False) for f in filenames]
            for name, is_dir in entries:
                path = os.path.join(dirpath, name)
                if want_folder != is_dir and names:
                    continue
                if names and all(w.lower() in name.lower() for w in names):
                    out.append(path)
                elif content and not is_dir:
                    txt = open(path, errors="ignore").read().lower()
                    if all(c.lower() in txt for c in content):
                        out.append(path)
        return out
    return mdfind


NAME_TERMS = ["hw2", "hw 2", "homework2", "homework 2", "assignment 2", "cs101 hw2"]
FOLDER_TERMS = ["cs101", "cs 101"]
CONTENT_TERMS = ["homework 2", "instructions"]


def test_norm():
    assert norm("HW_02") == "hw2"
    assert norm("CS 101") == "cs101"
    assert norm("hw10") == "hw10"      # don't eat real zeros


def test_finds_hw2_instructions(tmp_path):
    make_tree(tmp_path)
    res = smart_find(NAME_TERMS, CONTENT_TERMS, FOLDER_TERMS, home=str(tmp_path),
                     mdfind=fake_mdfind_for(tmp_path))
    paths = [os.path.relpath(p, tmp_path) for p, _, _ in res]
    print("\n".join(f"{s:5}  {p}  {r}" for (p0, s, r), p in zip(res, paths)))
    top2 = set(paths[:2])
    assert "Documents/School/CS101/HW2/writeup.pdf" in top2
    assert "Downloads/HW_02(1).pdf" in top2
    assert "Downloads/MA261_hw2.pdf" not in paths[:3]          # other class's hw2 ranks lower
    assert "Documents/resume.pdf" not in paths
    assert not any(p.endswith(".DS_Store") for p in paths)


def test_pdf_type_filter(tmp_path):
    make_tree(tmp_path)
    res = smart_find(NAME_TERMS, [], FOLDER_TERMS, ["pdf"], home=str(tmp_path),
                     mdfind=fake_mdfind_for(tmp_path))
    assert res[0][0].endswith(".pdf")
    assert not any(p.endswith(".py") for p, _, _ in res[:3])


def test_walk_fallback_when_spotlight_empty(tmp_path):
    make_tree(tmp_path)
    res = smart_find(NAME_TERMS, CONTENT_TERMS, FOLDER_TERMS, home=str(tmp_path),
                     mdfind=lambda *a, **k: [], fallback_roots=[str(tmp_path)])
    assert any(p.endswith("HW2/writeup.pdf") for p, _, _ in res[:3])


def test_peek_text_and_pptx(tmp_path):
    t = tmp_path / "notes.md"
    t.write_text("# HW2\n\nInstructions:   do   the thing")
    assert peek_file(str(t)).startswith("# HW2 Instructions: do the thing")
    pp = tmp_path / "deck.pptx"
    with zipfile.ZipFile(pp, "w") as z:
        z.writestr("ppt/slides/slide1.xml", "<p><a:t>Homework 2</a:t><a:t>Due Friday</a:t></p>")
    assert "Homework 2 Due Friday" in peek_file(str(pp))
    assert peek_file(str(tmp_path / "nope.pdf")) == "(not a file)"


def test_ai_flow_search_peek_open(tmp_path, monkeypatch):
    """Claude (faked) runs smart_find -> peek_file -> open_path; check the real tools behave."""
    import subprocess
    from types import SimpleNamespace as NS
    from heymac import actions
    from heymac.actions import Mac
    from heymac.brain import Brain
    from heymac.config import Config

    make_tree(tmp_path)
    calls = []
    monkeypatch.setattr(actions, "_run", lambda cmd, timeout=10: calls.append(cmd) or
                        subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(smartsearch, "_mdfind", fake_mdfind_for(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))            # so "~" means the fake home
    # PDFKit only exists on macOS; our fake PDFs are plain text
    monkeypatch.setattr(smartsearch, "_pdf_text", lambda path, n: open(path).read())
    cfg = Config()
    cfg.search_roots = [str(tmp_path)]
    mac = Mac(cfg)

    seen = {}

    class Client:
        messages = None

        def __init__(self):
            self.messages = self
            self.step = 0

        def create(self, **kw):
            self.step += 1
            last = kw["messages"][-1]["content"]
            if self.step == 1:
                return NS(stop_reason="tool_use", content=[NS(type="tool_use", id="a", name="smart_find", input={
                    "name_terms": NAME_TERMS, "folder_terms": FOLDER_TERMS, "content_terms": CONTENT_TERMS})])
            if self.step == 2:
                seen["find"] = last[0]["content"]
                p = [l.split(". ", 1)[1] for l in seen["find"].splitlines() if l[:1].isdigit()]
                return NS(stop_reason="tool_use", content=[
                    NS(type="tool_use", id="b", name="peek_file", input={"path": p[0]}),
                    NS(type="tool_use", id="c", name="peek_file", input={"path": p[1]})])
            if self.step == 3:
                seen["peek"] = [r["content"] for r in last]
                return NS(stop_reason="tool_use", content=[NS(type="tool_use", id="d", name="open_path",
                          input={"path": "~/Documents/School/CS101/HW2/writeup.pdf"})])
            return NS(stop_reason="end_turn", content=[NS(type="text", text="Opened writeup.pdf (CS101 HW2).")])

    reply = Brain(cfg, mac, client=Client()).ask(
        "find me the file that has the instructions to my cs101 hw2 and open it")
    print(seen["find"])
    assert "Homework 2 instructions" in " ".join(seen["peek"])
    assert ["open", str(tmp_path / "Documents/School/CS101/HW2/writeup.pdf")] in calls
    assert reply.items and reply.text.startswith("Opened")