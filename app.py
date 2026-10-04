from flask import Flask, abort, jsonify, request, render_template, session, redirect, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer
from urllib.parse import urlparse, urljoin
from functools import wraps
import json
import math
import os
import re
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')

# 管理者認証情報
ADMIN_CREDENTIALS = {
    'admin': '123'
}

# ────────────────────────────────
# 気象警報・注意報設定
PREFECTURE_CODE = "020000"  # 青森県
AREA_NAME = "青森市"
AREA_CODE = "0220100"

WARNING_URL = (
    f"https://www.jma.go.jp/bosai/warning/data/r8/{PREFECTURE_CODE}.json"
)

JST = timezone(timedelta(hours=9))

# 警報・注意報のコード一覧
WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}

# ────────────────────────────────
# サンプルデータの読み込み
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

shelters = load_json(DATA_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])


def ensure_shelter_ids(items):
    used_ids = {str(item['id']) for item in items if item.get('id') is not None}
    numeric_ids = [int(value) for value in used_ids if value.isdigit()]
    next_id = max(numeric_ids, default=0) + 1
    for item in items:
        if item.get('id') is None:
            while str(next_id) in used_ids:
                next_id += 1
            item['id'] = next_id
            used_ids.add(str(next_id))
            next_id += 1


ensure_shelter_ids(shelters)
INSTRUCTION_TARGETS = ["住民", "防災課", "災害対策本部", "消防", "道路管理課", "避難所"]
INSTRUCTION_URGENCIES = ["高", "中", "低"]
INSTRUCTION_STATUSES = ["発令中", "対応中", "完了", "解除"]
INSTRUCTION_TYPES = ["避難情報", "お知らせ"]
SHELTER_STATUSES = ["開設中", "開設前", "閉鎖", "状況未登録"]
SHELTER_CROWD_STATUSES = ["空きあり", "やや混雑", "混雑", "未確認"]
SHELTER_HAZARDS = ["地震", "津波", "洪水", "土砂災害", "高潮", "火災", "大雪"]
SHELTER_FACILITIES = ["ペット可", "バリアフリー", "非常用電源", "備蓄あり", "授乳室"]
GEOCODE_URL = "https://nominatim.openstreetmap.org/search"
GEOCODE_USER_AGENT = os.environ.get(
    'GEOCODER_USER_AGENT', 'BousaiApp/1.0 (shelter address search)'
)
GEOCODE_TOKEN_SERIALIZER = URLSafeTimedSerializer(
    app.secret_key, salt='shelter-geocode-candidate'
)
GEOCODE_CACHE = {}
GEOCODE_CACHE_TTL = 600
GEOCODE_MIN_INTERVAL = 1.1
_geocode_lock = threading.Lock()
_geocode_last_request = 0.0

def save_instructions():
    """指示ボードのデータをファイルに保存する"""
    try:
        with open(INSTRUCTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(instructions, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def save_shelters():
    """避難所データを一時ファイル経由で安全に保存する"""
    temporary_path = None
    try:
        directory = os.path.dirname(DATA_FILE)
        os.makedirs(directory, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=directory, delete=False
        ) as data_file:
            json.dump(shelters, data_file, ensure_ascii=False, indent=2)
            temporary_path = data_file.name
        os.replace(temporary_path, DATA_FILE)
        return True
    except OSError:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)
        return False


def valid_coordinates(latitude, longitude):
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(latitude) or not math.isfinite(longitude):
        return None
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None
    return latitude, longitude


def get_shelter_form_values(form):
    return {
        'name': form.get('name', '').strip(),
        'address': form.get('address', '').strip(),
        'status': form.get('status', '状況未登録').strip(),
        'crowd_status': form.get('crowd_status', '未確認').strip(),
        'capacity': form.get('capacity', '').strip(),
        'hazards': form.getlist('hazards'),
        'facilities': form.getlist('facilities'),
        'note': form.get('note', '').strip(),
        'location_token': form.get('location_token', '')
    }


def validate_shelter_form(form, existing=None):
    values = get_shelter_form_values(form)
    errors = []
    capacity = values['capacity']
    capacity_value = None

    if not values['name']:
        errors.append('避難所名を入力してください。')
    elif len(values['name']) > 120:
        errors.append('避難所名は120文字以内で入力してください。')

    if not values['address']:
        errors.append('住所を入力してください。')
    elif len(values['address']) > 250:
        errors.append('住所は250文字以内で入力してください。')

    if values['status'] not in SHELTER_STATUSES:
        errors.append('開設状況を選択してください。')
    if values['crowd_status'] not in SHELTER_CROWD_STATUSES:
        errors.append('混雑状況を選択してください。')

    if capacity:
        if not re.fullmatch(r'[0-9]+', capacity) or int(capacity) < 1:
            errors.append('収容人数は1以上の整数で入力してください。')
        else:
            capacity_value = int(capacity)

    if any(value not in SHELTER_HAZARDS for value in values['hazards']):
        errors.append('対応できる災害の選択内容が正しくありません。')
    if any(value not in SHELTER_FACILITIES for value in values['facilities']):
        errors.append('設備・受け入れ条件の選択内容が正しくありません。')
    if len(values['note']) > 1000:
        errors.append('備考は1000文字以内で入力してください。')

    location = None
    if existing and values['address'] == existing.get('address', '').strip():
        location = valid_coordinates(
            existing.get('latitude'), existing.get('longitude')
        )

    if location is None:
        try:
            candidate = GEOCODE_TOKEN_SERIALIZER.loads(
                form.get('location_token', ''), max_age=1800
            )
            if candidate.get('address') == values['address']:
                location = valid_coordinates(
                    candidate.get('latitude'), candidate.get('longitude')
                )
        except (BadSignature, TypeError, ValueError):
            pass

    if location is None:
        errors.append('住所検索から場所を選択してください。')

    return values, capacity_value, location, errors
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")


def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


def filter_shelters(district=None, query=None):
    """地区・施設名・住所で避難所を絞り込む"""
    results = [s for s in shelters if not district or s.get('district') == district]
    if query:
        normalized_query = query.strip().casefold()
        results = [
            shelter for shelter in results
            if normalized_query in ' '.join((
                str(shelter.get('name', '')),
                str(shelter.get('address', '')),
                str(shelter.get('district', ''))
            )).casefold()
        ]
    return results


def shelter_for_display(shelter):
    display = dict(shelter)
    open_status = shelter.get('open_status', shelter.get('status', '状況未登録'))
    crowd_status = shelter.get('crowd_status', '未確認')
    display['display_open_status'] = (
        open_status if open_status in SHELTER_STATUSES else '状況未登録'
    )
    display['display_crowd_status'] = (
        crowd_status if crowd_status in SHELTER_CROWD_STATUSES else '未確認'
    )

    facilities = shelter.get('facilities', [])
    facilities = list(facilities) if isinstance(facilities, list) else []
    for key, label in (
        ('wheelchair', 'バリアフリー'),
        ('baby', '授乳室'),
        ('pet', 'ペット可')
    ):
        if shelter.get(key) and label not in facilities:
            facilities.append(label)
    display['display_facilities'] = facilities
    return display


def parse_area_warnings(warning_data):
    """気象庁の新形式JSONから最新の対象市区町村の警報・注意報を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    area_reports = []

    for report in warning_data:
        if not isinstance(report, dict):
            continue

        report_datetime = report.get("reportDatetime")

        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        class20_items = warning.get("class20Items", [])
        if not isinstance(class20_items, list):
            continue

        area = next(
            (
                item for item in class20_items
                if isinstance(item, dict)
                and item.get("areaCode") == AREA_CODE
            ),
            None
        )
        if area:
            area_reports.append((report_datetime, area))

    if not area_reports:
        raise ValueError(f"{AREA_NAME}の警報・注意報データが見つかりません")

    def report_time_key(record):
        value = record[0]
        if not isinstance(value, str):
            return datetime.min.replace(tzinfo=timezone.utc)
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return parsed.replace(tzinfo=JST) if parsed.tzinfo is None else parsed
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    latest_report_datetime, latest_area = max(area_reports, key=report_time_key)
    warnings = []
    seen_codes = set()
    kinds = latest_area.get("kinds", [])
    if isinstance(kinds, list):
        for kind in kinds:
            if not isinstance(kind, dict):
                continue
            status = kind.get("status", "")
            code = kind.get("code", "")
            if status not in ("発表", "継続") or not code or code in seen_codes:
                continue
            warnings.append({
                "name": WARNING_CODES.get(
                    code,
                    f"不明な警報・注意報 (コード: {code})"
                ),
                "code": code,
                "status": status
            })
            seen_codes.add(code)

    return warnings, latest_report_datetime


def get_weather_warnings():
    """対象市区町村の警報・注意報を取得する"""
    try:
        # 青森県の新形式（令和8年～）警報・注意報データを取得
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as res:
            warning_data = json.loads(res.read())

        warnings, report_datetime = parse_area_warnings(warning_data)

        return {
            "area_name": AREA_NAME,
            "warnings": warnings,
            "report_time": format_report_time(report_datetime),
            "report_datetime": report_datetime,
            "last_fetch_time": get_japan_time()
        }

    except Exception:
        return {
            "area_name": AREA_NAME,
            "warnings": [],
            "report_time": "取得失敗",
            "report_datetime": "",
            "last_fetch_time": get_japan_time(),
            "error": True
        }


# トップページ：templates/index.html を返す（住民向け指示も表示する）
@app.route('/')
def index():
    return render_template('index.html')

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    # リダイレクト先を取得（デフォルトは避難所登録画面）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('shelter_register')

    if request.method == 'POST':
        password = request.form.get('password', '').strip()

        # 認証チェック
        username = next(
            (name for name, registered_password in ADMIN_CREDENTIALS.items()
             if registered_password == password),
            None
        )
        if username:
            session['logged_in'] = True
            session['username'] = username
            # ログイン成功後は指定されたページにリダイレクト
            return redirect(next_url)
        return render_template('login.html', error=True, message="パスワードが正しくありません。", next=next_url)

    # ログイン済みの場合は指定されたページにリダイレクト
    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url)

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 避難所登録・編集ページ
@app.route('/shelter_register', methods=['GET', 'POST'])
@login_required
def shelter_register():
    error_message = None
    form_values = {
        'name': '',
        'address': '',
        'status': '状況未登録',
        'crowd_status': '未確認',
        'capacity': '',
        'hazards': [],
        'facilities': [],
        'note': '',
        'location_token': ''
    }
    editing_shelter = None

    if request.method == 'POST':
        action = request.form.get('action', 'create')
        form_values = get_shelter_form_values(request.form)

        if action == 'update_status':
            shelter_id = request.form.get('shelter_id', '')
            status = request.form.get('status', '')
            shelter = next(
                (item for item in shelters if str(item.get('id')) == shelter_id),
                None
            )
            if not shelter:
                error_message = '対象の避難所が見つかりません。'
            elif status not in SHELTER_STATUSES:
                error_message = '開設状況を選択してください。'
            else:
                previous_data = shelter.copy()
                shelter['status'] = status
                shelter['open_status'] = status
                if save_shelters():
                    return redirect(url_for('shelter_register', notice='status_updated'))
                shelter.clear()
                shelter.update(previous_data)
                error_message = '開設状況を保存できませんでした。'

        elif action in ('create', 'update'):
            shelter_id = request.form.get('shelter_id', '')
            existing = None
            if action == 'update':
                existing = next(
                    (item for item in shelters if str(item.get('id')) == shelter_id),
                    None
                )
                if not existing:
                    error_message = '編集する避難所が見つかりません。'
                else:
                    editing_shelter = existing

            if not error_message:
                values, capacity, location, errors = validate_shelter_form(
                    request.form, existing
                )
                form_values = values
                if errors:
                    error_message = ' '.join(errors)
                else:
                    shelter_data = {
                        'name': values['name'],
                        'address': values['address'],
                        'status': values['status'],
                        'open_status': values['status'],
                        'crowd_status': values['crowd_status'],
                        'capacity': capacity,
                        'hazards': values['hazards'],
                        'facilities': values['facilities'],
                        'note': values['note'],
                        'latitude': location[0],
                        'longitude': location[1]
                    }
                    if existing:
                        previous_data = existing.copy()
                        existing.update(shelter_data)
                    else:
                        existing_ids = [
                            int(item['id']) for item in shelters
                            if str(item.get('id', '')).isdigit()
                        ]
                        shelter_data['id'] = max(existing_ids, default=0) + 1
                        shelters.insert(0, shelter_data)

                    if save_shelters():
                        return redirect(url_for(
                            'shelter_register',
                            notice='updated' if existing else 'created'
                        ))

                    if existing:
                        existing.clear()
                        existing.update(previous_data)
                        editing_shelter = existing
                    else:
                        shelters.remove(shelter_data)
                    error_message = '避難所情報を保存できませんでした。'
        else:
            error_message = '操作を確認できませんでした。'

    elif request.args.get('edit'):
        edit_id = request.args.get('edit', '')
        editing_shelter = next(
            (item for item in shelters if str(item.get('id')) == edit_id),
            None
        )
        if editing_shelter:
            form_values = {
                'name': editing_shelter.get('name', ''),
                'address': editing_shelter.get('address', ''),
                'status': editing_shelter.get(
                    'open_status', editing_shelter.get('status', '状況未登録')
                ),
                'crowd_status': editing_shelter.get('crowd_status', '未確認'),
                'capacity': editing_shelter.get('capacity') or '',
                'hazards': editing_shelter.get('hazards', []),
                'facilities': editing_shelter.get('facilities', []),
                'note': editing_shelter.get('note', ''),
                'location_token': ''
            }
        else:
            error_message = '編集する避難所が見つかりません。'

    return render_template(
        'shelter_register.html',
        shelters=[shelter_for_display(shelter) for shelter in shelters],
        form_values=form_values,
        editing_shelter=editing_shelter,
        error_message=error_message,
        notice=request.args.get('notice'),
        shelter_statuses=SHELTER_STATUSES,
        crowd_statuses=SHELTER_CROWD_STATUSES,
        hazard_options=SHELTER_HAZARDS,
        facility_options=SHELTER_FACILITIES
    )


@app.route('/api/geocode')
def geocode_search():
    global _geocode_last_request

    if not session.get('logged_in'):
        return jsonify({'error': '住所検索にはログインが必要です。'}), 401

    query = request.args.get('q', '').strip()
    if len(query) < 2 or len(query) > 200:
        return jsonify({'error': '住所は2〜200文字で入力してください。'}), 400

    cache_key = query.casefold()
    now = time.monotonic()
    with _geocode_lock:
        expired_keys = [
            key for key, value in GEOCODE_CACHE.items()
            if now - value[0] >= GEOCODE_CACHE_TTL
        ]
        for key in expired_keys:
            GEOCODE_CACHE.pop(key, None)
        cached = GEOCODE_CACHE.get(cache_key)
        if cached and now - cached[0] < GEOCODE_CACHE_TTL:
            candidates = cached[1]
        else:
            if now - _geocode_last_request < GEOCODE_MIN_INTERVAL:
                return jsonify({'error': '住所検索は1秒以上間隔をあけてください。'}), 429
            _geocode_last_request = now
            candidates = None

    if candidates is None:
        params = urllib.parse.urlencode({
            'q': query,
            'format': 'jsonv2',
            'limit': 5,
            'addressdetails': 1,
            'countrycodes': 'jp'
        })
        geocode_request = urllib.request.Request(
            f'{GEOCODE_URL}?{params}',
            headers={'User-Agent': GEOCODE_USER_AGENT}
        )
        try:
            with urllib.request.urlopen(geocode_request, timeout=5) as response:
                payload = json.loads(response.read())
            if not isinstance(payload, list):
                raise ValueError('住所検索の応答形式が正しくありません。')

            candidates = []
            for item in payload[:5]:
                if not isinstance(item, dict):
                    continue
                location = valid_coordinates(item.get('lat'), item.get('lon'))
                address = item.get('display_name')
                if location and isinstance(address, str) and address.strip():
                    candidates.append({
                        'address': address.strip()[:250],
                        'latitude': location[0],
                        'longitude': location[1]
                    })
            with _geocode_lock:
                GEOCODE_CACHE[cache_key] = (time.monotonic(), candidates)
        except Exception:
            return jsonify({'error': '住所候補を取得できませんでした。時間をおいて再度お試しください。'}), 502

    results = []
    for candidate in candidates:
        results.append({
            'address': candidate['address'],
            'token': GEOCODE_TOKEN_SERIALIZER.dumps(candidate)
        })
    return jsonify({'results': results})

# 避難所検索ページ
@app.route('/shelter_search')
def shelter_search():
    districts = sorted({
        shelter.get('district') for shelter in shelters
        if shelter.get('district')
    })
    return render_template('shelter_search.html', districts=districts)

# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    return render_template(
        'search_results.html',
        results=[shelter_for_display(shelter) for shelter in shelters],
        district='',
        query=''
    )


@app.route('/shelters/<shelter_id>')
def shelter_detail(shelter_id):
    shelter = next(
        (item for item in shelters if str(item.get('id')) == shelter_id),
        None
    )
    if not shelter:
        abort(404)

    return render_template(
        'shelter_detail.html',
        shelter=shelter_for_display(shelter),
        coordinates=valid_coordinates(
            shelter.get('latitude'), shelter.get('longitude')
        )
    )


# 指示ボード：住民向けの指示を一覧で確認する
@app.route('/board', methods=['GET', 'POST'])
@login_required
def board():
    error_message = None

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'create':
            target = request.form.get('target', '').strip()
            content = request.form.get('content', '').strip()
            urgency = request.form.get('urgency', '').strip()
            information_type = request.form.get('information_type', 'お知らせ').strip()

            if target not in INSTRUCTION_TARGETS:
                error_message = '発信先を選択してください。'
            elif not content:
                error_message = '指示・発信内容を入力してください。'
            elif urgency not in INSTRUCTION_URGENCIES:
                error_message = '緊急度を選択してください。'
            elif information_type not in INSTRUCTION_TYPES:
                error_message = '情報の種別を選択してください。'
            else:
                numeric_ids = [
                    int(item['id']) for item in instructions
                    if str(item.get('id', '')).isdigit()
                ]
                now = get_japan_time()
                new_instruction = {
                    'id': max(numeric_ids, default=0) + 1,
                    'target': target,
                    'information_type': information_type,
                    'district': request.form.get('district', '').strip(),
                    'content': content,
                    'shelter': request.form.get('shelter', '').strip(),
                    'urgency': urgency,
                    'status': '発令中',
                    'created_at': now,
                    'updated_at': now
                }
                instructions.insert(0, new_instruction)
                if save_instructions():
                    return redirect(url_for('board', notice='created'))
                instructions.remove(new_instruction)
                error_message = '発信を保存できませんでした。時間をおいて再度お試しください。'

        elif action == 'update_status':
            instruction_id = request.form.get('instruction_id', '')
            new_status = request.form.get('status', '')
            instruction = next(
                (item for item in instructions if str(item.get('id')) == instruction_id),
                None
            )

            if not instruction:
                error_message = '対象の発信が見つかりません。'
            elif new_status not in INSTRUCTION_STATUSES:
                error_message = '対応状況を選択してください。'
            else:
                previous_status = instruction.get('status', '発令中')
                previous_updated_at = instruction.get('updated_at', '')
                instruction['status'] = new_status
                instruction['updated_at'] = get_japan_time()
                if save_instructions():
                    return redirect(url_for('board', notice='updated'))
                instruction['status'] = previous_status
                instruction['updated_at'] = previous_updated_at
                error_message = '対応状況を保存できませんでした。時間をおいて再度お試しください。'
        else:
            error_message = '操作を確認できませんでした。'

    active_count = sum(
        item.get('status', '発令中') in ('発令中', '対応中')
        for item in instructions
    )
    return render_template(
        'board.html',
        instructions=instructions,
        targets=INSTRUCTION_TARGETS,
        information_types=INSTRUCTION_TYPES,
        urgencies=INSTRUCTION_URGENCIES,
        statuses=INSTRUCTION_STATUSES,
        active_count=active_count,
        error_message=error_message,
        notice=request.args.get('notice')
    )

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results')
def search_results():
    district = request.args.get('district', '').strip()
    query = request.args.get('q', '').strip()
    results = filter_shelters(district, query)
    display_results = []
    for shelter in results:
        display_shelter = shelter_for_display(shelter)
        display_shelter['detail_url'] = url_for(
            'shelter_detail',
            shelter_id=shelter['id'],
            q=query,
            district=district
        )
        display_results.append(display_shelter)
    return render_template(
        'search_results.html',
        results=display_results,
        district=district,
        query=query
    )

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    results = filter_shelters(request.args.get('district'))
    return jsonify(results)

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    return jsonify(get_weather_warnings())


def information_datetime_key(value):
    if not isinstance(value, str) or not value:
        return datetime.min.replace(tzinfo=JST)
    try:
        if '年' in value:
            return datetime.strptime(value, '%Y年%m月%d日 %H:%M').replace(tzinfo=JST)
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=JST) if parsed.tzinfo is None else parsed
    except ValueError:
        return datetime.min.replace(tzinfo=JST)


@app.route('/api/disaster_information')
def api_disaster_information():
    weather = get_weather_warnings()
    items = []
    weather_state = 'error' if weather.get('error') else 'ok'
    weather_message = (
        '気象情報を取得できませんでした。再読み込みしてください。'
        if weather.get('error') else ''
    )

    if weather.get('error'):
        pass
    elif weather.get('warnings'):
        for warning in weather['warnings']:
            items.append({
                'source': '気象庁',
                'type': '気象情報',
                'title': warning['name'],
                'status': warning['status'],
                'datetime': weather.get('report_time', '不明'),
                'sort_datetime': weather.get('report_datetime', ''),
                'details': f"{weather.get('area_name', AREA_NAME)}で{warning['status']}中です。",
                'urgency': '高' if '警報' in warning['name'] and '注意報' not in warning['name'] else '中',
                'active': warning['status'] in ('発表', '継続'),
                'shelter': '',
                'shelter_url': ''
            })
    else:
        items.append({
            'source': '気象庁',
            'type': '気象情報',
            'title': '警報・注意報',
            'status': '発表なし',
            'datetime': weather.get('report_time', '不明'),
            'sort_datetime': weather.get('report_datetime', ''),
            'details': f"{weather.get('area_name', AREA_NAME)}に現在発表中の警報・注意報はありません。",
            'urgency': '低',
            'active': False,
            'shelter': '',
            'shelter_url': ''
        })

    for instruction in instructions:
        if instruction.get('target') != '住民':
            continue
        information_type = instruction.get('information_type')
        if information_type not in INSTRUCTION_TYPES:
            information_type = '避難情報' if instruction.get('shelter') else 'お知らせ'
        status = instruction.get('status', '発令中')
        is_active = status in ('発令中', '対応中')
        shelter = instruction.get('shelter', '')
        items.append({
            'source': '防災アプリ',
            'type': information_type,
            'title': instruction.get('content', ''),
            'status': status,
            'datetime': instruction.get('created_at', '日時不明'),
            'sort_datetime': instruction.get('created_at', ''),
            'details': instruction.get('content', ''),
            'urgency': instruction.get('urgency', '中'),
            'active': is_active,
            'shelter': shelter,
            'shelter_url': url_for('all_shelters') if shelter else ''
        })

    items.sort(
        key=lambda item: information_datetime_key(item['sort_datetime']),
        reverse=True
    )
    has_emergency = any(
        item['active'] and (
            item['urgency'] in ('高', '緊急')
            or (item['type'] == '気象情報' and '警報' in item['title'] and '注意報' not in item['title'])
        )
        for item in items
    )
    return jsonify({
        'items': items,
        'updated_at': get_japan_time(),
        'weather_status': {
            'state': weather_state,
            'message': weather_message,
            'last_fetched_at': weather.get('last_fetch_time', '')
        },
        'has_emergency': has_emergency
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)
