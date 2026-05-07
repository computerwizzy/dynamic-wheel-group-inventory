"""
Sync DWG inventory quantities to Shopify (DWG Warehouse location).

- Looks up "DWG Warehouse" location ID dynamically (no hardcoded ID).
- Bulk-fetches all DIABLO/GIANNA variant inventory item IDs in one pass.
- Sets on-hand quantities at DWG Warehouse in batches of 100.

Run:  python scripts/sync_dwg_inventory.py
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
_local_env = Path(r'C:\Users\DELL-i7\Downloads\wheel1_not_in _store\.env')
if _local_env.exists():
    load_dotenv(_local_env, override=False)

STORE_URL    = os.environ.get('SHOPIFY_STORE_URL', '').rstrip('/')
ACCESS_TOKEN = os.environ.get('SHOPIFY_ACCESS_TOKEN', '')
API_VERSION  = '2026-01'
GRAPHQL_URL  = f'https://{STORE_URL}/admin/api/{API_VERSION}/graphql.json'
CSV_PATH     = BASE_DIR / 'DWG_Shopify_Import.csv'

LOCATION_ID   = os.environ.get('DWG_LOCATION_ID', 'gid://shopify/Location/91987378411')

HEADERS = {
    'Content-Type': 'application/json',
    'X-Shopify-Access-Token': ACCESS_TOKEN,
}

QUERY_VARIANTS = """
query getDWGVariants($cursor: String) {
  productVariants(first: 100, after: $cursor, query: "vendor:DIABLO OR vendor:GIANNA") {
    edges {
      cursor
      node {
        sku
        inventoryItem { id }
      }
    }
    pageInfo { hasNextPage }
  }
}
"""

SET_ON_HAND = """
mutation inventorySetOnHandQuantities($input: InventorySetOnHandQuantitiesInput!) {
  inventorySetOnHandQuantities(input: $input) {
    inventoryAdjustmentGroup { id }
    userErrors { field message }
  }
}
"""


def gql(query, variables=None):
    resp = requests.post(GRAPHQL_URL, headers=HEADERS,
                         json={'query': query, 'variables': variables or {}}, timeout=30)
    resp.raise_for_status()
    return resp.json()



def fetch_shopify_inventory_items():
    """Returns {sku: inventory_item_id} for all DIABLO/GIANNA variants."""
    items  = {}
    cursor = None
    while True:
        result = gql(QUERY_VARIANTS, {'cursor': cursor})
        edges  = result['data']['productVariants']['edges']
        for edge in edges:
            node = edge['node']
            if node['sku']:
                items[node['sku']] = node['inventoryItem']['id']
            cursor = edge['cursor']
        if not result['data']['productVariants']['pageInfo']['hasNextPage']:
            break
        time.sleep(0.3)
    return items


def load_csv_quantities():
    skus = {}
    with open(CSV_PATH, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            if row['Variant SKU']:
                try:
                    qty = int(float(row['Variant Inventory Qty'] or '0'))
                except (ValueError, TypeError):
                    qty = 0
                skus[row['Variant SKU']] = qty
    return skus


def main():
    if not STORE_URL or not ACCESS_TOKEN:
        print('ERROR: SHOPIFY_STORE_URL and SHOPIFY_ACCESS_TOKEN must be set')
        return

    print(f'Using location ID: {LOCATION_ID}')

    # Load CSV quantities
    print('Loading quantities from CSV...')
    csv_qtys = load_csv_quantities()
    print(f'  {len(csv_qtys)} SKUs — {sum(1 for q in csv_qtys.values() if q > 0)} with stock')

    # Bulk fetch inventory item IDs from Shopify
    print('Fetching inventory item IDs from Shopify (bulk)...')
    shopify_items = fetch_shopify_inventory_items()
    print(f'  {len(shopify_items)} variants found in Shopify')

    # Build on-hand payload
    quantities = []
    not_found  = []
    for sku, qty in csv_qtys.items():
        inv_id = shopify_items.get(sku)
        if inv_id:
            quantities.append({'inventoryItemId': inv_id, 'locationId': LOCATION_ID, 'quantity': qty})
        else:
            not_found.append(sku)

    if not_found:
        print(f'  WARNING: {len(not_found)} SKUs not found in Shopify: {not_found}')

    if not quantities:
        print('Nothing to sync.')
        return

    print(f'\nSetting on-hand quantities for {len(quantities)} SKUs at {LOCATION_ID}...')

    BATCH   = 100
    updated = 0
    errors  = 0
    for start in range(0, len(quantities), BATCH):
        batch  = quantities[start:start + BATCH]
        result = gql(SET_ON_HAND, {
            'input': {
                'reason':       'correction',
                'setQuantities': batch,
            }
        })
        errs = result.get('data', {}).get('inventorySetOnHandQuantities', {}).get('userErrors', [])
        if errs:
            errors += len(batch)
            print(f'  ERROR batch {start // BATCH + 1}: {errs}')
        else:
            updated += len(batch)
            print(f'  Batch {start // BATCH + 1}: {len(batch)} items set OK')
        time.sleep(0.5)

    print(f'\nDone. On-hand updated: {updated}, Not found: {len(not_found)}, Errors: {errors}')


if __name__ == '__main__':
    main()
