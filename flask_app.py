from flask import Flask, request, jsonify, send_from_directory
import requests
import os
import datetime

app = Flask(__name__)

# ── Token CEIDG – przechowywany wyłącznie po stronie serwera ─────────────────
CEIDG_TOKEN = 'eyJraWQiOiJjZWlkZyIsImFsZyI6IkhTNTEyIn0.eyJnaXZlbl9uYW1lIjoiREFXSUQiLCJwZXNlbCI6Ijk5MDIyMDAzNTMzIiwiaWF0IjoxNzc1MjMyNDU5LCJmYW1pbHlfbmFtZSI6IkpVU1RZxYNTS0kiLCJjbGllbnRfaWQiOiJVU0VSLTk5MDIyMDAzNTMzLURBV0lELUpVU1RZxYNTS0kifQ.RgU7tn2IVo8wBj7TStTgv2akNfnkWqMYZkKSAfIG4xTOrkTpQSRje73P1JK0LC1yZhXRnwd1bT8GeRBK8Wvk2g'
CEIDG_BASE  = 'https://dane.biznes.gov.pl/api/ceidg/v3'

@app.route('/')
def index():
    return send_from_directory(os.path.dirname(__file__), 'carrier-verify.html')

# ── VIES proxy ────────────────────────────────────────────────────────────────
@app.route('/vies')
def vies():
    country = request.args.get('country', '').upper().strip()
    vat     = request.args.get('vat', '').strip()
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
            data    = resp.json()
            records = data.get('result', {}).get('records', [])
            all_records.extend(records)
        except Exception as e:
            errors.append(f'{resource_id}: {str(e)}')

    if all_records:
        return jsonify({'success': True, 'records': all_records})
    elif errors:
        return jsonify({'success': False, 'error': '; '.join(errors), 'records': [], 'debug': 'check logs'}), 500
    else:
        return jsonify({'success': True, 'records': []})

# ── Biała Lista VAT proxy ─────────────────────────────────────────────────────
@app.route('/bialalistava')
def bialalistava():
    nip = request.args.get('nip', '').replace('-', '').replace(' ', '').strip()
    if not nip or len(nip) != 10 or not nip.isdigit():
        return jsonify({'success': False, 'error': 'Nieprawidłowy NIP (wymagane 10 cyfr)'}), 400

    url    = f'https://wl-api.mf.gov.pl/api/search/nip/{nip}'
    params = {'date': datetime.date.today().isoformat()}

    try:
        resp = requests.get(url, params=params, timeout=20)
        print(f"[BIALA_LISTA] NIP={nip} status={resp.status_code} body={resp.text[:400]}")

        if resp.status_code == 404:
            return jsonify({'success': True, 'found': False, 'nip': nip, 'result': None, 'source': 'wl-api.mf.gov.pl'})

        resp.raise_for_status()
        data    = resp.json()
        result  = data.get('result', {})
        subject = result.get('subject', None)

        return jsonify({
            'success': True,
            'found': subject is not None,
            'nip': nip,
            'result': subject,
            'requestDateTime': result.get('requestDateTime', ''),
            'source': 'wl-api.mf.gov.pl'
        })

    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': 'Przekroczono czas oczekiwania (API MF niedostępne)'}), 504
    except requests.exceptions.HTTPError as e:
        return jsonify({'success': False, 'error': f'Błąd HTTP: {str(e)}'}), 502
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ── CEIDG proxy ───────────────────────────────────────────────────────────────
# Token JWT nigdy nie trafia do przeglądarki — jest dodawany tu, po stronie serwera.
@app.route('/ceidg')
def ceidg():
    nip = request.args.get('nip', '').replace('-', '').replace(' ', '').strip()
    if not nip or len(nip) != 10 or not nip.isdigit():
        return jsonify({'success': False, 'error': 'Nieprawidłowy NIP (wymagane 10 cyfr)'}), 400

    headers = {
        'Authorization': f'Bearer {CEIDG_TOKEN}',
        'Accept': 'application/json',
    }

    try:
        # Próba 1: endpoint /firma (singiel, pełne dane) — nip jako string
        resp = requests.get(f'{CEIDG_BASE}/firma', params={'nip': nip}, headers=headers, timeout=20)
        print(f"[CEIDG] /firma NIP={nip} status={resp.status_code} body={resp.text[:300]}")

        if resp.status_code == 204:
            # 204 = brak wyników — nie zwracamy od razu, próbujemy /firmy
            pass
        elif resp.ok:
            data  = resp.json()
            firma = data.get('firma', [])
            if isinstance(firma, list):
                firma = firma[0] if firma else None
            if firma:
                return jsonify({'success': True, 'found': True, 'nip': nip, 'firma': firma})
            # pusta lista firma[] — przechodzimy do /firmy

        # Próba 2: endpoint /firmy (lista) — nip jako tablica zgodnie ze specyfikacją API
        # API wymaga wielokrotnego parametru: ?nip=X (requests obsługuje listę krotek)
        resp2 = requests.get(
            f'{CEIDG_BASE}/firmy',
            params=[('nip', nip), ('limit', 1)],
            headers=headers,
            timeout=20
        )
        print(f"[CEIDG] /firmy NIP={nip} status={resp2.status_code} body={resp2.text[:300]}")

        if resp2.status_code == 204:
            return jsonify({'success': True, 'found': False, 'nip': nip, 'firma': None})

        if not resp2.ok:
            return jsonify({'success': False, 'error': f'HTTP {resp2.status_code}', 'nip': nip}), 502

        data2 = resp2.json()
        firmy = data2.get('firmy', [])

        if not firmy:
            return jsonify({'success': True, 'found': False, 'nip': nip, 'firma': None})

        # Pobierz pełne szczegóły przez link z odpowiedzi listy
        link = firmy[0].get('link')
        if link:
            resp3 = requests.get(link, headers=headers, timeout=20)
            print(f"[CEIDG] detail link={link} status={resp3.status_code}")
            if resp3.ok:
                data3 = resp3.json()
                firma3 = data3.get('firma', [])
                if isinstance(firma3, list):
                    firma3 = firma3[0] if firma3 else None
                if firma3:
                    return jsonify({'success': True, 'found': True, 'nip': nip, 'firma': firma3})

        # Fallback: dane z listy (mniej szczegółowe, ale coś zwracamy)
        return jsonify({'success': True, 'found': True, 'nip': nip, 'firma': firmy[0], 'partial': True})

    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': 'Przekroczono czas oczekiwania (API CEIDG niedostępne)'}), 504
    except requests.exceptions.HTTPError as e:
        return jsonify({'success': False, 'error': f'Błąd HTTP: {str(e)}'}), 502
    except Exception as e:
        print(f"[CEIDG] wyjątek: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ── KRS proxy (Portal Rejestrów Sądowych — prs.ms.gov.pl — bezpłatne, bez klucza) ──
# Prawidłowe API: https://prs.ms.gov.pl/krs/openApi
# Krok 1: szukamy podmiotów po NIP → dostajemy numer KRS
# Krok 2: pobieramy odpis aktualny po numerze KRS
KRS_BASE = 'https://api-krs.ms.gov.pl/api/krs'

@app.route('/krs')
def krs():
    nip = request.args.get('nip', '').replace('-', '').replace(' ', '').strip()
    if not nip or len(nip) != 10 or not nip.isdigit():
        return jsonify({'success': False, 'error': 'Nieprawidłowy NIP (wymagane 10 cyfr)'}), 400

    headers = {
        'Accept': 'application/json',
        'User-Agent': 'Mozilla/5.0 (compatible; CarrierCheck/1.0)',
    }

    try:
        # ── Krok 1: wyszukiwanie podmiotów po NIP ──────────────────────────────
        # Endpoint: GET /api/krs/OdpisAktualny/wyszukaj?nip=...
        # Zwraca listę podmiotów pasujących do NIP
        search_url = f'{KRS_BASE}/OdpisAktualny/wyszukaj'
        r1 = requests.get(search_url, params={'nip': nip}, headers=headers, timeout=20)
        print(f"[KRS] wyszukaj NIP={nip} status={r1.status_code} body={r1.text[:400]}")

        krs_nr = None

        if r1.ok:
            items = r1.json()
            # Odpowiedź to lista lub obiekt z listą podmiotów
            if isinstance(items, list) and len(items) > 0:
                krs_nr = items[0].get('numerKRS') or items[0].get('krs')
            elif isinstance(items, dict):
                lista = items.get('odpisy') or items.get('podmioty') or items.get('items') or []
                if lista:
                    krs_nr = lista[0].get('numerKRS') or lista[0].get('krs')

        # ── Krok 2: jeśli nie znaleźliśmy przez /wyszukaj, próbuj /search ──────
        if not krs_nr:
            r1b = requests.get(
                f'{KRS_BASE}/OdpisAktualny/search',
                params={'nip': nip},
                headers=headers,
                timeout=20
            )
            print(f"[KRS] search NIP={nip} status={r1b.status_code} body={r1b.text[:400]}")
            if r1b.ok:
                items2 = r1b.json()
                if isinstance(items2, list) and len(items2) > 0:
                    krs_nr = items2[0].get('numerKRS') or items2[0].get('krs')
                elif isinstance(items2, dict):
                    lista2 = items2.get('odpisy') or items2.get('podmioty') or items2.get('items') or []
                    if lista2:
                        krs_nr = lista2[0].get('numerKRS') or lista2[0].get('krs')

        # ── Krok 2b: próbuj endpoint /podmiot z NIP ────────────────────────────
        if not krs_nr:
            for rejestr in ['P', 'S']:
                r1c = requests.get(
                    f'{KRS_BASE}/OdpisAktualny/',
                    params={'nip': nip, 'rejestr': rejestr, 'format': 'json'},
                    headers=headers,
                    timeout=20
                )
                print(f"[KRS] OdpisAktualny rejestr={rejestr} NIP={nip} status={r1c.status_code} body={r1c.text[:400]}")
                if r1c.ok and r1c.text.strip():
                    try:
                        d = r1c.json()
                        # Jeśli dostaliśmy pełny odpis od razu — zwracamy go
                        if d and isinstance(d, dict) and ('odpis' in d or 'dane' in d or 'naglowekA' in d):
                            return jsonify({'success': True, 'found': True, 'nip': nip, 'odpis': d})
                        # Może to lista — wyciągamy numer KRS
                        if isinstance(d, list) and len(d) > 0:
                            krs_nr = d[0].get('numerKRS') or d[0].get('krs')
                            if krs_nr:
                                break
                    except Exception:
                        pass

        if not krs_nr:
            return jsonify({'success': True, 'found': False, 'nip': nip, 'odpis': None})

        # ── Krok 3: pobieramy odpis aktualny po numerze KRS ───────────────────
        # Numer KRS musi być 10-cyfrowy (z zerami wiodącymi)
        krs_nr_str = str(krs_nr).zfill(10)

        # Próbuj rejestr P (przedsiębiorcy) i S (stowarzyszenia)
        odpis = None
        for rejestr in ['P', 'S']:
            r2 = requests.get(
                f'{KRS_BASE}/OdpisAktualny/{krs_nr_str}',
                params={'rejestr': rejestr, 'format': 'json'},
                headers=headers,
                timeout=25
            )
            print(f"[KRS] OdpisAktualny/{krs_nr_str} rejestr={rejestr} status={r2.status_code}")
            if r2.ok and r2.text.strip():
                try:
                    odpis = r2.json()
                    if odpis:
                        break
                except Exception:
                    pass

        if not odpis:
            return jsonify({'success': True, 'found': False, 'nip': nip, 'odpis': None})

        return jsonify({'success': True, 'found': True, 'nip': nip, 'krs': krs_nr_str, 'odpis': odpis})

    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': 'Przekroczono czas oczekiwania (API KRS niedostępne)'}), 504
    except requests.exceptions.HTTPError as e:
        return jsonify({'success': False, 'error': f'Błąd HTTP: {str(e)}'}), 502
    except Exception as e:
        print(f"[KRS] wyjątek: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
