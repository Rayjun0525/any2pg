# Any2PG

**한국어** | [English](README.md)

다양한 데이터베이스(Oracle, MySQL, MSSQL 등)의 스키마를 PostgreSQL로 변환하는 하이브리드 SQL 마이그레이션 도구입니다.

## 🎯 주요 기능

- **멀티 스테이지 파이프라인**: SQLGlot 자동 변환 ➜ LLM 검수/수정 ➜ PostgreSQL 검증(자동 롤백)
- **재개 가능한 프로세싱**: SQLite 기반 상태 저장으로 중단된 작업을 이어서 진행
- **메타데이터 기반 RAG**: 스키마 정보를 컨텍스트로 활용하여 정확한 변환
- **K9s 스타일 TUI**: 직관적인 터미널 UI로 전체 프로세스를 시각적으로 관리
- **설정 기반 제어**: YAML 파일로 모든 동작을 제어

## 🏗️ 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                  Any2PG 마이그레이션 파이프라인                  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1️⃣ 메타데이터 수집                                            │
│  Source DB ─► SQLAlchemy Inspector ─► SQLite (schema_objects) │
│  (테이블, 뷰, 인덱스, 프로시저, 함수, 트리거 등)                    │
│                                                             │
│  2️⃣ 변환 모드 선택                                             │
│  ┌─────────────┐         ┌───────────────────┐             │
│  │ FAST 모드    │         │ AGENT 모드 (LLM)  │             │
│  │ SQLGlot만   │         │ 검수 ↔ 변환 루프   │             │
│  └─────────────┘         └───────────────────┘             │
│         │                        │                         │
│         └────────┬───────────────┘                         │
│                  ↓                                         │
│  3️⃣ 검증 (PostgreSQL)                                        │
│  BEGIN ─► 실행 ─► ROLLBACK (안전한 검증)                      │
│                  ↓                                         │
│  4️⃣ 결과 저장                                                │
│  SQLite (rendered_outputs) + 파일 export (선택)              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## 📦 설치

```bash
# 1. 저장소 클론
git clone https://github.com/your-repo/any2pg.git
cd any2pg

# 2. 의존성 설치
pip install -r requirements.txt

# 3. 설정 파일 준비
cp sample/config.sample.yaml config.yaml
# config.yaml 파일을 편집하여 DB 연결 정보 입력
```

### Oracle 드라이버 선택

Oracle DB를 사용하는 경우 다음 중 하나를 선택:

**Option 1: oracledb (권장)** - Pure Python 드라이버
```bash
pip install oracledb  # 이미 requirements.txt에 포함됨
```

**Option 2: cx_Oracle** - 네이티브 드라이버 (Oracle Instant Client + C++ Build Tools 필요)
```bash
# requirements.txt에서 cx_Oracle 주석 해제 후
pip install cx_Oracle
```

## 🚀 빠른 시작

### TUI 모드 (권장)

```bash
python src/main.py --config config.yaml
```

K9s 스타일의 대화형 UI가 실행됩니다:
- **좌측**: 자산(SQL 파일) 목록
- **우측**: 상세 정보 (Info / SQL / Logs 탭)
- **네비게이션**: ↑↓로 이동, ←→로 탭 전환, Space로 선택 토글

### CLI 모드

```bash
# 1. 메타데이터 수집
python src/main.py --mode metadata

# 2. 변환 실행
python src/main.py --mode port

# 3. 상태 확인
python src/main.py --mode status

# 4. 결과 내보내기
python src/main.py --mode export

# 5. 타겟 DB에 적용
python src/main.py --mode apply
```

## ⚙️ 설정 파일 (config.yaml)

```yaml
general:
  project_name: "my_project"
  log_path: "logs/any2pg.log"
  log_level: "INFO"
  mode: "cli"  # cli 또는 tui
  metadata_path: "data/project.db"
  max_retries: 3

database:
  source:
    type: "oracle"  # oracle, mysql, mssql, db2 등
    connection_string: "oracle+oracledb://user:pass@host:1521/?service_name=xe"
    schemas:
      - "HR"
      - "SCOTT"
  
  target:
    connection_string: "postgresql://user:pass@localhost:5432/target_db"
    target_schema: "public"

llm:
  provider: "ollama"
  model: "llama3"
  base_url: "http://localhost:11434"
  mode: "fast"  # fast (sqlglot만) 또는 agent (LLM 사용)
```

## 🎨 K9s-Style TUI 사용법

### 메인 화면
```
┌──────────────────────────────────────────────────────────────┐
│ Any2PG v1.0 | Project: my_project                           │
├──────────────────┬───────────────────────────────────────────┤
│ Asset List       │ Detail View                               │
├──────────────────┼───────────────────────────────────────────┤
│ [X] table1.sql   │ Tab: Info | SQL | Logs                   │
│ [ ] table2.sql   │                                           │
│ [X] proc1.sql    │ File: table1.sql                         │
│                  │ Selected: True                            │
│                  │ Status: DONE                              │
│                  │ Extracted: 2025-12-07 15:30:00           │
└──────────────────┴───────────────────────────────────────────┘
 Tab: Info | Space: toggle select | q: quit
```

### 주요 단축키
- `↑/↓` 또는 `j/k`: 자산 목록에서 이동
- `←/→` 또는 `h/l`: 탭 전환
- `Space`: 자산 선택/해제 토글
- `q` 또는 `ESC`: 종료

## 📋 지원되는 소스 데이터베이스

| 데이터베이스 | Type 값 | 어댑터 | 테이블/뷰 | 프로시저/함수 |
|------------|---------|--------|----------|--------------|
| Oracle | `oracle` | `oracle.py` | ✅ | ✅ |
| MySQL/MariaDB | `mysql` | `mysql.py` | ✅ | ✅ |
| MS SQL Server | `mssql` | `mssql.py` | ✅ | ✅ |
| IBM DB2 | `db2` | `db2.py` | ✅ | ✅ |
| SAP HANA | `hana` | `hana.py` | ✅ | ✅ |
| Snowflake | `snowflake` | `snowflake.py` | ✅ | ✅ |
| MongoDB | `mongodb` | `mongodb.py` | ✅ | 🚫 |

## 🔄 워크플로우

### 1. 메타데이터 수집
```bash
python src/main.py --mode metadata
```
- 소스 DB에 연결하여 스키마 정보 추출
- 테이블, 뷰, 인덱스, 프로시저, 함수, 트리거 등을 SQLite에 저장
- **읽기 전용**: 소스 DB는 절대 변경하지 않음

### 2. 변환 실행

**FAST 모드** (sqlglot만 사용)
```yaml
llm:
  mode: "fast"
```
- 규칙 기반 자동 변환
- LLM 비용 없음
- 빠른 처리

**AGENT 모드** (LLM 기반)
```yaml
llm:
  mode: "agent"
```
- AI 기반 변환 및 검수
- RAG를 활용한 컨텍스트 인식 변환
- 높은 품질, 느린 처리

### 3. 검증
- PostgreSQL에서 `BEGIN` → 실행 → `ROLLBACK`으로 안전하게 검증
- 위험한 구문(DROP, DELETE 등)은 설정으로 제어
- 트랜잭션 제어가 불가능한 구문은 `need_permission` 플래그로 표시

### 4. 적용
```bash
python src/main.py --mode apply
```
- 검증이 완료된 SQL을 타겟 DB에 실제 적용
- 선택된 자산만 적용 가능

## 🗄️ SQLite 스키마

주요 테이블:

### schema_objects
소스 DB의 메타데이터 저장
- `project_name`, `schema_name`, `obj_name`, `obj_type`
- `ddl_script`, `source_code`

### source_assets
변환 대상 SQL 파일 정보
- `file_name`, `file_path`, `sql_text`
- `selected_for_port`, `analysis_data`

### rendered_outputs
변환 결과 저장
- `sql_text`, `status`, `verified`
- `review_comments`, `need_permission`, `agent_state`

### execution_logs
실행 로그
- `level`, `event`, `detail`, `created_at`

## 🧪 테스트

```bash
# 단위 테스트
python -m pytest tests/

# PostgreSQL 통합 테스트 (실제 DB 필요)
export POSTGRES_TEST_DSN="postgresql://user:pass@localhost:5432/test_db"
python -m pytest tests/integration/
```

## 🛠️ 개발자 가이드

### 프로젝트 구조
```
any2pg/
├── src/
│   ├── main.py                    # CLI 진입점
│   ├── modules/
│   │   ├── sqlite_store.py        # SQLite 저장소
│   │   ├── metadata_extractor.py  # 메타데이터 수집기
│   │   ├── context_builder.py     # RAG 컨텍스트 빌더
│   │   ├── postgres_verifier.py   # PostgreSQL 검증 엔진
│   │   └── adapters/               # DB 어댑터
│   ├── agents/
│   │   ├── workflow.py             # LangGraph 워크플로우
│   │   └── prompts.py              # LLM 프롬프트
│   └── ui/
│       └── tui.py                  # K9s-style TUI
├── config.yaml                     # 설정 파일
└── requirements.txt                # 의존성
```

### 새 어댑터 추가

1. `src/modules/adapters/`에 새 파일 생성
2. `BaseDBAdapter`를 상속
3. `get_tables_and_views()`, `get_procedures()` 구현
4. `__init__.py`에 등록

## 🔍 문제 해결

### 일반적인 문제

**1. Oracle 연결 실패**
```bash
# oracledb 사용 시
pip install oracledb

# connection_string 확인
oracle+oracledb://user:pass@host:1521/?service_name=xe
```

**2. LLM 연결 실패**
```bash
# Ollama 서버 확인
curl http://localhost:11434/api/tags

# 모델 다운로드
ollama pull llama3
```

**3. 변환 실패 반복**
```yaml
general:
  max_retries: 5  # 재시도 횟수 증가
```

### 로그 확인

```bash
# 상세 로그 활성화
python src/main.py --mode port --log-level DEBUG

# 로그 파일 확인
tail -f logs/any2pg.log
```

## 📝 라이선스

이 프로젝트는 오픈소스 프로젝트입니다.

## 🤝 기여

버그 리포트, 기능 제안, Pull Request 환영합니다!

## 📞 지원

문제가 발생하면 GitHub Issues에 등록해 주세요.

---

**Made with ❤️ for Database Migration**
