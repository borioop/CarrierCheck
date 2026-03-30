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

# ── KREPTD proxy ──────────────────────────────────────────────────────────────
@app.route('/kreptd')
def kreptd():
    nip = request.args.get('nip', '').replace('-', '').replace(' ', '').strip()
    if not nip or len(nip) != 10 or not nip.isdigit():
        return jsonify({'success': False, 'error': 'Nieprawidłowy NIP'}), 400

    all_records = []
    errors = []
    resources = [
        '28f6a3dc-be26-4e30-be8e-0e1ce498c935',
        'f4026e09-77c1-466c-a9d5-46b05c62a9b4',
    ]

    for resource_id in resources:
        try:
            sql = f'SELECT * FROM "{resource_id}" WHERE nip = \'{nip}\' LIMIT 20'
            url = 'https://dane.gov.pl/api/3/action/datastore_search_sql'
            resp = requests.get(url, params={'sql': sql}, headers={'Accept': 'application/json'}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            records = data.get('result', {}).get('records', [])
            all_records.extend(records)
        except Exception as e:
            errors.append(f'{resource_id}: {str(e)}')

    if not all_records:
        for resource_id in resources:
            try:
                resp = requests.get(
                    'https://dane.gov.pl/api/3/action/datastore_search',
                    params={'resource_id': resource_id, 'q': nip, 'limit': 20},
                    headers={'Accept': 'application/json'}, timeout=15
                )
                resp.raise_for_status()
                records = resp.json().get('result', {}).get('records', [])
                records = [r for r in records if str(r.get('nip', '')).replace('-','').replace(' ','') == nip]
                all_records.extend(records)
            except Exception as e:
                errors.append(f'fallback {resource_id}: {str(e)}')

    if all_records:
        return jsonify({'success': True, 'records': all_records})
    elif errors:
        return jsonify({'success': False, 'error': '; '.join(errors), 'records': []}), 500
    else:
        return jsonify({'success': True, 'records': []})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
