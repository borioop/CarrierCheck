from flask import Flask, request, jsonify, send_from_directory
import requests
import os

app = Flask(__name__)

CEIDG_API_KEY = 'eyJraWQiOiJjZWlkZyIsImFsZyI6IkhTNTEyIn0.eyJnaXZlbl9uYW1lIjoiREFXSUQiLCJwZXNlbCI6Ijk5MDIyMDAzNTMzIiwiaWF0IjoxNzc1MjMyNDU5LCJmYW1pbHlfbmFtZSI6IkpVU1RZxYNTS0kiLCJjbGllbnRfaWQiOiJVU0VSLTk5MDIyMDAzNTMzLURBV0lELUpVU1RZxYNTS0kifQ.RgU7tn2IVo8wBj7TStTgv2akNfnkWqMYZkKSAfIG4xTOrkTpQSRje73P1JK0LC1yZhXRnwd1bT8GeRBK8Wvk2g'

@app.route('/')
def index():
    return send_from_directory(os.path.dirname(__file__), 'carrier-verify.html')

# VIES proxy
@app.route('/vies')
def vies():
    country = request.args.get('country', '').upper().strip()
    vat = request.args.get('vat', '').strip()
    if not country or not vat:
        return jsonify({'error': 'Brak parametrow country lub vat'}), 400
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

# KREPTD proxy
@app.route('/kreptd')
def kreptd():
    nip = request.args.get('nip', '').replace('-', '').replace(' ', '').strip()
    if not nip or len(nip) != 10 or not nip.isdigit():
        return jsonify({'success': False, 'error': 'Nieprawidlowy NIP'}), 400
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

# Biala Lista VAT proxy
@app.route('/bialalistava')
def bialalistava():
    nip = request.args.get('nip', '').replace('-', '').replace(' ', '').strip()
    if not nip or len(nip) != 10 or not nip.isdigit():
        return jsonify({'success': False, 'error': 'Nieprawidlowy NIP (wymagane 10 cyfr)'}), 400
    url = f'https://wl-api.mf.gov.pl/api/search/nip/{nip}'
    params = {'date': __import__('datetime').date.today().isoformat()}
    try:
        resp = requests.get(url, params=params, timeout=20)
        print(f"[BIALA_LISTA] NIP={nip} status={resp.status_code} body={resp.text[:400]}")
        if resp.status_code == 404:
            return jsonify({'success': True, 'found': False, 'nip': nip, 'result': None, 'source': 'wl-api.mf.gov.pl'})
        resp.raise_for_status()
        data = resp.json()
        result = data.get('result', {})
        subject = result.get('subject', None)
        return jsonify({
            'success': True, 'found': subject is not None, 'nip': nip,
            'result': subject, 'requestDateTime': result.get('requestDateTime', ''),
            'source': 'wl-api.mf.gov.pl'
        })
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': 'Przekroczono czas oczekiwania (API MF niedostepne)'}), 504
    except requests.exceptions.HTTPError as e:
        return jsonify({'success': False, 'error': f'Blad HTTP: {str(e)}'}), 502
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# CEIDG proxy - DIAGNOSTYKA DOSTEPU DO DOMENY
@app.route('/ceidg')
def ceidg():
    nip = request.args.get('nip', '').replace('-', '').replace(' ', '').strip()
    if not nip or len(nip) != 10 or not nip.isdigit():
        return jsonify({'success': False, 'error': 'Nieprawidlowy NIP (wymagane 10 cyfr)'}), 400

    auth_headers = {
        'Authorization': 'Bearer ' + CEIDG_API_KEY,
        'Accept': 'application/json',
    }

    # Test 1: czy w ogole odpowiada root domeny (bez auth)
    try:
        r_root = requests.get('https://dane.biznes.gov.pl/', timeout=10)
        root_status = r_root.status_code
        root_body = r_root.text[:200]
    except Exception as e:
        root_status = f'ERROR: {e}'
        root_body = ''

    # Test 2: v2/firmy z NIP (z auth)
    try:
        r1 = requests.get('https://dane.biznes.gov.pl/api/ceidg/v2/firmy',
                          headers=auth_headers, params={'nip': nip}, timeout=15)
        s1, b1 = r1.status_code, r1.text[:300]
    except Exception as e:
        s1, b1 = f'ERROR: {e}', ''

    # Test 3: v2/firmy BEZ auth
    try:
        r2 = requests.get('https://dane.biznes.gov.pl/api/ceidg/v2/firmy',
                          params={'nip': nip}, timeout=15)
        s2, b2 = r2.status_code, r2.text[:300]
    except Exception as e:
        s2, b2 = f'ERROR: {e}', ''

    # Test 4: v3/firmy z auth (najnowsze API)
    try:
        r3 = requests.get('https://dane.biznes.gov.pl/api/ceidg/v3/firmy',
                          headers=auth_headers, params={'nip': nip}, timeout=15)
        s3, b3 = r3.status_code, r3.text[:300]
    except Exception as e:
        s3, b3 = f'ERROR: {e}', ''

    # Test 5: Swagger/OpenAPI spec
    try:
        r4 = requests.get('https://dane.biznes.gov.pl/api/ceidg/v2/swagger.json',
                          headers=auth_headers, timeout=10)
        s4, b4 = r4.status_code, r4.text[:300]
    except Exception as e:
        s4, b4 = f'ERROR: {e}', ''

    return jsonify({
        'success': True, 'found': False, 'firmy': [], 'nip': nip,
        '_diag': {
            'root_domain': {'status': root_status, 'body': root_body},
            'v2_firmy_with_auth': {'status': s1, 'body': b1},
            'v2_firmy_no_auth':   {'status': s2, 'body': b2},
            'v3_firmy_with_auth': {'status': s3, 'body': b3},
            'v2_swagger':         {'status': s4, 'body': b4},
        }
    })
