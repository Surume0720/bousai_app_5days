import json
import os
import tempfile
import unittest

import app as app_module


class InstructionBoardTests(unittest.TestCase):
    def setUp(self):
        self.original_instructions = app_module.instructions
        self.original_instructions_file = app_module.INSTRUCTIONS_FILE
        app_module.instructions = [{
            "id": 1,
            "target": "住民",
            "district": "A地区",
            "content": "川の近くから避難してください",
            "shelter": "青森小学校",
            "urgency": "高",
            "status": "発令中",
            "created_at": "2026年10月04日 09:00",
            "updated_at": "2026年10月04日 09:00"
        }, {
            "id": 2,
            "target": "住民",
            "district": "B地区",
            "content": "解除済みのお知らせ",
            "shelter": "",
            "urgency": "中",
            "status": "解除",
            "created_at": "2026年10月04日 08:00",
            "updated_at": "2026年10月04日 08:30"
        }]
        self.temp_dir = tempfile.TemporaryDirectory()
        app_module.INSTRUCTIONS_FILE = os.path.join(self.temp_dir.name, "instructions.json")
        self.client = app_module.app.test_client()
        with self.client.session_transaction() as session:
            session["logged_in"] = True
            session["username"] = "admin"

    def tearDown(self):
        app_module.instructions = self.original_instructions
        app_module.INSTRUCTIONS_FILE = self.original_instructions_file
        self.temp_dir.cleanup()

    def test_create_instruction_with_urgency_and_target(self):
        response = self.client.post("/board", data={
            "action": "create",
            "target": "消防",
            "information_type": "避難情報",
            "district": "A地区",
            "content": "河川の状況を確認してください",
            "shelter": "",
            "urgency": "高"
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("指示を発信しました", response.get_data(as_text=True))
        with open(app_module.INSTRUCTIONS_FILE, encoding="utf-8") as file:
            saved = json.load(file)
        self.assertEqual(saved[0]["target"], "消防")
        self.assertEqual(saved[0]["information_type"], "避難情報")
        self.assertEqual(saved[0]["district"], "A地区")
        self.assertEqual(saved[0]["urgency"], "高")
        self.assertEqual(saved[0]["status"], "発令中")

    def test_emergency_urgency_cannot_be_created(self):
        response = self.client.post("/board", data={
            "action": "create",
            "target": "消防",
            "content": "河川の状況を確認してください",
            "urgency": "緊急"
        })

        self.assertEqual(response.status_code, 200)
        self.assertIn("緊急度を選択してください", response.get_data(as_text=True))

    def test_emergency_is_absent_from_create_and_filter_choices(self):
        response = self.client.get("/board")
        html = response.get_data(as_text=True)
        create_urgencies = html.split('<select id="urgency"', 1)[1].split("</select>", 1)[0]
        filter_urgencies = html.split('<select id="urgencyFilter"', 1)[1].split("</select>", 1)[0]

        self.assertNotIn('value="緊急"', create_urgencies)
        self.assertNotIn('value="緊急"', filter_urgencies)

    def test_update_instruction_status(self):
        response = self.client.post("/board", data={
            "action": "update_status",
            "instruction_id": "1",
            "status": "対応中"
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("対応状況を更新しました", response.get_data(as_text=True))
        self.assertEqual(app_module.instructions[0]["status"], "対応中")

    def test_home_renders_disaster_information_feed(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="disasterInfo"', html)
        self.assertIn('id="informationList"', html)
        self.assertIn("/api/disaster_information", html)
        self.assertIn('aria-current="page"', html)
        self.assertIn('href="#shelterMapSection"', html)
        self.assertIn('navigator.geolocation.getCurrentPosition', html)

    def test_board_lists_internal_and_resident_instructions(self):
        response = self.client.get("/board")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("川の近くから避難してください", html)
        self.assertIn("解除済みのお知らせ", html)
        self.assertIn("status-issued", html)
        self.assertIn("status-released", html)

    def test_board_has_accessible_text_size_controls(self):
        response = self.client.get("/board")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="normalText"', html)
        self.assertIn('id="largeText"', html)
        self.assertIn('aria-pressed="true">100％', html)
        self.assertIn('aria-pressed="false">150％', html)
        self.assertIn("board-text-scale", html)

    def test_instruction_form_has_confirmation_dialog(self):
        response = self.client.get("/board")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="instructionForm"', html)
        self.assertIn("指示発信</button>", html)
        self.assertIn('id="instructionConfirmDialog"', html)
        self.assertIn("この内容で発信", html)
        self.assertIn("戻って修正", html)


if __name__ == "__main__":
    unittest.main()
