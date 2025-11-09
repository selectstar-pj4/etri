# ETRI VQA Annotation Tool

MS-COCO 데이터셋 이미지를 사용하여 Visual Question Answering (VQA) 어노테이션을 생성하는 웹 기반 도구입니다.

## 📋 목차

- [실행 방법](#실행-방법)
- [필수 요구사항](#필수-요구사항)
- [프로젝트 구조](#프로젝트-구조)
- [주요 기능](#주요-기능)
- [사용법](#사용법)
- [출력 형식](#출력-형식)
- [키보드 단축키](#키보드-단축키)
- [문제 해결](#문제-해결)

## 🚀 실행 방법

### 1. 필수 패키지 설치

```bash
pip install flask pillow pycocotools openai
```

### 2. OpenAI API 키 설정

`config.py.example` 파일을 복사하여 `config.py`로 이름을 변경하고 API 키를 입력하세요:

```bash
# Windows
copy config.py.example config.py

# Linux/Mac
cp config.py.example config.py
```

그 다음 `config.py` 파일을 열어 API 키를 입력:

```python
OPENAI_API_KEY = "your-api-key-here"
```

**참고**: `config.py`는 `.gitignore`에 포함되어 Git에 업로드되지 않습니다.

### 3. 서버 실행

기본 설정으로 실행:

```bash
python coco_web_annotator.py --mscoco_folder ./mscoco --coco_json ./mscoco/instances_train2017.json --output_json ./mscoco/web_annotations.json
```

또는 커스텀 설정으로 실행:

```bash
python coco_web_annotator.py \
    --mscoco_folder ./mscoco \
    --coco_json ./mscoco/instances_train2017.json \
    --output_json ./mscoco/web_annotations.json \
    --categories_json ./mscoco/categories.json \
    --host 0.0.0.0 \
    --port 5000
```

### 4. 웹 인터페이스 접속

브라우저에서 다음 주소로 접속:

- **로컬**: `http://localhost:5000`
- **원격 서버**: `http://[서버IP]:5000`

## 📦 필수 요구사항

- Python 3.x
- 필수 Python 패키지:
  - `flask`: 웹 서버
  - `pillow`: 이미지 처리
  - `pycocotools`: COCO 데이터셋 처리
  - `openai`: 번역 및 검수 기능 (선택사항)

## 📁 프로젝트 구조

```
etri_annotation_tool/
├── coco_web_annotator.py      # Flask 웹 애플리케이션 (메인)
├── annotation_utils.py         # 어노테이션 유틸리티 스크립트
├── exo_data_sample.json       # 출력 형식 샘플
├── README.md                  # 이 파일
├── mscoco/
│   ├── exo_images/            # Exo 이미지 폴더
│   ├── ego_images/            # Ego 이미지 폴더
│   ├── instances_train2017.json  # COCO 어노테이션 파일
│   ├── web_annotations_exo.json   # Exo 어노테이션 출력 파일
│   └── web_annotations_ego.json  # Ego 어노테이션 출력 파일
└── templates/
    └── index.html             # 웹 인터페이스 템플릿
```

## ✨ 주요 기능

### 1. 이중 패널 인터페이스
- **왼쪽 패널**: 한글 입력 (Question, Choices, Rationale)
- **오른쪽 패널**: 영어 출력 및 어노테이션 관리

### 2. 자동 번역 기능
- **GPT-4o-mini 기반 번역**: 한글 질문과 선택지를 영어로 자동 번역
- **이미지 분석 통합**: 이미지 컨텍스트를 활용한 정확한 번역
- **태그 자동 생성**: `<ATT>`, `<POS>`, `<REL>` 태그 자동 포함
- **한글 근거 자동 생성**: 선택된 답안을 바탕으로 한글 근거 자동 생성

### 3. Exo/Ego 분리
- 이미지를 `exo_images`와 `ego_images` 폴더로 자동 분류
- 각각 별도의 JSON 파일로 저장 (`web_annotations_exo.json`, `web_annotations_ego.json`)

### 4. Bounding Box 관리
- 인터랙티브한 bbox 선택/해제
- 시각적 피드백 제공
- 단축키로 빠른 토글 (Ctrl+X)

### 5. 자동 저장
- 30초마다 자동 저장
- 페이지 종료 시 자동 저장
- 마지막 작업 이미지 기억

## 📖 사용법

### 기본 워크플로우

1. **한글 입력** (왼쪽 패널)
   - Question (한글) 텍스트 영역에 질문 입력
   - Choices (객관식 선지)에 a, b, c, d 선택지 입력
   - 정답 선택 (라디오 버튼)
   - Rationale (한글) 텍스트 영역에 근거 입력

2. **번역 실행**
   - Choices 아래 "번역" 버튼 클릭
   - 이미지 분석 → 질문/선택지 번역 → 한글 근거 자동 생성
   - Rationale (한글) 아래 "번역" 버튼 클릭하여 영어 근거 생성

3. **Bounding Box 선택**
   - 이미지에서 bbox 클릭하여 선택/해제
   - Ctrl+X로 bbox 표시/숨김 토글

4. **View 타입 선택**
   - Exo 또는 Ego 라디오 버튼 선택 (이미지 폴더에 따라 자동 설정)

5. **저장**
   - Ctrl+S 또는 "Save" 버튼 클릭
   - 자동 저장도 활성화됨

### 이미지 탐색

- **Previous/Next 버튼**: 이전/다음 이미지로 이동
- **Ctrl+Left/Right Arrow**: 키보드로 이동
- **Image ID 검색**: Image ID 입력 후 "Go" 버튼 클릭

## 📄 출력 형식

출력 JSON 파일 형식:

```json
[
  {
    "image_id": 579446,
    "image_path": "/000000579446.jpg",
    "image_resolution": "480x640",
    "question": "<REL>Second-closest</REL> to the refrigerator a countertop located <POS>in the center</POS> of the image, which object is it <ATT>among the items</ATT>? <choice>(a) sink, (b) vase, (c) orange bag, (d) rightmost red chair</choice> And provide the bounding box coordinate of the region related to your answer.",
    "response": "(b) vase",
    "rationale": "The question is exo-centric: The sink is placed immediately adjacent to the refrigerator, making it the closest. The vase sits slightly forward on the counter, farther than the sink but clearly closer than the orange bag at the far right edge and the red chair in the front seating area. Therefore the vase is second-closest.",
    "bbox": [260.35, 375.13, 18.42, 21.74],
    "view": "exo"
  }
]
```

### 필드 설명

- `image_id`: COCO 데이터셋 이미지 ID
- `image_path`: 이미지 파일 경로 (상대 경로)
- `image_resolution`: 이미지 원본 크기 (예: "640x480")
- `question`: 영어 질문 (태그 포함)
- `response`: 답변 (예: "(b) vase")
- `rationale`: 영어 근거 설명
- `bbox`: 선택된 bounding box 좌표 `[x, y, width, height]` (단일 bbox) 또는 `[[x1, y1, w1, h1], [x2, y2, w2, h2]]` (다중 bbox)
- `view`: 뷰 타입 ("exo" 또는 "ego")

## ⌨️ 키보드 단축키

### 기본 단축키
- `Ctrl+S`: 현재 어노테이션 저장
- `Ctrl+Left Arrow`: 이전 이미지
- `Ctrl+Right Arrow`: 다음 이미지

### Bbox 관리
- `Ctrl+X`: Bbox 표시/숨김 토글

### 객관식 선택
- `Ctrl+1`: 선택지 (a) 선택
- `Ctrl+2`: 선택지 (b) 선택
- `Ctrl+3`: 선택지 (c) 선택
- `Ctrl+4`: 선택지 (d) 선택

**참고**: textarea나 input에 포커스가 있을 때는 단축키가 작동하지 않습니다.

## 🔧 문제 해결

### 1. "COCO JSON file not found" 오류

**원인**: COCO 어노테이션 파일 경로가 잘못되었습니다.

**해결**:
```bash
# 올바른 경로로 실행
python coco_web_annotator.py --coco_json ./mscoco/instances_train2017.json ...
```

### 2. "Image folder not found" 오류

**원인**: 이미지 폴더 경로가 잘못되었습니다.

**해결**:
- `mscoco` 폴더에 `exo_images`와 `ego_images` 폴더가 있는지 확인
- `--mscoco_folder` 옵션에 올바른 경로 지정

### 3. 포트가 이미 사용 중

**원인**: 포트 5000이 이미 사용 중입니다.

**해결**:
```bash
# 다른 포트 사용
python coco_web_annotator.py ... --port 5001
```

### 4. 번역 기능이 작동하지 않음

**원인**: OpenAI API 키가 설정되지 않았거나 잘못되었습니다.

**해결**:
- `coco_web_annotator.py` 파일의 `OPENAI_API_KEY` 변수 확인
- API 키가 유효한지 확인
- `openai` 패키지가 설치되었는지 확인: `pip install openai`

### 5. 이미지가 로드되지 않음

**원인**: 이미지 파일 경로 문제 또는 파일이 없음.

**해결**:
- `mscoco/exo_images` 및 `mscoco/ego_images` 폴더에 이미지 파일이 있는지 확인
- 브라우저 콘솔(F12)에서 에러 메시지 확인

## 🛠️ 유틸리티 스크립트

`annotation_utils.py`는 어노테이션 파일 관리 유틸리티를 제공합니다:

```bash
# image_resolution 필드 추가
python annotation_utils.py add_resolution \
    --annotation_file ./mscoco/web_annotations_exo.json \
    --coco_json ./mscoco/instances_train2017.json

# bbox 형식 수정
python annotation_utils.py fix_bbox \
    --annotation_file ./mscoco/web_annotations_exo.json

# 이미지 정리 (exo/ego 폴더로 분리)
python annotation_utils.py organize_images \
    --exo_json ./mscoco/web_annotations_exo.json \
    --ego_json ./mscoco/web_annotations_ego.json \
    --mscoco_folder ./mscoco
```

## 📝 참고사항

- 어노테이션은 자동으로 저장되며, 페이지를 새로고침해도 마지막 작업 이미지로 돌아갑니다
- Exo 이미지는 `web_annotations_exo.json`에, Ego 이미지는 `web_annotations_ego.json`에 저장됩니다
- 같은 `image_id`가 이미 존재하면 덮어쓰기됩니다 (중복 방지)
- 번역 기능은 GPT-4o-mini를 사용하며, 이미지 분석을 통해 더 정확한 번역을 제공합니다

## 📄 라이선스

이 프로젝트는 ETRI에서 개발되었습니다.

