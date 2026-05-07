"""
Activate and publish DWG products that have inventory qty > 0.
Sets status=ACTIVE and publishes to all channels.

Run:  python scripts/activate_dwg_products.py
Env:  SHOPIFY_STORE_URL, SHOPIFY_ACCESS_TOKEN
"""
import csv
import os
import time
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

QUERY_VARIANT = """
query getVariantBySku($sku: String!) {
  productVariants(first: 1, query: $sku) {
    edges {
      node {
        sku
        product {
          id
          status
        }
      }
    }
  }
}
"""

PRODUCT_UPDATE_STATUS = """
mutation productUpdate($input: ProductInput!) {
  productUpdate(input: $input) {
    product { id status }
    userErrors { field message }
  }
}
"""

PUBLISH_ALL = """
mutation publishablePublish($id: ID!, $input: [PublicationInput!]!) {
  publishablePublish(id: $id, input: $input) {
    publishable { ... on Product { id title } }
    userErrors { field message }
  }
}
"""


def gql(query, variables=None):
    resp = requests.post(GRAPHQL_URL, headers=HEADERS,
                         json={'query': query, 'variables': variables or {}}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_all_publication_ids():
    result = gql('{publications(first: 20) { edges { node { id name } } }}')
    pubs = result['data']['publications']['edges']
    return [{'publicationId': e['node']['id']} for e in pubs]


def load_skus_with_stock():
    skus = {}
    with open(CSV_PATH, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            if row['Variant SKU']:
                try:
                    qty = int(float(row['Variant Inventory Qty'] or '0'))
                except (ValueError, TypeError):
                    qty = 0
                if qty > 0:
                    skus[row['Variant SKU']] = qty
    return skus


def main():
    if not STORE_URL or not ACCESS_TOKEN:
        print('ERROR: SHOPIFY_STORE_URL and SHOPIFY_ACCESS_TOKEN must be set')
        return

    print('Loading SKUs with stock from CSV...')
    skus = load_skus_with_stock()
    print(f'  {len(skus)} products have qty > 0')

    print('Fetching all publication channels...')
    publications = get_all_publication_ids()
    print(f'  {len(publications)} channels: will publish to all')

    activated = 0
    already   = 0
    errors    = 0
    total     = len(skus)

    for i, (sku, qty) in enumerate(skus.items(), start=1):
        # Get product ID via SKU
        result = gql(QUERY_VARIANT, {'sku': f'sku:{sku}'})
        edges  = result.get('data', {}).get('productVariants', {}).get('edges', [])

        if not edges or edges[0]['node']['sku'] != sku:
            errors += 1
            print(f'[{i}/{total}] NOT FOUND  {sku}')
            time.sleep(0.3)
            continue

        product    = edges[0]['node']['product']
        product_id = product['id']
        status     = product['status']

        if status == 'ACTIVE':
            already += 1
            print(f'[{i}/{total}] ALREADY ACTIVE  {sku} (qty={qty})')
            time.sleep(0.3)
            continue

        # Set ACTIVE
        upd = gql(PRODUCT_UPDATE_STATUS, {'input': {'id': product_id, 'status': 'ACTIVE'}})
        upd_errs = upd.get('data', {}).get('productUpdate', {}).get('userErrors', [])
        if upd_errs:
            errors += 1
            print(f'[{i}/{total}] ERROR(activate)  {sku}: {upd_errs}')
            time.sleep(0.3)
            continue

        # Publish to all channels
        pub = gql(PUBLISH_ALL, {'id': product_id, 'input': publications})
        pub_errs = pub.get('data', {}).get('publishablePublish', {}).get('userErrors', [])
        if pub_errs:
            errors += 1
            print(f'[{i}/{total}] ERROR(publish)  {sku}: {pub_errs}')
        else:
            activated += 1
            print(f'[{i}/{total}] ACTIVE+PUBLISHED  {sku} (qty={qty})')

        time.sleep(0.4)

    print(f'\nDone. Activated: {activated}, Already active: {already}, Errors: {errors}')


if __name__ == '__main__':
    main()
