"""Замер скорости скачивания: серия последовательных запросов к одному адресу и сводка по ним."""
import argparse
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_URL = "https://images.unsplash.com/photo-1506744038136-46273834b3fb?fm=jpg&q=100"
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


def download(url):
    """Скачивает ответ целиком; возвращает (секунды, байты)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    started = time.perf_counter()
    size = 0
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        while chunk := response.read(CHUNK_SIZE):
            size += len(chunk)
    return time.perf_counter() - started, size


def measure(url):
    """Скачивает адрес REQUESTS раз подряд, отдавая замер каждого запроса."""
    for _ in range(REQUESTS):
        yield download(url)


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
    parser = argparse.ArgumentParser(description="Замер скорости интернета по скачиванию файла.")
    parser.add_argument("url", nargs="?", default=DEFAULT_URL, help="адрес тяжёлого файла")
    url = parser.parse_args(argv).url

    print(f"Адрес: {url}")
    samples = []
    # Таймаут на подключении приходит как URLError, а на чтении ответа — голым TimeoutError;
    # ValueError — адрес без схемы.
    try:
        for seconds, size in measure(url):
            samples.append((seconds, size))
            print(f"Запрос {len(samples):2}: {seconds:.3f} с, {size / MEGA:.2f} МБ")
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise SystemExit(f"Запрос {len(samples) + 1} не удался: {error}") from error

    summary = summarize(samples)
    print(f"Среднее время запроса: {summary.avg_seconds:.3f} с")
    print(f"Скачано всего: {summary.total_bytes / MEGA:.2f} МБ ({summary.total_bytes} байт)")
    print(f"Скорость: {summary.mbit_per_s:.2f} Мбит/с ({summary.mbyte_per_s:.2f} МБ/с)")


if __name__ == "__main__":
    main()
