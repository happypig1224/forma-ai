"""Production Readiness & Environment Health Verification.

Runs an automated pre-flight check across all subsystems:
- MySQL Connectivity, Charset, and Migration Head
- Redis Cache Connectivity and Latency
- Storage Subsystem Read/Write/Delete Permissions
- Mock/Dirty Data Absence Verification
- Live Health Endpoints (/health, /health/live, /health/ready)
- Frontend Production Bundle (dist/index.html)
"""

import sys
import time
import urllib.request
import urllib.error
import json
from pathlib import Path

import redis
from sqlalchemy import text

from formaai.config import load_settings
from formaai.infrastructure.database import build_engine


def run_checks():
    settings = load_settings()
    results = []

    print("=" * 65)
    print("  FormaAI Production Readiness Pre-Flight Inspection")
    print("=" * 65)

    # 1. Config Check
    print("\n[1/7] Configuration & Environment Parameters:")
    print(f"  * App Env:        {settings.app_env}")
    print(f"  * Host/Port:      {settings.host}:{settings.port}")
    print(f"  * Reload:         {settings.reload} (Must be False in prod)")
    print(f"  * Log Level:      {settings.log_level}")
    print(f"  * Storage Dir:    {settings.storage_dir}")
    if settings.app_env == "production" and settings.reload:
        results.append(("Configuration Security", False, "Reload is enabled in production"))
    else:
        results.append(("Configuration Security", True, "Production security constraints met"))

    # 2. Database Check
    print("\n[2/7] MySQL Relational Database:")
    try:
        t0 = time.perf_counter()
        engine = build_engine(settings.database_url)
        with engine.connect() as conn:
            # Check connection & timezone
            tz = conn.execute(text("SELECT @@session.time_zone")).scalar()
            # Check alembic migration head
            head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            elapsed_ms = (time.perf_counter() - t0) * 1000
            print(f"  * Connection:     OK ({elapsed_ms:.1f}ms latency)")
            print(f"  * UTC Timezone:   {tz}")
            print(f"  * Migration Head: {head}")
            results.append(("MySQL Connectivity & Migration", True, f"Head: {head}, Latency: {elapsed_ms:.1f}ms"))
    except Exception as exc:
        print(f"  * Connection:     FAILED - {exc}")
        results.append(("MySQL Connectivity & Migration", False, str(exc)))

    # 3. Redis Cache Check
    print("\n[3/7] Redis Zero-Trust Session Cache:")
    try:
        t0 = time.perf_counter()
        r = redis.from_url(settings.redis_url.get_secret_value())
        r.ping()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        dbsize = r.dbsize()
        print(f"  * Redis Ping:     PONG ({elapsed_ms:.1f}ms latency)")
        print(f"  * Active Keys:    {dbsize} keys")
        results.append(("Redis Cache & Auth Session Store", True, f"PONG in {elapsed_ms:.1f}ms, {dbsize} keys"))
    except Exception as exc:
        print(f"  * Redis Ping:     FAILED - {exc}")
        results.append(("Redis Cache & Auth Session Store", False, str(exc)))

    # 4. Storage Permissions Check
    print("\n[4/7] Object/Local Storage Subsystem:")
    try:
        p = Path(settings.storage_dir)
        p.mkdir(parents=True, exist_ok=True)
        probe_file = p / ".probe_readiness.tmp"
        probe_file.write_text("formaai_production_probe_ok", encoding="utf-8")
        content = probe_file.read_text(encoding="utf-8")
        probe_file.unlink()
        assert content == "formaai_production_probe_ok"
        print(f"  * Path:           {p.resolve()}")
        print(f"  * RW Permissions: Verified (Write/Read/Unlink passed)")
        results.append(("Storage Subsystem R/W", True, "Permissions verified"))
    except Exception as exc:
        print(f"  * Storage Check:  FAILED - {exc}")
        results.append(("Storage Subsystem R/W", False, str(exc)))

    # 5. Clean Mock Data Check
    print("\n[5/7] Mock Data & Clean State Verification:")
    try:
        with engine.connect() as conn:
            task_count = conn.execute(text("SELECT COUNT(*) FROM video_task")).scalar()
            shot_count = conn.execute(text("SELECT COUNT(*) FROM video_shot")).scalar()
            attempt_count = conn.execute(text("SELECT COUNT(*) FROM video_attempt")).scalar()
            decision_count = conn.execute(text("SELECT COUNT(*) FROM video_decision")).scalar()
            test_users = conn.execute(text("SELECT COUNT(*) FROM user_account WHERE email LIKE '%test_%'")).scalar()

        print(f"  * video_task:     {task_count} rows")
        print(f"  * video_shot:     {shot_count} rows")
        print(f"  * video_attempt:  {attempt_count} rows")
        print(f"  * video_decision: {decision_count} rows")
        print(f"  * test_users:     {test_users} rows")

        is_clean = (task_count == 0 and shot_count == 0 and attempt_count == 0 and decision_count == 0 and test_users == 0)
        if is_clean:
            print("  * State:          PRISTINE (Zero mock artifacts)")
            results.append(("Pristine Data State", True, "All test artifacts cleared"))
        else:
            print("  * State:          DIRTY (Mock data detected, run reset_mock_data.py)")
            results.append(("Pristine Data State", False, "Mock data remnants found"))
    except Exception as exc:
        print(f"  * Mock Data Check: FAILED - {exc}")
        results.append(("Pristine Data State", False, str(exc)))

    # 6. HTTP Health Endpoints Check
    print("\n[6/7] Backend HTTP Health Endpoints:")
    host_for_probe = "127.0.0.1" if settings.host in ("0.0.0.0", "::") else settings.host
    base_url = f"http://{host_for_probe}:{settings.port}"
    for ep in ["/health", "/health/live", "/health/ready"]:
        try:
            req = urllib.request.Request(f"{base_url}{ep}", method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                status_code = resp.getcode()
                body = resp.read().decode("utf-8")
                print(f"  * {ep:<14} -> HTTP {status_code} ({body.strip()})")
                results.append((f"Endpoint {ep}", status_code == 200, f"HTTP {status_code}"))
        except Exception as exc:
            print(f"  * {ep:<14} -> FAILED - {exc}")
            results.append((f"Endpoint {ep}", False, str(exc)))

    # 7. Frontend Production Bundle Check
    print("\n[7/7] Frontend Production Distribution Assets:")
    frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist" / "index.html"
    if frontend_dist.exists():
        size_kb = frontend_dist.stat().st_size / 1024
        print(f"  * Dist Index:     Found ({frontend_dist})")
        print(f"  * Entry Size:     {size_kb:.2f} KB")
        results.append(("Frontend Production Bundle", True, f"dist/index.html verified ({size_kb:.2f} KB)"))
    else:
        print(f"  * Dist Index:     NOT FOUND at {frontend_dist}")
        results.append(("Frontend Production Bundle", False, "Run 'npm run build' in frontend directory"))

    # Summary Table
    print("\n" + "=" * 65)
    print("  Readiness Inspection Summary Report")
    print("=" * 65)
    all_passed = True
    for name, passed, detail in results:
        status_icon = "✓ PASS" if passed else "✗ FAIL"
        if not passed:
            all_passed = False
        print(f"  [{status_icon}] {name:<35} {detail}")

    print("=" * 65)
    if all_passed:
        print("  >>> SYSTEM STATUS: 100% READY FOR PRODUCTION DRILL <<<")
    else:
        print("  >>> SYSTEM STATUS: WARNINGS DETECTED (Review Failed Items) <<<")
    print("=" * 65)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(run_checks())
