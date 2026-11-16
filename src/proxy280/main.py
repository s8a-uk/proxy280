import contextlib
import logging
import signal
import sys

from proxy280.app import create_server
from proxy280.config import Settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    # docker stop の SIGTERM で serve_forever を抜けて後始末する
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    settings = Settings.from_env()
    with create_server(settings) as server:
        logging.info("listening on %s:%d", settings.host, settings.port)
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()
