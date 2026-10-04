import json
import os
import tempfile
import unittest
from unittest.mock import patch

import app as app_module


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class ShelterManagementTests(unittest.TestCase):
    def setUp(self):
        self.original_shelters = app_module.shelters
        self.original_data_file = app_module.DATA_FILE
        self.original_geocode_cache = getattr(app_module, "GEOCODE_CACHE", None)
        self.temp_dir = tempfile.TemporaryDirectory()
        app_module.DATA_FILE = os.path.join(self.temp_dir.name, "shelters.json")
        app_module.shelters = [{
            "id": 1,
            "name": "既存避難所",
            "district": "青森地区",
            "address": "青森市安方1丁目",
            "latitude": 40.8222,
            "longitude": 140.7474,
            "status": "開設中",
            "crowd_status": "やや混雑",
            "capacity": 100,
            "hazards": ["地震"],
            "facilities": ["ペット可", "バリアフリー"],
            "note": "入口は北側です。"
        }]
        if self.original_geocode_cache is not None:
            app_module.GEOCODE_CACHE.clear()

        self.client = app_module.app.test_client()
        with self.client.session_transaction() as session:
            session["logged_in"] = True
            session["username"] = "admin"

    def tearDown(self):
        app_module.shelters = self.original_shelters
        app_module.DATA_FILE = self.original_data_file
        if self.original_geocode_cache is not None:
            app_module.GEOCODE_CACHE.clear()
            app_module.GEOCODE_CACHE.update(self.original_geocode_cache)
        self.temp_dir.cleanup()

    def location_token(self, address="青森市新町1丁目"):
        return app_module.GEOCODE_TOKEN_SERIALIZER.dumps({
            "address": address,
            "latitude": 40.8245,
            "longitude": 140.7400
        })

    def registration_data(self, **updates):
        data = {
            "action": "create",
            "name": "新しい避難所",
            "address": "青森市新町1丁目",
            "status": "開設前",
            "crowd_status": "やや混雑",
            "capacity": "250",
            "hazards": ["地震", "洪水"],
            "facilities": ["ペット可", "備蓄あり"],
            "note": "夜間は正面入口を使用",
            "location_token": self.location_token()
        }
        data.update(updates)
        return data

    def test_create_shelter_saves_selected_location_and_details(self):
        response = self.client.post(
            "/shelter_register",
            data=self.registration_data(),
            follow_redirects=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("避難所が登録されました", response.get_data(as_text=True))
        saved = app_module.shelters[0]
        self.assertEqual(saved["name"], "新しい避難所")
        self.assertEqual(saved["status"], "開設前")
        self.assertEqual(saved["open_status"], "開設前")
        self.assertEqual(saved["crowd_status"], "やや混雑")
        self.assertEqual(saved["capacity"], 250)
        self.assertEqual(saved["hazards"], ["地震", "洪水"])
        self.assertEqual(saved["facilities"], ["ペット可", "備蓄あり"])
        self.assertEqual(saved["note"], "夜間は正面入口を使用")
        self.assertEqual(saved["latitude"], 40.8245)
        self.assertEqual(saved["longitude"], 140.74)
        with open(app_module.DATA_FILE, encoding="utf-8") as file:
            persisted = json.load(file)
        self.assertEqual(persisted[0]["name"], "新しい避難所")

    def test_create_requires_a_selected_location(self):
        data = self.registration_data()
        data.pop("location_token")
        response = self.client.post("/shelter_register", data=data)

        self.assertEqual(response.status_code, 200)
        self.assertIn("住所検索から場所を選択してください", response.get_data(as_text=True))
        self.assertEqual(len(app_module.shelters), 1)

    def test_create_rejects_invalid_capacity_and_coordinates(self):
        response = self.client.post(
            "/shelter_register",
            data=self.registration_data(capacity="0")
        )
        self.assertIn("収容人数は1以上の整数", response.get_data(as_text=True))
        self.assertIn(self.location_token(), response.get_data(as_text=True))
        self.assertEqual(len(app_module.shelters), 1)

        invalid_token = app_module.GEOCODE_TOKEN_SERIALIZER.dumps({
            "address": "青森市新町1丁目",
            "latitude": 95,
            "longitude": 140.74
        })
        response = self.client.post(
            "/shelter_register",
            data=self.registration_data(location_token=invalid_token)
        )
        self.assertIn("住所検索から場所を選択してください", response.get_data(as_text=True))
        self.assertEqual(len(app_module.shelters), 1)

    def test_edit_preserves_coordinates_when_address_is_unchanged(self):
        response = self.client.post("/shelter_register", data={
            "action": "update",
            "shelter_id": "1",
            "name": "既存避難所（更新）",
            "address": "青森市安方1丁目",
            "status": "閉鎖",
            "capacity": "120",
            "hazards": ["地震"],
            "facilities": ["バリアフリー"],
            "location_token": ""
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        updated = app_module.shelters[0]
        self.assertEqual(updated["latitude"], 40.8222)
        self.assertEqual(updated["longitude"], 140.7474)
        self.assertEqual(updated["status"], "閉鎖")
        self.assertEqual(updated["capacity"], 120)

    def test_edit_rejects_changed_address_without_new_location(self):
        response = self.client.post("/shelter_register", data={
            "action": "update",
            "shelter_id": "1",
            "name": "既存避難所",
            "address": "別の住所",
            "status": "開設中",
            "capacity": "100",
            "location_token": ""
        })

        self.assertEqual(response.status_code, 200)
        self.assertIn("住所検索から場所を選択してください", response.get_data(as_text=True))
        self.assertIn('name="action" value="update"', response.get_data(as_text=True))
        self.assertIn('name="shelter_id" value="1"', response.get_data(as_text=True))
        self.assertEqual(app_module.shelters[0]["address"], "青森市安方1丁目")

    def test_update_shelter_status(self):
        response = self.client.post("/shelter_register", data={
            "action": "update_status",
            "shelter_id": "1",
            "status": "閉鎖"
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(app_module.shelters[0]["status"], "閉鎖")

    def test_shelter_json_api_returns_coordinates_and_status(self):
        response = self.client.get("/shelters")

        self.assertEqual(response.status_code, 200)
        shelter = response.get_json()[0]
        self.assertEqual(shelter["latitude"], 40.8222)
        self.assertEqual(shelter["longitude"], 140.7474)
        self.assertEqual(shelter["status"], "開設中")

    def test_shelter_search_matches_name_and_address(self):
        response = self.client.get("/search_results?q=青森市安方")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("既存避難所", html)
        self.assertIn("青森市安方1丁目", html)
        self.assertIn("位置情報あり", html)

    def test_search_results_include_map_and_base_point_controls(self):
        response = self.client.get("/search_results?q=既存避難所")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="shelterMap"', html)
        self.assertIn('id="baseLatitude"', html)
        self.assertIn('id="baseLongitude"', html)
        self.assertIn("40.8178", html)
        self.assertIn("140.7575", html)
        self.assertIn("/shelters/1", html)

    def test_shelter_detail_shows_operational_information(self):
        response = self.client.get("/shelters/1")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("既存避難所", html)
        self.assertIn("青森市安方1丁目", html)
        self.assertIn("やや混雑", html)
        self.assertIn("開設中", html)
        self.assertIn("バリアフリー", html)
        self.assertIn("入口は北側です。", html)

    def test_shelter_registration_still_requires_admin_login(self):
        response = app_module.app.test_client().get("/shelter_register")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    @patch("app.urllib.request.urlopen")
    def test_geocode_search_returns_selectable_signed_candidates(self, urlopen):
        urlopen.return_value = FakeResponse([{
            "lat": "40.8245",
            "lon": "140.7400",
            "display_name": "青森県青森市新町一丁目"
        }])
        response = self.client.get("/api/geocode?q=青森市新町")

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["results"][0]
        self.assertEqual(result["address"], "青森県青森市新町一丁目")
        self.assertTrue(result["token"])

    def test_geocode_search_requires_admin_login(self):
        client = app_module.app.test_client()
        response = client.get("/api/geocode?q=青森市")

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
