#!/usr/bin/env python3
"""
Annotation 유틸리티 스크립트
다양한 annotation 파일 관리 작업을 수행합니다.
"""

import argparse
import json
import os
import shutil
from pycocotools.coco import COCO


def add_image_resolution(annotation_file, coco_json_path):
    """
    annotation JSON 파일의 각 항목에 image_resolution 필드를 추가합니다.
    
    Args:
        annotation_file: 수정할 annotation JSON 파일 경로
        coco_json_path: COCO JSON 파일 경로 (이미지 크기 정보 가져오기용)
    """
    if not os.path.exists(annotation_file):
        print(f"[ERROR] Annotation file not found: {annotation_file}")
        return False
    
    if not os.path.exists(coco_json_path):
        print(f"[ERROR] COCO JSON file not found: {coco_json_path}")
        return False
    
    # COCO 데이터 로드
    print(f"Loading COCO data from {coco_json_path}...")
    coco = COCO(coco_json_path)
    
    # Annotation 파일 로드
    print(f"Loading annotations from {annotation_file}...")
    with open(annotation_file, 'r', encoding='utf-8') as f:
        annotations = json.load(f)
    
    if not isinstance(annotations, list):
        print(f"[ERROR] Annotation file should contain a JSON array")
        return False
    
    updated_count = 0
    missing_count = 0
    
    # 각 annotation에 image_resolution 추가
    for ann in annotations:
        image_id = ann.get('image_id')
        if not image_id:
            print(f"[WARN] Skipping annotation without image_id")
            continue
        
        # 이미 image_resolution이 있으면 스킵
        if 'image_resolution' in ann:
            continue
        
        # COCO에서 이미지 정보 가져오기
        if image_id not in coco.imgs:
            print(f"[WARN] Image ID {image_id} not found in COCO data")
            missing_count += 1
            continue
        
        image_info = coco.imgs[image_id]
        width = image_info.get('width')
        height = image_info.get('height')
        
        if width and height:
            ann['image_resolution'] = f"{width}x{height}"
            updated_count += 1
        else:
            print(f"[WARN] Image ID {image_id} missing width/height")
            missing_count += 1
    
    # 백업 파일 생성
    backup_file = annotation_file + '.backup'
    print(f"Creating backup: {backup_file}")
    with open(backup_file, 'w', encoding='utf-8') as f:
        json.dump(annotations, f, indent=2, ensure_ascii=False)
    
    # 수정된 파일 저장
    print(f"Saving updated annotations to {annotation_file}...")
    with open(annotation_file, 'w', encoding='utf-8') as f:
        json.dump(annotations, f, indent=2, ensure_ascii=False)
    
    print(f"\n[SUCCESS] Updated {updated_count} annotations")
    if missing_count > 0:
        print(f"[WARN] {missing_count} annotations could not be updated")
    
    return True


def fix_bbox_format(annotation_file):
    """
    annotation JSON 파일의 bbox 형식을 수정합니다.
    단일 bbox는 배열의 배열이 아닌 단일 배열로 변경합니다.
    
    Args:
        annotation_file: 수정할 annotation JSON 파일 경로
    """
    if not os.path.exists(annotation_file):
        print(f"[ERROR] Annotation file not found: {annotation_file}")
        return False
    
    # Annotation 파일 로드
    print(f"Loading annotations from {annotation_file}...")
    with open(annotation_file, 'r', encoding='utf-8') as f:
        annotations = json.load(f)
    
    if not isinstance(annotations, list):
        print(f"[ERROR] Annotation file should contain a JSON array")
        return False
    
    updated_count = 0
    
    # 각 annotation의 bbox 형식 수정
    for ann in annotations:
        bbox = ann.get('bbox')
        if bbox is None:
            continue
        
        # bbox가 배열의 배열이고 단일 bbox인 경우
        if isinstance(bbox, list) and len(bbox) == 1:
            if isinstance(bbox[0], list) and len(bbox[0]) == 4:
                # 단일 bbox를 배열에서 꺼내서 직접 저장
                ann['bbox'] = bbox[0]
                updated_count += 1
        # bbox가 이미 단일 배열인 경우는 그대로 유지
        elif isinstance(bbox, list) and len(bbox) == 4:
            # 이미 올바른 형식
            pass
        # 여러 bbox인 경우는 배열 유지
        elif isinstance(bbox, list) and len(bbox) > 1:
            # 여러 bbox는 배열로 유지
            pass
    
    # 백업 파일 생성
    backup_file = annotation_file + '.backup2'
    print(f"Creating backup: {backup_file}")
    with open(backup_file, 'w', encoding='utf-8') as f:
        json.dump(annotations, f, indent=2, ensure_ascii=False)
    
    # 수정된 파일 저장
    print(f"Saving updated annotations to {annotation_file}...")
    with open(annotation_file, 'w', encoding='utf-8') as f:
        json.dump(annotations, f, indent=2, ensure_ascii=False)
    
    print(f"\n[SUCCESS] Updated {updated_count} annotations")
    
    return True


def organize_images(exo_json_path, ego_json_path, mscoco_folder):
    """
    exo와 ego annotation 파일을 읽어서 이미지를 mscoco/exo_images/와 mscoco/ego_images/ 폴더로 복사합니다.
    
    Args:
        exo_json_path: exo annotations JSON 파일 경로
        ego_json_path: ego annotations JSON 파일 경로
        mscoco_folder: mscoco 폴더 경로 (exo_images와 ego_images가 있는 폴더)
    """
    output_exo_dir = os.path.join(mscoco_folder, 'exo_images')
    output_ego_dir = os.path.join(mscoco_folder, 'ego_images')
    
    # 출력 디렉토리 생성
    os.makedirs(output_exo_dir, exist_ok=True)
    os.makedirs(output_ego_dir, exist_ok=True)
    
    copied_exo = 0
    copied_ego = 0
    skipped_exo = 0
    skipped_ego = 0
    
    # exo 이미지 복사
    if os.path.exists(exo_json_path):
        print(f"\n[EXO] Processing {exo_json_path}...")
        with open(exo_json_path, 'r', encoding='utf-8') as f:
            exo_annotations = json.load(f)
        
        for ann in exo_annotations:
            # image_path에서 파일명 추출 (상대 경로 또는 절대 경로 모두 처리)
            image_path = ann.get('image_path', '')
            if not image_path:
                # image_id로 파일명 찾기
                image_id = ann.get('image_id')
                if image_id:
                    # COCO 형식: 000000391895.jpg
                    image_filename = f"{image_id:012d}.jpg"
                else:
                    print(f"  [WARN] Skipping annotation without image_path or image_id")
                    continue
            else:
                # 상대 경로에서 파일명 추출 (mscoco/exo_images/xxx.jpg 또는 mscoco/ego_images/xxx.jpg)
                image_filename = os.path.basename(image_path)
            
            # 원본 이미지 경로 (exo_images 또는 ego_images에서 찾기)
            source_path_exo = os.path.join(mscoco_folder, 'exo_images', image_filename)
            source_path_ego = os.path.join(mscoco_folder, 'ego_images', image_filename)
            
            # exo_images에서 먼저 찾고, 없으면 ego_images에서 찾기
            if os.path.exists(source_path_exo):
                source_path = source_path_exo
            elif os.path.exists(source_path_ego):
                source_path = source_path_ego
            else:
                print(f"  [WARN] Source image not found: {image_filename}")
                continue
            
            # 대상 경로
            dest_path = os.path.join(output_exo_dir, image_filename)
            
            if os.path.exists(source_path):
                if not os.path.exists(dest_path):
                    shutil.copy2(source_path, dest_path)
                    copied_exo += 1
                    if copied_exo % 100 == 0:
                        print(f"  Copied {copied_exo} exo images...")
                else:
                    skipped_exo += 1
            else:
                print(f"  [WARN] Source image not found: {source_path}")
        
        print(f"[EXO] Completed: {copied_exo} copied, {skipped_exo} already exist")
    else:
        print(f"[WARN] Exo JSON file not found: {exo_json_path}")
    
    # ego 이미지 복사
    if os.path.exists(ego_json_path):
        print(f"\n[EGO] Processing {ego_json_path}...")
        with open(ego_json_path, 'r', encoding='utf-8') as f:
            ego_annotations = json.load(f)
        
        for ann in ego_annotations:
            # image_path에서 파일명 추출
            image_path = ann.get('image_path', '')
            if not image_path:
                # image_id로 파일명 찾기
                image_id = ann.get('image_id')
                if image_id:
                    image_filename = f"{image_id:012d}.jpg"
                else:
                    print(f"  [WARN] Skipping annotation without image_path or image_id")
                    continue
            else:
                image_filename = os.path.basename(image_path)
            
            # 원본 이미지 경로 (exo_images 또는 ego_images에서 찾기)
            source_path_exo = os.path.join(mscoco_folder, 'exo_images', image_filename)
            source_path_ego = os.path.join(mscoco_folder, 'ego_images', image_filename)
            
            # ego_images에서 먼저 찾고, 없으면 exo_images에서 찾기
            if os.path.exists(source_path_ego):
                source_path = source_path_ego
            elif os.path.exists(source_path_exo):
                source_path = source_path_exo
            else:
                print(f"  [WARN] Source image not found: {image_filename}")
                continue
            
            # 대상 경로
            dest_path = os.path.join(output_ego_dir, image_filename)
            
            if os.path.exists(source_path):
                if not os.path.exists(dest_path):
                    shutil.copy2(source_path, dest_path)
                    copied_ego += 1
                    if copied_ego % 100 == 0:
                        print(f"  Copied {copied_ego} ego images...")
                else:
                    skipped_ego += 1
            else:
                print(f"  [WARN] Source image not found: {source_path}")
        
        print(f"[EGO] Completed: {copied_ego} copied, {skipped_ego} already exist")
    else:
        print(f"[WARN] Ego JSON file not found: {ego_json_path}")
    
    print(f"\n✅ Total: {copied_exo + copied_ego} images copied")
    print(f"   - Exo: {copied_exo} new, {skipped_exo} already exist")
    print(f"   - Ego: {copied_ego} new, {skipped_ego} already exist")


def main():
    parser = argparse.ArgumentParser(
        description='Annotation 유틸리티 스크립트 - 다양한 annotation 파일 관리 작업 수행')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # add_image_resolution 명령
    parser_add = subparsers.add_parser('add_resolution',
                                       help='Add image_resolution field to annotation JSON files')
    parser_add.add_argument('--annotation_file', required=True,
                           help='Path to annotation JSON file to update')
    parser_add.add_argument('--coco_json', required=True,
                           help='Path to COCO JSON file (for image size information)')
    
    # fix_bbox_format 명령
    parser_fix = subparsers.add_parser('fix_bbox',
                                       help='Fix bbox format in annotation JSON files')
    parser_fix.add_argument('--annotation_file', required=True,
                           help='Path to annotation JSON file to update')
    
    # organize_images 명령
    parser_org = subparsers.add_parser('organize_images',
                                       help='Organize images into exo_images and ego_images folders')
    parser_org.add_argument('--exo_json', required=True,
                           help='Path to exo annotations JSON file')
    parser_org.add_argument('--ego_json', required=True,
                           help='Path to ego annotations JSON file')
    parser_org.add_argument('--mscoco_folder', required=True,
                           help='Path to mscoco folder (contains exo_images and ego_images)')
    
    args = parser.parse_args()
    
    if args.command == 'add_resolution':
        add_image_resolution(args.annotation_file, args.coco_json)
    elif args.command == 'fix_bbox':
        fix_bbox_format(args.annotation_file)
    elif args.command == 'organize_images':
        if not os.path.exists(args.mscoco_folder):
            print(f"Error: mscoco folder not found: {args.mscoco_folder}")
            return
        
        exo_images_path = os.path.join(args.mscoco_folder, 'exo_images')
        ego_images_path = os.path.join(args.mscoco_folder, 'ego_images')
        
        if not os.path.exists(exo_images_path):
            print(f"Warning: exo_images folder not found: {exo_images_path}")
        if not os.path.exists(ego_images_path):
            print(f"Warning: ego_images folder not found: {ego_images_path}")
        
        organize_images(args.exo_json, args.ego_json, args.mscoco_folder)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

