import unittest

from app import AREA_CODE, parse_area_warnings


class ParseAreaWarningsTests(unittest.TestCase):
    def test_extracts_warnings_for_aomori_city(self):
        warning_data = [{
            "reportDatetime": "2026-10-04T00:00:00Z",
            "warning": {
                "class20Items": [{
                    "areaCode": AREA_CODE,
                    "kinds": [{"code": "15", "status": "継続"}]
                }]
            }
        }]

        warnings, report_datetime = parse_area_warnings(warning_data)

        self.assertEqual(AREA_CODE, "0220100")
        self.assertEqual(warnings, [{
            "name": "強風注意報",
            "code": "15",
            "status": "継続"
        }])
        self.assertEqual(report_datetime, "2026-10-04T00:00:00Z")

    def test_missing_aomori_city_record_raises_error(self):
        warning_data = [{
            "warning": {
                "class20Items": [{
                    "areaCode": "1420500",
                    "kinds": [{"status": "発表警報・注意報はなし"}]
                }]
            }
        }]

        with self.assertRaisesRegex(ValueError, "青森市の警報・注意報データが見つかりません"):
            parse_area_warnings(warning_data)


if __name__ == "__main__":
    unittest.main()