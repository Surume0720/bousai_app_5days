import unittest
import os
import tempfile

import app as app_module


class ShelterRegisterRouteTests(unittest.TestCase):
    def setUp(self):
        self.original_shelters = app_module.shelters
        self.original_data_file = app_module.DATA_FILE
        self.temp_dir = tempfile.TemporaryDirectory()
        app_module.shelters = []
        app_module.DATA_FILE = os.path.join(self.temp_dir.name, 'shelters.json')
        self.client = app_module.app.test_client()
        with self.client.session_transaction() as sess:
            sess['logged_in'] = True
            sess['username'] = 'admin'

    def tearDown(self):
        app_module.shelters = self.original_shelters
        app_module.DATA_FILE = self.original_data_file
        self.temp_dir.cleanup()

    def test_empty_name_shows_validation_error(self):
        response = self.client.post('/shelter_register', data={'name': '   '})
        self.assertEqual(response.status_code, 200)
        self.assertIn('避難所名を入力してください', response.get_data(as_text=True))

    def test_valid_name_shows_success_message(self):
        address = '青森市新町1丁目'
        token = app_module.GEOCODE_TOKEN_SERIALIZER.dumps({
            'address': address,
            'latitude': 40.8245,
            'longitude': 140.7400
        })
        response = self.client.post('/shelter_register', data={
            'action': 'create',
            'name': '山田小学校',
            'address': address,
            'status': '開設前',
            'capacity': '',
            'location_token': token
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('避難所が登録されました。', response.get_data(as_text=True))
        self.assertEqual(app_module.shelters[0]['latitude'], 40.8245)


if __name__ == '__main__':
    unittest.main()
