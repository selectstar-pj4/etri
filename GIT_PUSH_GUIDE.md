# Git Push 가이드

## .gitignore 설정 완료

다음 폴더/파일이 Git에서 제외됩니다:
- `mscoco/exo_images/` - Exo 이미지 폴더 (로컬에서만 사용)
- `mscoco/ego_images/` - Ego 이미지 폴더 (로컬에서만 사용)
- `__pycache__/` - Python 캐시 파일
- 기타 임시 파일들

## Git Push 명령어

Git이 설치되어 있다면 다음 명령어를 순서대로 실행하세요:

```bash
# 1. Git 저장소 초기화 (처음 한 번만)
git init

# 2. 원격 저장소 추가 (이미 있다면 생략)
git remote add origin <your-repository-url>

# 3. 변경사항 확인
git status

# 4. 모든 파일 추가 (.gitignore 제외)
git add .

# 5. 커밋
git commit -m "Initial commit: ETRI VQA Annotation Tool"

# 6. Push
git push -u origin main
# 또는
git push -u origin master
```

## 주의사항

- `mscoco/exo_images/`와 `mscoco/ego_images/` 폴더는 Git에 포함되지 않습니다
- 이 폴더들은 로컬에서 직접 해당 경로에 복사해서 사용하세요
- `coco_web_annotator.py`의 `OPENAI_API_KEY`가 코드에 포함되어 있으니, 공개 저장소에 push할 경우 보안에 주의하세요

