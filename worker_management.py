"""
작업자 관리 시스템
- 작업자별 이미지 할당
- 작업 진행 상황 추적
- 시간당/일일 작업량 통계
- 스프레드시트 생성 및 Google Drive 업로드
"""

import json
import os
from datetime import datetime, timedelta
from collections import defaultdict
import csv

# Google Drive API 지원
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    GOOGLE_DRIVE_AVAILABLE = True
except ImportError:
    GOOGLE_DRIVE_AVAILABLE = False

class WorkerManager:
    """작업자 관리 클래스"""
    
    def __init__(self, workers_file='workers.json', assignments_file='worker_assignments.json', stats_file='worker_stats.json', 
                 google_drive_folder_id=None, google_credentials_path=None):
        self.workers_file = workers_file
        self.assignments_file = assignments_file
        self.stats_file = stats_file
        self.google_drive_folder_id = google_drive_folder_id
        self.google_credentials_path = google_credentials_path
        
        # Google Drive 서비스 초기화
        self.drive_service = None
        if GOOGLE_DRIVE_AVAILABLE and google_drive_folder_id and google_credentials_path:
            try:
                credentials = service_account.Credentials.from_service_account_file(
                    google_credentials_path,
                    scopes=['https://www.googleapis.com/auth/drive.file']
                )
                self.drive_service = build('drive', 'v3', credentials=credentials)
                print(f"[INFO] Google Drive API initialized. Folder ID: {google_drive_folder_id}")
            except Exception as e:
                print(f"[WARN] Failed to initialize Google Drive API: {e}")
                self.drive_service = None
        
        # 작업자 목록 로드
        self.workers = self.load_workers()
        
        # 이미지 할당 정보 로드
        self.assignments = self.load_assignments()
        
        # 통계 정보 로드
        self.stats = self.load_stats()
    
    def load_workers(self):
        """작업자 목록 로드"""
        if os.path.exists(self.workers_file):
            with open(self.workers_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    
    def save_workers(self):
        """작업자 목록 저장"""
        with open(self.workers_file, 'w', encoding='utf-8') as f:
            json.dump(self.workers, f, ensure_ascii=False, indent=2)
    
    def load_assignments(self):
        """이미지 할당 정보 로드"""
        if os.path.exists(self.assignments_file):
            with open(self.assignments_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    
    def save_assignments(self):
        """이미지 할당 정보 저장"""
        with open(self.assignments_file, 'w', encoding='utf-8') as f:
            json.dump(self.assignments, f, ensure_ascii=False, indent=2)
    
    def load_stats(self):
        """통계 정보 로드"""
        if os.path.exists(self.stats_file):
            with open(self.stats_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    
    def save_stats(self):
        """통계 정보 저장"""
        with open(self.stats_file, 'w', encoding='utf-8') as f:
            json.dump(self.stats, f, ensure_ascii=False, indent=2)
    
    def add_worker(self, worker_id, worker_name):
        """작업자 추가"""
        worker = {
            'worker_id': worker_id,
            'worker_name': worker_name,
            'created_at': datetime.now().isoformat(),
            'status': 'active'
        }
        self.workers.append(worker)
        self.save_workers()
        return worker
    
    def assign_images(self, worker_id, image_ids):
        """작업자에게 이미지 할당"""
        if worker_id not in self.assignments:
            self.assignments[worker_id] = []
        
        for image_id in image_ids:
            assignment = {
                'image_id': image_id,
                'assigned_at': datetime.now().isoformat(),
                'status': 'assigned',  # assigned, in_progress, completed
                'completed_at': None,
                'worker_id': worker_id
            }
            self.assignments[worker_id].append(assignment)
        
        self.save_assignments()
        return len(image_ids)
    
    def mark_completed(self, worker_id, image_id):
        """이미지 작업 완료 표시"""
        if worker_id not in self.assignments:
            return False
        
        for assignment in self.assignments[worker_id]:
            if assignment['image_id'] == image_id and assignment['status'] != 'completed':
                assignment['status'] = 'completed'
                assignment['completed_at'] = datetime.now().isoformat()
                
                # 통계 업데이트
                self.update_stats(worker_id, image_id, assignment['assigned_at'])
                
                self.save_assignments()
                return True
        
        return False
    
    def update_stats(self, worker_id, image_id, assigned_at):
        """통계 정보 업데이트"""
        if worker_id not in self.stats:
            self.stats[worker_id] = {
                'total_completed': 0,
                'daily_stats': {},
                'hourly_stats': {}
            }
        
        # 일일 통계
        today = datetime.now().date().isoformat()
        if today not in self.stats[worker_id]['daily_stats']:
            self.stats[worker_id]['daily_stats'][today] = 0
        self.stats[worker_id]['daily_stats'][today] += 1
        
        # 시간당 통계
        current_hour = datetime.now().strftime('%Y-%m-%d %H:00')
        if current_hour not in self.stats[worker_id]['hourly_stats']:
            self.stats[worker_id]['hourly_stats'][current_hour] = 0
        self.stats[worker_id]['hourly_stats'][current_hour] += 1
        
        # 총 완료 수
        self.stats[worker_id]['total_completed'] += 1
        
        self.save_stats()
    
    def get_worker_progress(self, worker_id):
        """작업자 진행 상황 조회"""
        if worker_id not in self.assignments:
            return {
                'assigned': 0,
                'in_progress': 0,
                'completed': 0,
                'total': 0
            }
        
        assigned = len(self.assignments[worker_id])
        completed = sum(1 for a in self.assignments[worker_id] if a['status'] == 'completed')
        in_progress = sum(1 for a in self.assignments[worker_id] if a['status'] == 'in_progress')
        
        return {
            'assigned': assigned,
            'in_progress': in_progress,
            'completed': completed,
            'total': assigned,
            'completion_rate': (completed / assigned * 100) if assigned > 0 else 0
        }
    
    def get_worker_stats(self, worker_id, date=None):
        """작업자 통계 조회"""
        if worker_id not in self.stats:
            return {
                'total_completed': 0,
                'daily_stats': {},
                'hourly_stats': {}
            }
        
        stats = self.stats[worker_id].copy()
        
        if date:
            # 특정 날짜의 통계만 반환
            stats['daily_stats'] = {k: v for k, v in stats['daily_stats'].items() if k == date}
            # 해당 날짜의 시간당 통계만 반환
            stats['hourly_stats'] = {k: v for k, v in stats['hourly_stats'].items() if k.startswith(date)}
        
        return stats
    
    def upload_to_google_drive(self, file_path, file_name):
        """Google Drive에 파일 업로드"""
        if not self.drive_service or not self.google_drive_folder_id:
            return None
        
        try:
            file_metadata = {
                'name': file_name,
                'parents': [self.google_drive_folder_id]
            }
            
            media = MediaFileUpload(file_path, mimetype='text/csv', resumable=True)
            file = self.drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink'
            ).execute()
            
            print(f"[INFO] File uploaded to Google Drive: {file.get('webViewLink')}")
            return file.get('webViewLink')
        except Exception as e:
            print(f"[WARN] Failed to upload to Google Drive: {e}")
            return None
    
    def export_to_spreadsheet(self, output_file='worker_stats.csv', date=None):
        """스프레드시트로 내보내기 (CSV) 및 Google Drive 업로드"""
        with open(output_file, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            
            # 헤더
            writer.writerow(['작업자 ID', '작업자 이름', '날짜', '시간', '작업 완료 수', '총 완료 수'])
            
            # 각 작업자별 통계
            for worker in self.workers:
                worker_id = worker['worker_id']
                worker_name = worker['worker_name']
                
                stats = self.get_worker_stats(worker_id, date)
                total_completed = stats['total_completed']
                
                # 시간당 통계
                if stats['hourly_stats']:
                    for hour, count in sorted(stats['hourly_stats'].items()):
                        date_part = hour.split(' ')[0]
                        time_part = hour.split(' ')[1]
                        writer.writerow([worker_id, worker_name, date_part, time_part, count, total_completed])
                else:
                    # 통계가 없으면 빈 행
                    writer.writerow([worker_id, worker_name, date or '전체', '', 0, total_completed])
        
        # Google Drive에 업로드
        drive_link = self.upload_to_google_drive(output_file, os.path.basename(output_file))
        
        return {
            'local_file': output_file,
            'drive_link': drive_link
        }
    
    def export_daily_summary(self, output_file='daily_summary.csv', date=None):
        """일일 요약 스프레드시트 생성 및 Google Drive 업로드"""
        if date is None:
            date = datetime.now().date().isoformat()
        
        with open(output_file, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            
            # 헤더
            writer.writerow(['작업자 ID', '작업자 이름', '날짜', '일일 작업 완료 수', '시간당 평균 작업 수', '총 완료 수'])
            
            # 각 작업자별 일일 통계
            for worker in self.workers:
                worker_id = worker['worker_id']
                worker_name = worker['worker_name']
                
                stats = self.get_worker_stats(worker_id, date)
                daily_count = stats['daily_stats'].get(date, 0)
                total_completed = stats['total_completed']
                
                # 시간당 평균 계산
                hourly_counts = [v for k, v in stats['hourly_stats'].items() if k.startswith(date)]
                hourly_avg = sum(hourly_counts) / len(hourly_counts) if hourly_counts else 0
                
                writer.writerow([worker_id, worker_name, date, daily_count, f'{hourly_avg:.2f}', total_completed])
        
        # Google Drive에 업로드
        drive_link = self.upload_to_google_drive(output_file, os.path.basename(output_file))
        
        return {
            'local_file': output_file,
            'drive_link': drive_link
        }


# Flask API 라우트 등록 함수
def register_worker_routes(app, worker_manager):
    """작업자 관리 API 라우트 등록"""
    
    if not worker_manager:
        return
    
    from flask import jsonify, request
    
    @app.route('/api/workers', methods=['GET'])
    def get_workers():
        """작업자 목록 조회"""
        workers = worker_manager.workers
        return jsonify({'success': True, 'workers': workers})
    
    @app.route('/api/workers', methods=['POST'])
    def add_worker():
        """작업자 추가 (이미 존재하면 그대로 반환)"""
        data = request.json
        worker_id = data.get('worker_id')
        worker_name = data.get('worker_name')
        
        if not worker_id or not worker_name:
            return jsonify({'success': False, 'error': 'worker_id and worker_name are required'}), 400
        
        # 이미 존재하는 작업자인지 확인
        existing_worker = None
        for worker in worker_manager.workers:
            if worker['worker_id'] == worker_id:
                existing_worker = worker
                # 이름이 다르면 업데이트
                if worker['worker_name'] != worker_name:
                    worker['worker_name'] = worker_name
                    worker_manager.save_workers()
                break
        
        if existing_worker:
            # 이미 존재하는 작업자
            return jsonify({'success': True, 'worker': existing_worker, 'is_new': False})
        else:
            # 새 작업자 추가
            worker = worker_manager.add_worker(worker_id, worker_name)
            return jsonify({'success': True, 'worker': worker, 'is_new': True})
    
    @app.route('/api/workers/<worker_id>/assign', methods=['POST'])
    def assign_images_to_worker(worker_id):
        """작업자에게 이미지 할당"""
        data = request.json
        image_ids = data.get('image_ids', [])
        
        if not image_ids:
            return jsonify({'success': False, 'error': 'image_ids are required'}), 400
        
        count = worker_manager.assign_images(worker_id, image_ids)
        return jsonify({'success': True, 'assigned_count': count})
    
    @app.route('/api/workers/<worker_id>/progress', methods=['GET'])
    def get_worker_progress(worker_id):
        """작업자 진행 상황 조회"""
        progress = worker_manager.get_worker_progress(worker_id)
        return jsonify({'success': True, 'progress': progress})
    
    @app.route('/api/workers/<worker_id>/stats', methods=['GET'])
    def get_worker_stats(worker_id):
        """작업자 통계 조회"""
        date = request.args.get('date')  # YYYY-MM-DD 형식
        stats = worker_manager.get_worker_stats(worker_id, date)
        return jsonify({'success': True, 'stats': stats})
    
    @app.route('/api/workers/<worker_id>/assignments', methods=['GET'])
    def get_worker_assignments(worker_id):
        """작업자에게 할당된 이미지 목록 조회"""
        if worker_id not in worker_manager.assignments:
            return jsonify({'success': True, 'assignments': []})
        
        assignments = worker_manager.assignments[worker_id]
        return jsonify({'success': True, 'assignments': assignments})
    
    @app.route('/api/workers/export', methods=['GET'])
    def export_worker_stats():
        """작업자 통계 스프레드시트 내보내기"""
        date = request.args.get('date')  # YYYY-MM-DD 형식, 없으면 전체
        export_type = request.args.get('type', 'detailed')  # 'detailed' or 'daily'
        
        if export_type == 'daily':
            result = worker_manager.export_daily_summary(date=date)
        else:
            result = worker_manager.export_to_spreadsheet(date=date)
        
        # result는 dict (local_file, drive_link) 또는 str (local_file만)
        if isinstance(result, dict):
            response_data = {
                'success': True,
                'local_file': result['local_file'],
                'drive_link': result.get('drive_link'),
                'message': f'Statistics exported to {result["local_file"]}'
            }
            if result.get('drive_link'):
                response_data['message'] += f' and uploaded to Google Drive'
        else:
            response_data = {
                'success': True,
                'local_file': result,
                'message': f'Statistics exported to {result}'
            }
        
        return jsonify(response_data)
    
    @app.route('/api/workers/<worker_id>/complete', methods=['POST'])
    def mark_image_completed(worker_id):
        """이미지 작업 완료 표시 (수동)"""
        data = request.json
        image_id = data.get('image_id')
        
        if not image_id:
            return jsonify({'success': False, 'error': 'image_id is required'}), 400
        
        success = worker_manager.mark_completed(worker_id, image_id)
        if success:
            progress = worker_manager.get_worker_progress(worker_id)
            return jsonify({'success': True, 'progress': progress})
        else:
            return jsonify({'success': False, 'error': 'Image not found or already completed'}), 404

