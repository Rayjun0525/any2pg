# any2pg

SQL Translator for any DB to PostgreSQL.

## Usage

This project provides a multi-agent pipeline powered by [autogen](https://github.com/microsoft/autogen)
and [Ollama](https://ollama.ai) with the `gpt-oss:20b` model to translate SQL files into PostgreSQL
compatible queries.

```bash
python main.py path/to/sql_dir path/to/output_dir --config config.yaml
```

Create a YAML configuration file to control execution, meta-pattern handling, and retry behaviour.

```yaml
max_rounds: 3
execute:
  enabled: true
  dsn: postgresql://user:pass@host/db
meta:
  enabled: true
  path: meta.json
```

A sample configuration file is available as `config.sample.yaml`.

The script will recursively search for `*.sql` files in the input directory, translate each file, and
write the PostgreSQL version to the output directory. When `execute.enabled` is true the converted
query is executed against PostgreSQL and the result or error is printed. When `meta.enabled` is true
general SQL patterns are stored in `meta.path` to improve future translations.

Stored patterns are kept in a JSON file and automatically deduplicated:

```json
{
  "patterns": [
    {
      "id": "e13a5c2f",
      "source_pattern": "SELECT ${expr1} || ${expr2}",
      "postgres_pattern": "SELECT ${expr1} || ${expr2}"
    }
  ]
}
```
