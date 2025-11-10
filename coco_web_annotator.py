#!/usr/bin/env python3
"""
Web-based COCO Annotation Interface (Flask)
Web-based annotation tool that can be used on remote servers
"""

import argparse
import base64
import json
import os
from io import BytesIO

from flask import Flask, render_template, request, jsonify, make_response
from PIL import Image
from pycocotools.coco import COCO
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
import re

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# OpenAI API Key (config.py에서 로드, 없으면 환경변수 또는 기본값 사용)
try:
    from config import OPENAI_API_KEY
except ImportError:
    import os
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
    if not OPENAI_API_KEY:
        print("[WARN] OpenAI API key not found. Please create config.py or set OPENAI_API_KEY environment variable.")

class COCOWebAnnotator:
    """Web-based COCO annotation tool for creating question-response pairs."""
    
    def __init__(self, mscoco_folder, coco_json_path, output_json_path, categories_json_path=None):
        # mscoco 폴더 경로 (exo_images와 ego_images가 있는 폴더)
        self.mscoco_folder = mscoco_folder
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
        
        # exo 먼저, 그 다음 ego, 마지막에 unknown
        self.image_ids = exo_image_ids + ego_image_ids + unknown_image_ids
        
        print(f"[INFO] Image order: {len(exo_image_ids)} exo images, {len(ego_image_ids)} ego images, {len(unknown_image_ids)} unknown images")

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
    response = make_response(render_template('index.html'))
    # 브라우저 캐시 방지
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

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
    
    if not question_ko:
        return jsonify({'success': False, 'error': 'Question (Korean) is required'}), 400
    
    try:
        if not OPENAI_AVAILABLE:
            return jsonify({'success': False, 'error': 'OpenAI library not installed. Install with: pip install openai'}), 500
        
        # OpenAI API 호출 (코드에서 직접 API 키 사용)
        if not OPENAI_API_KEY or OPENAI_API_KEY == "your-api-key-here":
            return jsonify({'success': False, 'error': 'OPENAI_API_KEY is not set. Please set it in coco_web_annotator.py'}), 500
        
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # exo_data_sample.json 형식 참고하여 프롬프트 작성
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
   - Examples: "red object", "square-shaped item", "among the items", "among the visible people", "edible food item"
   - Use for describing WHAT object/group is being asked about

Reference examples:
- "Which <ATT>red object</ATT> is <REL>farthest</REL> from the flag <POS>in the center of the table</POS>?"
- "Which <ATT>square-shaped item</ATT> is <REL>placed on the floor</REL> <POS>in front of</POS> the man?"
- "Which <ATT>edible food item</ATT> is the <REL>farthest</REL> from the fork <POS>on the left side of</POS> the table?"

Korean question: {question_ko}

Translate to English following the EXACT format above. Make sure:
- <REL> is used ONLY for relationship terms (farthest, closest, etc.)
- <POS> is used ONLY for position/location information (in the center, on the left side, etc.)
- <ATT> is used ONLY for attributes or target groups (red object, among the items, etc.)
- All tags have meaningful content inside them
- <choice> tag comes before "And provide..." phrase
- DO NOT use generic phrases like "in the image" for <POS> tag"""
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a professional translator specializing in VQA (Visual Question Answering) questions. CRITICAL RULES: 1) <REL> tag ONLY for relationship terms (farthest, closest, etc.), 2) <POS> tag ONLY for position/location (in the center, on the left side, etc.), 3) <ATT> tag ONLY for attributes/target groups (red object, among the items, etc.), 4) Tags MUST contain actual meaningful content, 5) Format: [Question with tags] <choice>...</choice> And provide..., 6) DO NOT use generic phrases like 'in the image' for <POS> tag."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        
        translated_question = response.choices[0].message.content.strip()
        
        # 태그 검증
        if '<ATT>' not in translated_question and '<POS>' not in translated_question and '<REL>' not in translated_question:
            return jsonify({'success': False, 'error': 'Translation must include at least one of <ATT>, <POS>, or <REL> tags'}), 400
        
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
        
        prompt = f"""Translate the following Korean multiple choice options to English. Keep them concise and natural.

(a) {choice_a}
(b) {choice_b}
(c) {choice_c}
(d) {choice_d}

Translate each option to English and format as: <choice>(a) translated_a, (b) translated_b, (c) translated_c, (d) translated_d</choice>

Return only the formatted <choice> tag with translations."""
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a translator. Translate Korean choices to natural English and format them correctly."},
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

@app.route('/api/analyze_image/<int:index>', methods=['GET'])
def analyze_image(index):
    """Analyze image using GPT-5 vision to extract detailed features."""
    if index >= len(annotator.image_ids):
        return jsonify({'error': 'Invalid index'}), 400
    
    image_id = annotator.image_ids[index]
    
    # 캐시 확인
    if image_id in image_analysis_cache:
        return jsonify({
            'success': True,
            'image_id': image_id,
            'analysis': image_analysis_cache[image_id],
            'cached': True
        })
    
    try:
        if not OPENAI_AVAILABLE:
            return jsonify({'success': False, 'error': 'OpenAI library not installed. Install with: pip install openai'}), 500
        
        if not OPENAI_API_KEY or OPENAI_API_KEY == "your-api-key-here":
            return jsonify({'success': False, 'error': 'OPENAI_API_KEY is not set. Please set it in coco_web_annotator.py'}), 500
        
        client = OpenAI(api_key=OPENAI_API_KEY)
        
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
        
        # 이미지 분석 프롬프트 (세부 특징 추출)
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
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": analysis_prompt
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_base64}"
                            }
                        }
                    ]
                }
            ],
            temperature=0.3,
            max_tokens=1000
        )
        
        analysis_result = response.choices[0].message.content.strip()
        
        # 캐시에 저장
        image_analysis_cache[image_id] = analysis_result
        
        return jsonify({
            'success': True,
            'image_id': image_id,
            'analysis': analysis_result,
            'cached': False
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

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
        image_analysis = ""
        if image_id and image_id in image_analysis_cache:
            image_analysis = image_analysis_cache[image_id]
        
        # Question과 Choices를 함께 번역하는 프롬프트 (이미지 분석 결과 포함)
        image_context = ""
        if image_analysis:
            image_context = f"""

IMAGE ANALYSIS CONTEXT:
{image_analysis}

Use this image analysis to better understand the context and spatial relationships mentioned in the Korean question. The analysis includes detailed features like colors, positions, orientations, and spatial relationships of objects in the image. Use this information to create more accurate <ATT>, <POS>, and <REL> tags that match the actual visual content."""
        
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
   - Examples: "red object", "square-shaped item", "among the items", "among the visible people", "edible food item", "object that can hold water", "non-edible item"
   - Use for describing WHAT object/group is being asked about
   - CORRECT: "Which <ATT>red object</ATT> is..."
   - CORRECT: "<ATT>Among the items</ATT> on the table..."
   - WRONG: "<ATT>flag in the center of the table</ATT>" (contains location, should split: flag <POS>in the center of the table</POS>)

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

Translate the Korean question and choices to English following the EXACT format above. Make sure:
- <REL> is used ONLY for relationship terms (farthest, closest, etc.)
- <POS> is used ONLY for position/location information (in the center, on the left side, etc.)
- <ATT> is used ONLY for attributes or target groups (red object, among the items, etc.)
- All tags have meaningful content inside them
- Tags are naturally embedded in the question sentence
- <choice> tag comes before "And provide..." phrase
- DO NOT use generic phrases like "in the image" for <POS> tag"""
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a professional translator specializing in VQA (Visual Question Answering) questions. CRITICAL RULES: 1) <REL> tag ONLY for relationship terms (farthest, closest, etc.), 2) <POS> tag ONLY for position/location (in the center, on the left side, etc.), 3) <ATT> tag ONLY for attributes/target groups (red object, among the items, etc.), 4) Tags MUST contain actual meaningful content, 5) Format: [Question with tags] <choice>...</choice> And provide... (choice tag BEFORE 'And provide' phrase), 6) DO NOT use generic phrases like 'in the image' for <POS> tag."},
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
        image_analysis = ""
        if image_id and image_id in image_analysis_cache:
            image_analysis = image_analysis_cache[image_id]
        
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
        
        prompt = f"""Translate the following Korean rationale to English. Follow these CRITICAL requirements:{image_context}{elimination_guide}

REQUIREMENTS:
1. The rationale MUST start with "The question is {question_type}:"
2. Use elimination method format: explain why incorrect choices are excluded, then explain why the correct answer is right
3. The translation must be at least 2 sentences long
4. Make it natural, grammatically correct, and detailed
5. Use the image analysis context to create accurate descriptions of spatial relationships and object positions
6. DO NOT include any bounding box coordinates (x1, y1, x2, y2) or coordinate information in the rationale
7. When the Korean rationale mentions choice letters (a, b, c, d), translate them to the corresponding English choice text from the question
8. CRITICAL: End the rationale with a simple "Therefore" statement. DO NOT add additional explanatory clauses after "Therefore" such as "as it is...", "because it is...", "since it is...", or any descriptive phrases that repeat information already stated

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
    
    # Validation: Check required fields
    required_fields = ['question', 'response', 'view', 'selected_bboxes']
    missing_fields = []
    
    for field in required_fields:
        if field == 'selected_bboxes':
            if not data.get(field) or len(data.get(field, [])) == 0:
                missing_fields.append('bbox')
        elif field == 'view':
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
    
    # bbox 처리: 단일 bbox면 배열로 감싸지 않고 그대로, 여러 개면 배열 유지
    selected_bboxes = data['selected_bboxes']
    if len(selected_bboxes) == 1:
        # 단일 bbox인 경우 배열로 감싸지 않고 직접 저장
        bbox_value = selected_bboxes[0]
    else:
        # 여러 bbox인 경우 배열로 저장
        bbox_value = selected_bboxes
    
    annotation = {
        'image_id': data['image_id'],
        'image_path': relative_image_path,  # 상대 경로로 변경
        'image_resolution': f"{image_info['width']}x{image_info['height']}",  # 원본 이미지 크기 (web_annotations_exo.json, web_annotations_ego.json에만 저장)
        'question': data['question'],
        'response': data['response'],
        'rationale': data.get('rationale', ''),
        'view': view_type,
        'bbox': bbox_value  # 단일 bbox는 배열로 감싸지 않음
    }
    
    # view 타입에 따라 해당 파일 경로 선택
    output_path = annotator.output_json_path_exo if view_type == 'exo' else annotator.output_json_path_ego
    other_output_path = annotator.output_json_path_ego if view_type == 'exo' else annotator.output_json_path_exo
    
    # 해당 view 타입의 annotations 로드
    view_annotations = []
    if os.path.exists(output_path):
        try:
            with open(output_path, 'r', encoding='utf-8') as f:
                view_annotations = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            view_annotations = []
    
    # 다른 view 타입 파일에서도 같은 image_id가 있으면 제거 (view 타입 변경 시)
    other_view_annotations = []
    if os.path.exists(other_output_path):
        try:
            with open(other_output_path, 'r', encoding='utf-8') as f:
                other_view_annotations = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            other_view_annotations = []
    
    # 다른 view 타입 파일에서 같은 image_id 제거
    other_view_annotations = [ann for ann in other_view_annotations if ann.get('image_id') != data['image_id']]
    
    # 현재 view 타입 파일에서 업데이트 또는 추가
    found = False
    for i, ann in enumerate(view_annotations):
        if ann.get('image_id') == data['image_id']:
            view_annotations[i] = annotation  # 덮어쓰기
            found = True
            break
    
    if not found:
        view_annotations.append(annotation)  # 새로 추가
    
    # Save to file
    try:
        # 출력 디렉토리 생성
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        
        # 현재 view 타입 파일 저장 (bbox는 한 줄로 저장)
        with open(output_path, 'w', encoding='utf-8') as f:
            json_str = json.dumps(view_annotations, indent=2, ensure_ascii=False)
            # bbox 배열을 한 줄로 변경: "bbox": [\n      숫자,\n      ...\n    ] -> "bbox": [숫자, ...]
            json_str = re.sub(
                r'"bbox":\s*\[\s*\n\s*([^\]]+?)\s*\n\s*\]',
                lambda m: f'"bbox": [{re.sub(r"\\s+", " ", m.group(1).strip())}]',
                json_str,
                flags=re.MULTILINE
            )
            f.write(json_str)
        
        # 다른 view 타입 파일도 저장 (같은 image_id 제거된 버전)
        if other_view_annotations != [] or os.path.exists(other_output_path):
            other_output_dir = os.path.dirname(other_output_path)
            if other_output_dir and not os.path.exists(other_output_dir):
                os.makedirs(other_output_dir, exist_ok=True)
            with open(other_output_path, 'w', encoding='utf-8') as f:
                json_str = json.dumps(other_view_annotations, indent=2, ensure_ascii=False)
                # bbox 배열을 한 줄로 변경
                json_str = re.sub(
                    r'"bbox":\s*\[\s*\n\s*([^\]]+?)\s*\n\s*\]',
                    lambda m: f'"bbox": [{re.sub(r"\\s+", " ", m.group(1).strip())}]',
                    json_str,
                    flags=re.MULTILINE
                )
                f.write(json_str)
        
        # 전체 annotations도 업데이트 (다음 로드 시 반영)
        annotator._reload_annotations()
        
        return jsonify({'success': True, 'updated': found})
    except (IOError, OSError) as e:
        return jsonify({'error': f'Failed to save: {e}'}), 500


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

    
    args = parser.parse_args()
    
    # Validate paths
    if not os.path.exists(args.mscoco_folder):
        print(f"Error: mscoco folder not found: {args.mscoco_folder}")
        return
    
    exo_images_path = os.path.join(args.mscoco_folder, 'exo_images')
    ego_images_path = os.path.join(args.mscoco_folder, 'ego_images')
    
    if not os.path.exists(exo_images_path):
        print(f"Warning: exo_images folder not found: {exo_images_path}")
    if not os.path.exists(ego_images_path):
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
                                 args.output_json, args.categories_json)
    
    # Create template
    create_template()
    
    print(f"Starting web server at http://{args.host}:{args.port}")
    print("Access the annotation tool in your web browser")
    print(f"Exo annotations will be saved to: {annotator.output_json_path_exo}")
    print(f"Ego annotations will be saved to: {annotator.output_json_path_ego}")
    
    app.run(host=args.host, port=args.port, debug=True)


if __name__ == "__main__":
    main()
