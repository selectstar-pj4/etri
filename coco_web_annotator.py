#!/usr/bin/env python3
"""
Web-based COCO Annotation Interface (Flask)
Web-based annotation tool that can be used on remote servers
"""

import argparse
import base64
import json
import os
import threading
import tempfile
import shutil
import time
from io import BytesIO
from datetime import datetime

from flask import Flask, render_template, request, jsonify, make_response
from PIL import Image
from pycocotools.coco import COCO
try:
    from openai import OpenAI
    from openai import RateLimitError
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# Gemini support removed - using OpenAI only
GEMINI_AVAILABLE = False

# Google Sheets 연동
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GOOGLE_SHEETS_AVAILABLE = True
    if __name__ == "__main__" or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        print(f"[DEBUG] gspread imported successfully from: {gspread.__file__}")
except ImportError as e:
    GOOGLE_SHEETS_AVAILABLE = False
    print(f"[WARN] gspread not installed. Google Sheets integration will be disabled.")
    print(f"[WARN] Import error: {e}")
    print(f"[DEBUG] Python path: {sys.executable}")
    print(f"[DEBUG] sys.path: {sys.path[:3]}")  # 처음 3개만 출력
    print("[INFO] Install with: pip install gspread google-auth")

import re
import sys

# 디버깅: Python 경로 출력
if __name__ == "__main__" or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    print(f"[DEBUG] Python executable: {sys.executable}")
    print(f"[DEBUG] Python version: {sys.version}")

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# 파일 저장을 위한 잠금 객체 (중복 데이터 방지)
file_locks = {
    'exo': threading.Lock(),
    'ego': threading.Lock()
}

# API Keys (config.py에서 로드, 없으면 환경변수 또는 기본값 사용)
try:
    from config import OPENAI_API_KEY, DEFAULT_MODEL
    # Google Sheets 설정 (선택사항)
    try:
        from config import GOOGLE_SHEETS_SPREADSHEET_ID, GOOGLE_SHEETS_CREDENTIALS_PATH
    except ImportError:
        GOOGLE_SHEETS_SPREADSHEET_ID = None
        GOOGLE_SHEETS_CREDENTIALS_PATH = None
    # 작업자 ID 설정 (선택사항)
    try:
        from config import WORKER_ID
    except ImportError:
        WORKER_ID = None
except ImportError:
    import os
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
    DEFAULT_MODEL = os.getenv('DEFAULT_MODEL', 'openai')
    GOOGLE_SHEETS_SPREADSHEET_ID = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID', None)
    GOOGLE_SHEETS_CREDENTIALS_PATH = os.getenv('GOOGLE_SHEETS_CREDENTIALS_PATH', None)
    WORKER_ID = os.getenv('WORKER_ID', None)
    if not OPENAI_API_KEY:
        print("[WARN] OpenAI API key not found. Please create config.py or set OPENAI_API_KEY environment variable.")

# 작업자 ID 출력
if WORKER_ID:
    print(f"[INFO] 작업자 ID: {WORKER_ID}")
else:
    print("[WARN] 작업자 ID가 설정되지 않았습니다. config.py에 WORKER_ID를 설정하세요.")

# Google Sheets 클라이언트 초기화
google_sheets_client = None
spreadsheet_cache = None  # 스프레드시트 객체 캐싱
spreadsheet_cache_lock = threading.Lock()  # 스레드 안전성을 위한 락

# Google Sheets 데이터 캐싱 (API 호출 최소화)
sheets_data_cache = {}  # {worker_id: {'data': [...], 'timestamp': float, 'lock': threading.Lock()}}
CACHE_TTL = 30  # 30초 캐시 유지 시간

# Google Sheets 데이터 캐싱 (API 호출 최소화)
sheets_data_cache = {}  # {worker_id: {'data': [...], 'timestamp': float, 'lock': threading.Lock()}}
CACHE_TTL = 30  # 30초 캐시 유지 시간

if GOOGLE_SHEETS_AVAILABLE and GOOGLE_SHEETS_SPREADSHEET_ID and GOOGLE_SHEETS_CREDENTIALS_PATH:
    try:
        if os.path.exists(GOOGLE_SHEETS_CREDENTIALS_PATH):
            scopes = ['https://www.googleapis.com/auth/spreadsheets']
            credentials = Credentials.from_service_account_file(
                GOOGLE_SHEETS_CREDENTIALS_PATH, scopes=scopes
            )
            google_sheets_client = gspread.authorize(credentials)
            print(f"[INFO] Google Sheets 연동 활성화: {GOOGLE_SHEETS_SPREADSHEET_ID}")
        else:
            print(f"[WARN] Google Sheets credentials 파일을 찾을 수 없습니다: {GOOGLE_SHEETS_CREDENTIALS_PATH}")
    except Exception as e:
        print(f"[WARN] Google Sheets 초기화 실패: {e}")
        google_sheets_client = None
elif GOOGLE_SHEETS_AVAILABLE:
    print("[INFO] Google Sheets 연동 비활성화 (설정 필요)")

def get_spreadsheet(force_refresh=False):
    """
    스프레드시트 객체를 캐싱하여 반환 (API 호출 최소화)
    
    Args:
        force_refresh: True이면 캐시를 무효화하고 새로 가져옴
    """
    global spreadsheet_cache
    if not google_sheets_client:
        return None
    
    with spreadsheet_cache_lock:
        if spreadsheet_cache is None or force_refresh:
            if force_refresh and spreadsheet_cache:
                print("[DEBUG] 스프레드시트 캐시 무효화")
                spreadsheet_cache = None
            
            try:
                spreadsheet_cache = google_sheets_client.open_by_key(GOOGLE_SHEETS_SPREADSHEET_ID)
                print("[DEBUG] 스프레드시트 객체 캐싱 완료")
            except gspread.exceptions.APIError as e:
                # APIError의 response는 requests.Response 객체이므로 status_code를 사용
                error_code = getattr(e.response, 'status_code', None)
                if error_code == 429:
                    # 429 에러는 조용히 처리 (로그 출력하지 않음)
                    # 캐시 무효화하여 다음 시도 시 재시도 가능하도록
                    spreadsheet_cache = None
                    return None
                raise
            except Exception as e:
                print(f"[ERROR] 스프레드시트 열기 실패: {e}")
                spreadsheet_cache = None
                return None
        return spreadsheet_cache

def clear_spreadsheet_cache():
    """스프레드시트 캐시 무효화"""
    global spreadsheet_cache
    with spreadsheet_cache_lock:
        spreadsheet_cache = None
        print("[DEBUG] 스프레드시트 캐시 클리어됨")

def clear_sheets_data_cache(worker_id=None):
    """
    Google Sheets 데이터 캐시 무효화
    
    Args:
        worker_id: 특정 작업자의 캐시만 무효화 (None이면 전체 무효화)
    """
    global sheets_data_cache
    if worker_id:
        if worker_id in sheets_data_cache:
            with sheets_data_cache[worker_id]['lock']:
                sheets_data_cache[worker_id]['timestamp'] = 0  # 캐시 만료 처리
                print(f"[DEBUG] {worker_id} 작업자의 데이터 캐시 무효화")
    else:
        # 전체 캐시 무효화
        for wid in list(sheets_data_cache.keys()):
            with sheets_data_cache[wid]['lock']:
                sheets_data_cache[wid]['timestamp'] = 0
        print("[DEBUG] 모든 작업자의 데이터 캐시 무효화")

class COCOWebAnnotator:
    """Web-based COCO annotation tool for creating question-response pairs."""
    
    def __init__(self, mscoco_folder, coco_json_path, output_json_path, categories_json_path=None, test_folder=None):
        # mscoco 폴더 경로 (exo_images와 ego_images가 있는 폴더)
        self.mscoco_folder = mscoco_folder
        # 테스트 폴더가 지정되면 사용, 아니면 기본 폴더 사용
        if test_folder:
            self.exo_images_folder = os.path.join(mscoco_folder, test_folder)
            self.ego_images_folder = os.path.join(mscoco_folder, 'ego_images')  # 테스트 시에도 ego는 기본 폴더
        else:
            self.exo_images_folder = os.path.join(mscoco_folder, 'exo_images')
            self.ego_images_folder = os.path.join(mscoco_folder, 'ego_images')
        self.coco_json_path = coco_json_path
        # output_json_path를 exo/ego로 분리
        output_dir = os.path.dirname(output_json_path) if os.path.dirname(output_json_path) else '.'
        output_basename = os.path.basename(output_json_path) if os.path.basename(output_json_path) else 'annotations.json'
        # 파일명에서 확장자 제거하고 exo/ego 접미사 추가
        if output_basename.endswith('.json'):
            base_name = output_basename[:-5]
        else:
            base_name = output_basename
        
        # 2-hop 저장 파일명으로 변경
        self.output_json_path_exo = os.path.join(output_dir, f'{base_name}_exo_2hop.json')
        self.output_json_path_ego = os.path.join(output_dir, f'{base_name}_ego_2hop.json')
        
        # Initialize COCO API
        self.coco = COCO(coco_json_path)
        all_image_ids = list(self.coco.imgs.keys())
        
        # 이미지 순서 정렬: exo_images 먼저, 그 다음 ego_images
        exo_image_ids = []
        ego_image_ids = []
        unknown_image_ids = []
        
        # test_folder가 지정되면 해당 폴더에 있는 이미지만 처리
        if test_folder:
            # test_folder에 있는 실제 파일 목록 가져오기
            test_folder_files = set()
            if os.path.exists(self.exo_images_folder):
                test_folder_files = set(os.listdir(self.exo_images_folder))
            
            for image_id in all_image_ids:
                image_info = self.coco.imgs[image_id]
                file_name = image_info.get('file_name', '')
                
                # test_folder에 있는 파일만 포함
                if file_name in test_folder_files:
                    exo_path = os.path.join(self.exo_images_folder, file_name)
                    if os.path.exists(exo_path):
                        exo_image_ids.append(image_id)
        else:
            # test_folder가 없으면 전체 이미지 순회
            for image_id in all_image_ids:
                image_info = self.coco.imgs[image_id]
                file_name = image_info.get('file_name', '')
                
                # exo_images 폴더에 있는지 확인
                exo_path = os.path.join(self.exo_images_folder, file_name)
                ego_path = os.path.join(self.ego_images_folder, file_name)
                
                if os.path.exists(exo_path):
                    exo_image_ids.append(image_id)
                elif os.path.exists(ego_path):
                    ego_image_ids.append(image_id)
                else:
                    # 둘 다 없으면 기본값으로 exo에 추가 (또는 unknown에 추가)
                    unknown_image_ids.append(image_id)
        
        # 이미지 ID를 파일명 순으로 정렬하는 함수
        def sort_by_filename(image_id_list):
            """이미지 ID 리스트를 파일명 순으로 정렬"""
            def get_filename(image_id):
                image_info = self.coco.imgs.get(image_id, {})
                return image_info.get('file_name', '')
            
            return sorted(image_id_list, key=get_filename)
        
        # test_folder가 지정되면 exo만, 아니면 exo + ego + unknown
        if test_folder:
            self.image_ids = sort_by_filename(exo_image_ids)
            print(f"[INFO] Test folder mode: {len(exo_image_ids)} images from {test_folder} (sorted by filename)")
        else:
            # exo 먼저, 그 다음 ego, 마지막에 unknown (각각 파일명 순으로 정렬)
            sorted_exo = sort_by_filename(exo_image_ids)
            sorted_ego = sort_by_filename(ego_image_ids)
            sorted_unknown = sort_by_filename(unknown_image_ids)
            self.image_ids = sorted_exo + sorted_ego + sorted_unknown
            print(f"[INFO] Image order: {len(exo_image_ids)} exo images, {len(ego_image_ids)} ego images, {len(unknown_image_ids)} unknown images (all sorted by filename)")

        # --- 추가: category id -> name 매핑 로드 ---
        self.category_id_to_name = {}
        if categories_json_path and os.path.exists(categories_json_path):
            try:
                with open(categories_json_path, 'r', encoding='utf-8') as f:
                    cats = json.load(f)
                    # cats가 [{"id": 74, "name": "mouse", ...}, ...] 형태라고 가정
                    for c in cats:
                        cid = c.get('id')
                        name = c.get('name')
                        if cid is not None and name:
                            self.category_id_to_name[int(cid)] = str(name)
            except Exception as e:
                print(f"[WARN] Failed to load categories_json: {e}")
        # pycocotools fallback
        if not self.category_id_to_name:
            # COCO의 카테고리 딕셔너리 사용
            for cid, info in self.coco.cats.items():
                self.category_id_to_name[int(cid)] = info.get('name', 'unknown')
        # -----------------------------------------
        
        # Load existing annotations (exo와 ego 모두 로드)
        self.annotations = []
        self._reload_annotations()
    
    def _reload_annotations(self):
        """Reload exo and ego annotations (called when needed)"""
        self.annotations = []
        # exo annotations 로드
        if os.path.exists(self.output_json_path_exo):
            try:
                with open(self.output_json_path_exo, 'r', encoding='utf-8') as f:
                    exo_anns = json.load(f)
                    self.annotations.extend(exo_anns)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                print(f"[WARN] Failed to load exo annotations: {e}")
        # ego annotations 로드
        if os.path.exists(self.output_json_path_ego):
            try:
                with open(self.output_json_path_ego, 'r', encoding='utf-8') as f:
                    ego_anns = json.load(f)
                    self.annotations.extend(ego_anns)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                print(f"[WARN] Failed to load ego annotations: {e}")
    

def get_vqa_json_by_filename(image_filename, coco_json_path, mscoco_folder=None, question=None, response=None, rationale=None, bbox=None, view=None):
    """
    이미지 파일명을 입력하면, COCO json에서 해당 이미지의 image_id/annotation/bbox/category를 찾아
    VQA Output 예시에 맞는 json(dict)을 반환합니다.
    question, response, rationale, bbox, view는 인자로 받아 그대로 사용합니다.
    결과는 파일로 저장하지 않고 dict로만 반환합니다.
    """
    import os
    from pycocotools.coco import COCO

    coco = COCO(coco_json_path)
    # 파일명 -> image_id, image_path 찾기
    image_id = None
    for img in coco.dataset["images"]:
        if img["file_name"] == image_filename:
            image_id = img["id"]
            break
    if image_id is None:
        raise ValueError(f"Image filename '{image_filename}' not found in COCO json.")

    # 상대 경로로 image_path 생성 (view가 있으면 사용, 없으면 기본값)
    view_type = view if view else 'exo'
    # mscoco 폴더명 추출
    if mscoco_folder:
        mscoco_folder_name = os.path.basename(os.path.normpath(mscoco_folder))
    else:
        mscoco_folder_name = 'mscoco'
    
    if view_type == 'ego':
        image_path = f"{mscoco_folder_name}/ego_images/{image_filename}"
    else:
        image_path = f"{mscoco_folder_name}/exo_images/{image_filename}"

    # bbox 자동/수동 입력: 입력값이 있으면 그대로, 없으면 전체 bbox 모두
    anns = coco.loadAnns(coco.getAnnIds(imgIds=image_id))
    all_bboxes = [a.get("bbox", []) for a in anns]
    bbox_out = bbox if bbox is not None else all_bboxes

    vqa_json = {
        "image_id": image_id,
        "image_path": image_path,  # 상대 경로로 변경
        "question": question if question is not None else "",
        "response": response if response is not None else "",
        "rationale": rationale if rationale is not None else "",
        "bbox": bbox_out,
        "view": view_type
    }
    return vqa_json

# Global annotator instance
annotator = None

# 이미지 분석 결과 캐시 (image_id를 키로 사용)
image_analysis_cache = {}

# idx 검색 라우트 추가 #
@app.route('/api/find/<int:image_id>')
def find_by_image_id(image_id):
    """Return dataset index for the given COCO image_id."""
    if annotator is None or not annotator.image_ids:
        return jsonify({'error': 'Annotator not initialized'}), 500
    try:
        idx = annotator.image_ids.index(image_id)
        
        # 납품완료된 이미지 필터링: 납품완료 상태인 이미지는 다음 이미지로 이동
        worker_id = request.args.get('worker_id') or WORKER_ID
        original_idx = idx
        max_iterations = len(annotator.image_ids)  # 무한 루프 방지
        
        while idx < len(annotator.image_ids) and max_iterations > 0:
            current_image_id = annotator.image_ids[idx]
            
            # 납품완료 상태 확인
            if google_sheets_client and worker_id:
                try:
                    sheet_data = read_from_google_sheets(worker_id)
                    is_completed = False
                    for row in sheet_data:
                        row_image_id = row.get('Image ID', '') or row.get('image_id', '')
                        if str(row_image_id) == str(current_image_id):
                            review_status = row.get('검수', '') or row.get('검수 상태', '')
                            # '납품 완료' 또는 '납품완료' (공백 유무 무관)
                            if review_status and ('납품 완료' in review_status or '납품완료' in review_status):
                                is_completed = True
                                print(f"[DEBUG] Image ID {current_image_id}는 납품완료 상태입니다. 다음 이미지로 이동합니다.")
                            break
                    
                    if is_completed:
                        # 납품완료된 이미지면 다음 이미지로 이동
                        idx += 1
                        max_iterations -= 1
                        continue
                except Exception as e:
                    print(f"[WARN] 납품완료 상태 확인 중 오류 (계속 진행): {e}")
            
            # 납품완료가 아닌 이미지를 찾았으면 루프 종료
            break
        
        # 모든 이미지가 납품완료인 경우
        if idx >= len(annotator.image_ids):
            return jsonify({'error': '모든 이미지가 납품 완료 상태입니다.'}), 404
        
        index_changed = (idx != original_idx)
        return jsonify({
            'index': idx, 
            'total': len(annotator.image_ids),
            'index_changed': index_changed,
            'original_index': original_idx
        })
    except ValueError:
        return jsonify({'error': f'Image ID {image_id} not found'}), 404

@app.route('/')
def index():
    """Render the main annotation interface."""
    response = make_response(render_template('index.html', worker_id=WORKER_ID))
    # 브라우저 캐시 방지
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/api/worker_id', methods=['GET'])
def get_worker_id():
    """Get worker ID from config."""
    return jsonify({'worker_id': WORKER_ID})

@app.route('/api/exo_image_indices', methods=['GET'])
def get_exo_image_indices():
    """Get list of all exo image indices (for batch processing) - 빠른 버전"""
    try:
        exo_indices = []
        for idx, image_id in enumerate(annotator.image_ids):
            image_info = annotator.coco.imgs[image_id]
            file_name = image_info.get('file_name', '')
            exo_path = os.path.join(annotator.exo_images_folder, file_name)
            if os.path.exists(exo_path):
                exo_indices.append(idx)
        
        return jsonify({
            'success': True,
            'exo_indices': exo_indices,
            'total': len(exo_indices)
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/image/<int:index>')
def get_image(index):
    """Get image information for a specific index."""
    if index >= len(annotator.image_ids):
        return jsonify({'error': 'Invalid index'}), 400
    
    # 납품완료된 이미지 필터링: 납품완료 상태인 이미지는 건너뛰기
    worker_id = request.args.get('worker_id') or WORKER_ID
    original_index = index
    max_iterations = len(annotator.image_ids)  # 무한 루프 방지
    
    while index < len(annotator.image_ids) and max_iterations > 0:
        image_id = annotator.image_ids[index]
        
        # 납품완료 상태 확인
        if google_sheets_client and worker_id:
            try:
                sheet_data = read_from_google_sheets(worker_id)
                is_completed = False
                for row in sheet_data:
                    row_image_id = row.get('Image ID', '') or row.get('image_id', '')
                    if str(row_image_id) == str(image_id):
                        review_status = row.get('검수', '') or row.get('검수 상태', '')
                        # '납품 완료' 또는 '납품완료' (공백 유무 무관)
                        if review_status and ('납품 완료' in review_status or '납품완료' in review_status):
                            is_completed = True
                            print(f"[DEBUG] Image ID {image_id}는 납품완료 상태입니다. 다음 이미지로 이동합니다.")
                        break
                
                if is_completed:
                    # 납품완료된 이미지면 다음 이미지로 이동
                    index += 1
                    max_iterations -= 1
                    continue
            except Exception as e:
                print(f"[WARN] 납품완료 상태 확인 중 오류 (계속 진행): {e}")
        
        # 납품완료가 아닌 이미지를 찾았으면 루프 종료
        break
    
    # 모든 이미지가 납품완료인 경우
    if index >= len(annotator.image_ids):
        return jsonify({'error': '모든 이미지가 납품완료 상태입니다.'}), 400
    
    # 인덱스가 변경되었으면 클라이언트에 알림
    index_changed = (index != original_index)
        
    image_id = annotator.image_ids[index]
    image_info = annotator.coco.imgs[image_id]
    
    # Get annotations for this image
    ann_ids = annotator.coco.getAnnIds(imgIds=image_id)
    annotations = annotator.coco.loadAnns(ann_ids)

    # === 새로 추가: bbox/카테고리 묶음 배열 ===
    anns_payload = []
    for ann in annotations:
        bbox = ann.get('bbox', [])
        cid = ann.get('category_id', None)
        name = annotator.category_id_to_name.get(int(cid), 'unknown') if cid is not None else 'unknown'
        anns_payload.append({
            'bbox': bbox,
            'category_id': cid,
            'category_name': name
        })

    # === 추가: category_names 채우기 ===
    category_names = []
    for ann in annotations:
        cid = ann.get('category_id', None)
        name = annotator.category_id_to_name.get(int(cid)) if cid is not None else None
        category_names.append(name if name else 'unknown')

    # Check existing annotations (exo와 ego 모두 확인)
    existing_annotation = None
    for ann in annotator.annotations:
        if ann['image_id'] == image_id:
            existing_annotation = ann
            break

    # Convert image to base64 for web display
    # 기존 annotation의 view 타입 확인하여 해당 폴더에서 이미지 로드
    view_type = 'exo'  # 기본값
    if existing_annotation:
        view_type = existing_annotation.get('view', 'exo')
    else:
        # annotations에서 찾기
        for ann in annotator.annotations:
            if ann.get('image_id') == image_id:
                view_type = ann.get('view', 'exo')
                break
    
    # 이미지 경로 찾기: 두 폴더 모두 확인 (view_type에 관계없이)
    alt_path_exo = os.path.join(annotator.exo_images_folder, image_info['file_name'])
    alt_path_ego = os.path.join(annotator.ego_images_folder, image_info['file_name'])
    
    # 먼저 view_type에 따라 시도
    if view_type == 'ego':
        image_path = alt_path_ego
    else:
        image_path = alt_path_exo
    
    # 이미지가 없으면 다른 폴더에서 시도
    if not os.path.exists(image_path):
        print(f"[WARN] Image not found at {image_path}, trying alternative paths...")
        
        # ego 폴더에서 찾기
        if os.path.exists(alt_path_ego):
            image_path = alt_path_ego
            view_type = 'ego'
            print(f"[INFO] Found image in ego_images: {image_path}")
        # exo 폴더에서 찾기
        elif os.path.exists(alt_path_exo):
            image_path = alt_path_exo
            view_type = 'exo'
            print(f"[INFO] Found image in exo_images: {image_path}")
        else:
            # 이미지를 찾을 수 없음
            error_msg = f"Image not found: {image_info['file_name']}\n"
            error_msg += f"Tried paths:\n"
            error_msg += f"  - {alt_path_exo} (exists: {os.path.exists(alt_path_exo)})\n"
            error_msg += f"  - {alt_path_ego} (exists: {os.path.exists(alt_path_ego)})"
            print(f"[ERROR] {error_msg}")
            return jsonify({'error': error_msg}), 500
    
    try:
        with Image.open(image_path) as img:
            original_width, original_height = img.size
            # Resize if too large but keep track of scale
            max_width, max_height = 800, 600
            scale = min(max_width/original_width, 
                       max_height/original_height, 1.0)
            if scale < 1.0:
                new_width = int(original_width * scale)
                new_height = int(original_height * scale)
                img = img.resize((new_width, new_height), 
                                Image.Resampling.LANCZOS)
            else:
                new_width, new_height = original_width, original_height
            
            buffer = BytesIO()
            img.save(buffer, format='JPEG')
            img_base64 = base64.b64encode(buffer.getvalue()).decode()
    except (IOError, OSError, ValueError) as e:
        return jsonify({'error': f'Failed to load image: {e}'}), 500
    # 납품완료된 이미지 개수 계산 (남은 이미지 계산을 위해)
    # exo_images와 ego_images 폴더 모두 확인
    completed_count = 0
    passed_count = 0
    total_all_images = 0
    remaining_count = 0
    
    # exo_images와 ego_images 폴더의 실제 파일 개수 계산
    exo_images_folder_path = annotator.exo_images_folder
    ego_images_folder_path = annotator.ego_images_folder
    
    total_exo_images = 0
    total_ego_images = 0
    
    if os.path.exists(exo_images_folder_path):
        try:
            exo_files = [f for f in os.listdir(exo_images_folder_path) 
                         if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp'))]
            total_exo_images = len(exo_files)
            print(f"[DEBUG] exo_images 폴더의 이미지 개수: {total_exo_images}")
        except Exception as e:
            print(f"[ERROR] exo_images 폴더 읽기 실패: {e}")
            total_exo_images = 0
    else:
        print(f"[DEBUG] exo_images 폴더 경로 확인: {exo_images_folder_path}")
        print(f"[DEBUG] exo_images 폴더 존재 여부: {os.path.exists(exo_images_folder_path)}")
    
    if os.path.exists(ego_images_folder_path):
        try:
            ego_files = [f for f in os.listdir(ego_images_folder_path) 
                         if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp'))]
            total_ego_images = len(ego_files)
            print(f"[DEBUG] ego_images 폴더의 이미지 개수: {total_ego_images}")
        except Exception as e:
            print(f"[ERROR] ego_images 폴더 읽기 실패: {e}")
            total_ego_images = 0
    else:
        print(f"[DEBUG] ego_images 폴더 경로 확인: {ego_images_folder_path}")
        print(f"[DEBUG] ego_images 폴더 존재 여부: {os.path.exists(ego_images_folder_path)}")
    
    # 전체 이미지 개수 = exo + ego (중복 제거는 하지 않음, 각 폴더의 파일 개수 합산)
    total_all_images = total_exo_images + total_ego_images
    
    if google_sheets_client and worker_id:
        try:
            sheet_data = read_from_google_sheets(worker_id)
            print(f"[DEBUG] 구글시트에서 읽은 전체 이미지 개수: {len(sheet_data)}")
            
            if len(sheet_data) > 0:
                # 구글시트에서 모든 이미지 확인 (view 필터링 없음)
                all_sheet_images = 0
                for row in sheet_data:
                    row_image_id = row.get('Image ID', '') or row.get('image_id', '')
                    
                    # 모든 이미지 처리 (view 필터링 없음)
                    if row_image_id:
                        all_sheet_images += 1
                        review_status = row.get('검수', '') or row.get('검수 상태', '')
                        if review_status == '납품 완료':
                            completed_count += 1
                        elif review_status == '통과':
                            passed_count += 1
                
                # 전체 이미지 폴더 개수와 구글시트의 이미지 개수 중 큰 값 사용
                if total_all_images > 0:
                    # 남은 이미지 개수 = 전체 폴더 이미지 - 통과 - 납품완료
                    remaining_count = total_all_images - passed_count - completed_count
                else:
                    # 폴더 개수를 알 수 없으면 구글시트의 이미지 개수 사용
                    remaining_count = all_sheet_images - passed_count - completed_count
                
                if remaining_count < 0:
                    remaining_count = 0
                
                print(f"[DEBUG] 남은 이미지 계산: 전체폴더={total_all_images}(exo={total_exo_images}, ego={total_ego_images}), 구글시트={all_sheet_images}, 통과={passed_count}, 납품완료={completed_count}, 남은={remaining_count}")
            else:
                # 구글시트 데이터가 없으면 전체 이미지 폴더 개수 사용
                if total_all_images > 0:
                    remaining_count = total_all_images
                    print(f"[INFO] 구글시트 데이터가 없습니다. 전체 이미지 폴더 개수 사용: {remaining_count}")
                else:
                    print(f"[WARN] 구글시트 데이터도 없고 이미지 폴더도 찾을 수 없습니다.")
        except Exception as e:
            # 429 에러는 조용히 처리 (로그 최소화)
            if hasattr(e, 'response') and getattr(e.response, 'status_code', None) == 429:
                pass  # 429 에러는 로그 출력하지 않음
            else:
                print(f"[WARN] 납품완료/통과 개수 계산 중 오류: {e}")
            # 에러 발생 시 전체 이미지 폴더 개수 사용
            if total_all_images > 0:
                remaining_count = total_all_images
    else:
        # 구글시트 클라이언트가 없으면 전체 이미지 폴더 개수 사용
        if total_all_images > 0:
            remaining_count = total_all_images
            print(f"[INFO] 구글시트 클라이언트가 없습니다. 전체 이미지 폴더 개수 사용: {remaining_count}")
        else:
            print(f"[WARN] 구글시트 클라이언트도 없고 이미지 폴더도 찾을 수 없습니다.")
    
    # 최종 검증: remaining_count가 비정상적으로 크면 0으로 설정
    if remaining_count > 100000:
        print(f"[WARN] 남은 이미지 개수가 비정상적으로 큽니다: {remaining_count}, 전체 이미지 폴더 개수로 재계산합니다.")
        if total_all_images > 0:
            remaining_count = total_all_images
        else:
            remaining_count = 0
    
    return jsonify({
        'image_id': image_id,
        'image_data': f'data:image/jpeg;base64,{img_base64}',
        'width': image_info['width'],
        'height': image_info['height'],
        'display_width': new_width,
        'display_height': new_height,
        'scale': scale,
        'file_name': image_info['file_name'],
        'bboxes': [ann['bbox'] for ann in annotations],
        'categories': [ann.get('category_id', 0) for ann in annotations],
        'category_names': category_names,  # <<< 추가
        'anns': anns_payload, # <<< 추가
        'existing_annotation': existing_annotation,
        'view_type': view_type,  # 이미지가 있는 폴더에 따라 결정된 view 타입
        'current_index': index,
        'total_images': total_ego_images,  # ego_images 폴더의 전체 이미지 개수
        'remaining_images': remaining_count,  # 남은 이미지 개수
        'index_changed': index_changed,  # 인덱스가 변경되었는지 여부
        'original_index': original_index  # 원래 요청한 인덱스
    })

@app.route('/api/translate/question', methods=['POST'])
def translate_question():
    """Translate Korean question to English using GPT-5."""
    data = request.json
    question_ko = data.get('question_ko', '').strip()
    view_type = data.get('view_type', 'exo')  # 'exo' or 'ego'
    
    if not question_ko:
        return jsonify({'success': False, 'error': 'Question (Korean) is required'}), 400
    
    try:
        if not OPENAI_AVAILABLE:
            return jsonify({'success': False, 'error': 'OpenAI library not installed. Install with: pip install openai'}), 500
        
        # OpenAI API 호출 (코드에서 직접 API 키 사용)
        if not OPENAI_API_KEY or OPENAI_API_KEY == "your-api-key-here":
            return jsonify({'success': False, 'error': 'OPENAI_API_KEY is not set. Please set it in coco_web_annotator.py'}), 500
        
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # view_type에 따라 다른 프롬프트 사용
        if view_type == 'ego':
            # ego_data_sample.json 형식 참고
            prompt = f"""Translate the following Korean question to English. You MUST follow this EXACT format for EGO-CENTRIC questions.

═══════════════════════════════════════════════════════════════════════════════
📋 TRANSLATION RULES - EGO-CENTRIC QUESTIONS (2-hop)
═══════════════════════════════════════════════════════════════════════════════

**FORMAT**: [Question with EXACTLY TWO tags from: (POS+REL), (ATT+REL), (POS+ATT)] <choice>(a) option1, (b) option2, (c) option3, (d) option4</choice> And provide the bounding box coordinate of the region related to your answer.

🚨 CRITICAL: NEVER include the third tag. Use EXACTLY TWO tags only.

═══════════════════════════════════════════════════════════════════════════════
STEP 1: EGO-CENTRIC QUESTION STARTING PHRASES
═══════════════════════════════════════════════════════════════════════════════
1. If the Korean question contains "~관점에서" (from the perspective of ~):
   → Translate to: "From the perspective of [person/object], ..."
   Example: "작은 소녀의 관점에서" → "From the perspective of the little girl, ..."

2. If the Korean question contains "내가" or "I'm" (when I am in the image):
   → Translate to: "When I'm [action/position], ..."
   Examples:
   - "내가 소파 오른쪽에 앉아 있을 때" → "When I'm sitting on the right side of the sofa, ..."
   - "내가 의자에 앉아 있을 때" → "When I'm sitting on the chair, ..."
   - "내가 테이블 앞에 서 있을 때" → "When I'm standing in front of the table, ..."

CRITICAL TAG USAGE RULES (2-hop):

0. TAG COUNT RULE - EGO:
   - Use EXACTLY TWO tags per question.
   - Allowed pairs ONLY: (POS+REL), (ATT+REL), (POS+ATT).
   - DO NOT include the third tag. NO 3-tag questions.

1. <REL> tag - Use ONLY for RELATIONSHIP terms (distance, order, placement):
   - Examples: "farthest", "closest", "second-closest", "highest in position"
   - DO NOT use for objects or locations

2. <POS> tag - Use ONLY for POSITION/LOCATION information from the perspective:
   - Examples: "on the left side", "on the right side", "in front of", "behind", "to the left of", "to the right of"
   - DO NOT use for object attributes or relationships
   - DO NOT use generic phrases like "in the image"
   - Remember: In ego-centric questions, "left/right" are from the person's perspective

3. <ATT> tag - Use ONLY for ATTRIBUTES or TARGET GROUPS:
   - Examples: "round object", "green object", "white object", "rectangular object", "party item", "furry creature"
   - Use for describing WHAT object/group is being asked about
   
🚨 CRITICAL - <ATT> TAG USAGE RULES:
   - ✅ **USE <ATT> TAG**: When Korean question contains objects WITH modifiers (수식어가 붙은 객체)
     * "흰색 객체" (white object) → "<ATT>white object</ATT>"
     * "빨간색 객체" (red object) → "<ATT>red object</ATT>"
     * "원형 객체" (round object) → "<ATT>round object</ATT>"
     * "정사각형 객체" (square object) → "<ATT>square object</ATT>"
     * "식용 가능한 물체" (edible item) → "<ATT>edible item</ATT>"
     * "밝은 색상의 객체" (bright colored object) → "<ATT>bright colored object</ATT>"
   - ❌ **DO NOT USE <ATT> TAG**: When Korean question contains plain "객체" (object), "물체" (item) WITHOUT modifiers
     * "객체" (object) → just "object" (NO <ATT> tag)
     * "물체" (item) → just "item" (NO <ATT> tag)
   - WRONG: "which <ATT>object</ATT>" (plain object without modifier)
   - CORRECT: "which object" (no ATT tag for plain object)
   - WRONG: "which white object" (missing <ATT> tag for object with modifier)
   - CORRECT: "which <ATT>white object</ATT>"

Reference examples from ego_data_sample.json (2-hop format, two tags only):

Example 1 (ATT+REL): "From the perspective of the little girl, which <ATT>party item</ATT> is <REL>farthest</REL> from her? <choice>(a) cake, (b) camera, (c) party plate, (d) flower</choice> And provide the bounding box coordinate of the region related to your answer."

Example 2 (POS+ATT): "When I'm sitting on the right side of the large sofa, which <ATT>square or rectangular object</ATT> is <POS>on the right side of the room</POS>? <choice>(a) fan, (b) large bottle, (c) shoe, (d) tv</choice> And provide the bounding box coordinate of the region related to your answer."

Example 3 (POS+REL): "From the perspective of the woman, which object <POS>to the right of</POS> her is <REL>closest to her</REL>? <choice>(a) fork, (b) knife, (c) spoon, (d) wine glass</choice> And provide the bounding box coordinate of the region related to your answer."

Korean question: {question_ko}

═══════════════════════════════════════════════════════════════════════════════
STEP 4: TRANSLATION VERIFICATION CHECKLIST
═══════════════════════════════════════════════════════════════════════════════

**BEFORE FINALIZING, VERIFY EACH STEP**:

1. **TAG COUNT VERIFICATION** (MOST IMPORTANT):
   [ ] Count <ATT> tags → Must be 0 or 1
   [ ] Count <POS> tags → Must be 0 or 1
   [ ] Count <REL> tags → Must be 0 or 1
   [ ] Total tag count → Must be EXACTLY 2
   [ ] Tag pair is in allowed list: (POS+REL), (ATT+REL), (POS+ATT)

2. **ATT TAG DECISION VERIFICATION**:
   [ ] Does Korean contain "객체" or "물체"?
      → If NO: No ATT tag needed
      → If YES: Check step 3
   [ ] Is there a modifier BEFORE "객체/물체"?
      → Examples: "빨간색", "원형", "나무", "식용 가능한"
      → If YES: MUST use <ATT> tag
      → If NO: DO NOT use <ATT> tag

3. **TRANSLATION QUALITY**:
   [ ] Use "From the perspective of ~" if Korean contains "~관점에서"
   [ ] Use "When I'm ~" if Korean contains "내가" or "I'm"
   [ ] <choice> tag comes before "And provide..." phrase
   [ ] All tags have meaningful content inside them

**FINAL CHECK**:
- ✅ EXACTLY 2 tags used (no more, no less)
- ✅ ATT tag used ONLY for objects WITH modifiers
- ✅ ATT tag NOT used for plain "객체" or "물체"
- ✅ Tag pair matches allowed combinations

Korean question: {question_ko}

Translate to English following the EXACT format and verification checklist above."""
        else:
            # exo_data_sample.json 형식 참고
            prompt = f"""Translate the following Korean question to English. You MUST follow this EXACT format:

CORRECT FORMAT (2-hop: EXACTLY TWO TAGS):
[Question with EXACTLY TWO tags chosen only from these pairs: (ATT+REL), (POS+REL), (POS+ATT)] <choice>(a) option1, (b) option2, (c) option3, (d) option4</choice> And provide the bounding box coordinate of the region related to your answer. 🚨 NEVER include the third tag.

CRITICAL TAG USAGE RULES (2-hop):

0. TAG COUNT RULE - EXO:
   - Use EXACTLY TWO tags per question.
   - Allowed pairs ONLY: (ATT+REL), (POS+REL), (POS+ATT).
   - DO NOT include the third tag. NO 3-tag questions.

1. <REL> tag - Use ONLY for RELATIONSHIP terms (distance, order, placement):
   - Examples: "farthest", "closest", "second-closest", "placed on the floor"
   - DO NOT use for objects or locations

2. <POS> tag - Use ONLY for POSITION/LOCATION information:
   - Examples: "in the center", "on the left side of", "in front of", "to the left side", "on the right side"
   - DO NOT use for object attributes or relationships
   - DO NOT use generic phrases like "in the image"

3. <ATT> tag - Use ONLY for ATTRIBUTES or TARGET GROUPS:
   - Examples: "red object", "square-shaped item", "among the items", "among the visible people", "edible food item", "white object", "round object"
   - Use for describing WHAT object/group is being asked about
   
🚨 CRITICAL - <ATT> TAG USAGE RULES:
   - ✅ **USE <ATT> TAG**: When Korean question contains objects WITH modifiers (수식어가 붙은 객체)
     * "흰색 객체" (white object) → "<ATT>white object</ATT>"
     * "빨간색 객체" (red object) → "<ATT>red object</ATT>"
     * "원형 객체" (round object) → "<ATT>round object</ATT>"
     * "정사각형 객체" (square object) → "<ATT>square object</ATT>"
     * "식용 가능한 물체" (edible item) → "<ATT>edible item</ATT>"
     * "밝은 색상의 객체" (bright colored object) → "<ATT>bright colored object</ATT>"
   - ❌ **DO NOT USE <ATT> TAG**: When Korean question contains plain "객체" (object), "물체" (item) WITHOUT modifiers
     * "객체" (object) → just "object" (NO <ATT> tag)
     * "물체" (item) → just "item" (NO <ATT> tag)
   - WRONG: "which <ATT>object</ATT>" (plain object without modifier)
   - CORRECT: "which object" (no ATT tag for plain object)
   - WRONG: "which white object" (missing <ATT> tag for object with modifier)
   - CORRECT: "which <ATT>white object</ATT>"

Reference examples from exo_data_sample.json (2-hop format, two tags only):
- Example 1 (POS+REL): "Which object <POS>in the center</POS> of the countertop is <REL>second-closest</REL> to the refrigerator? <choice>(a) sink, (b) vase, (c) orange bag, (d) rightmost red chair</choice> And provide the bounding box coordinate of the region related to your answer."
- Example 2 (ATT+REL): "Which <ATT>square-shaped item</ATT> is <REL>placed on the floor</REL>? <choice>(a) handbag, (b) coke, (c) laptop, (d) cell phone</choice> And provide the bounding box coordinate of the region related to your answer."

Korean question: {question_ko}

═══════════════════════════════════════════════════════════════════════════════
STEP 4: TRANSLATION VERIFICATION CHECKLIST
═══════════════════════════════════════════════════════════════════════════════

**BEFORE FINALIZING, VERIFY EACH STEP**:

1. **TAG COUNT VERIFICATION** (MOST IMPORTANT):
   [ ] Count <ATT> tags → Must be 0 or 1
   [ ] Count <POS> tags → Must be 0 or 1
   [ ] Count <REL> tags → Must be 0 or 1
   [ ] Total tag count → Must be EXACTLY 2
   [ ] Tag pair is in allowed list: (ATT+REL), (POS+REL), (POS+ATT)

2. **ATT TAG DECISION VERIFICATION**:
   [ ] Does Korean contain "객체" or "물체"?
      → If NO: No ATT tag needed
      → If YES: Check step 3
   [ ] Is there a modifier BEFORE "객체/물체"?
      → Examples: "빨간색", "원형", "나무", "식용 가능한"
      → If YES: MUST use <ATT> tag
      → If NO: DO NOT use <ATT> tag

3. **TRANSLATION QUALITY**:
   [ ] <choice> tag comes before "And provide..." phrase
   [ ] All tags have meaningful content inside them
   [ ] DO NOT use generic phrases like "in the image" for <POS> tag

**FINAL CHECK**:
- ✅ EXACTLY 2 tags used (no more, no less)
- ✅ ATT tag used ONLY for objects WITH modifiers
- ✅ ATT tag NOT used for plain "객체" or "물체"
- ✅ Tag pair matches allowed combinations

Korean question: {question_ko}

Translate to English following the EXACT format and verification checklist above."""
        
        # view_type에 따라 다른 시스템 메시지 사용
        if view_type == 'ego':
            system_message = "You are a professional translator specializing in VQA (Visual Question Answering) EGO-CENTRIC questions. CRITICAL RULES: 1) EXACTLY TWO TAGS per question, allowed pairs ONLY (POS+REL), (ATT+REL), (POS+ATT) — NEVER include the third tag, 2) Use 'From the perspective of ~' for '~관점에서', 3) Use 'When I'm ~' for '내가', 4) <REL> tag ONLY for relationship terms (farthest, closest, etc.), 5) <POS> tag ONLY for position/location from person's perspective (on the left side, on the right side, etc.), 6) <ATT> tag ONLY for attributes/target groups (round object, green object, white object, person, etc.), 7) 🚨 MANDATORY: If Korean contains ANY attribute word (color, shape, material, '사람', '객체', '물체'), you MUST use <ATT> tag, 8) 🚨 MANDATORY: If Korean ends with '~사람은?' or '~객체는?', you MUST include <ATT> tag, 9) Tags MUST contain actual meaningful content, 10) Format: [Question with tags] <choice>...</choice> And provide..., 11) DO NOT use generic phrases like 'in the image' for <POS> tag, 12) DOUBLE-CHECK: Verify ALL attribute descriptions are wrapped in <ATT> tags and ONLY TWO TAGS are present from allowed pairs."
        else:
            system_message = "You are a professional translator specializing in VQA (Visual Question Answering) questions. CRITICAL RULES: 1) EXACTLY TWO TAGS per question, allowed pairs ONLY (ATT+REL), (POS+REL), (POS+ATT) — NEVER include the third tag, 2) <REL> tag ONLY for relationship terms (farthest, closest, etc.), 3) <POS> tag ONLY for position/location (in the center, on the left side, etc.), 4) <ATT> tag ONLY for attributes/target groups (red object, white object, among the items, person, etc.), 5) 🚨 MANDATORY: If Korean contains ANY attribute word (color, shape, material, '사람', '객체', '물체'), you MUST use <ATT> tag, 6) 🚨 MANDATORY: If Korean ends with '~사람은?' or '~객체는?', you MUST include <ATT> tag, 7) Tags MUST contain actual meaningful content, 8) Format: [Question with tags] <choice>...</choice> And provide..., 9) DO NOT use generic phrases like 'in the image' for <POS> tag, 10) DOUBLE-CHECK: Verify ALL attribute descriptions are wrapped in <ATT> tags and ONLY TWO TAGS are present from allowed pairs."
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        
        translated_question = response.choices[0].message.content.strip()
        
        # 태그 검증
        if '<ATT>' not in translated_question and '<POS>' not in translated_question and '<REL>' not in translated_question:
            return jsonify({'success': False, 'error': 'Translation must include at least one of <ATT>, <POS>, or <REL> tags'}), 400
        
        # ATT 태그 누락 검증: 질문에서 찾는 대상(객체)에 속성이 있는지 확인
        # 단순히 키워드가 있는지만 확인하는 것이 아니라, 질문의 끝 부분(찾는 대상)에 수식어가 있는지 확인
        # 예: "파란색 청바지를 입은 사람 오른쪽에 있는 가장 높은 객체" → "가장 높은 객체"가 찾는 대상이므로 ATT 필요 없음
        # 예: "파란색 청바지를 입은 사람 오른쪽에 있는 빨간색 객체" → "빨간색 객체"가 찾는 대상이므로 ATT 필요
        
        # 질문 끝 부분에서 "~객체", "~물체" 패턴 찾기
        object_pattern = r'([가-힣\s]+(?:객체|물체|항목))'
        matches = re.findall(object_pattern, question_ko)
        
        # 질문 끝 부분의 객체 표현 확인
        question_has_target_attribute = False
        if matches:
            # 마지막 매치(질문의 끝 부분) 확인
            last_object_phrase = matches[-1].strip()
            # 수식어가 있는지 확인 (색상, 형태, 재질 등)
            attribute_modifiers = ['흰색', '빨간색', '파란색', '초록색', '검은색', '노란색', '원형', '정사각형', '직사각형', '사각형', '밝은', '어두운', '나무', '금속', '식용', '밝은 색상', '어두운 색상']
            for modifier in attribute_modifiers:
                if modifier in last_object_phrase and ('객체' in last_object_phrase or '물체' in last_object_phrase or '항목' in last_object_phrase):
                    question_has_target_attribute = True
                    break
        
        # 질문에서 찾는 대상에 속성이 있는데 ATT 태그가 없는 경우에만 에러
        if question_has_target_attribute and '<ATT>' not in translated_question:
            return jsonify({
                'success': False, 
                'error': f'ATT tag is missing! Korean question contains attribute words in the target object phrase ("{last_object_phrase}") but translation lacks <ATT> tag. Please ensure all attribute descriptions for the target object are wrapped in <ATT> tags. Translation: {translated_question[:200]}...'
            }), 400
        
        if '<choice>' not in translated_question:
            return jsonify({'success': False, 'error': 'Translation must include <choice> tag'}), 400
        
        if 'And provide the bounding box coordinate of the region related to your answer.' not in translated_question:
            return jsonify({'success': False, 'error': 'Translation must end with the required phrase'}), 400
        
        return jsonify({
            'success': True,
            'translated_question': translated_question
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/translate/choices', methods=['POST'])
def translate_choices():
    """Translate Korean choices to English and format as <choice> tag."""
    data = request.json
    choice_a = data.get('choice_a', '').strip()
    choice_b = data.get('choice_b', '').strip()
    choice_c = data.get('choice_c', '').strip()
    choice_d = data.get('choice_d', '').strip()
    
    if not all([choice_a, choice_b, choice_c, choice_d]):
        return jsonify({'success': False, 'error': 'All choices are required'}), 400
    
    try:
        if not OPENAI_AVAILABLE:
            return jsonify({'success': False, 'error': 'OpenAI library not installed. Install with: pip install openai'}), 500
        
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            return jsonify({'success': False, 'error': 'OPENAI_API_KEY environment variable not set'}), 500
        
        client = OpenAI(api_key=api_key)
        
        prompt = f"""Translate the following Korean multiple choice options to English. Use concise, intuitive adjective+noun or noun+noun format (NOT full sentences).

CRITICAL FORMATTING RULES:
- Use concise, intuitive format: adjective + noun or noun + noun
- Examples:
  * "a person in a black shirt" → "black shirt person"
  * "a person wearing glasses" → "glasses person"
  * "a cup on the table" → "table cup" or "cup"
  * "a red chair" → "red chair"
  * "a man with a blue t-shirt" → "blue t-shirt man"
- DO NOT use full sentences like "a person who is wearing a black shirt"
- DO NOT use articles "a" or "the" unless necessary
- Keep it short and intuitive

Korean choices:
(a) {choice_a}
(b) {choice_b}
(c) {choice_c}
(d) {choice_d}

Translate each option to English and format as: <choice>(a) translated_a, (b) translated_b, (c) translated_c, (d) translated_d</choice>

Return only the formatted <choice> tag with translations."""
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a translator specializing in concise, intuitive translations for multiple choice options. CRITICAL: Use adjective+noun or noun+noun format (e.g., 'black shirt person', 'glasses person'), NOT full sentences. Keep translations short and intuitive."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        
        translated_choices = response.choices[0].message.content.strip()
        
        # <choice> 태그 추출
        choice_match = re.search(r'<choice>(.*?)</choice>', translated_choices, re.IGNORECASE)
        if not choice_match:
            return jsonify({'success': False, 'error': 'Translation must include <choice> tag'}), 400
        
        choice_content = choice_match.group(1)
        # 각 선택지 텍스트 추출
        choice_texts = {}
        for letter in ['a', 'b', 'c', 'd']:
            pattern = rf'\({letter}\)\s*([^,)]+)'
            match = re.search(pattern, choice_content, re.IGNORECASE)
            if match:
                choice_texts[letter] = match.group(1).strip()
        
        return jsonify({
            'success': True,
            'translated_choices': translated_choices,
            'choice_texts': choice_texts
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# Gemini functions removed - using OpenAI only

def analyze_image_with_model(image_base64, model='openai', image_path=None):
    """이미지 분석을 모델별로 수행하는 헬퍼 함수"""
    analysis_prompt = """Analyze this image in detail and extract specific visual features. Focus on:

1. **Objects with detailed attributes**: 
   - Color (e.g., "yellow cup", "red chair", "blue bag")
   - Size/shape (e.g., "large square table", "small round plate")
   - Material/texture (e.g., "wooden shelf", "glass window", "metal door")

2. **Spatial relationships and positions**:
   - Location (e.g., "book on the shelf", "cup on the table", "person in the corner")
   - Relative positions (e.g., "left side of the image", "center of the room", "right edge")
   - Orientation (e.g., "person facing right", "door opening left", "chair tilted")

3. **Detailed object descriptions**:
   - Specific features (e.g., "person wearing glasses", "book with red cover", "chair with armrests")
   - States/conditions (e.g., "open door", "closed window", "empty cup")
   - Interactions (e.g., "person holding cup", "book placed on shelf")

4. **Spatial context**:
   - Room/space type (e.g., "kitchen", "living room", "office")
   - Layout information (e.g., "countertop in center", "refrigerator on left side")
   - Distance relationships (e.g., "closest to camera", "farthest from door")

Provide a comprehensive but concise description that captures these detailed visual features. Format the output as structured text that can be used to understand spatial relationships and object attributes for VQA (Visual Question Answering) tasks."""
    
    if model == 'openai' or model == 'gpt':
        if not OPENAI_AVAILABLE:
            raise Exception('OpenAI library not installed. Install with: pip install openai')
        if not OPENAI_API_KEY:
            raise Exception('OPENAI_API_KEY is not set')
        
        
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": analysis_prompt
                }, {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
                }]
            }],
            temperature=0.3,
            max_tokens=1000
        )
        return response.choices[0].message.content.strip()
    
    else:
        raise Exception(f'Unknown model: {model}. Supported models: "openai", "gpt"')

@app.route('/api/analyze_image/<int:index>', methods=['GET'])
def analyze_image(index):
    """Analyze image using GPT-4o to extract detailed features."""
    if index >= len(annotator.image_ids):
        return jsonify({'error': 'Invalid index'}), 400
    
    image_id = annotator.image_ids[index]
    model = request.args.get('model', DEFAULT_MODEL).lower()
    
    # 캐시 확인 (모델별 캐시 키)
    cache_key = f"{image_id}_{model}"
    if cache_key in image_analysis_cache:
        return jsonify({
            'success': True,
            'image_id': image_id,
            'analysis': image_analysis_cache[cache_key],
            'cached': True,
            'model': model
        })
    
    try:
        
        # 이미지 로드 및 base64 변환
        image_info = annotator.coco.imgs[image_id]
        
        # Check existing annotations to determine view type
        existing_annotation = None
        for ann in annotator.annotations:
            if ann['image_id'] == image_id:
                existing_annotation = ann
                break
        
        view_type = 'exo'
        if existing_annotation:
            view_type = existing_annotation.get('view', 'exo')
        
        if view_type == 'ego':
            image_path = os.path.join(annotator.ego_images_folder, image_info['file_name'])
        else:
            image_path = os.path.join(annotator.exo_images_folder, image_info['file_name'])
        
        if not os.path.exists(image_path):
            alt_path_exo = os.path.join(annotator.exo_images_folder, image_info['file_name'])
            alt_path_ego = os.path.join(annotator.ego_images_folder, image_info['file_name'])
            if os.path.exists(alt_path_exo):
                image_path = alt_path_exo
            elif os.path.exists(alt_path_ego):
                image_path = alt_path_ego
            else:
                return jsonify({'success': False, 'error': f'Image not found: {image_info["file_name"]}'}), 404
        
        # 이미지를 base64로 변환 (분석용으로는 원본 크기 사용, 최대 1024x1024로 리사이즈)
        with Image.open(image_path) as img:
            original_width, original_height = img.size
            # Vision API는 최대 20MP까지 지원하지만, 토큰 절약을 위해 리사이즈
            max_size = 1024
            if original_width > max_size or original_height > max_size:
                scale = min(max_size/original_width, max_size/original_height)
                new_width = int(original_width * scale)
                new_height = int(original_height * scale)
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            buffer = BytesIO()
            img.save(buffer, format='JPEG', quality=85)
            img_base64 = base64.b64encode(buffer.getvalue()).decode()
        
        # 모델별 이미지 분석 수행 (CLIP-2 통합 지원)
        analysis_result = analyze_image_with_model(img_base64, model, image_path)
        
        # 캐시에 저장 (모델별 키 사용)
        image_analysis_cache[cache_key] = analysis_result
        
        return jsonify({
            'success': True,
            'image_id': image_id,
            'analysis': analysis_result,
            'cached': False,
            'model': model
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/generate_question_and_choices', methods=['POST'])
def generate_question_and_choices():
    """Generate Korean question and choices using GPT-4o, after image analysis with GPT-4o."""
    data = request.json
    image_id = data.get('image_id', None)
    index = data.get('index', None)
    # 기본값은 DEFAULT_MODEL 사용
    model = data.get('model', DEFAULT_MODEL).lower()
    
    if image_id is None and index is None:
        return jsonify({'success': False, 'error': 'image_id or index is required'}), 400
    
    try:
        # image_id가 없으면 index로 찾기
        if image_id is None:
            if index >= len(annotator.image_ids):
                return jsonify({'error': 'Invalid index'}), 400
            image_id = annotator.image_ids[index]
        
        # 1단계: 이미지 분석 (선택한 모델 사용) - 캐시 확인 또는 실행
        image_analysis = ""
        image_path = None  # image_path 초기화
        cache_key = f"{image_id}_{model}"
        if cache_key in image_analysis_cache:
            image_analysis = image_analysis_cache[cache_key]
        else:
            # 이미지 분석 API 호출 (캐시에 없으면 실행)
            # index 찾기
            if index is None:
                for idx, img_id in enumerate(annotator.image_ids):
                    if img_id == image_id:
                        index = idx
                        break
                if index is None:
                    return jsonify({'success': False, 'error': 'Image not found'}), 404
            
            # analyze_image 함수 로직 재사용
            image_info = annotator.coco.imgs[image_id]
            existing_annotation = None
            for ann in annotator.annotations:
                if ann['image_id'] == image_id:
                    existing_annotation = ann
                    break
            
            view_type = 'exo'
            if existing_annotation:
                view_type = existing_annotation.get('view', 'exo')

            # 2-hop 태그 조합 안내 문구 (view_type별 허용 조합)
            if view_type == 'ego':
                allowed_tag_pairs = "(POS+REL), (ATT+REL), (POS+ATT)  # exactly two tags, NEVER the third"
            else:
                allowed_tag_pairs = "(ATT+REL), (POS+REL), (POS+ATT)  # exactly two tags, NEVER the third"
            
            if view_type == 'ego':
                image_path = os.path.join(annotator.ego_images_folder, image_info['file_name'])
            else:
                image_path = os.path.join(annotator.exo_images_folder, image_info['file_name'])
            
            if not os.path.exists(image_path):
                alt_path_exo = os.path.join(annotator.exo_images_folder, image_info['file_name'])
                alt_path_ego = os.path.join(annotator.ego_images_folder, image_info['file_name'])
                if os.path.exists(alt_path_exo):
                    image_path = alt_path_exo
                elif os.path.exists(alt_path_ego):
                    image_path = alt_path_ego
                else:
                    return jsonify({'success': False, 'error': f'Image not found: {image_info["file_name"]}'}), 404
            
            # 이미지 분석 실행 (GPT-4o-mini)
            with Image.open(image_path) as img:
                original_width, original_height = img.size
                max_size = 1024
                if original_width > max_size or original_height > max_size:
                    scale = min(max_size/original_width, max_size/original_height)
                    new_width = int(original_width * scale)
                    new_height = int(original_height * scale)
                    img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                
                buffer = BytesIO()
                img.save(buffer, format='JPEG', quality=85)
                img_base64 = base64.b64encode(buffer.getvalue()).decode()
            
            # 이미지 분석 수행
            image_analysis = analyze_image_with_model(img_base64, model, image_path)
            image_analysis_cache[cache_key] = image_analysis
        
        # 2단계: COCO 어노테이션 정보 가져오기
        ann_ids = annotator.coco.getAnnIds(imgIds=image_id)
        annotations = annotator.coco.loadAnns(ann_ids)
        
        # 카테고리 정보 구성
        category_info = []
        for ann in annotations:
            cid = ann.get('category_id', None)
            name = annotator.category_id_to_name.get(int(cid), 'unknown') if cid is not None else 'unknown'
            bbox = ann.get('bbox', [])
            category_info.append({
                'category_name': name,
                'bbox': bbox
            })
        
        # 주요 객체 목록 생성
        main_objects = list(set([cat['category_name'] for cat in category_info if cat['category_name'] != 'unknown']))[:10]
        
        # 3단계: 질문 생성 (OpenAI만 사용)
        if not OPENAI_AVAILABLE:
            return jsonify({'success': False, 'error': 'OpenAI library not installed. Install with: pip install openai'}), 500
        
        if not OPENAI_API_KEY or OPENAI_API_KEY == "your-api-key-here":
            return jsonify({'success': False, 'error': 'OPENAI_API_KEY is not set. Please set it in config.py'}), 500
        
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # 2-hop 질문 생성: ATT, POS, REL 중 정확히 두 가지 태그만 사용 (view_type별 허용 조합은 allowed_tag_pairs 참고)
        question_generation_prompt = f"""Generate VQA (Visual Question Answering) 2-hop questions in Korean based on the image and image analysis results.

⚠️ IMPORTANT: You must generate questions in KOREAN language, but follow all rules and guidelines below.

═══════════════════════════════════════════════════════════════════════════════
📋 CURRENT SETTINGS
═══════════════════════════════════════════════════════════════════════════════
- View type: {view_type}
- Allowed tag combinations: {allowed_tag_pairs}
- Each tag type must be used EXACTLY ONCE (ATT 1, POS 1, REL 1 - choose 2 out of 3)
- Total tag count: EXACTLY 2 tags

═══════════════════════════════════════════════════════════════════════════════
🚨 ABSOLUTE MANDATORY RULES - MUST FOLLOW
═══════════════════════════════════════════════════════════════════════════════

**STEP 1: Verify Image Content and ATT Attribute Accuracy (MANDATORY)**

First, directly examine the image and verify that the ATT attributes you plan to use in the question exactly match the actual objects in the image.

🚨 **CRITICAL - ATT Attribute Accuracy Verification (MANDATORY)**:
1. First decide on the ATT attribute you will use in the question (e.g., "빨간색 객체" (red object), "원형 또는 원통형 객체" (round or cylindrical object), "식용 가능한 물체" (edible item)).
2. Directly examine the image to confirm that objects satisfying this ATT attribute actually exist.
3. For example, if you want to ask about "흰색 객체" (white object), there must actually be white objects in the image.
4. For example, if you want to ask about "정사각형 또는 직사각형 객체" (square or rectangular object), there must actually be square or rectangular objects in the image.
5. It is ABSOLUTELY FORBIDDEN to use ATT attributes that do not exist in the image.

**Verification Checklist**:
- [ ] Does the ATT attribute you plan to use exactly match the actual objects in the image?
- [ ] Do objects satisfying the ATT attribute actually exist in the image?
- [ ] Are you NOT using ATT attributes that do not exist in the image?

═══════════════════════════════════════════════════════════════════════════════
STEP 2: Generate 2-hop Question Structure (Tag Usage Rules)
═══════════════════════════════════════════════════════════════════════════════

🚨 **CRITICAL - 2-hop Tag Usage Rules (ABSOLUTE MANDATORY, MUST FOLLOW)**:

**RULE 1: Tag Count Limitation**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ **Use EXACTLY 2 tags only** (choose 2 out of ATT, POS, REL)
✅ **Each tag type must be used EXACTLY ONCE** (ATT 1, POS 1, REL 1 - choose 2)
❌ **ABSOLUTELY FORBIDDEN**: Use all 3 tags (ATT + POS + REL)
❌ **ABSOLUTELY FORBIDDEN**: Use same tag type 2 or more times (ATT 2, POS 2, etc.)

**Allowed Combinations (Current view type: {view_type})**:
{allowed_tag_pairs}

**Verification Method**:
1. Count <ATT> tags in your question → Must be exactly 0 or 1
2. Count <POS> tags in your question → Must be exactly 0 or 1
3. Count <REL> tags in your question → Must be exactly 0 or 1
4. Count total tags → Must be exactly 2
5. Check if used tag combination is included in {allowed_tag_pairs}

**❌ ABSOLUTELY FORBIDDEN - Too Simple Question Patterns**:
- "X 오른쪽에 있는 가장 가까운 Y 객체" (simple position+attribute combination)
- "X 위에 있는 가장 가까운 Y 객체" (simple position+attribute combination)
- "X 왼쪽에 있는 가장 먼 Y 객체" (simple position+attribute combination)

**✅ MUST USE - Complex Advanced Reasoning Question Patterns (2-hop, two tags only)**:

1. **ATT+REL Combination** (ATT and distance/order relationship only, POS forbidden):
   - "<ATT>정사각형 또는 직사각형 객체</ATT> 중에서 포크로부터 <REL>가장 먼</REL> 객체"
   - "<ATT>파티용품 객체</ATT> 중에서 사람과의 <REL>두 번째로 가까운</REL> 객체"

2. **POS+REL Combination** (position and relationship only, ATT forbidden):
   - "테이블 <POS>왼쪽에 있는</POS> 물체들 중 <REL>가장 가까운</REL> 객체"
   - "싱크대 <POS>앞에 있는</POS> 물체들 중 <REL>두 번째로 먼</REL> 객체"

3. **POS+ATT Combination** (position and attribute only, REL forbidden):
   - "소파 <POS>오른쪽에 위치한</POS> <ATT>밝은 색상의 객체</ATT>"
   - "전자레인지 <POS>위에 있는</POS> <ATT>원형 또는 원통형 객체</ATT>"

**RULE 2: ATT Tag Usage Decision Criteria**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**How to Decide Whether to Use ATT Tag**:

1️⃣ **Is there a modifier BEFORE "객체" (object), "물체" (item), or "항목" (item)?**
   - Modifier examples: color("빨간색" red, "흰색" white), shape("원형" round, "사각형" square), material("나무" wood, "금속" metal), function("식용 가능한" edible), other attributes("밝은 색상의" bright colored, "파티용품" party item)

2️⃣ **Decision Criteria**:
   ✅ **USE ATT TAG**: modifier + "객체/물체/항목" form
      Example: "빨간색 객체" → <ATT>빨간색 객체</ATT>
      Example: "원형 또는 원통형 객체" → <ATT>원형 또는 원통형 객체</ATT>
      Example: "식용 가능한 물체" → <ATT>식용 가능한 물체</ATT>
   
   ❌ **DO NOT USE ATT TAG**: plain "객체", "물체", "항목" (no modifier)
      Example: "객체" → just "객체" (NO ATT tag)
      Example: "물체" → just "물체" (NO ATT tag)

3️⃣ **Concrete nouns are ABSOLUTELY FORBIDDEN**:
   ❌ "컵" (cup), "접시" (plate), "의자" (chair), "테이블" (table), etc. → Instead use attribute-based expressions like "원형 객체" (round object), "사각형 객체" (square object), etc.

**ATT Tag Usage Examples**:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ USE: "빨간색 객체" → <ATT>빨간색 객체</ATT>
✅ USE: "원형 또는 원통형 객체" → <ATT>원형 또는 원통형 객체</ATT>
✅ USE: "밝은 색상의 객체" → <ATT>밝은 색상의 객체</ATT>
✅ USE: "식용 가능한 물체" → <ATT>식용 가능한 물체</ATT>
✅ USE: "정사각형 또는 직사각형 객체" → <ATT>정사각형 또는 직사각형 객체</ATT>
✅ USE: "나무 재질의 객체" → <ATT>나무 재질의 객체</ATT>
❌ DO NOT USE: "객체" → just "객체" (NO ATT tag)
❌ DO NOT USE: "물체" → just "물체" (NO ATT tag)

**RULE 3: POS Tag Usage Rules**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ **USE**: Specific object-based position expressions
   - "테이블 중앙에" (center of table) → <POS>테이블 중앙에</POS>
   - "소파 왼쪽에" (left side of sofa) → <POS>소파 왼쪽에</POS>
   - "싱크대 오른쪽에" (right side of sink) → <POS>싱크대 오른쪽에</POS>
   - "의자 앞에" (in front of chair) → <POS>의자 앞에</POS>
   - "창문 옆에" (next to window) → <POS>창문 옆에</POS>

❌ **ABSOLUTELY FORBIDDEN**: Ambiguous position expressions
   - "이미지 중앙에" (center of image - ambiguous)
   - "이미지 왼쪽에" (left side of image - ambiguous)
   - "화면 위에" (top of screen - ambiguous)

⚠️ **Position Reversal Rule**: If something is actually on the "왼쪽" (left), express it as "오른쪽" (right) in the question

**RULE 4: REL Tag Usage Rules**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ **USE**: Relationship expressions for distance, order, height, etc.
   - "가장 가까운" (closest) → <REL>가장 가까운</REL>
   - "가장 먼" (farthest) → <REL>가장 먼</REL>
   - "두 번째로 가까운" (second-closest) → <REL>두 번째로 가까운</REL>
   - "가장 높은" (highest) → <REL>가장 높은</REL>
   - "가장 낮은" (lowest) → <REL>가장 낮은</REL>
   - "더 가까운" (closer) → <REL>더 가까운</REL>
   - "더 먼" (farther) → <REL>더 먼</REL>

**🚨 CRITICAL - Question Ending Format Rule (ABSOLUTE MANDATORY)**:
Questions MUST end with "~객체" (object). NEVER use interrogative forms like "는?" (is?) or "는 무엇인가요?" (what is?).

- ❌ **ABSOLUTELY FORBIDDEN**:
  * "~사람은 누구인가요?" (asking about person - forbidden)
  * "것은 무엇인가요?" (ambiguous expression - forbidden)
  * "가장 가까운 것은?" (ATT attribute not specified - forbidden)
  * "가장 먼 것은?" (ATT attribute not specified - forbidden)
  * "~객체는?" ("는?" usage forbidden)
  * "~객체는 무엇인가요?" ("는 무엇인가요?" usage forbidden)
  * "무엇인가요?" (ATT attribute not specified format - forbidden)

- ✅ **MUST USE - Format ending with "~객체"**:
  * "정사각형 또는 직사각형의 객체" (square or rectangular object)
  * "원통형 또는 원형의 객체" (cylindrical or round object)
  * "밝은 색상의 객체" (bright colored object)
  * "무채색 객체" (achromatic object)
  * "금속 재질의 객체" (metal object)
  * "식용 가능한 객체" (edible object)
  * "빨간색 객체" (red object)
  * "나무 재질의 객체" (wooden object)

**Question Format Examples**:
- ✅ Correct: "테이블 위에 있는 가장 가까운 원형 또는 원통형의 객체"
- ✅ Correct: "소파 왼쪽에 위치한 밝은 색상의 객체"
- ✅ Correct: "싱크대 오른쪽에 있는 무채색 객체"
- ✅ Correct: "식용 가능한 객체 중에서 포크로부터 가장 먼 객체"
- ❌ Wrong: "소파 왼쪽에 있는 사람은 누구인가요?" (asking about person, using "는?")
- ❌ Wrong: "테이블 위에 있는 것은 무엇인가요?" (ATT attribute not specified, using "는 무엇인가요?")
- ❌ Wrong: "가장 가까운 것은?" (ATT attribute not specified, using "는?")
- ❌ Wrong: "가장 가까운 객체는?" (using "는?" - forbidden)
- ❌ Wrong: "가장 가까운 객체는 무엇인가요?" (using "는 무엇인가요?" - forbidden)

**IMPORTANT**: Questions MUST end with "~객체" that includes ATT attributes, and MUST NEVER use interrogative forms like "는?" or "는 무엇인가요?". Questions must be in noun phrase form ending with "~객체".

**STEP 3: Design Choices for Elimination Method (Requires Advanced Reasoning)**

🚨 **CRITICAL - Choice Composition for Advanced Reasoning Requirements (ABSOLUTE MANDATORY)**:
- Objects satisfying the question's ATT condition must appear in **at least 2 or more** choices.
- This ensures that when another AI solves the problem, it cannot find the answer by simply checking if the ATT condition is satisfied, and requires additional reasoning (position, distance, etc.).

**Example 1 - Correct Composition (Requires Advanced Reasoning)**:
Question: "식용 가능한 물체 중에서..." (Among edible items...)
Choices:
- a: 케이크 조각 (ATT condition satisfied, but other conditions not satisfied)
- b: 케이크 조각 (ATT condition satisfied, but other conditions not satisfied) ← different cake piece
- c: 피자 (ATT condition satisfied, but other conditions not satisfied)
- d: 햄버거 (Correct answer: ATT condition satisfied + all other conditions satisfied)

In this case, 4 objects (a, b, c, d all) satisfy the ATT condition, so advanced reasoning is required.

**Example 2 - Incorrect Composition (Too Easy)**:
Question: "식용 가능한 물체 중에서..." (Among edible items...)
Choices:
- a: 컵 (ATT condition not satisfied - not edible)
- b: 접시 (ATT condition not satisfied - not edible)
- c: 포크 (ATT condition not satisfied - not edible)
- d: 케이크 조각 (Correct answer: ATT condition satisfied)

In this case, only 1 object (d only) satisfies the ATT condition, so it's too easy. ❌

**Verification Checklist**:
- [ ] Do at least 2 or more objects satisfying the question's ATT condition appear in the choices? (requires advanced reasoning)
- [ ] Can each choice be excluded for different reasons?
- [ ] Are there no duplicate objects in the choices?
- [ ] Do all objects in the choices actually exist in the image?

**STEP 4: Prohibit Duplicate Objects**

🚨 **CRITICAL - Prohibit Duplicate Objects (ABSOLUTE MANDATORY)**:
- Each choice must point to **different object instances**.
- Even objects of the same category must point to different instances (different bbox) within the image.
- Example: Even if there are 3 "컵" (cups) in the image, "컵" should not appear twice in the choices. They must be distinguished as "왼쪽 컵" (left cup), "오른쪽 컵" (right cup), "중앙 컵" (center cup), etc.

**Image Analysis Results**:
{image_analysis}

**COCO Object Information (Objects identifiable by bbox)**:
- Main objects: {', '.join(main_objects) if main_objects else 'None'}
- Total object count: {len(category_info)}
- Each object can be accurately identified by bbox within the image

**IMPORTANT**: Among objects mentioned in the image analysis results, use only objects that exist in COCO annotations as choices. If there are multiple objects of the same type, clearly distinguish them by color, position, attributes, etc.

**🚨 CRITICAL - Reference Examples (2-hop format, two tags only)**:

You MUST refer to the following examples to generate questions and choices in **2-hop format (exactly two tags only)**:

**Example 1** (ATT+REL combination - exo):
- Question: "Which <ATT>edible food item</ATT> is the <REL>farthest</REL> from the fork?"
- Choices: (a) glass, (b) potato fries, (c) hamburger, (d) cell phone
- Reasoning: cell phone is not edible (ATT condition not satisfied), glass is also not edible (ATT condition not satisfied), potato fries is closer than hamburger (REL condition not satisfied), therefore hamburger is correct
- ✅ **2-hop**: ATT + REL (no POS)
- ✅ **Advanced Reasoning**: 2 objects (b, c) satisfy ATT condition, so cannot find answer by checking ATT only

**Example 2** (POS+REL combination - exo):
- Question: "Which object <POS>on the left side of</POS> the table is <REL>farthest</REL> from the person?"
- Choices: (a) plate, (b) white cake, (c) rightmost coke, (d) vase
- Reasoning: rightmost coke is not on left side of table (POS condition not satisfied), plate and white cake are closer (REL condition not satisfied), vase is farthest
- ✅ **2-hop**: POS + REL (no ATT)
- ✅ **Advanced Reasoning**: Distance calculation needed among objects satisfying POS condition

**Example 3** (POS+ATT combination - exo):
- Question: "Which <ATT>square-shaped item</ATT> is <POS>in front of</POS> the brown-haired man sitting on the sofa?"
- Choices: (a) handbag, (b) coke, (c) laptop, (d) cell phone
- Reasoning: laptop and cell phone are on sofa (POS condition not satisfied), coke is cylindrical so excluded (ATT condition not satisfied), handbag is in front and square-shaped (all conditions satisfied)
- ✅ **2-hop**: POS + ATT (no REL)
- ✅ **Advanced Reasoning**: Each choice excluded for different reasons (position, shape, etc.)

**Example 4** (ATT+REL combination - ego):
- Question: "From the perspective of the little girl, which <ATT>party item</ATT> is <REL>farthest</REL> from her?"
- Choices: (a) cake, (b) camera, (c) party plate, (d) flower
- Reasoning: cake, camera, party plate are closer (REL condition not satisfied), flower is farthest
- ✅ **2-hop**: ATT + REL (no POS)
- ✅ **Advanced Reasoning**: Distance calculation needed among objects satisfying ATT condition

**Example 5** (POS+REL combination - ego):
- Question: "When I'm sitting on the right side of the sofa, which object <POS>on my left side</POS> is <REL>closest</REL> to me?"
- Choices: (a) fan, (b) large bottle, (c) shoe, (d) tv
- Reasoning: tv is not on left side (POS condition not satisfied), fan and large bottle are farther (REL condition not satisfied), shoe is closest
- ✅ **2-hop**: POS + REL (no ATT)
- ✅ **Advanced Reasoning**: Distance calculation needed among objects satisfying POS condition

**Example 6** (POS+ATT combination - ego):
- Question: "When I'm standing in front of the white board, which <ATT>rectangular object</ATT> is <POS>behind me</POS>?"
- Choices: (a) tv, (b) water bowl, (c) table, (d) tablemat
- Reasoning: tv is not behind (POS condition not satisfied), water bowl and table are not rectangular (ATT condition not satisfied), tablemat is behind and rectangular (all conditions satisfied)
- ✅ **2-hop**: POS + ATT (no REL)
- ✅ **Advanced Reasoning**: Each choice excluded for different reasons

**🚨 CRITICAL - Choice Composition Principles (ABSOLUTE MANDATORY)**:

1. **Diverse Exclusion Reasons**: Each choice must be excluded for different reasons:
   - ATT condition not satisfied (attributes, shape, color, etc.)
   - POS condition not satisfied (position, spatial relationships, etc.)
   - REL condition not satisfied (distance, order, etc.)
   - Multiple conditions simultaneously not satisfied

2. **At Least 2 Objects Satisfying ATT Condition**: At least 2 or more objects satisfying the question's ATT condition must appear in the choices. This ensures that the answer cannot be found by simply checking the ATT condition, and requires additional reasoning (POS, REL).

3. **Choice Diversity**: Choices must include diverse categories and attributes:
   - ❌ Bad example: "밝은 색상의 의자" (bright colored chair), "밝은 색상의 벤치" (bright colored bench), "밝은 색상의 식탁" (bright colored table), "밝은 색상의 쓰레기통" (bright colored trash can) (all same attribute)
   - ✅ Good example: "glass", "potato fries", "hamburger", "cell phone" (diverse attributes and categories)

**IMPORTANT**: Refer to the above examples to:
1. **Complex Question Structure**: NEVER use simple "X 오른쪽에 있는 가장 가까운 Y 객체" (closest Y object on the right side of X) format
2. **Nested Conditions**: Generate questions with multiple conditions applied simultaneously
3. **Diverse Exclusion Reasons**: Compose choices so each is excluded for different reasons
4. **At Least 2 Objects Satisfying ATT Condition**: Compose choices to require advanced reasoning

**OUTPUT FORMAT (MUST be in JSON format, generate exactly 3 questions)**:

🚨 **CRITICAL**: All questions MUST end with "~객체" (object). NEVER use interrogative forms like "는?" (is?) or "는 무엇인가요?" (what is?).

⚠️ **IMPORTANT**: Generate questions in KOREAN language, but follow all English instructions above.

{{
  "questions": [
    {{
      "question": "첫 번째 2-hop 한글 질문 (허용 태그 조합만 사용, ATT는 속성 기반 표현, ATT 조건 만족 객체 최소 2개 이상, 반드시 '~객체'로 끝남, '는?' 또는 '는 무엇인가요?' 사용 금지)",
      "choices": {{
        "a": "선택지 a (한글, 소거 가능한 이유가 명확해야 함, 동일 물체 중복 금지)",
        "b": "선택지 b (한글, 소거 가능한 이유가 명확해야 함, 동일 물체 중복 금지)",
        "c": "선택지 c (한글, 소거 가능한 이유가 명확해야 함, 동일 물체 중복 금지)",
        "d": "선택지 d (한글, 정답, ATT 조건 만족 객체 중 하나)"
      }},
      "correct_answer": "a"
    }},
    {{
      "question": "두 번째 2-hop 한글 질문 (첫 번째와 다른 구조/조합, 허용 태그 조합만 사용, ATT 조건 만족 객체 최소 2개 이상, 반드시 '~객체'로 끝남, '는?' 또는 '는 무엇인가요?' 사용 금지)",
      "choices": {{
        "a": "선택지 a (한글, 소거 가능한 이유가 명확해야 함, 동일 물체 중복 금지)",
        "b": "선택지 b (한글, 소거 가능한 이유가 명확해야 함, 동일 물체 중복 금지)",
        "c": "선택지 c (한글, 소거 가능한 이유가 명확해야 함, 동일 물체 중복 금지)",
        "d": "선택지 d (한글, 정답, ATT 조건 만족 객체 중 하나)"
      }},
      "correct_answer": "b"
    }},
    {{
      "question": "세 번째 2-hop 한글 질문 (앞의 두 질문과 다른 구조/조합, 허용 태그 조합만 사용, ATT 조건 만족 객체 최소 2개 이상, 반드시 '~객체'로 끝남, '는?' 또는 '는 무엇인가요?' 사용 금지)",
      "choices": {{
        "a": "선택지 a (한글, 소거 가능한 이유가 명확해야 함, 동일 물체 중복 금지)",
        "b": "선택지 b (한글, 소거 가능한 이유가 명확해야 함, 동일 물체 중복 금지)",
        "c": "선택지 c (한글, 소거 가능한 이유가 명확해야 함, 동일 물체 중복 금지)",
        "d": "선택지 d (한글, 정답, ATT 조건 만족 객체 중 하나)"
      }},
      "correct_answer": "c"
    }}
  ]
}}

**Question Format Examples (MUST refer to)**:

**❌ ABSOLUTELY FORBIDDEN - Too Simple Questions**:
- "테이블 위에 있는 가장 가까운 원형 또는 원통형의 객체" (simple position+attribute)
- "소파 왼쪽에 위치한 밝은 색상의 객체" (simple position+attribute)
- "싱크대 오른쪽에 있는 무채색 객체" (simple position+attribute)
- "소파 왼쪽에 있는 사람은 누구인가요?" (forbidden - using "는 누구인가요?")
- "테이블 위에 있는 것은 무엇인가요?" (forbidden - ATT attribute not specified, using "는 무엇인가요?")
- "가장 가까운 것은?" (forbidden - ATT attribute not specified, using "는?")

**✅ MUST USE - Complex Advanced Reasoning Questions (2-hop, each tag 1 each)**:
- "식용 가능한 객체 중에서 포크로부터 가장 먼 객체" (ATT 1 + REL 1, no POS)
- "테이블 왼쪽에 있는 물체들 중 두 번째로 먼 객체" (POS 1 + REL 1, no ATT)
- "소파 오른쪽에 위치한 밝은 색상의 객체" (POS 1 + ATT 1, no REL)
- "전자레인지 위에 있는 원형 또는 원통형 객체" (POS 1 + ATT 1, no REL)
- "파티용품 객체 중에서 사람과의 두 번째로 가까운 객체" (ATT 1 + REL 1, no POS)

═══════════════════════════════════════════════════════════════════════════════
🚨 FINAL VERIFICATION CHECKLIST (MUST verify step-by-step before generation)
═══════════════════════════════════════════════════════════════════════════════

**STEP 1: Tag Count Verification (MOST IMPORTANT!)**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[ ] Count <ATT> tags in question → Is it exactly 0 or 1?
[ ] Count <POS> tags in question → Is it exactly 0 or 1?
[ ] Count <REL> tags in question → Is it exactly 0 or 1?
[ ] Count total tags → Is it exactly 2? (3 tags = ❌, 1 tag = ❌)
[ ] Is the used tag combination included in {allowed_tag_pairs}?

**STEP 2: ATT Tag Usage Verification**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[ ] If ATT tag is used, is there a modifier BEFORE "객체/물체/항목"?
    - Modifier examples: color, shape, material, function, etc.
    - Example: "빨간색 객체" ✅ / "객체" ❌
[ ] If ATT tag is NOT used, is it a plain "객체/물체" mention?
[ ] Are concrete nouns ("컵" cup, "접시" plate, etc.) NOT used?
[ ] Are attribute-based expressions ("원형 객체" round object, "빨간색 객체" red object, etc.) used?

**STEP 3: POS Tag Usage Verification**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[ ] Does POS tag use specific object-based position?
    - ✅ "테이블 중앙에" (center of table), "소파 왼쪽에" (left side of sofa)
    - ❌ "이미지 중앙에" (center of image), "화면 위에" (top of screen) (ambiguous)
[ ] Is position reversal rule applied? (actual left → question right)

**STEP 4: REL Tag Usage Verification**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[ ] Does REL tag use relationship expressions for distance/order/height, etc.?
    - Examples: "가장 가까운" (closest), "가장 먼" (farthest), "두 번째로 가까운" (second-closest), etc.

**STEP 5: Question Format Verification**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[ ] Does question end with "~객체"? (NOT using "는?", "는 무엇인가요?")
[ ] Is question in noun phrase form? (NOT using interrogative forms)

**STEP 6: Choice Composition Verification**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[ ] Do at least 2 or more objects satisfying the question's ATT condition appear in choices?
[ ] Can each choice be excluded for different reasons?
[ ] Are there no duplicate objects in choices?
[ ] Do all objects in choices actually exist in the image?
[ ] Do choices include diverse categories and attributes?

**STEP 7: Image Match Verification**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[ ] Does ATT attribute exactly match actual objects in the image?
[ ] Do objects mentioned in the question actually exist in the image?

**IMPORTANT**: Generate exactly 3 questions, and each question MUST follow all rules above. MUST respond in valid JSON format."""

        # RateLimitError 처리: 재시도 로직 포함
        max_retries = 5
        retry_delay = 1  # 초기 대기 시간 (초)
        
        generation_response = None
        generated_content = None
        
        for attempt in range(max_retries):
            try:
                generation_response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {
                            "role": "system",
                            "content": """You are an expert VQA question generator specializing in 2-hop reasoning questions. 

CRITICAL RULES (MUST FOLLOW EXACTLY):

RULE 1 - TAG COUNT (MOST IMPORTANT):
- Use EXACTLY TWO tags per question (ATT, POS, REL 중 2개만)
- Each tag type must appear EXACTLY ONCE (ATT 1개, POS 1개, REL 1개 중 2개만)
- NEVER use all three tags
- NEVER use same tag type twice

RULE 2 - ATT TAG DECISION:
- Use <ATT> tag ONLY when object has modifier (수식어가 붙은 객체)
  Example: '빨간색 객체' → <ATT>red object</ATT>
- DO NOT use <ATT> tag for plain '객체' or '물체' (no modifier)
  Example: '객체' → just 'object' (NO <ATT> tag)

RULE 3 - QUESTION QUALITY:
- Questions MUST require advanced reasoning
- Use ONLY objects that exist in the image
- At least 2 choices MUST satisfy ATT condition
- Each choice excluded for DIFFERENT reasons
- Generate exactly 3 questions with DIFFERENT 2-hop structures

Return valid JSON."""
                        },
                        {
                            "role": "user",
                            "content": question_generation_prompt
                        }
                    ],
                    temperature=0.5,
                    max_tokens=2000,
                    response_format={"type": "json_object"}
                )
                
                generated_content = generation_response.choices[0].message.content.strip()
                break  # 성공하면 루프 종료
                
            except RateLimitError as e:
                if attempt < max_retries - 1:
                    # retry_after 정보가 있으면 사용, 없으면 exponential backoff
                    wait_time = getattr(e, 'retry_after', None)
                    if wait_time is None:
                        wait_time = retry_delay * (2 ** attempt)  # exponential backoff
                    else:
                        wait_time = float(wait_time) + 1  # retry_after에 1초 추가 여유
                    
                    print(f"[WARN] Rate limit reached. Waiting {wait_time:.2f} seconds before retry {attempt + 1}/{max_retries}...")
                    import time
                    time.sleep(wait_time)
                    continue
                else:
                    # 마지막 시도에서도 실패하면 에러 반환
                    return jsonify({
                        'success': False,
                        'error': f'Rate limit exceeded after {max_retries} retries. Please try again later or reduce parallel workers.'
                    }), 429
            except Exception as e:
                # RateLimitError가 아닌 다른 에러는 즉시 반환
                import traceback
                traceback.print_exc()
                return jsonify({'success': False, 'error': f'OpenAI question generation failed: {str(e)}'}), 500
        
        if generated_content is None:
            return jsonify({
                'success': False,
                'error': 'Failed to generate questions after retries'
            }), 500
        
        # JSON 파싱
        try:
            import json
            generated_data = json.loads(generated_content)
            questions = generated_data.get('questions', [])
            
            if not questions:
                return jsonify({'success': False, 'error': 'No questions generated'}), 500
            
            # 정확히 3개만 반환 (더 많으면 앞의 3개만)
            if len(questions) > 3:
                questions = questions[:3]
            elif len(questions) < 3:
                return jsonify({'success': False, 'error': f'Expected 3 questions but got {len(questions)}'}), 500
            
            return jsonify({
                'success': True,
                'image_id': image_id,
                'questions': questions
            })
        except json.JSONDecodeError as e:
            return jsonify({'success': False, 'error': f'Failed to parse JSON: {str(e)}', 'raw_response': generated_content}), 500
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'OpenAI question generation failed: {str(e)}'}), 500

@app.route('/api/translate/question_and_choices', methods=['POST'])
def translate_question_and_choices():
    """Translate Korean question and choices to English together using GPT-5, with image analysis context."""
    data = request.json
    question_ko = data.get('question_ko', '').strip()
    choice_a = data.get('choice_a', '').strip()
    choice_b = data.get('choice_b', '').strip()
    choice_c = data.get('choice_c', '').strip()
    choice_d = data.get('choice_d', '').strip()
    image_id = data.get('image_id', None)  # 이미지 ID 추가
    view_type = data.get('view_type', 'exo')  # 'exo' or 'ego'
    
    if not question_ko:
        return jsonify({'success': False, 'error': 'Question (Korean) is required'}), 400
    
    if not all([choice_a, choice_b, choice_c, choice_d]):
        return jsonify({'success': False, 'error': 'All choices are required'}), 400
    
    try:
        if not OPENAI_AVAILABLE:
            return jsonify({'success': False, 'error': 'OpenAI library not installed. Install with: pip install openai'}), 500
        
        if not OPENAI_API_KEY or OPENAI_API_KEY == "your-api-key-here":
            return jsonify({'success': False, 'error': 'OPENAI_API_KEY is not set. Please set it in coco_web_annotator.py'}), 500
        
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # 이미지 분석 결과 가져오기 (캐시에서만 확인)
        # 프론트엔드에서 이미 분석을 수행하므로 여기서는 캐시만 확인
        # 캐시 키는 "image_id_model" 형식이므로 모든 모델의 캐시를 확인
        image_analysis = ""
        if image_id:
            # 기본 모델부터 확인
            for model_name in [DEFAULT_MODEL, 'openai']:
                cache_key = f"{image_id}_{model_name}"
                if cache_key in image_analysis_cache:
                    image_analysis = image_analysis_cache[cache_key]
                    break
        
        # Question과 Choices를 함께 번역하는 프롬프트 (이미지 분석 결과 포함)
        image_context = ""
        if image_analysis:
            image_context = f"""

IMAGE ANALYSIS CONTEXT:
{image_analysis}

Use this image analysis to better understand the context and spatial relationships mentioned in the Korean question. The analysis includes detailed features like colors, positions, orientations, and spatial relationships of objects in the image. Use this information to create more accurate <ATT>, <POS>, and <REL> tags that match the actual visual content."""
        
        # view_type에 따라 다른 프롬프트 사용
        if view_type == 'ego':
            prompt = f"""Translate the following Korean question and multiple choice options to English. You MUST follow this EXACT format for EGO-CENTRIC questions:{image_context}

CORRECT FORMAT FOR EGO-CENTRIC QUESTIONS (2-hop: EXACTLY TWO TAGS):
[Question with EXACTLY TWO tags chosen only from these pairs: (POS+REL), (ATT+REL), (POS+ATT)] <choice>(a) option1, (b) option2, (c) option3, (d) option4</choice> And provide the bounding box coordinate of the region related to your answer. 🚨 NEVER include the third tag.

CRITICAL - EGO-CENTRIC QUESTION STARTING PHRASES:
1. If the Korean question contains "~관점에서" (from the perspective of ~):
   → Translate to: "From the perspective of [person/object], ..."
   Example: "작은 소녀의 관점에서" → "From the perspective of the little girl, ..."

2. If the Korean question contains "내가" or "I'm" (when I am in the image):
   → Translate to: "When I'm [action/position], ..."
   Examples:
   - "내가 소파 오른쪽에 앉아 있을 때" → "When I'm sitting on the right side of the sofa, ..."
   - "내가 의자에 앉아 있을 때" → "When I'm sitting on the chair, ..."
   - "내가 테이블 앞에 서 있을 때" → "When I'm standing in front of the table, ..."

CRITICAL TAG USAGE RULES (2-hop):

0. TAG COUNT RULE - EGO:
   - Use EXACTLY TWO tags per question.
   - Allowed pairs ONLY: (POS+REL), (ATT+REL), (POS+ATT).
   - DO NOT include the third tag. NO 3-tag questions.

1. <REL> tag - Use ONLY for RELATIONSHIP terms (distance, order, placement):
   - Examples: "farthest", "closest", "second-closest", "highest in position"
   - DO NOT use for objects or locations

2. <POS> tag - Use ONLY for POSITION/LOCATION information from the perspective:
   - Examples: "on the left side", "on the right side", "in front of", "behind", "to the left of", "to the right of"
   - DO NOT use for object attributes or relationships
   - DO NOT use generic phrases like "in the image"
   - Remember: In ego-centric questions, "left/right" are from the person's perspective

3. <ATT> tag - Use ONLY for ATTRIBUTES or TARGET GROUPS:
   - Examples: "round object", "green object", "white object", "rectangular object", "party item", "furry creature"
   - Use for describing WHAT object/group is being asked about
   
🚨 CRITICAL - <ATT> TAG USAGE RULES:
   - ✅ **USE <ATT> TAG**: When Korean question contains objects WITH modifiers (수식어가 붙은 객체)
     * "흰색 객체" (white object) → "<ATT>white object</ATT>"
     * "빨간색 객체" (red object) → "<ATT>red object</ATT>"
     * "원형 객체" (round object) → "<ATT>round object</ATT>"
     * "정사각형 객체" (square object) → "<ATT>square object</ATT>"
     * "식용 가능한 물체" (edible item) → "<ATT>edible item</ATT>"
     * "밝은 색상의 객체" (bright colored object) → "<ATT>bright colored object</ATT>"
   - ❌ **DO NOT USE <ATT> TAG**: When Korean question contains plain "객체" (object), "물체" (item) WITHOUT modifiers
     * "객체" (object) → just "object" (NO <ATT> tag)
     * "물체" (item) → just "item" (NO <ATT> tag)
   - WRONG: "which <ATT>object</ATT>" (plain object without modifier)
   - CORRECT: "which object" (no ATT tag for plain object)
   - WRONG: "which white object" (missing <ATT> tag for object with modifier)
   - CORRECT: "which <ATT>white object</ATT>"

4. GENERAL RULES:
   - Tags MUST contain actual meaningful content (NOT empty like <ATT></ATT>)
   - Tags should be embedded naturally within the question sentence, not at the end
   - The <choice> tag MUST come BEFORE "And provide..." phrase
   - DO NOT use generic phrases like "in the image" for <POS> tag
   - If a phrase contains both attribute and location, split them appropriately

Reference examples from ego_data_sample.json (2-hop format, two tags only):

Example 1 (ATT+REL): "From the perspective of the little girl, which <ATT>party item</ATT> is <REL>farthest</REL> from her? <choice>(a) cake, (b) camera, (c) party plate, (d) flower</choice> And provide the bounding box coordinate of the region related to your answer."

Example 2 (POS+ATT): "When I'm sitting on the right side of the large sofa, which <ATT>square or rectangular object</ATT> is <POS>on the right side of the room</POS>? <choice>(a) fan, (b) large bottle, (c) shoe, (d) tv</choice> And provide the bounding box coordinate of the region related to your answer."

Example 3 (POS+REL): "From the perspective of the woman, which object <POS>to the right of</POS> her is <REL>closest to her</REL>? <choice>(a) fork, (b) knife, (c) spoon, (d) wine glass</choice> And provide the bounding box coordinate of the region related to your answer."

Korean question: {question_ko}

Korean choices:
(a) {choice_a}
(b) {choice_b}
(c) {choice_c}
(d) {choice_d}

CRITICAL - Choice Translation Format:
- Use concise, intuitive adjective+noun or noun+noun format (NOT full sentences)
- Examples:
  * "a person in a black shirt" → "black shirt person"
  * "a person wearing glasses" → "glasses person"
  * "a cup on the table" → "table cup" or "cup"
  * "a red chair" → "red chair"
  * "a man with a blue t-shirt" → "blue t-shirt man"
- DO NOT use full sentences like "a person who is wearing a black shirt"
- DO NOT use articles "a" or "the" unless necessary
- Keep choices short and intuitive

Translate the Korean question and choices to English following the EXACT format above. Make sure:
- Use "From the perspective of ~" if Korean contains "~관점에서"
- Use "When I'm ~" if Korean contains "내가" or "I'm"
- <REL> is used ONLY for relationship terms (farthest, closest, etc.)
- <POS> is used ONLY for position/location information from the person's perspective (on the left side, on the right side, etc.)
- <ATT> is used ONLY for attributes or target groups (round object, green object, white object, person, etc.)
- 🚨 2-HOP RULE: Use EXACTLY TWO TAGS per question and ONLY from (POS+REL), (ATT+REL), (POS+ATT). Do NOT add the third tag.
- 🚨 ATT TAG RULE: Use <ATT> tag ONLY when Korean question contains objects WITH modifiers (수식어가 붙은 객체). Do NOT use <ATT> tag for plain "객체" (object) or "물체" (item) without modifiers.
- 🚨 MANDATORY: If Korean question contains objects with modifiers like "흰색 객체" (white object), "빨간색 객체" (red object), "원형 객체" (round object), you MUST use <ATT> tag
- 🚨 MANDATORY: NEVER translate "흰색 객체" as "white object" without <ATT> tags - it MUST be "<ATT>white object</ATT>"
- 🚨 DO NOT USE ATT TAG: If Korean question contains plain "객체" (object) or "물체" (item) without modifiers, translate as just "object" or "item" WITHOUT <ATT> tags
- All tags have meaningful content inside them
- Tags are naturally embedded in the question sentence
- <choice> tag comes before "And provide..." phrase
- DO NOT use generic phrases like "in the image" for <POS> tag
- Choices are in concise adjective+noun or noun+noun format
- DOUBLE-CHECK: Before finalizing, verify that ALL attribute descriptions are wrapped in <ATT> tags"""
        else:
            prompt = f"""Translate the following Korean question and multiple choice options to English. You MUST follow this EXACT format:{image_context}

CORRECT FORMAT (2-hop: EXACTLY TWO TAGS):
[Question with EXACTLY TWO tags chosen only from these pairs: (ATT+REL), (POS+REL), (POS+ATT)] <choice>(a) option1, (b) option2, (c) option3, (d) option4</choice> And provide the bounding box coordinate of the region related to your answer. 🚨 NEVER include the third tag.

CRITICAL TAG USAGE RULES (2-hop):

0. TAG COUNT RULE - EXO:
   - Use EXACTLY TWO tags per question.
   - Allowed pairs ONLY: (ATT+REL), (POS+REL), (POS+ATT).
   - DO NOT include the third tag. NO 3-tag questions.

1. <REL> tag - Use ONLY for RELATIONSHIP terms (distance, order, placement):
   - Examples: "farthest", "closest", "second-closest", "placed on the floor"
   - DO NOT use for objects or locations
   - CORRECT: "Which object is <REL>farthest</REL> from..."
   - WRONG: "<REL>flag in the center</REL>" (this should be <POS>)

2. <POS> tag - Use ONLY for POSITION/LOCATION information:
   - Examples: "in the center", "on the left side of", "in front of", "to the left side", "on the right side", "around the dining table"
   - DO NOT use for object attributes or relationships
   - CORRECT: "...flag <POS>in the center of the table</POS>"
   - WRONG: "<POS>in the image</POS>" (too generic, not meaningful)
   - WRONG: "<ATT>flag in the center of the table</ATT>" (location info should be <POS>)

3. <ATT> tag - Use ONLY for ATTRIBUTES or TARGET GROUPS:
   - Examples: "red object", "square-shaped item", "among the items", "among the visible people", "edible food item", "object that can hold water", "non-edible item", "white object", "round object", "person"
   - Use for describing WHAT object/group is being asked about
   - CORRECT: "Which <ATT>red object</ATT> is..."
   - CORRECT: "<ATT>Among the items</ATT> on the table..."
   - WRONG: "<ATT>flag in the center of the table</ATT>" (contains location, should split: flag <POS>in the center of the table</POS>)
   
🚨 CRITICAL - <ATT> TAG USAGE RULES:
   - ✅ **USE <ATT> TAG**: When Korean question contains objects WITH modifiers (수식어가 붙은 객체)
     * "흰색 객체" (white object) → "<ATT>white object</ATT>"
     * "빨간색 객체" (red object) → "<ATT>red object</ATT>"
     * "원형 객체" (round object) → "<ATT>round object</ATT>"
     * "정사각형 객체" (square object) → "<ATT>square object</ATT>"
     * "식용 가능한 물체" (edible item) → "<ATT>edible item</ATT>"
     * "밝은 색상의 객체" (bright colored object) → "<ATT>bright colored object</ATT>"
   - ❌ **DO NOT USE <ATT> TAG**: When Korean question contains plain "객체" (object), "물체" (item) WITHOUT modifiers
     * "객체" (object) → just "object" (NO <ATT> tag)
     * "물체" (item) → just "item" (NO <ATT> tag)
   - WRONG: "which <ATT>object</ATT>" (plain object without modifier)
   - CORRECT: "which object" (no ATT tag for plain object)
   - WRONG: "which white object" (missing <ATT> tag for object with modifier)
   - CORRECT: "which <ATT>white object</ATT>"

4. GENERAL RULES:
   - Tags MUST contain actual meaningful content (NOT empty like <ATT></ATT>)
   - Tags should be embedded naturally within the question sentence, not at the end
   - The <choice> tag MUST come BEFORE "And provide..." phrase
   - DO NOT use generic phrases like "in the image" for <POS> tag
   - If a phrase contains both attribute and location, split them appropriately

Reference examples from exo_data_sample.json (2-hop format, two tags only):

Example 1 (POS+REL): "Which object <POS>in the center</POS> of the countertop is <REL>second-closest</REL> to the refrigerator? <choice>(a) sink, (b) vase, (c) orange bag, (d) rightmost red chair</choice> And provide the bounding box coordinate of the region related to your answer."

Example 2 (ATT+REL): "Which <ATT>square-shaped item</ATT> is <REL>placed on the floor</REL>? <choice>(a) handbag, (b) coke, (c) laptop, (d) cell phone</choice> And provide the bounding box coordinate of the region related to your answer."

Example 3 (ATT+REL): "Which <ATT>round and cylindrical object</ATT> is <REL>farthest</REL> from the person? <choice>(a) plate, (b) white cake, (c) rightmost coke, (d) vase</choice> And provide the bounding box coordinate of the region related to your answer."

Example 4 (ATT+REL): "Which <ATT>edible food item</ATT> is the <REL>farthest</REL> from the fork? <choice>(a) glass, (b) potato fries, (c) hamburger, (d) cell phone</choice> And provide the bounding box coordinate of the region related to your answer."

Korean question: {question_ko}

Korean choices:
(a) {choice_a}
(b) {choice_b}
(c) {choice_c}
(d) {choice_d}

CRITICAL - Choice Translation Format:
- Use concise, intuitive adjective+noun or noun+noun format (NOT full sentences)
- Examples:
  * "a person in a black shirt" → "black shirt person"
  * "a person wearing glasses" → "glasses person"
  * "a cup on the table" → "table cup" or "cup"
  * "a red chair" → "red chair"
  * "a man with a blue t-shirt" → "blue t-shirt man"
- DO NOT use full sentences like "a person who is wearing a black shirt"
- DO NOT use articles "a" or "the" unless necessary
- Keep choices short and intuitive

Translate the Korean question and choices to English following the EXACT format above. Make sure:
- <REL> is used ONLY for relationship terms (farthest, closest, etc.)
- <POS> is used ONLY for position/location information (in the center, on the left side, etc.)
- <ATT> is used ONLY for attributes or target groups (red object, white object, among the items, person, etc.)
- 🚨 2-HOP RULE: Use EXACTLY TWO TAGS per question and ONLY from (ATT+REL), (POS+REL), (POS+ATT). Do NOT add the third tag.
- 🚨 TAG COUNT RULE: Each tag type (ATT, POS, REL) must appear EXACTLY ONCE per question. Do NOT use multiple ATT tags, multiple POS tags, or multiple REL tags.
- 🚨 ATT TAG RULE: Use <ATT> tag ONLY when Korean question contains objects WITH modifiers (수식어가 붙은 객체). Do NOT use <ATT> tag for plain "객체" (object) or "물체" (item) without modifiers.
- 🚨 MANDATORY: If Korean question contains objects with modifiers like "흰색 객체" (white object), "빨간색 객체" (red object), "원형 객체" (round object), you MUST use <ATT> tag
- 🚨 MANDATORY: NEVER translate "흰색 객체" as "white object" without <ATT> tags - it MUST be "<ATT>white object</ATT>"
- 🚨 DO NOT USE ATT TAG: If Korean question contains plain "객체" (object) or "물체" (item) without modifiers, translate as just "object" or "item" WITHOUT <ATT> tags
- All tags have meaningful content inside them
- Tags are naturally embedded in the question sentence
- <choice> tag comes before "And provide..." phrase
- DO NOT use generic phrases like "in the image" for <POS> tag
- Choices are in concise adjective+noun or noun+noun format
- DOUBLE-CHECK: Before finalizing, verify that ALL attribute descriptions are wrapped in <ATT> tags"""
        
        # view_type에 따라 다른 시스템 메시지 사용
        if view_type == 'ego':
            system_message = "You are a professional translator specializing in VQA (Visual Question Answering) EGO-CENTRIC questions. CRITICAL RULES: 1) EXACTLY TWO TAGS per question, allowed pairs ONLY (POS+REL), (ATT+REL), (POS+ATT) — NEVER include the third tag, 2) Use 'From the perspective of ~' for '~관점에서', 3) Use 'When I'm ~' for '내가', 4) <REL> tag ONLY for relationship terms (farthest, closest, etc.), 5) <POS> tag ONLY for position/location from person's perspective (on the left side, on the right side, etc.), 6) <ATT> tag ONLY for attributes/target groups (round object, green object, etc.), 7) Tags MUST contain actual meaningful content, 8) Format: [Question with tags] <choice>...</choice> And provide... (choice tag BEFORE 'And provide' phrase), 9) DO NOT use generic phrases like 'in the image' for <POS> tag, 10) Choices MUST be in concise adjective+noun or noun+noun format (e.g., 'black shirt person', 'glasses person'), NOT full sentences."
        else:
            system_message = "You are a professional translator specializing in VQA (Visual Question Answering) questions. CRITICAL RULES: 1) EXACTLY TWO TAGS per question, allowed pairs ONLY (ATT+REL), (POS+REL), (POS+ATT) — NEVER include the third tag, 2) <REL> tag ONLY for relationship terms (farthest, closest, etc.), 3) <POS> tag ONLY for position/location (in the center, on the left side, etc.), 4) <ATT> tag ONLY for attributes/target groups (red object, among the items, etc.), 5) Tags MUST contain actual meaningful content, 6) Format: [Question with tags] <choice>...</choice> And provide... (choice tag BEFORE 'And provide' phrase), 7) DO NOT use generic phrases like 'in the image' for <POS> tag, 8) Choices MUST be in concise adjective+noun or noun+noun format (e.g., 'black shirt person', 'glasses person'), NOT full sentences."
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        
        translated_question = response.choices[0].message.content.strip()
        
        # 태그 검증 - 빈 태그 확인 (내용이 있는 태그만 유효)
        has_valid_att = bool(re.search(r'<ATT>[^<]+</ATT>', translated_question, re.IGNORECASE))
        has_valid_pos = bool(re.search(r'<POS>[^<]+</POS>', translated_question, re.IGNORECASE))
        has_valid_rel = bool(re.search(r'<REL>[^<]+</REL>', translated_question, re.IGNORECASE))
        
        if not (has_valid_att or has_valid_pos or has_valid_rel):
            return jsonify({'success': False, 'error': 'Translation must include at least one of <ATT>, <POS>, or <REL> tags with actual content inside them'}), 400
        
        # ATT 태그 누락 검증: 질문에서 찾는 대상(객체)에 속성이 있는지 확인
        # 단순히 키워드가 있는지만 확인하는 것이 아니라, 질문의 끝 부분(찾는 대상)에 수식어가 있는지 확인
        # 예: "파란색 청바지를 입은 사람 오른쪽에 있는 가장 높은 객체" → "가장 높은 객체"가 찾는 대상이므로 ATT 필요 없음
        # 예: "파란색 청바지를 입은 사람 오른쪽에 있는 빨간색 객체" → "빨간색 객체"가 찾는 대상이므로 ATT 필요
        
        # 질문 끝 부분에서 "~객체", "~물체" 패턴 찾기
        object_pattern = r'([가-힣\s]+(?:객체|물체|항목))'
        matches = re.findall(object_pattern, question_ko)
        
        # 질문 끝 부분의 객체 표현 확인
        question_has_target_attribute = False
        last_object_phrase = ""
        if matches:
            # 마지막 매치(질문의 끝 부분) 확인
            last_object_phrase = matches[-1].strip()
            # 수식어가 있는지 확인 (색상, 형태, 재질 등)
            attribute_modifiers = ['흰색', '빨간색', '파란색', '초록색', '검은색', '노란색', '원형', '정사각형', '직사각형', '사각형', '밝은', '어두운', '나무', '금속', '식용', '밝은 색상', '어두운 색상']
            for modifier in attribute_modifiers:
                if modifier in last_object_phrase and ('객체' in last_object_phrase or '물체' in last_object_phrase or '항목' in last_object_phrase):
                    question_has_target_attribute = True
                    break
        
        # 질문에서 찾는 대상에 속성이 있는데 ATT 태그가 없는 경우에만 에러
        if question_has_target_attribute and not has_valid_att:
            return jsonify({
                'success': False, 
                'error': f'ATT tag is missing! Korean question contains attribute words in the target object phrase ("{last_object_phrase}") but translation lacks <ATT> tag. Please ensure all attribute descriptions for the target object are wrapped in <ATT> tags. Translation: {translated_question[:200]}...'
            }), 400
        
        if '<choice>' not in translated_question:
            return jsonify({'success': False, 'error': 'Translation must include <choice> tag'}), 400
        
        # "And provide..." 문구가 <choice> 태그 뒤에 있는지 확인
        choice_match = re.search(r'<choice>.*?</choice>', translated_question, re.IGNORECASE | re.DOTALL)
        if choice_match:
            choice_end_pos = choice_match.end()
            if 'And provide the bounding box coordinate of the region related to your answer.' not in translated_question[choice_end_pos:]:
                return jsonify({'success': False, 'error': 'The phrase "And provide the bounding box coordinate..." must come AFTER the <choice> tag'}), 400
        else:
            if 'And provide the bounding box coordinate of the region related to your answer.' not in translated_question:
                return jsonify({'success': False, 'error': 'Translation must include the required ending phrase'}), 400
        
        # <choice> 태그에서 각 선택지 텍스트 추출
        choice_match = re.search(r'<choice>(.*?)</choice>', translated_question, re.IGNORECASE)
        choice_texts = {}
        if choice_match:
            choice_content = choice_match.group(1)
            for letter in ['a', 'b', 'c', 'd']:
                pattern = rf'\({letter}\)\s*([^,)]+)'
                match = re.search(pattern, choice_content, re.IGNORECASE)
                if match:
                    choice_texts[letter] = match.group(1).strip()
        
        # 번역 결과에서 앞뒤 대괄호 제거
        cleaned_question = translated_question.strip()
        # 앞의 대괄호 제거 (예: "[Question text..." -> "Question text...")
        if cleaned_question.startswith('[') and cleaned_question.endswith(']'):
            # 전체가 대괄호로 감싸져 있는 경우만 제거
            cleaned_question = cleaned_question[1:-1].strip()
        elif cleaned_question.startswith('['):
            # 앞에만 대괄호가 있는 경우 제거
            cleaned_question = re.sub(r'^\[+\s*', '', cleaned_question).strip()
        
        # "?" 뒤의 "]" 제거
        cleaned_question = re.sub(r'\?\s*\]+\s*', '? ', cleaned_question)
        # 문장 끝의 "]" 제거 (choice 태그 앞)
        cleaned_question = re.sub(r'\]+\s*(?=<choice>)', ' ', cleaned_question, flags=re.IGNORECASE)
        
        return jsonify({
            'success': True,
            'translated_question': cleaned_question,
            'choice_texts': choice_texts
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/translate/rationale', methods=['POST'])
def translate_rationale():
    """Translate Korean rationale to English with image analysis context."""
    data = request.json
    rationale_ko = data.get('rationale_ko', '').strip()
    image_id = data.get('image_id', None)
    view_type = data.get('view_type', 'exo')  # 'exo' or 'ego'
    
    if not rationale_ko:
        return jsonify({'success': False, 'error': 'Rationale (Korean) is required'}), 400
    
    try:
        if not OPENAI_AVAILABLE:
            return jsonify({'success': False, 'error': 'OpenAI library not installed. Install with: pip install openai'}), 500
        
        if not OPENAI_API_KEY or OPENAI_API_KEY == "your-api-key-here":
            return jsonify({'success': False, 'error': 'OPENAI_API_KEY is not set. Please set it in coco_web_annotator.py'}), 500
        
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # 이미지 분석 결과 가져오기 (캐시에서)
        # 캐시 키는 "image_id_model" 형식이므로 모든 모델의 캐시를 확인
        image_analysis = ""
        if image_id:
            # 기본 모델부터 확인
            for model_name in [DEFAULT_MODEL, 'openai']:
                cache_key = f"{image_id}_{model_name}"
                if cache_key in image_analysis_cache:
                    image_analysis = image_analysis_cache[cache_key]
                    break
        
        # Question과 Response 정보 가져오기 (소거법 형식을 위해)
        question = data.get('question', '').strip()
        response = data.get('response', '').strip()  # 예: "(b) vase"
        
        # view 타입에 따라 시작 문구 결정
        question_type = "exo-centric" if view_type == 'exo' else "ego-centric"
        
        # 이미지 분석 컨텍스트
        image_context = ""
        if image_analysis:
            image_context = f"""

IMAGE ANALYSIS CONTEXT:
{image_analysis}

Use this image analysis to better understand the visual context and spatial relationships when translating the rationale."""
        
        # 소거법 형식 가이드
        elimination_guide = ""
        if question and response:
            # Response에서 정답 추출 (예: "(b) vase" -> "b")
            response_match = re.search(r'\(([a-d])\)', response, re.IGNORECASE)
            if response_match:
                correct_answer = response_match.group(1).lower()
                # Choice 태그에서 모든 선택지 추출
                choice_match = re.search(r'<choice>(.*?)</choice>', question, re.IGNORECASE)
                if choice_match:
                    choice_content = choice_match.group(1)
                    choices = {}
                    for letter in ['a', 'b', 'c', 'd']:
                        pattern = rf'\({letter}\)\s*([^,)]+)'
                        match = re.search(pattern, choice_content, re.IGNORECASE)
                        if match:
                            choices[letter] = match.group(1).strip()
                    
                    elimination_guide = f"""

ELIMINATION METHOD FORMAT:
The rationale must follow an elimination method format:
1. Start with "The question is {question_type}:"
2. Explain why each incorrect choice (a, b, c, d) is excluded, EXCEPT for the correct answer ({correct_answer})
3. For each incorrect choice, state why it doesn't match the question criteria
4. Finally, explain why the correct answer ({correct_answer}: {choices.get(correct_answer, '')}) is the right choice
5. End with "Therefore [correct answer] is [the answer/description]." - DO NOT add any additional explanation after "Therefore" sentence
6. CRITICAL: After "Therefore" statement, do NOT add phrases like "as it is...", "because it is...", "since it is...", or any additional descriptive clauses

Example format:
"The question is {question_type}: [Choice a] is excluded because [reason]. [Choice b] is excluded because [reason]. [Choice c] is excluded because [reason]. Therefore [correct answer] is [the answer/description]."

WRONG examples (DO NOT include):
- "Therefore the sandwich is correct, as it is the closest edible object to the wine glass on the table in the restaurant."
- "Therefore the vase is correct because it is the farthest object from the boy."

CORRECT examples:
- "Therefore the sandwich is correct."
- "Therefore the vase is the farthest object from the boy, making the vase correct."

Current question: {question}
Correct answer: {response}
"""
        
        # view_type에 따라 다른 프롬프트 사용
        if view_type == 'ego':
            # ego_data_sample.json 형식 참고
            prompt = f"""Translate the following Korean rationale to English. Follow these CRITICAL requirements for EGO-CENTRIC rationales:{image_context}{elimination_guide}

REQUIREMENTS FOR EGO-CENTRIC RATIONALES:
1. The rationale MUST start with "The question is ego-centric:"
2. Use elimination method format: explain why incorrect choices are excluded, then explain why the correct answer is right
3. The translation must be at least 2 sentences long
4. Make it natural, grammatically correct, and detailed
5. Use the image analysis context to create accurate descriptions of spatial relationships and object positions FROM THE PERSON'S PERSPECTIVE
6. DO NOT include any bounding box coordinates (x1, y1, x2, y2) or coordinate information in the rationale
7. When the Korean rationale mentions choice letters (a, b, c, d), translate them to the corresponding English choice text from the question
8. CRITICAL: End the rationale with a simple "Therefore" statement. DO NOT add additional explanatory clauses after "Therefore" such as "as it is...", "because it is...", "since it is...", or any descriptive phrases that repeat information already stated
9. IMPORTANT: When describing spatial relationships, always clarify the perspective (e.g., "From the person's perspective, the right side corresponds to the left side of the image")

Reference examples from ego_data_sample.json:

Example 1: "The question is ego-centric: The little girl in front of the man has her right side corresponding to the left side of the image. The cake and the camera are positioned in front of her, and the party plate is on her left side. Therefore, the flower is the farthest among the party items."

Example 2: "The question is ego-centric: From the person's perspective, sitting on the right side of the large sofa corresponds to sitting on the left side of the large sofa in the image, and the person's right side aligns with the left side of the image. The large bottle and shoe are located on the person's left side, while the fan is on the right but is not a square-shaped object. Therefore, the TV is the correct answer."

Example 3: "The question is ego-centric: From the woman's perspective, her right side corresponds to the left side of the image. The fork and knife are located on her left side, so they can be excluded. The wine glass, while positioned on the correct side, is made of glass and not a silver object. Therefore, the correct answer is the spoon."

Korean rationale: {rationale_ko}

Translate to English following the format and style of ego_data_sample.json examples."""
        else:
            # exo_data_sample.json 형식 참고
            prompt = f"""Translate the following Korean rationale to English. Follow these CRITICAL requirements:{image_context}{elimination_guide}

REQUIREMENTS:
1. The rationale MUST start with "The question is exo-centric:"
2. Use elimination method format: explain why incorrect choices are excluded, then explain why the correct answer is right
3. The translation must be at least 2 sentences long
4. Make it natural, grammatically correct, and detailed
5. Use the image analysis context to create accurate descriptions of spatial relationships and object positions
6. DO NOT include any bounding box coordinates (x1, y1, x2, y2) or coordinate information in the rationale
7. When the Korean rationale mentions choice letters (a, b, c, d), translate them to the corresponding English choice text from the question
8. CRITICAL: End the rationale with a simple "Therefore" statement. DO NOT add additional explanatory clauses after "Therefore" such as "as it is...", "because it is...", "since it is...", or any descriptive phrases that repeat information already stated

Reference examples from exo_data_sample.json:

Example 1: "The question is exo-centric: The sink is placed immediately adjacent to the refrigerator, making it the closest. The vase sits slightly forward on the counter, farther than the sink but clearly closer than the orange bag at the far right edge and the red chair in the front seating area. Therefore the vase is second-closest."

Example 2: "The question is exo-centric: The laptop and the cell phone are located on the sofa near the brown-haired man, while the handbag is placed on the floor near his feet. The coke bottle is also on the floor, but it is cylindrical, not square-shaped. Therefore the handbag is the only square-shaped object on the floor."

Korean rationale: {rationale_ko}

Translate to English following the format and style of exo_data_sample.json examples."""
        
        # Choice 정보를 rationale 번역에 활용하기 위한 매핑 생성
        choice_mapping = ""
        if question and response:
            choice_match = re.search(r'<choice>(.*?)</choice>', question, re.IGNORECASE)
            if choice_match:
                choice_content = choice_match.group(1)
                choices = {}
                for letter in ['a', 'b', 'c', 'd']:
                    pattern = rf'\({letter}\)\s*([^,)]+)'
                    match = re.search(pattern, choice_content, re.IGNORECASE)
                    if match:
                        choices[letter] = match.group(1).strip()
                
                if choices:
                    choice_mapping = f"""

CHOICE MAPPING (for translating choice letters in Korean rationale):
When the Korean rationale mentions choice letters (a, b, c, d) or Korean choice text, translate them to the corresponding English choice:
{', '.join([f'({k}) {v}' for k, v in choices.items()])}

For example, if the Korean rationale says "(d) 포크" or just "d" or "포크", translate it to "fork" (which is choice (d) fork).
"""
        
        # 프롬프트에 choice 매핑 추가
        enhanced_prompt = prompt + choice_mapping
        
        translated_rationale = ""
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": f"You are a professional translator specializing in VQA (Visual Question Answering) rationales. Always start with 'The question is {question_type}:' and use elimination method format with at least 2 sentences. Never include bounding box coordinates. When Korean rationale mentions choice letters (a, b, c, d) or Korean choice text, translate them to the corresponding English choice text. CRITICAL: End with a simple 'Therefore' statement - do NOT add additional explanatory clauses like 'as it is...', 'because it is...', or 'since it is...' after the 'Therefore' sentence."},
                    {"role": "user", "content": enhanced_prompt}
                ],
                temperature=0.3,
                max_tokens=400
            )
            
            translated_rationale = response.choices[0].message.content.strip()
        except Exception as api_error:
            print(f"[ERROR] API error in rationale translation: {type(api_error).__name__}: {str(api_error)}")
            import traceback
            traceback.print_exc()
            return jsonify({'success': False, 'error': f'Translation API error: {str(api_error)}'}), 500
        
        if not translated_rationale:
            return jsonify({'success': False, 'error': 'Translation returned empty result'}), 500
        
        # 시작 문구 검증
        if not translated_rationale.startswith(f"The question is {question_type}:"):
            # 자동으로 시작 문구 추가
            translated_rationale = f"The question is {question_type}: {translated_rationale}"
        
        # bounding box 좌표 제거 (x1, y1, x2, y2 또는 [x, y, w, h] 형식)
        translated_rationale = re.sub(r'\[?\s*\d+\.?\d*\s*,\s*\d+\.?\d*\s*,\s*\d+\.?\d*\s*,\s*\d+\.?\d*\s*\]?', '', translated_rationale)
        translated_rationale = re.sub(r'bounding box[^.]*\.?', '', translated_rationale, flags=re.IGNORECASE)
        translated_rationale = re.sub(r'bbox[^.]*\.?', '', translated_rationale, flags=re.IGNORECASE)
        translated_rationale = re.sub(r'coordinate[^.]*\.?', '', translated_rationale, flags=re.IGNORECASE)
        translated_rationale = re.sub(r'\(x\d+.*?y\d+.*?\)', '', translated_rationale, flags=re.IGNORECASE)
        
        # "Therefore" 문장 뒤의 추가 설명 제거 (as it is, because it is, since it is 등)
        # "Therefore" 문장 뒤에 ", as it is", ", because it is", ", since it is" 같은 패턴이 있으면 제거
        translated_rationale = re.sub(r'(Therefore[^,.]*?)(,\s*(as|because|since)\s+it\s+is[^.]*?\.)', r'\1.', translated_rationale, flags=re.IGNORECASE)
        # "Therefore" 문장 뒤에 추가 문장이 있고, 그것이 "as it is", "because it is", "since it is"로 시작하면 제거
        translated_rationale = re.sub(r'(Therefore[^.]*\.)\s+((As|Because|Since)\s+it\s+is[^.]*?\.)', r'\1', translated_rationale, flags=re.IGNORECASE)
        # "Therefore" 문장 뒤에 ", as" 또는 ", because" 또는 ", since"로 시작하는 추가 설명이 있으면 제거
        translated_rationale = re.sub(r'(Therefore[^,.]*?)(,\s+(as|because|since)\s+[^.]*?\.)', r'\1.', translated_rationale, flags=re.IGNORECASE)
        # "Therefore" 문장을 찾아서 그 문장의 마침표까지만 남기고, 그 뒤의 모든 추가 설명 제거 (더 안전한 방법)
        # "Therefore" 문장 뒤에 나오는 ", as it is..." 같은 모든 추가 설명 제거
        therefore_match = re.search(r'(Therefore[^.]*?\.)', translated_rationale, re.IGNORECASE)
        if therefore_match:
            therefore_end = therefore_match.end()
            # "Therefore" 문장 뒤에 ", as", ", because", ", since" 같은 패턴이 있으면 제거
            remaining = translated_rationale[therefore_end:].strip()
            if remaining:
                # ", as it is", ", because it is", ", since it is" 같은 패턴 제거
                remaining = re.sub(r'^,\s*(as|because|since)\s+it\s+is[^.]*?\.', '', remaining, flags=re.IGNORECASE)
                # "As it is", "Because it is", "Since it is" 같은 패턴으로 시작하는 문장 제거
                remaining = re.sub(r'^(As|Because|Since)\s+it\s+is[^.]*?\.', '', remaining, flags=re.IGNORECASE)
                # ", as", ", because", ", since" 같은 패턴 제거
                remaining = re.sub(r'^,\s+(as|because|since)\s+[^.]*?\.', '', remaining, flags=re.IGNORECASE)
                translated_rationale = translated_rationale[:therefore_end] + (' ' + remaining if remaining else '')
        
        translated_rationale = re.sub(r'\s+', ' ', translated_rationale).strip()
        
        # 문장 수 확인 (최소 2문장)
        sentences = [s.strip() for s in translated_rationale.split('.') if s.strip()]
        sentence_count = len(sentences)
        
        if sentence_count < 2:
            # 2문장 이상으로 확장
            additional_prompt = f"""The following rationale is too short. Expand it to at least 2 sentences while maintaining the elimination method format. Do NOT include any bounding box coordinates or coordinate information:

Current rationale: {translated_rationale}

Expand it to at least 2 sentences, keeping the same format and style."""
            
            additional_response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a professional translator. Expand the rationale to at least 2 sentences while maintaining the elimination method format. Never include bounding box coordinates."},
                    {"role": "user", "content": additional_prompt}
                ],
                temperature=0.3,
                max_tokens=200
            )
            translated_rationale = additional_response.choices[0].message.content.strip()
            # 다시 bounding box 좌표 제거
            translated_rationale = re.sub(r'\[?\s*\d+\.?\d*\s*,\s*\d+\.?\d*\s*,\s*\d+\.?\d*\s*,\s*\d+\.?\d*\s*\]?', '', translated_rationale)
            translated_rationale = re.sub(r'bounding box[^.]*\.?', '', translated_rationale, flags=re.IGNORECASE)
            translated_rationale = re.sub(r'bbox[^.]*\.?', '', translated_rationale, flags=re.IGNORECASE)
            translated_rationale = re.sub(r'coordinate[^.]*\.?', '', translated_rationale, flags=re.IGNORECASE)
            translated_rationale = re.sub(r'\(x\d+.*?y\d+.*?\)', '', translated_rationale, flags=re.IGNORECASE)
            # "Therefore" 문장 뒤의 추가 설명 제거
            translated_rationale = re.sub(r'(Therefore[^,.]*?)(,\s*(as|because|since)\s+it\s+is[^.]*?\.)', r'\1.', translated_rationale, flags=re.IGNORECASE)
            translated_rationale = re.sub(r'(Therefore[^.]*\.)\s+((As|Because|Since)\s+it\s+is[^.]*?\.)', r'\1', translated_rationale, flags=re.IGNORECASE)
            translated_rationale = re.sub(r'(Therefore[^,.]*?)(,\s+(as|because|since)\s+[^.]*?\.)', r'\1.', translated_rationale, flags=re.IGNORECASE)
            # "Therefore" 문장을 찾아서 그 문장의 마침표까지만 남기고, 그 뒤의 모든 추가 설명 제거
            therefore_match = re.search(r'(Therefore[^.]*?\.)', translated_rationale, re.IGNORECASE)
            if therefore_match:
                therefore_end = therefore_match.end()
                remaining = translated_rationale[therefore_end:].strip()
                if remaining:
                    remaining = re.sub(r'^,\s*(as|because|since)\s+it\s+is[^.]*?\.', '', remaining, flags=re.IGNORECASE)
                    remaining = re.sub(r'^(As|Because|Since)\s+it\s+is[^.]*?\.', '', remaining, flags=re.IGNORECASE)
                    remaining = re.sub(r'^,\s+(as|because|since)\s+[^.]*?\.', '', remaining, flags=re.IGNORECASE)
                    translated_rationale = translated_rationale[:therefore_end] + (' ' + remaining if remaining else '')
            translated_rationale = re.sub(r'\s+', ' ', translated_rationale).strip()
        
        return jsonify({
            'success': True,
            'translated_rationale': translated_rationale
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/review_translation', methods=['POST'])
def review_translation():
    """Review translated question, response, and rationale using GPT-5 for grammar and unnecessary phrases."""
    data = request.json
    question = data.get('question', '').strip()
    response = data.get('response', '').strip()
    rationale = data.get('rationale', '').strip()
    
    if not question and not rationale:
        return jsonify({'success': False, 'error': 'Question or Rationale is required'}), 400
    
    try:
        if not OPENAI_AVAILABLE:
            return jsonify({'success': False, 'error': 'OpenAI library not installed. Install with: pip install openai'}), 500
        
        if not OPENAI_API_KEY or OPENAI_API_KEY == "your-api-key-here":
            return jsonify({'success': False, 'error': 'OPENAI_API_KEY is not set. Please set it in coco_web_annotator.py'}), 500
        
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # 검수 프롬프트 구성
        review_prompt = f"""Review the following English translations for a VQA (Visual Question Answering) task. Check for:
1. Grammar errors and awkward phrasing
2. Unnecessary phrases or redundant words
3. Naturalness and clarity
4. Consistency with VQA format requirements

Question:
{question if question else '(empty)'}

Response:
{response if response else '(empty)'}

Rationale:
{rationale if rationale else '(empty)'}

CRITICAL INSTRUCTIONS:
- If the texts are grammatically correct, natural, and have no unnecessary phrases, respond with ONLY: "OK"
- If there are ANY issues that need revision, you MUST provide the response in EXACTLY this format (do not deviate):

=== Issues Found ===
[Here, explain in detail:
1. Which specific sentences are unnatural or have grammar errors
2. What the errors are (e.g., "The phrase 'X' is awkward because...")
3. What unnecessary phrases exist (e.g., "The word 'Y' is redundant")
4. How to fix each issue (e.g., "Change 'X' to 'Y' for better clarity")
Be very specific and detailed. Point out exact sentences and words that need fixing.]

=== Question (수정) ===
[If Question needs revision, provide the corrected version here. If Question is fine, write "(No changes needed)"]

=== Rationale (수정) ===
[If Rationale needs revision, provide the corrected version here. If Rationale is fine, write "(No changes needed)"]

IMPORTANT:
- You MUST always include the "=== Issues Found ===" section when there are any issues
- Be specific: mention exact sentence numbers, phrases, or words that are problematic
- Explain WHY each issue is a problem and HOW to fix it
- If everything is perfect, respond with ONLY "OK" (nothing else)"""
        
        review_result = None
        review_response = None
        
        try:
            # GPT-4o-mini 사용
            review_response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a professional English grammar and style reviewer for VQA (Visual Question Answering) tasks. Review texts for grammar, naturalness, and unnecessary phrases."},
                    {"role": "user", "content": review_prompt}
                ],
                temperature=0.3,
                max_tokens=1000
            )
        except Exception as api_error:
            print(f"[ERROR] GPT-4o-mini API error: {type(api_error).__name__}: {str(api_error)}")
            import traceback
            traceback.print_exc()
            return jsonify({
                'success': False,
                'error': f'Review API error: {str(api_error)}'
            }), 500
        
        # 응답 검증
        if not review_response:
            print(f"[ERROR] review_response is None")
            return jsonify({
                'success': False,
                'error': 'Failed to get response from GPT API'
            }), 500
        
        if not review_response.choices or len(review_response.choices) == 0:
            print(f"[ERROR] review_response.choices is empty")
            return jsonify({
                'success': False,
                'error': 'GPT API returned empty choices'
            }), 500
        
        if not review_response.choices[0].message or not review_response.choices[0].message.content:
            print(f"[ERROR] review_response.choices[0].message.content is empty")
            return jsonify({
                'success': False,
                'error': 'GPT API returned empty content'
            }), 500
        
        review_result = review_response.choices[0].message.content.strip()
        
        if not review_result or len(review_result) == 0:
            print(f"[ERROR] review_result is empty after strip")
            return jsonify({
                'success': False,
                'error': 'GPT API returned empty result'
            }), 500
        
        # "OK"인지 확인
        review_upper = review_result.upper().strip()
        
        # OK 체크: 다양한 형식의 OK 인식
        # 1. 정확히 "OK"
        # 2. "OK"로 시작하고 짧은 경우 (예: "OK.", "OK\n", "OK ", "OKAY")
        # 3. "OK"만 포함하고 다른 내용이 거의 없는 경우
        is_ok = False
        
        if review_upper == "OK":
            is_ok = True
        elif review_upper.startswith("OK") and len(review_upper) <= 20:
            # "OK"로 시작하고 짧은 경우
            # "OK.", "OK\n", "OK ", "OKAY", "OK -", "OK:" 등 허용
            remaining = review_upper[2:].strip()
            if not remaining or remaining in [".", ":", "-", " ", "\n", "\r", "\r\n"] or remaining.startswith(".") or remaining.startswith(":") or remaining.startswith("-"):
                is_ok = True
        elif "OK" in review_upper and len(review_upper) <= 30:
            # "OK"가 포함되어 있고 전체가 짧은 경우 (예: "The text is OK")
            # 하지만 너무 긴 설명이 있으면 OK가 아님
            ok_index = review_upper.find("OK")
            before_ok = review_upper[:ok_index].strip()
            after_ok = review_upper[ok_index+2:].strip()
            # OK 앞뒤로 중요한 내용이 거의 없으면 OK로 간주
            if len(before_ok) <= 15 and len(after_ok) <= 10:
                is_ok = True
        
        if is_ok:
            return jsonify({
                'success': True,
                'needs_revision': False,
                'message': '검수 통과',
                'review_notes': review_result
            })
        else:
            # 수정이 필요한 경우
            revised_question = None
            revised_rationale = None
            issues_found = None
            
            # review_notes는 항상 채워야 함 (이미 위에서 검증했으므로 여기서는 체크만)
            if not review_result or len(review_result.strip()) == 0:
                print(f"[ERROR] review_result is empty in else block (should not happen)")
                return jsonify({
                    'success': False,
                    'error': 'Review result is empty'
                }), 500
            
            # === Issues Found === 부분 추출 (더 유연한 패턴)
            # 패턴 1: 정확한 형식
            issues_match = re.search(r'=== Issues Found ===\s*([\s\S]*?)(?=\n\n=== Question|=== Rationale|=== Response|$)', review_result, re.IGNORECASE)
            if issues_match:
                issues_found = issues_match.group(1).strip()
            else:
                # 패턴 2: "Issues Found" 또는 "Issues:" 같은 변형
                issues_match2 = re.search(r'(?:Issues Found|Issues:|Problems:|Issues to fix):?\s*([\s\S]*?)(?=\n\n=== Question|=== Rationale|=== Response|$)', review_result, re.IGNORECASE)
                if issues_match2:
                    issues_found = issues_match2.group(1).strip()
                else:
                    # 패턴 3: Issues Found가 없으면 응답의 처음 부분을 Issues로 사용 (Question/Rationale 섹션 전까지)
                    before_question = re.search(r'^([\s\S]*?)(?=\n\n=== Question|=== Rationale|=== Response)', review_result, re.IGNORECASE)
                    if before_question and not review_result.startswith("==="):
                        # OK가 아니고 섹션 헤더가 없는 경우, 전체를 Issues로 간주
                        issues_found = before_question.group(1).strip()
            
            # Issues Found가 여전히 없으면 전체 응답의 일부를 사용
            if not issues_found or len(issues_found) < 10:
                # Question/Rationale 섹션을 제외한 나머지를 Issues로 사용
                temp_result = review_result
                temp_result = re.sub(r'=== Question.*?===.*?(?=\n\n=== Rationale|$)', '', temp_result, flags=re.IGNORECASE | re.DOTALL)
                temp_result = re.sub(r'=== Rationale.*?===.*?$', '', temp_result, flags=re.IGNORECASE | re.DOTALL)
                temp_result = temp_result.strip()
                if temp_result and len(temp_result) > 10:
                    issues_found = temp_result
            
            # === Question (수정) === 부분 추출
            question_match = re.search(r'=== Question \(수정\) ===\s*([\s\S]*?)(?=\n\n=== Rationale|=== Response|$)', review_result, re.IGNORECASE)
            if question_match:
                revised_question = question_match.group(1).strip()
                # "(No changes needed)" 체크
                if revised_question.upper().strip() == "(NO CHANGES NEEDED)":
                    revised_question = None
            
            # === Rationale (수정) === 부분 추출
            rationale_match = re.search(r'=== Rationale \(수정\) ===\s*([\s\S]*?)$', review_result, re.IGNORECASE)
            if rationale_match:
                revised_rationale = rationale_match.group(1).strip()
                # "(No changes needed)" 체크
                if revised_rationale.upper().strip() == "(NO CHANGES NEEDED)":
                    revised_rationale = None
            
            # 최종 검증: Issues Found가 없으면 전체 응답을 Issues로 사용
            if not issues_found or len(issues_found) < 10:
                # Question과 Rationale을 제외한 나머지
                full_text = review_result
                if revised_question:
                    full_text = re.sub(r'=== Question.*?===\s*' + re.escape(revised_question), '', full_text, flags=re.IGNORECASE | re.DOTALL)
                if revised_rationale:
                    full_text = re.sub(r'=== Rationale.*?===\s*' + re.escape(revised_rationale), '', full_text, flags=re.IGNORECASE | re.DOTALL)
                full_text = re.sub(r'=== .*? ===', '', full_text).strip()
                if full_text and len(full_text) > 10:
                    issues_found = full_text
            
            # 최종 검증: Issues Found가 여전히 없고, Question/Rationale도 없으면
            # 전체 응답을 Issues Found로 사용
            if (not issues_found or len(issues_found) < 10) and not revised_question and not revised_rationale:
                issues_found = review_result
            
            # 최종 응답 구성
            response_data = {
                'success': True,
                'needs_revision': True,
                'revised_question': revised_question,
                'revised_rationale': revised_rationale,
                'issues_found': issues_found,
                'review_notes': review_result
            }
            
            return jsonify(response_data)
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/save', methods=['POST'])
def save_annotation():
    """Save annotation data to JSON file."""
    data = request.json
    
    # Validation: Check required fields (bbox는 선택사항)
    required_fields = ['question', 'response', 'view', 'rationale']
    missing_fields = []
    
    for field in required_fields:
        if field == 'view':
            if not data.get(field) or data.get(field).strip() == '':
                missing_fields.append('view')
        else:
            if not data.get(field) or data.get(field).strip() == '':
                missing_fields.append(field)
    
    if missing_fields:
        return jsonify({
            'error': 'Missing required fields',
            'missing_fields': missing_fields,
            'message': f'Please fill in: {", ".join(missing_fields)}'
        }), 400
    
    # Rationale 내용 검증 및 정리: (a), (b), (c), (d) 및 (ATT), (POS), (REL) 같은 패턴 제거
    rationale = data.get('rationale', '').strip()
    if rationale:
        # (a), (b), (c), (d) 패턴 제거
        rationale = re.sub(r'\([abcd]\)', '', rationale, flags=re.IGNORECASE)
        # (ATT), (POS), (REL) 패턴 제거
        rationale = re.sub(r'\(ATT\)', '', rationale, flags=re.IGNORECASE)
        rationale = re.sub(r'\(POS\)', '', rationale, flags=re.IGNORECASE)
        rationale = re.sub(r'\(REL\)', '', rationale, flags=re.IGNORECASE)
        # 연속된 공백 정리
        rationale = re.sub(r'\s+', ' ', rationale).strip()
        # annotation에 정리된 rationale 저장
        data['rationale'] = rationale
    
    # Rationale에 객관식 선지 단어가 포함되어 있는지 검증
    question = data.get('question', '').strip()
    if question and rationale:
        # question에서 <choice> 태그 파싱
        choice_match = re.search(r'<choice>(.*?)</choice>', question, re.IGNORECASE)
        if choice_match:
            choice_content = choice_match.group(1)
            # 각 선지 텍스트 추출
            choices = {}
            for letter in ['a', 'b', 'c', 'd']:
                pattern = rf'\({letter}\)\s*([^,)]+)'
                match = re.search(pattern, choice_content, re.IGNORECASE)
                if match:
                    choices[letter] = match.group(1).strip()
            
            # 선지가 있으면 rationale에 선지 단어가 포함되어 있는지 확인
            if choices:
                all_choice_words = []
                for choice_text in choices.values():
                    # 선지 텍스트를 단어로 분리 (2글자 이상인 단어만)
                    words = [w.lower() for w in choice_text.split() if len(w) > 2]
                    all_choice_words.extend(words)
                
                # rationale을 소문자로 변환하여 검색
                rationale_lower = rationale.lower()
                
                # 선지 단어 중 하나라도 rationale에 포함되어 있는지 확인
                found_words = [word for word in all_choice_words if word in rationale_lower]
                
                if not found_words:
                    return jsonify({
                        'error': 'Rationale must contain words from the choices',
                        'message': f'Rationale에 객관식 선지의 단어가 포함되어야 합니다. 선지: {", ".join(choices.values())}'
                    }), 400
    
    # Get image info
    image_id = data['image_id']
    image_info = annotator.coco.imgs[image_id]
    view_type = data['view']
    
    # image_path 생성: "/파일명" 형식
    image_filename = image_info['file_name']
    relative_image_path = f"/{image_filename}"
    
    # bbox 처리: bbox가 있으면 처리, 없으면 None (선택사항)
    # bbox 좌표를 소수점 둘째자리로 통일
    selected_bboxes = data.get('selected_bboxes', [])
    if selected_bboxes and len(selected_bboxes) > 0:
        # 소수점 둘째자리로 통일
        def round_bbox(bbox):
            if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                return [round(float(coord), 2) if isinstance(coord, (int, float)) else coord for coord in bbox]
            return bbox
        
        rounded_bboxes = [round_bbox(bbox) for bbox in selected_bboxes]
        
        if len(rounded_bboxes) == 1:
            # 단일 bbox인 경우 배열로 감싸지 않고 직접 저장
            bbox_value = rounded_bboxes[0]
        else:
            # 여러 bbox인 경우 배열로 저장
            bbox_value = rounded_bboxes
    else:
        # bbox가 없으면 None으로 저장
        bbox_value = None
    
    annotation = {
        'image_id': data['image_id'],
        'image_path': relative_image_path,  # 상대 경로로 변경
        'image_resolution': f"{image_info['width']}x{image_info['height']}",  # 원본 이미지 크기 (web_annotations_exo.json, web_annotations_ego.json에만 저장)
        'question': data['question'],
        'response': data['response'],
        'rationale': data.get('rationale', ''),  # 이미 정리된 rationale 사용
        'question_ko': data.get('question_ko', ''),  # 한글 질문 추가
        'rationale_ko': data.get('rationale_ko', ''),  # 한글 근거 추가
        'view': view_type,
        'bbox': bbox_value  # 단일 bbox는 배열로 감싸지 않음
    }
    
    # view 타입에 따라 해당 파일 경로 선택
    output_path = annotator.output_json_path_exo if view_type == 'exo' else annotator.output_json_path_ego
    other_output_path = annotator.output_json_path_ego if view_type == 'exo' else annotator.output_json_path_exo
    
    # 파일 잠금을 사용하여 동시 접근 방지 (중복 데이터 방지)
    lock = file_locks[view_type]
    
    with lock:  # 잠금 획득 (다른 작업자가 저장 중이면 대기)
        # 해당 view 타입의 annotations 로드 (잠금 내에서 다시 읽어 최신 데이터 보장)
        view_annotations = []
        if os.path.exists(output_path):
            try:
                with open(output_path, 'r', encoding='utf-8') as f:
                    view_annotations = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                view_annotations = []
        
        # 중복 체크: 같은 image_id가 이미 있는지 확인
        found = False
        for i, ann in enumerate(view_annotations):
            if ann.get('image_id') == data['image_id']:
                view_annotations[i] = annotation  # 덮어쓰기
                found = True
                break
        
        if not found:
            # 중복 확인: 혹시 모를 중복 방지
            if not any(ann.get('image_id') == data['image_id'] for ann in view_annotations):
                view_annotations.append(annotation)  # 새로 추가
            else:
                # 이미 존재하는 경우 업데이트
                for i, ann in enumerate(view_annotations):
                    if ann.get('image_id') == data['image_id']:
                        view_annotations[i] = annotation
                        found = True
                        break
        
        # 다른 view 타입 파일 처리 (다른 view 타입 파일도 잠금 필요)
        other_lock = file_locks['ego' if view_type == 'exo' else 'exo']
        with other_lock:
            other_view_annotations = []
            if os.path.exists(other_output_path):
                try:
                    with open(other_output_path, 'r', encoding='utf-8') as f:
                        other_view_annotations = json.load(f)
                except (FileNotFoundError, json.JSONDecodeError):
                    other_view_annotations = []
            
            # 다른 view 타입 파일에서 같은 image_id 제거
            other_view_annotations = [ann for ann in other_view_annotations if ann.get('image_id') != data['image_id']]
        
        # Save to file (원자적 쓰기: 임시 파일에 쓰고 rename)
        try:
            # 출력 디렉토리 생성
            output_dir = os.path.dirname(output_path)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)
            
            # 현재 view 타입 파일 저장 (원자적 쓰기)
            json_str = json.dumps(view_annotations, indent=2, ensure_ascii=False)
            # bbox 배열을 한 줄로 변경: "bbox": [\n      숫자,\n      ...\n    ] -> "bbox": [숫자, ...]
            json_str = re.sub(
                r'"bbox":\s*\[\s*\n\s*([^\]]+?)\s*\n\s*\]',
                lambda m: f'"bbox": [{re.sub(r"\\s+", " ", m.group(1).strip())}]',
                json_str,
                flags=re.MULTILINE
            )
            
            # 임시 파일에 쓰고 원자적으로 rename (중복 방지)
            temp_fd, temp_path = tempfile.mkstemp(dir=output_dir, suffix='.json.tmp', text=True)
            try:
                with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                    f.write(json_str)
                # 원자적 쓰기: 임시 파일을 최종 파일로 rename
                shutil.move(temp_path, output_path)
            except Exception:
                # 실패 시 임시 파일 정리
                try:
                    os.unlink(temp_path)
                except:
                    pass
                raise
            
            # 다른 view 타입 파일도 저장 (같은 image_id 제거된 버전)
            if other_view_annotations != [] or os.path.exists(other_output_path):
                other_output_dir = os.path.dirname(other_output_path)
                if other_output_dir and not os.path.exists(other_output_dir):
                    os.makedirs(other_output_dir, exist_ok=True)
                
                other_json_str = json.dumps(other_view_annotations, indent=2, ensure_ascii=False)
                # bbox 배열을 한 줄로 변경
                other_json_str = re.sub(
                    r'"bbox":\s*\[\s*\n\s*([^\]]+?)\s*\n\s*\]',
                    lambda m: f'"bbox": [{re.sub(r"\\s+", " ", m.group(1).strip())}]',
                    other_json_str,
                    flags=re.MULTILINE
                )
                
                # 다른 view 타입 파일도 원자적 쓰기 (다시 잠금 필요)
                with other_lock:
                    other_temp_fd, other_temp_path = tempfile.mkstemp(dir=other_output_dir, suffix='.json.tmp', text=True)
                    try:
                        with os.fdopen(other_temp_fd, 'w', encoding='utf-8') as f:
                            f.write(other_json_str)
                        shutil.move(other_temp_path, other_output_path)
                    except Exception:
                        try:
                            os.unlink(other_temp_path)
                        except:
                            pass
                        raise
        
        except (IOError, OSError) as e:
            return jsonify({'error': f'Failed to save: {e}'}), 500
        
        # 전체 annotations도 업데이트 (다음 로드 시 반영)
        annotator._reload_annotations()
        
        # Google Sheets에 저장 (실패해도 로컬 저장은 성공한 것으로 처리)
        # worker_id는 요청에서 가져오거나 config에서 자동으로 사용
        worker_id = data.get('worker_id') or WORKER_ID
        sheets_success = False
        sheets_error = None
        revision_updated = False
        
        if google_sheets_client and worker_id:
            try:
                sheets_success = save_to_google_sheets(
                    worker_id=worker_id,
                    annotation=annotation,
                    image_info=image_info
                )
                if not sheets_success:
                    sheets_error = "Google Sheets 저장 실패 (알 수 없는 오류)"
                
                # 불통 상태이고 수정여부가 아직 업데이트되지 않았다면 업데이트
                # 검수 상태 확인을 위해 시트에서 읽어오기
                sheet_data = read_from_google_sheets(worker_id)
                print(f"[DEBUG] 시트 데이터에서 Image ID {image_id} 검색 중... (총 {len(sheet_data)}개 행)")
                for row in sheet_data:
                    row_image_id = row.get('Image ID', '') or row.get('image_id', '')
                    if str(row_image_id) == str(image_id):
                        review_status = row.get('검수', '') or row.get('검수 상태', '')
                        revision_status = row.get('수정여부', '') or row.get('수정 여부', '')
                        print(f"[DEBUG] Image ID {image_id} 발견 - 검수: {review_status}, 수정여부: {revision_status}")
                        if review_status == '불통' and revision_status != '수정완료' and revision_status != '수정 완료':
                            # 수정여부 열 업데이트
                            print(f"[DEBUG] 수정여부 업데이트 시도 중...")
                            revision_updated = update_revision_status(worker_id, image_id, '수정완료')
                            if revision_updated:
                                print(f"[INFO] Image ID {image_id}의 수정여부를 '수정완료'로 업데이트했습니다.")
                            else:
                                print(f"[WARN] Image ID {image_id}의 수정여부 업데이트 실패")
                        else:
                            print(f"[DEBUG] 업데이트 불필요 - 검수: {review_status}, 수정여부: {revision_status}")
                        break
                else:
                    print(f"[WARN] Image ID {image_id}를 시트 데이터에서 찾을 수 없습니다.")
                        
            except Exception as e:
                sheets_error = str(e)
                print(f"[WARN] Google Sheets 저장 실패: {e}")
                import traceback
                print(f"[WARN] 상세 에러:\n{traceback.format_exc()}")
        elif not google_sheets_client:
            sheets_error = "Google Sheets 클라이언트가 초기화되지 않았습니다"
            print("[WARN] Google Sheets 클라이언트가 초기화되지 않았습니다.")
        elif not worker_id:
            sheets_error = "작업자 ID가 없습니다"
            print("[WARN] 작업자 ID가 없어 Google Sheets에 저장하지 않습니다. config.py에 WORKER_ID를 설정하거나 요청에 worker_id를 포함하세요.")
        
        response_data = {
            'success': True, 
            'updated': found,
            'sheets_saved': sheets_success,
            'sheets_error': sheets_error if not sheets_success else None,
            'revision_updated': revision_updated
        }
        
        return jsonify(response_data)


def save_to_google_sheets(worker_id, annotation, image_info):
    """
    Google Sheets에 어노테이션 저장
    
    Args:
        worker_id: 작업자 ID (예: "worker001")
        annotation: 어노테이션 딕셔너리
        image_info: 이미지 정보 딕셔너리
        
    Returns:
        성공 여부 (bool)
    """
    if not google_sheets_client:
        return False
    
    try:
        # 스프레드시트 열기
        spreadsheet = get_spreadsheet()
        if not spreadsheet:
            return False  # 할당량 초과 등으로 스프레드시트를 열 수 없음
        
        # 작업자별 시트 가져오기 또는 생성
        sheet_name = worker_id
        try:
            worksheet = spreadsheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            # 시트가 없으면 생성
            worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=20)
            # 헤더 추가
            headers = [
                '저장시간', 'Image ID', 'Image Path', 'Image Resolution', 
                'Question', 'Response', 'Rationale', 'View', 'Bbox', 'SKIP'
            ]
            worksheet.append_row(headers)
            # 헤더 스타일 설정 (선택사항)
            try:
                worksheet.format('A1:J1', {'textFormat': {'bold': True}})
            except:
                pass
        
        # Bbox를 문자열로 변환
        bbox_str = ''
        if annotation.get('bbox'):
            if isinstance(annotation['bbox'], list):
                if isinstance(annotation['bbox'][0], list):
                    # 여러 bbox
                    bbox_str = '; '.join([str(b) for b in annotation['bbox']])
                else:
                    # 단일 bbox (배열)
                    bbox_str = str(annotation['bbox'])
            else:
                bbox_str = str(annotation['bbox'])
        
        # 행 데이터 준비
        skip_value = annotation.get('skip', '') or ''
        row_data = [
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),  # 저장시간
            annotation.get('image_id', ''),
            annotation.get('image_path', ''),
            annotation.get('image_resolution', ''),
            annotation.get('question', ''),
            annotation.get('response', ''),
            annotation.get('rationale', ''),
            annotation.get('view', ''),
            bbox_str,
            skip_value
        ]
        
        # 같은 image_id가 이미 있는지 확인 (업데이트)
        existing_rows = worksheet.get_all_values()
        row_to_update = None
        for idx, row in enumerate(existing_rows[1:], start=2):  # 헤더 제외
            if len(row) > 1 and str(row[1]) == str(annotation.get('image_id', '')):
                row_to_update = idx
                break
        
        if row_to_update:
            # 기존 행 업데이트
            worksheet.update(f'A{row_to_update}:J{row_to_update}', [row_data])
        else:
            # 새 행 추가
            worksheet.append_row(row_data)
        
        # 데이터 캐시 무효화 (해당 작업자만)
        clear_sheets_data_cache(worker_id)
        return True
        
    except Exception as e:
        error_msg = str(e)
        print(f"[ERROR] Google Sheets 저장 중 오류: {e}")
        import traceback
        print(f"[ERROR] 상세 스택 트레이스:\n{traceback.format_exc()}")
        # 에러를 다시 발생시켜서 상위에서 처리하도록 함
        raise


def read_from_google_sheets(worker_id, use_cache=True, force_refresh=False):
    """
    Google Sheets에서 작업자의 어노테이션 데이터 읽기 (캐싱 지원)
    
    Args:
        worker_id: 작업자 ID (예: "test")
        use_cache: 캐시 사용 여부 (기본값: True)
        force_refresh: 강제 새로고침 (캐시 무시, 기본값: False)
        
    Returns:
        리스트: 각 행의 데이터 딕셔너리 리스트
        각 딕셔너리는 {'image_id': ..., '검수': ..., '비고': ..., '수정여부': ..., ...} 형태
    """
    if not google_sheets_client:
        return []
    
    global sheets_data_cache
    
    # 캐시 확인 (force_refresh가 False이고 use_cache가 True일 때만)
    if use_cache and not force_refresh:
        if worker_id in sheets_data_cache:
            cache_entry = sheets_data_cache[worker_id]
            with cache_entry['lock']:
                cache_age = time.time() - cache_entry.get('timestamp', 0)
                if cache_age < CACHE_TTL and cache_entry.get('data') is not None:
                    # 캐시 히트 - 캐시된 데이터 반환
                    print(f"[DEBUG] 캐시 히트: {worker_id} (캐시 나이: {cache_age:.1f}초)")
                    return cache_entry['data']
    
    # 캐시 미스 또는 만료 - 실제 API 호출
    print(f"[DEBUG] 캐시 미스: {worker_id} - API 호출")
    
    try:
        # 스프레드시트 열기 (캐싱된 객체 사용)
        spreadsheet = get_spreadsheet()
        if not spreadsheet:
            # 429 에러 등으로 스프레드시트를 열 수 없을 때 캐시된 데이터 반환 시도
            if worker_id in sheets_data_cache:
                cache_entry = sheets_data_cache[worker_id]
                with cache_entry['lock']:
                    if cache_entry.get('data') is not None:
                        print(f"[DEBUG] 스프레드시트 열기 실패, 캐시된 데이터 반환: {worker_id}")
                        return cache_entry['data']
            return []  # 할당량 초과 등으로 스프레드시트를 열 수 없음
        
        # 작업자별 시트 가져오기
        sheet_name = worker_id
        try:
            worksheet = spreadsheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            print(f"[WARN] 시트 '{sheet_name}'를 찾을 수 없습니다.")
            return []
        
        # 모든 데이터 가져오기
        all_values = worksheet.get_all_values()
        if len(all_values) < 2:  # 헤더만 있거나 비어있음
            return []
        
        # 헤더 추출
        headers = all_values[0]
        
        # 헤더 인덱스 찾기
        header_indices = {}
        for idx, header in enumerate(headers):
            header_indices[header] = idx
        
        # 데이터 행 처리
        result = []
        for row in all_values[1:]:  # 헤더 제외
            if len(row) == 0 or not row[1]:  # Image ID가 없으면 스킵
                continue
            
            row_data = {}
            for header, idx in header_indices.items():
                if idx < len(row):
                    row_data[header] = row[idx]
                else:
                    row_data[header] = ''
            
            result.append(row_data)
        
        # 캐시에 저장 (성공한 경우만)
        if worker_id not in sheets_data_cache:
            sheets_data_cache[worker_id] = {
                'data': [],
                'timestamp': 0,
                'lock': threading.Lock()
            }
        
        with sheets_data_cache[worker_id]['lock']:
            sheets_data_cache[worker_id]['data'] = result
            sheets_data_cache[worker_id]['timestamp'] = time.time()
            print(f"[DEBUG] 캐시 저장: {worker_id} ({len(result)}개 행)")
        
        return result
        
    except gspread.exceptions.APIError as e:
        # APIError의 response는 requests.Response 객체이므로 status_code를 사용
        error_code = getattr(e.response, 'status_code', None)
        if error_code == 429:
            # 할당량 초과 에러 - 스프레드시트 캐시 무효화
            clear_spreadsheet_cache()
            # 데이터 캐시는 유지 (오래된 데이터라도 보여주는 것이 나음)
            # 캐시가 있으면 캐시된 데이터 반환 시도
            if worker_id in sheets_data_cache:
                cache_entry = sheets_data_cache[worker_id]
                with cache_entry['lock']:
                    if cache_entry.get('data') is not None:
                        print(f"[DEBUG] 429 에러 발생, 캐시된 데이터 반환: {worker_id}")
                        return cache_entry['data']
            # 캐시가 없으면 빈 리스트 반환
            return []
        else:
            # 429가 아닌 다른 에러만 로그 출력
            print(f"[ERROR] Google Sheets API 오류 ({error_code}): {e}")
            return []
    except Exception as e:
        print(f"[ERROR] Google Sheets 읽기 중 오류: {e}")
        import traceback
        print(f"[ERROR] 상세 스택 트레이스:\n{traceback.format_exc()}")
        return []


def update_revision_status(worker_id, image_id, status='수정완료'):
    """
    Google Sheets의 수정여부 열 업데이트
    
    Args:
        worker_id: 작업자 ID
        image_id: 이미지 ID
        status: 업데이트할 상태 (기본값: '수정 완료')
        
    Returns:
        성공 여부 (bool)
    """
    if not google_sheets_client:
        return False
    
    try:
        spreadsheet = get_spreadsheet()
        if not spreadsheet:
            return False  # 할당량 초과 등으로 스프레드시트를 열 수 없음
        worksheet = spreadsheet.worksheet(worker_id)
        
        # 모든 데이터 가져오기
        all_values = worksheet.get_all_values()
        if len(all_values) < 2:
            return False
        
        # 헤더에서 열 인덱스 찾기
        headers = all_values[0]
        print(f"[DEBUG] 헤더 목록: {headers}")
        image_id_col = None
        revision_status_col = None
        
        for idx, header in enumerate(headers):
            header_clean = header.strip()
            if header_clean == 'Image ID' or header_clean == 'image_id':
                image_id_col = idx
                print(f"[DEBUG] Image ID 열 발견: 인덱스 {idx}")
            if header_clean == '수정여부' or header_clean == '수정 여부':
                revision_status_col = idx
                print(f"[DEBUG] 수정여부 열 발견: 인덱스 {idx}")
        
        if image_id_col is None:
            print("[WARN] Image ID 열을 찾을 수 없습니다.")
            print(f"[WARN] 사용 가능한 헤더: {headers}")
            return False
        
        if revision_status_col is None:
            print("[WARN] 수정여부 열을 찾을 수 없습니다.")
            print(f"[WARN] 사용 가능한 헤더: {headers}")
            return False
        
        # 해당 image_id 찾아서 업데이트
        for row_idx, row in enumerate(all_values[1:], start=2):  # 헤더 제외, 1-based index
            if len(row) > image_id_col and str(row[image_id_col]) == str(image_id):
                # 수정여부 열 업데이트 (update_cell 사용: row, col은 1-based)
                # revision_status_col은 0-based이므로 +1 해서 1-based로 변환
                worksheet.update_cell(row_idx, revision_status_col + 1, status)
                print(f"[INFO] Image ID {image_id}의 수정여부를 '{status}'로 업데이트했습니다. (셀: 행{row_idx}, 열{revision_status_col + 1})")
                return True
        
        print(f"[WARN] Image ID {image_id}를 찾을 수 없습니다.")
        return False
        
    except Exception as e:
        print(f"[ERROR] 수정여부 업데이트 중 오류: {e}")
        import traceback
        print(f"[ERROR] 상세 스택 트레이스:\n{traceback.format_exc()}")
        return False


def remove_duplicate_annotations(json_path):
    """
    JSON 파일에서 중복된 어노테이션 제거 (같은 image_id가 여러 개 있는 경우)
    가장 최근 것만 유지 (또는 첫 번째 것만 유지)
    
    Args:
        json_path: JSON 파일 경로
        
    Returns:
        제거된 중복 개수
    """
    if not os.path.exists(json_path):
        return 0
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            annotations = json.load(f)
        
        # image_id를 키로 하는 딕셔너리로 변환 (중복 시 마지막 것만 유지)
        seen = {}
        duplicates_removed = 0
        
        for ann in annotations:
            image_id = ann.get('image_id')
            if image_id is not None:
                if image_id in seen:
                    duplicates_removed += 1
                seen[image_id] = ann
        
        # 중복이 있으면 파일 저장
        if duplicates_removed > 0:
            # 딕셔너리를 리스트로 변환
            unique_annotations = list(seen.values())
            
            # 원자적 쓰기로 저장
            output_dir = os.path.dirname(json_path)
            json_str = json.dumps(unique_annotations, indent=2, ensure_ascii=False)
            # bbox 배열을 한 줄로 변경
            json_str = re.sub(
                r'"bbox":\s*\[\s*\n\s*([^\]]+?)\s*\n\s*\]',
                lambda m: f'"bbox": [{re.sub(r"\\s+", " ", m.group(1).strip())}]',
                json_str,
                flags=re.MULTILINE
            )
            
            temp_fd, temp_path = tempfile.mkstemp(dir=output_dir, suffix='.json.tmp', text=True)
            try:
                with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                    f.write(json_str)
                shutil.move(temp_path, json_path)
            except Exception:
                try:
                    os.unlink(temp_path)
                except:
                    pass
                raise
            
            print(f"[INFO] {json_path}: {duplicates_removed}개 중복 어노테이션 제거됨")
        
        return duplicates_removed
    except Exception as e:
        print(f"[ERROR] {json_path} 중복 제거 실패: {e}")
        return 0


@app.route('/api/sync_from_sheets', methods=['GET'])
def sync_from_sheets():
    """
    Google Sheets에서 현재 작업자의 데이터 동기화
    """
    try:
        worker_id = WORKER_ID
        if not worker_id:
            return jsonify({'error': '작업자 ID가 설정되지 않았습니다.'}), 400
        
        # 구글 시트에서 데이터 읽기
        sheet_data = read_from_google_sheets(worker_id)
        
        # 검수 상태별로 분류
        passed_images = []  # 통과
        failed_images = []  # 불통
        completed_images = []  # 납품 완료
        
        for row in sheet_data:
            image_id = row.get('Image ID', '') or row.get('image_id', '')
            review_status = row.get('검수', '') or row.get('검수 상태', '')
            note = row.get('비고', '') or row.get('검수 의견', '')
            revision_status = row.get('수정여부', '') or row.get('수정 여부', '')
            view = row.get('View', '') or row.get('view', '')
            
            if not image_id:
                continue
            
            # view 필터링: ego만 처리 (클라이언트에서 ego_images만 사용)
            if view and view.lower() != 'ego':
                continue
            
            image_info = {
                'image_id': int(image_id) if image_id.isdigit() else image_id,
                'review_status': review_status,
                'note': note,
                'revision_status': revision_status,
                'row_data': row
            }
            
            if review_status == '통과':
                passed_images.append(image_info)
            elif review_status == '불통':
                failed_images.append(image_info)
            elif review_status == '납품 완료':
                completed_images.append(image_info)
        
        # image_id로 정렬하여 일관성 보장
        passed_images.sort(key=lambda x: x['image_id'] if isinstance(x['image_id'], int) else 0)
        failed_images.sort(key=lambda x: x['image_id'] if isinstance(x['image_id'], int) else 0)
        completed_images.sort(key=lambda x: x['image_id'] if isinstance(x['image_id'], int) else 0)
        
        return jsonify({
            'success': True,
            'worker_id': worker_id,
            'passed': passed_images,
            'failed': failed_images,
            'completed': completed_images,
            'total': len(sheet_data)
        })
        
    except Exception as e:
        print(f"[ERROR] 구글 시트 동기화 중 오류: {e}")
        import traceback
        print(f"[ERROR] 상세 스택 트레이스:\n{traceback.format_exc()}")
        return jsonify({'error': f'동기화 실패: {str(e)}'}), 500


@app.route('/api/get_review_status/<int:image_id>', methods=['GET'])
def get_review_status(image_id):
    """
    특정 이미지의 검수 상태만 가져오기
    """
    try:
        worker_id = request.args.get('worker_id') or WORKER_ID
        if not worker_id:
            return jsonify({'error': '작업자 ID가 필요합니다.'}), 400
        
        # 구글 시트에서 데이터 읽기
        sheet_data = read_from_google_sheets(worker_id)
        
        # 해당 image_id 찾기
        for row in sheet_data:
            row_image_id = row.get('Image ID', '') or row.get('image_id', '')
            if str(row_image_id) == str(image_id):
                review_status = row.get('검수', '') or row.get('검수 상태', '')
                note = row.get('비고', '') or row.get('검수 의견', '')
                revision_status = row.get('수정여부', '') or row.get('수정 여부', '')
                
                return jsonify({
                    'success': True,
                    'image_id': image_id,
                    'review_status': review_status,
                    'note': note,
                    'revision_status': revision_status
                })
        
        # 찾지 못한 경우
        return jsonify({
            'success': False,
            'message': 'Image ID not found in sheet.'
        })
        
    except gspread.exceptions.APIError as e:
        # 429 에러는 조용히 처리 (로그 최소화)
        error_code = getattr(e.response, 'status_code', None)
        if error_code == 429:
            # 429 에러는 빈 응답 반환 (로그 출력하지 않음)
            return jsonify({
                'success': False,
                'image_id': image_id,
                'status': '',
                'note': '',
                'revision_status': ''
            }), 200
        else:
            print(f"[ERROR] 검수 상태 조회 중 오류 ({error_code}): {e}")
            return jsonify({'error': f'검수 상태 조회 실패: {str(e)}'}), 500
    except Exception as e:
        print(f"[ERROR] 검수 상태 조회 중 오류: {e}")
        return jsonify({'error': f'검수 상태 조회 실패: {str(e)}'}), 500


@app.route('/api/images_by_status', methods=['GET'])
def get_images_by_status():
    """
    상태별로 이미지 리스트를 필터링하여 반환
    Query parameters:
        - status: 'all', 'unfinished', 'working', 'passed', 'failed', 'delivered', 'completed', 'skipped'
        - worker_id: 작업자 ID (선택, 없으면 WORKER_ID 사용)
    """
    try:
        worker_id = request.args.get('worker_id') or WORKER_ID
        status = request.args.get('status', 'all')
        
        if not worker_id:
            return jsonify({'error': '작업자 ID가 필요합니다.'}), 400
        
        # Google Sheets에서 데이터 읽기
        try:
            sheet_data = read_from_google_sheets(worker_id)
        except Exception as e:
            # 429 에러 등으로 읽기 실패 시 빈 리스트 반환
            print(f"[WARN] 상태별 이미지 조회 중 Google Sheets 읽기 실패: {e}")
            sheet_data = []
        
        # 모든 이미지 ID 가져오기 (exo_images와 ego_images 둘 다 확인)
        all_image_ids = []
        for image_id in annotator.image_ids:
            image_info = annotator.coco.imgs[image_id]
            file_name = image_info.get('file_name', '')
            exo_path = os.path.join(annotator.exo_images_folder, file_name)
            ego_path = os.path.join(annotator.ego_images_folder, file_name)
            # exo 또는 ego 폴더 중 하나라도 존재하면 포함
            if os.path.exists(exo_path) or os.path.exists(ego_path):
                all_image_ids.append(image_id)
        
        # Google Sheets 데이터를 image_id로 매핑 (view 필터링 없이 모든 데이터 포함)
        sheet_data_map = {}
        for row in sheet_data:
            image_id_str = row.get('Image ID', '') or row.get('image_id', '')
            if image_id_str:
                try:
                    image_id = int(image_id_str)
                    sheet_data_map[image_id] = {
                        'review_status': row.get('검수', '') or row.get('검수 상태', ''),
                        '저장시간': row.get('저장시간', ''),
                        '수정여부': row.get('수정여부', '') or row.get('수정 여부', ''),
                        '비고': row.get('비고', '') or row.get('검수 의견', ''),
                        'view': row.get('View', '') or row.get('view', ''),
                        'skip': row.get('SKIP', '') or row.get('skip', '') or row.get('스킵', '')
                    }
                except ValueError:
                    continue
        
        # 상태별로 필터링
        filtered_images = []
        
        for image_id in all_image_ids:
            sheet_info = sheet_data_map.get(image_id, {})
            review_status = sheet_info.get('review_status', '')
            저장시간 = sheet_info.get('저장시간', '')
            
            # 상태 판단
            skip_status = sheet_info.get('skip', '').strip().upper()
            image_status = 'unfinished'  # 기본값
            if skip_status == 'SKIP' or skip_status == 'Y' or skip_status == 'YES':
                image_status = 'skipped'
            elif review_status == '통과':
                image_status = 'passed'
            elif review_status == '불통':
                image_status = 'failed'
            elif review_status == '납품 완료':
                image_status = 'delivered'
            elif 저장시간 and not review_status:
                # 작업: 저장시간이 있지만 검수 상태가 없는 것 (SKIP은 이미 제외됨)
                image_status = 'working'
            elif 저장시간:  # 저장은 했지만 검수 상태가 없는 경우 (기타)
                image_status = 'completed'
            
            # 필터링
            if status == 'all':
                filtered_images.append({
                    'image_id': image_id,
                    'status': image_status,
                    'review_status': review_status,
                    '저장시간': 저장시간,
                    '수정여부': sheet_info.get('수정여부', ''),
                    '비고': sheet_info.get('비고', '')
                })
            elif status == 'unfinished':
                # 미작업: Google Sheets에 있지만 다른 상태가 아닌 것
                # (작업, 납품완료, 통과, 불통, 검수대기, SKIP이 아닌 것)
                if image_status == 'unfinished':
                    filtered_images.append({
                        'image_id': image_id,
                        'status': image_status,
                        'review_status': review_status,
                        '저장시간': 저장시간,
                        '수정여부': sheet_info.get('수정여부', ''),
                        '비고': sheet_info.get('비고', '')
                    })
            elif status == 'passed' and image_status == 'passed':
                filtered_images.append({
                    'image_id': image_id,
                    'status': image_status,
                    'review_status': review_status,
                    '저장시간': 저장시간,
                    '수정여부': sheet_info.get('수정여부', ''),
                    '비고': sheet_info.get('비고', '')
                })
            elif status == 'failed' and image_status == 'failed':
                filtered_images.append({
                    'image_id': image_id,
                    'status': image_status,
                    'review_status': review_status,
                    '저장시간': 저장시간,
                    '수정여부': sheet_info.get('수정여부', ''),
                    '비고': sheet_info.get('비고', '')
                })
            elif status == 'skipped' and image_status == 'skipped':
                filtered_images.append({
                    'image_id': image_id,
                    'status': image_status,
                    'review_status': review_status,
                    '저장시간': 저장시간,
                    '수정여부': sheet_info.get('수정여부', ''),
                    '비고': sheet_info.get('비고', '')
                })
            elif status == 'working' and image_status == 'working':
                filtered_images.append({
                    'image_id': image_id,
                    'status': image_status,
                    'review_status': review_status,
                    '저장시간': 저장시간,
                    '수정여부': sheet_info.get('수정여부', ''),
                    '비고': sheet_info.get('비고', '')
                })
            elif status == 'delivered' and image_status == 'delivered':
                filtered_images.append({
                    'image_id': image_id,
                    'status': image_status,
                    'review_status': review_status,
                    '저장시간': 저장시간,
                    '수정여부': sheet_info.get('수정여부', ''),
                    '비고': sheet_info.get('비고', '')
                })
            elif status == 'completed' and image_status == 'completed':
                filtered_images.append({
                    'image_id': image_id,
                    'status': image_status,
                    'review_status': review_status,
                    '저장시간': 저장시간,
                    '수정여부': sheet_info.get('수정여부', ''),
                    '비고': sheet_info.get('비고', '')
                })
            elif status == 'pending':
                # 검수 대기: 불통 상태이면서 수정완료인 것
                if image_status == 'failed' and sheet_info.get('수정여부', '').strip() in ['수정완료', '수정 완료']:
                    filtered_images.append({
                        'image_id': image_id,
                        'status': 'pending',
                        'review_status': review_status,
                        '저장시간': 저장시간,
                        '수정여부': sheet_info.get('수정여부', ''),
                        '비고': sheet_info.get('비고', '')
                    })
        
        # 미작업 필터링: Google Sheets에 없는 이미지도 포함
        if status == 'unfinished':
            for image_id in all_image_ids:
                if image_id not in sheet_data_map:
                    # Google Sheets에 없는 이미지는 미작업
                    filtered_images.append({
                        'image_id': image_id,
                        'status': 'unfinished',
                        'review_status': '',
                        '저장시간': '',
                        '수정여부': '',
                        '비고': ''
                    })
        
        # image_id로 정렬
        filtered_images.sort(key=lambda x: x['image_id'])
        
        return jsonify({
            'success': True,
            'status': status,
            'images': filtered_images,
            'count': len(filtered_images)
        })
        
    except Exception as e:
        print(f"[ERROR] 상태별 이미지 조회 중 오류: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'조회 실패: {str(e)}'}), 500


@app.route('/api/skip', methods=['POST'])
def skip_image():
    """이미지를 SKIP 상태로 표시"""
    try:
        data = request.json
        image_id = data.get('image_id')
        worker_id = data.get('worker_id') or WORKER_ID
        
        if not image_id:
            return jsonify({'error': 'image_id가 필요합니다.'}), 400
        
        if not worker_id:
            return jsonify({'error': '작업자 ID가 필요합니다.'}), 400
        
        # Google Sheets에 SKIP 상태 저장
        if not google_sheets_client:
            return jsonify({'error': 'Google Sheets 클라이언트가 초기화되지 않았습니다.'}), 500
        
        spreadsheet = get_spreadsheet()
        if not spreadsheet:
            return False  # 할당량 초과 등으로 스프레드시트를 열 수 없음
        sheet_name = worker_id
        
        try:
            worksheet = spreadsheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            # 시트가 없으면 생성
            worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=20)
            # 헤더 추가
            headers = [
                '저장시간', 'Image ID', 'Image Path', 'Image Resolution', 
                'Question', 'Response', 'Rationale', 'View', 'Bbox', 'SKIP'
            ]
            worksheet.append_row(headers)
            # 헤더 스타일 설정 (선택사항)
            try:
                worksheet.format('A1:J1', {'textFormat': {'bold': True}})
            except:
                pass
        
        # 기존 행 찾기 (API 호출 최소화: find 메서드 사용)
        row_to_update = None
        try:
            # Image ID 컬럼(B열)에서 특정 image_id 찾기
            cell = worksheet.find(str(image_id), in_column=2)  # B열 = Image ID
            if cell:
                row_to_update = cell.row
                print(f"[DEBUG] Image ID {image_id}를 행 {row_to_update}에서 찾음")
        except gspread.exceptions.CellNotFound:
            print(f"[DEBUG] Image ID {image_id}를 찾을 수 없음 (새 행 추가)")
            row_to_update = None
        except Exception as e:
            print(f"[WARN] find 메서드 실패, 전체 검색으로 대체: {e}")
            # find 실패 시 전체 검색 (최후의 수단)
            try:
                existing_rows = worksheet.get_all_values()
                for idx, row in enumerate(existing_rows[1:], start=2):  # 헤더 제외
                    if len(row) > 1 and str(row[1]) == str(image_id):
                        row_to_update = idx
                        break
            except Exception as e2:
                print(f"[ERROR] 전체 검색도 실패: {e2}")
                raise
        
        if row_to_update:
            # 먼저 헤더 확인하여 SKIP 컬럼 위치 확인
            headers = worksheet.row_values(1)
            print(f"[DEBUG] 헤더 목록: {headers}")
            skip_col_index = None
            for idx, header in enumerate(headers, start=1):
                header_upper = header.strip().upper() if header else ''
                if header_upper in ['SKIP', '스킵']:
                    skip_col_index = idx
                    print(f"[DEBUG] SKIP 헤더를 인덱스 {idx}에서 찾음: '{header}'")
                    break
            
            if not skip_col_index:
                # 헤더에 SKIP 컬럼이 없으면 에러
                print(f"[ERROR] SKIP 헤더를 찾을 수 없음. 헤더 개수: {len(headers)}")
                print(f"[ERROR] 헤더 목록: {headers}")
                return jsonify({'error': 'SKIP 컬럼을 찾을 수 없습니다. Google Sheets에 SKIP 헤더가 있는지 확인해주세요.'}), 500
            
            # 헤더에서 찾은 컬럼 사용 (A=1, B=2, ..., Z=26, AA=27, ...)
            if skip_col_index <= 26:
                col_letter = chr(64 + skip_col_index)  # A=65, B=66, ..., Z=90
            else:
                # 26개 이상인 경우 (AA, AB, ...)
                first_letter = chr(64 + ((skip_col_index - 1) // 26))
                second_letter = chr(64 + ((skip_col_index - 1) % 26) + 1)
                col_letter = first_letter + second_letter
            
            print(f"[DEBUG] SKIP 컬럼 위치: {col_letter}{row_to_update} (인덱스: {skip_col_index}, 헤더: '{headers[skip_col_index-1] if skip_col_index <= len(headers) else 'N/A'}')")
            
            # SKIP 값 업데이트 (확실하게 저장)
            print(f"[DEBUG] SKIP 값 업데이트: {col_letter}{row_to_update} (행: {row_to_update}, 열: {skip_col_index})")
            try:
                # SKIP 열에만 'skip' 표시 (소문자)
                # 다른 열의 값은 건드리지 않음
                worksheet.update(f'{col_letter}{row_to_update}', [['skip']])
                print(f"[DEBUG] SKIP 저장 성공: Image ID {image_id}, 위치: {col_letter}{row_to_update}")
                # 데이터 캐시 무효화 (해당 작업자만)
                clear_sheets_data_cache(worker_id)
            except Exception as e:
                print(f"[ERROR] SKIP 값 업데이트 실패: {e}")
                import traceback
                traceback.print_exc()
                raise
        else:
            # 새 행 추가 (최소한의 데이터)
            headers = worksheet.row_values(1)
            print(f"[DEBUG] 새 행 추가 - 헤더 목록: {headers}")
            
            # SKIP 컬럼 위치 찾기
            skip_col_index = None
            for idx, header in enumerate(headers, start=1):
                header_upper = header.strip().upper() if header else ''
                if header_upper in ['SKIP', '스킵']:
                    skip_col_index = idx
                    print(f"[DEBUG] 새 행 추가 - SKIP 헤더를 인덱스 {idx}에서 찾음: '{header}'")
                    break
            
            if not skip_col_index:
                print(f"[ERROR] 새 행 추가 - SKIP 헤더를 찾을 수 없음. 헤더 개수: {len(headers)}")
                return jsonify({'error': 'SKIP 컬럼을 찾을 수 없습니다. Google Sheets에 SKIP 헤더가 있는지 확인해주세요.'}), 500
            
            image_info = annotator.coco.imgs.get(image_id, {})
            file_name = image_info.get('file_name', '')
            # Image Path를 "/000000060515.jpg" 형식으로 변경
            image_path = f"/{file_name}" if file_name else f"/{image_id:012d}.jpg"
            
            # 헤더 개수만큼 빈 리스트 생성
            row_data = [''] * len(headers)
            
            # 기본 필수 데이터만 채우기
            # 저장시간 찾기
            for idx, header in enumerate(headers):
                if header and '저장시간' in header:
                    row_data[idx] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    break
            
            # Image ID 찾기
            for idx, header in enumerate(headers):
                if header and ('Image ID' in header or 'image_id' in header.lower()):
                    row_data[idx] = image_id
                    break
            
            # Image Path 찾기
            for idx, header in enumerate(headers):
                if header and ('Image Path' in header or 'image_path' in header.lower()):
                    row_data[idx] = image_path
                    break
            
            # View 찾기
            for idx, header in enumerate(headers):
                if header and header.strip().upper() == 'VIEW':
                    row_data[idx] = 'ego'
                    break
            
            # SKIP 열에만 'skip' 저장 (정확한 위치)
            row_data[skip_col_index - 1] = 'skip'  # 인덱스는 0부터 시작하므로 -1
            
            print(f"[DEBUG] 새 행 추가 - row_data: {row_data}")
            print(f"[DEBUG] 새 행 추가 - SKIP 값은 {skip_col_index}번째 열({chr(64 + skip_col_index) if skip_col_index <= 26 else 'N/A'})에 저장됨")
            worksheet.append_row(row_data)
            print(f"[DEBUG] SKIP 새 행 추가 성공: Image ID {image_id}")
            # 데이터 캐시 무효화 (해당 작업자만)
            clear_sheets_data_cache(worker_id)
        
        return jsonify({
            'success': True,
            'message': 'SKIP 상태로 저장되었습니다.',
            'image_id': image_id
        })
        
    except gspread.exceptions.APIError as e:
        # APIError의 response는 requests.Response 객체이므로 status_code를 사용
        error_code = getattr(e.response, 'status_code', None)
        if error_code == 429:
            # 할당량 초과 에러
            # 429 에러는 조용히 처리 (로그 출력하지 않음)
            return jsonify({
                'error': 'Google Sheets API 할당량이 초과되었습니다. 잠시 후 다시 시도해주세요.',
                'error_code': 429,
                'retry_after': 60  # 60초 후 재시도 권장
            }), 429
        else:
            print(f"[ERROR] Google Sheets API 오류 ({error_code}): {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'error': f'Google Sheets API 오류: {str(e)}'}), 500
    except Exception as e:
        print(f"[ERROR] SKIP 저장 중 오류: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'SKIP 저장 실패: {str(e)}'}), 500


@app.route('/api/work_statistics', methods=['GET'])
def get_work_statistics():
    """작업 통계 및 진행률 계산"""
    try:
        worker_id = request.args.get('worker_id') or WORKER_ID
        if not worker_id:
            return jsonify({'error': '작업자 ID가 필요합니다.'}), 400
        
        sheet_data = read_from_google_sheets(worker_id)
        print(f"[DEBUG] Google Sheets에서 읽은 데이터 개수: {len(sheet_data)}")
        
        # 모든 이미지 개수 (exo + ego)
        all_image_count = 0
        for image_id in annotator.image_ids:
            image_info = annotator.coco.imgs[image_id]
            file_name = image_info.get('file_name', '')
            exo_path = os.path.join(annotator.exo_images_folder, file_name)
            ego_path = os.path.join(annotator.ego_images_folder, file_name)
            if os.path.exists(exo_path) or os.path.exists(ego_path):
                all_image_count += 1
        
        # 상태별 카운트
        stats = {
            'total': all_image_count,
            'unfinished': 0,
            'working': 0,  # 작업: 구글시트에 저장시간이 있지만 검수가 안된 것 (SKIP 제외)
            'passed': 0,
            'failed': 0,
            'delivered': 0,
            'completed': 0,
            'skipped': 0
        }
        
        # Google Sheets 데이터를 image_id로 매핑
        sheet_data_map = {}
        for row in sheet_data:
            # Image ID 찾기 (여러 가능한 컬럼명 시도)
            image_id_str = row.get('Image ID', '') or row.get('image_id', '') or row.get('Image ID', '')
            if not image_id_str:
                continue
            
            try:
                image_id = int(image_id_str)
                # View 컬럼 확인 (exo 또는 ego 모두 포함)
                view = row.get('View', '') or row.get('view', '') or ''
                
                # SKIP 컬럼 값 읽기 (대소문자 구분 없이)
                skip_value = row.get('SKIP', '') or row.get('skip', '') or row.get('스킵', '')
                # 검수 상태 읽기 (여러 가능한 컬럼명 시도)
                review_status = row.get('검수', '') or row.get('검수 상태', '') or row.get('검수', '')
                저장시간 = row.get('저장시간', '') or row.get('저장 시간', '')
                수정여부 = row.get('수정여부', '') or row.get('수정 여부', '')
                
                sheet_data_map[image_id] = {
                    'review_status': review_status,
                    '저장시간': 저장시간,
                    'skip': skip_value,  # 원본 값 저장 (나중에 .strip().upper() 처리)
                    '수정여부': 수정여부,
                    'view': view
                }
                
                # 디버깅: 모든 데이터 출력
                print(f"[DEBUG] Image ID {image_id}: View='{view}', 검수='{review_status}', SKIP='{skip_value}', 수정여부='{수정여부}'")
            except (ValueError, TypeError) as e:
                print(f"[WARN] Image ID 변환 실패: '{image_id_str}' - {e}")
                continue
        
        # Google Sheets에 있는 모든 image_id에 대해 상태 확인
        # annotator.image_ids에 없는 image_id도 Google Sheets에 있으면 포함
        processed_image_ids = set()  # 이미 처리한 image_id 추적
        
        # 1단계: Google Sheets에 있는 모든 image_id 처리
        print(f"[DEBUG] sheet_data_map에 있는 image_id 개수: {len(sheet_data_map)}")
        print(f"[DEBUG] sheet_data_map의 키: {list(sheet_data_map.keys())}")
        
        for image_id in sheet_data_map.keys():
            print(f"[DEBUG] 처리 중인 Image ID: {image_id}")
            sheet_info = sheet_data_map[image_id]
            review_status = sheet_info.get('review_status', '')
            저장시간 = sheet_info.get('저장시간', '')
            skip_status_raw = sheet_info.get('skip', '')
            skip_status = skip_status_raw.strip().upper() if skip_status_raw else ''
            revision_status = sheet_info.get('수정여부', '')
            view = sheet_info.get('view', '')
            
            print(f"[DEBUG] Image ID {image_id} 상태 확인: view='{view}', review_status='{review_status}', skip_status='{skip_status}', revision_status='{revision_status}'")
            
            processed_image_ids.add(image_id)
            
            # SKIP 상태 우선 확인 (가장 먼저 확인)
            if skip_status and (skip_status == 'SKIP' or skip_status == 'Y' or skip_status == 'YES'):
                stats['skipped'] += 1
                print(f"[DEBUG] SKIP 카운트: Image ID {image_id}, skip_status='{skip_status}' (원본: '{skip_status_raw}')")
                continue  # SKIP이면 다른 상태 확인하지 않음
            
            # 검수 상태 확인
            if review_status == '통과':
                stats['passed'] += 1
                print(f"[DEBUG] 통과 카운트: Image ID {image_id}, review_status='{review_status}'")
            elif review_status == '불통':
                # 불통: 수정완료가 아닌 불통 상태만 카운트
                # 검수 대기(수정완료)는 별도로 계산
                if revision_status != '수정완료' and revision_status != '수정 완료':
                    stats['failed'] += 1
                    print(f"[DEBUG] 불통 카운트: Image ID {image_id}, review_status='{review_status}', 수정여부='{revision_status}'")
            elif review_status == '납품 완료' or review_status == '납품완료':
                stats['delivered'] += 1
                print(f"[DEBUG] 납품완료 카운트: Image ID {image_id}, review_status='{review_status}'")
            elif 저장시간 and not review_status:
                # 작업: 저장시간이 있지만 검수 상태가 없는 것 (SKIP은 이미 제외됨)
                stats['working'] += 1
                print(f"[DEBUG] 작업 카운트: Image ID {image_id}, 저장시간='{저장시간}', review_status='{review_status}'")
        
        # 2단계: annotator.image_ids에 있지만 Google Sheets에 없는 image_id는 미작업으로 카운트하지 않음
        # (이미 전체 개수에서 계산됨)
        
        # 미작업 계산: 전체 - 통과 - 불통 - 검수 대기 - SKIP
        # 검수 대기는 불통 중 수정완료된 것들
        pending_review_count = 0
        for image_id in sheet_data_map.keys():
            sheet_info = sheet_data_map[image_id]
            review_status = sheet_info.get('review_status', '')
            revision_status = sheet_info.get('수정여부', '')
            
            if review_status == '불통' and (revision_status == '수정완료' or revision_status == '수정 완료'):
                pending_review_count += 1
                print(f"[DEBUG] 검수대기 카운트: Image ID {image_id}, review_status='{review_status}', 수정여부='{revision_status}'")
        
        # 미작업 = 전체 이미지 - 작업 - 납품완료 - 통과 - 불통 - 검수대기 - SKIP
        # 디버깅: 각 카운트 출력
        print(f"[DEBUG] 통계 계산: 전체={stats['total']}, 작업={stats['working']}, 납품완료={stats['delivered']}, 통과={stats['passed']}, 불통={stats['failed']}, 검수대기={pending_review_count}, SKIP={stats['skipped']}")
        stats['unfinished'] = stats['total'] - stats['working'] - stats['delivered'] - stats['passed'] - stats['failed'] - pending_review_count - stats['skipped']
        print(f"[DEBUG] 미작업 계산 결과: {stats['unfinished']} = {stats['total']} - {stats['working']} - {stats['delivered']} - {stats['passed']} - {stats['failed']} - {pending_review_count} - {stats['skipped']}")
        if stats['unfinished'] < 0:
            stats['unfinished'] = 0  # 음수 방지
        
        # 완료율 계산 (저장시간이 있는 것들)
        completed_count = stats['passed'] + stats['failed'] + stats['delivered'] + stats['completed']
        stats['completion_rate'] = (completed_count / stats['total'] * 100) if stats['total'] > 0 else 0
        
        return jsonify({
            'success': True,
            'statistics': stats
        })
        
    except Exception as e:
        print(f"[ERROR] 통계 조회 중 오류: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'통계 조회 실패: {str(e)}'}), 500


@app.route('/api/remove_duplicates', methods=['POST'])
def remove_duplicates():
    """중복 어노테이션 제거 API"""
    try:
        exo_count = remove_duplicate_annotations(annotator.output_json_path_exo)
        ego_count = remove_duplicate_annotations(annotator.output_json_path_ego)
        
        # 전체 annotations도 업데이트
        annotator._reload_annotations()
        
        return jsonify({
            'success': True,
            'exo_removed': exo_count,
            'ego_removed': ego_count,
            'total_removed': exo_count + ego_count
        })
    except Exception as e:
        return jsonify({'error': f'Failed to remove duplicates: {e}'}), 500


def create_template():
    """Create HTML template for the annotation interface."""
    template_dir = 'templates'
    if not os.path.exists(template_dir):
        os.makedirs(template_dir)

    # index.html 덮어쓰기 방지 추가
    target = os.path.join(template_dir, 'index.html')
    if os.path.exists(target):
        return
    
    html_content = '''<!DOCTYPE html>
<html>
<head>
    <title>COCO Annotation Tool</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        .container { display: flex; gap: 20px; }
        .image-panel { flex: 2; }
        .control-panel { flex: 1; min-width: 350px; }
        .image-container { 
            border: 2px solid #ccc; 
            position: relative; 
            display: inline-block;
        }
        #image { max-width: 100%; display: block; }
        .bbox { 
            position: absolute; 
            border: 2px solid rgba(255, 0, 0, 0.7); 
            background-color: rgba(255, 0, 0, 0.1);
            cursor: pointer;
            transition: all 0.2s ease;
            z-index: 10;
        }
        .bbox:hover { 
            border-color: rgba(255, 255, 0, 0.9);
            background-color: rgba(255, 255, 0, 0.2);
            transform: scale(1.05);
            box-shadow: 0 0 10px rgba(255, 255, 0, 0.5);
            z-index: 20;
        }
        .bbox.selected { 
            border-color: rgba(0, 0, 255, 0.9);
            background-color: rgba(0, 0, 255, 0.2);
            border-width: 3px;
        }
        .bbox.selected:hover { 
            border-color: rgba(0, 255, 255, 0.9);
            background-color: rgba(0, 255, 255, 0.3);
        }
        .bbox-label {
            position: absolute;
            top: -20px;
            left: 0;
            background: rgba(0, 0, 0, 0.7);
            color: white;
            padding: 2px 5px;
            font-size: 11px;
            white-space: nowrap;
            display: none;
            pointer-events: none;
        }
        .bbox:hover .bbox-label {
            display: block;
        }
        .form-group { margin-bottom: 15px; }
        label { display: block; font-weight: bold; margin-bottom: 5px; }
        textarea, input { width: 100%; padding: 8px; border: 1px solid #ccc; }
        button { 
            padding: 10px 15px; 
            margin: 5px; 
            border: none; 
            cursor: pointer;
        }
        .btn-save { background-color: lightgreen; }
        .btn-nav { background-color: lightblue; }
        .status { 
            position: fixed; 
            bottom: 0; 
            left: 0; 
            right: 0; 
            background: #f0f0f0; 
            padding: 10px; 
            border-top: 1px solid #ccc;
        }
    </style>
</head>
<body>
    <h1>MS-COCO Annotation Tool (Web Version)</h1>
    
    <div class="container">
        <div class="image-panel">
            <div class="image-container" id="imageContainer">
                <img id="image" src="" alt="COCO Image">
            </div>
        </div>
        
        <div class="control-panel">
            <div class="form-group">
                <label>Image Info:</label>
                <div id="imageInfo">Loading...</div>
            </div>
            
            <div class="form-group">
                <label for="question">Question: 
                    <span style="color: red;">*</span></label>
                <textarea id="question" rows="4"></textarea>
            </div>
            
            <div class="form-group">
                <label for="response">Response: 
                    <span style="color: red;">*</span></label>
                <textarea id="response" rows="4"></textarea>
            </div>
            
            <div class="form-group">
                <label for="rationale">Rationale:</label>
                <textarea id="rationale" rows="3"></textarea>
            </div>
            
            <div class="form-group">
                <label>View: <span style="color: red;">*</span></label>
                <div>
                    <input type="radio" id="viewExo" name="view" value="exo">
                    <label for="viewExo">Exo</label>
                </div>
                <div>
                    <input type="radio" id="viewEgo" name="view" value="ego">
                    <label for="viewEgo">Ego</label>
                </div>
            </div>
            
            <div class="form-group">
                <label for="selectedBboxes">Selected Bounding Boxes: 
                    <span style="color: red;">*</span></label>
                <textarea id="selectedBboxes" rows="3" readonly></textarea>
                <button onclick="clearBboxes()">Clear Bboxes</button>
            </div>
            
            <div class="form-group">
                <button class="btn-nav" onclick="previousImage()">Previous</button>
                <button class="btn-nav" onclick="nextImage()">Next</button>
                <button class="btn-save" onclick="saveAnnotation()">Save</button>
            </div>
        </div>
    </div>
    
    <div class="status" id="status">Ready</div>

    <script>
        let currentIndex = 0;
        let currentImageData = null;
        let selectedBboxes = [];
        let bboxElements = [];

        function loadImage(index) {
            fetch(`/api/image/${index}`)
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        alert(data.error);
                        return;
                    }
                    
                    currentImageData = data;
                    currentIndex = index;
                    
                    // Update image
                    const img = document.getElementById('image');
                    img.src = data.image_data;
                    
                    // Wait for image to load before drawing bboxes
                    img.onload = () => {
                        drawBboxes();
                    };
                    
                    // Update info
                    document.getElementById('imageInfo').innerHTML = 
                        `Image ${index + 1}/${data.total_images}<br>` +
                        `ID: ${data.image_id}<br>` +
                        `Original Size: ${data.width}x` +
                        `${data.height}<br>` +
                        `Display Size: ${data.display_width}x` +
                        `${data.display_height}<br>` +
                        `File: ${data.file_name}`;
                    
                    // Load existing annotation
                    if (data.existing_annotation) {
                        document.getElementById('question').value = 
                            data.existing_annotation.question || '';
                        document.getElementById('response').value = 
                            data.existing_annotation.response || '';
                        document.getElementById('rationale').value = 
                            data.existing_annotation.rationale || '';
                        selectedBboxes = data.existing_annotation.bbox || [];
                        
                        // Set view radio button
                        const view = data.existing_annotation.view || '';
                        if (view === 'exo') {
                            document.getElementById('viewExo').checked = true;
                        } else if (view === 'ego') {
                            document.getElementById('viewEgo').checked = true;
                        }
                    } else {
                        document.getElementById('question').value = '';
                        document.getElementById('response').value = '';
                        document.getElementById('rationale').value = '';
                        selectedBboxes = [];
                        // Clear view radio buttons
                        document.getElementById('viewExo').checked = false;
                        document.getElementById('viewEgo').checked = false;
                    }
                    
                    updateBboxDisplay();
                    updateStatus();
                })
                .catch(error => {
                    console.error('Error:', error);
                    alert('Failed to load image');
                });
        }

        function drawBboxes() {
            // Clear existing bboxes
            bboxElements.forEach(el => el.remove());
            bboxElements = [];
            
            if (!currentImageData) return;
            
            const container = document.getElementById('imageContainer');
            const img = document.getElementById('image');
            const scale = currentImageData.scale || 1.0;
            
            currentImageData.bboxes.forEach((bbox, index) => {
                const [x, y, w, h] = bbox;
                
                // Scale bbox coordinates to match displayed image size
                const scaledX = x * scale;
                const scaledY = y * scale;
                const scaledW = w * scale;
                const scaledH = h * scale;
                
                const div = document.createElement('div');
                div.className = 'bbox';
                div.style.left = `${scaledX}px`;
                div.style.top = `${scaledY}px`;
                div.style.width = `${scaledW}px`;
                div.style.height = `${scaledH}px`;
                
                // Add label
                const label = document.createElement('div');
                label.className = 'bbox-label';
                label.textContent = `Box ${index + 1}: [${x},${y},${w},${h}]`;
                div.appendChild(label);
                
                // Check if selected
                if (selectedBboxes.some(sb => 
                    JSON.stringify(sb) === JSON.stringify(bbox))) {
                    div.classList.add('selected');
                }
                
                // Add click event
                div.addEventListener('click', (e) => {
                    e.stopPropagation();
                    selectBbox(bbox, div);
                });
                
                container.appendChild(div);
                bboxElements.push(div);
            });
        }

        function selectBbox(bbox, element) {
            const bboxStr = JSON.stringify(bbox);
            const existingIndex = selectedBboxes.findIndex(sb => 
                JSON.stringify(sb) === bboxStr);
            
            if (existingIndex === -1) {
                selectedBboxes.push(bbox);
                element.classList.add('selected');
            } else {
                selectedBboxes.splice(existingIndex, 1);
                element.classList.remove('selected');
            }
            
            updateBboxDisplay();
            updateStatus();
        }

        function updateBboxDisplay() {
            const display = selectedBboxes.map(bbox => 
                `[${bbox.join(',')}]`).join(', ');
            document.getElementById('selectedBboxes').value = display;
        }

        function clearBboxes() {
            selectedBboxes = [];
            updateBboxDisplay();
            drawBboxes();
        }

        function previousImage() {
            if (currentIndex > 0) {
                loadImage(currentIndex - 1);
            }
        }

        function nextImage() {
            loadImage(currentIndex + 1);
        }

        function saveAnnotation() {
            const question = document.getElementById('question').value.trim();
            const response = document.getElementById('response').value.trim();
            const rationale = document.getElementById('rationale').value.trim();
            
            // Get selected view
            const viewRadios = document.getElementsByName('view');
            let selectedView = '';
            for (let radio of viewRadios) {
                if (radio.checked) {
                    selectedView = radio.value;
                    break;
                }
            }
            
            // Client-side validation
            const missingFields = [];
            if (!question) missingFields.push('question');
            if (!response) missingFields.push('response');
            if (!selectedView) missingFields.push('view');
            if (selectedBboxes.length === 0) missingFields.push('bbox');
            
            if (missingFields.length > 0) {
                alert(`Please fill in the following required fields: ` +
                      `${missingFields.join(', ')}`);
                return;
            }
            
            const data = {
                image_id: currentImageData.image_id,
                question: question,
                response: response,
                rationale: rationale,
                view: selectedView,
                selected_bboxes: selectedBboxes
            };
            
            fetch('/api/save', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            })
            .then(response => response.json())
            .then(result => {
                if (result.success) {
                    alert('Annotation saved successfully!');
                    nextImage();
                } else {
                    if (result.missing_fields) {
                        alert(`Server validation failed: ${result.message}`);
                    } else {
                        alert('Failed to save: ' + result.error);
                    }
                }
            })
            .catch(error => {
                console.error('Error:', error);
                alert('Failed to save annotation');
            });
        }

        function updateStatus() {
            document.getElementById('status').textContent = 
                `Current: ${currentIndex + 1} | Selected bboxes: ` +
                `${selectedBboxes.length}`;
        }

        // Auto-save function
        function autoSave() {
            const question = document.getElementById('question').value.trim();
            const response = document.getElementById('response').value.trim();
            const rationale = document.getElementById('rationale').value.trim();
            
            // Get selected view
            const viewRadios = document.getElementsByName('view');
            let selectedView = '';
            for (let radio of viewRadios) {
                if (radio.checked) {
                    selectedView = radio.value;
                    break;
                }
            }
            
            // Only auto-save if all required fields are filled
            if (currentImageData && question && response && 
                selectedView && selectedBboxes.length > 0) {
                const data = {
                    image_id: currentImageData.image_id,
                    question: question,
                    response: response,
                    rationale: rationale,
                    view: selectedView,
                    selected_bboxes: selectedBboxes
                };
                
                fetch('/api/save', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(data)
                })
                .then(response => response.json())
                .then(result => {
                    if (result.success) {
                        console.log('Auto-saved');
                    }
                })
                .catch(error => {
                    console.error('Auto-save error:', error);
                });
            }
        }

        // Save on unload
        window.addEventListener('beforeunload', (e) => {
            autoSave();
        });

        // Auto-save every 30 seconds
        setInterval(autoSave, 30000);

        // Handle keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if (e.ctrlKey || e.metaKey) {
                if (e.key === 's') {
                    e.preventDefault();
                    saveAnnotation();
                } else if (e.key === 'ArrowLeft') {
                    e.preventDefault();
                    previousImage();
                } else if (e.key === 'ArrowRight') {
                    e.preventDefault();
                    nextImage();
                }
            }
        });

        // Load first image on start
        loadImage(0);
    </script>
</body>
</html>'''
    
    with open(os.path.join(template_dir, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html_content)


def main():
    """Main function to start the web server."""
    
    parser = argparse.ArgumentParser(
        description='Web-based COCO Annotation Tool')
    parser.add_argument('--mscoco_folder', 
                        default='./mscoco',
                        help='Path to mscoco folder (contains exo_images and ego_images)')
    parser.add_argument('--coco_json', 
                        default='/Data/MSCOCO/annotations/instances_train2017.json',
                        help='Path to COCO annotations JSON file')
    parser.add_argument('--output_json', required=True,
                        help='Path to output JSON file for annotations')
    parser.add_argument('--host', default='0.0.0.0', 
                        help='Host to run server on')
    parser.add_argument('--port', default=5000, type=int, 
                        help='Port to run server on')
    parser.add_argument('--categories_json', default=None,
                        help='Path to custom categories JSON (list of {id,name})')
    parser.add_argument('--test_folder', default=None,
                        help='Test folder name (e.g., exo_test_image) to use instead of exo_images')

    
    args = parser.parse_args()
    
    # Validate paths
    if not os.path.exists(args.mscoco_folder):
        print(f"Error: mscoco folder not found: {args.mscoco_folder}")
        return
    
    # 테스트 폴더가 지정되면 사용, 아니면 기본 폴더 확인
    if args.test_folder:
        exo_images_path = os.path.join(args.mscoco_folder, args.test_folder)
        print(f"[INFO] Using test folder: {exo_images_path}")
        # test_folder 모드에서는 ego_images_path를 설정하지 않음 (사용하지 않음)
        ego_images_path = None
    else:
        exo_images_path = os.path.join(args.mscoco_folder, 'exo_images')
        ego_images_path = os.path.join(args.mscoco_folder, 'ego_images')
    
    if not os.path.exists(exo_images_path):
        print(f"Warning: exo images folder not found: {exo_images_path}")
    if ego_images_path and not os.path.exists(ego_images_path):
        print(f"Warning: ego_images folder not found: {ego_images_path}")
    
    if not os.path.exists(args.coco_json):
        print(f"Error: COCO JSON file not found: {args.coco_json}")
        return
    
    # Create output directory
    output_dir = os.path.dirname(args.output_json) if os.path.dirname(args.output_json) else '.'
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
    
    # Initialize global annotator
    global annotator
    annotator = COCOWebAnnotator(args.mscoco_folder, args.coco_json, 
                                 args.output_json, args.categories_json, 
                                 test_folder=args.test_folder)
    
    # Create template
    create_template()
    
    
    print(f"Starting web server at http://{args.host}:{args.port}")
    print("Access the annotation tool in your web browser")
    print(f"Exo annotations will be saved to: {annotator.output_json_path_exo}")
    print(f"Ego annotations will be saved to: {annotator.output_json_path_ego}")
    
    # 멀티스레드 모드로 실행 (타임아웃 방지)
    # Google Sheets 연동 상태 확인
    if GOOGLE_SHEETS_AVAILABLE:
        print(f"[INFO] Google Sheets 연동: 사용 가능")
        if google_sheets_client:
            print(f"[INFO] Google Sheets 클라이언트: 초기화 완료")
        else:
            print(f"[WARN] Google Sheets 클라이언트: 초기화 실패 (설정 확인 필요)")
    else:
        print(f"[WARN] Google Sheets 연동: 사용 불가")
    
    app.run(host=args.host, port=args.port, debug=True, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
