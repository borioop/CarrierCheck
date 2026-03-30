from flask import Flask, request, jsonify, send_from_directory
import requests
import os
import json

app = Flask(__name__)

# Serwuje plik HTML gdy ktoś wchodzi na stronę główną
@app.route('/')
def index():
    return send_from_directory(os.path.dirname(__file__), 'carrier-verify.html')


# ── VIES proxy — rozwiązuje problem CORS z ec.europa.eu ─────────────────────
@app.route('/vies')
def vies():
    country = request.args.get('country', '').upper().strip()
    vat = request.args.get('vat', '').strip()

    if not country or not vat:
        return jsonify({'error': 'Brak parametrów country lub vat'}), 400

    url = f'https://ec.europa.eu/taxation_customs/vies/rest-api/ms/{country}/vat/{vat}'
    try:
        resp = requests.get(url, headers={'Accept': 'application/json'}, timeout=15)
        data = resp.json()
        return jsonify(data)
    except Exception as e:
        # Fallback: vatcomply.com
        try:
            fb = requests.get(f'https://api.vatcomply.com/vat?vat_number={country}{vat}', timeout=10)
            return jsonify(fb.json())
        except Exception as e2:
            return jsonify({'error': str(e2)}), 500


# ── KREPTD proxy ─────────────────────────────────────────────────────────────
@app.route('/kreptd')
def kreptd():
    nip = request.args.get('nip', '').replace('-', '').replace(' ', '').strip()

    if not nip or len(nip) != 10 or not nip.isdigit():
        return jsonify({'success': False, 'error': 'Nieprawidłowy NIP — podaj 10 cyfr'}), 400

    resources = [
        '28f6a3dc-be26-4e30-be8e-0e1ce498c935',  # zezwolenia / licencje krajowe
        'f4026e09-77c1-466c-a9d5-46b05c62a9b4',  # licencje wspólnotowe
    ]

    all_records = []
    errors = []

    for resource_id in resources:
        url = 'https://dane.gov.pl/api/3/action/datastore_search'
        # Przekazujemy filters jako słownik — requests zakoduje to poprawnie
        params = {
            'resource_id': resource_id,
            'filters': json.dumps({"nip": nip}),
            'limit': 20
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            records = data.get('result', {}).get('records', [])
            all_records.extend(records)
        except requests.exceptions.HTTPError as e:
            errors.append(f'HTTP {e.response.status_code} dla resource {resource_id}')
        except Exception as e:
            errors.append(f'{resource_id}: {str(e)}')

    if all_records:
        return jsonify({'success': True, 'records': all_records})
    elif errors:
        return jsonify({'success': False, 'error': '; '.join(errors), 'records': []}), 500
    else:
        return jsonify({'success': True, 'records': []})