# src/modules/extractor.py (생성자 부분 변경)

class MetadataExtractor:
    def __init__(self, config: dict, db_manager: DBManager):
        self.config = config
        self.db_mngr = db_manager
        
        source_conf = self.config['database']['source']
        
        # [변경] 엔진 생성을 팩토리 내부로 위임 (NoSQL 지원 위해)
        self.adapter = get_adapter(source_conf)