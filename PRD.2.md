You are upgrading an existing Persian news RSS scraper into a production-grade long-running service.

The current system:
- Fetches MehrNews RSS
- Downloads only new articles
- Saves XML and images
- Maintains history correctly

DO NOT break existing functionality.

========================
NEW GOALS
========================
1. Convert the scraper into a long-running service
2. Support scheduled execution
3. Add structured logging
4. Improve error isolation
5. Make execution idempotent and safe

========================
SERVICE MODE
========================
- The program must support:
    - one-shot execution
    - daemon/service mode
- Service mode:
    - runs forever
    - polls RSS every N minutes (configurable)
    - graceful shutdown (SIGTERM, SIGINT)

========================
CONFIGURATION
========================
- All configs via environment or config file:
    - RSS interval
    - output paths
    - timeout
    - retry count
    - user-agent

========================
LOGGING
========================
- Use structured logging (JSON)
- Log levels:
    - INFO: new article saved
    - WARNING: partial failures
    - ERROR: fetch/parsing failures
- Each log entry must include:
    - source
    - url
    - job_id (if exists)

========================
ERROR HANDLING
========================
- One failed article must NOT stop the loop
- Retry network errors
- Skip malformed items safely

========================
PROCESS SAFETY
========================
- File writes must be atomic
- No corrupted XML allowed
- History must never be lost

========================
DELIVERABLES
========================
1. Refactored main entrypoint
2. Service loop implementation
3. Signal handling
4. Logging setup
5. Example config file
6. README update

========================
STRICT RULE
========================
Preserve all existing behavior.
Only extend and harden the system.
Generate real production-ready code.
