import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile
from pathlib import Path


TOOLS = (
    "vrad.exe", "vrad_dll.dll", "bspzip.exe", "vpk.exe", "tier0.dll",
    "vstdlib.dll", "filesystem_stdio.dll", "vphysics.dll",
)


def synthetic_pe(payload=b"fixture", machine=0x14C, signature=b"PE\0\0", magic=0x10B):
    data = bytearray(0x9A + len(payload))
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = signature
    struct.pack_into("<H", data, 0x84, machine)
    struct.pack_into("<H", data, 0x94, 2)  # SizeOfOptionalHeader in COFF header.
    struct.pack_into("<H", data, 0x98, magic)
    data[0x9A:] = payload
    return bytes(data)


class ToolBundleTests(unittest.TestCase):
    def make_tools(self, root):
        root.mkdir()
        result = {}
        for index, name in enumerate(TOOLS):
            data = synthetic_pe(f"{name}:{index}".encode("ascii"))
            (root / name).write_bytes(data)
            result[name] = data
        return result

    def test_bundle_has_exact_allowlist_verified_bytes_and_portable_metadata(self):
        from scripts.make_tool_bundle import build_bundle
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "tools"
            expected = self.make_tools(root)
            (root / "unrelated.exe").write_bytes(synthetic_pe())
            (root / "server.dll").write_bytes(synthetic_pe())
            output = Path(temp) / "bundle.zip"

            result = build_bundle(root, output)

            self.assertEqual(result["path"], str(output.resolve()))
            self.assertEqual(result["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(result["files"], len(TOOLS))
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                self.assertEqual(names, {f"bin/{name}" for name in TOOLS} | {
                    "MANIFEST.json", "FILES.sha256", "README.zh-CN.md"
                })
                for name, data in expected.items():
                    self.assertEqual(archive.read(f"bin/{name}"), data)
                manifest = json.loads(archive.read("MANIFEST.json"))
                self.assertEqual(manifest["schema"], "l4d2-map-converter-tool-bundle")
                self.assertEqual(manifest["version"], 1)
                self.assertEqual([item["source_name"] for item in manifest["files"]], list(TOOLS))
                for item in manifest["files"]:
                    data = expected[item["source_name"]]
                    self.assertEqual(item["path"], f'bin/{item["source_name"]}')
                    self.assertEqual(item["pe_machine"], "0x014c")
                    self.assertEqual(item["architecture"], "x86")
                    self.assertEqual(item["size"], len(data))
                    self.assertEqual(item["sha256"], hashlib.sha256(data).hexdigest())
                serialized = archive.read("MANIFEST.json").decode("utf-8")
                self.assertNotIn(str(root), serialized)
                checksums = archive.read("FILES.sha256").decode("ascii").splitlines()
                self.assertEqual(len(checksums), len(TOOLS))
                self.assertEqual(set(checksums), {
                    f"{hashlib.sha256(data).hexdigest()}  bin/{name}" for name, data in expected.items()
                })
                guide = archive.read("README.zh-CN.md").decode("utf-8")
                self.assertIn("tools_dir", guide)
                self.assertIn("native_mounts", guide)
                self.assertIn("resource_roots", guide)
                self.assertIn("更新后的转换器", guide)
                self.assertIn("bin", guide)
                self.assertIn("完整游戏", guide)
                self.assertIn("Windows", guide)
                self.assertIn("未修改", guide)
                self.assertIn("原权利人", guide)
                self.assertIn("适用许可", guide)
                self.assertIn("非商业", guide)
                self.assertNotIn("不要再次分发", guide)

    def test_missing_file_leaves_no_output(self):
        from scripts.make_tool_bundle import build_bundle
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "tools"
            self.make_tools(root)
            (root / "vpk.exe").unlink()
            output = Path(temp) / "bundle.zip"
            with self.assertRaises(FileNotFoundError):
                build_bundle(root, output)
            self.assertFalse(output.exists())

    def test_rejects_bad_dos_pe_signature_truncated_header_and_non_x86(self):
        from scripts.make_tool_bundle import build_bundle
        cases = {
            "DOS signature": b"NO" + synthetic_pe()[2:],
            "PE signature": synthetic_pe(signature=b"PX\0\0"),
            "header bounds": b"MZ" + bytes(30),
            "COFF header bounds": synthetic_pe()[:0x96],
            "PE offset": b"MZ" + bytes(0x3A) + struct.pack("<I", 32) + bytes(100),
            "PE32 optional header": synthetic_pe(magic=0x20B),
            "x86": synthetic_pe(machine=0x8664),
        }
        for message, bad_data in cases.items():
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "tools"
                self.make_tools(root)
                (root / "vrad.exe").write_bytes(bad_data)
                output = Path(temp) / "bundle.zip"
                with self.assertRaisesRegex(ValueError, message):
                    build_bundle(root, output)
                self.assertFalse(output.exists())

    def test_refuses_existing_output_and_output_within_source(self):
        from scripts.make_tool_bundle import build_bundle
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "tools"
            self.make_tools(root)
            output = Path(temp) / "bundle.zip"
            output.write_bytes(b"keep")
            with self.assertRaises(FileExistsError):
                build_bundle(root, output)
            self.assertEqual(output.read_bytes(), b"keep")
            nested = root / "bundle.zip"
            with self.assertRaisesRegex(ValueError, "inside the tools directory"):
                build_bundle(root, nested)
            self.assertFalse(nested.exists())

    def test_input_replacement_during_packaging_leaves_no_output(self):
        import scripts.make_tool_bundle as bundler
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "tools"
            self.make_tools(root)
            output = Path(temp) / "bundle.zip"
            original_write = bundler._write_zip

            def replace_then_write(path, files):
                replacement = root / "replacement.tmp"
                replacement.write_bytes(synthetic_pe(b"replacement"))
                os.replace(replacement, root / "vrad.exe")
                original_write(path, files)

            with mock.patch.object(bundler, "_write_zip", side_effect=replace_then_write):
                with self.assertRaisesRegex(ValueError, "changed or was replaced"):
                    bundler.build_bundle(root, output)
            self.assertFalse(output.exists())
            self.assertEqual(list(output.parent.glob(f".{output.name}.*.tmp")), [])

    def test_publish_race_preserves_competing_output_and_cleans_own_temp(self):
        import scripts.make_tool_bundle as bundler
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "tools"
            self.make_tools(root)
            output = Path(temp) / "bundle.zip"
            original_write = bundler._write_zip

            def competitor_wins(path, files):
                original_write(path, files)
                output.write_bytes(b"competitor")

            with mock.patch.object(bundler, "_write_zip", side_effect=competitor_wins):
                with self.assertRaises(FileExistsError):
                    bundler.build_bundle(root, output)
            self.assertEqual(output.read_bytes(), b"competitor")
            self.assertEqual(list(output.parent.glob(f".{output.name}.*.tmp")), [])

    def test_rejects_dangling_symlink_output_without_following_it(self):
        from scripts.make_tool_bundle import build_bundle
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "tools"
            self.make_tools(root)
            output = base / "bundle.zip"
            try:
                output.symlink_to(base / "missing-target.zip")
            except OSError as exc:
                self.skipTest(f"Symlinks unavailable: {exc}")
            with self.assertRaisesRegex(ValueError, "reparse|symlink"):
                build_bundle(root, output)
            self.assertTrue(output.is_symlink())

    def test_rejects_symlinked_input_and_symlinked_source_parent(self):
        from scripts.make_tool_bundle import build_bundle
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            real = base / "real"
            self.make_tools(real)
            outside = base / "outside.exe"
            outside.write_bytes(synthetic_pe())
            linked_file = real / "vrad.exe"
            linked_file.unlink()
            try:
                linked_file.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"Symlinks unavailable: {exc}")
            with self.assertRaisesRegex(ValueError, "reparse|symlink"):
                build_bundle(real, base / "file-link.zip")
            parent_link = base / "linked-tools"
            try:
                parent_link.symlink_to(real, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"Directory symlinks unavailable: {exc}")
            with self.assertRaisesRegex(ValueError, "reparse|symlink"):
                build_bundle(parent_link, base / "parent-link.zip")

    def test_cli_accepts_tools_dir_and_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "tools"
            self.make_tools(root)
            output = Path(temp) / "bundle.zip"
            script = Path(__file__).resolve().parents[1] / "scripts" / "make_tool_bundle.py"
            completed = subprocess.run(
                [sys.executable, str(script), "--tools-dir", str(root), "--output", str(output)],
                capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
