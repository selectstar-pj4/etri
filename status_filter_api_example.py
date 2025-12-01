"""
상태별 필터링 API 추가 예시
coco_web_annotator.py에 추가할 코드
"""

@app.route('/api/images_by_status', methods=['GET'])
def get_images_by_status():
    """
    상태별로 이미지 리스트를 필터링하여 반환
    Query parameters:
        - status: 'all', 'unfinished', 'passed', 'failed', 'delivered', 'skipped'
        - worker_id: 작업자 ID (선택, 없으면 WORKER_ID 사용)
        - sort_by: 'oldest', 'newest', 'image_id' (기본값: 'oldest')
    """
    try:
        worker_id = request.args.get('worker_id') or WORKER_ID
        status = request.args.get('status', 'all')  # all, unfinished, passed, failed, delivered, skipped
        sort_by = request.args.get('sort_by', 'oldest')  # oldest, newest, image_id
        
        if not worker_id:
            return jsonify({'error': '작업자 ID가 필요합니다.'}), 400
        
        # Google Sheets에서 데이터 읽기
        sheet_data = read_from_google_sheets(worker_id)
        
        # 모든 이미지 ID 가져오기 (ego_images 기준)
        all_ego_image_ids = []
        for image_id in annotator.image_ids:
            image_info = annotator.coco.imgs[image_id]
            file_name = image_info.get('file_name', '')
            ego_path = os.path.join(annotator.ego_images_folder, file_name)
            if os.path.exists(ego_path):
                all_ego_image_ids.append(image_id)
        
        # Google Sheets 데이터를 image_id로 매핑
        sheet_data_map = {}
        for row in sheet_data:
            image_id_str = row.get('Image ID', '') or row.get('image_id', '')
            if image_id_str:
                try:
                    image_id = int(image_id_str)
                    sheet_data_map[image_id] = {
                        'review_status': row.get('검수', '') or row.get('검수 상태', ''),
                        '저장시간': row.get('저장시간', ''),
                        '할당시간': row.get('할당시간', ''),  # 새로 추가할 필드
                        '수정여부': row.get('수정여부', '') or row.get('수정 여부', ''),
                        '비고': row.get('비고', '') or row.get('검수 의견', ''),
                        'view': row.get('View', '') or row.get('view', '')
                    }
                except ValueError:
                    continue
        
        # 상태별로 필터링
        filtered_images = []
        
        for image_id in all_ego_image_ids:
            sheet_info = sheet_data_map.get(image_id, {})
            review_status = sheet_info.get('review_status', '')
            저장시간 = sheet_info.get('저장시간', '')
            할당시간 = sheet_info.get('할당시간', '')
            
            # 상태 판단
            image_status = 'unfinished'  # 기본값
            if review_status == '통과':
                image_status = 'passed'
            elif review_status == '불통':
                image_status = 'failed'
            elif review_status == '납품 완료':
                image_status = 'delivered'
            elif 저장시간:  # 저장은 했지만 검수 상태가 없는 경우
                image_status = 'completed'
            elif not 저장시간 and not 할당시간:  # 아직 할당되지 않은 경우
                image_status = 'unassigned'
            
            # 필터링
            if status == 'all':
                filtered_images.append({
                    'image_id': image_id,
                    'status': image_status,
                    'review_status': review_status,
                    '저장시간': 저장시간,
                    '할당시간': 할당시간,
                    '수정여부': sheet_info.get('수정여부', ''),
                    '비고': sheet_info.get('비고', '')
                })
            elif status == 'unfinished' and image_status in ['unfinished', 'unassigned']:
                filtered_images.append({
                    'image_id': image_id,
                    'status': image_status,
                    'review_status': review_status,
                    '저장시간': 저장시간,
                    '할당시간': 할당시간,
                    '수정여부': sheet_info.get('수정여부', ''),
                    '비고': sheet_info.get('비고', '')
                })
            elif status == 'passed' and image_status == 'passed':
                filtered_images.append({
                    'image_id': image_id,
                    'status': image_status,
                    'review_status': review_status,
                    '저장시간': 저장시간,
                    '할당시간': 할당시간,
                    '수정여부': sheet_info.get('수정여부', ''),
                    '비고': sheet_info.get('비고', '')
                })
            elif status == 'failed' and image_status == 'failed':
                filtered_images.append({
                    'image_id': image_id,
                    'status': image_status,
                    'review_status': review_status,
                    '저장시간': 저장시간,
                    '할당시간': 할당시간,
                    '수정여부': sheet_info.get('수정여부', ''),
                    '비고': sheet_info.get('비고', '')
                })
            elif status == 'delivered' and image_status == 'delivered':
                filtered_images.append({
                    'image_id': image_id,
                    'status': image_status,
                    'review_status': review_status,
                    '저장시간': 저장시간,
                    '할당시간': 할당시간,
                    '수정여부': sheet_info.get('수정여부', ''),
                    '비고': sheet_info.get('비고', '')
                })
        
        # 정렬
        if sort_by == 'oldest':
            # 할당시간이 있으면 할당시간 기준, 없으면 image_id 기준
            filtered_images.sort(key=lambda x: (
                x['할당시간'] if x['할당시간'] else '9999-12-31',
                x['image_id']
            ))
        elif sort_by == 'newest':
            filtered_images.sort(key=lambda x: (
                x['할당시간'] if x['할당시간'] else '1970-01-01',
                -x['image_id']
            ), reverse=True)
        elif sort_by == 'image_id':
            filtered_images.sort(key=lambda x: x['image_id'])
        
        # 통계 계산
        stats = {
            'total': len(all_ego_image_ids),
            'unfinished': sum(1 for img in filtered_images if img['status'] in ['unfinished', 'unassigned']),
            'passed': sum(1 for img in filtered_images if img['status'] == 'passed'),
            'failed': sum(1 for img in filtered_images if img['status'] == 'failed'),
            'delivered': sum(1 for img in filtered_images if img['status'] == 'delivered'),
            'completed': sum(1 for img in filtered_images if img['status'] == 'completed')
        }
        
        return jsonify({
            'success': True,
            'status': status,
            'images': filtered_images,
            'count': len(filtered_images),
            'statistics': stats
        })
        
    except Exception as e:
        print(f"[ERROR] 상태별 이미지 조회 중 오류: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'조회 실패: {str(e)}'}), 500


@app.route('/api/work_statistics', methods=['GET'])
def get_work_statistics():
    """작업 통계 및 진행률 계산"""
    try:
        worker_id = request.args.get('worker_id') or WORKER_ID
        if not worker_id:
            return jsonify({'error': '작업자 ID가 필요합니다.'}), 400
        
        sheet_data = read_from_google_sheets(worker_id)
        
        # 모든 ego 이미지 개수
        all_ego_count = 0
        for image_id in annotator.image_ids:
            image_info = annotator.coco.imgs[image_id]
            file_name = image_info.get('file_name', '')
            ego_path = os.path.join(annotator.ego_images_folder, file_name)
            if os.path.exists(ego_path):
                all_ego_count += 1
        
        # 상태별 카운트
        stats = {
            'total': all_ego_count,
            'unfinished': 0,
            'passed': 0,
            'failed': 0,
            'delivered': 0,
            'completed': 0,
            'unassigned': 0
        }
        
        sheet_data_map = {}
        for row in sheet_data:
            image_id_str = row.get('Image ID', '') or row.get('image_id', '')
            if image_id_str:
                try:
                    image_id = int(image_id_str)
                    review_status = row.get('검수', '') or row.get('검수 상태', '')
                    저장시간 = row.get('저장시간', '')
                    할당시간 = row.get('할당시간', '')
                    
                    if review_status == '통과':
                        stats['passed'] += 1
                    elif review_status == '불통':
                        stats['failed'] += 1
                    elif review_status == '납품 완료':
                        stats['delivered'] += 1
                    elif 저장시간:
                        stats['completed'] += 1
                    elif not 저장시간 and 할당시간:
                        stats['unfinished'] += 1
                    elif not 저장시간 and not 할당시간:
                        stats['unassigned'] += 1
                except ValueError:
                    continue
        
        # 미작업 = unfinished + unassigned
        stats['unfinished'] = stats['unfinished'] + stats['unassigned']
        
        # 완료율 계산
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

