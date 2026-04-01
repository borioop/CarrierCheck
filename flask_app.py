from flask import Flask, request, jsonify, send_from_directory
import requests
import os
import json
import xml.etree.ElementTree as ET

app = Flask(__name__)

# ── GUS BIR1 proxy ────────────────────────────────────────────────────────────
# Klucz testowy GUS działa na środowisku testowym (birapi.stat.gov.pl/wiadomoscibir/test)
# Na produkcji zamień klucz na swój z https://api.stat.gov.pl/Home/RegonApi
GUS_API_KEY = os.environ.get('GUS_API_KEY', 'abcde12345abcde12345')

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

# ── Biała Lista VAT proxy ─────────────────────────────────────────────────────
@app.route('/bialalistava')
def bialalistava():
    nip = request.args.get('nip', '').replace('-', '').replace(' ', '').strip()
    if not nip or len(nip) != 10 or not nip.isdigit():
        return jsonify({'success': False, 'error': 'Nieprawidłowy NIP (wymagane 10 cyfr)'}), 400

    # Oficjalne API MF – Biała Lista podatników VAT
    url = f'https://wl-api.mf.gov.pl/api/search/nip/{nip}'
    params = {
        'date': __import__('datetime').date.today().isoformat()
    }

    try:
        resp = requests.get(url, params=params, timeout=20)
        print(f"[BIALA_LISTA] NIP={nip} status={resp.status_code} body={resp.text[:400]}")

        if resp.status_code == 404:
            # Nie znaleziono — podatnik nie figuruje na liście
            return jsonify({
                'success': True,
                'found': False,
                'nip': nip,
                'result': None,
                'source': 'wl-api.mf.gov.pl'
            })

        resp.raise_for_status()
        data = resp.json()

        result = data.get('result', {})
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

# ── GUS BIR1 proxy ────────────────────────────────────────────────────────────
GUS_WSDL    = 'https://wyszukiwarkaregon.stat.gov.pl/wsBIR/UslugaBIRzewnPubl.svc'
GUS_NS      = 'http://CIS/BIR/PUBL/2014/07'

def gus_soap(action, body_xml, session_id=None):
    """Wykonuje zapytanie SOAP do GUS BIR1."""
    headers = {
        'Content-Type': 'application/soap+xml; charset=utf-8',
        'SOAPAction':    action,
    }
    if session_id:
        headers['sid'] = session_id

    envelope = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
               xmlns:ns="{GUS_NS}">
  <soap:Body>{body_xml}</soap:Body>
</soap:Envelope>"""

    resp = requests.post(GUS_WSDL, data=envelope.encode('utf-8'), headers=headers, timeout=20)
    resp.raise_for_status()
    return ET.fromstring(resp.content)

def gus_login():
    body = f'<ns:Zaloguj xmlns:ns="{GUS_NS}"><ns:pKluczUzytkownika>{GUS_API_KEY}</ns:pKluczUzytkownika></ns:Zaloguj>'
    root = gus_soap('http://CIS/BIR/PUBL/2014/07/IUslugaBIRzewnPubl/Zaloguj', body)
    sid = root.find('.//{%s}ZalogujResult' % GUS_NS)
    return sid.text if sid is not None else None

def gus_logout(sid):
    body = f'<ns:Wyloguj xmlns:ns="{GUS_NS}"><ns:pIdentyfikatorSesji>{sid}</ns:pIdentyfikatorSesji></ns:Wyloguj>'
    try:
        gus_soap('http://CIS/BIR/PUBL/2014/07/IUslugaBIRzewnPubl/Wyloguj', body)
    except Exception:
        pass

def gus_search_nip(sid, nip):
    body = f"""<ns:DaneSzukajPodmioty xmlns:ns="{GUS_NS}">
      <ns:pParametryWyszukiwania>
        <ns1:Nip xmlns:ns1="http://CIS/BIR/PUBL/2014/07/DataContract">{nip}</ns1:Nip>
      </ns:pParametryWyszukiwania>
    </ns:DaneSzukajPodmioty>"""
    root = gus_soap('http://CIS/BIR/PUBL/2014/07/IUslugaBIRzewnPubl/DaneSzukajPodmioty', body, sid)
    result_el = root.find('.//{%s}DaneSzukajPodmiotyResult' % GUS_NS)
    return result_el.text if result_el is not None else None

def gus_get_full_data(sid, regon, typ):
    """Pobiera pełne dane podmiotu wg REGON."""
    # Dobieramy raport: P – osoba prawna, F – fizyczna, LP – lokalny oddział prawny
    raport_map = {
        'P':  'BIR11OsPrawna',
        'LP': 'BIR11OsPrawnaLokalnaJednostka',
        'F':  'BIR11OsFizycznaDzialalnoscCeidg',
        'LF': 'BIR11OsFizycznaLokalnaJednostka',
    }
    raport = raport_map.get(typ, 'BIR11OsPrawna')
    body = f"""<ns:DanePobierzPelnyRaport xmlns:ns="{GUS_NS}">
      <ns:pRegon>{regon}</ns:pRegon>
      <ns:pNazwaRaportu>{raport}</ns:pNazwaRaportu>
    </ns:DanePobierzPelnyRaport>"""
    root = gus_soap('http://CIS/BIR/PUBL/2014/07/IUslugaBIRzewnPubl/DanePobierzPelnyRaport', body, sid)
    result_el = root.find('.//{%s}DanePobierzPelnyRaportResult' % GUS_NS)
    return result_el.text if result_el is not None else None

def xml_to_dict(xml_string):
    """Zamienia XML ze stringa na słownik Python."""
    if not xml_string:
        return {}
    try:
        root = ET.fromstring(xml_string)
        result = {}
        for child in root.iter():
            tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
            if child.text and child.text.strip():
                result[tag] = child.text.strip()
        return result
    except Exception:
        return {}

@app.route('/gus')
def gus():
    nip = request.args.get('nip', '').replace('-', '').replace(' ', '').strip()
    if not nip or len(nip) != 10 or not nip.isdigit():
        return jsonify({'success': False, 'error': 'Nieprawidłowy NIP (wymagane 10 cyfr)'}), 400

    sid = None
    try:
        # 1. Logowanie
        sid = gus_login()
        if not sid:
            return jsonify({'success': False, 'error': 'Nie udało się zalogować do GUS BIR'}), 502

        # 2. Szukanie po NIP
        raw_search = gus_search_nip(sid, nip)
        if not raw_search or raw_search.strip() in ('', '[]', '<root/>'):
            return jsonify({'success': True, 'found': False, 'nip': nip, 'data': None})

        search_data = xml_to_dict(raw_search)
        regon  = search_data.get('Regon', '').strip()
        typ    = search_data.get('Typ', 'P').strip()   # P, LP, F, LF

        # 3. Pobieranie pełnych danych
        full_data = {}
        if regon:
            raw_full = gus_get_full_data(sid, regon, typ)
            full_data = xml_to_dict(raw_full)

        # Łączymy dane z obu odpowiedzi
        combined = {**search_data, **full_data}

        print(f"[GUS] NIP={nip} REGON={regon} TYP={typ} keys={list(combined.keys())[:10]}")

        return jsonify({
            'success': True,
            'found':   True,
            'nip':     nip,
            'regon':   regon,
            'typ':     typ,
            'data':    combined,
        })

    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': 'Przekroczono czas oczekiwania (GUS BIR niedostępne)'}), 504
    except requests.exceptions.HTTPError as e:
        return jsonify({'success': False, 'error': f'Błąd HTTP GUS: {str(e)}'}), 502
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        if sid:
            gus_logout(sid)
