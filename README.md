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

### 0. 권장 환경 설정

```bash
# (선택) 가상환경 생성
python -m venv .venv

# 가상환경 활성화
# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate
```

가상환경을 사용하면 패키지 버전 충돌을 방지하고 OpenAI SDK를 안전하게 관리할 수 있습니다.

### 1. 필수 패키지 설치

```bash
pip install flask pillow pycocotools openai
```


### 2. API 키 설정

`config.py` 파일을 열어 OpenAI API 키를 입력하세요:

```python
# OpenAI API Key (필수)
OPENAI_API_KEY = "your-openai-api-key-here"

# 기본 모델 선택: "openai" (현재 OpenAI만 지원)
DEFAULT_MODEL = "openai"

# 관리자 설정
ADMIN_NAMES = ["전요한", "홍지우", "박남준"]  # 관리자 이름 목록
ADMIN_PASSWORD = "admin2025"  # 관리자 비밀번호 (변경 권장)

# Google Drive 설정 (선택사항)
GOOGLE_DRIVE_FOLDER_ID = "your-folder-id"  # Google Drive 폴더 ID
GOOGLE_CREDENTIALS_PATH = "credentials.json"  # 서비스 계정 JSON 키 파일 경로
```

**참고**: 
- `config.py`는 `.gitignore`에 포함되어 Git에 업로드되지 않습니다.
- OpenAI API 키는 필수입니다.
- Google Drive 연동은 선택사항입니다.

### 3. 서버 실행

기본 설정으로 실행:

```bash
python coco_web_annotator.py --mscoco_folder ./mscoco --coco_json ./mscoco/instances_train2017.json --output_json ./mscoco/web_annotations.json
```

> **주의**: `--output_json` 인자는 필수이며, 지정한 파일명을 기준으로 `_exo.json`, `_ego.json` 두 개의 결과 파일이 생성됩니다.

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

### 🔧 실행 옵션 요약

`coco_web_annotator.py`는 다음과 같은 CLI 옵션을 제공합니다:

- `--mscoco_folder` (기본값 `./mscoco`): `exo_images`, `ego_images` 폴더가 포함된 루트 경로
- `--coco_json` (기본값 `/Data/MSCOCO/annotations/instances_train2017.json`): COCO 어노테이션 JSON 경로
- `--output_json` (**필수**): 저장할 어노테이션 파일 이름 (실제로는 `_exo.json`, `_ego.json` 두 파일 생성)
- `--categories_json`: `{ "id": <int>, "name": <str> }` 형식의 커스텀 카테고리 매핑 파일
- `--host`, `--port`: Flask 서버 바인딩 설정 (기본값 `0.0.0.0:5000`)

출력 경로의 상위 폴더가 존재하지 않으면 자동으로 생성되며, 템플릿 폴더에 `index.html`이 없을 경우 최초 실행 시 자동 생성합니다.

## 📁 프로젝트 구조

```
etri_annotation_tool/
├── coco_web_annotator.py      # Flask 웹 애플리케이션 (메인)
├── annotation_utils.py         # 어노테이션 유틸리티 스크립트
├── worker_management.py      # 작업자 관리 시스템 (API 라우트 포함)
├── batch_process_with_save.py # 배치 처리 스크립트
├── assign_images_to_workers.py # 이미지 할당 유틸리티
├── config.py                  # 설정 파일 (API 키, 관리자 정보 등)
├── exo_data_sample.json       # 출력 형식 샘플
├── README.md                  # 이 파일
├── mscoco/
│   ├── exo_images/            # Exo 이미지 폴더
│   ├── ego_images/            # Ego 이미지 폴더
│   ├── instances_train2017.json  # COCO 어노테이션 파일
│   ├── web_annotations_exo.json   # Exo 어노테이션 출력 파일
│   └── web_annotations_ego.json  # Ego 어노테이션 출력 파일
└── templates/
    ├── index.html             # 웹 인터페이스 템플릿
    └── admin.html             # 관리자 페이지 템플릿
```

`templates/index.html` 파일은 서버 최초 실행 시 자동으로 생성됩니다. 커스터마이징한 템플릿을 덮어쓰지 않으려면 생성된 파일을 버전 관리하거나 별도로 백업해 두세요.

### ⚠️ 중요: 로컬에 필요한 파일 구성

다음 파일들은 용량이 크거나 Git에 포함되지 않으므로, **로컬 환경에서 직접 구성해야 합니다**:

- `mscoco/instances_train2017.json` - COCO 어노테이션 파일 (약 448MB)
- `mscoco/filtered_annotations.json` - 필터링된 어노테이션 파일 (약 54MB)
- `mscoco/exo_images/` - Exo 이미지 폴더
- `mscoco/ego_images/` - Ego 이미지 폴더

이 파일들은 `.gitignore`에 포함되어 있어 Git 저장소에는 업로드되지 않습니다. 프로젝트를 사용하려면 해당 경로에 위 파일들을 직접 구성해야 합니다.

## 🔑 OpenAI 연동 가이드

- `openai` 패키지를 설치하지 않아도 UI와 수동 작성 기능은 사용 가능하지만, **자동 번역 / 이미지 분석 / 검수** 기능을 사용하려면 OpenAI SDK와 API 키가 필요합니다.
- API 키 설정 방법:
  - `config.py.example`를 복사하여 `config.py`로 저장하고 `OPENAI_API_KEY` 값을 입력 (권장)
  - 또는 운영체제 환경 변수 `OPENAI_API_KEY`를 설정 (`PowerShell` 예: `setx OPENAI_API_KEY "sk-..."`)
- 키가 없거나 잘못되면 관련 API는 500 에러를 반환하며, 브라우저 알림과 서버 로그에서 오류 메시지가 출력됩니다.
- 이미지 분석 결과는 메모리 캐시(`image_analysis_cache`)에 저장되어 같은 이미지를 반복 분석하지 않습니다. 서버를 재시작하면 캐시가 초기화됩니다.

## ✨ 주요 기능

### 1. 이중 패널 인터페이스
- **왼쪽 패널**: 한글 입력 (Question, Choices, Rationale)
- **오른쪽 패널**: 영어 출력 및 어노테이션 관리

### 2. 자동 번역 및 QA 생성 기능
- **GPT-4o 기반 이미지 분석**: 이미지 분석 후 QA 자동 생성
- **GPT-4o 기반 번역**: 한글 질문과 선택지를 영어로 자동 번역
- **태그 자동 생성**: `<ATT>`, `<POS>`, `<REL>` 태그 자동 포함
- **한글 근거 자동 생성**: 선택된 답안을 바탕으로 한글 근거 자동 생성
- **단일 이미지 QA 생성**: 이미지를 불러와서 질문과 답변을 자동으로 생성

### 3. Exo/Ego 분리
- 이미지를 `exo_images`와 `ego_images` 폴더로 자동 분류
- 각각 별도의 JSON 파일로 저장 (`web_annotations_exo.json`, `web_annotations_ego.json`)

### 4. Bounding Box 관리
- 인터랙티브한 bbox 선택/해제
- **직접 bbox 그리기**: Draw Bbox 모드로 이미지에 직접 bbox 그리기
- 시각적 피드백 제공
- 단축키로 빠른 토글 (Ctrl+X)
- Delete 키로 선택된 bbox 삭제

### 5. 자동 저장
- 30초마다 자동 저장
- 페이지 종료 시 자동 저장
- 마지막 작업 이미지 기억

### 6. 작업자 관리 시스템
- **작업자 로그인**: 작업자 ID와 이름으로 로그인
- **이미지 할당**: 관리자가 작업자에게 이미지 할당
- **진행 상황 추적**: 작업 완료 자동 체크 및 진행률 표시
- **통계 및 리포트**: 작업자별 시간당/일일 작업 통계 생성
- **Google Drive 연동**: 스프레드시트 자동 업로드 (선택사항)

### 7. 관리자 기능
- **관리자 로그인**: 관리자 이름과 비밀번호로 로그인
- **관리자 페이지**: 웹 UI에서 모든 작업 관리
  - 작업자 목록 조회
  - 이미지 할당 (직접 입력, 범위 지정, 파일 업로드)
  - 작업 진행 상황 모니터링
  - 통계 및 리포트 생성
- **보안**: 작업자에게는 관리자 기능 숨김

## 📖 사용법

### 기본 워크플로우

1. **한글 입력** (왼쪽 패널)
   - Question (한글) 텍스트 영역에 질문 입력
   - Choices (객관식 선지)에 a, b, c, d 선택지 입력
   - 정답 선택 (라디오 버튼)
   - Rationale (한글) 텍스트 영역에 근거 입력

2. **QA 자동 생성 또는 번역 실행**
   - **QA 자동 생성**: "질문 자동 생성" 버튼 클릭
     - 이미지 분석 (GPT-4o) → 질문/선택지 자동 생성 → 한글 근거 자동 생성
   - **수동 번역**: Choices 아래 "번역" 버튼 클릭
     - 이미지 분석 → 질문/선택지 번역 → 한글 근거 자동 생성
   - Rationale (한글) 아래 "번역" 버튼 클릭하여 영어 근거 생성

3. **Bounding Box 선택 및 직접 그리기**
   - 이미지에서 bbox 클릭하여 선택/해제
   - **Draw Bbox 모드**: "Draw Bbox" 버튼 클릭 후 이미지에서 드래그하여 직접 bbox 그리기
   - Ctrl+X로 bbox 표시/숨김 토글
   - Delete 키로 선택된 bbox 삭제

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

`annotation_utils.py`는 어노테이션 파일 관리 유틸리티를 제공합니다. 각 명령은 실행 전 대상 JSON을 자동으로 백업(`.backup`, `.backup2`)하므로, 문제가 생기면 백업 파일로 복원할 수 있습니다.

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

## 🌐 내부 API 엔드포인트 요약

프론트엔드와 외부 스크립트에서 활용 가능한 주요 REST API는 다음과 같습니다:

- `GET /api/image/<index>`: 인덱스에 해당하는 이미지 정보와 기존 어노테이션 반환
- `GET /api/analyze_image/<index>`: OpenAI 비전 모델을 이용한 이미지 분석 (캐시 적용)
- `POST /api/translate/question`, `/api/translate/choices`, `/api/translate/question_and_choices`: 한글 질문·선택지를 영어 포맷으로 변환
- `POST /api/translate/rationale`: 한글 근거를 영어 소거법 형식으로 변환
- `POST /api/review_translation`: 번역문 문법 검수 및 수정 제안
- `POST /api/save`: bbox와 번역 결과를 저장 (작업자 ID 포함)
- `GET /api/find/<image_id>`: COCO `image_id`로 데이터셋 인덱스 조회
- `POST /api/admin/login`: 관리자 로그인 인증
- `GET /api/workers`: 작업자 목록 조회
- `POST /api/workers`: 작업자 추가
- `POST /api/workers/<worker_id>/assign`: 작업자에게 이미지 할당
- `GET /api/workers/<worker_id>/progress`: 작업자 진행 상황 조회
- `GET /api/workers/<worker_id>/stats`: 작업자 통계 조회
- `GET /api/workers/export`: 통계 스프레드시트 내보내기

번역·분석 관련 엔드포인트는 OpenAI API 키가 설정되지 않으면 500 에러를 반환하므로, 자동화 시 예외 처리가 필요합니다.

## 👥 작업자 관리 시스템

### 작업자 로그인
1. 웹 인터페이스 접속 시 로그인 화면 표시
2. 작업자 ID와 이름 입력
3. 로그인 후 작업 시작 (자동으로 작업 완료 추적)

### 관리자 로그인
1. 로그인 화면에서 "관리자" 탭 클릭
2. 관리자 이름 입력 (전요한, 홍지우, 박남준)
3. 비밀번호 입력 (`config.py`에서 설정)
4. 관리자 페이지로 자동 이동

### 관리자 페이지 기능
- **작업자 목록**: 등록된 모든 작업자 확인
- **이미지 할당**: 
  - 직접 입력: 쉼표로 구분된 이미지 ID
  - 범위 지정: 시작 ID ~ 끝 ID
  - 파일 업로드: 텍스트 파일에서 이미지 ID 읽기
- **진행 상황 모니터링**: 실시간 작업 진행률 확인
- **통계 및 리포트**: 
  - 일일 요약 리포트
  - 상세 통계 (시간당 작업량)
  - Google Drive 자동 업로드 (설정 시)

### Google Drive 연동 설정
`config.py`에 다음 설정 추가:
```python
GOOGLE_DRIVE_FOLDER_ID = "your-folder-id"
GOOGLE_CREDENTIALS_PATH = "credentials.json"
```

Google Drive API 설정 방법:
1. Google Cloud Console에서 서비스 계정 생성
2. JSON 키 파일 다운로드
3. `credentials.json`으로 저장
4. Google Drive 폴더 ID 설정

## 🔄 배치 처리

대량 이미지에 대한 QA 자동 생성이 가능합니다:

```bash
# 배치 처리 스크립트 실행
python batch_process_with_save.py \
    --start_index 0 \
    --end_index 100 \
    --parallel 3 \
    --output_json ./mscoco/web_annotations.json
```

옵션:
- `--start_index`: 시작 인덱스
- `--end_index`: 끝 인덱스
- `--parallel`: 병렬 처리 수 (기본값: 3)
- `--output_json`: 출력 JSON 파일 경로

## 📝 참고사항

- 어노테이션은 자동으로 저장되며, 페이지를 새로고침해도 마지막 작업 이미지로 돌아갑니다
- Exo 이미지는 `web_annotations_exo.json`에, Ego 이미지는 `web_annotations_ego.json`에 저장됩니다
- 같은 `image_id`가 이미 존재하면 덮어쓰기됩니다 (중복 방지)
- 번역 기능은 GPT-4o-mini를 사용하며, 이미지 분석을 통해 더 정확한 번역을 제공합니다
- 작업자 관리 시스템은 `worker_management.py`를 통해 동작하며, 작업자 정보는 `workers.json`에 저장됩니다
- 관리자 비밀번호는 `config.py`의 `ADMIN_PASSWORD`에서 변경할 수 있습니다

## 📄 라이선스

이 프로젝트는 ETRI에서 개발되었습니다.

