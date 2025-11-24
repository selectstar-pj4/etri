# Google Sheets 연동 설정 가이드

작업자들이 SAVE를 누르면 구글 스프레드시트에 실시간으로 저장되는 기능을 설정하는 방법입니다.

## 📋 사전 준비

1. Google 계정 (Gmail 계정)
2. Python 패키지 설치: `pip install gspread google-auth`

## 🎯 설정 방식

### ⭐ 권장 방식: 관리자 1명만 설정, 작업자는 파일만 받아서 사용

**작업자들은 Google Cloud Console 설정 없이, 관리자가 준 파일만 받아서 사용합니다!**

- ✅ **관리자**: Google Cloud Console에서 한 번만 설정
- ✅ **작업자**: 관리자로부터 받은 `credentials.json` 파일을 프로젝트 폴더에 넣고, `config.py`에 `WORKER_ID`만 설정하면 끝!
- ✅ **설정 시간**: 관리자 10분, 작업자 1분

## 🔧 설정 단계

### 👨‍💼 관리자 설정 (한 번만 수행, 약 10분 소요)

> 💡 **작업자들은 이 단계를 건너뛰고 아래 "작업자 설정"으로 바로 가세요!**

### 1단계: Google Cloud Console에서 프로젝트 생성 (관리자만)

1. [Google Cloud Console](https://console.cloud.google.com/) 접속
2. 상단 프로젝트 선택 → **새 프로젝트** 클릭
3. 프로젝트 이름 입력 (예: "COCO Annotation Tool")
4. **만들기** 클릭

### 2단계: Google Sheets API 활성화

1. 왼쪽 메뉴에서 **API 및 서비스** → **라이브러리** 클릭
2. 검색창에 "Google Sheets API" 입력
3. **Google Sheets API** 선택 → **사용 설정** 클릭

### 3단계: Service Account 생성

1. 왼쪽 메뉴에서 **API 및 서비스** → **사용자 인증 정보** 클릭
2. 상단 **+ 사용자 인증 정보 만들기** → **서비스 계정** 선택
3. 서비스 계정 이름 입력 (예: "coco-annotation-service")
4. **만들기** 클릭
5. 역할은 선택하지 않고 **완료** 클릭

### 4단계: JSON 키 다운로드 (관리자만)

1. 생성된 서비스 계정 클릭
2. **키** 탭 클릭
3. **키 추가** → **새 키 만들기** 선택
4. 키 유형: **JSON** 선택
5. **만들기** 클릭 → 자동으로 JSON 파일 다운로드됨
6. 다운로드된 JSON 파일을 프로젝트 폴더에 저장 (예: `credentials.json`)
   - ⚠️ **중요**: 이 파일은 절대 Git에 업로드하지 마세요! (`.gitignore`에 추가 권장)

### 4-1단계: JSON 파일 공유 (관리자 → 다른 작업자)

1. 다운로드한 `credentials.json` 파일을 **안전하게** 다른 작업자들에게 공유
   - 방법 1: 비밀번호로 보호된 ZIP 파일로 공유
   - 방법 2: 안전한 파일 공유 서비스 사용 (예: Google Drive 비공개 링크 + 비밀번호)
   - 방법 3: USB 등 오프라인 방식
   - 방법 4: 프로젝트 폴더에 직접 포함 (보안 주의, 내부 네트워크에서만 권장)
2. **스프레드시트 ID도 함께 공유** (5단계에서 얻은 ID)

### 5단계: 스프레드시트 생성 및 권한 부여 (관리자만)

1. [Google Sheets](https://sheets.google.com/) 접속
2. **빈 스프레드시트** 생성
3. 스프레드시트 이름 설정 (예: "COCO Annotation Results")
4. URL에서 스프레드시트 ID 복사
   - 예: `https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit`
   - `SPREADSHEET_ID` 부분이 스프레드시트 ID입니다
5. **공유** 버튼 클릭
6. 4단계에서 다운로드한 JSON 파일을 열어서 `client_email` 값을 복사
   - 예: `coco-annotation-service@your-project.iam.gserviceaccount.com`
7. 공유 창에 이 이메일 주소 입력
8. 권한: **편집자** 선택
9. **완료** 클릭
10. **스프레드시트 ID를 모든 작업자에게 공유** (예: 메시지, 이메일 등)

### 6단계: 관리자 config.py 설정

관리자도 `config.py`를 설정하세요:

```python
GOOGLE_SHEETS_SPREADSHEET_ID = "여기에_스프레드시트_ID_입력"
GOOGLE_SHEETS_CREDENTIALS_PATH = "./credentials.json"
WORKER_ID = "worker001"  # 관리자 자신의 ID
```

---

## 👷 작업자 설정 (매우 간단! 약 1분 소요)

> ✅ **작업자들은 Google Cloud Console 설정 없이 아래 3가지만 하면 됩니다!**

### 1단계: 파일 받기

관리자로부터 다음 2가지를 받으세요:
1. `credentials.json` 파일
2. 스프레드시트 ID (예: `1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p7q8r9s0t`)

### 2단계: 파일 배치

1. 받은 `credentials.json` 파일을 **프로젝트 폴더**에 저장
   - 예: `C:\Users\USER\Downloads\etri\credentials.json`

### 3단계: config.py 설정

`config.py` 파일을 열고 다음만 설정하세요:

```python
# 관리자가 공유한 스프레드시트 ID (모든 작업자가 동일)
GOOGLE_SHEETS_SPREADSHEET_ID = "1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p7q8r9s0t"

# credentials.json 파일 경로 (기본값 그대로 사용)
GOOGLE_SHEETS_CREDENTIALS_PATH = "./credentials.json"

# ⭐ 여기만 자신의 ID로 변경하세요!
WORKER_ID = "worker002"  # 자신의 고유 ID (예: "worker002", "worker003" 등)
```

**끝!** 이제 서버를 실행하면 바로 Google Sheets에 저장됩니다! 🎉

### 작업자 설정 체크리스트

- [ ] `credentials.json` 파일을 프로젝트 폴더에 저장했나요?
- [ ] `config.py`에 스프레드시트 ID를 입력했나요?
- [ ] `config.py`에서 `WORKER_ID`를 자신의 ID로 변경했나요?
- [ ] 서버를 실행했을 때 `[INFO] Google Sheets 연동 활성화` 메시지가 나오나요?

### 7단계: 서버 실행 및 확인

설정을 완료한 후 서버를 실행하세요:

```powershell
python coco_web_annotator.py --mscoco_folder ./mscoco --coco_json ./mscoco/instances_train2017.json --output_json ./mscoco/web_annotations.json
```

**서버 시작 시 다음과 같은 메시지가 보이면 성공입니다:**
```
[INFO] Google Sheets 연동 활성화: 1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p7q8r9s0t
[INFO] 작업자 ID: worker001
```

만약 다음과 같은 메시지가 보이면:
- `[WARN] Google Sheets credentials 파일을 찾을 수 없습니다` → `credentials.json` 파일이 프로젝트 폴더에 있는지 확인
- `[INFO] Google Sheets 연동 비활성화` → `config.py` 설정을 확인

## 📊 사용 방법

### 작업자 작업 흐름

1. `config.py`에서 `WORKER_ID`가 올바르게 설정되어 있는지 확인
2. 이미지에 대한 어노테이션 작성
3. **Save** 버튼 클릭
4. 자동으로 구글 스프레드시트에 저장됨
   - 작업자 ID는 `config.py`의 `WORKER_ID`에서 자동으로 가져옵니다

### 스프레드시트 구조

- 각 작업자마다 **별도의 시트**가 자동으로 생성됩니다
- 시트 이름 = 작업자 ID (예: `worker001`)
- 각 시트의 헤더:
  - 저장시간
  - Image ID
  - Image Path
  - Image Resolution
  - Question
  - Response
  - Rationale
  - View (exo/ego)
  - Bbox

### 검수자 작업 흐름

1. 구글 스프레드시트 열기
2. 각 작업자별 시트에서 작업 내용 확인
3. 실시간으로 업데이트되는 내용 검수
4. 필요시 댓글이나 별도 시트에 검수 결과 기록

## 🔍 문제 해결

### "Google Sheets 연동 비활성화" 메시지가 나오는 경우

1. `config.py`에 설정이 올바르게 되어 있는지 확인
2. `GOOGLE_SHEETS_CREDENTIALS_PATH`의 파일 경로가 올바른지 확인
3. JSON 키 파일이 존재하는지 확인

### "Google Sheets 초기화 실패" 메시지가 나오는 경우

1. JSON 키 파일 형식이 올바른지 확인
2. Google Sheets API가 활성화되어 있는지 확인
3. Service Account에 스프레드시트 편집 권한이 있는지 확인

### 저장은 되는데 스프레드시트에 안 나타나는 경우

1. 스프레드시트를 새로고침해보세요
2. 서버 콘솔에서 에러 메시지 확인
3. 작업자 ID가 올바르게 입력되었는지 확인

## 💡 팁

- **관리자**: 한 번만 설정하고 파일을 공유하면 됩니다
- **작업자**: 관리자로부터 `credentials.json`과 `GOOGLE_SHEETS_SPREADSHEET_ID`를 받아서 설정
- 작업자 ID는 일관되게 사용하세요 (예: `worker001`, `worker002`)
- 스프레드시트는 여러 검수자가 동시에 볼 수 있습니다
- 실시간 업데이트는 보통 1-2초 내에 반영됩니다
- 같은 Image ID로 저장하면 기존 행이 업데이트됩니다 (중복 방지)

## 🔒 보안 주의사항

- ⚠️ `credentials.json` 파일은 절대 Git에 업로드하지 마세요
- ⚠️ 스프레드시트는 필요한 사람에게만 공유하세요
- ⚠️ Service Account 키는 안전하게 보관하세요
- ⚠️ `credentials.json` 파일을 공유할 때는 비밀번호 보호나 안전한 채널을 사용하세요
- ⚠️ 공유 후에는 불필요한 사람에게는 접근 권한을 제거하세요

## 📝 요약

### 관리자 (1명)
1. Google Cloud Console에서 프로젝트 생성 (1-3단계)
2. Service Account JSON 키 다운로드 (4단계)
3. 스프레드시트 생성 및 권한 부여 (5단계)
4. `credentials.json`과 스프레드시트 ID를 작업자들에게 공유
5. 자신의 `config.py` 설정 (6단계)

### 작업자 (여러 명)
1. 관리자로부터 `credentials.json` 파일 받기
2. 파일을 프로젝트 폴더에 저장
3. `config.py`에 스프레드시트 ID 입력 및 `WORKER_ID`만 변경
4. 서버 실행 → 끝! 🎉

**작업자들은 Google Cloud Console 설정이 전혀 필요 없습니다!**

