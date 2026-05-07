"""
Delete all DIABLO and GIANNA products from Shopify (cleanup after failed push runs).
Run:  python scripts/cleanup_dwg_shopify.py
"""
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

HEADERS = {
    'Content-Type': 'application/json',
    'X-Shopify-Access-Token': ACCESS_TOKEN,
}

QUERY_PRODUCTS = """
query getProducts($cursor: String) {
  products(first: 50, after: $cursor, query: "vendor:DIABLO OR vendor:GIANNA") {
    edges {
      cursor
      node {
        id
        title
        vendor
        handle
      }
    }
    pageInfo {
      hasNextPage
    }
  }
}
"""

DELETE_PRODUCT = """
mutation productDelete($id: ID!) {
  productDelete(input: {id: $id}) {
    deletedProductId
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


def fetch_all_products():
    products = []
    cursor = None
    while True:
        result = gql(QUERY_PRODUCTS, {'cursor': cursor})
        edges = result['data']['products']['edges']
        for edge in edges:
            products.append(edge['node'])
            cursor = edge['cursor']
        if not result['data']['products']['pageInfo']['hasNextPage']:
            break
        time.sleep(0.3)
    return products


def main():
    if not STORE_URL or not ACCESS_TOKEN:
        print('ERROR: SHOPIFY_STORE_URL and SHOPIFY_ACCESS_TOKEN must be set')
        return

    print('Fetching DIABLO and GIANNA products...')
    products = fetch_all_products()
    print(f'Found {len(products)} products to delete')

    if not products:
        print('Nothing to delete.')
        return

    for p in products:
        print(f'  {p["vendor"]:8s} {p["handle"]}')

    confirm = input(f'\nDelete all {len(products)} products? (yes/no): ').strip().lower()
    if confirm != 'yes':
        print('Aborted.')
        return

    deleted = 0
    errors  = 0
    for i, p in enumerate(products, start=1):
        result = gql(DELETE_PRODUCT, {'id': p['id']})
        errs   = result.get('data', {}).get('productDelete', {}).get('userErrors', [])
        did    = result.get('data', {}).get('productDelete', {}).get('deletedProductId')
        if errs:
            errors += 1
            print(f'[{i}/{len(products)}] ERROR {p["handle"]}: {errs}')
        elif did:
            deleted += 1
            print(f'[{i}/{len(products)}] DELETED {p["handle"]}')
        else:
            errors += 1
            print(f'[{i}/{len(products)}] UNKNOWN {result}')
        time.sleep(0.3)

    print(f'\nDone. Deleted: {deleted}, Errors: {errors}')


if __name__ == '__main__':
    main()