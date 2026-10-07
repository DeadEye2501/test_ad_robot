"""Замер скорости скачивания: серия последовательных запросов, каждый — параллельная загрузка набора адресов, и сводка по ним."""
import argparse
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

# Картинки по 4–20 МБ на трёх разных хостах: один поток не выбирает весь канал, десять — выбирают.
DEFAULT_URLS = [
    "https://images.unsplash.com/photo-1506744038136-46273834b3fb?fm=jpg&q=100",
    "https://images.unsplash.com/photo-1501785888041-af3ef285b470?fm=jpg&q=100",
    "https://images.unsplash.com/photo-1470071459604-3b5ec3a7fe05?fm=jpg&q=100",
    "https://images.unsplash.com/photo-1426604966848-d7adac402bff?fm=jpg&q=100",
    "https://cdn.esawebb.org/archives/images/large/weic2205a.jpg",
    "https://cdn.esawebb.org/archives/images/large/weic2301a.jpg",
    "https://cdn.esawebb.org/archives/images/large/weic2207a.jpg",
    "https://cdn.esahubble.org/archives/images/large/heic1509a.jpg",
    "https://cdn.esahubble.org/archives/images/large/heic0910e.jpg",
    "https://cdn.esahubble.org/archives/images/large/heic1107a.jpg",
]
REQUESTS = 10
MEGA = 1_000_000
CHUNK_SIZE = 64 * 1024
TIMEOUT_SECONDS = 30
# Часть хостов (например, Wikimedia) отвечает 403 на User-Agent, который urllib ставит по умолчанию.
USER_AGENT = "speedmeter/1.0 (internet speed test script)"


@dataclass(frozen=True)
class Summary:
    avg_seconds: float
    total_bytes: int
    mbit_per_s: float
    mbyte_per_s: float


class DownloadError(Exception):
    """Адрес не скачался; в тексте — адрес и причина."""


def download(url, stop):
    """Скачивает ответ целиком, пока не поднят stop; возвращает число байт."""
    size = 0
    # Таймаут на подключении приходит как URLError, а на чтении ответа — голым TimeoutError;
    # ValueError — адрес без схемы.
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            while not stop.is_set() and (chunk := response.read(CHUNK_SIZE)):
                size += len(chunk)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise DownloadError(f"{url}: {error}") from error
    return size


def probe(urls):
    """Скачивает все адреса параллельно; возвращает (секунды до последнего ответа, байты всего)."""
    stop = threading.Event()
    pool = ThreadPoolExecutor(max_workers=len(urls))
    started = time.perf_counter()
    # На первой же ошибке (и на Ctrl+C) остальные загрузки гасятся, а не докачиваются до конца.
    try:
        futures = [pool.submit(download, url, stop) for url in urls]
        total_bytes = sum(future.result() for future in as_completed(futures))
        return time.perf_counter() - started, total_bytes
    finally:
        stop.set()
        pool.shutdown(wait=False, cancel_futures=True)


def measure(urls):
    """Делает REQUESTS запросов подряд, отдавая замер каждого."""
    for _ in range(REQUESTS):
        yield probe(urls)


def summarize(samples):
    """Сводка по списку (секунды, байты): скорость — весь объём на всё время."""
    total_seconds = sum(seconds for seconds, _ in samples)
    total_bytes = sum(size for _, size in samples)
    bytes_per_s = total_bytes / total_seconds
    return Summary(
        avg_seconds=total_seconds / len(samples),
        total_bytes=total_bytes,
        mbit_per_s=bytes_per_s * 8 / MEGA,
        mbyte_per_s=bytes_per_s / MEGA,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Замер скорости интернета по скачиванию тяжёлых файлов.")
    parser.add_argument(
        "urls", nargs="*", default=DEFAULT_URLS, help="адреса тяжёлых файлов; в каждом запросе качаются параллельно"
    )
    urls = parser.parse_args(argv).urls

    print(f"Адресов: {len(urls)}, в каждом запросе качаются параллельно")
    samples = []
    try:
        for seconds, size in measure(urls):
            samples.append((seconds, size))
            speed = summarize([(seconds, size)]).mbit_per_s
            print(f"Запрос {len(samples):2}: {seconds:.3f} с, {size / MEGA:.2f} МБ, {speed:.2f} Мбит/с")
    except DownloadError as error:
        raise SystemExit(f"Запрос {len(samples) + 1} не удался: {error}") from error

    summary = summarize(samples)
    print(f"Среднее время запроса: {summary.avg_seconds:.3f} с")
    print(f"Скачано всего: {summary.total_bytes / MEGA:.2f} МБ ({summary.total_bytes} байт)")
    print(f"Скорость: {summary.mbit_per_s:.2f} Мбит/с ({summary.mbyte_per_s:.2f} МБ/с)")


if __name__ == "__main__":
    main()
