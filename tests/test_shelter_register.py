import unittest

from app import app


class ShelterRegisterRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        with self.client.session_transaction() as sess:
            sess['logged_in'] = True
            sess['username'] = 'admin'

    def test_empty_name_shows_validation_error(self):
        response = self.client.post('/shelter_register', data={'name': '   '})
        self.assertEqual(response.status_code, 200)
        self.assertIn('避難所名を入力してください', response.get_data(as_text=True))

    def test_valid_name_shows_success_message(self):
        response = self.client.post('/shelter_register', data={'name': '山田小学校'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('避難所が登録されました。', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
