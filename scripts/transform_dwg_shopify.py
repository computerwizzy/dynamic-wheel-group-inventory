"""
Transform DWG (Dynamic Wheel Group) inventory from Google Sheets into a
Shopify product import CSV. Each Part # becomes its own independent product.

Run:  python scripts/transform_dwg_shopify.py
Output: DWG_Shopify_Import.csv (at repo root)
"""
import csv
import html as _html
import io
import re
from pathlib import Path

import requests

SHEET_ID = '1hyXZzvAFfm2FKL8p-sw2cJTSMUeotfRXApjMhSOM3uA'
MASTER_GID = '51551165'
COMBINED_SHEETS = ['Diablo', 'Offroad', 'Curva', 'Tuners', 'Gianna']

OUT = Path(__file__).parent.parent / 'DWG_Shopify_Import.csv'

# Update these if you know the full brand names
VENDOR_MAP = {
    'ANG': 'Angle',
    'DNA': 'DNA',
    'DE':  'DE Wheels',
    'FUR': 'Furiosa',
    'MOR': 'Moro',
    'REF': 'Reflex',
    'G1':  'G1',
}

BASE_COLORS = [
    ('BRONZE', 'Bronze'), ('COPPER', 'Bronze'),
    ('GOLD', 'Gold'),
    ('BLACK', 'Black'),
    ('WHITE', 'White'),
    ('RED', 'Red'), ('ORANGE', 'Red'),
    ('BLUE', 'Blue'),
    ('GREEN', 'Green'),
    ('YELLOW', 'Yellow'),
    ('PURPLE', 'Purple'),
    ('TITANIUM', 'Gray'), ('GUN METAL', 'Gray'), ('GUNMETAL', 'Gray'),
    ('GREY', 'Gray'), ('GRAY', 'Gray'),
    ('CHROME', 'Chrome'),
    ('POLISH', 'Polished'),
    ('MACHINED', 'Silver'), ('BRUSHED', 'Silver'), ('SILVER', 'Silver'),
]

def derive_color(finish):
    u = str(finish or '').upper()
    for keyword, base in BASE_COLORS:
        if keyword in u:
            return base
    return 'Silver'


METRIC_TO_IMPERIAL = {
    '108': '4.25', '114.3': '4.5', '120.7': '4.75',
    '127': '5', '139.7': '5.5',
    '152.4': '6', '165.1': '6.5',
    '177.8': '7',
}

def bp_dual(bp):
    """'5x114.3' -> '5x114.3 (5x4.5)'. Handles dual patterns like '5x112/114.3'."""
    if not bp or bp.strip().lower() in ('blank', ''):
        return bp
    s = str(bp).strip()
    # dual pattern like 5x112/114.3
    m = re.match(r'^(\d+)\s*x\s*([\d.]+)\s*/\s*([\d.]+)$', s)
    if m:
        lugs, pcd1, pcd2 = m.group(1), m.group(2), m.group(3)
        imp1 = METRIC_TO_IMPERIAL.get(pcd1, '')
        imp2 = METRIC_TO_IMPERIAL.get(pcd2, '')
        dual_imp = f'{lugs}x{imp1}/{imp2}' if (imp1 and imp2) else ''
        return f'{lugs}x{pcd1}/{pcd2} ({dual_imp})' if dual_imp else f'{lugs}x{pcd1}/{pcd2}'
    # single pattern
    m = re.match(r'^(\d+)\s*x\s*([\d.]+)$', s.lower())
    if not m:
        return s
    lugs, pcd = m.group(1), m.group(2)
    imp = METRIC_TO_IMPERIAL.get(pcd)
    return f'{lugs}x{pcd} ({lugs}x{imp})' if imp else f'{lugs}x{pcd}'


def bp_normalize(bp):
    """Normalize dual bolt pattern so both sides have the lug count.
    '5x112/114.3'   -> '5x112/5x114.3'
    '5x114.3/120'   -> '5x114.3/5x120'
    '6x135/6x139.7' -> '6x135/6x139.7'  (already normalized)
    '5x114.3'       -> '5x114.3'
    """
    if not bp or bp.strip().lower() == 'blank':
        return bp
    s = bp.strip()
    m = re.match(r'^(\d+)\s*x\s*([\d.]+)\s*/\s*((?:\d+x)?([\d.]+))$', s)
    if m:
        lugs, pcd1, second = m.group(1), m.group(2), m.group(3)
        if 'x' in second.lower():
            return f'{lugs}x{pcd1}/{second}'
        return f'{lugs}x{pcd1}/{lugs}x{second}'
    return s


def bp_list(bp):
    """Return Shopify list metafield string for Bolt Pattern 2.
    '6x135/6x139.7' -> '["6x135","6x139.7"]'
    '5x112/114.3'   -> '["5x112","5x114.3"]'  (lug count shared)
    '5x114.3'       -> '["5x114.3"]'
    """
    if not bp:
        return ''
    if bp.strip().lower() == 'blank':
        return '["Blank"]'
    s = bp.strip().lower()
    # dual pattern: NxA/B or NxA/NxB
    m = re.match(r'^(\d+)\s*x\s*([\d.]+)\s*/\s*((?:\d+x)?([\d.]+))$', s)
    if m:
        lugs, pcd1, second = m.group(1), m.group(2), m.group(3)
        # if second part already has lug count (e.g. "6x139.7"), use as-is; else prepend lugs
        if 'x' in second:
            pcd2_full = second
        else:
            pcd2_full = f'{lugs}x{second}'
        bp1 = f'{lugs}x{pcd1}'
        bp2 = pcd2_full
        return f'["{bp1}","{bp2}"]'
    return f'["{s}"]'


def slugify(text):
    s = str(text).lower()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    return s.strip('-')


def build_spec_html(vendor, size, bolt, offset, hub, finish, part_number,
                    backspace='', load_rating=''):
    # parse diameter/width from size e.g. "20x8.5"
    diam, width = '', ''
    m = re.match(r'^(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)', str(size or ''))
    if m:
        diam, width = m.group(1), m.group(2)

    def li(label, val=''):
        v = _html.escape(str(val)) if val else ''
        return f'<li><strong>{label}:</strong> {v}</li>'

    items = [
        li('Brand', vendor),
        li('Size', size),
        li('Part Number', part_number),
        li('Color', finish),
        li('Backspacing', backspace),
        li('Offset', offset),
        li('Wheel Diameter', diam),
        li('Wheel Width', width),
        li('Hub Bore', hub),
        li('Load Rating', load_rating),
        li('Wheel Exposed Lugs'),
        li('Wheel Material', 'Aluminum Alloy'),
        li('Weight'),
        li('Wheel Structure', 'One Piece'),
        li('Wheel Spoke Number'),
        li('Bolt Pattern', bp_dual(bolt)),
        li('True Directional'),
    ]
    lines = '\n'.join(items)
    return f'<ul>\n{lines}\n</ul>'


OUT_COLS = [
    'Handle', 'Title', 'Body (HTML)', 'Vendor', 'Product Category', 'Type', 'Tags', 'Published',
    'Option1 Name', 'Option1 Value', 'Option2 Name', 'Option2 Value', 'Option3 Name', 'Option3 Value',
    'Variant SKU', 'Variant Grams', 'Variant Inventory Tracker', 'Variant Inventory Qty',
    'Variant Inventory Policy', 'Variant Fulfillment Service', 'Variant Price',
    'Variant Compare At Price', 'Variant Requires Shipping', 'Variant Taxable',
    'Variant Barcode', 'Image Src', 'Image Position', 'Image Alt Text', 'Gift Card',
    'SEO Title', 'SEO Description',
    'Wheel Diameter (product.metafields.custom.wheel_diameter)',
    'Wheel Width (product.metafields.custom.wheel_width)',
    'Hub (product.metafields.custom.hub)',
    'Size (product.metafields.global.size)',
    'Bolt Pattern (product.metafields.global.bolt_pattern)',
    'Offset (product.metafields.global.offset)',
    'Bolt Pattern 2 (product.metafields.custom.bolt_pattern_2)',
    'Backspace (product.metafields.custom.backspace)',
    'Color (product.metafields.custom.color)',
    'Wheel Model (product.metafields.custom.wheel_model)',
    'Variant Image', 'Variant Weight Unit', 'Variant Tax Code', 'Cost per item',
    'Included / United States', 'Price / United States', 'Compare At Price / United States',
    'Status',
]


def build_tags(vendor, diam, width, bolt, offset, hub, finish):
    tags = []

    # generic category
    tags += ['wheels', 'rims', 'rim', 'wheel', 'Category_Wheels']

    # brand
    if vendor:
        tags += [vendor, f'brand_{vendor}']

    # finish / color
    if finish:
        tags.append(finish)

    # diameter
    if diam:
        tags += [
            f'{diam}"',
            f'{diam}" wheels',
            f'{diam}" wheel',
            f'{diam}-diameter',
            f'{diam}-diameter-wheel',
            f'{diam}-diameter-wheels',
            f'{diam} inch wheels',
            f'wheel diameter_{diam}"',
            f'diameter-{diam} Inches',
        ]

    # width
    if width:
        tags += [
            f'width-{width}',
            f'wheel width_{width}"',
        ]

    # size combined e.g. 20X8.5
    if diam and width:
        tags.append(f'{diam}X{width}')

    # offset
    if offset:
        try:
            off_int = int(float(offset))
            tags += [
                f'offset-{off_int}',
                f'wheel offset_{off_int}mm',
            ]
            if off_int < 0:
                tags.append(f'negative-{abs(off_int)}')
        except ValueError:
            tags.append(f'offset-{offset}')

    # hub bore
    if hub:
        tags += [
            f'hub-bore-{hub}',
            f'{hub}-center-bore',
        ]

    # bolt pattern
    if bolt and bolt.lower() != 'blank':
        # handle dual pattern e.g. 6x135/6x139.7
        parts = re.split(r'/', bolt)
        lug_m = re.match(r'^(\d+)', bolt)
        lug = lug_m.group(1) if lug_m else ''
        if lug:
            tags.append(f'{lug}-lugs')
        for p in parts:
            p = p.strip()
            # if second part has no lug count, prepend it
            if 'x' not in p.lower() and lug:
                p = f'{lug}x{p}'
            tags += [
                p,
                p.upper(),
                f'bolt-pattern-{p}',
                f'wheel bolt-pattern_{p}',
            ]
        if len(parts) > 1:
            tags.append(f'bolt pattern_{bp_dual(bolt)}')

    # size+bolt+offset combo slug
    if diam and bolt and offset:
        bolt_slug = re.sub(r'[^a-z0-9x.]', '', bolt.lower())
        tags.append(f'{diam}X{width}-{bolt_slug}-offset-{offset}')

    # deduplicate preserving order
    seen = set()
    result = []
    for t in tags:
        if t and t not in seen:
            seen.add(t)
            result.append(t)
    return ','.join(result)


def fetch_master():
    """Fetch master price/spec sheet by GID. Returns dict keyed by Part #."""
    url = f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={MASTER_GID}'
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    raw_rows = list(csv.DictReader(io.StringIO(resp.text)))
    lookup = {}
    style_lookup = {}

    def clean_price(v):
        return re.sub(r'[^0-9.]', '', str(v or ''))

    for row in raw_rows:
        pn = row.get(' ', '').strip()
        s  = row.get('Style #', '').strip()
        w  = row.get('Wheel_Name', '').strip()
        if s and w and s not in style_lookup:
            style_lookup[s] = w
        if not pn:
            continue
        lookup[pn] = {
            'msrp':         clean_price(row.get('MSRP', '')),
            'map':          clean_price(row.get('MAP', '')),
            'brand':        row.get('Brand', '').strip(),
            'wheel_name':   w,
            'weight_lb':    re.sub(r'[^0-9.]', '', row.get('Weight (lb)', '')),
            'backspace':    re.sub(r'[^0-9.]', '', row.get('Backspace', '')),
            'load_rating':  row.get('Load Rating LBs', '').strip(),
            'upc':          row.get('12 Digit UPC Code', '').strip(),
            'image_angle':  row.get('Angle - PNG', '').strip(),
            'image_front':  row.get('Front - PNG', '').strip(),
            'image_side':   row.get('Side - PNG', '').strip(),
            'image_tilt':   row.get('Tilt - PNG', '').strip(),
            'lugseat':      row.get('Lugseat Type', '').strip(),
            'division':     row.get('Division', '').strip(),
            'status':       row.get('Status', '').strip(),
        }
    print(f'  Master sheet: {len(lookup)} part numbers loaded, {len(style_lookup)} style codes')
    return lookup, style_lookup


def fetch_sheet(sheet_name):
    url = (f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/export'
           f'?format=csv&sheet={requests.utils.quote(sheet_name)}')
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = [r for r in reader if r.get('Part #', '').strip()]
    print(f'  {sheet_name}: {len(rows)} rows')
    return rows


def main():
    print('Fetching DWG inventory from Google Sheets...')
    master, style_names = fetch_master()
    all_rows = []
    seen_skus = set()
    for sheet in COMBINED_SHEETS:
        try:
            rows = fetch_sheet(sheet)
            for r in rows:
                sku = r['Part #'].strip()
                if sku and sku not in seen_skus:
                    seen_skus.add(sku)
                    all_rows.append(r)
        except Exception as e:
            print(f'  ERROR fetching {sheet}: {e}')

    print(f'Total unique products: {len(all_rows)}')

    out_rows = []
    for r in all_rows:
        part_num = r['Part #'].strip()
        wheel    = r.get('Wheel', '').strip()
        bolt     = r.get('Bolt', '').strip()
        offset   = r.get('Offset', '').strip()
        hub      = r.get('Hub', '').strip()
        finish   = r.get('Finish', '').strip()
        total    = r.get('Total', '0').strip() or '0'
        style    = r.get('Style', '').strip()

        # Skip rows with no valid style or #N/A
        if style in ('', '#N/A'):
            style = part_num.split('-')[0] if '-' in part_num else ''

        # Master sheet enrichment (needed for vendor name)
        mx = master.get(part_num, {})
        if not mx:
            continue
        wheel_name  = mx.get('wheel_name', '')
        brand       = mx.get('brand', '')

        vendor = brand.upper() if brand else style.upper()

        # Parse diameter and width from Wheel column e.g. "20x8.5"
        diam, width = '', ''
        m = re.match(r'^(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)', wheel)
        if m:
            diam, width = m.group(1), m.group(2)

        # Bolt pattern normalized (lug count on both sides of dual patterns)
        bp_norm = 'Blank' if bolt.lower() == 'blank' else (bp_normalize(bolt) if bolt else '')
        bp_meta = bp_norm if bp_norm == 'Blank' else bp_norm.lower()

        color_base = derive_color(finish)
        price       = mx.get('map', '')
        compare     = mx.get('msrp', '')
        weight_lb   = mx.get('weight_lb', '')
        backspace   = mx.get('backspace', '')
        load_rating = mx.get('load_rating', '')
        upc         = mx.get('upc', '')
        images      = [img for img in [
                            mx.get('image_angle', ''),
                            mx.get('image_front', ''),
                            mx.get('image_side', ''),
                            mx.get('image_tilt', ''),
                       ] if img]

        model_slug = style_names.get(style) or wheel_name or style
        handle = slugify(f'{brand}-{model_slug}-{wheel}-{part_num}')

        bp_display = bp_dual(bolt)
        offset_str = (offset + 'mm') if offset else ''

        # Include wheel model name in title if available
        title_parts = [vendor, wheel_name, wheel, bp_display, offset_str, finish, part_num]
        title = ' '.join(p for p in title_parts if p)

        html_body = build_spec_html(vendor, wheel, bolt, offset, hub, finish, part_num,
                                    backspace=backspace, load_rating=load_rating)

        # Normalize inventory qty
        try:
            qty = str(int(float(total)))
        except (ValueError, TypeError):
            qty = '0'

        out = {c: '' for c in OUT_COLS}
        out['Handle']                   = handle
        out['Title']                    = title
        out['Body (HTML)']              = html_body
        out['Vendor']                   = vendor
        out['Type']                     = f'{diam}" Wheels' if diam else 'Wheels'
        out['Tags']                     = build_tags(vendor, diam, width, bolt, offset, hub, finish)
        out['Published']                = 'TRUE'
        out['Option1 Name']             = 'Size'
        out['Option1 Value']            = wheel or 'Default'
        out['Option2 Name']             = 'Bolt Pattern' if bolt else ''
        out['Option2 Value']            = bp_norm
        out['Option3 Name']             = 'Offset' if offset else ''
        out['Option3 Value']            = offset
        out['Variant SKU']              = part_num
        try:
            ship_weight = str(round(float(weight_lb) + 3, 2)) if weight_lb else '0'
        except ValueError:
            ship_weight = '0'
        out['Variant Grams']            = ship_weight
        out['Variant Inventory Tracker']= 'shopify'
        out['Variant Inventory Qty']    = qty
        out['Variant Inventory Policy'] = 'deny'
        out['Variant Fulfillment Service'] = 'manual'
        out['Variant Price']            = price
        out['Variant Compare At Price'] = compare if (compare and price and compare != price) else ''
        out['Variant Requires Shipping']= 'TRUE'
        out['Variant Taxable']          = 'TRUE'
        out['Variant Barcode']          = upc
        out['Image Src']                = images[0] if images else ''
        out['Image Position']           = '1' if images else ''
        out['Image Alt Text']           = title
        out['Gift Card']                = 'FALSE'
        out['SEO Title']                = title
        out['Status']                   = 'draft'
        out['Variant Weight Unit']      = 'lb'

        out['Wheel Diameter (product.metafields.custom.wheel_diameter)'] = f'{diam}"' if diam else ''
        out['Wheel Width (product.metafields.custom.wheel_width)']       = f'{width}"' if width else ''
        out['Hub (product.metafields.custom.hub)']                       = hub
        out['Size (product.metafields.global.size)']                     = wheel
        out['Bolt Pattern (product.metafields.global.bolt_pattern)']     = bp_meta
        out['Offset (product.metafields.global.offset)']                 = offset
        out['Bolt Pattern 2 (product.metafields.custom.bolt_pattern_2)'] = bp_list(bolt)
        out['Backspace (product.metafields.custom.backspace)']           = backspace
        out['Color (product.metafields.custom.color)']                   = f'["{color_base}"]'
        out['Wheel Model (product.metafields.custom.wheel_model)']       = (style_names.get(style) or wheel_name or style).upper()

        out_rows.append(out)

        # Additional image rows (positions 2-4) only if images exist
        for pos, img_url in enumerate(images[1:], start=2):
            img_row = {c: '' for c in OUT_COLS}
            img_row['Handle']         = handle
            img_row['Image Src']      = img_url
            img_row['Image Position'] = str(pos)
            img_row['Image Alt Text'] = title
            out_rows.append(img_row)


    with open(OUT, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=OUT_COLS)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f'Wrote {len(out_rows)} products -> {OUT}')
    print('\nSample (first 5):')
    for r in out_rows[:5]:
        print(f"  SKU={r['Variant SKU']!r:30s}  Vendor={r['Vendor']!r:12s}  "
              f"Size={r['Option1 Value']!r:8s}  Bolt={r['Option2 Value']!r:15s}  "
              f"Offset={r['Option3 Value']!r:5s}  Qty={r['Variant Inventory Qty']}")


if __name__ == '__main__':
    main()
