from services.scheduler import party_scheduler
from services.fixedraid_scheduler import start as fixedraid_start, stop as fixedraid_stop
from utils.http_client import init_http_client, close_http_client
from utils.task_utils import get_background_task_stats
from utils.metrics import set_process_rss
from database.connection import get_pool_stats
import asyncio
import signal
import sys
import os
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)
HEARTBEAT_SEC = max(60, int(os.getenv("SCHED_HEARTBEAT_SEC", "180")))

import psutil

def signal_handler(sig, frame):
    party_scheduler.stop()
    fixedraid_stop()
    print("[INFO] scheduler stopped")
    sys.exit(0)


async def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    print("[INFO] scheduler started")
    await init_http_client()
    party_scheduler.start()
    fixedraid_start()
    try:
        loop = asyncio.get_running_loop()
        next_heartbeat = loop.time() + HEARTBEAT_SEC
        while True:
            await asyncio.sleep(1)
            now = loop.time()
            if now < next_heartbeat:
                continue
            next_heartbeat = now + HEARTBEAT_SEC

            rss = 0
            if psutil:
                try:
                    rss = int(psutil.Process(os.getpid()).memory_info().rss or 0)
                except Exception:
                    rss = 0
            set_process_rss(rss)

            pool = get_pool_stats()
            bg = get_background_task_stats()
            logger.info(
                "runtime_heartbeat rss=%s asyncio_tasks=%s db_pool_used=%s db_pool_free=%s bg_tasks=%s bg_task_keys=%s",
                rss,
                len(asyncio.all_tasks()),
                int(pool.get("used", 0)),
                int(pool.get("free", 0)),
                int(bg.get("pending", 0)),
                int(bg.get("coalesced_keys", 0)),
            )
    except KeyboardInterrupt:
        signal_handler(None, None)
    except Exception as e:
        print(f"[ERROR] scheduler error: {e}")
        party_scheduler.stop()
        fixedraid_stop()
    finally:
        await close_http_client()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[INFO] 스케줄러 종료")
    except Exception as e:
        print(f"[ERROR] 스케줄러 구동중 오류 발생: {e}")
