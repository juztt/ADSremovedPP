"""
core.apk_meta
=============

Read an Android APK far enough to recover the two things a technician actually
looks at on screen: the **launcher icon** and the **human-readable app label**.

Why this module exists
----------------------
``pm list packages`` prints package names, never app names. ``dumpsys package``
prints a *resource id* (``labelRes=0x7f13000c``), not the string behind it.
Resolving that id means reading two binary formats Android never documents
in a friendly way:

* ``AndroidManifest.xml`` — **AXML** (Android binary XML)
* ``resources.arsc``     — the compiled resource table

Both parsers below are written against the published ``ResourceTypes.h``
layout. They are deliberately tolerant: a malformed chunk makes the whole read
fail *softly*, and the caller falls back to the package name — which is always
better than crashing a scan that has 300 other apps to show.

Supported resource-id resolution
---------------------------------
* ``TYPE_STRING`` in the manifest  → the literal label, straight from the
  manifest's own string pool. No resource table needed.
* ``TYPE_REFERENCE`` in the manifest → a resource id such as ``0x7f13000c``,
  resolved through ``resources.arsc`` by walking the package → type → entry
  chain and finally the global string pool.

Both the "normal" (``ResTable_entry``) and the newer "compact" entry encodings
are handled, because AAPT2 emits the compact form for most modern APKs.
"""

from __future__ import annotations

import re
import struct
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Chunk type constants
# ---------------------------------------------------------------------------

RES_STRING_POOL_TYPE = 0x0001
RES_XML_TYPE = 0x0003
RES_XML_START_TAG_TYPE = 0x0102
RES_XML_RESOURCE_MAP_TYPE = 0x0180
RES_TABLE_TYPE = 0x0002
RES_TABLE_PACKAGE_TYPE = 0x0200
RES_TABLE_TYPE_TYPE = 0x0201
RES_TABLE_TYPE_SPEC_TYPE = 0x0202

#: ``Res_value::dataType`` values we care about.
TYPE_NULL = 0x00
TYPE_REFERENCE = 0x01
TYPE_STRING = 0x03

#: ``ResStringPool_header::flags`` — the pool stores UTF-8 instead of UTF-16.
UTF8_FLAG = 0x00000100

#: ``ResTable_entry::flags``
ENTRY_FLAG_COMPLEX = 0x0001
ENTRY_FLAG_COMPACT = 0x0008


# ---------------------------------------------------------------------------
# Small binary helpers
# ---------------------------------------------------------------------------


def _u16(data: bytes, at: int) -> int:
    return struct.unpack_from("<H", data, at)[0]


def _u32(data: bytes, at: int) -> int:
    return struct.unpack_from("<I", data, at)[0]


def _decode_len16(data: bytes, at: int) -> tuple[int, int]:
    """Read a UTF-16 length prefix. Returns ``(value, bytes_consumed)``."""
    value = _u16(data, at)
    if value & 0x8000:
        high = value & 0x7FFF
        low = _u16(data, at + 2)
        return ((high << 16) | low), 4
    return value, 2


def _decode_len8(data: bytes, at: int) -> tuple[int, int]:
    """Read a UTF-8 length prefix. Returns ``(value, bytes_consumed)``."""
    value = data[at]
    if value & 0x80:
        return ((value & 0x7F) << 8) | data[at + 1], 2
    return value, 1


def _looks_like_image(data: bytes) -> bool:
    """Sniff the file signature so we never hand Qt a compiled XML resource."""
    if not data or len(data) < 4:
        return False
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return False


# ---------------------------------------------------------------------------
# String pool
# ---------------------------------------------------------------------------


class _StringPool:
    """One ``ResStringPool_header`` chunk and the strings it points at."""

    def __init__(self, data: bytes, offset: int) -> None:
        self.strings: list[str] = []
        self.next_offset = offset
        self._read(data, offset)

    def get(self, index: int) -> str:
        """Return the string at ``index``, or ``""`` when out of range."""
        if index < 0 or index >= len(self.strings):
            return ""
        return self.strings[index]

    # -- internals ---------------------------------------------------------

    def _read(self, data: bytes, offset: int) -> None:
        if offset + 28 > len(data):
            self.next_offset = offset
            return
        (
            _ctype,
            header_size,
            chunk_size,
            string_count,
            _style_count,
            flags,
            strings_start,
            _styles_start,
        ) = struct.unpack_from("<HHIIIIII", data, offset)

        if chunk_size < 28 or offset + chunk_size > len(data):
            self.next_offset = offset + max(chunk_size, 0)
            return

        is_utf8 = bool(flags & UTF8_FLAG)
        base = offset + strings_start
        offsets_at = offset + header_size

        for index in range(string_count):
            slot = offsets_at + 4 * index
            if slot + 4 > len(data):
                break
            try:
                entry_offset = _u32(data, slot)
            except struct.error:
                break
            self.strings.append(self._read_string(data, base + entry_offset, is_utf8))

        self.next_offset = offset + chunk_size

    @staticmethod
    def _read_string(data: bytes, at: int, is_utf8: bool) -> str:
        if at < 0 or at + 2 > len(data):
            return ""
        try:
            if is_utf8:
                # Layout: [utf16 length][byte length][bytes][0x00]
                _char_length, n1 = _decode_len8(data, at)
                byte_length, n2 = _decode_len8(data, at + n1)
                start = at + n1 + n2
                raw = data[start:start + byte_length]
                return raw.decode("utf-8", errors="replace")
            # Layout: [length in chars][UTF-16LE chars][0x0000]
            length, header = _decode_len16(data, at)
            start = at + header
            raw = data[start:start + length * 2]
            return raw.decode("utf-16-le", errors="replace")
        except (struct.error, IndexError, UnicodeDecodeError):
            return ""


# ---------------------------------------------------------------------------
# AXML — AndroidManifest.xml
# ---------------------------------------------------------------------------


def _iter_chunks(data: bytes, start: int) -> list[tuple[int, int, int, int]]:
    """Walk the chunk chain of a binary resource file.

    Returns ``[(type, header_size, size, offset), …]``.
    """
    chunks: list[tuple[int, int, int, int]] = []
    offset = start
    total = len(data)
    while offset + 8 <= total:
        try:
            ctype = _u16(data, offset)
            header_size = _u16(data, offset + 2)
            size = _u32(data, offset + 4)
        except struct.error:
            break
        if size < 8 or offset + size > total:
            break
        chunks.append((ctype, header_size, size, offset))
        offset += size
    return chunks


def _manifest_label(manifest: bytes) -> tuple[str, int]:
    """Return ``(literal_label, resource_id)`` for ``<application android:label>``.

    ``literal_label`` is non-empty when the manifest carries the text inline.
    ``resource_id`` is non-zero when the label points at a string resource and
    the caller must consult ``resources.arsc``.

    Two layouts are supported because both occur in the wild:

    * **Flat** (what AAPT2 emits for ``AndroidManifest.xml``) — the START_TAG
      nodes are siblings of the string pool, with no enclosing tree chunk.
    * **Nested** — the nodes live inside a ``RES_XML_TYPE`` (0x0003) chunk.

    Both are walked, so the parser does not care which one it is handed.
    """
    pool: _StringPool | None = None
    chunks = _iter_chunks(manifest, 8)

    # Pass 1 — the string pool always comes first.
    for ctype, _header_size, _size, offset in chunks:
        if ctype == RES_STRING_POOL_TYPE:
            pool = _StringPool(manifest, offset)
            break
    if pool is None:
        return ("", 0)

    # Pass 2 — look for the <application> tag.
    for ctype, _header_size, size, offset in chunks:
        if ctype == RES_XML_START_TAG_TYPE:
            found = _read_start_tag(manifest, offset, pool)
            if found is not None:
                return found
        elif ctype == RES_XML_TYPE:
            # Nested layout: walk the children of the tree chunk.
            node_at = offset + _header_size
            node_end = offset + size
            while node_at + 16 <= node_end:
                try:
                    ntype = _u16(manifest, node_at)
                    nsize = _u32(manifest, node_at + 4)
                except struct.error:
                    break
                if nsize < 16 or node_at + nsize > node_end:
                    break
                if ntype == RES_XML_START_TAG_TYPE:
                    found = _read_start_tag(manifest, node_at, pool)
                    if found is not None:
                        return found
                node_at += nsize

    return ("", 0)


def _read_start_tag(data: bytes, node_at: int, pool: _StringPool) -> tuple[str, int] | None:
    """Inspect one START_TAG node; return the label when the tag is ``application``.

    Layout of a START_TAG node::

        +0   ResChunk_header      type=0x0102, headerSize=16, size
        +8   uint32               line number
        +12  uint32               comment reference
        +16  ResXMLTree_attrExt   ns(4) name(4) attributeStart(2) attributeSize(2)
                                  attributeCount(2) id/class/style(6)
        +36  ResXMLTree_attribute[]

    ``attributeStart`` is measured **from the start of the attrExt** (which
    begins at node+16), not from the start of the node — getting that wrong
    shifts every attribute by 16 bytes and silently mis-reads the tag name as
    the first attribute.
    """
    try:
        _ns, name_index, attr_start, attr_size, attr_count = struct.unpack_from(
            "<IIHHH", data, node_at + 16
        )
    except struct.error:
        return None

    if pool.get(name_index) != "application":
        return None
    if attr_count == 0 or attr_size == 0:
        return ("", 0)

    base = node_at + 16 + attr_start
    for index in range(attr_count):
        at = base + index * attr_size
        if at + 20 > len(data):
            break
        try:
            _a_ns, a_name, _a_raw, _v_size, _v_res0, v_type, v_data = struct.unpack_from(
                "<IIIHBBI", data, at
            )
        except struct.error:
            break
        if pool.get(a_name) != "label":
            continue
        if v_type == TYPE_STRING:
            return (pool.get(v_data), 0)
        if v_type == TYPE_REFERENCE:
            return ("", v_data)
        # TYPE_NULL or something exotic — ignore and keep looking.
    return ("", 0)


# ---------------------------------------------------------------------------
# ARSC — resources.arsc
# ---------------------------------------------------------------------------


class _ResourceTable:
    """The compiled resource table, enough to resolve a string resource id."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._strings: list[str] = []
        self._packages: list[tuple[int, int, int]] = []   # (package_id, chunk_start, chunk_end)
        self._read(data)

    # -- public ------------------------------------------------------------

    def resolve_string(self, resource_id: int, _depth: int = 0) -> str:
        """Return the string behind ``resource_id`` (e.g. ``0x7f13000c``).

        Resource **aliases** are followed: a string resource may point at
        another resource rather than at a literal —

            @string/app_name  →  @0x7f130116  →  "Google Docs"

        — and the label of many Google apps is wired up exactly that way, so a
        single hop would resolve nothing. The chain is walked with a depth
        guard so a self-referencing table cannot loop forever.
        """
        if resource_id <= 0 or _depth > 6:
            return ""

        package_id = (resource_id >> 24) & 0xFF
        type_id = (resource_id >> 16) & 0xFF
        entry_index = resource_id & 0xFFFF

        for pkg_id, start, end in self._packages:
            if pkg_id != package_id:
                continue
            for ctype, header_size, size, offset in _iter_chunks(self._data, start + 288):
                if ctype != RES_TABLE_TYPE_TYPE:
                    continue
                if offset + 20 > end:
                    continue
                type_value = self._data[offset + 8]
                if type_value != type_id:
                    continue
                data_type, value = self._resolve_entry(offset, entry_index)
                if data_type == TYPE_STRING:
                    return self._strings[value] if value < len(self._strings) else ""
                if data_type == TYPE_REFERENCE:
                    return self.resolve_string(value, _depth + 1)
                return ""
        return ""

    # -- internals ---------------------------------------------------------

    def _read(self, data: bytes) -> None:
        if len(data) < 12:
            return
        for ctype, header_size, size, offset in _iter_chunks(data, 12):
            if ctype == RES_STRING_POOL_TYPE and not self._strings:
                pool = _StringPool(data, offset)
                self._strings = pool.strings
            elif ctype == RES_TABLE_PACKAGE_TYPE:
                if offset + 12 > len(data):
                    continue
                package_id = _u32(data, offset + 8)
                self._packages.append((package_id, offset, offset + size))

    def _resolve_entry(self, type_chunk: int, entry_index: int) -> tuple[int, int]:
        """Return ``(data_type, value)`` for one entry, or ``(-1, 0)``.

        ``data_type`` is the ``Res_value`` type (0x03 = string, 0x01 =
        reference) and ``value`` is the string-pool index or the target
        resource id.
        """
        data = self._data
        try:
            entry_count = _u32(data, type_chunk + 12)
            entries_start = _u32(data, type_chunk + 16)
        except struct.error:
            return (-1, 0)

        if entry_index >= entry_count or entries_start == 0:
            return (-1, 0)

        # The offset array sits immediately before the entry data, so its
        # position can be derived without knowing the exact config size.
        offsets_at = type_chunk + entries_start - 4 * entry_count
        if offsets_at < 0 or offsets_at + 4 * entry_count > len(data):
            return (-1, 0)

        try:
            offset = _u32(data, offsets_at + 4 * entry_index)
        except struct.error:
            return (-1, 0)

        if offset == 0xFFFFFFFF:
            return (-1, 0)

        entry_at = type_chunk + entries_start + offset
        if entry_at + 8 > len(data):
            return (-1, 0)

        first = _u16(data, entry_at)
        if (first >> 8) != 0 and (first & ENTRY_FLAG_COMPACT):
            # Compact encoding: the flags word carries the data type in the
            # high byte and the value follows immediately.
            return ((first >> 8) & 0xFF, _u32(data, entry_at + 2))

        # Normal encoding: size(2) flags(2) key(4) then Res_value.
        try:
            flags = _u16(data, entry_at + 2)
        except struct.error:
            return (-1, 0)
        if flags & ENTRY_FLAG_COMPLEX:
            return (-1, 0)
        try:
            data_type = data[entry_at + 11]
            value = _u32(data, entry_at + 12)
        except (struct.error, IndexError):
            return (-1, 0)
        return (data_type, value)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_app_label(apk_path: Path | str) -> str:
    """Return the launcher label of an APK, or ``""`` when it cannot be read.

    Never raises — a corrupt APK simply yields an empty string and the caller
    keeps whatever it already had (usually the package name).
    """
    try:
        with zipfile.ZipFile(Path(apk_path)) as archive:
            names = set(archive.namelist())
            if "AndroidManifest.xml" not in names:
                return ""
            manifest = archive.read("AndroidManifest.xml")
            literal, resource_id = _manifest_label(manifest)
            if literal:
                return literal.strip()
            if resource_id and "resources.arsc" in names:
                table = _ResourceTable(archive.read("resources.arsc"))
                return table.resolve_string(resource_id).strip()
    except (zipfile.BadZipFile, OSError, KeyError, ValueError, struct.error, IndexError):
        return ""
    return ""


#: Candidate resource paths, best-looking first.
_ICON_BASENAMES = (
    "ic_launcher.png",
    "ic_launcher.webp",
    "ic_launcher.jpg",
    "ic_launcher_round.png",
    "ic_launcher_round.webp",
    "ic_launcher_foreground.png",
    "icon.png",
    "app_icon.png",
)

#: Density buckets ordered from highest to lowest quality.
_MIPMAP_DIRS = (
    "mipmap-xxxhdpi",
    "mipmap-xxhdpi",
    "mipmap-xhdpi",
    "mipmap-hdpi",
    "mipmap-mdpi",
    "mipmap-anydpi-v26",
    "mipmap",
)

#: Fallback matcher for icons that do not use the conventional names.
_RE_ICON_NAME = re.compile(
    r"^res/.*/(?:ic_)?(?:launcher|icon|logo|app_icon)[a-z0-9_]*\.(?:png|webp|jpg|jpeg)$",
    re.IGNORECASE,
)

#: Ranking of density buckets — higher wins when several match.
_DENSITY_SCORE = {
    "xxxhdpi": 60,
    "xxhdpi": 50,
    "xhdpi": 40,
    "hdpi": 30,
    "mdpi": 20,
    "ldpi": 10,
    "nodpi": 15,
    "anydpi": 25,
    "anydpi-v26": 5,
}


def _icon_rank(name: str) -> int:
    """Score an icon path so the highest-density variant is chosen."""
    lowered = name.lower()
    best = 0
    for bucket, score in _DENSITY_SCORE.items():
        if f"/{bucket}/" in lowered:
            best = max(best, score)
    if best == 0:
        best = 25
    if lowered.endswith((".webp", ".jpg", ".jpeg")):
        best -= 1
    if "round" in lowered:
        best -= 2
    if "foreground" in lowered:
        best -= 3
    return best


def extract_icon_bytes(apk_path: Path | str) -> bytes | None:
    """Read a launcher icon out of a local APK, or ``None`` when unavailable.

    Only plain raster entries are considered. Android binary XML is skipped on
    purpose — decoding it needs an AXML parser we deliberately do not ship
    (the manifest parser in this module handles the *label*, not the icon
    resources, because adaptive icons point at vector drawables).
    """
    try:
        with zipfile.ZipFile(Path(apk_path)) as archive:
            names = set(archive.namelist())

            # 1. Conventional names in the well-known density buckets.
            for directory in _MIPMAP_DIRS:
                for base in _ICON_BASENAMES:
                    candidate = f"res/{directory}/{base}"
                    if candidate in names:
                        data = archive.read(candidate)
                        if _looks_like_image(data):
                            return data

            # 2. Anything else that looks like a launcher raster.
            matches = [n for n in names if _RE_ICON_NAME.match(n)]
            if matches:
                matches.sort(key=_icon_rank, reverse=True)
                for candidate in matches:
                    data = archive.read(candidate)
                    if _looks_like_image(data):
                        return data
    except (zipfile.BadZipFile, OSError, KeyError, ValueError):
        return None
    return None
