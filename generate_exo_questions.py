"""
exo_images의 모든 이미지에 대해 질문 생성 및 최적 질문 선정 스크립트

사용법:
    python generate_exo_questions.py --model openai --output mscoco/question_candidates_exo.json

옵션:
    --model: 사용할 모델 (openai) (기본값: openai)
    --output: 출력 JSON 파일 경로 (기본값: mscoco/question_candidates_exo.json)
    --start_index: 시작 인덱스 (기본값: 0)
    --end_index: 종료 인덱스 (기본값: None, 전체)
    --parallel: 병렬 처리 개수 (기본값: 3)
"""

import argparse
import requests
import json
import time
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

def select_best_question(questions):
    """
    질문 후보 3개 중 가장 적합한 1개를 선정
    
    선정 기준:
    1. ATT, POS, REL 모두 포함
    2. "~객체"로 끝남 ("는?", "는 무엇인가요?" 없음)
    3. 소거법 적합 (ATT 조건 만족 객체가 선택지에 최소 2개 이상)
    4. 동일 물체 중복 없음
    """
    if not questions or len(questions) == 0:
        return None
    
    best_question = None
    best_score = -1
    
    for q in questions:
        score = 0
        question_text = q.get('question', '').strip()
        choices = q.get('choices', {})
        
        # 1. ATT, POS, REL 포함 여부 확인
        has_att = 'ATT' in question_text or any(keyword in question_text for keyword in ['속성', '객체', '물체'])
        has_pos = 'POS' in question_text or any(keyword in question_text for keyword in ['위', '옆', '중앙', '왼쪽', '오른쪽', '앞', '뒤'])
        has_rel = 'REL' in question_text or any(keyword in question_text for keyword in ['가장 가까운', '가장 먼', '가까운', '먼'])
        
        if has_att:
            score += 1
        if has_pos:
            score += 1
        if has_rel:
            score += 1
        
        # 2. "~객체"로 끝나는지 확인
        if question_text.endswith('객체') or question_text.endswith('물체'):
            score += 2
        elif '는?' in question_text or '는 무엇인가요?' in question_text:
            score -= 1  # 감점
        
        # 3. 선택지 개수 확인 (4개여야 함)
        if len(choices) == 4:
            score += 1
        
        # 4. 선택지에 동일한 단어가 반복되지 않는지 확인
        choice_values = [v.lower() for v in choices.values()]
        unique_choices = len(set(choice_values))
        if unique_choices == 4:
            score += 1
        
        if score > best_score:
            best_score = score
            best_question = q
    
    # 점수가 너무 낮으면 첫 번째 질문 반환
    if best_score < 3:
        return questions[0]
    
    return best_question

def generate_questions_for_image(index, model='openai', base_url='http://localhost:5000', max_retries=3, skip_if_exists=False, existing_image_ids=None):
    """단일 이미지에 대해 질문 생성 및 최적 질문 선정"""
    try:
        # 1단계: 이미지 정보 가져오기
        image_info_response = requests.get(
            f'{base_url}/api/image/{index}',
            timeout=60
        )
        if image_info_response.status_code != 200:
            return {
                'index': index,
                'success': False,
                'error': f'Failed to get image info: {image_info_response.status_code}'
            }
        
        image_info = image_info_response.json()
        if 'error' in image_info:
            return {
                'index': index,
                'success': False,
                'error': image_info['error']
            }
        
        image_id = image_info.get('image_id')
        view_type = image_info.get('view_type', 'exo')
        
        # 작업자가 이미 저장한 질문이 있으면 건너뛰기
        if skip_if_exists and existing_image_ids is not None:
            if image_id in existing_image_ids:
                return {
                    'index': index,
                    'image_id': image_id,
                    'success': True,
                    'skipped': True,
                    'message': 'Already exists in annotations, skipped'
                }
        
        # exo 이미지만 처리
        if view_type != 'exo':
            return {
                'index': index,
                'success': False,
                'error': f'Not an exo image (view_type: {view_type})'
            }
        
        # 2단계: 질문 생성 (재시도 로직 포함, RateLimitError 처리)
        questions_response = None
        retry_delay = 1  # 초기 대기 시간 (초)
        
        for attempt in range(max_retries):
            try:
                questions_response = requests.post(
                    f'{base_url}/api/generate_question_and_choices',
                    json={'index': index, 'model': model},
                    timeout=180
                )
                
                # RateLimitError (HTTP 429) 처리
                if questions_response.status_code == 429:
                    if attempt < max_retries - 1:
                        # Retry-After 헤더 확인
                        retry_after = questions_response.headers.get('Retry-After')
                        if retry_after:
                            wait_time = float(retry_after) + 2  # Retry-After에 2초 추가 여유
                        else:
                            wait_time = retry_delay * (2 ** attempt)  # exponential backoff
                        
                        print(f"[WARN] Rate limit reached for image {index}. Waiting {wait_time:.2f} seconds before retry {attempt + 1}/{max_retries}...")
                        time.sleep(wait_time)
                        continue
                    else:
                        return {
                            'index': index,
                            'success': False,
                            'error': f'Rate limit exceeded after {max_retries} retries'
                        }
                
                if questions_response.status_code == 200:
                    break
                    
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 5
                    print(f"[WARN] Timeout for image {index}. Waiting {wait_time} seconds before retry {attempt + 1}/{max_retries}...")
                    time.sleep(wait_time)
                    continue
                else:
                    return {
                        'index': index,
                        'success': False,
                        'error': 'Timeout after retries'
                    }
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 5
                    print(f"[WARN] Request failed for image {index}: {str(e)}. Waiting {wait_time} seconds before retry {attempt + 1}/{max_retries}...")
                    time.sleep(wait_time)
                    continue
                else:
                    return {
                        'index': index,
                        'success': False,
                        'error': f'Request failed: {str(e)}'
                    }
        
        if questions_response is None or questions_response.status_code != 200:
            error_msg = 'Failed to generate questions'
            if questions_response is not None:
                try:
                    error_data = questions_response.json()
                    error_msg = error_data.get('error', error_msg)
                except:
                    error_msg = f'HTTP {questions_response.status_code}: {error_msg}'
            
            return {
                'index': index,
                'success': False,
                'error': error_msg
            }
        
        questions_result = questions_response.json()
        if not questions_result.get('success'):
            return {
                'index': index,
                'success': False,
                'error': questions_result.get('error', 'Question generation failed')
            }
        
        questions = questions_result.get('questions', [])
        if not questions or len(questions) == 0:
            return {
                'index': index,
                'success': False,
                'error': 'No questions generated'
            }
        
        # 3단계: 가장 적합한 질문 선정
        best_question = select_best_question(questions)
        
        if not best_question:
            return {
                'index': index,
                'success': False,
                'error': 'Failed to select best question'
            }
        
        return {
            'index': index,
            'image_id': image_id,
            'success': True,
            'question': best_question.get('question', ''),
            'choices': best_question.get('choices', {}),
            'correct_answer': best_question.get('correct_answer', 'a'),
            'all_questions': questions  # 나중에 참고용으로 전체 질문도 저장
        }
        
    except Exception as e:
        return {
            'index': index,
            'success': False,
            'error': str(e)
        }

def load_existing_annotations(annotation_path):
    """기존 annotation 파일에서 이미 저장된 image_id 목록 가져오기"""
    existing_image_ids = set()
    
    if not os.path.exists(annotation_path):
        return existing_image_ids
    
    try:
        with open(annotation_path, 'r', encoding='utf-8') as f:
            annotations = json.load(f)
        
        # annotations가 리스트인 경우
        if isinstance(annotations, list):
            for ann in annotations:
                image_id = ann.get('image_id')
                if image_id is not None:
                    existing_image_ids.add(int(image_id))
        # annotations가 딕셔너리인 경우 (image_id를 키로 사용)
        elif isinstance(annotations, dict):
            for key in annotations.keys():
                try:
                    existing_image_ids.add(int(key))
                except ValueError:
                    pass
        
        print(f"[INFO] Found {len(existing_image_ids)} existing annotations in {annotation_path}")
    except Exception as e:
        print(f"[WARN] Failed to load existing annotations from {annotation_path}: {e}")
    
    return existing_image_ids

def get_exo_image_indices(base_url='http://localhost:5000'):
    """exo_images에 해당하는 이미지 인덱스 목록 가져오기 (새로운 API 사용)"""
    print("[INFO] Getting exo image indices from server...")
    
    try:
        # 새로운 API 엔드포인트 사용 (훨씬 빠름)
        response = requests.get(f'{base_url}/api/exo_image_indices', timeout=60)
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                exo_indices = data.get('exo_indices', [])
                print(f"[INFO] Found {len(exo_indices)} exo images from server API")
                return exo_indices
            else:
                print(f"[WARN] API returned error: {data.get('error')}")
        else:
            print(f"[WARN] API request failed: {response.status_code}")
    except Exception as e:
        print(f"[WARN] Failed to use new API, falling back to old method: {e}")
    
    # Fallback: 기존 방법 (느리지만 작동함)
    print("[INFO] Using fallback method (slower)...")
    exo_indices = []
    
    first_image = requests.get(f'{base_url}/api/image/0', timeout=30)
    if first_image.status_code != 200:
        print(f"[ERROR] Failed to connect to server: {first_image.status_code}")
        return []
    
    first_data = first_image.json()
    total_images = first_data.get('total_images', 0)
    print(f"[INFO] Total images: {total_images}, scanning for exo images...")
    
    for idx in range(total_images):
        try:
            img_response = requests.get(f'{base_url}/api/image/{idx}', timeout=30)
            if img_response.status_code == 200:
                img_data = img_response.json()
                view_type = img_data.get('view_type', 'exo')
                if view_type == 'exo':
                    exo_indices.append(idx)
            
            if (idx + 1) % 1000 == 0:
                print(f"[INFO] Checked {idx + 1}/{total_images} images, found {len(exo_indices)} exo images...")
        except Exception as e:
            print(f"[WARN] Failed to check image {idx}: {e}")
            continue
    
    print(f"[INFO] Found {len(exo_indices)} exo images")
    return exo_indices

def main():
    parser = argparse.ArgumentParser(description='Generate questions for all exo_images')
    parser.add_argument('--model', type=str, default='openai', help='Model to use (openai only)')
    parser.add_argument('--output', type=str, default='mscoco/question_candidates_exo.json', help='Output JSON file path')
    parser.add_argument('--start_index', type=int, default=0, help='Start index')
    parser.add_argument('--end_index', type=int, default=None, help='End index (None for all)')
    parser.add_argument('--parallel', type=int, default=3, help='Number of parallel workers')
    parser.add_argument('--base_url', type=str, default='http://localhost:5000', help='Base URL of the API server')
    parser.add_argument('--skip_existing', action='store_true', help='Skip images that already have annotations saved')
    parser.add_argument('--annotation_path', type=str, default=None, help='Path to existing annotation file to check (e.g., mscoco/web_annotations_exo.json)')
    
    args = parser.parse_args()
    
    print(f"[INFO] Starting question generation for exo_images")
    print(f"[INFO] Model: {args.model}")
    print(f"[INFO] Output: {args.output}")
    print(f"[INFO] Parallel workers: {args.parallel}")
    
    # exo 이미지 인덱스 목록 가져오기
    print("[INFO] Getting exo image indices...")
    exo_indices = get_exo_image_indices(args.base_url)
    
    if not exo_indices:
        print("[ERROR] No exo images found")
        return
    
    # 인덱스 범위 필터링
    if args.start_index > 0 or args.end_index is not None:
        if args.end_index is not None:
            exo_indices = [idx for idx in exo_indices if args.start_index <= idx < args.end_index]
        else:
            exo_indices = [idx for idx in exo_indices if idx >= args.start_index]
    
    print(f"[INFO] Found {len(exo_indices)} exo images to process")
    
    # 기존 annotation 파일에서 이미 저장된 image_id 목록 가져오기
    existing_image_ids = set()
    if args.skip_existing:
        if args.annotation_path:
            annotation_path = args.annotation_path
        else:
            # 기본 경로 시도 (mscoco 폴더 기준)
            possible_paths = [
                'mscoco/web_annotations_exo.json',
                os.path.join(os.path.dirname(args.output), 'web_annotations_exo.json'),
                'web_annotations_exo.json'
            ]
            annotation_path = None
            for path in possible_paths:
                if os.path.exists(path):
                    annotation_path = path
                    break
        
        if annotation_path:
            existing_image_ids = load_existing_annotations(annotation_path)
            print(f"[INFO] Will skip {len(existing_image_ids)} images that already have annotations")
        else:
            print(f"[WARN] Annotation file not found. Use --annotation_path to specify the path.")
    
    # 기존 결과 로드 (재개용)
    results = {}
    if os.path.exists(args.output):
        try:
            with open(args.output, 'r', encoding='utf-8') as f:
                results = json.load(f)
            print(f"[INFO] Loaded {len(results)} existing results from output file")
        except Exception as e:
            print(f"[WARN] Failed to load existing results: {e}")
    
    # 처리할 인덱스 필터링 (이미 처리된 것 제외)
    processed_indices = set(int(k) for k in results.keys() if results[k].get('success', False))
    indices_to_process = [idx for idx in exo_indices if idx not in processed_indices]
    
    print(f"[INFO] {len(indices_to_process)} images to process (skipping {len(processed_indices)} already processed in output file)")
    
    if not indices_to_process:
        print("[INFO] All images already processed")
        return
    
    # 병렬 처리
    success_count = 0
    fail_count = 0
    
    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        # 작업 제출
        future_to_index = {
            executor.submit(generate_questions_for_image, idx, args.model, args.base_url, 3, args.skip_existing, existing_image_ids): idx
            for idx in indices_to_process
        }
        
        # 진행 상황 표시
        with tqdm(total=len(indices_to_process), desc="Generating questions") as pbar:
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                try:
                    result = future.result()
                    
                    if result.get('success'):
                        # 건너뛴 경우는 저장하지 않음
                        if result.get('skipped'):
                            print(f"\n[SKIP] Index {idx}: {result.get('message', 'Skipped')}")
                            success_count += 1  # 건너뛴 것도 성공으로 카운트
                        else:
                            # 결과 저장
                            image_id = result.get('image_id')
                            if image_id:
                                results[str(image_id)] = {
                                    'index': result['index'],
                                    'question': result['question'],
                                    'choices': result['choices'],
                                    'correct_answer': result['correct_answer']
                                }
                            success_count += 1
                    else:
                        fail_count += 1
                        print(f"\n[ERROR] Index {idx}: {result.get('error', 'Unknown error')}")
                    
                    # 주기적으로 저장 (매 10개마다)
                    if (success_count + fail_count) % 10 == 0:
                        output_dir = os.path.dirname(args.output)
                        if output_dir and not os.path.exists(output_dir):
                            os.makedirs(output_dir, exist_ok=True)
                        
                        with open(args.output, 'w', encoding='utf-8') as f:
                            json.dump(results, f, indent=2, ensure_ascii=False)
                    
                    pbar.update(1)
                    
                except Exception as e:
                    fail_count += 1
                    print(f"\n[ERROR] Index {idx}: Exception occurred: {e}")
                    pbar.update(1)
    
    # 최종 저장
    output_dir = os.path.dirname(args.output)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
    
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n[INFO] Completed!")
    print(f"[INFO] Success: {success_count}, Failed: {fail_count}")
    print(f"[INFO] Results saved to: {args.output}")

if __name__ == '__main__':
    main()

