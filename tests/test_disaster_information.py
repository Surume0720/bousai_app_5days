import unittest
from unittest.mock import patch

import app as app_module


class DisasterInformationApiTests(unittest.TestCase):
    def setUp(self):
        self.original_instructions = app_module.instructions
        app_module.instructions = [{
            "id": 1,
            "target": "住民",
            "information_type": "避難情報",
            "district": "青森地区",
            "content": "川から離れて避難してください",
            "shelter": "青森小学校",
            "urgency": "高",
            "status": "発令中",
            "created_at": "2026年10月04日 08:00"
        }, {
            "id": 2,
            "target": "住民",
            "information_type": "お知らせ",
            "content": "訓練のお知らせ",
            "shelter": "",
            "urgency": "低",
            "status": "完了",
            "created_at": "2026年10月04日 07:00"
        }, {
            "id": 3,
            "target": "道路管理課",
            "content": "内部連絡",
            "urgency": "高",
            "status": "発令中",
            "created_at": "2026年10月04日 09:30"
        }]
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.instructions = self.original_instructions

    def test_api_merges_sorted_weather_and_resident_information(self):
        weather = {
            "area_name": "青森市",
            "warnings": [{"name": "大雨警報", "code": "03", "status": "継続"}],
            "report_time": "2026年10月04日 09:00",
            "report_datetime": "2026-10-04T09:00:00+09:00",
            "last_fetch_time": "2026年10月04日 09:05"
        }
        with patch.object(app_module, "get_weather_warnings", return_value=weather):
            response = self.client.get("/api/disaster_information")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["weather_status"]["state"], "ok")
        self.assertTrue(data["has_emergency"])
        self.assertEqual(data["items"][0]["source"], "気象庁")
        self.assertEqual(data["items"][1]["type"], "避難情報")
        self.assertEqual(data["items"][1]["shelter"], "青森小学校")
        self.assertEqual(data["items"][1]["shelter_url"], "/all_shelters")
        self.assertIn("訓練のお知らせ", [item["title"] for item in data["items"]])
        self.assertNotIn("内部連絡", [item["title"] for item in data["items"]])

    def test_weather_failure_is_explicit_and_keeps_resident_items(self):
        weather = {
            "area_name": "青森市",
            "warnings": [],
            "report_time": "取得失敗",
            "last_fetch_time": "2026年10月04日 09:05",
            "error": True
        }
        with patch.object(app_module, "get_weather_warnings", return_value=weather):
            response = self.client.get("/api/disaster_information")

        data = response.get_json()
        self.assertEqual(data["weather_status"]["state"], "error")
        self.assertIn("取得できません", data["weather_status"]["message"])
        self.assertIn("川から離れて避難してください", [item["title"] for item in data["items"]])


if __name__ == "__main__":
    unittest.main()
