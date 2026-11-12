"""Godot project discovery, configuration parser, and file utilities."""

from __future__ import annotations

import fnmatch
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_IGNORED_DIRS = {
    ".godot",
    ".git",
    ".import",
    ".github",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
}

# Directory ignored only when it sits at the project root (like Godot's own plugin folder).
ADDONS_DIR = "addons"

# Scripts whose base class makes the engine/test runner discover methods by name.
_TEST_SCRIPT_REGEX = re.compile(
    r"^[ \t]*(?:class_name[ \t]+\w+[ \t]+)?extends[ \t]+"
    r"(?:(?:GutTest|GdUnitTestSuite)\b|[\"']res://addons/gut/test\.gd[\"'])",
    re.MULTILINE,
)

# A res:// literal, optionally with printf-style placeholders (%s, %d...) built at runtime.
_RES_LITERAL_REGEX = re.compile(r"res://[a-zA-Z0-9_./\-+%]+")

# Word tokens counted in GodotProject.word_counts (identifiers, keywords, class names).
WORD_REGEX = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b")

ASSET_EXTENSIONS = {
    # 2D & UI
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".svg",
    ".bmp",
    # Audio
    ".wav",
    ".ogg",
    ".mp3",
    # Fonts
    ".ttf",
    ".otf",
    ".woff",
    ".woff2",
    # Shaders
    ".gdshader",
    ".shader",
    # 3D Assets
    ".glb",
    ".gltf",
    ".obj",
    ".blend",
    ".fbx",
    # Godot Resource Types
    ".tres",
    ".res",
    ".material",
    ".theme",
    ".fontdata",
    ".anim",
}

TEXT_EXTENSIONS = {
    ".gd",
    ".tscn",
    ".tres",
    ".res",
    ".gdshader",
    ".shader",
    ".json",
    ".cfg",
}


@dataclass(frozen=True)
class AutoloadEntry:
    """Represents an autoload entry configured in project.godot."""

    name: str
    path: str
    is_singleton: bool = True


def is_test_script(clean_source: str) -> bool:
    """Return True if the script extends GutTest, GdUnitTestSuite or GUT's test.gd.

    Pass source with comments already masked so a commented-out ``extends`` is not detected.
    """
    return _TEST_SCRIPT_REGEX.search(clean_source) is not None


def dynamic_res_prefix(literal: str) -> str | None:
    """Return the ``res://`` prefix covered by a dynamically built path literal.

    A literal ending in ``/`` (folder concatenated with an id at runtime) or containing ``%``
    (``%s``, ``%d`` placeholders) stands for every resource starting with the text before the
    first ``%`` (or the whole folder). To avoid silencing the whole project, the prefix must
    contain at least two folder segments, or one folder plus a partial file name. Returns None
    when the literal is not dynamic or the prefix is too short.
    """
    if not literal.startswith("res://"):
        return None
    if "%" in literal:
        prefix = literal.split("%", 1)[0]
    elif literal.endswith("/"):
        prefix = literal
    else:
        return None

    sub = prefix[len("res://") :].replace("\\", "/")
    segments = sub.split("/")
    if ".." in segments or "." in segments:
        return None
    if sub.endswith("/"):
        folders = [seg for seg in segments if seg]
        if len(folders) < 2:
            return None
        return "res://" + "/".join(folders) + "/"
    # Last segment is a partial file name inside a folder.
    folders = [seg for seg in segments[:-1] if seg]
    partial = segments[-1]
    if not folders or not partial:
        return None
    return "res://" + "/".join(folders) + "/" + partial


def is_binary_file(file_path: Path) -> bool:
    """Check if a file contains null bytes in its first 1024 bytes.

    Used to detect corrupted or binary files with text extensions.
    """
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(1024)
            return b"\x00" in chunk
    except OSError:
        return False


def normalize_res_path(res_path: str) -> str:
    """Normalize a Godot 'res://' path eliminating redundant components."""
    clean = res_path.strip().replace("\\", "/")
    if clean.startswith("res://"):
        sub = clean[len("res://") :].lstrip("/")
        parts: list[str] = []
        for seg in sub.split("/"):
            if seg == "." or not seg:
                continue
            if seg == "..":
                if parts:
                    parts.pop()
                else:
                    parts.append("..")
            else:
                parts.append(seg)
        return f"res://{'/'.join(parts)}"
    return clean


def find_project_root(start_path: Path | str) -> Path:
    """Locate the Godot project root containing 'project.godot'.

    Traverses upwards from the provided path, and if not found, searches one level
    downwards to accommodate repository subfolder roots.
    """
    current = Path(start_path).resolve()

    if current.is_file():
        if current.name == "project.godot":
            return current.parent
        current = current.parent

    # Traverse upwards
    candidate = current
    while candidate != candidate.parent:
        if (candidate / "project.godot").is_file():
            return candidate
        candidate = candidate.parent

    # If root reached and not found, check immediate subdirectories
    for child in current.iterdir():
        if child.is_dir() and (child / "project.godot").is_file():
            return child

    raise FileNotFoundError(
        f"Could not locate 'project.godot' in '{start_path}' or its parent/child directories."
    )


def res_to_path(res_path: str, project_root: Path) -> Path:
    """Convert a Godot 'res://' URI to an absolute filesystem Path safely.

    Prevents directory traversal attacks and UNC path access.
    """
    raw = res_path.strip()

    # Reject UNC paths
    if raw.startswith(("//", "\\\\")):
        raise ValueError(f"UNC paths are not permitted: '{res_path}'")

    if raw.startswith("res://"):
        clean = raw[len("res://") :]
    else:
        clean = raw

    if clean.startswith(("//", "\\\\")):
        raise ValueError(f"UNC paths are not permitted: '{res_path}'")

    # Strip drive letter if present (e.g. C:, D:)
    clean = re.sub(r"^[a-zA-Z]:", "", clean)
    # Strip leading slashes to prevent Path / from discarding root
    clean = clean.lstrip("/\\")

    resolved_root = project_root.resolve()
    target_path = (resolved_root / clean).resolve()

    if not target_path.is_relative_to(resolved_root):
        raise ValueError(f"Path traversal detected: '{res_path}' resolves outside project root.")

    return target_path


def path_to_res(file_path: Path, project_root: Path) -> str:
    """Convert an absolute or relative filesystem Path to a Godot 'res://' URI."""
    abs_path = file_path.resolve()
    abs_root = project_root.resolve()
    try:
        rel = abs_path.relative_to(abs_root)
        return f"res://{rel.as_posix()}"
    except ValueError:
        # Not within project root; return formatted relative
        return f"res://{file_path.as_posix()}"


def parse_project_godot(project_file: Path) -> dict[str, Any]:
    """Parse a Godot 3/4 'project.godot' INI-style configuration file."""
    if not project_file.is_file():
        raise FileNotFoundError(f"Project file not found: {project_file}")

    sections: dict[str, dict[str, str]] = {}
    current_section = "root"
    sections[current_section] = {}

    content = project_file.read_text(encoding="utf-8-sig", errors="replace")

    section_regex = re.compile(r"^\[([a-zA-Z0-9_./-]+)\]$")
    prop_regex = re.compile(r"^([a-zA-Z0-9_./-]+)\s*=\s*(.*)$")

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";"):
            continue

        sec_match = section_regex.match(line)
        if sec_match:
            current_section = sec_match.group(1)
            if current_section not in sections:
                sections[current_section] = {}
            continue

        prop_match = prop_regex.match(line)
        if prop_match:
            key, val = prop_match.group(1), prop_match.group(2)
            # Unquote string if fully enclosed
            val = val.strip()
            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                val = val[1:-1]
            sections[current_section][key] = val

    # Extract high-level values
    app_section = sections.get("application", {})
    project_name = app_section.get("config/name", project_file.parent.name)
    main_scene = app_section.get("run/main_scene")

    autoloads: dict[str, AutoloadEntry] = {}
    autoload_section = sections.get("autoload", {})
    for name, raw_path in autoload_section.items():
        is_singleton = True
        path = raw_path
        if path.startswith("*"):
            is_singleton = True
            path = path[1:]
        autoloads[name] = AutoloadEntry(name=name, path=path, is_singleton=is_singleton)

    return {
        "name": project_name,
        "main_scene": main_scene,
        "autoloads": autoloads,
        "sections": sections,
    }


class GodotProject:
    """Represents a discovered Godot project with its file index, caches, and configuration."""

    def __init__(
        self,
        root_path: Path | str,
        ignore_patterns: list[str] | None = None,
        include_addons: bool = False,
    ) -> None:
        self.root: Path = find_project_root(root_path)
        self.project_godot_path: Path = self.root / "project.godot"

        self.ignore_patterns: list[str] = ignore_patterns or []
        self.include_addons: bool = include_addons
        self._gdignore_cache: dict[Path, bool] = {}

        parsed = parse_project_godot(self.project_godot_path)
        self.name: str = parsed["name"]
        self.main_scene: str | None = parsed["main_scene"]
        self.autoloads: dict[str, AutoloadEntry] = parsed["autoloads"]
        self.raw_sections: dict[str, dict[str, str]] = parsed["sections"]

        # Discovered files
        self.gd_files: list[Path] = []
        self.tscn_files: list[Path] = []
        self.tres_files: list[Path] = []
        self.asset_files: list[Path] = []
        self.all_files: list[Path] = []

        # Caches & pre-indexing
        self.file_texts: dict[Path, str] = {}
        self.known_res: set[str] = set()
        self.referenced_res: set[str] = set()
        # res:// prefixes from dynamically built paths (see dynamic_res_prefix)
        self.referenced_prefixes: set[str] = set()
        self.word_counts: Counter[str] = Counter()
        self.scene_connected_methods: set[str] = set()

        self._discover_files()
        self._index_project()

    def _dir_has_gdignore(self, directory: Path) -> bool:
        """Return True if the directory directly contains a '.gdignore' file (cached)."""
        cached = self._gdignore_cache.get(directory)
        if cached is None:
            cached = (directory / ".gdignore").is_file()
            self._gdignore_cache[directory] = cached
        return cached

    def is_ignored(self, file_path: Path) -> bool:
        """Check if a path matches ignored directory lists, '.gdignore' folders or user patterns."""
        rel = file_path.relative_to(self.root)
        rel_posix = rel.as_posix()
        parts = rel.parts

        # Check default ignored dirs in parts
        for part in parts:
            if part in DEFAULT_IGNORED_DIRS:
                return True

        # 'addons/' is ignored only when it sits at the project root
        if not self.include_addons and parts and parts[0] == ADDONS_DIR:
            return True

        # Godot ignores any folder containing '.gdignore' together with everything below it.
        # The project root itself is never treated as ignored.
        current = self.root
        directories = parts if file_path.is_dir() else parts[:-1]
        for part in directories:
            current = current / part
            if self._dir_has_gdignore(current):
                return True

        # Check custom patterns
        for pattern in self.ignore_patterns:
            clean_pat = pattern.strip()
            if not clean_pat:
                continue
            if fnmatch.fnmatch(rel_posix, clean_pat) or fnmatch.fnmatch(file_path.name, clean_pat):
                return True
            if clean_pat.endswith("/") and rel_posix.startswith(clean_pat):
                return True
        return False

    def _discover_files(self) -> None:
        """Scan project files recursively, filtering out ignored folders and patterns."""
        for root, dirs, files in os.walk(self.root):
            # Prune ignored directories in-place to avoid unnecessary traversal
            dirs[:] = [
                d
                for d in dirs
                if d not in DEFAULT_IGNORED_DIRS and not self.is_ignored(Path(root) / d)
            ]

            for file_name in files:
                full_path = Path(root) / file_name
                if self.is_ignored(full_path):
                    continue

                self.all_files.append(full_path)
                suffix = full_path.suffix.lower()

                if suffix == ".gd":
                    self.gd_files.append(full_path)
                elif suffix == ".tscn":
                    self.tscn_files.append(full_path)
                elif suffix in (".tres", ".res"):
                    self.tres_files.append(full_path)
                elif suffix in ASSET_EXTENSIONS:
                    self.asset_files.append(full_path)

    def _index_project(self) -> None:
        """Single-pass pre-indexing of project files, text cache, words, and references."""
        # 1. Populate known_res with normalized res:// paths of all project files
        for f in self.all_files:
            r = path_to_res(f, self.root)
            self.known_res.add(r)
            self.known_res.add(normalize_res_path(r))

        if self.project_godot_path.is_file():
            r_proj = path_to_res(self.project_godot_path, self.root)
            self.known_res.add(r_proj)
            self.known_res.add(normalize_res_path(r_proj))

        # 2. Read text files once into file_texts cache (skipping binary files)
        text_candidates: list[Path] = []
        if self.project_godot_path.is_file():
            text_candidates.append(self.project_godot_path)

        for f in self.gd_files + self.tscn_files + self.tres_files:
            if f not in text_candidates:
                text_candidates.append(f)

        # Include autoload target files if not already queued
        for entry in self.autoloads.values():
            try:
                auto_path = res_to_path(entry.path, self.root)
                if auto_path.is_file() and auto_path not in text_candidates:
                    text_candidates.append(auto_path)
            except (ValueError, OSError):
                pass

        for f in text_candidates:
            if is_binary_file(f):
                continue
            try:
                self.file_texts[f] = f.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue

        # 3. Populate referenced_res
        if self.main_scene:
            clean_main = self.main_scene.strip()
            self.referenced_res.add(clean_main)
            self.referenced_res.add(normalize_res_path(clean_main))

        for entry in self.autoloads.values():
            if entry.path:
                clean_auto = entry.path.strip()
                self.referenced_res.add(clean_auto)
                self.referenced_res.add(normalize_res_path(clean_auto))

        app_sec = self.raw_sections.get("application", {})
        if "config/icon" in app_sec:
            clean_icon = app_sec["config/icon"].strip()
            self.referenced_res.add(clean_icon)
            self.referenced_res.add(normalize_res_path(clean_icon))

        # 4. Single pass regex over all cached text for referenced_res and word_counts
        for text in self.file_texts.values():
            for match in _RES_LITERAL_REGEX.findall(text):
                prefix = dynamic_res_prefix(match)
                if prefix is not None:
                    self.referenced_prefixes.add(prefix)
                # Text before the first '%' placeholder is still recorded as a plain literal
                clean_match = match.split("%", 1)[0].rstrip("\"';,)]}")
                self.referenced_res.add(clean_match)
                self.referenced_res.add(normalize_res_path(clean_match))

            self.word_counts.update(WORD_REGEX.findall(text))

        # 5. Extract scene-connected methods from .tscn files
        conn_regex = re.compile(r'\[connection\b[^\]]*\bmethod=["\']([^"\']+)["\']')
        for tscn in self.tscn_files:
            text = self.file_texts.get(tscn, "")
            for m in conn_regex.findall(text):
                self.scene_connected_methods.add(m)

    def is_res_referenced(self, res_uri: str) -> bool:
        """Check whether a res:// URI is referenced exactly or through a dynamic path prefix."""
        if res_uri in self.referenced_res or normalize_res_path(res_uri) in self.referenced_res:
            return True
        return any(res_uri.startswith(prefix) for prefix in self.referenced_prefixes)

    def exists_on_disk(self, res_uri: str) -> bool:
        """Check whether a res:// URI points to an existing file, even inside ignored folders."""
        try:
            return res_to_path(res_uri, self.root).is_file()
        except (ValueError, OSError):
            return False

    @property
    def stats(self) -> dict[str, int]:
        """Summary count of discovered project files by category."""
        return {
            "gd_scripts": len(self.gd_files),
            "scenes": len(self.tscn_files),
            "resources": len(self.tres_files),
            "assets": len(self.asset_files),
            "total_files": len(self.all_files),
        }
