#!/usr/bin/env python3
"""
Eskondidinho Events Updater
Scrapes PagTickets public page to generate events.json for the site.
Also auto-updates index.html, reservas.html, mapa.html, and script.js.

Usage:
    python update_events.py              # Just update events.json + HTML files
    python update_events.py --deploy     # Update + deploy to Netlify
"""

import requests
from bs4 import BeautifulSoup
import json
import re
import sys
import os
import html as html_module
import base64
import urllib.request
import urllib.error
from datetime import datetime

PAGTICKETS_URL = "https://eskondidinhoeventos.pagtickets.com.br/"
OUTPUT_FILE = "events.json"

# Push automático pro repo de dados (de onde o JS do site lê em runtime)
GITHUB_TOKEN  = os.environ.get('GITHUB_TOKEN', '')
GITHUB_REPO   = os.environ.get('GITHUB_REPO',   'krum-lab/eskondidinho-data')
GITHUB_FILE   = os.environ.get('GITHUB_FILE',   'events.json')
GITHUB_BRANCH = os.environ.get('GITHUB_BRANCH', 'main')


def push_events_to_github(events_data):
    """Faz push do events.json pro repo de dados via API GitHub.
    Sem isso, o site (que lê via raw.githubusercontent) fica stale mesmo
    com o Netlify atualizado."""
    if not GITHUB_TOKEN:
        print("  ⚠️ GITHUB_TOKEN não setado — site lê do GitHub e não vai atualizar! "
              "Configure a env var GITHUB_TOKEN.")
        return False
    try:
        api_url = f'https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}'
        headers = {
            'Authorization': f'token {GITHUB_TOKEN}',
            'Content-Type': 'application/json',
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'eskondidinho-events-updater'
        }
        # Pega SHA atual (necessário p/ update)
        req = urllib.request.Request(api_url, headers=headers)
        sha = ''
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                sha = json.loads(resp.read()).get('sha', '')
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise

        content_str = json.dumps(events_data, indent=2, ensure_ascii=False)
        content_b64 = base64.b64encode(content_str.encode('utf-8')).decode('utf-8')
        payload = {
            'message': f'events: update {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
            'content': content_b64,
            'branch': GITHUB_BRANCH,
        }
        if sha:
            payload['sha'] = sha
        body = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(api_url, data=body, headers=headers, method='PUT')
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        print('  🚀 GitHub atualizado! events.json sincronizado no repo de dados.')
        return True
    except Exception as e:
        print(f'  ⚠️ Falha ao fazer push pro GitHub: {e}')
        return False

# Month name mappings (Portuguese)
MONTH_MAP = {
    'jan': ('01', 'Janeiro'), 'fev': ('02', 'Fevereiro'), 'mar': ('03', 'Março'),
    'abr': ('04', 'Abril'), 'mai': ('05', 'Maio'), 'jun': ('06', 'Junho'),
    'jul': ('07', 'Julho'), 'ago': ('08', 'Agosto'), 'set': ('09', 'Setembro'),
    'out': ('10', 'Outubro'), 'nov': ('11', 'Novembro'), 'dez': ('12', 'Dezembro')
}

# Month abbreviation for display (e.g., "MAR")
MONTH_ABBR = {
    'Janeiro': 'JAN', 'Fevereiro': 'FEV', 'Março': 'MAR',
    'Abril': 'ABR', 'Maio': 'MAI', 'Junho': 'JUN',
    'Julho': 'JUL', 'Agosto': 'AGO', 'Setembro': 'SET',
    'Outubro': 'OUT', 'Novembro': 'NOV', 'Dezembro': 'DEZ'
}

WEEKDAY_MAP = {
    'seg': ('SEGUNDA', 'Segunda-feira'), 'ter': ('TERÇA', 'Terça-feira'),
    'qua': ('QUARTA', 'Quarta-feira'), 'qui': ('QUINTA', 'Quinta-feira'),
    'sex': ('SEXTA', 'Sexta-feira'), 'sab': ('SÁBADO', 'Sábado'),
    'sáb': ('SÁBADO', 'Sábado'), 'dom': ('DOMINGO', 'Domingo')
}


def fetch_pagtickets():
    """Fetch and parse PagTickets main page."""
    print(f"🔍 Fetching {PAGTICKETS_URL}...")
    resp = requests.get(PAGTICKETS_URL, timeout=15)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, 'html.parser')


def extract_events(soup):
    """Extract event data from PagTickets HTML."""
    raw_events = []

    # Find all event links - they are in anchor tags that point to event pages
    # PagTickets uses cards with links to each event
    for link in soup.find_all('a', href=True):
        href = link['href']

        # Extract event ID from URL (format: ...event-name__ID/)
        id_match = re.search(r'__(\d+)/?$', href)
        if not id_match:
            continue

        event_id = id_match.group(1)
        
        # Ensure URLs are absolute for the final JSON
        if href.startswith('/'):
            href = 'https://eskondidinhoeventos.pagtickets.com.br' + href

        # Find event title - look for the heading within the link
        title_el = link.find(['h4', 'h3', 'h2', 'strong', 'span'])
        title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)

        if not title or len(title) < 3:
            continue

        # Find date/time info near the link
        # PagTickets shows: "Sex, 06/Mar às 22h" in a sibling element
        parent = link.find_parent('div', class_='card-body')
        date_text = ''
        if parent:
            full_text = parent.get_text(' ', strip=True)
            date_match = re.search(
                r'(seg|ter|qua|qui|sex|s[aá]b|dom),?\s*(\d{1,2})/(\w{3})\s*[àa]s?\s*(\d{1,2})h',
                full_text, re.IGNORECASE
            )
            if date_match:
                date_text = date_match.group(0)

        raw_events.append({
            'id': event_id,
            'title': title,
            'href': href,
            'date_text': date_text,
            'is_reserva': title.upper().startswith('RESERVA')
        })

    return raw_events


def parse_date(date_text):
    """Parse 'Sex, 06/Mar às 22h' into structured data."""
    match = re.search(
        r'(seg|ter|qua|qui|sex|s[aá]b|dom),?\s*(\d{1,2})/(\w{3})\s*[àa]s?\s*(\d{1,2})h',
        date_text, re.IGNORECASE
    )
    if not match:
        return None

    weekday_abbr = match.group(1).lower()
    day = match.group(2).zfill(2)
    month_abbr = match.group(3).lower()
    hour = match.group(4).zfill(2)

    month_num, month_name = MONTH_MAP.get(month_abbr, ('01', 'Janeiro'))
    weekday_short, weekday_full = WEEKDAY_MAP.get(weekday_abbr, ('', ''))

    # Determine year (assume current or next year)
    now = datetime.now()
    year = now.year
    event_date = datetime(year, int(month_num), int(day))
    # If the event is more than 2 months in the past, assume next year
    if (now - event_date).days > 60:
        year += 1

    return {
        'date': f"{year}-{month_num}-{day}",
        'day': day,
        'weekday': weekday_short,
        'weekday_full': weekday_full,
        'time': f"{hour}:00",
        'month_name': month_name
    }


def title_case_pt(text):
    """Convert text to title case with Portuguese small words."""
    if not text:
        return text
    small_words = {'de', 'do', 'da', 'dos', 'das', 'e', 'em', 'no', 'na', 'o', 'a', 'os', 'as'}
    words = text.split()
    return ' '.join(
        w.capitalize() if i == 0 or w.lower() not in small_words else w.lower()
        for i, w in enumerate(words)
    )


def parse_title(title):
    """Parse event title to extract name, bands, and promo."""
    # Remove "RESERVAS - DD/MM - " prefix if present
    title = re.sub(r'^RESERVAS?\s*[-–]\s*\d{2}/\d{2}\s*[-–]\s*', '', title, flags=re.IGNORECASE)

    # Extract promo — handles both closed "(ELAS FREE ATÉ 23H)" and truncated "(ELAS FREE ATÉ…"
    promo = ''
    # Try closed parentheses first
    promo_match = re.search(r'\(\s*([^)]*(?:FREE|ELAS|UNISSEX|GRÁTIS|PROMO)[^)]*)\s*\)', title, re.IGNORECASE)
    if promo_match:
        promo = promo_match.group(1).strip()
        promo = re.sub(r'\s+', ' ', promo)
        title = title[:promo_match.start()].strip()
    else:
        # Try truncated promo with ellipsis: "( ELAS FREE ATÉ…" or "( FREE UNISSEX…"
        promo_trunc = re.search(r'\(\s*([^)]*(?:FREE|ELAS|UNISSEX|GRÁTIS|PROMO)[^)]*?)[…\.]{1,3}$', title, re.IGNORECASE)
        if promo_trunc:
            promo = promo_trunc.group(1).strip()
            promo = re.sub(r'\s+', ' ', promo)
            title = title[:promo_trunc.start()].strip()
    
    # Also check if promo text is embedded in the last band after a " (" 
    # (PagTickets sometimes puts promo as part of band name)
    
    # Detect promo from URL pattern if not found (e.g., url contains "elas-free-ate-23h")
    # This is handled later in the update_html_files function using link_ingresso

    # Split into name and bands using the following priority:
    # 1. Dash:  "EVENT NAME - Band1 / Band2"  → name = before dash, bands = after
    # 2. "com":  "EVENT NAME com Band1 / Band2" → name = before "com", bands = after
    #            (PagTickets uses "com" to mean "featuring", same as a dash separator)
    # 3. Slash: "EVENT NAME / Band1 / Band2"  → name = first segment, bands = rest
    dash_parts = re.split(r'\s*[-–]\s*', title, maxsplit=1)

    if len(dash_parts) == 2 and len(dash_parts[0]) > 2:
        # Dash separator: "EVENT NAME - Band1 / Band2"
        name = dash_parts[0].strip()
        bands_raw = dash_parts[1].strip()
    else:
        # "com" separator: "EVENT NAME com Band1 / Band2"
        # Only match " com " surrounded by spaces (not part of a word like "economia")
        com_match = re.search(r'\s+com\s+', title, re.IGNORECASE)
        if com_match:
            name = title[:com_match.start()].strip()
            bands_raw = title[com_match.end():].strip()
        else:
            # Slash-only: "EVENT NAME / Band1 / Band2"
            slash_parts = [p.strip() for p in title.split('/') if p.strip()]
            if len(slash_parts) >= 2:
                first_segment = slash_parts[0].strip()

                # Check for double-space separator within the first segment
                # PagTickets sometimes formats: "SABADÃO TOP  Grupo Evidência / Moretti Show"
                # where double space separates the event name from the first band
                dbl_space = re.split(r'\s{2,}', first_segment)
                if len(dbl_space) >= 2:
                    name = dbl_space[0].strip()
                    # The rest of the double-space split + remaining slash parts = all bands
                    extra_bands = [s.strip() for s in dbl_space[1:] if s.strip()]
                    bands_raw = ' / '.join(extra_bands + slash_parts[1:])
                else:
                    # First slash-part = event name AND first attraction.
                    # Per convention: "GDO / Garotos de Ouro / Ricardo & Juninho"
                    # → name = "GDO", bands = ["GDO", "Garotos de Ouro", "Ricardo & Juninho"]
                    # (dash or "com" are used when the name is NOT itself a band)
                    name = first_segment
                    bands_raw = ' / '.join(slash_parts)  # all segments are bands
            else:
                # Single token — no separators found. Use as both name AND band
                name = title.strip()
                bands_raw = title.strip()

    # Split bands by / + •
    bands = [b.strip() for b in re.split(r'\s*[/•+]\s*', bands_raw) if b.strip()]
    # Fallback: se ficou tudo numa banda só e ainda tem " - " no meio,
    # divide também por dash com espaços (caso de "Banda A - Banda B - Banda C")
    if len(bands) == 1 and re.search(r'\s[-–]\s', bands[0]):
        bands = [b.strip() for b in re.split(r'\s+[-–]\s+', bands[0]) if b.strip()]
    
    # Clean promo suffixes from individual band names
    cleaned_bands = []
    # Pattern de banda que é promo "puro" (não tem nome de banda real, só promo)
    # Ex.: "Elas Free …", "Free Unissex Até 23h", "ELAS FREE", "FREE …"
    promo_only = re.compile(r'^\s*(elas\s+free|free\s+unissex|free|elas|unissex|gr[áa]tis|promo)\b[\s\w…\.]*$', re.IGNORECASE)
    for b in bands:
        # Remove trailing "(ELAS..." or "(FREE..." from band names
        b_clean = re.sub(r'\s*\(\s*(ELAS|FREE|UNISSEX).*$', '', b, flags=re.IGNORECASE).strip()
        # Skip se a banda inteira é promo (não captura como promo — deixa
        # o URL fallback em match_events() resolver o texto completo)
        if promo_only.match(b_clean):
            continue
        if b_clean:
            cleaned_bands.append(b_clean)
            # If we removed a promo part and don't have one yet, try to extract it
            if b_clean != b and not promo:
                promo_part = re.search(r'\(\s*(.+?)(?:[…\.]*)$', b, re.IGNORECASE)
                if promo_part:
                    promo = promo_part.group(1).strip()
    bands = cleaned_bands if cleaned_bands else bands

    # Clean up name — title case
    name = name.strip().rstrip(' -/')
    if name.isupper() and len(name) > 3:
        name = title_case_pt(name)

    # Title case bands for cleaner display
    display_bands = []
    for b in bands:
        if b.isupper() and len(b) > 3:
            display_bands.append(title_case_pt(b))
        else:
            display_bands.append(b)
    bands = display_bands

    bands_text = ' • '.join(bands)

    # Try to extract promo from URL if still missing
    # This is a fallback — actual extraction happens at a higher level

    return name, bands, bands_text, promo


def match_events(raw_events):
    """Match ingresso events with reserva events and build final list."""
    ingressos = [e for e in raw_events if not e['is_reserva']]
    reservas = {e['id']: e for e in raw_events if e['is_reserva']}

    # Deduplicate ingressos by ID (same event may appear multiple times)
    seen_ids = set()
    unique_ingressos = []
    for e in ingressos:
        if e['id'] not in seen_ids:
            seen_ids.add(e['id'])
            unique_ingressos.append(e)

    events = []
    month_name = None  # definido a partir do PRIMEIRO evento upcoming (após sort)

    for ing in unique_ingressos:
        # Parse date
        date_info = parse_date(ing['date_text'])
        if not date_info:
            print(f"  ⚠️ Could not parse date for: {ing['title']}")
            continue

        # Parse title
        name, bands, bands_text, promo = parse_title(ing['title'])

        # ── Fallback: extract promo from the URL if not found in title ──
        # PagTickets often truncates promo text in the card title but keeps
        # the full promo in the URL slug (e.g. "elas-free-ate-23h").
        if not promo:
            url_lower = ing['href'].lower()
            if 'elas-free-ate' in url_lower:
                promo = 'ELAS FREE ATÉ 23H'
            elif 'free-unissex-ate' in url_lower or 'free-unissex-as' in url_lower:
                promo = 'FREE UNISSEX ATÉ AS 23H'
            elif 'free-unissex' in url_lower:
                promo = 'FREE UNISSEX ATÉ 23H'

        # Find matching reserva (try ID+1, which is the common pattern)
        reserva_id = str(int(ing['id']) + 1)
        reserva = reservas.get(reserva_id)

        event = {
            'id_ingresso': ing['id'],
            'id_reserva': reserva_id if reserva else '',
            'date': date_info['date'],
            'day': date_info['day'],
            'weekday': date_info['weekday'],
            'weekday_full': date_info['weekday_full'],
            'time': date_info['time'],
            'name': name,
            'bands': bands,
            'bands_text': bands_text,
            'promo': promo,
            'link_ingresso': ing['href'],
            'link_reserva': reserva['href'] if reserva else ''
        }

        events.append(event)
        print(f"  ✅ {date_info['date']} — {name} ({len(bands)} banda(s))")

    # Sort by date
    events.sort(key=lambda e: e['date'])

    # month_name = mês do PRIMEIRO evento upcoming (o mais cedo, após sort).
    # Intuição: "Agenda de Maio" enquanto o próximo show é em maio, mesmo que
    # haja eventos de junho no mesmo carrossel. Fallback pro mês corrente em BRT.
    if events:
        month_num_first = events[0]['date'].split('-')[1]  # YYYY-MM-DD → MM
        for _abbr, (_num, _name) in MONTH_MAP.items():
            if _num == month_num_first:
                month_name = _name
                break
    if not month_name:
        month_name = list(MONTH_MAP.values())[datetime.now().month - 1][1]

    return events, month_name


def save_events(events, month_name):
    """Save events to JSON file."""
    data = {
        'month': month_name,
        'updated_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'events': events
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    print(f"\n💾 Saved {len(events)} events to {OUTPUT_FILE}")
    return data


# ============================
# HTML AUTO-UPDATE FUNCTIONS
# ============================

def clean_band_name(band):
    """Remove promo suffixes from band names like '( ELAS F…' or '( ELA…'."""
    band = re.sub(r'\s*\(\s*(ELAS|ELA|FREE|UNISSEX).*$', '', band, flags=re.IGNORECASE)
    band = band.strip().rstrip('(').strip()
    return band


def format_promo_display(promo):
    """Format promo text for display in event cards."""
    if not promo:
        return ''
    # Normalize common patterns
    p = promo.strip()
    # Remove trailing ellipsis
    p = p.rstrip('…').rstrip('.').strip()
    
    # Normalize known truncated patterns
    # "ELAS F" → "ELAS FREE ATÉ 23H"
    if re.match(r'^ELAS\s+F$', p, re.IGNORECASE):
        p = 'ELAS FREE ATÉ 23H'
    # "ELAS FREE ATÉ" → "ELAS FREE ATÉ 23H"
    elif re.match(r'^ELAS\s+FREE\s+AT[ÉE]$', p, re.IGNORECASE):
        p = 'ELAS FREE ATÉ 23H'
    # "FREE UNISSEX ATÉ AS" → "FREE UNISSEX ATÉ AS 23H"
    elif re.match(r'^FREE\s+UNISSEX\s+AT[ÉE]\s+AS$', p, re.IGNORECASE):
        p = 'FREE UNISSEX ATÉ AS 23H'
    # "FREE UNISSEX ATÉ" → "FREE UNISSEX ATÉ AS 23H"
    elif re.match(r'^FREE\s+UNISSEX\s+AT[ÉE]$', p, re.IGNORECASE):
        p = 'FREE UNISSEX ATÉ AS 23H'
    
    # Make it more readable
    p = re.sub(r'(FREE)\s+(UNISSEX)', r'\1 \2', p, flags=re.IGNORECASE)
    p = re.sub(r'(ELAS)\s+(FREE)', r'\1 \2', p, flags=re.IGNORECASE)
    # Title case but keep FREE/UNISSEX uppercase
    words = p.split()
    result = []
    for w in words:
        upper = w.upper()
        if upper in ('FREE', 'UNISSEX', 'ATÉ', 'ATE', 'AS', 'ÀS'):
            result.append(w.lower() if upper in ('ATÉ', 'ATE', 'AS', 'ÀS') else upper)
        else:
            result.append(w.capitalize())
    return ' '.join(result)


def generate_index_events_html(events, month_name):
    """Generate the event cards HTML for index.html."""
    cards = []
    # Map de número-do-mês → abreviação (e.g. '06' → 'JUN'), pra cada card usar
    # a SUA própria abreviação (e não a do primeiro evento do agenda).
    num_to_abbr = {}
    for _abbr, (_num, _name) in MONTH_MAP.items():
        num_to_abbr[_num] = MONTH_ABBR.get(_name, _name[:3].upper())

    for i, ev in enumerate(events):
        ev_month_num = ev['date'].split('-')[1]
        month_abbr = num_to_abbr.get(ev_month_num, MONTH_ABBR.get(month_name, 'MAR'))
        bands_html = '\n'.join(
            f'                        <li>{html_module.escape(clean_band_name(b))}</li>'
            for b in ev['bands']
        )
        
        promo_line = ''
        promo = format_promo_display(ev.get('promo', ''))
        if promo:
            promo_line = f'\n                    <span class="event-card-promo">🎉 {html_module.escape(promo)}</span>'
        
        card = f"""                <!-- Event {i+1} -->
                <div class="event-card reveal" data-date="{ev['date']}">
                    <div class="event-card-date">
                        <div class="event-card-day">
                            <span class="day-number">{ev['day']}</span>
                            <span class="day-month">{month_abbr}</span>
                        </div>
                        <span class="event-card-weekday">{ev['weekday_full']}</span>
                    </div>
                    <h3 class="event-card-name">{html_module.escape(ev['name'])}</h3>
                    <ul class="event-card-bands">
{bands_html}
                    </ul>
                    <p class="event-card-time">
                        <i class="far fa-clock"></i> {ev['time'].replace(':00', 'h')} às 04h
                    </p>{promo_line}
                    <a href="{html_module.escape(ev['link_ingresso'])}"
                        target="_blank" class="event-card-btn">Comprar Ingresso</a>
                </div>"""
        cards.append(card)
    
    return '\n\n'.join(cards)


def generate_reservas_html(events):
    """Generate the checkout cards HTML for reservas.html."""
    cards = []
    num_to_abbr = {}
    for _abbr, (_num, _name) in MONTH_MAP.items():
        num_to_abbr[_num] = MONTH_ABBR.get(_name, _name[:3].upper())

    for ev in events:
        ev_month_abbr = num_to_abbr.get(ev['date'].split('-')[1], 'MAR')
        bands_display = ' &amp; '.join(clean_band_name(b) for b in ev['bands'])
        # Use bullet separator like the original
        bands_display = ' • '.join(html_module.escape(clean_band_name(b)) for b in ev['bands'])

        card = f"""                <a href="mapa.html?id={ev['id_reserva']}" class="checkout-card" data-date="{ev['date']}">
                    <div class="checkout-date"><span class="checkout-day">{ev['day']}</span><span
                            class="checkout-month">{ev_month_abbr}</span><span class="checkout-weekday">{ev['weekday']}</span></div>
                    <div class="checkout-info">
                        <h3>{html_module.escape(ev['name'])}</h3>
                        <p>{bands_display}</p>
                    </div>
                    <div class="checkout-action"><span class="checkout-btn">Reservar Mesa →</span></div>
                </a>"""
        cards.append(card)
    
    return '\n'.join(cards)


def generate_mapa_events_js(events):
    """Generate the EVENTS JavaScript config for mapa.html."""
    entries = []
    
    for ev in events:
        bands_display = ' • '.join(clean_band_name(b) for b in ev['bands'])
        # Escape single quotes for JS strings
        name_js = ev['name'].replace("'", "\\'")
        bands_js = bands_display.replace("'", "\\'")
        url_js = ev['link_reserva'].replace("'", "\\'")
        
        entry = f"""            '{ev['id_reserva']}': {{
                date: '{ev['date']}',
                day: '{ev['day']}',
                weekday: '{ev['weekday']}',
                name: '{name_js}',
                bands: '{bands_js}',
                url: '{url_js}'
            }}"""
        entries.append(entry)
    
    return '        const EVENTS = {\n' + ',\n'.join(entries) + '\n        };'


def generate_script_events_js(events):
    """Generate the events array for script.js."""
    entries = []
    
    for ev in events:
        bands_list = ', '.join(
            f"'{clean_band_name(b).replace(chr(39), chr(92)+chr(39))}'"
            for b in ev['bands']
        )
        name_js = ev['name'].replace("'", "\\'")
        promo = format_promo_display(ev.get('promo', ''))
        promo_js = promo.replace("'", "\\'")
        link_js = ev['link_ingresso'].replace("'", "\\'")
        
        entry = f"""    {{
        date: '{ev['date']}',
        time: '{ev['time']}',
        name: '{name_js}',
        bands: [{bands_list}],
        promo: '{promo_js}',
        link: '{link_js}'
    }}"""
        entries.append(entry)
    
    return 'const events = [\n' + ',\n'.join(entries) + '\n];'


def update_html_files(events, month_name):
    """Update all HTML and JS files with new event data."""
    print("\n📝 Updating HTML/JS files with new event data...")
    
    # 1. Update index.html
    try:
        with open('index.html', 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Replace event cards section (between eventsGrid div and its closing)
        new_cards = generate_index_events_html(events, month_name)
        # Match from first <!-- Event to last </div> before closing </div> of events-grid
        pattern = r'(<!-- Event 1 -->.*?)(\n\n            </div>\s*\n        </div>\s*\n    </section>)'
        replacement = new_cards + r'\2'
        new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
        
        # Also update the month title
        new_content = re.sub(
            r'Agenda de <span>\w+</span>',
            f'Agenda de <span>{month_name}</span>',
            new_content
        )
        
        if new_content != content:
            with open('index.html', 'w', encoding='utf-8') as f:
                f.write(new_content)
            print("  ✅ index.html updated")
        else:
            print("  ℹ️ index.html — no changes needed")
    except Exception as e:
        print(f"  ❌ index.html update failed: {e}")
    
    # 2. Update reservas.html
    try:
        with open('reservas.html', 'r', encoding='utf-8') as f:
            content = f.read()
        
        new_cards = generate_reservas_html(events)
        # Match the checkout-grid content
        pattern = r'(<div class="checkout-grid">\s*\n).*?(\n            </div>\s*\n\s*<div class="checkout-help">)'
        replacement = r'\1' + new_cards + r'\2'
        new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
        
        if new_content != content:
            with open('reservas.html', 'w', encoding='utf-8') as f:
                f.write(new_content)
            print("  ✅ reservas.html updated")
        else:
            print("  ℹ️ reservas.html — no changes needed")
    except Exception as e:
        print(f"  ❌ reservas.html update failed: {e}")
    
    # 3. Update mapa.html
    try:
        with open('mapa.html', 'r', encoding='utf-8') as f:
            content = f.read()
        
        new_events_js = generate_mapa_events_js(events)
        # Match the EVENTS config block
        pattern = r'        const EVENTS = \{.*?\};'
        new_content = re.sub(pattern, new_events_js, content, flags=re.DOTALL)
        
        if new_content != content:
            with open('mapa.html', 'w', encoding='utf-8') as f:
                f.write(new_content)
            print("  ✅ mapa.html updated")
        else:
            print("  ℹ️ mapa.html — no changes needed")
    except Exception as e:
        print(f"  ❌ mapa.html update failed: {e}")
    
    # 4. Update script.js
    try:
        with open('script.js', 'r', encoding='utf-8') as f:
            content = f.read()
        
        new_events_array = generate_script_events_js(events)
        # Match the events array
        pattern = r'const events = \[.*?\];'
        new_content = re.sub(pattern, new_events_array, content, flags=re.DOTALL)
        
        if new_content != content:
            with open('script.js', 'w', encoding='utf-8') as f:
                f.write(new_content)
            print("  ✅ script.js updated")
        else:
            print("  ℹ️ script.js — no changes needed")
    except Exception as e:
        print(f"  ❌ script.js update failed: {e}")


import time
import os
import shutil
import subprocess

def deploy_to_netlify():
    """Deploy to netlify using a temporary directory to avoid playwright locks."""
    print("\n🚀 Deploying to Netlify...")
    temp_dir = os.path.join(os.environ.get('TEMP', '/tmp'), 'eskondidinho-deploy')
    
    # Clean up old temp dir
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)
    
    current_dir = os.getcwd()
    
    try:
        # Copy necessary files
        for ext in ['*.html', '*.css', '*.js', '*.ico', 'events.json', 'occupied-tables.json', 'netlify.toml']:
            # Using PowerShell to copy files matching the pattern
            subprocess.run(['powershell', '-Command', f'Copy-Item "{current_dir}\\{ext}" "{temp_dir}"'], capture_output=True)
        
        # Copy directories
        for d in ['assets', 'img', 'gallery']:
            dir_path = os.path.join(current_dir, d)
            if os.path.exists(dir_path):
                subprocess.run(['powershell', '-Command', f'Copy-Item "{dir_path}" "{temp_dir}" -Recurse'], capture_output=True)
        if os.path.exists(os.path.join(current_dir, '.netlify')):
            subprocess.run(['powershell', '-Command', f'Copy-Item "{current_dir}\\.netlify" "{temp_dir}" -Recurse'], capture_output=True)
            
        print(f"📦 Files prepared at {temp_dir}")
        
        npx_cmd = 'npx.cmd' if os.name == 'nt' else 'npx'
        result = subprocess.run(
            [npx_cmd, '-y', 'netlify-cli', 'deploy', '--prod', f'--dir={temp_dir}', '--message', 'Auto-update events from PagTickets'],
            capture_output=True, text=True, encoding='utf-8', errors='ignore'
        )
        if result.returncode == 0:
            print("✅ Deploy successful!")
        else:
            print(f"❌ Deploy failed: {result.stderr}")
    except Exception as e:
        print(f"❌ Deploy process failed: {e}")

def run_update_cycle(last_events_hash=None, last_occupied_hash=None):
    """Run a single update cycle. Returns (events_hash, occupied_hash)."""
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting update cycle...")
    try:
        soup = fetch_pagtickets()
        raw_events = extract_events(soup)
        print(f"📋 Found {len(raw_events)} raw event entries")

        events, month_name = match_events(raw_events)

        if not events:
            print("❌ No events found! Check if PagTickets page structure changed.")
            return last_events_hash, last_occupied_hash

        # Check for event changes
        current_events_hash = hash(json.dumps(events, sort_keys=True))
        events_changed = last_events_hash is None or current_events_hash != last_events_hash
        
        # Check for occupied-tables.json changes
        occupied_changed = False
        current_occupied_hash = last_occupied_hash
        occupied_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'occupied-tables.json')
        if os.path.exists(occupied_path):
            try:
                with open(occupied_path, 'r', encoding='utf-8') as f:
                    occupied_data = f.read()
                current_occupied_hash = hash(occupied_data)
                if last_occupied_hash is not None and current_occupied_hash != last_occupied_hash:
                    occupied_changed = True
                    print("🪑 Mudanças detectadas em occupied-tables.json!")
                elif last_occupied_hash is None:
                    occupied_changed = True  # First run
            except Exception as e:
                print(f"⚠️ Erro ao ler occupied-tables.json: {e}")

        if events_changed:
            print("💡 Found changes or first run. Updating events.json...")
            save_events(events, month_name)
            update_html_files(events, month_name)
            # 🚀 Push pro repo de dados — site lê dali em runtime via JS.
            # Sem isso, Netlify atualiza mas o site continua mostrando o antigo.
            try:
                with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
                    events_payload = json.load(f)
                push_events_to_github(events_payload)
            except Exception as e:
                print(f"  ⚠️ Falha ao ler events.json p/ push GitHub: {e}")

        if events_changed or occupied_changed:
            if not events_changed and occupied_changed:
                print("🪑 Mesas ocupadas atualizadas. Fazendo deploy...")
            if '--deploy' in sys.argv:
                deploy_to_netlify()
        else:
            print("✅ No changes detected in events or tables. Skipping deploy.")

        return current_events_hash, current_occupied_hash
    except requests.RequestException as e:
        print(f"❌ Network error: {e}")
        return last_events_hash, last_occupied_hash
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return last_events_hash, last_occupied_hash

def main():
    print("=" * 50)
    print("🎶 ESKONDIDINHO — Events Updater")
    print("=" * 50)

    if '--daemon' in sys.argv:
        print("🔄 Starting 24/7 daemon mode. Will check every 30 seconds.")
        last_events_hash = None
        last_occupied_hash = None
        while True:
            last_events_hash, last_occupied_hash = run_update_cycle(last_events_hash, last_occupied_hash)
            time.sleep(30)  # Sleep 30 seconds
    else:
        run_update_cycle()
        print("\n✨ Done!")


if __name__ == '__main__':
    main()
