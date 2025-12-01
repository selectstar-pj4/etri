# 상태별 필터링 및 그룹화 구현 가이드

## 개요
작업자들이 500장의 이미지를 상태별로 필터링하고 그룹화하여 효율적으로 작업할 수 있도록 하는 기능입니다.

## 구현 단계

### 1단계: Google Sheets에 '할당시간' 필드 추가 (선택사항)

**목적**: 미작업 항목을 "오래된 순"으로 정렬하기 위해

**방법**:
1. Google Sheets에서 작업자별 시트 열기
2. 헤더 행에 '할당시간' 열 추가 (예: J열)
3. 이미지 할당 시 자동으로 기록되도록 백엔드 수정 필요

**백엔드 수정** (`coco_web_annotator.py`의 `save_to_google_sheets` 함수):
```python
# 할당시간 추가 (이미지가 처음 할당될 때만 기록)
if not row_to_update:  # 새 행인 경우
    할당시간 = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
else:
    # 기존 행이면 할당시간 유지
    할당시간 = row_data.get('할당시간', '') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')

row_data = [
    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),  # 저장시간
    할당시간,  # 할당시간 추가
    annotation.get('image_id', ''),
    # ... 나머지 필드
]
```

### 2단계: 백엔드 API 추가

**파일**: `coco_web_annotator.py`

**추가할 API**:
1. `/api/images_by_status` - 상태별 이미지 리스트 조회
2. `/api/work_statistics` - 작업 통계 조회

**위치**: `status_filter_api_example.py` 파일의 내용을 `coco_web_annotator.py`에 복사하여 추가

**주의사항**:
- `annotator` 객체가 접근 가능한 위치에 추가
- `read_from_google_sheets` 함수가 이미 존재하는지 확인
- Google Sheets 헤더 이름이 일치하는지 확인 ('검수', '저장시간', '할당시간' 등)

### 3단계: 프론트엔드 UI 추가

**파일**: `templates/index.html`

**추가 위치**: `<h1>` 태그 아래, 기존 컨테이너 위

**추가할 코드**: `status_filter_ui_example.html` 파일의 내용 참고

**주요 기능**:
1. 상태별 탭 (전체, 미작업, 불통, 통과, 납품완료, 완료)
2. 각 탭에 개수 표시
3. 정렬 옵션 (오래된 순, 최신 순, 이미지 ID 순)
4. 진행률 바
5. 통계 표시

### 4단계: 기존 네비게이션 로직 수정

**파일**: `templates/index.html`

**수정할 함수**:
- `nextImage()` - 필터링된 리스트 기준으로 다음 이미지로 이동
- `prevImage()` - 필터링된 리스트 기준으로 이전 이미지로 이동

**수정 예시**:
```javascript
function nextImage() {
    if (currentStatusFilter === 'all') {
        // 기존 로직
        if (currentIndex < filteredImageIds.length - 1) {
            loadImage(currentIndex + 1);
        }
    } else {
        // 필터링된 리스트 기준으로 이동
        navigateFilteredImages('next');
    }
}
```

### 5단계: 이미지 저장 후 통계 갱신

**파일**: `templates/index.html`

**수정 위치**: `saveAnnotation()` 함수 내부, 저장 성공 후

**추가 코드**:
```javascript
// 저장 성공 후
if (response.ok) {
    // ... 기존 코드 ...
    loadStatistics(); // 통계 갱신
}
```

## 상태 판단 로직

### 상태 정의:
- **unfinished (미작업)**: 저장시간이 없고 할당시간이 있음
- **unassigned (미할당)**: 저장시간도 할당시간도 없음
- **completed (완료)**: 저장시간은 있지만 검수 상태가 없음
- **passed (통과)**: 검수 상태가 '통과'
- **failed (불통)**: 검수 상태가 '불통'
- **delivered (납품완료)**: 검수 상태가 '납품 완료'

### 정렬 기준:
- **oldest (오래된 순)**: 할당시간 오름차순 → image_id 오름차순
- **newest (최신 순)**: 할당시간 내림차순 → image_id 내림차순
- **image_id**: image_id 오름차순

## 테스트 방법

1. **API 테스트**:
   ```bash
   # 상태별 이미지 조회
   curl "http://localhost:5000/api/images_by_status?status=unfinished&sort_by=oldest"
   
   # 통계 조회
   curl "http://localhost:5000/api/work_statistics"
   ```

2. **UI 테스트**:
   - 각 탭 클릭하여 필터링 동작 확인
   - 정렬 옵션 변경하여 정렬 동작 확인
   - 진행률 바가 올바르게 표시되는지 확인
   - 다음/이전 버튼이 필터링된 리스트 기준으로 동작하는지 확인

## 주의사항

1. **Google Sheets 헤더 이름**: 
   - '검수', '저장시간', '할당시간' 등이 정확히 일치해야 함
   - 대소문자 구분 없음

2. **성능**:
   - 500장 이상의 이미지가 있으면 API 응답 시간이 길어질 수 있음
   - 필요시 캐싱 또는 페이지네이션 추가 고려

3. **동기화**:
   - Google Sheets와 웹 인터페이스 간 데이터 동기화 지연 가능
   - 통계는 30초마다 자동 갱신

4. **에러 처리**:
   - Google Sheets 접근 실패 시 적절한 에러 메시지 표시
   - 네트워크 오류 시 재시도 로직 추가 고려

## 추가 개선 사항

1. **검색 기능**: 이미지 ID로 검색
2. **일괄 작업**: 선택한 이미지들의 상태 일괄 변경
3. **필터 조합**: 여러 상태를 동시에 선택
4. **날짜 필터**: 특정 기간의 이미지만 표시
5. **작업자별 통계**: 여러 작업자의 통계 비교

