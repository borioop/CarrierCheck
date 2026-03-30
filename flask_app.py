from flask import Flask, request, jsonify, send_from_directory
import requests
import os
import json

app = Flask(__name__)

@app.route('/')
def index():
    return send_from_directory(os.path.dirname(__file__), 'carrier-verify.html')

# ── VIES proxy ────────────────────────────────────────────────────────────────
@app.route('/vies')
def vies():
    country = request.args.get('country', '').upper().strip()
    vat = request.args.get('vat', '').strip()
    if not country or not vat:
        return jsonify({'error': 'Brak parametrów country lub vat'}), 400
    url = f'https://ec.europa.eu/taxation_customs/vies/rest-api/ms/{country}/vat/{vat}'
    try:
        resp = requests.get(url, headers={'Accept': 'application/json'}, timeout=15)
        return jsonify(resp.json())
    except Exception as e:
        try:
            fb = requests.get(f'https://api.vatcomply.com/vat?vat_number={country}{vat}', timeout=10)
            return jsonify(fb.json())
        except Exception as e2:
            return jsonify({'error': str(e2)}), 500

@app.route('/kreptd')
def kreptd():
    nip = request.args.get('nip', '').replace('-', '').replace(' ', '').strip()
    if not nip or len(nip) != 10 or not nip.isdigit():
        return jsonify({'success': False, 'error': 'Nieprawidłowy NIP'}), 400

    headers = {
        'Accept': 'application/json, text/plain, */*',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36',
        'Referer': 'https://dane.gov.pl/',
        'Accept-Language': 'pl-PL,pl;q=0.9',
    }

    all_records = []
    errors = []
    resources = [
        '28f6a3dc-be26-4e30-be8e-0e1ce498c935',
        'f4026e09-77c1-466c-a9d5-46b05c62a9b4',
    ]

    for resource_id in resources:
        try:
            sql = f'SELECT * FROM "{resource_id}" WHERE nip = \'{nip}\' LIMIT 20'
            resp = requests.get(
                'https://dane.gov.pl/api/3/action/datastore_search_sql',
                params={'sql': sql},
                headers=headers,
                timeout=20
            )
            # Loguj co faktycznie dostajemy
            print(f"[KREPTD] {resource_id} status={resp.status_code} body={resp.text[:300]}")
            resp.raise_for_status()
            data = resp.json()
            records = data.get('result', {}).get('records', [])
            all_records.extend(records)
        except Exception as e:
            errors.append(f'{resource_id}: {str(e)}')

    if all_records:
        return jsonify({'success': True, 'records': all_records})
    elif errors:
        return jsonify({'success': False, 'error': '; '.join(errors), 'records': [], 'debug': 'check render logs'}), 500
    else:
        return jsonify({'success': True, 'records': []})
