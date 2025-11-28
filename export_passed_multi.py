"""
여러 작업자 시트에서 '통과' 데이터만 추출하여 JSON으로 합치고,
해당 이미지 파일을 지정 폴더에 복사합니다.
"""
import os
import json
import ast
import shutil
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from config import GOOGLE_SHEETS_SPREADSHEET_ID, GOOGLE_SHEETS_CREDENTIALS_PATH

# 설정
TARGET_SHEETS = [
    '한솔미',
    '양문정',
    '이주원',
    '김양희',
    '황선우',
]
DATE_PREFIX = '251127'
OUTPUT_JSON_NAME = f'{DATE_PREFIX}_ego'
SOURCE_IMAGES_DIR = r'C:\Users\USER\Downloads\images\ego\ego_images'

# 이주원 시트는 상단에서부터 52개만 사용
LEEJUWON_LIMIT = 52


def init_client():
    if not os.path.exists(GOOGLE_SHEETS_CREDENTIALS_PATH):
        raise FileNotFoundError(f'Google credentials not found: {GOOGLE_SHEETS_CREDENTIALS_PATH}')
    scope = ['https://spreadsheets.google.com/feeds',
             'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_file(GOOGLE_SHEETS_CREDENTIALS_PATH, scopes=scope)
    return gspread.authorize(creds)


def read_sheet(client, sheet_name):
    spreadsheet = client.open_by_key(GOOGLE_SHEETS_SPREADSHEET_ID)
    try:
        worksheet = spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        print(f"[WARN] 시트 '{sheet_name}'을 찾을 수 없습니다.")
        return []

    all_values = worksheet.get_all_values()
    if len(all_values) < 2:
        print(f"[WARN] 시트 '{sheet_name}'에 데이터가 없습니다.")
        return []

    headers = all_values[0]
    header_indices = {header: idx for idx, header in enumerate(headers)}

    result = []
    for row in all_values[1:]:
        if len(row) == 0:
            continue
        row_data = {}
        for header, idx in header_indices.items():
            row_data[header] = row[idx] if idx < len(row) else ''
        result.append(row_data)
    return result


def parse_bbox(bbox_str):
    bbox_str = bbox_str.strip()
    if not bbox_str:
        return []
    try:
        parsed = ast.literal_eval(bbox_str)
        if isinstance(parsed, list):
            return parsed
    except Exception as e:
        print(f"[WARN] bbox 파싱 실패: {bbox_str} ({e})")
    return []


def row_to_json(row):
    image_id_raw = row.get('Image ID', '') or row.get('image_id', '')
    try:
        image_id = int(image_id_raw)
    except ValueError:
        image_id = image_id_raw
    return {
        "image_id": image_id,
        "image_path": row.get('image_path', '') or row.get('Image Path', ''),
        "image_resolution": row.get('image_resolution', '') or row.get('Image Resolution', ''),
        "question": row.get('question', '') or row.get('Question', ''),
        "response": row.get('response', '') or row.get('Response', ''),
        "rationale": row.get('rationale', '') or row.get('Rationale', ''),
        "view": row.get('view', '') or row.get('View', ''),
        "bbox": parse_bbox(row.get('bbox', '') or row.get('Bbox', ''))
    }


def copy_images(image_items, dest_folder):
    os.makedirs(dest_folder, exist_ok=True)
    copied = 0
    missing = []
    for item in image_items:
        image_path = item.get('image_path', '')
        filename = os.path.basename(image_path)
        if not filename:
            continue
        src = os.path.join(SOURCE_IMAGES_DIR, filename)
        dest = os.path.join(dest_folder, filename)
        if os.path.exists(src):
            shutil.copy2(src, dest)
            copied += 1
        else:
            missing.append(filename)
            print(f"[WARN] 이미지 파일을 찾을 수 없습니다: {src}")
    return copied, missing


def main():
    client = init_client()
    all_items = []
    counts = {}

    for sheet in TARGET_SHEETS:
        rows = read_sheet(client, sheet)
        passed_rows = []
        for row in rows:
            review_status = row.get('검수', '') or row.get('검수 상태', '')
            review_status = review_status.strip()
            if review_status != '통과':
                continue

            passed_rows.append(row)

            if sheet == '이주원' and len(passed_rows) >= LEEJUWON_LIMIT:
                break

        print(f"[INFO] {sheet}: 통과 {len(passed_rows)}개")
        counts[sheet] = len(passed_rows)
        all_items.extend(row_to_json(r) for r in passed_rows)

    total_count = len(all_items)
    print(f"[INFO] 총 개수: {total_count}개 (세부: {counts})")

    output_json = f"{OUTPUT_JSON_NAME}_{total_count}.json"
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)
    print(f"[INFO] JSON 저장 완료: {output_json}")

    dest_folder = os.path.join(os.getcwd(), f"{OUTPUT_JSON_NAME}_images_{total_count}")
    copied, missing = copy_images(all_items, dest_folder)
    print(f"[INFO] 이미지 복사: {copied}개 (대상 폴더: {dest_folder})")
    if missing:
        print(f"[WARN] 누락된 이미지 {len(missing)}개: {missing}")


if __name__ == '__main__':
    main()

