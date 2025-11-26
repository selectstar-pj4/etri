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
        
        self.output_json_path_exo = os.path.join(output_dir, f'{base_name}_exo.json')
        self.output_json_path_ego = os.path.join(output_dir, f'{base_name}_ego.json')
        
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
        return jsonify({'index': idx, 'total': len(annotator.image_ids)})
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
    
    # view 타입에 따라 올바른 폴더에서 이미지 로드
    if view_type == 'ego':
        image_path = os.path.join(annotator.ego_images_folder, image_info['file_name'])
    else:
        image_path = os.path.join(annotator.exo_images_folder, image_info['file_name'])
    
    # 이미지가 없으면 다른 폴더에서 시도
    if not os.path.exists(image_path):
        print(f"[WARN] Image not found at {image_path}, trying alternative paths...")
        # exo에서 찾기
        alt_path_exo = os.path.join(annotator.exo_images_folder, image_info['file_name'])
        alt_path_ego = os.path.join(annotator.ego_images_folder, image_info['file_name'])
        
        if os.path.exists(alt_path_exo):
            image_path = alt_path_exo
            view_type = 'exo'
            print(f"[INFO] Found image in exo_images: {image_path}")
        elif os.path.exists(alt_path_ego):
            image_path = alt_path_ego
            view_type = 'ego'
            print(f"[INFO] Found image in ego_images: {image_path}")
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
        'total_images': len(annotator.image_ids)
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
            prompt = f"""Translate the following Korean question to English. You MUST follow this EXACT format for EGO-CENTRIC questions:

CORRECT FORMAT FOR EGO-CENTRIC QUESTIONS:
[Question with <ATT>, <POS>, <REL> tags embedded naturally in the sentence] <choice>(a) option1, (b) option2, (c) option3, (d) option4</choice> And provide the bounding box coordinate of the region related to your answer.

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

CRITICAL TAG USAGE RULES:

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
   
🚨 CRITICAL - <ATT> TAG IS MANDATORY WHEN:
   - Korean question contains attribute words like: "흰색" (white), "빨간색" (red), "원형" (round), "정사각형" (square), "사람" (person), "객체" (object), "물체" (item), etc.
   - Korean question ends with "~사람은?" (which person?), "~객체는?" (which object?), "~물체는?" (which item?)
   - Korean question mentions specific attributes: "~색" (color), "~모양" (shape), "~재질" (material)
   - ALWAYS wrap attribute descriptions in <ATT> tags, even if the question seems simple
   - WRONG: "which white object" (missing <ATT> tag)
   - CORRECT: "which <ATT>white object</ATT>"
   - WRONG: "which person" (missing <ATT> tag)
   - CORRECT: "which <ATT>person</ATT>" or "which <ATT>person in white shirt</ATT>"

Reference examples from ego_data_sample.json:

Example 1: "From the perspective of the little girl standing in front of the man, which <ATT>party item</ATT> is <REL>farthest</REL> and located <POS>to the right</POS> of her? <choice>(a) cake, (b) camera, (c) party plate, (d) flower</choice> And provide the bounding box coordinate of the region related to your answer."

Example 2: "When I'm sitting on the right side of the large sofa, which <ATT>square or rectangular object</ATT> on the <POS>right side of the room</POS> is <REL>farthest from me</REL>? <choice>(a) fan, (b) large bottle, (c) shoe, (d) tv</choice> And provide the bounding box coordinate of the region related to your answer."

Example 3: "From the perspective of the woman, which <ATT>silver object</ATT> <POS>to the right of</POS> her is <REL>closest to her</REL>? <choice>(a) fork, (b) knife, (c) spoon, (d) wine glass</choice> And provide the bounding box coordinate of the region related to your answer."

Korean question: {question_ko}

Translate to English following the EXACT format above. Make sure:
- Use "From the perspective of ~" if Korean contains "~관점에서"
- Use "When I'm ~" if Korean contains "내가" or "I'm"
- <REL> is used ONLY for relationship terms (farthest, closest, etc.)
- <POS> is used ONLY for position/location information from the person's perspective (on the left side, on the right side, etc.)
- <ATT> is used ONLY for attributes or target groups (round object, green object, white object, person, etc.)
- 🚨 MANDATORY: If Korean question contains ANY attribute word (color, shape, material, "사람", "객체", "물체"), you MUST use <ATT> tag
- 🚨 MANDATORY: If Korean question ends with "~사람은?" or "~객체는?" or "~물체는?", you MUST include <ATT> tag
- 🚨 MANDATORY: NEVER translate "흰색 객체" as "white object" without <ATT> tags - it MUST be "<ATT>white object</ATT>"
- All tags have meaningful content inside them
- <choice> tag comes before "And provide..." phrase
- DO NOT use generic phrases like "in the image" for <POS> tag
- DOUBLE-CHECK: Before finalizing, verify that ALL attribute descriptions are wrapped in <ATT> tags"""
        else:
            # exo_data_sample.json 형식 참고
            prompt = f"""Translate the following Korean question to English. You MUST follow this EXACT format:

CORRECT FORMAT:
[Question with <ATT>, <POS>, <REL> tags embedded naturally in the sentence] <choice>(a) option1, (b) option2, (c) option3, (d) option4</choice> And provide the bounding box coordinate of the region related to your answer.

CRITICAL TAG USAGE RULES:

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
   
🚨 CRITICAL - <ATT> TAG IS MANDATORY WHEN:
   - Korean question contains attribute words like: "흰색" (white), "빨간색" (red), "원형" (round), "정사각형" (square), "사람" (person), "객체" (object), "물체" (item), etc.
   - Korean question ends with "~사람은?" (which person?), "~객체는?" (which object?), "~물체는?" (which item?)
   - Korean question mentions specific attributes: "~색" (color), "~모양" (shape), "~재질" (material)
   - ALWAYS wrap attribute descriptions in <ATT> tags, even if the question seems simple
   - WRONG: "which white object" (missing <ATT> tag)
   - CORRECT: "which <ATT>white object</ATT>"
   - WRONG: "which person" (missing <ATT> tag)
   - CORRECT: "which <ATT>person</ATT>" or "which <ATT>person in white shirt</ATT>"

Reference examples from exo_data_sample.json:
- "<REL>Second-closest</REL> to the refrigerator a countertop located <POS>in the center</POS> of the image, which object is it <ATT>among the items</ATT>? <choice>(a) sink, (b) vase, (c) orange bag, (d) rightmost red chair</choice> And provide the bounding box coordinate of the region related to your answer."
- "Which <ATT>square-shaped item</ATT> is <REL>placed on the floor</REL> <POS>in front of</POS> the brown-haired man sitting on the sofa? <choice>(a) handbag, (b) coke, (c) laptop, (d) cell phone</choice> And provide the bounding box coordinate of the region related to your answer."

Korean question: {question_ko}

Translate to English following the EXACT format above. Make sure:
- <REL> is used ONLY for relationship terms (farthest, closest, etc.)
- <POS> is used ONLY for position/location information (in the center, on the left side, etc.)
- <ATT> is used ONLY for attributes or target groups (red object, white object, among the items, person, etc.)
- 🚨 MANDATORY: If Korean question contains ANY attribute word (color, shape, material, "사람", "객체", "물체"), you MUST use <ATT> tag
- 🚨 MANDATORY: If Korean question ends with "~사람은?" or "~객체는?" or "~물체는?", you MUST include <ATT> tag
- 🚨 MANDATORY: NEVER translate "흰색 객체" as "white object" without <ATT> tags - it MUST be "<ATT>white object</ATT>"
- All tags have meaningful content inside them
- <choice> tag comes before "And provide..." phrase
- DO NOT use generic phrases like "in the image" for <POS> tag
- DOUBLE-CHECK: Before finalizing, verify that ALL attribute descriptions are wrapped in <ATT> tags"""
        
        # view_type에 따라 다른 시스템 메시지 사용
        if view_type == 'ego':
            system_message = "You are a professional translator specializing in VQA (Visual Question Answering) EGO-CENTRIC questions. CRITICAL RULES: 1) Use 'From the perspective of ~' for '~관점에서', 2) Use 'When I'm ~' for '내가', 3) <REL> tag ONLY for relationship terms (farthest, closest, etc.), 4) <POS> tag ONLY for position/location from person's perspective (on the left side, on the right side, etc.), 5) <ATT> tag ONLY for attributes/target groups (round object, green object, white object, person, etc.), 6) 🚨 MANDATORY: If Korean contains ANY attribute word (color, shape, material, '사람', '객체', '물체'), you MUST use <ATT> tag, 7) 🚨 MANDATORY: If Korean ends with '~사람은?' or '~객체는?', you MUST include <ATT> tag, 8) Tags MUST contain actual meaningful content, 9) Format: [Question with tags] <choice>...</choice> And provide..., 10) DO NOT use generic phrases like 'in the image' for <POS> tag, 11) DOUBLE-CHECK: Verify ALL attribute descriptions are wrapped in <ATT> tags."
        else:
            system_message = "You are a professional translator specializing in VQA (Visual Question Answering) questions. CRITICAL RULES: 1) <REL> tag ONLY for relationship terms (farthest, closest, etc.), 2) <POS> tag ONLY for position/location (in the center, on the left side, etc.), 3) <ATT> tag ONLY for attributes/target groups (red object, white object, among the items, person, etc.), 4) 🚨 MANDATORY: If Korean contains ANY attribute word (color, shape, material, '사람', '객체', '물체'), you MUST use <ATT> tag, 5) 🚨 MANDATORY: If Korean ends with '~사람은?' or '~객체는?', you MUST include <ATT> tag, 6) Tags MUST contain actual meaningful content, 7) Format: [Question with tags] <choice>...</choice> And provide..., 8) DO NOT use generic phrases like 'in the image' for <POS> tag, 9) DOUBLE-CHECK: Verify ALL attribute descriptions are wrapped in <ATT> tags."
        
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
        
        # ATT 태그 누락 검증 강화: 한국어 질문에 속성 단어가 있는데 ATT 태그가 없는 경우
        attribute_keywords_ko = ['흰색', '빨간색', '파란색', '초록색', '검은색', '노란색', '원형', '정사각형', '직사각형', '사람', '객체', '물체', '색', '모양', '재질']
        question_has_attribute = any(keyword in question_ko for keyword in attribute_keywords_ko)
        if question_has_attribute and '<ATT>' not in translated_question:
            return jsonify({
                'success': False, 
                'error': f'ATT tag is missing! Korean question contains attribute words but translation lacks <ATT> tag. Please ensure all attribute descriptions are wrapped in <ATT> tags. Translation: {translated_question[:200]}...'
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
        
        # 3-hop 질문 생성: ATT, POS, REL이 모두 포함된 복잡한 질문
        question_generation_prompt = f"""이미지와 이미지 분석 결과를 바탕으로 VQA (Visual Question Answering) 3-hop 질문을 한글로 생성해주세요.

🚨 **절대 필수 규칙 - 반드시 준수해야 함**:

**STEP 1: 이미지 내용 직접 확인 및 ATT 속성 검증 (절대 필수)**

먼저 이미지를 직접 확인하고, 질문에 사용할 ATT 속성이 실제 이미지의 객체와 정확히 일치하는지 검증하세요.

🚨 **CRITICAL - ATT 속성 정확성 검증 (절대 필수)**:
1. 질문에서 사용할 ATT 속성(예: "빨간색 객체", "원형 또는 원통형 객체", "식용 가능한 물체")을 먼저 결정하세요.
2. 이미지를 직접 확인하여 해당 ATT 속성을 만족하는 객체들이 실제로 존재하는지 확인하세요.
3. 예를 들어, "흰색 객체"라고 질문하려면 이미지에 실제로 흰색 객체가 있어야 합니다.
4. 예를 들어, "정사각형 또는 직사각형 객체"라고 질문하려면 이미지에 실제로 정사각형 또는 직사각형 객체가 있어야 합니다.
5. 이미지에 존재하지 않는 속성을 ATT로 사용하는 것은 절대 금지입니다.

**검증 체크리스트**:
- [ ] 질문에서 사용할 ATT 속성이 실제 이미지의 객체와 정확히 일치하는가?
- [ ] ATT 속성을 만족하는 객체가 이미지에 실제로 존재하는가?
- [ ] 이미지에 존재하지 않는 속성을 ATT로 사용하지 않았는가?

**STEP 2: 복잡하고 고급 추론이 필요한 3-hop 질문 구조 생성**

🚨 **CRITICAL - 질문 복잡도 및 고급 추론 요구사항 (절대 필수)**:

각 질문은 반드시 ATT(속성), POS(위치), REL(관계) 세 가지 요소를 모두 포함해야 하며, **단순한 질문은 절대 금지**입니다.

**❌ 절대 금지 - 너무 단순한 질문 패턴**:
- "X 오른쪽에 있는 가장 가까운 Y 객체" (단순 위치+속성 조합)
- "X 위에 있는 가장 가까운 Y 객체" (단순 위치+속성 조합)
- "X 왼쪽에 있는 가장 먼 Y 객체" (단순 위치+속성 조합)

**✅ 반드시 사용 - 복잡하고 고급 추론이 필요한 질문 패턴**:

1. **중첩된 조건 조합**:
   - "X <POS>위에 있는</POS> <ATT>Y 객체</ATT> 중에서 Z로부터 <REL>가장 먼</REL> 객체"
   - "X <POS>왼쪽에 있는</POS> <ATT>Y 객체</ATT> 중에서 Z <POS>앞에 있는</POS> <REL>가장 가까운</REL> 객체"
   - "<ATT>Y 객체</ATT> 중에서 X <POS>위에 있는</POS> Z로부터 <REL>가장 먼</REL> 객체"

2. **복잡한 기준점과 대상의 조합**:
   - "X <POS>위에 있는</POS> <ATT>Y 객체</ATT>로부터 <REL>가장 먼</REL> <ATT>Z 객체</ATT>"
   - "X <POS>앞에 있는</POS> <ATT>Y 객체</ATT> 중에서 Z <POS>옆에 있는</POS> <REL>가장 가까운</REL> 객체"

3. **여러 조건이 동시에 적용되는 질문**:
   - "<ATT>Y 객체</ATT> 중에서 X <POS>위에</POS> <REL>놓여 있는</REL> Z <POS>앞에 있는</POS> 객체"
   - "<ATT>Y 객체</ATT> 중에서 X <POS>옆에 있는</POS> <REL>가장 높은</REL> 객체"

4. **복잡한 공간 관계**:
   - "X <POS>앞에 있는</POS> <ATT>Y 객체</ATT> 중에서 Z <POS>반대편에 있는</POS> <REL>가장 먼</REL> 객체"
   - "X <POS>중앙에 있는</POS> <ATT>Y 객체</ATT> 중에서 Z <POS>옆에 있는</POS> <REL>가장 가까운</REL> 객체"

**ATT (속성/대상) 규칙 - CRITICAL: 속성 기반 표현만 사용, 구체적 명사 금지**:
- ❌ **절대 사용 금지 - 구체적 명사**: "컵", "접시", "의자", "테이블" 등
- ✅ **반드시 사용 - 속성 기반 표현**:
  * "원형 또는 원통형 객체" (컵, 병 등)
  * "밝은 색상의 객체" (밝은 색의 물체들)
  * "파티용품 객체" (파티 관련 물체들)
  * "식용 가능한 물체" (먹을 수 있는 것들)
  * "정사각형 또는 직사각형 객체" (사각형 모양)
  * "빨간색 객체", "흰색 색상의 객체" (색상 기반)
  * "나무 재질의 객체" (재질 기반)

**POS (위치) 규칙**:
- ❌ 절대 사용 금지: "이미지 중앙에", "이미지 왼쪽에" (모호함)
- ✅ 반드시 사용: "테이블 중앙에", "소파 왼쪽에", "싱크대 오른쪽에" (구체적 객체 기준)
- **위치 반전 규칙**: 실제로 "왼쪽"에 있으면 질문에서는 "오른쪽"으로 표현

**REL (관계) 규칙**:
- "가장 가까운", "가장 먼", "두 번째로 가까운" 등

**🚨 CRITICAL - 질문 끝 표현 규칙 (절대 필수)**:
질문은 반드시 "~객체"로 끝나야 합니다. "는?", "는 무엇인가요?" 같은 의문사는 절대 사용하지 마세요.

- ❌ **절대 사용 금지**:
  * "~사람은 누구인가요?" (사람을 묻는 형식 금지)
  * "것은 무엇인가요?" (모호한 표현 금지)
  * "가장 가까운 것은?" (ATT 속성 미명시)
  * "가장 먼 것은?" (ATT 속성 미명시)
  * "~객체는?" ("는?" 사용 금지)
  * "~객체는 무엇인가요?" ("는 무엇인가요?" 사용 금지)
  * "무엇인가요?" (ATT 속성이 명시되지 않은 형식 금지)

- ✅ **반드시 사용 - "~객체"로 끝나는 형식**:
  * "정사각형 또는 직사각형의 객체"
  * "원통형 또는 원형의 객체"
  * "밝은 색상의 객체"
  * "무채색 객체"
  * "금속 재질의 객체"
  * "식용 가능한 객체"
  * "빨간색 객체"
  * "나무 재질의 객체"

**질문 형식 예시**:
- ✅ 올바른 예시: "테이블 위에 있는 가장 가까운 원형 또는 원통형의 객체"
- ✅ 올바른 예시: "소파 왼쪽에 위치한 밝은 색상의 객체"
- ✅ 올바른 예시: "싱크대 오른쪽에 있는 무채색 객체"
- ✅ 올바른 예시: "식용 가능한 객체 중에서 포크로부터 가장 먼 객체"
- ❌ 잘못된 예시: "소파 왼쪽에 있는 사람은 누구인가요?" (사람을 묻는 형식, "는?" 사용)
- ❌ 잘못된 예시: "테이블 위에 있는 것은 무엇인가요?" (ATT 속성 미명시, "는 무엇인가요?" 사용)
- ❌ 잘못된 예시: "가장 가까운 것은?" (ATT 속성 미명시, "는?" 사용)
- ❌ 잘못된 예시: "가장 가까운 객체는?" ("는?" 사용 금지)
- ❌ 잘못된 예시: "가장 가까운 객체는 무엇인가요?" ("는 무엇인가요?" 사용 금지)

**중요**: 질문은 반드시 ATT 속성을 포함한 "~객체"로 끝나야 하며, "는?", "는 무엇인가요?" 같은 의문사는 절대 사용하지 마세요. 질문은 "~객체"로 끝나는 명사구 형태여야 합니다.

**STEP 3: 소거법을 위한 선택지 설계 및 검증 (고급 추론 능력 요구)**

🚨 **CRITICAL - 고급 추론 능력 요구를 위한 선택지 구성 (절대 필수)**:
- 질문의 ATT 조건을 만족하는 객체가 선택지에 **최소 2개 이상** 있어야 합니다.
- 이렇게 해야 다른 AI가 문제를 풀 때 단순히 ATT 조건을 만족하는지 확인하는 것만으로는 정답을 찾을 수 없고, 추가적인 추론(위치, 거리 등)이 필요합니다.

**예시 1 - 올바른 구성 (고급 추론 요구)**:
질문: "식용 가능한 물체 중에서..."
선택지:
- a: 케이크 조각 (ATT 조건 만족, 하지만 다른 조건 불만족)
- b: 케이크 조각 (ATT 조건 만족, 하지만 다른 조건 불만족) ← 다른 케이크 조각
- c: 피자 (ATT 조건 만족, 하지만 다른 조건 불만족)
- d: 햄버거 (정답: ATT 조건 만족 + 다른 모든 조건 만족)

이 경우 ATT 조건을 만족하는 객체가 4개(a, b, c, d 모두)이므로 고급 추론이 필요합니다.

**예시 2 - 잘못된 구성 (너무 쉬움)**:
질문: "식용 가능한 물체 중에서..."
선택지:
- a: 컵 (ATT 조건 불만족 - 식용 불가)
- b: 접시 (ATT 조건 불만족 - 식용 불가)
- c: 포크 (ATT 조건 불만족 - 식용 불가)
- d: 케이크 조각 (정답: ATT 조건 만족)

이 경우 ATT 조건을 만족하는 객체가 1개(d만)이므로 너무 쉽습니다. ❌

**검증 체크리스트**:
- [ ] 질문의 ATT 조건을 만족하는 객체가 선택지에 최소 2개 이상 있는가? (고급 추론 능력 요구)
- [ ] 각 선택지는 서로 다른 이유로 제외될 수 있는가?
- [ ] 선택지에 동일한 물체가 중복되지 않았는가?
- [ ] 선택지의 모든 객체가 이미지에 실제로 존재하는가?

**STEP 4: 동일 물체 중복 금지**

🚨 **CRITICAL - 동일 물체 중복 금지 (절대 필수)**:
- 각 선택지는 반드시 **서로 다른 객체 인스턴스**를 가리켜야 합니다.
- 같은 카테고리의 객체라도, 이미지 내에서 다른 인스턴스(다른 bbox)를 가리켜야 합니다.
- 예: 이미지에 "컵"이 3개 있어도, 선택지에 "컵"이 2번 나오면 안 됩니다. 각각 "왼쪽 컵", "오른쪽 컵", "중앙 컵" 등으로 구분해야 합니다.

**이미지 분석 결과**:
{image_analysis}

**COCO 객체 정보 (bbox로 식별 가능한 객체들)**:
- 주요 객체: {', '.join(main_objects) if main_objects else '없음'}
- 총 객체 수: {len(category_info)}
- 각 객체는 이미지 내 bbox로 정확히 식별 가능함

**중요**: 이미지 분석 결과에서 언급된 객체들 중에서, COCO 어노테이션에 존재하는 객체만 선택지로 사용하세요. 같은 종류의 객체가 여러 개 있으면 색상, 위치, 속성 등으로 명확히 구분하세요.

**🚨 CRITICAL - 참고 예시 (exo_data_sample.json, web_annotations_exo.json 스타일)**:

다음 예시들을 반드시 참고하여 **복잡하고 고급 추론이 필요한** 질문과 선택지를 생성하세요:

**예시 1** (exo_data_sample.json - 복잡한 조건 조합):
- 질문: "Which <ATT>edible food item</ATT> is the <REL>farthest</REL> from the fork <POS>on the left side of</POS> the table?"
- 선택지: (a) glass, (b) potato fries, (c) hamburger, (d) cell phone
- 근거: cell phone은 식용 불가 (ATT 조건 불만족), glass도 식용 불가 (ATT 조건 불만족), potato fries는 hamburger보다 가까움 (REL 조건 불만족), 따라서 hamburger가 정답
- ✅ **복잡도**: ATT 조건 + POS 조건 + REL 조건이 모두 적용됨
- ✅ **고급 추론**: ATT 조건을 만족하는 객체가 2개(b, c) 있어서 단순히 ATT만 확인해서는 안 됨

**예시 2** (exo_data_sample.json - 중첩된 공간 관계):
- 질문: "Which <ATT>round and cylindrical object</ATT> is <REL>farthest</REL> from the person sitting <POS>on the right side of</POS> the dining table?"
- 선택지: (a) plate, (b) white cake, (c) rightmost coke, (d) vase
- 근거: plate, white cake, rightmost coke는 모두 가까운 편이지만, vase는 테이블 반대편에 위치하여 가장 멀리 떨어져 있음
- ✅ **복잡도**: ATT 조건 + POS 조건(사람의 위치) + REL 조건이 모두 적용됨
- ✅ **고급 추론**: ATT 조건을 만족하는 객체가 4개(a, b, c, d 모두) 있어서 거리 계산이 필요함

**예시 3** (exo_data_sample.json - 여러 조건 동시 적용):
- 질문: "Which <ATT>square-shaped item</ATT> is <REL>placed on the floor</REL> <POS>in front of</POS> the brown-haired man sitting on the sofa?"
- 선택지: (a) handbag, (b) coke, (c) laptop, (d) cell phone
- 근거: laptop과 cell phone은 소파 위에 있음 (POS 조건 불만족), coke는 원통형이므로 제외 (ATT 조건 불만족), handbag만 바닥에 있고 사각형 모양 (모든 조건 만족)
- ✅ **복잡도**: ATT 조건 + REL 조건(위치 상태) + POS 조건이 모두 적용됨
- ✅ **고급 추론**: 각 선택지가 서로 다른 이유로 제외됨 (위치, 형태 등)

**예시 4** (web_annotations_exo.json - 복잡한 기준점):
- 질문: "Which object is <REL>farthest</REL> from the <ATT>white object</ATT> <POS>on the left side of</POS> the child wearing a striped shirt in the center?"
- 선택지: (a) keyboard, (b) piano, (c) sofa, (d) plant
- 근거: sofa는 아이 오른쪽에 있음 (POS 조건 불만족), keyboard와 piano는 더 가까움 (REL 조건 불만족), plant가 가장 멀리 있음
- ✅ **복잡도**: 기준점이 "흰색 객체"이고, 그 객체의 위치가 "아이 왼쪽"이라는 중첩된 조건
- ✅ **고급 추론**: 기준점을 먼저 찾고, 그 기준점으로부터 거리를 계산해야 함

**예시 5** (web_annotations_exo.json - 복잡한 속성 조합):
- 질문: "Which <ATT>object that can hold water</ATT> is the <REL>closest</REL> to the pizza placed in front of the woman <POS>on the left</POS>?"
- 선택지: (a) fork, (b) empty glass, (c) blue vase, (d) water glass
- 근거: fork는 물을 담을 수 없음 (ATT 조건 불만족), blue vase와 water glass는 오른쪽 여자에게 더 가까움 (POS 조건 불만족), empty glass가 왼쪽 여자 앞 피자에 가장 가까움
- ✅ **복잡도**: ATT 조건(기능적 속성) + POS 조건(여자의 위치) + REL 조건이 모두 적용됨
- ✅ **고급 추론**: ATT 조건을 만족하는 객체가 3개(b, c, d) 있어서 위치와 거리를 모두 고려해야 함

**예시 6** (web_annotations_exo.json - 복잡한 공간 관계):
- 질문: "Which object <REL>farthest</REL> from the window <POS>on the table</POS> among the <ATT>square or rectangular objects</ATT>?"
- 선택지: (a) backpack, (b) laptop, (c) beige book, (d) blue bowl
- 근거: backpack은 테이블 위에 없음 (POS 조건 불만족), blue bowl은 사각형이 아님 (ATT 조건 불만족), laptop은 beige book보다 창문에 가까움 (REL 조건 불만족), beige book이 가장 멀리 있음
- ✅ **복잡도**: POS 조건 + ATT 조건 + REL 조건이 모두 적용됨
- ✅ **고급 추론**: 각 선택지가 서로 다른 이유로 제외되고, ATT 조건을 만족하는 객체 중에서 거리를 계산해야 함

**🚨 CRITICAL - 선택지 구성 원칙 (절대 필수)**:

1. **다양한 제외 이유**: 각 선택지는 서로 다른 이유로 제외되어야 합니다:
   - ATT 조건 불만족 (속성, 형태, 색상 등)
   - POS 조건 불만족 (위치, 공간 관계 등)
   - REL 조건 불만족 (거리, 순서 등)
   - 여러 조건 동시 불만족

2. **ATT 조건 만족 객체 최소 2개 이상**: 질문의 ATT 조건을 만족하는 객체가 선택지에 최소 2개 이상 있어야 합니다. 이렇게 해야 단순히 ATT 조건만 확인해서는 정답을 찾을 수 없고, 추가적인 추론(POS, REL)이 필요합니다.

3. **선택지 다양성**: 선택지는 다양한 카테고리와 속성을 포함해야 합니다:
   - ❌ 나쁜 예: "밝은 색상의 의자", "밝은 색상의 벤치", "밝은 색상의 식탁", "밝은 색상의 쓰레기통" (모두 같은 속성)
   - ✅ 좋은 예: "glass", "potato fries", "hamburger", "cell phone" (다양한 속성과 카테고리)

**중요**: 위 예시들을 참고하여:
1. **복잡한 질문 구조**: 단순한 "X 오른쪽에 있는 가장 가까운 Y 객체" 형식은 절대 사용하지 마세요
2. **중첩된 조건**: 여러 조건이 동시에 적용되는 질문을 생성하세요
3. **다양한 제외 이유**: 각 선택지가 서로 다른 이유로 제외되도록 구성하세요
4. **ATT 조건 만족 객체 최소 2개**: 고급 추론이 필요하도록 선택지를 구성하세요

**출력 형식 (반드시 JSON 형식으로, 정확히 3개만 생성)**:

🚨 **CRITICAL**: 모든 질문은 반드시 "~객체"로 끝나야 합니다. "는?", "는 무엇인가요?" 같은 의문사는 절대 사용하지 마세요.

{{
  "questions": [
    {{
      "question": "첫 번째 3-hop 한글 질문 (ATT는 속성 기반 표현, POS는 구체적 객체 기준, REL 포함, 소거법 가능한 선택지 구성, ATT 조건 만족 객체 최소 2개 이상, 반드시 '~객체'로 끝남, '는?' 또는 '는 무엇인가요?' 사용 금지)",
      "choices": {{
        "a": "선택지 a (한글, 소거 가능한 이유가 명확해야 함, 동일 물체 중복 금지)",
        "b": "선택지 b (한글, 소거 가능한 이유가 명확해야 함, 동일 물체 중복 금지)",
        "c": "선택지 c (한글, 소거 가능한 이유가 명확해야 함, 동일 물체 중복 금지)",
        "d": "선택지 d (한글, 정답, ATT 조건 만족 객체 중 하나)"
      }},
      "correct_answer": "a"
    }},
    {{
      "question": "두 번째 3-hop 한글 질문 (첫 번째와 다른 구조/조합, ATT는 속성 기반 표현, ATT 조건 만족 객체 최소 2개 이상, 반드시 '~객체'로 끝남, '는?' 또는 '는 무엇인가요?' 사용 금지)",
      "choices": {{
        "a": "선택지 a (한글, 소거 가능한 이유가 명확해야 함, 동일 물체 중복 금지)",
        "b": "선택지 b (한글, 소거 가능한 이유가 명확해야 함, 동일 물체 중복 금지)",
        "c": "선택지 c (한글, 소거 가능한 이유가 명확해야 함, 동일 물체 중복 금지)",
        "d": "선택지 d (한글, 정답, ATT 조건 만족 객체 중 하나)"
      }},
      "correct_answer": "b"
    }},
    {{
      "question": "세 번째 3-hop 한글 질문 (앞의 두 질문과 다른 구조/조합, ATT는 속성 기반 표현, ATT 조건 만족 객체 최소 2개 이상, 반드시 '~객체'로 끝남, '는?' 또는 '는 무엇인가요?' 사용 금지)",
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

**질문 형식 예시 (반드시 참고)**:

**❌ 절대 금지 - 너무 단순한 질문**:
- "테이블 위에 있는 가장 가까운 원형 또는 원통형의 객체" (단순 위치+속성)
- "소파 왼쪽에 위치한 밝은 색상의 객체" (단순 위치+속성)
- "싱크대 오른쪽에 있는 무채색 객체" (단순 위치+속성)
- "소파 왼쪽에 있는 사람은 누구인가요?" (금지 - "는 누구인가요?" 사용)
- "테이블 위에 있는 것은 무엇인가요?" (금지 - ATT 속성 미명시, "는 무엇인가요?" 사용)
- "가장 가까운 것은?" (금지 - ATT 속성 미명시, "는?" 사용)

**✅ 반드시 사용 - 복잡하고 고급 추론이 필요한 질문**:
- "식용 가능한 객체 중에서 포크로부터 가장 먼 객체" (ATT + REL + 기준점)
- "테이블 위에 있는 원형 또는 원통형 객체 중에서 사람으로부터 가장 먼 객체" (POS + ATT + REL)
- "소파 왼쪽에 있는 밝은 색상의 객체 중에서 창문으로부터 가장 가까운 객체" (POS + ATT + REL)
- "식용 가능한 객체 중에서 포크 왼쪽에 있는 가장 먼 객체" (ATT + POS + REL)
- "테이블 중앙에 있는 원형 또는 원통형 객체 중에서 사람 앞에 있는 가장 가까운 객체" (POS + ATT + POS + REL)
- "식용 가능한 객체 중에서 테이블 왼쪽에 있는 포크로부터 가장 먼 객체" (ATT + POS + REL)

🚨 **최종 검증 체크리스트 (생성 전 반드시 확인)**:

**질문 복잡도 검증**:
- [ ] 질문이 단순한 "X 오른쪽에 있는 가장 가까운 Y 객체" 형식이 아닌가? (이런 형식은 절대 금지)
- [ ] 질문에 중첩된 조건이나 복잡한 공간 관계가 포함되어 있는가?
- [ ] 각 질문에 ATT, POS, REL이 모두 포함되어 있고, 서로 복잡하게 얽혀있는가?

**질문 형식 검증**:
- [ ] **CRITICAL**: 질문이 "~객체"로 끝나는가? ("는?", "는 무엇인가요?", "~사람은 누구인가요?", "것은 무엇인가요?" 형식 금지)
- [ ] ATT 태그에 구체적 명사("컵", "접시" 등)가 아닌 속성 기반 표현("원형 또는 원통형 객체" 등)을 사용했는가?
- [ ] ATT 속성이 실제 이미지의 객체와 정확히 일치하는가?
- [ ] POS 표현이 구체적 객체 기준인가? ("이미지 중앙" 대신 "테이블 중앙" 등)
- [ ] 위치 반전 규칙을 적용했는가? (실제 왼쪽 → 질문에서는 오른쪽)

**선택지 구성 검증**:
- [ ] 질문의 ATT 조건을 만족하는 객체가 선택지에 최소 2개 이상 있는가? (고급 추론 능력 요구)
- [ ] 각 선택지는 서로 다른 이유로 제외될 수 있는가? (ATT 불만족, POS 불만족, REL 불만족 등)
- [ ] 선택지에 동일한 물체가 중복되지 않았는가?
- [ ] 선택지의 모든 객체가 이미지에 실제로 존재하는가?
- [ ] 선택지가 다양한 카테고리와 속성을 포함하고 있는가? (모두 같은 속성의 객체가 아닌가?)

**중요**: 정확히 3개의 질문만 생성하고, 각 질문은 반드시 위의 모든 규칙을 준수해야 합니다. 반드시 유효한 JSON 형식으로 응답하세요."""

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
                            "content": "You are an expert VQA question generator specializing in complex, multi-hop reasoning questions. CRITICAL RULES: 1) Each question MUST include ATT (attribute), POS (position), REL (relationship) in a COMPLEX, INTERWOVEN manner - NOT simple patterns like 'X right side, closest Y object'. 2) Questions MUST require advanced reasoning with nested conditions, multiple spatial relationships, or complex attribute combinations. 3) Use ONLY objects that actually exist in the image. 4) Choices must be clearly distinguishable and diverse (use color, position: 'red cup', 'leftmost chair'). 5) For POS, use specific object references ('center of table', NOT 'center of image'). 6) Reverse left/right positions in questions. 7) Use ONLY objective attributes (color, shape, material) - NEVER subjective ('small', 'pretty'). 8) Ask about concrete objects, NOT abstract properties. 9) At least 2 choices MUST satisfy the ATT condition to require advanced reasoning. 10) Each choice should be excluded for DIFFERENT reasons (ATT failure, POS failure, REL failure, etc.). 11) Generate exactly 3 questions with DIFFERENT complex structures. Return valid JSON."
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

CORRECT FORMAT FOR EGO-CENTRIC QUESTIONS:
[Question with <ATT>, <POS>, <REL> tags embedded naturally in the sentence] <choice>(a) option1, (b) option2, (c) option3, (d) option4</choice> And provide the bounding box coordinate of the region related to your answer.

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

CRITICAL TAG USAGE RULES:

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
   
🚨 CRITICAL - <ATT> TAG IS MANDATORY WHEN:
   - Korean question contains attribute words like: "흰색" (white), "빨간색" (red), "원형" (round), "정사각형" (square), "사람" (person), "객체" (object), "물체" (item), etc.
   - Korean question ends with "~사람은?" (which person?), "~객체는?" (which object?), "~물체는?" (which item?)
   - Korean question mentions specific attributes: "~색" (color), "~모양" (shape), "~재질" (material)
   - ALWAYS wrap attribute descriptions in <ATT> tags, even if the question seems simple
   - WRONG: "which white object" (missing <ATT> tag)
   - CORRECT: "which <ATT>white object</ATT>"
   - WRONG: "which person" (missing <ATT> tag)
   - CORRECT: "which <ATT>person</ATT>" or "which <ATT>person in white shirt</ATT>"

4. GENERAL RULES:
   - Tags MUST contain actual meaningful content (NOT empty like <ATT></ATT>)
   - Tags should be embedded naturally within the question sentence, not at the end
   - The <choice> tag MUST come BEFORE "And provide..." phrase
   - DO NOT use generic phrases like "in the image" for <POS> tag
   - If a phrase contains both attribute and location, split them appropriately

Reference examples from ego_data_sample.json:

Example 1: "From the perspective of the little girl standing in front of the man, which <ATT>party item</ATT> is <REL>farthest</REL> and located <POS>to the right</POS> of her? <choice>(a) cake, (b) camera, (c) party plate, (d) flower</choice> And provide the bounding box coordinate of the region related to your answer."

Example 2: "When I'm sitting on the right side of the large sofa, which <ATT>square or rectangular object</ATT> on the <POS>right side of the room</POS> is <REL>farthest from me</REL>? <choice>(a) fan, (b) large bottle, (c) shoe, (d) tv</choice> And provide the bounding box coordinate of the region related to your answer."

Example 3: "From the perspective of the woman, which <ATT>silver object</ATT> <POS>to the right of</POS> her is <REL>closest to her</REL>? <choice>(a) fork, (b) knife, (c) spoon, (d) wine glass</choice> And provide the bounding box coordinate of the region related to your answer."

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
- 🚨 MANDATORY: If Korean question contains ANY attribute word (color, shape, material, "사람", "객체", "물체"), you MUST use <ATT> tag
- 🚨 MANDATORY: If Korean question ends with "~사람은?" or "~객체는?" or "~물체는?", you MUST include <ATT> tag
- 🚨 MANDATORY: NEVER translate "흰색 객체" as "white object" without <ATT> tags - it MUST be "<ATT>white object</ATT>"
- All tags have meaningful content inside them
- Tags are naturally embedded in the question sentence
- <choice> tag comes before "And provide..." phrase
- DO NOT use generic phrases like "in the image" for <POS> tag
- Choices are in concise adjective+noun or noun+noun format
- DOUBLE-CHECK: Before finalizing, verify that ALL attribute descriptions are wrapped in <ATT> tags"""
        else:
            prompt = f"""Translate the following Korean question and multiple choice options to English. You MUST follow this EXACT format:{image_context}

CORRECT FORMAT:
[Question with <ATT>, <POS>, <REL> tags embedded naturally in the sentence] <choice>(a) option1, (b) option2, (c) option3, (d) option4</choice> And provide the bounding box coordinate of the region related to your answer.

CRITICAL TAG USAGE RULES:

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
   
🚨 CRITICAL - <ATT> TAG IS MANDATORY WHEN:
   - Korean question contains attribute words like: "흰색" (white), "빨간색" (red), "원형" (round), "정사각형" (square), "사람" (person), "객체" (object), "물체" (item), etc.
   - Korean question ends with "~사람은?" (which person?), "~객체는?" (which object?), "~물체는?" (which item?)
   - Korean question mentions specific attributes: "~색" (color), "~모양" (shape), "~재질" (material)
   - ALWAYS wrap attribute descriptions in <ATT> tags, even if the question seems simple
   - WRONG: "which white object" (missing <ATT> tag)
   - CORRECT: "which <ATT>white object</ATT>"
   - WRONG: "which person" (missing <ATT> tag)
   - CORRECT: "which <ATT>person</ATT>" or "which <ATT>person in white shirt</ATT>"

4. GENERAL RULES:
   - Tags MUST contain actual meaningful content (NOT empty like <ATT></ATT>)
   - Tags should be embedded naturally within the question sentence, not at the end
   - The <choice> tag MUST come BEFORE "And provide..." phrase
   - DO NOT use generic phrases like "in the image" for <POS> tag
   - If a phrase contains both attribute and location, split them appropriately

Reference examples from exo_data_sample.json:

Example 1: "<REL>Second-closest</REL> to the refrigerator a countertop located <POS>in the center</POS> of the image, which object is it <ATT>among the items</ATT>? <choice>(a) sink, (b) vase, (c) orange bag, (d) rightmost red chair</choice> And provide the bounding box coordinate of the region related to your answer."

Example 2: "Which <ATT>square-shaped item</ATT> is <REL>placed on the floor</REL> <POS>in front of</POS> the brown-haired man sitting on the sofa? <choice>(a) handbag, (b) coke, (c) laptop, (d) cell phone</choice> And provide the bounding box coordinate of the region related to your answer."

Example 3: "Which <ATT>round and cylindrical object</ATT> is <REL>farthest</REL> from the person sitting <POS>on the right side of</POS> the dining table? <choice>(a) plate, (b) white cake, (c) rightmost coke, (d) vase</choice> And provide the bounding box coordinate of the region related to your answer."

Example 4: "Which <ATT>edible food item</ATT> is the <REL>farthest</REL> from the fork <POS>on the left side of</POS> the table? <choice>(a) glass, (b) potato fries, (c) hamburger, (d) cell phone</choice> And provide the bounding box coordinate of the region related to your answer."

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
- 🚨 MANDATORY: If Korean question contains ANY attribute word (color, shape, material, "사람", "객체", "물체"), you MUST use <ATT> tag
- 🚨 MANDATORY: If Korean question ends with "~사람은?" or "~객체는?" or "~물체는?", you MUST include <ATT> tag
- 🚨 MANDATORY: NEVER translate "흰색 객체" as "white object" without <ATT> tags - it MUST be "<ATT>white object</ATT>"
- All tags have meaningful content inside them
- Tags are naturally embedded in the question sentence
- <choice> tag comes before "And provide..." phrase
- DO NOT use generic phrases like "in the image" for <POS> tag
- Choices are in concise adjective+noun or noun+noun format
- DOUBLE-CHECK: Before finalizing, verify that ALL attribute descriptions are wrapped in <ATT> tags"""
        
        # view_type에 따라 다른 시스템 메시지 사용
        if view_type == 'ego':
            system_message = "You are a professional translator specializing in VQA (Visual Question Answering) EGO-CENTRIC questions. CRITICAL RULES: 1) Use 'From the perspective of ~' for '~관점에서', 2) Use 'When I'm ~' for '내가', 3) <REL> tag ONLY for relationship terms (farthest, closest, etc.), 4) <POS> tag ONLY for position/location from person's perspective (on the left side, on the right side, etc.), 5) <ATT> tag ONLY for attributes/target groups (round object, green object, etc.), 6) Tags MUST contain actual meaningful content, 7) Format: [Question with tags] <choice>...</choice> And provide... (choice tag BEFORE 'And provide' phrase), 8) DO NOT use generic phrases like 'in the image' for <POS> tag, 9) Choices MUST be in concise adjective+noun or noun+noun format (e.g., 'black shirt person', 'glasses person'), NOT full sentences."
        else:
            system_message = "You are a professional translator specializing in VQA (Visual Question Answering) questions. CRITICAL RULES: 1) <REL> tag ONLY for relationship terms (farthest, closest, etc.), 2) <POS> tag ONLY for position/location (in the center, on the left side, etc.), 3) <ATT> tag ONLY for attributes/target groups (red object, among the items, etc.), 4) Tags MUST contain actual meaningful content, 5) Format: [Question with tags] <choice>...</choice> And provide... (choice tag BEFORE 'And provide' phrase), 6) DO NOT use generic phrases like 'in the image' for <POS> tag, 7) Choices MUST be in concise adjective+noun or noun+noun format (e.g., 'black shirt person', 'glasses person'), NOT full sentences."
        
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
        
        # ATT 태그 누락 검증 강화: 한국어 질문에 속성 단어가 있는데 ATT 태그가 없는 경우
        attribute_keywords_ko = ['흰색', '빨간색', '파란색', '초록색', '검은색', '노란색', '원형', '정사각형', '직사각형', '사람', '객체', '물체', '색', '모양', '재질']
        question_has_attribute = any(keyword in question_ko for keyword in attribute_keywords_ko)
        if question_has_attribute and not has_valid_att:
            return jsonify({
                'success': False, 
                'error': f'ATT tag is missing! Korean question contains attribute words but translation lacks <ATT> tag. Please ensure all attribute descriptions are wrapped in <ATT> tags. Translation: {translated_question[:200]}...'
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
    required_fields = ['question', 'response', 'view']
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
        'rationale': data.get('rationale', ''),
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
        spreadsheet = google_sheets_client.open_by_key(GOOGLE_SHEETS_SPREADSHEET_ID)
        
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
                'Question', 'Response', 'Rationale', 'View', 'Bbox'
            ]
            worksheet.append_row(headers)
            # 헤더 스타일 설정 (선택사항)
            try:
                worksheet.format('A1:I1', {'textFormat': {'bold': True}})
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
        row_data = [
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),  # 저장시간
            annotation.get('image_id', ''),
            annotation.get('image_path', ''),
            annotation.get('image_resolution', ''),
            annotation.get('question', ''),
            annotation.get('response', ''),
            annotation.get('rationale', ''),
            annotation.get('view', ''),
            bbox_str
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
            worksheet.update(f'A{row_to_update}:I{row_to_update}', [row_data])
        else:
            # 새 행 추가
            worksheet.append_row(row_data)
        
        return True
        
    except Exception as e:
        error_msg = str(e)
        print(f"[ERROR] Google Sheets 저장 중 오류: {e}")
        import traceback
        print(f"[ERROR] 상세 스택 트레이스:\n{traceback.format_exc()}")
        # 에러를 다시 발생시켜서 상위에서 처리하도록 함
        raise


def read_from_google_sheets(worker_id):
    """
    Google Sheets에서 작업자의 어노테이션 데이터 읽기
    
    Args:
        worker_id: 작업자 ID (예: "test")
        
    Returns:
        리스트: 각 행의 데이터 딕셔너리 리스트
        각 딕셔너리는 {'image_id': ..., '검수': ..., '비고': ..., '수정여부': ..., ...} 형태
    """
    if not google_sheets_client:
        return []
    
    try:
        # 스프레드시트 열기
        spreadsheet = google_sheets_client.open_by_key(GOOGLE_SHEETS_SPREADSHEET_ID)
        
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
        
        return result
        
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
        spreadsheet = google_sheets_client.open_by_key(GOOGLE_SHEETS_SPREADSHEET_ID)
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
            
            if not image_id:
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
        
    except Exception as e:
        print(f"[ERROR] 검수 상태 조회 중 오류: {e}")
        import traceback
        print(f"[ERROR] 상세 스택 트레이스:\n{traceback.format_exc()}")
        return jsonify({'error': f'검수 상태 조회 실패: {str(e)}'}), 500


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
