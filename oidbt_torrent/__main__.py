def run_example():
    from pathlib import Path

    from easyrip import log

    from .torrent import Torrent

    log.print_level = log.LogLevel.debug
    log.write_level = log.LogLevel.none

    for path in (
        r"种子测试-v1-单文件.torrent",
        r"种子测试-v1.torrent",
        r"种子测试-v2.torrent",
        r"种子测试-混合.torrent",
    ):
        log.info(path)
        torrent = Torrent(Path(path))
        log.info(torrent.info.format)
        log.info(torrent.get_magnet())
        print()


if __name__ == "__main__":
    run_example()
