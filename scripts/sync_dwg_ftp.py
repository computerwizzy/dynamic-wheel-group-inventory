import os
import csv
import io
import ftplib
import logging
import datetime
import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE_DIR, 'logs')
FEED_DIR = os.path.join(BASE_DIR, 'feeds')
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(FEED_DIR, exist_ok=True)

log_file = os.path.join(LOG_DIR, f'dwg_sync_{datetime.date.today()}.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(log_file), logging.StreamHandler()]
)

load_dotenv(os.path.join(BASE_DIR, '.env'))

SHEET_ID = '1hyXZzvAFfm2FKL8p-sw2cJTSMUeotfRXApjMhSOM3uA'

WBR_FTP_HOST = os.environ.get('FTP_HOST')
WBR_FTP_USER = os.environ.get('FTP_USER')
WBR_FTP_PASS = os.environ.get('FTP_PASS')

CSV_HEADERS = ['Part #', 'Wheel', 'Bolt', 'Offset', 'Hub', 'Finish',
               'CA-W1', 'CA-W3', 'CA-W4', 'TX-W6', 'GA-W7', 'NC-W8',
               'Total', 'OTW', 'CA STOCK', 'TX', 'GA', 'Style']

# Sheets to combine into one file
COMBINED_SHEETS = ['Diablo', 'Offroad', 'Curva', 'Tuners', 'Gianna']

# Sheets to upload as separate files
SEPARATE_SHEETS = {
    'Inventory File Links': 'DWG_Inventory_File_Links.csv',
    'Master Sheet Link':    'DWG_Master_Sheet.csv',
}


def fetch_sheet(sheet_name):
    url = f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&sheet={requests.utils.quote(sheet_name)}'
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = [row for row in reader if row.get('Part #', '').strip()]
    logging.info(f"  {sheet_name}: {len(rows)} rows")
    return rows


def write_csv(rows, filepath):
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def upload_to_wbr(local_file, remote_name):
    logging.info(f"Uploading {remote_name} to WBR FTP...")
    ftp = ftplib.FTP(WBR_FTP_HOST, timeout=60)
    ftp.login(WBR_FTP_USER, WBR_FTP_PASS)
    with open(local_file, 'rb') as f:
        ftp.storbinary(f'STOR {remote_name}', f)
    ftp.quit()
    logging.info(f"{remote_name} uploaded OK")


def main():
    start = datetime.datetime.now()
    logging.info("=== DWG Inventory Sync Started ===")

    # Combined sheet
    logging.info("Fetching combined sheets...")
    combined_rows = []
    for sheet in COMBINED_SHEETS:
        try:
            combined_rows.extend(fetch_sheet(sheet))
        except Exception as e:
            logging.error(f"Failed to fetch {sheet}: {e}")

    logging.info(f"Combined total: {len(combined_rows)} rows")
    combined_file = os.path.join(FEED_DIR, 'DWG_Inventory.csv')
    write_csv(combined_rows, combined_file)
    try:
        upload_to_wbr(combined_file, 'DWG_Inventory.csv')
    except Exception as e:
        logging.error(f"Upload error DWG_Inventory.csv: {e}")

    # Separate sheets
    for sheet_name, filename in SEPARATE_SHEETS.items():
        logging.info(f"Fetching {sheet_name}...")
        try:
            rows = fetch_sheet(sheet_name)
            filepath = os.path.join(FEED_DIR, filename)
            write_csv(rows, filepath)
            upload_to_wbr(filepath, filename)
        except Exception as e:
            logging.error(f"Error processing {sheet_name}: {e}")

    logging.info(f"=== DWG Sync Done in {datetime.datetime.now() - start} ===")


if __name__ == "__main__":
    main()
