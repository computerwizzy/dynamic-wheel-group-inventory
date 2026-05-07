"""
Push DWG_Shopify_Import.csv products to Shopify via GraphQL API (2026-01).

Two-step flow (required by 2024-01+ API):
  1. productCreate — title, description, options, metafields, media
  2. productVariantsBulkUpdate — update auto-created variant: SKU, price, weight, inventory

Run:  python scripts/push_dwg_shopify.py
Env:  SHOPIFY_STORE_URL, SHOPIFY_ACCESS_TOKEN
"""
import csv
import os
import time
from collections import defaultdict
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / '.env')
load_dotenv(Path(r'C:\Users\DELL-i7\Downloads\wheel1_not_in _store\.env'), override=False)

STORE_URL    = os.environ.get('SHOPIFY_STORE_URL', '').rstrip('/')
ACCESS_TOKEN = os.environ.get('SHOPIFY_ACCESS_TOKEN', '')
API_VERSION  = '2026-01'
GRAPHQL_URL  = f'https://{STORE_URL}/admin/api/{API_VERSION}/graphql.json'
CSV_PATH     = BASE_DIR / 'DWG_Shopify_Import.csv'

HEADERS = {
    'Content-Type': 'application/json',
    'X-Shopify-Access-Token': ACCESS_TOKEN,
}

QUERY_EXISTING_SKUS = """
query getVariants($cursor: String) {
  productVariants(first: 100, after: $cursor, query: "vendor:DIABLO OR vendor:GIANNA") {
    edges {
      cursor
      node {
        sku
      }
    }
    pageInfo { hasNextPage }
  }
}
"""

PRODUCT_CREATE = """
mutation productCreate($product: ProductCreateInput!, $media: [CreateMediaInput!]) {
  productCreate(product: $product, media: $media) {
    product {
      id
      handle
      title
      variants(first: 1) {
        edges { node { id } }
      }
    }
    userErrors {
      field
      message
    }
  }
}
"""

VARIANT_BULK_UPDATE = """
mutation productVariantsBulkUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants {
      id
    }
    userErrors {
      field
      message
    }
  }
}
"""


def gql(query, variables=None):
    resp = requests.post(GRAPHQL_URL, headers=HEADERS,
                         json={'query': query, 'variables': variables or {}}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_existing_skus():
    skus = set()
    cursor = None
    while True:
        result = gql(QUERY_EXISTING_SKUS, {'cursor': cursor})
        edges = result['data']['productVariants']['edges']
        for edge in edges:
            sku = edge['node']['sku']
            if sku:
                skus.add(sku)
            cursor = edge['cursor']
        if not result['data']['productVariants']['pageInfo']['hasNextPage']:
            break
        time.sleep(0.3)
    return skus


def build_metafields(row):
    mf = []
    def add(ns, key, val, ftype):
        if val:
            mf.append({'namespace': ns, 'key': key, 'value': str(val), 'type': ftype})

    add('custom', 'wheel_diameter', row['Wheel Diameter (product.metafields.custom.wheel_diameter)'], 'single_line_text_field')
    add('custom', 'wheel_width',    row['Wheel Width (product.metafields.custom.wheel_width)'],       'single_line_text_field')
    add('custom', 'hub',            row['Hub (product.metafields.custom.hub)'],                       'single_line_text_field')
    add('custom', 'backspace',      row['Backspace (product.metafields.custom.backspace)'],            'single_line_text_field')
    wm = row['Wheel Model (product.metafields.custom.wheel_model)']
    if wm and not wm.startswith('['):
        wm = f'["{wm}"]'
    add('custom', 'wheel_model',    wm,                                                                 'list.single_line_text_field')
    add('global',  'size',          row['Size (product.metafields.global.size)'],                     'single_line_text_field')
    add('global',  'bolt_pattern',  row['Bolt Pattern (product.metafields.global.bolt_pattern)'],     'single_line_text_field')
    add('global',  'offset',        row['Offset (product.metafields.global.offset)'],                 'single_line_text_field')
    add('custom', 'bolt_pattern_2', row['Bolt Pattern 2 (product.metafields.custom.bolt_pattern_2)'], 'list.single_line_text_field')
    add('custom', 'color',          row['Color (product.metafields.custom.color)'],                   'list.single_line_text_field')
    return mf


def build_payload(main_row, image_rows):
    # Step-1 product options (no variants here)
    options = []
    option_values = []
    for name_key, val_key in [('Option1 Name', 'Option1 Value'),
                               ('Option2 Name', 'Option2 Value'),
                               ('Option3 Name', 'Option3 Value')]:
        name = main_row.get(name_key, '').strip()
        val  = main_row.get(val_key, '').strip()
        if name and val:
            options.append({'name': name, 'values': [{'name': val}]})
            option_values.append({'optionName': name, 'name': val})

    try:
        weight = float(main_row['Variant Grams'])
    except (ValueError, TypeError):
        weight = 0

    # Step-2 variant input (2024-01+ API: sku/weight/requiresShipping live in inventoryItem)
    variant = {
        'price':           main_row['Variant Price'] or '0.00',
        'compareAtPrice':  main_row['Variant Compare At Price'] or None,
        'barcode':         main_row['Variant Barcode'] or None,
        'taxable':         True,
        'inventoryItem': {
            'tracked':          True,
            'sku':              main_row['Variant SKU'],
            'requiresShipping': True,
            'measurement': {
                'weight': {'value': weight, 'unit': 'POUNDS'},
            },
        },
        'inventoryPolicy': 'DENY',
    }
    if option_values:
        variant['optionValues'] = option_values

    tags = [t.strip() for t in main_row['Tags'].split(',') if t.strip()]

    product = {
        'title':           main_row['Title'],
        'descriptionHtml': main_row['Body (HTML)'],
        'vendor':          main_row['Vendor'],
        'productType':     main_row['Type'],
        'tags':            tags,
        'status':          main_row['Status'].upper(),
        'productOptions':  options,
        'metafields':      build_metafields(main_row),
    }

    # Media for step-1
    media = []
    all_images = []
    if main_row['Image Src']:
        all_images.append((main_row['Image Src'], main_row['Image Alt Text']))
    for img_row in image_rows:
        if img_row['Image Src']:
            all_images.append((img_row['Image Src'], img_row['Image Alt Text']))
    for src, alt in all_images:
        media.append({'originalSource': src, 'alt': alt, 'mediaContentType': 'IMAGE'})

    return product, variant, media


def main():
    if not STORE_URL or not ACCESS_TOKEN:
        print('ERROR: SHOPIFY_STORE_URL and SHOPIFY_ACCESS_TOKEN must be set')
        return

    products = defaultdict(lambda: {'main': None, 'images': []})
    with open(CSV_PATH, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            handle = row['Handle']
            if row['Variant SKU']:
                products[handle]['main'] = row
            elif row['Image Src']:
                products[handle]['images'].append(row)

    total = len(products)
    print(f'Products in CSV: {total}')

    print('Checking existing SKUs in Shopify...')
    existing_skus = fetch_existing_skus()
    if existing_skus:
        print(f'Already in Shopify: {len(existing_skus)} SKUs — will skip those')

    created = 0
    skipped = 0
    errors  = 0
    for i, (handle, data) in enumerate(products.items(), start=1):
        main_row = data['main']
        if not main_row:
            continue

        sku = main_row['Variant SKU']
        if sku in existing_skus:
            skipped += 1
            print(f'[{i}/{total}] SKIP {sku} (already exists)')
            continue

        product_input, variant_input, media = build_payload(main_row, data['images'])

        # Step 1: create product shell with options and media
        result = gql(PRODUCT_CREATE, {'product': product_input, 'media': media})
        user_errors = result.get('data', {}).get('productCreate', {}).get('userErrors', [])
        product     = result.get('data', {}).get('productCreate', {}).get('product')

        if user_errors or not product:
            errors += 1
            print(f'[{i}/{total}] ERROR(create) {main_row["Variant SKU"]}: {user_errors or result}')
            time.sleep(0.5)
            continue

        product_id = product['id']
        # productCreate auto-generates one variant per option combo — grab its ID to update it
        auto_variant_id = product['variants']['edges'][0]['node']['id']
        variant_input['id'] = auto_variant_id

        # Step 2: update the auto-created variant with SKU, price, weight, etc.
        v_result   = gql(VARIANT_BULK_UPDATE, {'productId': product_id, 'variants': [variant_input]})
        v_errors   = v_result.get('data', {}).get('productVariantsBulkUpdate', {}).get('userErrors', [])
        v_variants = v_result.get('data', {}).get('productVariantsBulkUpdate', {}).get('productVariants', [])

        if v_errors:
            errors += 1
            print(f'[{i}/{total}] ERROR(variant) {main_row["Variant SKU"]}: {v_errors}')
        elif v_variants:
            created += 1
            print(f'[{i}/{total}] OK  {main_row["Variant SKU"]} -> {product["handle"]}')
        else:
            errors += 1
            print(f'[{i}/{total}] UNKNOWN(variant) {main_row["Variant SKU"]}: {v_result}')

        time.sleep(0.5)

    print(f'\nDone. Created: {created}, Skipped: {skipped}, Errors: {errors}')


if __name__ == '__main__':
    main()
