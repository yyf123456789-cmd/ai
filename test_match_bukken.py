import csv
import tempfile
import unittest
from pathlib import Path

import match_bukken as mb


OLD_HEADER = [
    "T03_TENPO_CD",
    "T03_TENPO_NAME",
    "T03_ORIGINAL_HEYA_CD01",
    "T03_TATEMONO_NAME",
    "T03_HEYA_BANGOU",
    "T03_JYUUSHO_TEXT",
    "T03_BANCHI",
    "T03_CHOUMOKU",
]
NEW_ADDRESS = "M.ADDR_NM||NVL(T.TATEYA_BANCHI,T.TATEYA_CHIBAN)"
NEW_HEADER = [
    "WEB_RENKEI_TEN_CD",
    "BUKKEN_NO",
    "TEN_NM",
    "TATEYA_NM",
    "HEYA_NM",
    NEW_ADDRESS,
]


def old_row(**overrides):
    base = {
        "T03_TENPO_CD": "40007818",
        "T03_TENPO_NAME": "薬院店",
        "T03_ORIGINAL_HEYA_CD01": "112404725",
        "T03_TATEMONO_NAME": "トリシア寺塚",
        "T03_HEYA_BANGOU": "303",
        "T03_JYUUSHO_TEXT": "福岡県福岡市南区寺塚２丁目",
        "T03_BANCHI": "30番6､30番7(地番)",
        "T03_CHOUMOKU": "２丁目",
    }
    base.update(overrides)
    return base


def new_row(**overrides):
    base = {
        "WEB_RENKEI_TEN_CD": "13047607",
        "BUKKEN_NO": "1000802886",
        "TEN_NM": "薬院店",
        "TATEYA_NM": "トリシア寺塚",
        "HEYA_NM": "３０３",
        NEW_ADDRESS: "福岡県福岡市南区寺塚二丁目30-6、30-7",
    }
    base.update(overrides)
    return base


class NormalizeTest(unittest.TestCase):
    def test_address_kanji_dash_and_banchi(self):
        left = mb.normalize_address("福岡県福岡市東区松島１丁目46番地9")
        right = mb.normalize_address("福岡県福岡市東区松島一丁目４６ー９")
        self.assertEqual(left, right)
        self.assertEqual(left, "福岡県福岡市東区松島1-46-9")

    def test_address_ignores_chiban_note_and_halfwidth_comma(self):
        left = mb.normalize_address("福岡県福岡市南区寺塚２丁目30番6､30番7(地番)")
        right = mb.normalize_address("福岡県福岡市南区寺塚二丁目30-6、30-7")
        self.assertEqual(left, right)

    def test_address_keeps_place_names_with_kanji_numbers(self):
        self.assertEqual(mb.normalize_address("福岡市千代"), "福岡市千代")
        self.assertEqual(mb.normalize_building("八幡マンション"), "八幡マンション")
        self.assertEqual(mb.normalize_address("東京都港区三田1丁目"), "東京都港区三田1")

    def test_building_width_space_roman_and_hyphen(self):
        self.assertEqual(mb.normalize_building("ＤーＲＯＯＭプラーナ"), "droomプラーナ")
        self.assertEqual(
            mb.normalize_building("ＤーＲＯＯＭプラーナ"),
            mb.normalize_building("D-ROOMプラーナ"),
        )
        self.assertEqual(
            mb.normalize_building("セレスティア江戸川ＩＩ"),
            mb.normalize_building("セレスティア江戸川2"),
        )
        self.assertEqual(
            mb.normalize_building("ｒｏｂｏｔ　ｈｏｍｅ　瑞江"),
            mb.normalize_building("robot home 瑞江"),
        )
        self.assertEqual(
            mb.normalize_building("コンフォリア・リヴ森下"),
            mb.normalize_building("コンフォリアリヴ森下"),
        )

    def test_room_width_kanji_suffix_and_leading_zero(self):
        self.assertEqual(mb.normalize_room("７０５号室"), "705")
        self.assertEqual(mb.normalize_room("0602"), mb.normalize_room("６０２"))
        self.assertEqual(mb.normalize_room("三百二"), "302")
        self.assertEqual(mb.normalize_room("Ａ１３１１"), "a1311")
        self.assertEqual(mb.normalize_room("1005"), "1005")

    def test_store_ignores_width_and_spaces_only(self):
        self.assertEqual(mb.normalize_store("薬院店"), mb.normalize_store("　薬院店 "))
        self.assertNotEqual(mb.normalize_store("薬院店"), mb.normalize_store("森下店"))


class MatchTest(unittest.TestCase):
    def match(self, old_rows, new_rows):
        old_records = []
        for row in old_rows:
            old_records.append(
                {
                    "store_cd": row["T03_TENPO_CD"],
                    "store_name": row["T03_TENPO_NAME"],
                    "old_no": row["T03_ORIGINAL_HEYA_CD01"],
                    "key": mb.make_key(
                        row["T03_TENPO_NAME"],
                        row["T03_TATEMONO_NAME"],
                        row["T03_HEYA_BANGOU"],
                        mb.old_address(row),
                    ),
                }
            )
        new_records = []
        for row in new_rows:
            new_records.append(
                {
                    "store_cd": row["WEB_RENKEI_TEN_CD"],
                    "store_name": row["TEN_NM"],
                    "new_no": row["BUKKEN_NO"],
                    "key": mb.make_key(
                        row["TEN_NM"],
                        row["TATEYA_NM"],
                        row["HEYA_NM"],
                        row[NEW_ADDRESS],
                    ),
                }
            )
        return mb.match_records(old_records, new_records)

    def test_sample_shape_matches_across_formatting(self):
        matched, stats = self.match([old_row()], [new_row()])
        self.assertEqual(
            matched,
            [
                {
                    "店铺CD": "40007818",
                    "店铺名": "薬院店",
                    "新物件番号": "1000802886",
                    "旧物件番号": "112404725",
                }
            ],
        )
        self.assertEqual(stats["new_unmatched"], 0)
        self.assertEqual(stats["old_unmatched"], 0)

    def test_choumoku_already_in_address_is_not_repeated(self):
        address = mb.old_address(old_row())
        self.assertNotIn("2-2", mb.normalize_address(address))

    def test_store_name_must_match(self):
        matched, stats = self.match(
            [old_row()],
            [new_row(TEN_NM="森下店")],
        )
        self.assertEqual(matched, [])
        self.assertEqual(stats["new_unmatched"], 1)

    def test_different_room_does_not_match(self):
        matched, _ = self.match([old_row()], [new_row(HEYA_NM="304")])
        self.assertEqual(matched, [])

    def test_room_suffix_building_hyphen_and_banchi(self):
        old = old_row(
            T03_TATEMONO_NAME="ＤーＲＯＯＭプラーナ",
            T03_HEYA_BANGOU="705号室",
            T03_JYUUSHO_TEXT="福岡県福岡市東区松島１丁目",
            T03_BANCHI="46番地9",
            T03_CHOUMOKU="１丁目",
            T03_ORIGINAL_HEYA_CD01="112417280",
        )
        new = new_row(
            TATEYA_NM="D-ROOMプラーナ",
            HEYA_NM="７０５",
            BUKKEN_NO="2001",
            **{NEW_ADDRESS: "福岡県福岡市東区松島一丁目４６ー９"},
        )
        matched, _ = self.match([old], [new])
        self.assertEqual(matched[0]["新物件番号"], "2001")
        self.assertEqual(matched[0]["旧物件番号"], "112417280")

    def test_duplicate_pair_is_emitted_once_and_ambiguous_pairs_are_kept(self):
        old_rows = [
            old_row(T03_ORIGINAL_HEYA_CD01="111"),
            old_row(T03_ORIGINAL_HEYA_CD01="111"),
            old_row(T03_ORIGINAL_HEYA_CD01="222"),
        ]
        matched, stats = self.match(old_rows, [new_row(), new_row()])
        self.assertEqual(
            sorted(row["旧物件番号"] for row in matched),
            ["111", "222"],
        )
        self.assertEqual(stats["ambiguous"], 2)
        self.assertEqual(stats["matched_rows"], 2)

    def test_output_uses_old_store_code(self):
        matched, _ = self.match([old_row()], [new_row()])
        self.assertEqual(matched[0]["店铺CD"], "40007818")
        self.assertNotEqual(matched[0]["店铺CD"], "13047607")


class FileTest(unittest.TestCase):
    def write_csv(self, directory: Path, name: str, header, rows, encoding: str) -> Path:
        path = directory / name
        with path.open("w", encoding=encoding, newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_cli_writes_four_columns_and_reads_cp932(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_path = self.write_csv(root, "old.csv", OLD_HEADER, [old_row()], "cp932")
            new_path = self.write_csv(
                root,
                "new.csv",
                NEW_HEADER,
                [new_row(), new_row(HEYA_NM="999", BUKKEN_NO="999")],
                "utf-8-sig",
            )
            output = root / "out.csv"
            code = mb.main([str(old_path), str(new_path), "-o", str(output)])
            self.assertEqual(code, 0)
            text = output.read_bytes()
            self.assertTrue(text.startswith(b"\xef\xbb\xbf"))
            with output.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows[0], ["店铺CD", "店铺名", "新物件番号", "旧物件番号"])
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1][2], "1000802886")
            self.assertEqual(rows[1][3], "112404725")

    def test_missing_column_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_path = root / "old.csv"
            old_path.write_text("T03_TENPO_NAME\n薬院店\n", encoding="utf-8")
            new_path = self.write_csv(root, "new.csv", NEW_HEADER, [new_row()], "utf-8")
            with self.assertRaises(SystemExit):
                mb.load_old(old_path)


if __name__ == "__main__":
    unittest.main()
