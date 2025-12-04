# Any2PG

하이브리드 SQL 마이그레이션 도구로, Oracle/MySQL 등 이기종 DB의 SQL을 PostgreSQL로 변환하고 검증합니다. LangGraph 기반 파이프라인과 `sqlglot` 1차 변환, LLM 보정, PostgreSQL 트랜잭션 검증을 조합합니다.

## 주요 기능
- `sqlglot` 기반 1차 변환 → LLM 리뷰/수정 → PostgreSQL 검증(자동 ROLLBACK) 워크플로우
- 멀티 스키마 메타데이터를 SQLite에 캐시하고 RAG Context로 활용
- CLI 재시작 시 완료된 작업을 건너뛰는 Resume 처리
- 설정 주도형 동작: 로그, LLM, 검증 파라미터 등을 `config.yaml`로 관리

## 사용 방법
```bash
# 헬프 (옵션/예제/기본값 포함)
python src/main.py --help

# 메타데이터 추출 및 DB 초기화
python src/main.py --init --config config.yaml --db-file="project_A.db"

# 마이그레이션 실행 (중단 지점 이어서 재개)
python src/main.py --run --config config.yaml --db-file="project_A.db"

# 작업 로그 초기화
python src/main.py --reset-logs --config config.yaml --db-file="project_A.db"
```
`--config`로 사용할 YAML을 지정할 수 있으며, `--db-file`은 옵션으로 특정 실행만 별도 DB 파일을 사용하고 싶을 때 유용합니다.

## 설정(`config.yaml`) 예시
```yaml
project:
  source_dir: "./input"
  target_dir: "./output"
  db_file: "./migration.db"
  max_retries: 5

logging:
  level: "INFO"                   # DEBUG/INFO/WARNING/ERROR
  file: "./any2pg.log"           # Leave empty to log only to console
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
  max_bytes: 1048576               # Rotate log after ~1MB
  backup_count: 3                  # Number of rotated files to keep

database:
  source:
    type: "oracle"
    uri: "oracle+oracledb://..."
    schemas: ["HR", "SCOTT"]
  target:
    type: "postgres"
    uri: "postgresql://..."
    statement_timeout_ms: 5000     # Optional statement_timeout during verification

llm:
  provider: "ollama"
  model: "gemma:7b"
  base_url: "http://localhost:11434"
  temperature: 0.1

rules:
  - "Oracle의 NVL은 COALESCE로 변환하라."
  - "SYSDATE는 CURRENT_TIMESTAMP로 변경하라."
```

샘플 설정과 테스트용 SQL 쿼리는 `sample/` 디렉터리에 포함되어 있습니다. `sample/config.sample.yaml`을 복사해 프로젝트에 맞게 수정하고, `sample/queries/*.sql` 파일을 `input/`으로 옮겨 바로 동작을 확인할 수 있습니다.

## Verifier 모듈 위치
검증 로직은 `src/modules/verifier.py`에 있으며, 기존 경로 호환을 위해 `src/verifier.py`에서 동일한 `VerifierAgent`를 재노출합니다. 신규 개발 시에는 모듈 경로를 직접 임포트하는 것을 권장합니다.

## 로그 & 사용성
- `logging` 섹션으로 로그 레벨/포맷/파일 회전을 제어합니다.
- 변환 성공/실패 요약을 처리 후 표시하며, 파일별 상태와 에러를 로그로 남깁니다.
- RAG 파서 입력 방언은 `database.source.type`을 따라가므로 소스 DB를 설정하면 자동 적용됩니다.
