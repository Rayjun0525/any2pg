# Any2PG (한글 안내)

이 도구는 다양한 원천 SQL(Oracle/MySQL 등)을 PostgreSQL로 변환하고, LLM 기반 리뷰·수정과 PostgreSQL 검증을 거쳐 안전한 결과를 생성하는 하이브리드 마이그레이션 툴입니다. 이 문서를 순서대로 따라 하면 별도 참조 없이 설치·구성·운영을 진행할 수 있습니다.

## 1) Any2PG가 하는 일
- 다단계 파이프라인: SQLGlot 변환 ➜ LLM 리뷰/패치 ➜ PostgreSQL 검증(강제 `ROLLBACK`).
- 재실행 친화적 처리: SQLite가 파일 상태를 기록해 완료된 항목을 건너뜀.
- 메타데이터 기반 RAG: 스키마별 객체를 SQLite에 캐시해 정확한 컨텍스트를 제공.
- 설정 우선 제어: 로깅, 어댑터, 검증 옵션, LLM 설정 모두 YAML로 관리.

## 2) 아키텍처 한눈에 보기
- **CLI (src/main.py):** `--init`, `--run`, `--reset-logs` 외에 `--mode assets/export/apply/quality`로 SQLite에 적재된 입력·출력 자산을 조회/내보내기/직접 적용하거나 품질 게이트를 점검할 수 있으며, `--config`, `--db-file`, `--log-level`, `--log-file` 오버라이드 지원.
- **추출기 (src/modules/metadata_extractor.py):** 설정된 스키마를 순회해 결과를 SQLite(`schema_objects`, `migration_logs`)에 저장.
- **RAG 컨텍스트 빌더 (src/modules/context_builder.py):** SQLGlot으로 SQL을 파싱하고 SQLite에서 관련 객체를 조회.
- **워크플로우 (src/agents/workflow.py):** LangGraph 상태 머신으로 변환 ➜ 리뷰 ➜ 검증 ➜ 보정 루프 수행.
- **검증기 (src/modules/postgres_verifier.py):** PostgreSQL을 `autocommit=False`로 실행 후 무조건 롤백; 과거 호환용 쉼은 `src/context_builder_shim.py`, `src/postgres_verifier_shim.py`.
- **TUI (src/ui/tui.py):** `--mode`를 생략하면 기본으로 실행되는 curses 기반 메뉴로, 메타정보 수집/조회, 포팅/재개, 내보내기/적용, 로그, 품질 검사를 탐색합니다.

## 3) 지원 소스 어댑터
| Source DB | Config `database.source.type` | Adapter Path | 테이블/뷰 추출 | 프로시저 추출 |
|-----------|------------------------------|--------------|---------------|---------------|
| Oracle | `oracle` | `src/modules/adapters/oracle.py` | ✅ SQLAlchemy inspector | ✅ `USER_SOURCE` 집계 |
| MySQL/MariaDB | `mysql` / `mariadb` | `src/modules/adapters/mysql.py` | ✅ inspector | ✅ `information_schema.ROUTINES` |
| Microsoft SQL Server | `mssql` | `src/modules/adapters/mssql.py` | ✅(`dbo` 기본) | ✅ `sys.objects`/`sys.sql_modules` |
| IBM DB2 | `db2` | `src/modules/adapters/db2.py` | ✅(대문자 스키마) | ✅ `SYSCAT.ROUTINES` |
| SAP HANA | `hana` | `src/modules/adapters/hana.py` | ✅ inspector | ✅ `SYS.PROCEDURES` |
| Snowflake | `snowflake` | `src/modules/adapters/snowflake.py` | ✅ inspector | ✅ `information_schema.routines` |
| MongoDB | `mongodb` | `src/modules/adapters/mongodb.py` | ✅ 컬렉션을 테이블로 취급 | 🚫 (미지원) |

> 새로운 어댑터가 없으면 `ValueError`가 발생합니다. `BaseDBAdapter`를 상속해 `src/modules/adapters/`에 구현을 추가하세요.

## 4) 전체 실행 흐름
1. **메타데이터 모드 (`--mode metadata` 또는 `--init`)**: 소스 DB에 연결해 설정된 스키마를 추출하고 SQLite에 캐시합니다. 추출은 읽기 전용이며 원천 DB를 변경하지 않습니다.
2. **포팅 모드 (`--mode port` 또는 `--run`, 기본)**: **SQLite를 단일 진리 소스로 사용**합니다. `project.auto_ingest_source_dir`가 `true`일 때만 `project.source_dir`을 스캔해 `source_assets`로 동기화하고, 이후 모든 단계는 SQLite 레코드를 기준으로 처리합니다. 변환 결과는 항상 `rendered_outputs`에 저장되며, `project.mirror_outputs`가 `true`일 때만 `project.target_dir`로 파일을 미러링합니다. 검증은 BEGIN/ROLLBACK 트랜잭션으로 감싸며, 위험 DDL/DML이나 프로시저 호출은 `verification.*` 설정을 허용하기 전까지 건너뜁니다. 진행 상황은 `migration_logs`에 `PENDING/DONE/FAILED/...` 상태로 저장됩니다.
3. **리포트 모드 (`--mode report`)**: SQLite에 축적된 변환 결과를 `project.name` 단위로 조회하고, 스키마/상태 필터로 보고서를 좁힐 수 있습니다. 스킵된 문장과 재시도 횟수도 함께 표시됩니다.
4. **자산 모드 (`--mode assets`)**: SQLite에 적재된 입력 SQL 자산을 조회하고 선택 여부를 확인합니다(`--only-selected`, `--changed-only`, `--show-sql` 지원).
5. **추출 모드 (`--mode export`)**: SQLite에 저장된 변환 결과를 선택적으로 파일로 내보냅니다(`--changed-only`, `--asset-names`, `--export-dir`).
6. **직접 적용 모드 (`--mode apply`)**: 선택한 변환 결과를 PostgreSQL에 실제 실행합니다(검증과 동일한 안전 필터 적용, `rendered_outputs`/`migration_logs` 업데이트).
7. **로그 리셋 (`--reset-logs`)**: 현재 `project.name`에 해당하는 `migration_logs`를 초기화해 모든 파일을 다시 처리합니다.
8. **회복력**: 재실행 시 `DONE` 상태이며 원본 해시가 변하지 않은 항목은 건너뜁니다. 재시도는 `project.max_retries`에 도달하면 중단됩니다.
9. **품질 점검 모드 (`--mode quality` 또는 `--quality`)**: 샌드박스 SQLite/자산을 사용해 설정/로그 안전성, 스키마 컬럼 존재 여부, 위험 SQL 차단, 자산 저장 여부를 점수화한 리포트를 출력합니다.

## 5) 빠른 시작 (기본 TUI)
```bash
# 0) 의존성 설치
python -m pip install -r requirements.txt

# 1) 샘플 설정 복사 후 연결 URI/스키마 수정
cp sample/config.sample.yaml ./config.yaml

# 2) TUI 실행 (기본 진입점)
python src/main.py --config config.yaml

# 3) CLI만 사용하고 싶다면
python src/main.py --mode metadata --config config.yaml --db-file "./project_A.db" --log-level DEBUG
python src/main.py --mode port --config config.yaml --db-file "./project_A.db"
python src/main.py --mode report --config config.yaml --schema-filter HR
```

## 6) 기본 TUI 흐름
1. **시작**: `python src/main.py --config config.yaml` 실행 시 프로젝트 이름/버전이 포함된 배너와 4가지 메뉴(메타정보 수집/런 포팅/상태 확인/익스포트)가 표시됩니다.
2. **메타정보 수집**: `database.source`에 접속해 스키마/DDL을 SQLite(`schema_objects`)에 적재합니다. 프로젝트당 한 번만 실행해도 되며, 상태 확인 메뉴에서 바로 내용을 탐색할 수 있습니다.
3. **런 포팅**: *FAST(sqlglot)*은 일반 변환만 수행해 SQLite에 저장 후 종료합니다. *FULL*은 sqlglot 1차 변환 → SQLite 저장 → 랭그래프 검수(통과 시 검증으로 이동, 미통과 시 변환 에이전트 재실행) → 검증 완료 시 SQLite에 확정본을 저장합니다. 각 단계 상태가 SQLite에 남기 때문에 강제 종료 후에도 재시작하면 이어서 진행됩니다.
4. **상태 확인**: 메타정보 내용, 변환/검증 진행 상황, 변환된 SQL 미리보기, 실행 로그/품질 검사를 한 메뉴에서 확인합니다.
5. **익스포트**: SQLite에 저장된 결과를 파일로 내보내거나, 바로 타깃 DB에 생성합니다.
6. **사일런트 모드**: `project.silent_mode: true` 또는 `--silent`로 표준출력을 최소화하고 실행 이벤트를 SQLite에 기록합니다.

7. **조작법**: ↑/↓로 이동하고, ←로 뒤로 가며, → 또는 Enter로 선택합니다. ESC도 메뉴를 닫습니다.

## 7) 설정 참조 (config.yaml)
```yaml
project:
  name: "example_project"        # 모든 SQLite 행과 리포트를 이 프로젝트명으로 구분
  version: "0.1.0"               # TUI 헤더에 표시할 선택적 버전/배포명
  source_dir: ""                   # 선택 사항: 파일 시스템 폴더에서 자동 적재할 때만 설정
  target_dir: ""                   # mirror_outputs가 true일 때 사용할 미러링 경로
  db_file: "./migration.db"        # 기본 SQLite 경로 (--db-file로 오버라이드 가능)
  max_retries: 5                    # 보정 루프 최대 재시도 횟수
  auto_ingest_source_dir: false     # 기본값은 비활성화; 파일 시스템에서 가져올 때만 true로 설정
  mirror_outputs: false             # true면 변환 SQL을 target_dir에도 파일로 기록
  silent_mode: false                # true면 stdout을 최소화하고 실행 로그를 SQLite에 적재(--silent로 오버라이드)

logging:
  level: "INFO"                     # DEBUG, INFO, WARNING, ERROR
  module_levels:                    # 모듈별 상세 로깅이 필요할 때 사용
    agents.workflow: "DEBUG"
    modules.context_builder: "DEBUG"
  file: "./any2pg.log"             # 빈 문자열이면 콘솔만 사용
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
  max_bytes: 1048576                # 약 1MB마다 로테이션
  backup_count: 3                   # 보관할 로테이션 파일 수

database:
  source:
    type: "oracle"                 # 위 어댑터 표 참고
    uri: "oracle+oracledb://user:pass@host:1521/?service_name=xe"
    schemas: ["HR", "SCOTT"]       # 스키마 목록; 생략 시 DB 기본값
  target:
    type: "postgres"
    uri: "postgresql://user:pass@localhost:5432/postgres"
    statement_timeout_ms: 5000      # 검증 시 사용할 PG statement_timeout

llm:
  mode: "full"                   # fast | full — fast는 LLM 리뷰/수정을 생략
  provider: "ollama"               # LangChain이 인터페이스 처리
  model: "gemma:7b"
  base_url: "http://localhost:11434"
  temperature: 0.1

verification:
  mode: "port"                    # metadata | port | report (기본값은 포팅)
  allow_dangerous_statements: false  # true면 DDL/DML 실행 허용(여전히 BEGIN/ROLLBACK 적용)
  allow_procedure_execution: false   # true면 CALL/DO/EXECUTE 검증 실행 허용

rules:                                # 리뷰어에게 전달할 가이드 문자열
  - "Convert Oracle NVL to COALESCE."
  - "Replace SYSDATE with CURRENT_TIMESTAMP."
```

## 8) SQLite 스키마
- **schema_objects**: `obj_id`(PK), `project_name`, `schema_name`, `obj_name`, `obj_type`, `ddl_script`, `source_code`, `extracted_at`. `(project_name, schema_name, obj_name, obj_type)`로 유니크 보장.
- **source_assets**: `asset_id`(PK), `project_name`, `file_name`, `file_path`, `sql_text`, `content_hash`, `parsed_schemas`, `selected_for_port`, `notes`, `created_at`, `updated_at`. 원본 SQL을 SQLite로 일원화하며 해시/선택 상태를 포함합니다.
- **rendered_outputs**: `output_id`(PK), `project_name`, `asset_id`, `file_name`, `file_path`, `sql_text`, `content_hash`, `source_hash`, `status`, `verified`, `last_error`, `updated_at`. 변환물과 검증/적용 상태를 보관하며 `source_hash`로 최신 여부를 판단합니다.
- **migration_logs / 리포트 소스**: `project_name`, `file_path`, `detected_schemas`, `status`, `retry_count`, `last_error_msg`, `target_path`, `skipped_statements`, `executed_statements`, `updated_at`. `(project_name, file_path)` 유니크로 동일 SQLite 파일을 여러 프로젝트가 안전하게 공유.
  - `detected_schemas`는 파싱된 SQL 참조로부터 파생되어, 스키마 기반 필터를 적용해도 프로젝트 간 충돌이 없습니다.

## 9) 샘플 자산
- `sample/config.sample.yaml`: Oracle➜Postgres 기본값이 포함된 복사용 샘플.
- `sample/queries/*.sql`: 세 개의 예제 쿼리(단순 select, join+decode, 함수 호출)로 워크플로를 검증할 수 있습니다. `./input`에 복사해 바로 실행해 보세요.

## 10) 로깅 & 트러블슈팅
- 조정: `logging.level`로 출력 수준을 조절하거나 `--log-level`(`ANY2PG_LOG_LEVEL`)로 1회성 오버라이드하세요. 파일 경로는 `--log-file`(`ANY2PG_LOG_FILE`) 또는 YAML로 지정하며, 필요 시 상위 디렉터리를 자동 생성합니다.
- 타깃 추적: 특정 모듈만 자세히 보고 싶다면 `logging.module_levels`로 설정합니다(예: 단계별 트레이스를 위한 `agents.workflow`, 컨텍스트 조회 디버깅용 `modules.context_builder`).
- 사일런트 실행: `project.silent_mode: true` 또는 `--silent`로 표준출력을 최소화하고 실행 이벤트를 SQLite(`execution_logs`)에 적재합니다. TUI의 *진행 현황/로그 확인* 메뉴에서 바로 조회할 수 있습니다.
- 검증 안전장치: 검증은 명시적 `BEGIN`/`ROLLBACK`으로 감싸며, 위험 DDL/DML과 프로시저 실행은 `verification.allow_dangerous_statements`/`allow_procedure_execution`을 활성화하지 않는 한 건너뜁니다. Statement timeout도 설정 가능. **데이터 동등성 비교는 자동으로 수행되지 않으므로, 실제 데이터 검증은 사용자가 직접 진행해야 합니다.**
- 어댑터 이슈: 다수의 어댑터는 SQLAlchemy inspector를 사용합니다. 필요한 드라이버가 없으면 import/connection 에러가 발생하므로 소스 DB에 맞는 드라이버를 설치하세요.
- 재개 로직: `FAILED`/`VERIFY_FAIL` 상태가 남으면 `migration_logs.last_error_msg`를 확인하고 필요 시 `project.max_retries`를 늘리세요.
- 설정 검증: 시작 시 필수 키(`project.*`, `database.{source,target}.uri`, `llm.*`)를 검사하고 `max_retries < 1`이면 실패하므로 잘못된 설정을 초기에 차단합니다.
- 결정적 컨텍스트: RAG 컨텍스트 빌더는 `schema_name`, `obj_type`, `obj_name` 순으로 정렬된 결과를 반환해 실행마다 동일한 프롬프트를 제공합니다.
- 리포팅: `--mode report --schema-filter HR`로 활성 `project.name` 범위의 SQLite 결과를 출력하며, 스킵된 문장·재시도 횟수를 포함해 교차 프로젝트 누출 없이 확인할 수 있습니다.

## 11) 품질 게이트 & 테스트
- 방어적 설정 검증(경로 확장, 재시도 수 정규화, 필수 키 확인)으로 잘못된 실행을 조기에 차단합니다.
- SQLite 작업은 모두 트랜잭션 기반이며 오류 시 롤백해 부분 저장을 방지합니다.
- RAG 컨텍스트 빌더는 파싱 실패 시 안전하게 무시하며, DDL/소스 텍스트를 제공하는 객체만 전달합니다.
- `--mode quality`는 설정/로그/스키마/안전 필터/자산 저장을 자동 점검해 모든 지표가 10/10인지 확인하는 리포트를 제공합니다.
- 전체 테스트는 `python -m pytest`로 실행하며, 실 PostgreSQL 스모크 테스트를 위해 `POSTGRES_TEST_DSN`을 설정할 수 있습니다.

## 12) 개발자 노트
- 핵심 코드는 `src/modules/`(metadata_extractor, context_builder, postgres_verifier, adapters)에 있으며, LangGraph 워크플로와 프롬프트는 `src/agents/` 아래에 있습니다.
- 하위 호환을 위해 `src/context_builder_shim.py`, `src/postgres_verifier_shim.py`가 동일 구현을 재노출합니다. 신규 코드는 `src/modules/` 경로를 직접 임포트하세요.
- 주석/도큐스트링과 사용자 노출 메시지는 기본적으로 영어를 사용합니다. 한글 안내는 이 `README_KR.md`에만 제공합니다.

## 13) 실 DB 검증 (PostgreSQL / Oracle)
- PostgreSQL 스모크 테스트: `POSTGRES_TEST_DSN`(예: `postgresql://user:pass@localhost:5432/any2pg_test`)을 지정하고 `python -m pytest -q`를 실행하면 `tests/integration/test_postgres_live.py`가 동작합니다. 검증은 트랜잭션 내부에서 수행되며 모든 문장이 롤백됩니다.
- Oracle 스모크 테스트: 번들되지 않은 외부 Oracle 인스턴스가 필요합니다. `ORACLE_TEST_DSN`을 설정하고 유사한 픽스처를 추가해 엔드 투 엔드 검증을 확장하세요.
