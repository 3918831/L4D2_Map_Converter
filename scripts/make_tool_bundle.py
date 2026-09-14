"""Build a minimal, verified ZIP from a local L4D2 native tools directory."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import zipfile


TOOL_FILES = (
    "vrad.exe",
    "vrad_dll.dll",
    "bspzip.exe",
    "vpk.exe",
    "tier0.dll",
    "vstdlib.dll",
    "filesystem_stdio.dll",
    "vphysics.dll",
)

README = """# L4D2 地图转换工具包

解压后，把更新后的转换器配置中的 `tools_dir` 设为解压目录下的 `bin` 文件夹，并设置 `native_mounts` 为 `resource_roots`。旧版 v0.5 转换器不包含该便携工具挂载集成。

本包只包含运行转换步骤所需的少量 Valve 原始工具文件，文件内容未修改。运行仍需要完整游戏、游戏资源及受支持的 Windows 系统；本包不包含操作系统 DLL。

这些文件用于本项目的非商业使用场景，但“非商业”并不是合法性的保证。文件权利仍属于其原权利人；使用和分发须遵守适用许可与法律。本项目不另行授予 Valve 文件的任何权利，也不替 Valve 提供许可。
"""


def _is_reparse(path):
    info = path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


def _reject_reparse_components(path, label="Path"):
    absolute = Path(os.path.abspath(path))
    components = list(reversed((absolute, *absolute.parents)))
    for component in components:
        if component.exists() or component.is_symlink():
            if _is_reparse(component):
                raise ValueError(f"{label} contains a symlink or reparse point: {component}")


def _identity(info):
    # Windows derives execute bits from a path's extension, so path stat and
    # handle fstat can disagree on st_mode for the same .exe/.dll file.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _read_stable_file(path):
    if _is_reparse(path):
        raise ValueError(f"Tool file is a symlink or reparse point: {path.name}")
    before_path = path.stat()
    with path.open("rb") as stream:
        before_handle = os.fstat(stream.fileno())
        data = stream.read()
        after_handle = os.fstat(stream.fileno())
    after_path = path.stat()
    if not stat.S_ISREG(before_path.st_mode) or not stat.S_ISREG(before_handle.st_mode):
        raise ValueError(f"Tool path is not a regular file: {path.name}")
    if (_identity(before_path) != _identity(before_handle)
            or _identity(before_handle) != _identity(after_handle)
            or _identity(after_handle) != _identity(after_path)
            or len(data) != after_handle.st_size):
        raise ValueError(f"Tool input changed or was replaced while reading: {path.name}")
    return data, _identity(after_path)


def _validate_x86_pe(name, data):
    if len(data) < 0x40:
        raise ValueError(f"PE header bounds invalid for {name}")
    if data[:2] != b"MZ":
        raise ValueError(f"DOS signature invalid for {name}")
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if pe_offset < 0x40:
        raise ValueError(f"PE offset invalid for {name}")
    if pe_offset > len(data) - 24:
        raise ValueError(f"COFF header bounds invalid for {name}")
    if data[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise ValueError(f"PE signature invalid for {name}")
    machine = struct.unpack_from("<H", data, pe_offset + 4)[0]
    if machine != 0x14C:
        raise ValueError(f"Expected x86 PE machine 0x014c for {name}, found 0x{machine:04x}")
    optional_size = struct.unpack_from("<H", data, pe_offset + 20)[0]
    if optional_size < 2 or pe_offset + 24 + optional_size > len(data):
        raise ValueError(f"PE optional header bounds invalid for {name}")
    magic = struct.unpack_from("<H", data, pe_offset + 24)[0]
    if magic != 0x10B:
        raise ValueError(f"Expected PE32 optional header for {name}, found 0x{magic:04x}")


def _write_zip(path, files):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(files):
            raise ValueError("Tool bundle failed ZIP CRC or inventory verification")
        for name, data in files.items():
            if archive.read(name) != data:
                raise ValueError(f"Tool bundle byte verification failed: {name}")


def build_bundle(source_dir: Path, output: Path) -> dict:
    """Build an exclusive, byte-verified tool ZIP and return its summary."""
    source_input = Path(source_dir)
    output_input = Path(output)
    _reject_reparse_components(source_input, "Source path")
    source = source_input.resolve(strict=True)
    if not source.is_dir():
        raise NotADirectoryError(source)
    _reject_reparse_components(output_input, "Output path")
    output = output_input.resolve(strict=False)
    if output.exists():
        raise FileExistsError(output)
    if output == source or output.is_relative_to(source):
        raise ValueError("Output archive must not be inside the tools directory")

    source_files = [source / name for name in TOOL_FILES]
    for path in source_files:
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Missing required tool file: {path.name}")

    binary_files = {}
    identities = {}
    manifest_files = []
    for name, path in zip(TOOL_FILES, source_files):
        data, identity = _read_stable_file(path)
        _validate_x86_pe(name, data)
        archive_name = f"bin/{name}"
        digest = hashlib.sha256(data).hexdigest()
        binary_files[archive_name] = data
        identities[path] = identity
        manifest_files.append({
            "source_name": name,
            "path": archive_name,
            "size": len(data),
            "sha256": digest,
            "pe_machine": "0x014c",
            "architecture": "x86",
        })

    for path, identity in identities.items():
        if _is_reparse(path) or _identity(path.stat()) != identity:
            raise ValueError(f"Tool input changed or was replaced while packaging: {path.name}")

    manifest = {
        "schema": "l4d2-map-converter-tool-bundle",
        "version": 1,
        "source": {
            "product": "Left 4 Dead 2 Authoring Tools",
            "publisher": "Valve",
            "description": "Files selected by name from the user's local tools directory",
        },
        "files": manifest_files,
    }
    inventory = "".join(
        f"{item['sha256']}  {item['path']}\n" for item in manifest_files
    ).encode("ascii")
    archive_files = dict(binary_files)
    archive_files["MANIFEST.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    archive_files["FILES.sha256"] = inventory
    archive_files["README.zh-CN.md"] = README.encode("utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
        _write_zip(temporary, archive_files)
        for path, identity in identities.items():
            if _is_reparse(path) or _identity(path.stat()) != identity:
                raise ValueError(f"Tool input changed or was replaced while packaging: {path.name}")
        try:
            if os.name == "nt":
                # Windows rename is atomic and refuses an existing destination;
                # unlike hard links, it is also supported by filesystems such as exFAT.
                os.rename(temporary, output)
            else:
                os.link(temporary, output)
                temporary.unlink()
        except FileExistsError:
            raise FileExistsError(output) from None
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return {"path": str(output), "sha256": digest, "files": len(TOOL_FILES)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_bundle(args.tools_dir, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
