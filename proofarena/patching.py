"""Apply a defender's unified diff inside a throwaway sandbox.

``git apply`` is used because it is strict about context and paths: a patch that
does not match the pinned baseline is rejected instead of silently producing a
different tree. The patch is applied to a copy, never to the pinned fixture.

Encoding rules (learned the hard way): the fixture contains non-ASCII text, and
Windows pipes default to a legacy code page. Patches are therefore passed as
UTF-8 **bytes**, the tree under test is newline-normalised to LF first, and
``git`` is told not to touch line endings. Getting this wrong produces the worst
possible failure mode: a correct patch rejected as "does not apply".
"""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .errors import RunnerError

TEXT_SUFFIXES = {".py", ".md", ".txt", ".rst", ".json", ".toml", ".cfg", ".ini", ".yaml", ".yml"}


class PatchError(RunnerError):
    code = "E_PATCH"


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_text(path: Path) -> str:
    return normalize_newlines(path.read_text(encoding="utf-8", errors="strict"))


def make_unified_diff(before_path: Path, after_path: Path, *, label: str) -> str:
    before = read_text(before_path).splitlines(keepends=True)
    after = read_text(after_path).splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(before, after, fromfile=f"a/{label}", tofile=f"b/{label}", n=3)
    )


def _normalize_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            raw = path.read_bytes()
            fixed = raw.replace(b"\r\n", b"\n")
            if fixed != raw:
                path.write_bytes(fixed)


def _git_env() -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
    }
    if os.environ.get("SYSTEMDRIVE"):
        env["SYSTEMDRIVE"] = os.environ["SYSTEMDRIVE"]
    if os.environ.get("COMSPEC"):
        env["COMSPEC"] = os.environ["COMSPEC"]
    return env


def apply_unified_diff(
    *,
    source_dir: Path,
    diff_text: str,
    target_dir: Path,
    timeout_s: int = 30,
) -> tuple[Path, str]:
    """Copy ``source_dir`` to ``target_dir`` and apply ``diff_text`` there."""
    if not diff_text.strip():
        raise PatchError("patch is empty")
    if len(diff_text.encode("utf-8")) > 512_000:
        raise PatchError("patch exceeds the 512 KB limit")
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    shutil.copytree(source_dir, target_dir)
    for junk in ("__pycache__", ".pytest_cache"):
        for path in list(target_dir.rglob(junk)):
            shutil.rmtree(path, ignore_errors=True)
    _normalize_tree(target_dir)

    patch_bytes = normalize_newlines(diff_text).encode("utf-8")
    try:
        proc = subprocess.run(
            [
                "git",
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.safecrlf=false",
                "apply",
                "--quiet",
                "--whitespace=nowarn",
                "-",
            ],
            input=patch_bytes,
            capture_output=True,
            cwd=str(target_dir),
            timeout=timeout_s,
            env=_git_env(),
        )
    except FileNotFoundError as exc:
        raise PatchError(
            "git is not installed; cannot verify applied patches. "
            "Install git or run verification in the pinned image."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PatchError("applying the patch timed out") from exc

    stderr = proc.stderr.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        raise PatchError(f"patch does not apply to the pinned source: {stderr[:400]}")
    return target_dir, proc.stdout.decode("utf-8", errors="replace")


def diff_touches_only(diff_text: str, allowed: set[str]) -> list[str]:
    """Return files a diff touches that are outside the authorized scope."""
    touched: list[str] = []
    for line in normalize_newlines(diff_text).splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            name = line[4:].strip().split("\t")[0]
            if name.startswith(("a/", "b/")):
                name = name[2:]
            if name and name != "/dev/null" and name not in touched:
                touched.append(name)
    return [name for name in touched if name not in allowed]


def python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
