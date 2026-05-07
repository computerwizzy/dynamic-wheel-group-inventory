"""
Sync DWG inventory quantities to Shopify (DWG Warehouse location).

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

LOCATION_ID  = 'gid://shopify/Location/91987378411'  # DWG Warehouse (Alabama)

HEADERS = {
    'Content-Type': 'application/json',
    'X-Shopify-Access-Token': ACCESS_TOKEN,
}

QUERY_INVENTORY_ITEM = """
query getVariantBySku($sku: String!) {
  productVariants(first: 1, query: $sku) {
    edges {
      node {
        sku
        inventoryItem {
          id
        }
      }
    }
  }
}
"""

SET_INVENTORY = """
mutation inventorySetOnHandQuantities($input: InventorySetOnHandQuantitiesInput!) {
  inventorySetOnHandQuantities(input: $input) {
    inventoryAdjustmentGroup {
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


def fetch_inventory_item_id(sku):
    result = gql(QUERY_INVENTORY_ITEM, {'sku': f'sku:{sku}'})
    edges = result.get('data', {}).get('productVariants', {}).get('edges', [])
    if not edges:
        return None
    node = edges[0]['node']
    if node['sku'] != sku:
        return None
    return node['inventoryItem']['id']


def main():
    if not STORE_URL or not ACCESS_TOKEN:
        print('ERROR: SHOPIFY_STORE_URL and SHOPIFY_ACCESS_TOKEN must be set')
        return

    print('Loading quantities from CSV...')
    skus = load_csv_quantities()
    print(f'  {len(skus)} SKUs — {sum(1 for q in skus.values() if q > 0)} with stock')

    # Build quantities list in batches of 100
    print('Fetching inventory item IDs from Shopify...')
    quantities = []
    not_found = []
    for i, (sku, qty) in enumerate(skus.items(), start=1):
        inv_id = fetch_inventory_item_id(sku)
        if inv_id:
            quantities.append({'inventoryItemId': inv_id, 'locationId': LOCATION_ID, 'quantity': qty})
            print(f'  [{i}/{len(skus)}] {sku}: qty={qty}')
        else:
            not_found.append(sku)
            print(f'  [{i}/{len(skus)}] {sku}: NOT FOUND in Shopify')
        time.sleep(0.3)

    if not quantities:
        print('Nothing to set.')
        return

    print(f'\nSetting {len(quantities)} inventory levels at DWG Warehouse...')

    # Send in batches of 100 (API limit)
    BATCH = 100
    updated = 0
    errors  = 0
    for start in range(0, len(quantities), BATCH):
        batch = quantities[start:start + BATCH]
        result = gql(SET_INVENTORY, {
            'input': {
                'reason': 'correction',
                'setQuantities': batch,
            }
        })
        errs = result.get('data', {}).get('inventorySetOnHandQuantities', {}).get('userErrors', [])
        if errs:
            errors += len(batch)
            print(f'ERROR on batch {start//BATCH + 1}: {errs}')
        else:
            updated += len(batch)
            print(f'Batch {start//BATCH + 1}: {len(batch)} items set OK')
        time.sleep(0.5)

    print(f'\nDone. Updated: {updated}, Not found: {len(not_found)}, Errors: {errors}')
    if not_found:
        print('SKUs not found in Shopify:', not_found)


if __name__ == '__main__':
    main()
