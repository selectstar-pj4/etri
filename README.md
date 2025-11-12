# ETRI VQA Annotation Tool

MS-COCO 데이터셋 이미지를 사용하여 Visual Question Answering (VQA) 어노테이션을 생성하는 웹 기반 도구입니다.

## 📋 목차

- [시작하기](#시작하기)
- [Git 워크플로우](#git-워크플로우)
- [VSCode 환경 설정](#vscode-환경-설정)
- [프로젝트 초기 설정](#프로젝트-초기-설정)
- [서버 실행 방법](#서버-실행-방법)
- [작업 방법](#작업-방법)
- [툴 사용법](#툴-사용법)
- [프로젝트 구조](#프로젝트-구조)
- [주요 기능](#주요-기능)
- [문제 해결](#문제-해결)

## 🚀 시작하기

### 1. VSCode 설치

1. [VSCode 공식 웹사이트](https://code.visualstudio.com/)에서 다운로드
2. 설치 후 실행

### 2. Python 설치

**Python이 설치되어 있는지 확인:**
```powershell
# PowerShell 또는 명령 프롬프트에서 실행
python --version
```

**설치되어 있지 않은 경우:**

1. [Python 공식 웹사이트](https://www.python.org/downloads/)에서 Python 3.8 이상 다운로드
2. **⚠️ 매우 중요: 설치 시 "Add Python to PATH" 옵션을 반드시 체크하세요!**
   - 이 옵션을 체크하지 않으면 터미널에서 `python` 명령어를 사용할 수 없습니다
   - 설치 화면에서 "Add Python to PATH" 체크박스를 반드시 선택해야 합니다
3. 설치 완료 후 터미널을 완전히 종료하고 다시 열기
4. 다시 확인:
   ```powershell
   python --version
   ```

**"Add Python to PATH"를 체크하지 않았거나 이미 설치한 경우:**
- Python을 다시 설치하거나
- 수동으로 PATH 환경 변수에 Python 경로를 추가해야 합니다

### 3. Git 설치

**Git이 설치되어 있는지 확인:**
```powershell
git --version
```

**설치되어 있지 않은 경우:**

1. [Git 공식 웹사이트](https://git-scm.com/download/win)에서 Windows용 Git 다운로드
2. 다운로드한 설치 파일 실행
3. 설치 과정에서 기본 설정을 사용하면 됩니다 (Next 버튼 클릭)
4. 설치 완료 후 터미널을 다시 열고 확인:
   ```powershell
   git --version
   ```

**참고:** Git은 프로젝트 코드를 다운로드하고 업데이트하는 데 필요합니다.

### 4. VSCode Python 확장 설치

1. VSCode 실행
2. 확장 프로그램 아이콘 클릭 (왼쪽 사이드바) 또는 `Ctrl+Shift+X`
3. 검색창에 "Python" 입력
4. Microsoft의 "Python" 확장 프로그램 설치
5. (선택) "Pylance" 확장 프로그램도 설치 권장

## 🔄 Git 워크플로우

### 처음 시작하기 (Git Clone)

1. **프로젝트 클론**
   ```powershell
   git clone [저장소 URL]
   cd etri
   ```

2. **config.py 파일 생성**
   - `config.txt` 파일을 복사하여 `config.py`로 이름 변경
   - `OPENAI_API_KEY`에 본인의 API 키 입력

3. **필수 패키지 설치** (아래 "프로젝트 초기 설정" 섹션 참고)

### 코드 업데이트 받기 (Git Pull)

코드를 업데이트할 때마다 다음 절차를 따르세요:

#### ⚠️ 중요: Git Pull 전 확인 사항

**다음 파일들은 Git에 포함되지 않으므로, `git pull` 전에 백업하거나 확인해야 합니다:**

1. **작업 파일 (백업 권장)**
   - `mscoco/web_annotations_exo.json` - 작업한 어노테이션 파일
   - `mscoco/web_annotations_ego.json` - 작업한 어노테이션 파일

2. **설정 파일 (덮어쓰기 주의)**
   - `config.py` - API 키 및 설정 (덮어쓰이지 않지만 확인 권장)

3. **기타 파일**
   - `*.csv` - 통계 파일들
   - `credentials.json` - Google Drive 인증 파일 (사용하는 경우)

#### Git Pull 실행 방법

```powershell
# 1. 현재 작업 상태 확인
git status

# 2. 작업 파일 백업 (선택사항)
# 필요시 수동으로 백업하거나, Git이 자동으로 처리합니다

# 3. 원격 저장소에서 최신 코드 가져오기
git pull

# 4. 충돌이 발생한 경우
# - Git이 자동으로 병합을 시도합니다
# - 충돌이 발생하면 파일을 확인하고 수동으로 해결해야 합니다
```

#### 충돌 발생 시 해결 방법

만약 `git pull` 시 충돌이 발생하면:

1. **충돌 파일 확인**
   ```powershell
   git status
   ```

2. **충돌 파일 수동 해결**
   - VSCode에서 충돌 파일 열기
   - `<<<<<<<`, `=======`, `>>>>>>>` 표시 확인
   - 필요한 내용만 남기고 충돌 마커 제거

3. **해결 완료 후 커밋**
   ```powershell
   git add [충돌 해결한 파일]
   git commit -m "Merge conflict resolved"
   ```

**참고:**
- 작업 파일들(`web_annotations_exo.json` 등)은 `.gitignore`에 포함되어 있어 Git에 업로드되지 않습니다
- 따라서 일반적으로 `git pull` 시 충돌이 발생하지 않습니다
- 하지만 코드 파일(`.py`, `.html` 등)이 수정된 경우 충돌이 발생할 수 있습니다

## 🔧 VSCode 환경 설정

### 1. 프로젝트 폴더 열기

1. VSCode 실행
2. `File` → `Open Folder...` (또는 `Ctrl+K, Ctrl+O`)
3. 프로젝트 폴더 선택 (`etri` 폴더)

### 2. Python 인터프리터 선택

1. `Ctrl+Shift+P` (명령 팔레트 열기)
2. "Python: Select Interpreter" 입력
3. 설치된 Python 버전 선택 (Python 3.8 이상 권장)

### 3. 터미널 열기

- `Ctrl+`` (백틱) 또는 `Terminal` → `New Terminal`
- 기본 터미널이 PowerShell 또는 Command Prompt로 열립니다

## 📦 프로젝트 초기 설정

### 1. 필수 패키지 설치

프로젝트 폴더에서 터미널을 열고 다음 명령 실행:

```powershell
pip install flask pillow pycocotools openai
```

**설치 확인:**
```powershell
pip list
```

다음 패키지들이 보여야 합니다:
- flask
- pillow
- pycocotools
- openai

### 2. API 키 설정

1. 프로젝트 루트에 `config.py` 파일 생성 (없는 경우)
2. 다음 내용 입력:

**방법 1: config.txt 사용 (권장)**

1. 프로젝트 루트에 있는 `config.txt` 파일을 열기
2. `config.txt` 파일을 복사하여 `config.py`로 이름 변경
3. `OPENAI_API_KEY`에 본인의 API 키 입력

**방법 2: 직접 생성**

1. 프로젝트 루트에 `config.py` 파일 생성
2. `config.txt` 파일의 내용을 참고하여 작성

**⚠️ 중요:** `config.py`는 Git에 업로드되지 않습니다 (`.gitignore`에 포함됨)

### 3. 작업자 데이터 공유 저장소 설정 (여러 컴퓨터 사용 시)

여러 컴퓨터에서 같은 레포지토리를 사용하고 **실시간으로 작업자 데이터를 동기화**하려면 공유 저장소를 설정해야 합니다.

**현재 상황:**
- 각 컴퓨터가 독립 서버를 실행하면, 각각의 로컬 파일(`workers.json` 등)을 사용
- 한 컴퓨터에서 작업자를 삭제해도 다른 컴퓨터에는 반영되지 않음

**해결 방법:**

### 방법 1: Windows 공유 폴더 (같은 와이파이 권장)

같은 와이파이에 연결된 컴퓨터들이 있다면 Windows 공유 폴더를 사용하는 것이 가장 빠르고 안정적입니다.

**설정 절차:**

1. **서버 컴퓨터에서 폴더 공유**
   - 공유할 폴더 생성 (예: `C:\shared\worker_data`)
   - 폴더 우클릭 → 속성 → 공유 탭 → 공유(S)... → Everyone 추가 (읽기/쓰기 권한)
   - 컴퓨터 이름 또는 IP 주소 확인

2. **모든 컴퓨터에서 config.py 설정**
   ```python
   # 컴퓨터 이름 사용
   WORKER_DATA_DIR = r"\\컴퓨터이름\worker_data"
   
   # 또는 IP 주소 사용 (더 안정적)
   WORKER_DATA_DIR = r"\\192.168.1.100\worker_data"
   ```

3. **자세한 설정 방법**
   - `SHARED_FOLDER_SETUP.md` 파일 참고

**장점:**
- 매우 빠름 (같은 네트워크 내)
- 안정적
- 인터넷 불필요
- 거의 실시간 동기화

**단점:**
- 서버 컴퓨터가 항상 켜져 있어야 함

### 방법 2: 클라우드 동기화 (다른 네트워크/원격 작업)

다른 와이파이에 있거나 원격으로 작업하는 경우 OneDrive/Google Drive를 사용할 수 있습니다.

**설정:**
```python
# OneDrive 사용 (Windows 기본 제공)
WORKER_DATA_DIR = r"C:\Users\사용자명\OneDrive\worker_data"

# Google Drive 사용
WORKER_DATA_DIR = r"C:\Users\사용자명\Google Drive\worker_data"
```

**장점:**
- 인터넷만 있으면 어디서든 접근
- 자동 동기화
- 별도 서버 불필요

**단점:**
- 동기화 지연 가능 (보통 몇 초~1분)

### 방법 3: 로컬 사용 (기본값)

```python
WORKER_DATA_DIR = None  # 각 컴퓨터가 독립적으로 동작
```

**⚠️ 중요:**
- 공유 저장소를 사용하면 모든 컴퓨터에서 같은 파일을 읽고 씁니다
- 한 컴퓨터에서 작업을 완료하면 다른 컴퓨터의 관리자 페이지에서도 실시간으로 반영됩니다 (5초 이내)
- 공유 저장소가 없으면 각 컴퓨터가 독립적으로 동작합니다

### 3. 필요한 파일 및 폴더 준비

프로젝트를 사용하려면 다음 파일/폴더가 필요합니다:

```
mscoco/
├── instances_train2017.json      # COCO 어노테이션 파일 (약 448MB)
├── filtered_annotations.json     # 필터링된 어노테이션 파일 (약 54MB, 선택사항)
├── exo_images/                   # Exo 이미지 폴더
│   └── *.jpg                     # 이미지 파일들
├── ego_images/                   # Ego 이미지 폴더 (선택사항)
│   └── *.jpg                     # 이미지 파일들
```

**참고:** 이 파일들은 용량이 크거나 Git에 포함되지 않으므로 로컬에서 직접 준비해야 합니다.

## 🖥️ 서버 실행 방법

### 기본 실행

```powershell
python coco_web_annotator.py --mscoco_folder ./mscoco --coco_json ./mscoco/instances_train2017.json --output_json ./mscoco/web_annotations.json
```

### 커스텀 설정

```powershell
python coco_web_annotator.py `
    --mscoco_folder ./mscoco `
    --coco_json ./mscoco/instances_train2017.json `
    --output_json ./mscoco/web_annotations.json `
    --categories_json ./mscoco/categories.json `
    --host 0.0.0.0 `
    --port 5000
```

### 실행 옵션 설명

- `--mscoco_folder`: 이미지 폴더가 있는 루트 경로 (기본값: `./mscoco`)
- `--coco_json`: COCO 어노테이션 JSON 파일 경로 (필수)
- `--output_json`: 저장할 어노테이션 파일 이름 (필수, 실제로는 `_exo.json`, `_ego.json` 두 파일 생성)
- `--test_folder`: 특정 폴더의 이미지만 로드 (선택사항)
- `--categories_json`: 커스텀 카테고리 매핑 파일 (선택사항)
- `--host`: 서버 호스트 (기본값: `0.0.0.0`)
- `--port`: 서버 포트 (기본값: `5000`)

### 웹 인터페이스 접속

서버가 실행되면 브라우저에서 접속:

- **로컬**: `http://localhost:5000` (또는 실행한 포트)
- **원격 서버**: `http://[서버IP]:5000`

## 📝 작업 방법

### 1. 기본 워크플로우

#### Step 1: 이미지 로드

- **Previous/Next 버튼**: 이전/다음 이미지로 이동
- **Ctrl+Left/Right Arrow**: 키보드로 이동
- **Image ID 검색**: Image ID 입력 후 "Go" 버튼 클릭


#### Step 2: 한글 입력 (왼쪽 패널)

1. **Question (한글)** 텍스트 영역
   - 질문 입력 (예: "테이블 오른쪽에 있는 원형 또는 원통형의 객체")
   - 질문은 반드시 "~객체"로 끝나야 합니다 ("는?", "는 무엇인가요?" 사용 금지)

2. **Choices (객관식 선지)**
   - `(a)`: 첫 번째 선택지 입력
   - `(b)`: 두 번째 선택지 입력
   - `(c)`: 세 번째 선택지 입력
   - `(d)`: 네 번째 선택지 입력
   - 정답 선택: 라디오 버튼 클릭

3. **Rationale (한글)** 텍스트 영역
   - 정답의 근거 입력 (예: "a는 ATT 조건 불만족, b는 POS 조건 불만족, c는 REL 조건 불만족, d는 모든 조건 만족")

#### Step 3: Bounding Box 선택

1. **기존 bbox 선택**
   - 이미지에서 빨간색 박스 클릭하여 선택/해제
   - 선택된 bbox는 파란색으로 표시됩니다

2. **직접 bbox 그리기**
   - "Draw Bbox" 버튼 클릭
   - 이미지에서 드래그하여 bbox 그리기
   - 그린 bbox는 자동으로 선택됩니다

3. **bbox 관리**
   - `Ctrl+X`: bbox 표시/숨김 토글
   - `Delete`: 선택된 bbox 삭제

#### Step 4: View 타입 선택

- **Exo** 또는 **Ego** 라디오 버튼 선택
- 이미지 폴더에 따라 자동 설정됩니다

#### Step 5: 저장

- **Ctrl+S**: 수동 저장
- **자동 저장**: 30초마다 자동 저장
- 페이지 종료 시에도 자동 저장

### 3. 번역 기능 사용

**QA 자동 생성:**
- 필요시 수동으로 질문을 입력할 수 있습니다

**수동 번역:**
- Choices 아래 "번역" 버튼: 질문과 선택지를 영어로 번역
- Rationale (한글) 아래 "번역" 버튼: 근거를 영어로 번역

## 🛠️ 툴 사용법

## 📁 프로젝트 구조

```
etri_annotation_tool/
├── coco_web_annotator.py      # Flask 웹 애플리케이션 (메인)
├── config.py                  # 설정 파일 (API 키 등)
├── config.txt                 # config.py 생성용 템플릿 파일
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
```

## ✨ 주요 기능

### 1. 이중 패널 인터페이스
- **왼쪽 패널**: 한글 입력 (Question, Choices, Rationale)
- **오른쪽 패널**: 영어 출력 및 어노테이션 관리

### 2. GPT-4o 기반 번역
- 한글 질문과 선택지를 영어로 자동 번역
- 태그 자동 생성: `<ATT>`, `<POS>`, `<REL>` 태그 자동 포함
- 한글 근거를 영어로 자동 번역

### 4. Exo/Ego 분리
- 이미지를 `exo_images`와 `ego_images` 폴더로 자동 분류
- 각각 별도의 JSON 파일로 저장

### 5. Bounding Box 관리
- 인터랙티브한 bbox 선택/해제
- 직접 bbox 그리기 기능
- 시각적 피드백 제공
- 단축키로 빠른 토글

### 6. 자동 저장
- 30초마다 자동 저장
- 페이지 종료 시 자동 저장
- 마지막 작업 이미지 기억

## ⌨️ 키보드 단축키

### 기본 단축키
- `Ctrl+S`: 현재 어노테이션 저장
- `Ctrl+Left Arrow`: 이전 이미지
- `Ctrl+Right Arrow`: 다음 이미지

### Bbox 관리
- `Ctrl+X`: Bbox 표시/숨김 토글
- `Delete`: 선택된 bbox 삭제

### 객관식 선택
- `Ctrl+1`: 선택지 (a) 선택
- `Ctrl+2`: 선택지 (b) 선택
- `Ctrl+3`: 선택지 (c) 선택
- `Ctrl+4`: 선택지 (d) 선택

**참고**: textarea나 input에 포커스가 있을 때는 단축키가 작동하지 않습니다.

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

## 🔧 문제 해결

### 1. "COCO JSON file not found" 오류

**원인**: COCO 어노테이션 파일 경로가 잘못되었습니다.

**해결**:
```powershell
# 올바른 경로로 실행
python coco_web_annotator.py --coco_json ./mscoco/instances_train2017.json ...
```

### 2. "Image folder not found" 오류

**원인**: 이미지 폴더 경로가 잘못되었습니다.

**해결**:
- `mscoco` 폴더에 `exo_images`와 `ego_images` 폴더가 있는지 확인
- `--mscoco_folder` 옵션에 올바른 경로 지정

### 3. 포트가 이미 사용 중

**원인**: 포트가 이미 사용 중입니다.

**해결**:
```powershell
# 다른 포트 사용
python coco_web_annotator.py ... --port 5001
```

### 4. 번역 기능이 작동하지 않음

**원인**: OpenAI API 키가 설정되지 않았거나 잘못되었습니다.

**해결**:
- `config.py` 파일의 `OPENAI_API_KEY` 확인
- API 키가 유효한지 확인
- `openai` 패키지가 설치되었는지 확인: `pip install openai`

### 5. 이미지가 로드되지 않음

**원인**: 이미지 파일 경로 문제 또는 파일이 없음.

**해결**:
- `mscoco/exo_images` 및 `mscoco/ego_images` 폴더에 이미지 파일이 있는지 확인
- 브라우저 콘솔(F12)에서 에러 메시지 확인

### 6. Rate Limit 오류

**원인**: OpenAI API 사용량 제한 초과.

**해결**:
- 스크립트가 자동으로 재시도합니다 (최대 5회)
- `--parallel` 옵션 값을 줄여서 실행 (예: `--parallel 3`)
- 잠시 기다린 후 다시 시도


## 📝 참고사항

- 어노테이션은 자동으로 저장되며, 페이지를 새로고침해도 마지막 작업 이미지로 돌아갑니다
- Exo 이미지는 `web_annotations_exo.json`에, Ego 이미지는 `web_annotations_ego.json`에 저장됩니다
- 같은 `image_id`가 이미 존재하면 덮어쓰기됩니다 (중복 방지)
- 번역 기능은 GPT-4o를 사용하며, 이미지 분석을 통해 더 정확한 번역을 제공합니다

## 📄 라이선스

이 프로젝트는 ETRI에서 개발되었습니다.
