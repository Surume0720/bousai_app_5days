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
        self.assertEqual(app_module.shelters[0]["open_status"], "閉鎖")

    def test_shelter_json_api_returns_coordinates_and_status(self):
        response = self.client.get("/shelters")

        self.assertEqual(response.status_code, 200)
        shelter = response.get_json()[0]
        self.assertEqual(shelter["latitude"], 40.8222)
        self.assertEqual(shelter["longitude"], 140.7474)
        self.assertEqual(shelter["status"], "開設中")

    def test_shelter_search_matches_facility_name(self):
        response = self.client.get("/search_results?q=既存避難所")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("既存避難所", html)
        self.assertIn("青森市安方1丁目", html)
        self.assertIn("位置情報あり", html)

    def test_search_form_restores_keyword_and_unknown_district(self):
        response = self.client.get("/shelter_search?q=青森市&district=未登録地区")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('value="青森市"', html)
        self.assertIn('value="未登録地区" selected', html)

    def test_search_form_has_only_requested_disaster_buttons_and_equipment(self):
        response = self.client.get("/shelter_search")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        for disaster_type in ("全件", "地震", "洪水", "津波", "土砂崩れ", "積雪", "台風", "火災"):
            self.assertIn(disaster_type, html)
        self.assertNotIn('value="高潮"', html)
        self.assertNotIn('value="土砂災害"', html)
        for equipment in ("ペット可", "バリアフリー", "車椅子対応", "高齢者対応", "家族対応", "外国人対応"):
            self.assertIn(equipment, html)
        for example in ("小学校", "体育館", "駅前"):
            self.assertIn(example, html)

    def test_search_filters_are_conjunctive_and_disaster_labels_are_exact(self):
        app_module.shelters = [{
            "id": 10,
            "name": "East Community Shelter",
            "district": "North",
            "disaster_types": ["土砂災害", "地震"],
            "equipment": ["ペット可", "バリアフリー"]
        }, {
            "id": 11,
            "name": "East Gym",
            "district": "North",
            "disaster_types": ["土砂崩れ", "地震"],
            "equipment": ["ペット可", "車椅子対応"]
        }, {
            "id": 12,
            "name": "West Gym",
            "district": "South",
            "disaster_types": ["土砂崩れ"],
            "equipment": ["ペット可", "車椅子対応"]
        }, {
            "id": 13,
            "name": "Annex Shelter",
            "district": "North",
            "address": "East Gym Road",
            "disaster_types": ["土砂崩れ"],
            "equipment": ["ペット可", "車椅子対応"]
        }]

        query = {
            "district": " North ",
            "disaster_type": "土砂崩れ",
            "q": " EAST ",
            "equipment": ["ペット可", "車椅子対応"]
        }
        response = self.client.get("/search_results", query_string=query)
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("East Gym", html)
        self.assertIn("災害 土砂崩れ", html)
        self.assertIn("設備 ペット可・車椅子対応", html)
        self.assertNotIn("East Community Shelter", html)
        self.assertNotIn("West Gym", html)
        self.assertNotIn("Annex Shelter", html)

        api_response = self.client.get("/shelters", query_string=query)
        self.assertEqual([item["id"] for item in api_response.get_json()], [11])

        detail_response = self.client.get("/shelters/11", query_string=query)
        detail_html = detail_response.get_data(as_text=True)
        self.assertEqual(detail_response.status_code, 200)
        self.assertIn("/search_results", detail_html)
        self.assertIn("disaster_type=", detail_html)
        self.assertIn("equipment=", detail_html)

        exact_disaster_response = self.client.get(
            "/shelters", query_string={"disaster_type": "土砂災害"}
        )
        self.assertEqual([item["id"] for item in exact_disaster_response.get_json()], [10])

        all_regions = self.client.get("/shelters", query_string={"district": "全地域"})
        self.assertEqual(len(all_regions.get_json()), 4)

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

    def test_unknown_shelter_statuses_are_shown_neutrally(self):
        app_module.shelters[0]["status"] = "未定義の開館値"
        app_module.shelters[0]["crowd_status"] = "未定義の混雑値"
        response = self.client.get("/shelters/1")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("開館状況：状況未登録", html)
        self.assertIn("混雑状況：未確認", html)

    def test_empty_search_still_renders_map_and_search_navigation(self):
        response = self.client.get("/search_results?q=存在しない施設")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="shelterMap"', html)
        self.assertIn("該当する避難所が見つかりませんでした", html)
        self.assertIn("検索条件を変更", html)

    def test_all_shelters_page_is_separate_and_shows_unregistered_fields(self):
        app_module.shelters = [{"id": 50, "name": "施設情報の少ない避難所"}]
        response = self.client.get("/all_shelters")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("1施設を掲載", html)
        self.assertIn("施設情報の少ない避難所", html)
        self.assertIn("未登録", html)
        self.assertNotIn('id="shelterMap"', html)

    def test_all_shelters_page_has_zero_record_state(self):
        app_module.shelters = []
        response = self.client.get("/all_shelters")

        self.assertEqual(response.status_code, 200)
        self.assertIn("登録されている避難所はありません", response.get_data(as_text=True))

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
