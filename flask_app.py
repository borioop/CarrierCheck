from flask import Flask, request, jsonify, send_from_directory
import requests
import os
import datetime

app = Flask(__name__)

# ── Token CEIDG – przechowywany wyłącznie po stronie serwera ─────────────────
CEIDG_TOKEN = 'eyJraWQiOiJjZWlkZyIsImFsZyI6IkhTNTEyIn0.eyJnaXZlbl9uYW1lIjoiREFXSUQiLCJwZXNlbCI6Ijk5MDIyMDAzNTMzIiwiaWF0IjoxNzc1MjMyNDU5LCJmYW1pbHlfbmFtZSI6IkpVU1RZxYNTS0kiLCJjbGllbnRfaWQiOiJVU0VSLTk5MDIyMDAzNTMzLURBV0lELUpVU1RZxYNTS0kifQ.RgU7tn2IVo8wBj7TStTgv2akNfnkWqMYZkKSAfIG4xTOrkTpQSRje73P1JK0LC1yZhXRnwd1bT8GeRBK8Wvk2g'
CEIDG_BASE  = 'https://dane.biznes.gov.pl/api/ceidg/v3'
KRS_BASE    = 'https://api-krs.ms.gov.pl/api/krs'

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


# ── Pomocnik: pobierz pełne dane firmy z CEIDG po link/ID ────────────────────
def _ceidg_fetch_detail(link_or_id, headers):
    """Pobiera pełne dane firmy z CEIDG. Przyjmuje pełny URL lub sam ID."""
    url = link_or_id if link_or_id.startswith('http') else f'{CEIDG_BASE}/firma/{link_or_id}'
    try:
        r = requests.get(url, headers=headers, timeout=20)
        print(f"[CEIDG] detail url={url} status={r.status_code}")
        if r.ok:
            data = r.json()
            firma = data.get('firma', [])
            if isinstance(firma, list):
                return firma[0] if firma else None
            return firma
    except Exception as e:
        print(f"[CEIDG] detail wyjątek: {e}")
    return None


# ── CEIDG proxy ───────────────────────────────────────────────────────────────
# NAPRAWKA spółek cywilnych:
#  - NIP podany może być NIPem SPÓŁKI CYWILNEJ (nip_sc), nie właściciela.
#    Endpoint /firma akceptuje tylko NIP właściciela. Dlatego:
#    1. Szukamy po nip (właściciel) przez /firma
#    2. Szukamy po nip (właściciel) przez /firmy
#    3. Szukamy po nip_sc (spółka cywilna) przez /firmy — TO JEST KLUCZOWA POPRAWKA
#    Wyniki z kroku 3 są oznaczane flagą is_spolka_cywilna=True.
#
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
        # ── Krok 1: /firma?nip=... (NIP właściciela, pełne dane) ─────────────
        resp = requests.get(f'{CEIDG_BASE}/firma', params={'nip': nip}, headers=headers, timeout=20)
        print(f"[CEIDG] /firma NIP={nip} status={resp.status_code} body={resp.text[:300]}")

        if resp.ok:
            data  = resp.json()
            firma = data.get('firma', [])
            if isinstance(firma, list):
                firma = firma[0] if firma else None
            if firma:
                return jsonify({
                    'success': True, 'found': True, 'nip': nip,
                    'firma': firma, 'is_spolka_cywilna': False
                })

        # ── Krok 2: /firmy?nip=... (NIP właściciela, lista) ──────────────────
        resp2 = requests.get(
            f'{CEIDG_BASE}/firmy',
            params=[('nip', nip), ('limit', 1)],
            headers=headers,
            timeout=20
        )
        print(f"[CEIDG] /firmy NIP={nip} status={resp2.status_code} body={resp2.text[:300]}")

        if resp2.ok:
            data2 = resp2.json()
            firmy = data2.get('firmy', [])
            if firmy:
                link = firmy[0].get('link')
                firma_detail = _ceidg_fetch_detail(link, headers) if link else None
                return jsonify({
                    'success': True, 'found': True, 'nip': nip,
                    'firma': firma_detail or firmy[0],
                    'partial': firma_detail is None,
                    'is_spolka_cywilna': False
                })

        # ── Krok 3: /firmy?nip_sc=... (NIP SPÓŁKI CYWILNEJ) ─────────────────
        # Spółki cywilne posiadają odrębny NIP spółki (nip_sc), różny od NIP
        # wspólników. Endpoint /firma nie obsługuje nip_sc — trzeba użyć /firmy.
        resp3 = requests.get(
            f'{CEIDG_BASE}/firmy',
            params=[('nip_sc', nip), ('limit', 5)],
            headers=headers,
            timeout=20
        )
        print(f"[CEIDG] /firmy nip_sc={nip} status={resp3.status_code} body={resp3.text[:300]}")

        if resp3.status_code == 204:
            return jsonify({'success': True, 'found': False, 'nip': nip, 'firma': None})

        if resp3.ok:
            data3 = resp3.json()
            firmy3 = data3.get('firmy', [])

            if not firmy3:
                return jsonify({'success': True, 'found': False, 'nip': nip, 'firma': None})

            # Pobierz pełne dane pierwszego wspólnika (każdy wspólnik ma wpis w CEIDG)
            link3 = firmy3[0].get('link')
            firma3_detail = _ceidg_fetch_detail(link3, headers) if link3 else None

            # Zbierz dane wszystkich wspólników (linki do ich wpisów)
            wspolnicy = []
            for f in firmy3:
                wspolnicy.append({
                    'imie': f.get('wlasciciel', {}).get('imie', ''),
                    'nazwisko': f.get('wlasciciel', {}).get('nazwisko', ''),
                    'nip_wspolnika': f.get('wlasciciel', {}).get('nip', ''),
                    'regon_wspolnika': f.get('wlasciciel', {}).get('regon', ''),
                    'status': f.get('status', ''),
                    'link': f.get('link', ''),
                })

            # Wyciągnij dane spółki z pola spolki[] pierwszego wpisu
            spolka_info = None
            if firma3_detail:
                spolki_list = firma3_detail.get('spolki', [])
                for s in spolki_list:
                    if s.get('nip') == nip or s.get('regon'):
                        spolka_info = s
                        break
                if not spolka_info and spolki_list:
                    spolka_info = spolki_list[0]

            return jsonify({
                'success': True,
                'found': True,
                'nip': nip,
                'firma': firma3_detail or firmy3[0],
                'partial': firma3_detail is None,
                'is_spolka_cywilna': True,
                'nip_sc': nip,
                'wspolnicy': wspolnicy,
                'spolka_info': spolka_info,
                'count_wspolnikow': data3.get('count', len(firmy3)),
            })

        # Żadna z prób nie dała wyniku
        return jsonify({'success': True, 'found': False, 'nip': nip, 'firma': None})

    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': 'Przekroczono czas oczekiwania (API CEIDG niedostępne)'}), 504
    except requests.exceptions.HTTPError as e:
        return jsonify({'success': False, 'error': f'Błąd HTTP: {str(e)}'}), 502
    except Exception as e:
        print(f"[CEIDG] wyjątek: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ── KRS proxy ─────────────────────────────────────────────────────────────────
# NAPRAWKA spółek cywilnych:
#  - Spółki cywilne NIE są rejestrowane w KRS (rejestr spółek prawa handlowego).
#    Jeśli CEIDG zwróciło is_spolka_cywilna=True, frontend może pominąć KRS.
#    Jednak dla bezpieczeństwa: jeśli klient odpyta /krs z NIPem s.c.,
#    zwracamy czytelną informację zamiast fałszywego "nie znaleziono".
#
# Otwarte API KRS nie wymaga tokenu – proxy potrzebne wyłącznie z powodu CORS.
@app.route('/krs')
def krs():
    nip = request.args.get('nip', '').replace('-', '').replace(' ', '').strip()
    if not nip or len(nip) != 10 or not nip.isdigit():
        return jsonify({'success': False, 'error': 'Nieprawidłowy NIP (wymagane 10 cyfr)'}), 400

    headers = {
        'Accept': 'application/json',
        'User-Agent': 'CarrierCheck/1.0',
    }

    try:
        krs_number = None

        # ── Krok 1: próba wyszukania po NIP przez podmiotySzukaj ─────────────
        try:
            alt_resp = requests.get(
                'https://api-krs.ms.gov.pl/api/krs/podmiotySzukaj',
                params={'nip': nip, 'stronaWynikow': 0, 'iloscWynikow': 1},
                headers=headers,
                timeout=15
            )
            print(f"[KRS] podmiotySzukaj NIP={nip} status={alt_resp.status_code} body={alt_resp.text[:400]}")
            if alt_resp.ok:
                alt_data = alt_resp.json()
                items = (alt_data.get('odpisy')
                         or alt_data.get('wyniki')
                         or alt_data.get('podmioty')
                         or [])
                if isinstance(items, list) and items:
                    krs_number = items[0].get('numerKRS') or items[0].get('krs')
        except Exception as e:
            print(f"[KRS] podmiotySzukaj wyjątek: {e}")

        # ── Krok 2: fallback – pobierz numer KRS z Białej Listy VAT ──────────
        if not krs_number:
            try:
                bl_resp = requests.get(
                    f'https://wl-api.mf.gov.pl/api/search/nip/{nip}',
                    params={'date': datetime.date.today().isoformat()},
                    timeout=15
                )
                print(f"[KRS] BL status={bl_resp.status_code} body={bl_resp.text[:300]}")
                if bl_resp.ok:
                    subj = bl_resp.json().get('result', {}).get('subject', {})
                    krs_number = subj.get('krs') if subj else None
                    print(f"[KRS] BL krs_number={krs_number}")
            except Exception as e:
                print(f"[KRS] BL wyjątek: {e}")

        # ── Krok 3: sprawdź w CEIDG czy NIP należy do spółki cywilnej ────────
        # Spółka cywilna NIE ma wpisu w KRS — zwróć informację zamiast błędu.
        if not krs_number:
            ceidg_headers = {
                'Authorization': f'Bearer {CEIDG_TOKEN}',
                'Accept': 'application/json',
            }
            is_sc = False
            try:
                sc_resp = requests.get(
                    f'{CEIDG_BASE}/firmy',
                    params=[('nip_sc', nip), ('limit', 1)],
                    headers=ceidg_headers,
                    timeout=15
                )
                print(f"[KRS] CEIDG nip_sc check NIP={nip} status={sc_resp.status_code}")
                if sc_resp.ok:
                    sc_data = sc_resp.json()
                    if sc_data.get('firmy') or sc_data.get('count', 0) > 0:
                        is_sc = True
            except Exception as e:
                print(f"[KRS] CEIDG nip_sc check wyjątek: {e}")

            if is_sc:
                return jsonify({
                    'success': True,
                    'found': False,
                    'nip': nip,
                    'is_spolka_cywilna': True,
                    'info': (
                        'Podany NIP należy do spółki cywilnej (s.c.). '
                        'Spółki cywilne nie są rejestrowane w KRS — '
                        'są wpisywane do CEIDG przez każdego wspólnika osobno. '
                        'Dane spółki dostępne są w sekcji CEIDG.'
                    )
                })

            return jsonify({
                'success': True,
                'found': False,
                'nip': nip,
                'is_spolka_cywilna': False,
                'info': (
                    'Nie znaleziono numeru KRS dla podanego NIP. '
                    'Podmiot może być wpisany do CEIDG (jednoosobowa działalność gospodarcza) '
                    'lub jest to spółka cywilna nieposiadająca wpisu w KRS.'
                )
            })

        # ── Krok 4: pobierz odpis aktualny po numerze KRS ────────────────────
        krs_padded = str(krs_number).zfill(10)
        for rejestr in ['P', 'S']:
            try:
                odpis_resp = requests.get(
                    f'{KRS_BASE}/OdpisAktualny/{krs_padded}',
                    params={'rejestr': rejestr, 'format': 'json'},
                    headers=headers,
                    timeout=20
                )
                print(f"[KRS] OdpisAktualny krs={krs_padded} rejestr={rejestr} status={odpis_resp.status_code}")
                if odpis_resp.status_code == 404:
                    continue
                if not odpis_resp.ok:
                    continue
                return jsonify({
                    'success': True,
                    'found': True,
                    'nip': nip,
                    'krs': krs_padded,
                    'rejestr': rejestr,
                    'is_spolka_cywilna': False,
                    'odpis': odpis_resp.json()
                })
            except Exception as e:
                print(f"[KRS] OdpisAktualny wyjątek rejestr={rejestr}: {e}")

        # Fallback – KRS znaleziony, ale odpis niedostępny
        return jsonify({
            'success': True,
            'found': True,
            'nip': nip,
            'krs': krs_padded,
            'is_spolka_cywilna': False,
            'info': 'Numer KRS znaleziony, ale nie udało się pobrać odpisu z API.'
        })

    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': 'Przekroczono czas oczekiwania (API KRS niedostępne)'}), 504
    except requests.exceptions.HTTPError as e:
        return jsonify({'success': False, 'error': f'Błąd HTTP: {str(e)}'}), 502
    except Exception as e:
        print(f"[KRS] wyjątek globalny: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ── CRBR proxy ────────────────────────────────────────────────────────────────
# Centralny Rejestr Beneficjentów Rzeczywistych (Ministerstwo Finansów)
# Usługa publiczna SOAP 1.2 — nie wymaga tokenu ani rejestracji.
CRBR_URL = 'https://bramka-crbr.mf.gov.pl:5058/uslugiBiznesowe/uslugiESB/AP/ApiPrzegladoweCRBR/2022/12/01'
CRBR_NS  = 'http://www.mf.gov.pl/uslugiBiznesowe/uslugiESB/AP/ApiPrzegladoweCRBR/2022/12/01'
CRBR_NS1 = 'http://www.mf.gov.pl/schematy/AP/ApiPrzegladoweCRBR/2022/12/01'

def _crbr_soap_body(nip: str) -> str:
    """Buduje kopertę SOAP 1.2 z zapytaniem o NIP."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
               xmlns:ns="{CRBR_NS}"
               xmlns:ns1="{CRBR_NS1}">
  <soap:Header/>
  <soap:Body>
    <ns:PobierzInformacjeOSpolkachIBeneficjentach>
      <PobierzInformacjeOSpolkachIBeneficjentachDane>
        <ns1:SzczegolyWniosku>
          <ns1:NIP>{nip}</ns1:NIP>
        </ns1:SzczegolyWniosku>
      </PobierzInformacjeOSpolkachIBeneficjentachDane>
    </ns:PobierzInformacjeOSpolkachIBeneficjentach>
  </soap:Body>
</soap:Envelope>"""

def _xml_text(el, tag):
    """Bezpieczne pobranie tekstu z elementu XML — obsługuje wielokrotne namespace-prefixy."""
    import xml.etree.ElementTree as ET
    for child in el.iter():
        local = child.tag.split('}')[-1] if '}' in child.tag else child.tag
        if local == tag:
            return (child.text or '').strip()
    return ''

def _xml_findall(root, tag):
    """Zwraca listę elementów o danej lokalnej nazwie tagu."""
    result = []
    for child in root.iter():
        local = child.tag.split('}')[-1] if '}' in child.tag else child.tag
        if local == tag:
            result.append(child)
    return result

def _parse_beneficjent(b_el):
    """Parsuje element BeneficjentRzeczywisty do słownika."""
    def t(tag): return _xml_text(b_el, tag)

    nazwa_grupowego = t('NazwaBeneficjentaGrupowego')
    if nazwa_grupowego:
        return {
            'typ': 'grupowy',
            'nazwa': nazwa_grupowego,
            'uprawnienia': t('InformacjeOUprawnieniachPrzyslugujacychBeneficjentowiGrupowemuTrust'),
        }

    benef = {
        'typ': 'fizyczny',
        'imie': t('PierwszeImie'),
        'kolejneImiona': t('KolejneImiona'),
        'nazwisko': t('Nazwisko'),
        'pesel': t('PESEL'),
        'dataUrodzenia': t('DataUrodzenia'),
        'obywatelstwo': t('Obywatelstwo'),
        'krajZamieszkania': t('KrajZamieszkania'),
        'udzialy': [],
    }

    for udz_el in _xml_findall(b_el, 'InformacjaOUdzialach'):
        udz = {}
        bezp_el = _xml_findall(udz_el, 'UprawnieniaWlascicielskieBezposrednie')
        if bezp_el:
            udz['rodzajBezposredni'] = _xml_text(bezp_el[0], 'RodzajUprawnienWlascicielskich')
            udz['kodBezposredni']    = _xml_text(bezp_el[0], 'KodUprawnienWlascicielskich')
            udz['jednostkaMiary']    = _xml_text(bezp_el[0], 'JednostkaMiary')
            udz['ilosc']             = _xml_text(bezp_el[0], 'Ilosc')
        posred = _xml_text(udz_el, 'UprawnieniaWlascicielskiePosrednie')
        if posred:
            udz['posrednie'] = posred
        inne_el = _xml_findall(udz_el, 'InneUprawnienia')
        if inne_el:
            udz['inne'] = _xml_text(inne_el[0], 'RodzajInnychUprawnien') or _xml_text(inne_el[0], 'OpisInnychUprawnien')
        benef['udzialy'].append(udz)

    return benef

def _parse_reprezentant(r_el):
    def t(tag): return _xml_text(r_el, tag)
    return {
        'imie': t('PierwszeImie'),
        'kolejneImiona': t('KolejneImiona'),
        'nazwisko': t('Nazwisko'),
        'pesel': t('PESEL'),
        'obywatelstwo': t('Obywatelstwo'),
        'rodzajReprezentacji': t('RodzajReprezentacji'),
    }

@app.route('/crbr')
def crbr():
    import xml.etree.ElementTree as ET

    nip = request.args.get('nip', '').replace('-', '').replace(' ', '').strip()
    if not nip or len(nip) != 10 or not nip.isdigit():
        return jsonify({'success': False, 'error': 'Nieprawidłowy NIP (wymagane 10 cyfr)'}), 400

    soap_body = _crbr_soap_body(nip)
    headers = {
        'Content-Type': 'application/soap+xml; charset=utf-8',
        'SOAPAction': f'{CRBR_NS}/PobierzInformacjeOSpolkachIBeneficjentach',
    }

    try:
        resp = requests.post(CRBR_URL, data=soap_body.encode('utf-8'), headers=headers, timeout=30)
        print(f"[CRBR] NIP={nip} status={resp.status_code} body={resp.text[:500]}")

        if not resp.ok:
            return jsonify({'success': False, 'error': f'HTTP {resp.status_code} z bramki CRBR'}), 502

        root = ET.fromstring(resp.content)

        status = _xml_text(root, 'Status')
        if status == 'BrakInformacji':
            return jsonify({'success': True, 'found': False, 'nip': nip, 'status': status})
        if status == 'BladFormalny':
            return jsonify({'success': False, 'error': 'BladFormalny — niepoprawna konstrukcja zapytania', 'nip': nip}), 400

        spolki = []
        for spolka_el in _xml_findall(root, 'SpolkaIBeneficjenci'):
            def t(tag): return _xml_text(spolka_el, tag)
            spolka = {
                'nazwa':                  t('Nazwa'),
                'nip':                    t('NIP'),
                'krs':                    t('KRS'),
                'kodFormyOrganizacyjnej': t('KodFormyOrganizacyjnej'),
                'opisFormyOrganizacyjnej':t('OpisFormyOrganizacyjnej'),
                'kodPocztowy':            t('KodPocztowy'),
                'miejscowosc':            t('Miejscowosc'),
                'ulica':                  t('Ulica'),
                'nrDomu':                 t('NrDomu'),
                'nrLokalu':               t('NrLokalu'),
                'kraj':                   t('Kraj'),
                'dataPoczatku':           t('DataPoczatkuPrezentacjiZgloszenia'),
                'dataKonca':              t('DataKoncaPrezentacjiZgloszenia'),
                'skorygowane':            t('Skorygowane'),
                'numerReferencyjny':      t('NumerReferencyjny'),
                'beneficjenci': [],
                'reprezentanci': [],
                'rozbieznosci': [],
            }

            for b_el in _xml_findall(spolka_el, 'BeneficjentRzeczywisty'):
                spolka['beneficjenci'].append(_parse_beneficjent(b_el))

            for r_el in _xml_findall(spolka_el, 'Reprezentant'):
                spolka['reprezentanci'].append(_parse_reprezentant(r_el))

            for rb_el in _xml_findall(spolka_el, 'InformacjaORozbieznosciach'):
                spolka['rozbieznosci'].append(_xml_text(rb_el, 'InformacjaDlaZainteresowanego'))

            spolki.append(spolka)

        return jsonify({
            'success': True,
            'found': len(spolki) > 0,
            'nip': nip,
            'status': status or 'IstniejaInformacje',
            'spolki': spolki,
        })

    except ET.ParseError as e:
        print(f"[CRBR] XML parse error: {e} — body: {resp.text[:400]}")
        return jsonify({'success': False, 'error': f'Błąd parsowania XML: {str(e)}'}), 500
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': 'Przekroczono czas oczekiwania (bramka CRBR niedostępna)'}), 504
    except requests.exceptions.ConnectionError as e:
        return jsonify({'success': False, 'error': f'Błąd połączenia z bramką CRBR: {str(e)}'}), 502
    except Exception as e:
        print(f"[CRBR] wyjątek globalny: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True)
