"""
배치 처리 스크립트: 2,500장 이미지에 대한 QA 자동 생성 및 어노테이션 자동 저장

사용법:
    python batch_process_with_save.py --start_index 0 --end_index 2500 --model openai

옵션:
    --start_index: 시작 인덱스 (기본값: 0)
    --end_index: 종료 인덱스 (기본값: 2500)
    --model: 사용할 모델 (openai, claude, gemini) (기본값: openai)
    --parallel: 병렬 처리 개수 (기본값: 5)
    --view: view 타입 (exo, ego) (기본값: exo)
    --auto_bbox: 정답 bbox 자동 선택 여부 (기본값: True)
    --output: 결과 로그 파일 경로 (기본값: batch_results.json)
"""

import argparse
import requests
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

def get_image_info(index, base_url='http://localhost:5000', max_retries=3):
    """이미지 정보 가져오기 (bbox 포함) - 재시도 로직 포함"""
    for attempt in range(max_retries):
        try:
            response = requests.get(
                f'{base_url}/api/image/{index}',
                timeout=60  # 타임아웃 증가: 30초 -> 60초
            )
            if response.status_code == 200:
                return response.json()
            return None
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 2  # 2초, 4초, 6초 대기
                print(f"[WARN] Timeout for index {index}, retrying in {wait_time} seconds... (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
            else:
                print(f"[WARN] Failed to get image info for index {index} after {max_retries} attempts: Timeout")
                return None
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 2
                print(f"[WARN] Error for index {index}, retrying in {wait_time} seconds... (attempt {attempt + 1}/{max_retries}): {e}")
                time.sleep(wait_time)
            else:
                print(f"[WARN] Failed to get image info for index {index} after {max_retries} attempts: {e}")
                return None
    return None

def generate_qa_for_image(index, model='openai', view='exo', base_url='http://localhost:5000'):
    """단일 이미지에 대한 QA 생성 및 저장"""
    try:
        # 서버 부하 감소를 위한 최소 지연
        time.sleep(0.1)
        
        # 1단계: 이미지 정보 가져오기 (bbox 정보 포함)
        image_info = get_image_info(index)
        if not image_info:
            return {
                'index': index,
                'success': False,
                'error': 'Failed to get image info'
            }
        
        image_id = image_info.get('image_id')
        bboxes = image_info.get('bboxes', [])
        view_type = image_info.get('view_type', view)
        
        # bbox는 선택사항이므로 체크하지 않음
        
        # 2단계: 질문 생성 (재시도 로직 포함)
        qa_response = None
        for qa_attempt in range(3):  # 최대 3회 재시도
            try:
                qa_response = requests.post(
                    f'{base_url}/api/generate_question_and_choices',
                    json={'index': index, 'model': model},
                    timeout=180  # 타임아웃 증가: 120초 -> 180초
                )
                break  # 성공하면 루프 종료
            except requests.exceptions.Timeout:
                if qa_attempt < 2:
                    wait_time = (qa_attempt + 1) * 5  # 5초, 10초 대기
                    print(f"[WARN] QA generation timeout for index {index}, retrying in {wait_time} seconds... (attempt {qa_attempt + 1}/3)")
                    time.sleep(wait_time)
                else:
                    return {
                        'index': index,
                        'success': False,
                        'error': f'QA generation timeout after 3 attempts'
                    }
            except Exception as e:
                if qa_attempt < 2:
                    wait_time = (qa_attempt + 1) * 5
                    print(f"[WARN] QA generation error for index {index}, retrying in {wait_time} seconds... (attempt {qa_attempt + 1}/3): {e}")
                    time.sleep(wait_time)
                else:
                    return {
                        'index': index,
                        'success': False,
                        'error': f'QA generation failed after 3 attempts: {str(e)}'
                    }
        
        if qa_response is None:
            return {
                'index': index,
                'success': False,
                'error': 'QA generation failed: No response'
            }
        
        if qa_response.status_code != 200:
            return {
                'index': index,
                'success': False,
                'error': f'QA generation failed: {qa_response.text}'
            }
        
        qa_result = qa_response.json()
        if not qa_result.get('success'):
            return {
                'index': index,
                'success': False,
                'error': qa_result.get('error', 'QA generation failed')
            }
        
        questions = qa_result.get('questions', [])
        if not questions:
            return {
                'index': index,
                'success': False,
                'error': 'No questions generated'
            }
        
        # 3단계: 각 질문에 대해 저장
        # 주의: 현재 저장 API는 같은 image_id에 대해 하나의 어노테이션만 저장합니다.
        # 따라서 첫 번째 질문만 저장하거나, 각 질문을 순차적으로 저장해야 합니다.
        # 여기서는 첫 번째 질문만 저장합니다 (나머지는 수동으로 추가 가능)
        saved_count = 0
        saved_questions = []
        
        # 첫 번째 질문만 저장 (나머지는 웹 인터페이스에서 수동으로 추가 가능)
        for q_idx, question_data in enumerate(questions):
            # 첫 번째 질문만 저장 (나중에 수정 가능)
            if q_idx > 0:
                saved_questions.append({
                    'question_index': q_idx + 1,
                    'question': question_data.get('question', ''),
                    'note': '첫 번째 질문만 자동 저장됨. 나머지는 웹 인터페이스에서 수동 추가 가능'
                })
                continue
            try:
                # 정답 선택지에서 정보 가져오기
                correct_answer = question_data.get('correct_answer', 'a')
                choices = question_data.get('choices', {})
                
                # 정답에 해당하는 선택지 텍스트
                correct_choice_text = choices.get(correct_answer, '')
                
                # bbox는 선택사항이므로 빈 배열로 설정 (나중에 수동으로 추가 가능)
                selected_bbox = []
                
                # 영어 질문 번역 (한글 질문이 있으면 번역 필요)
                question_ko = question_data.get('question', '')
                
                # 번역 API 호출 (재시도 로직 포함)
                translate_response = None
                for trans_attempt in range(3):  # 최대 3회 재시도
                    try:
                        translate_response = requests.post(
                            f'{base_url}/api/translate/question_and_choices',
                            json={
                                'question_ko': question_ko,
                                'choice_a': choices.get('a', ''),
                                'choice_b': choices.get('b', ''),
                                'choice_c': choices.get('c', ''),
                                'choice_d': choices.get('d', ''),
                                'image_id': image_id
                            },
                            timeout=90  # 타임아웃 증가: 60초 -> 90초
                        )
                        break  # 성공하면 루프 종료
                    except requests.exceptions.Timeout:
                        if trans_attempt < 2:
                            wait_time = (trans_attempt + 1) * 3  # 3초, 6초 대기
                            print(f"[WARN] Translation timeout for index {index}, question {q_idx+1}, retrying in {wait_time} seconds... (attempt {trans_attempt + 1}/3)")
                            time.sleep(wait_time)
                        else:
                            print(f"[WARN] Translation failed for index {index}, question {q_idx+1}: Timeout after 3 attempts")
                            break
                    except Exception as e:
                        if trans_attempt < 2:
                            wait_time = (trans_attempt + 1) * 3
                            print(f"[WARN] Translation error for index {index}, question {q_idx+1}, retrying in {wait_time} seconds... (attempt {trans_attempt + 1}/3): {e}")
                            time.sleep(wait_time)
                        else:
                            print(f"[WARN] Translation failed for index {index}, question {q_idx+1}: {e}")
                            break
                
                if translate_response is None:
                    continue
                
                if translate_response.status_code != 200:
                    print(f"[WARN] Translation failed for index {index}, question {q_idx+1}")
                    continue
                
                translate_result = translate_response.json()
                if not translate_result.get('success'):
                    print(f"[WARN] Translation failed for index {index}, question {q_idx+1}: {translate_result.get('error')}")
                    continue
                
                translated_question = translate_result.get('translated_question', '')
                translated_choices = translate_result.get('translated_choices', '')
                choice_texts = translate_result.get('choice_texts', {})
                
                # response 형식: "(a) choice_text" 또는 "(b) choice_text" 등
                # 영어로 번역된 선택지 사용
                translated_correct_choice = choice_texts.get(correct_answer, correct_choice_text)
                response = f"({correct_answer}) {translated_correct_choice}"
                
                # 저장 API 호출
                save_data = {
                    'image_id': image_id,
                    'question': translated_question,
                    'response': response,
                    'rationale': '',  # 나중에 수동으로 추가 가능
                    'view': view_type,
                    'selected_bboxes': selected_bbox
                }
                
                # 약간의 지연 (API rate limit 방지 및 서버 부하 감소)
                time.sleep(0.2)  # 지연 시간 최소화
                
                # 저장 API 호출 (재시도 로직 포함)
                save_response = None
                for save_attempt in range(3):  # 최대 3회 재시도
                    try:
                        save_response = requests.post(
                            f'{base_url}/api/save',
                            json=save_data,
                            timeout=60  # 타임아웃 증가: 30초 -> 60초
                        )
                        break  # 성공하면 루프 종료
                    except requests.exceptions.Timeout:
                        if save_attempt < 2:
                            wait_time = (save_attempt + 1) * 2  # 2초, 4초 대기
                            print(f"[WARN] Save timeout for index {index}, question {q_idx+1}, retrying in {wait_time} seconds... (attempt {save_attempt + 1}/3)")
                            time.sleep(wait_time)
                        else:
                            print(f"[WARN] Save failed for index {index}, question {q_idx+1}: Timeout after 3 attempts")
                            break
                    except Exception as e:
                        if save_attempt < 2:
                            wait_time = (save_attempt + 1) * 2
                            print(f"[WARN] Save error for index {index}, question {q_idx+1}, retrying in {wait_time} seconds... (attempt {save_attempt + 1}/3): {e}")
                            time.sleep(wait_time)
                        else:
                            print(f"[WARN] Save failed for index {index}, question {q_idx+1}: {e}")
                            break
                
                if save_response is None:
                    continue
                
                if save_response.status_code == 200:
                    saved_count += 1
                    saved_questions.append({
                        'question_index': q_idx + 1,
                        'question': question_ko,
                        'response': response,
                        'saved': True
                    })
                else:
                    print(f"[WARN] Save failed for index {index}, question {q_idx+1}: {save_response.text}")
                    
            except Exception as e:
                print(f"[WARN] Error processing question {q_idx+1} for index {index}: {e}")
                continue
        
        return {
            'index': index,
            'image_id': image_id,
            'success': True,
            'questions_generated': len(questions),
            'questions_saved': saved_count,
            'saved_questions': saved_questions
        }
        
    except Exception as e:
        return {
            'index': index,
            'success': False,
            'error': str(e)
        }

def batch_process_with_save(start_index=0, end_index=2500, model='openai', parallel=5, view='exo', output='batch_results.json'):
    """배치 처리 메인 함수 (자동 저장 포함)"""
    print(f"배치 처리 시작: 인덱스 {start_index} ~ {end_index-1}")
    print(f"모델: {model}, 병렬 처리: {parallel}개, View: {view}")
    print(f"자동 저장 활성화: 질문 생성 후 자동으로 어노테이션에 저장됩니다.")
    
    results = []
    failed_indices = []
    total_questions_generated = 0
    total_questions_saved = 0
    
    # 진행 상황 표시를 위한 tqdm
    # 타임아웃 방지를 위해 병렬 처리 수 제한
    if parallel > 3:
        print(f"⚠️  병렬 처리 수가 {parallel}개로 설정되었습니다. 타임아웃 방지를 위해 3개로 제한합니다.")
        parallel = 3
    
    # 서버 부하 감소를 위해 순차적으로 작업 제출 (모든 작업을 한 번에 제출하지 않음)
    with tqdm(total=end_index - start_index, desc="처리 중") as pbar:
        with ThreadPoolExecutor(max_workers=parallel) as executor:
            # 작업을 순차적으로 제출 (서버 부하 분산)
            futures = {}
            for i in range(start_index, end_index):
                # 각 작업 제출 간 약간의 지연
                if i > start_index:
                    time.sleep(0.2)  # 작업 제출 간 0.2초 지연
                futures[executor.submit(generate_qa_for_image, i, model, view)] = i
            
            # 완료된 작업 처리
            for future in as_completed(futures):
                index = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    
                    if result['success']:
                        total_questions_generated += result.get('questions_generated', 0)
                        total_questions_saved += result.get('questions_saved', 0)
                    else:
                        failed_indices.append(index)
                        print(f"\n[실패] 인덱스 {index}: {result.get('error', 'Unknown error')}")
                    
                    pbar.update(1)
                    
                except Exception as e:
                    failed_indices.append(index)
                    print(f"\n[예외] 인덱스 {index}: {e}")
                    pbar.update(1)
    
    # 결과 저장
    output_data = {
        'total': end_index - start_index,
        'success': len(results) - len(failed_indices),
        'failed': len(failed_indices),
        'failed_indices': failed_indices,
        'total_questions_generated': total_questions_generated,
        'total_questions_saved': total_questions_saved,
        'results': results
    }
    
    with open(output, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n{'='*60}")
    print(f"처리 완료!")
    print(f"{'='*60}")
    print(f"성공한 이미지: {output_data['success']}개")
    print(f"실패한 이미지: {output_data['failed']}개")
    print(f"생성된 질문 총계: {total_questions_generated}개")
    print(f"저장된 질문 총계: {total_questions_saved}개")
    print(f"결과 로그 저장: {output}")
    print(f"\n어노테이션 파일:")
    print(f"  - Exo: ./mscoco/web_annotations_exo.json")
    print(f"  - Ego: ./mscoco/web_annotations_ego.json")
    
    if failed_indices:
        print(f"\n실패한 인덱스: {failed_indices[:10]}..." if len(failed_indices) > 10 else f"\n실패한 인덱스: {failed_indices}")
        print("재시도하려면 실패한 인덱스만 다시 실행하세요.")
        
        # 실패한 인덱스만 재시도하는 스크립트 생성
        retry_script = f"retry_failed_{int(time.time())}.py"
        with open(retry_script, 'w', encoding='utf-8') as f:
            f.write(f"""# 실패한 인덱스 재시도 스크립트
# 생성 시간: {time.strftime('%Y-%m-%d %H:%M:%S')}

failed_indices = {failed_indices}

import subprocess
import sys

# 각 실패한 인덱스에 대해 개별적으로 재시도
for idx in failed_indices:
    print(f"\\n재시도: 인덱스 {{idx}}")
    cmd = [
        sys.executable,
        "batch_process_with_save.py",
        "--start_index", str(idx),
        "--end_index", str(idx + 1),
        "--model", "{model}",
        "--parallel", "1",  # 재시도 시 단일 처리
        "--view", "{view}",
        "--output", "retry_result_{{idx}}.json"
    ]
    result = subprocess.run(cmd)
    if result.returncode == 0:
        print(f"✅ 인덱스 {{idx}} 재시도 성공")
    else:
        print(f"❌ 인덱스 {{idx}} 재시도 실패")
""")
        print(f"\n💡 실패한 인덱스 재시도 스크립트 생성: {retry_script}")
        print(f"   실행 방법: python {retry_script}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='배치 처리 스크립트 (자동 저장 포함)')
    parser.add_argument('--start_index', type=int, default=0, help='시작 인덱스')
    parser.add_argument('--end_index', type=int, default=2500, help='종료 인덱스')
    parser.add_argument('--model', type=str, default='openai', choices=['openai', 'claude', 'gemini'], help='사용할 모델')
    parser.add_argument('--parallel', type=int, default=5, help='병렬 처리 개수')
    parser.add_argument('--view', type=str, default='exo', choices=['exo', 'ego'], help='View 타입')
    parser.add_argument('--output', type=str, default='batch_results.json', help='결과 로그 파일 경로')
    
    args = parser.parse_args()
    
    batch_process_with_save(
        start_index=args.start_index,
        end_index=args.end_index,
        model=args.model,
        parallel=args.parallel,
        view=args.view,
        output=args.output
    )

