import logging
import psycopg
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

class VerifierAgent:
    def __init__(self, config: dict):
        """
        :param config: config.yaml 설정 (Target DB 접속 정보 포함)
        """
        # 예: "postgresql://user:pass@host:5432/postgres"
        self.target_dsn = config['database']['target']['uri']

    def verify_sql(self, sql_script: str) -> Tuple[bool, Optional[str]]:
        """
        변환된 SQL을 Target DB에서 시뮬레이션 실행.
        
        :param sql_script: 검증할 SQL 문자열
        :return: (성공여부 Bool, 에러메시지 String)
        """
        if not sql_script or not sql_script.strip():
            return False, "Empty SQL script"

        try:
            # psycopg 3.x 스타일 연결
            with psycopg.connect(self.target_dsn) as conn:
                # 자동 커밋 비활성화 (트랜잭션 시작)
                conn.autocommit = False
                
                with conn.cursor() as cur:
                    try:
                        # 1. 실행 시도
                        # EXPLAIN을 먼저 수행하여 실행 계획 생성 가능 여부 확인 (구문 오류 체크)
                        # 일부 DDL은 EXPLAIN이 안될 수 있으므로, 바로 execute 하는 것이 확실함.
                        # 여기서는 바로 execute 후 rollback 전략 사용.
                        cur.execute(sql_script)
                        
                        # (옵션) 실행 결과가 있다면 fetch하여 서버 부하 테스트 등 가능
                        # if cur.description:
                        #     cur.fetchall()
                        
                        logger.info("Verification passed (Transaction will be rolled back).")
                        return True, None

                    except psycopg.Error as db_err:
                        # DB 레벨 에러 발생 (문법 오류, 객체 없음 등)
                        error_msg = f"DB Error: {db_err.diag.message_primary}"
                        if db_err.diag.context:
                            error_msg += f" | Context: {db_err.diag.context}"
                        
                        logger.warning(f"Verification failed: {error_msg}")
                        return False, error_msg

                    finally:
                        # 2. 필수: 롤백 (성공하든 실패하든 데이터 변경 취소)
                        conn.rollback()

        except Exception as e:
            # 연결 실패 등 시스템 에러
            logger.error(f"Verification system error: {e}")
            return False, str(e)