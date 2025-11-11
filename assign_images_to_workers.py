"""
작업자에게 이미지 할당 스크립트
이미지 ID 목록을 작업자들에게 분배하는 유틸리티
"""

import argparse
import json
from worker_management import WorkerManager

def assign_images_equally(worker_ids, image_ids):
    """이미지를 작업자들에게 균등하게 분배"""
    manager = WorkerManager()
    
    # 작업자 수만큼 이미지를 균등 분배
    num_workers = len(worker_ids)
    images_per_worker = len(image_ids) // num_workers
    remainder = len(image_ids) % num_workers
    
    assigned_count = 0
    for i, worker_id in enumerate(worker_ids):
        # 나머지가 있으면 앞의 작업자들에게 하나씩 추가
        start_idx = i * images_per_worker + min(i, remainder)
        end_idx = start_idx + images_per_worker + (1 if i < remainder else 0)
        
        worker_image_ids = image_ids[start_idx:end_idx]
        if worker_image_ids:
            count = manager.assign_images(worker_id, worker_image_ids)
            print(f"작업자 {worker_id}: {count}개 이미지 할당 (인덱스 {start_idx}~{end_idx-1})")
            assigned_count += count
    
    print(f"\n총 {assigned_count}개 이미지 할당 완료")
    return assigned_count

def assign_images_from_file(worker_ids, image_ids_file):
    """파일에서 이미지 ID 목록을 읽어서 할당"""
    with open(image_ids_file, 'r', encoding='utf-8') as f:
        image_ids = [int(line.strip()) for line in f if line.strip()]
    
    return assign_images_equally(worker_ids, image_ids)

def main():
    parser = argparse.ArgumentParser(description='작업자에게 이미지 할당')
    parser.add_argument('--workers', required=True, nargs='+', help='작업자 ID 목록 (예: worker001 worker002)')
    parser.add_argument('--images', nargs='+', type=int, help='이미지 ID 목록')
    parser.add_argument('--image_file', help='이미지 ID 목록이 있는 파일 경로 (한 줄에 하나씩)')
    parser.add_argument('--range', nargs=2, type=int, metavar=('START', 'END'), help='이미지 ID 범위 (예: --range 0 100)')
    
    args = parser.parse_args()
    
    # 이미지 ID 목록 생성
    if args.images:
        image_ids = args.images
    elif args.image_file:
        image_ids = assign_images_from_file(args.workers, args.image_file)
        return
    elif args.range:
        image_ids = list(range(args.range[0], args.range[1]))
    else:
        print("오류: --images, --image_file, 또는 --range 중 하나를 지정해야 합니다.")
        return
    
    # 이미지 할당
    assign_images_equally(args.workers, image_ids)

if __name__ == '__main__':
    main()

