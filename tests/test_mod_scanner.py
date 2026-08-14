from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ck3_languages import discover_locale_files, infer_locale_id, target_relative_path
from ck3_mod_scanner import (
    AUTO_LANGUAGE_ID,
    scan_descriptor,
    scan_mod_folder,
    scan_mod_library,
)


def localization(entries: list[tuple[str, str]], locale: str = "english") -> str:
    lines = [f"l_{locale}:"]
    lines.extend(f' {key}:0 "{value}"' for key, value in entries)
    return "\n".join(lines) + "\n"


def create_mod(
    parent: Path,
    folder_name: str,
    *,
    files: dict[str, str] | None = None,
    content_directory: str = "common",
) -> Path:
    root = parent / folder_name
    root.mkdir(parents=True)
    (root / "descriptor.mod").write_text(
        f'name="{folder_name}"\nversion="1.2.3"\nsupported_version="1.19.*"\n',
        encoding="utf-8",
    )
    content = root / content_directory
    content.mkdir(parents=True, exist_ok=True)
    if not files:
        (content / "fixture.txt").write_text("fixture = yes\n", encoding="utf-8")
    for relative, text in (files or {}).items():
        output = root / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8-sig")
    return root


class LanguageLayoutTests(unittest.TestCase):
    def test_discovers_direct_and_replace_layouts_and_maps_target_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            mod = Path(temporary) / "LayoutMod"
            direct = mod / "localization" / "english" / "direct_l_english.yml"
            replace = mod / "localization" / "replace" / "clausewitz" / "settings_l_english.yml"
            prefixed = mod / "localization" / "l_english" / "prefixed_l_english.yml"
            for path in (direct, replace, prefixed):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(localization([("layout_key", "The realm and the ruler")]), encoding="utf-8-sig")

            discovered = discover_locale_files(mod, "english")

            self.assertEqual(set(discovered), {direct, replace, prefixed})
            self.assertEqual(
                target_relative_path(direct, mod, "english", "japanese").as_posix(),
                "japanese/direct_l_japanese.yml",
            )
            self.assertEqual(
                target_relative_path(replace, mod, "english", "japanese").as_posix(),
                "replace/clausewitz/settings_l_japanese.yml",
            )
            self.assertEqual(
                target_relative_path(prefixed, mod, "english", "japanese").as_posix(),
                "l_japanese/prefixed_l_japanese.yml",
            )

    def test_header_takes_priority_over_filename_and_folder_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            localization_root = Path(temporary) / "localization"
            path = localization_root / "japanese" / "mismatch_l_japanese.yml"
            path.parent.mkdir(parents=True)
            path.write_text(localization([("actual", "English metadata")], "english"), encoding="utf-8-sig")

            self.assertEqual(infer_locale_id(path, localization_root), "english")


class ModScannerTests(unittest.TestCase):
    def test_detects_actual_english_russian_and_japanese_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            english = create_mod(
                parent,
                "EnglishMod",
                files={
                    "localization/english/text_l_english.yml": localization(
                        [
                            ("english_one", "The character and the ruler are in your realm."),
                            ("english_two", "This event will start a war for your kingdom."),
                        ]
                    )
                },
            )
            russian = create_mod(
                parent,
                "RussianUnderEnglishHeader",
                files={
                    "localization/english/text_l_english.yml": localization(
                        [
                            ("russian_one", "Ваш персонаж становится правителем великого королевства."),
                            ("russian_two", "Это событие начинает войну и изменяет отношения."),
                        ]
                    )
                },
            )
            japanese = create_mod(
                parent,
                "JapaneseUnderEnglishHeader",
                files={
                    "localization/replace/clausewitz/text_l_english.yml": localization(
                        [
                            ("japanese_one", "あなたのキャラクターは王国の新しい統治者になります。"),
                            ("japanese_two", "このイベントによって戦争が始まり、関係が変化します。"),
                        ]
                    )
                },
            )

            english_info = scan_mod_folder(english).localizations[0]
            russian_info = scan_mod_folder(russian).localizations[0]
            japanese_candidate = scan_mod_folder(japanese)
            japanese_info = japanese_candidate.localizations[0]

            self.assertEqual(english_info.detected_language_id, "english")
            self.assertEqual(russian_info.locale_id, "english")
            self.assertEqual(russian_info.detected_language_id, "russian")
            self.assertEqual(japanese_info.locale_id, "english")
            self.assertEqual(japanese_info.detected_language_id, "japanese")
            self.assertGreater(japanese_info.translatable_entries, 0)

            same_target_source = japanese_candidate.choose_source(AUTO_LANGUAGE_ID, "japanese")
            self.assertIsNotNone(same_target_source)
            self.assertEqual(same_target_source.detected_language_id, "japanese")
            different_target_source = japanese_candidate.choose_source(AUTO_LANGUAGE_ID, "english")
            self.assertIsNotNone(different_target_source)
            self.assertEqual(different_target_source.detected_language_id, "japanese")

    def test_auto_source_avoids_target_language_and_prefers_english(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            mod = create_mod(
                Path(temporary),
                "MultilingualMod",
                files={
                    "localization/english/text_l_english.yml": localization(
                        [("english", "The ruler and your character are in this realm.")]
                    ),
                    "localization/japanese/text_l_japanese.yml": localization(
                        [("japanese", "あなたのキャラクターはこの王国の統治者です。")],
                        "japanese",
                    ),
                },
            )
            candidate = scan_mod_folder(mod)

            source = candidate.choose_source(AUTO_LANGUAGE_ID, "japanese")
            explicit_japanese = candidate.choose_source("japanese", "japanese")

            self.assertIsNotNone(source)
            self.assertEqual(source.detected_language_id, "english")
            self.assertIsNotNone(explicit_japanese)
            self.assertEqual(explicit_japanese.detected_language_id, "japanese")

    def test_explicit_source_overrides_one_low_confidence_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            mod = create_mod(
                Path(temporary),
                "ShortFallback",
                files={
                    "localization/french/short_l_french.yml": localization(
                        [("short", "Realm")],
                        "french",
                    )
                },
            )
            candidate = scan_mod_folder(mod)

            detected = candidate.localizations[0]
            explicit = candidate.choose_source("english", "japanese")

            self.assertLess(detected.confidence, 0.5)
            self.assertEqual(detected.locale_id, "french")
            self.assertIs(explicit, detected)

    def test_non_linguistic_interface_mod_is_valid_and_missing_descriptor_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            graphics_only = create_mod(parent, "GraphicsOnly", content_directory="interface")
            broken = parent / "BrokenMod"
            (broken / "common").mkdir(parents=True)
            (broken / "common" / "rules.txt").write_text("rule = yes\n", encoding="utf-8")

            valid_candidate = scan_mod_folder(graphics_only)
            invalid_candidate = scan_mod_folder(broken)

            self.assertTrue(valid_candidate.valid)
            self.assertTrue(valid_candidate.is_non_linguistic)
            self.assertIsNone(valid_candidate.choose_source(AUTO_LANGUAGE_ID, "japanese"))
            self.assertFalse(invalid_candidate.valid)
            self.assertIn("Missing descriptor.mod", invalid_candidate.reason)

    def test_descriptor_and_readme_alone_are_not_valid_mod_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "MetadataOnly"
            root.mkdir()
            (root / "descriptor.mod").write_text('name="Metadata Only"\n', encoding="utf-8")
            (root / "README.txt").write_text("This folder has no CK3 content.\n", encoding="utf-8")

            candidate = scan_mod_folder(root)

            self.assertFalse(candidate.valid)
            self.assertIn("no recognizable CK3 mod content", candidate.reason)

    def test_missing_closing_quote_is_detected_as_linguistic_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            mod = create_mod(
                Path(temporary),
                "BrokenQuote",
                files={
                    "localization/english/broken_l_english.yml": (
                        'l_english:\n broken_key:0 "The character and the ruler are in your realm.\n'
                    )
                },
            )

            candidate = scan_mod_folder(mod)

            self.assertFalse(candidate.is_non_linguistic)
            self.assertEqual(candidate.localizations[0].detected_language_id, "english")
            self.assertEqual(candidate.localizations[0].malformed_entries, 1)
            self.assertTrue(any("missing closing quote" in warning for warning in candidate.warnings))

    def test_external_descriptor_resolution_library_dedup_and_invalid_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            library = Path(temporary) / "mod"
            library.mkdir()
            root = create_mod(
                library,
                "Shared Mod",
                files={
                    "localization/english/shared_l_english.yml": localization(
                        [("shared", "The character and the ruler share this realm.")]
                    )
                },
            )
            descriptor_text = (
                'name="Shared Launcher"\nversion="2.0"\nsupported_version="1.19.*"\n'
                f'path="{root.as_posix()}"\n'
            )
            first_descriptor = library / "Shared Mod.mod"
            first_descriptor.write_text(descriptor_text, encoding="utf-8")
            (library / "Duplicate.mod").write_text(descriptor_text, encoding="utf-8")
            (library / "descriptor.mod").write_text('name="Stray library descriptor"\n', encoding="utf-8")
            broken = library / "Broken Child"
            (broken / "events").mkdir(parents=True)
            (broken / "events" / "broken.txt").write_text("namespace = broken\n", encoding="utf-8")

            resolved = scan_descriptor(first_descriptor)
            candidates = scan_mod_library(library)
            shared = [candidate for candidate in candidates if candidate.root == root.resolve()]
            invalid = [candidate for candidate in candidates if not candidate.valid]

            self.assertTrue(resolved.valid)
            self.assertEqual(resolved.root, root.resolve())
            self.assertEqual(len(shared), 1, "multiple launcher descriptors and the child folder must be deduplicated")
            self.assertEqual(len(invalid), 1)
            self.assertEqual(invalid[0].name, "Broken Child")
            self.assertIn("Missing descriptor.mod", invalid[0].reason)

    def test_relative_mod_descriptor_and_single_mod_library_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ck3 = Path(temporary) / "Crusader Kings III"
            library = ck3 / "mod"
            library.mkdir(parents=True)
            mod = create_mod(library, "RelativeMod", content_directory="events")
            launcher = library / "RelativeMod.mod"
            launcher.write_text('name="Relative Launcher"\npath="mod/RelativeMod"\n', encoding="utf-8")

            resolved = scan_descriptor(launcher)
            scanned_as_library = scan_mod_library(mod)

            self.assertTrue(resolved.valid)
            self.assertEqual(resolved.root, mod.resolve())
            self.assertEqual(len(scanned_as_library), 1)
            self.assertTrue(scanned_as_library[0].valid)
            self.assertEqual(scanned_as_library[0].root, mod.resolve())


if __name__ == "__main__":
    unittest.main()
