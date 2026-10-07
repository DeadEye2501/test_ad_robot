"""Тесты замерятеля скорости на локальном HTTP-сервере."""
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import speedmeter

PAYLOAD_SIZE = 256 * 1024
STALL_SECONDS = 1.0
SLOW_SECONDS = 0.3
DRIP_CHUNKS = 20
DRIP_PAUSE = 0.1


class _PayloadHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        with self.server.lock:
            self.server.hits += 1
            hit = self.server.hits
        if self.path == "/missing" or (self.path == "/flaky" and hit > 1):
            self.send_error(404)
            return
        if self.path == "/stall":
            time.sleep(STALL_SECONDS)
        if self.path.startswith("/slow"):
            time.sleep(SLOW_SECONDS)
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(PAYLOAD_SIZE))
        self.end_headers()
        if self.path == "/drip":
            for _ in range(DRIP_CHUNKS):
                self.wfile.write(b"x" * (PAYLOAD_SIZE // DRIP_CHUNKS))
                self.wfile.flush()
                time.sleep(DRIP_PAUSE)
            return
        self.wfile.write(b"x" * PAYLOAD_SIZE)

    def log_message(self, format, *args):
        pass


@pytest.fixture
def server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PayloadHandler)
    server.hits = 0
    server.lock = threading.Lock()
    server.base = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def test_download_reads_whole_body(server):
    assert speedmeter.download(f"{server.base}/a.jpg", threading.Event()) == PAYLOAD_SIZE


def test_probe_downloads_all_urls_in_parallel(server):
    urls = [f"{server.base}/slow-{i}.jpg" for i in range(5)]

    seconds, size = speedmeter.probe(urls)

    assert server.hits == 5
    assert size == 5 * PAYLOAD_SIZE
    assert SLOW_SECONDS <= seconds < 3 * SLOW_SECONDS


def test_probe_fails_fast_without_waiting_for_other_downloads(server):
    started = time.perf_counter()

    with pytest.raises(speedmeter.DownloadError):
        speedmeter.probe([f"{server.base}/drip", f"{server.base}/missing"])

    assert time.perf_counter() - started < DRIP_CHUNKS * DRIP_PAUSE / 2


def test_measure_runs_ten_probes_one_after_another(server):
    started = time.perf_counter()

    samples = list(speedmeter.measure([f"{server.base}/slow.jpg"]))

    assert time.perf_counter() - started >= 10 * SLOW_SECONDS
    assert server.hits == 10
    assert [size for _, size in samples] == [PAYLOAD_SIZE] * 10


def test_summarize_averages_time_and_divides_bytes_by_total_time():
    summary = speedmeter.summarize([(1.0, 1_000_000), (3.0, 1_000_000)])

    assert summary.avg_seconds == 2.0
    assert summary.total_bytes == 2_000_000
    assert summary.mbit_per_s == 4.0
    assert summary.mbyte_per_s == 0.5


def test_main_makes_ten_requests_and_prints_report(server, capsys):
    speedmeter.main([f"{server.base}/slow-a.jpg", f"{server.base}/slow-b.jpg"])

    out = capsys.readouterr().out
    requests = re.findall(r"Запрос +\d+: ([\d.]+) с, ([\d.]+) МБ, ([\d.]+) Мбит/с", out)
    avg = re.search(r"Среднее время запроса: ([\d.]+) с", out)
    speed = re.search(r"Скорость: ([\d.]+) Мбит/с \(([\d.]+) МБ/с\)", out)
    assert server.hits == 20
    assert len(requests) == 10
    for seconds, mbytes, mbits in requests:
        assert float(mbytes) == pytest.approx(2 * PAYLOAD_SIZE / 1_000_000, abs=0.01)
        assert float(mbits) == pytest.approx(float(mbytes) * 8 / float(seconds), rel=0.05)
    assert f"{20 * PAYLOAD_SIZE} байт" in out
    assert avg and float(avg.group(1)) >= SLOW_SECONDS
    assert speed and float(speed.group(2)) > 0
    assert float(speed.group(1)) == pytest.approx(8 * float(speed.group(2)), abs=0.1)


def test_main_reports_http_error_with_request_number_and_url(server):
    with pytest.raises(SystemExit) as exit_info:
        speedmeter.main([f"{server.base}/a.jpg", f"{server.base}/missing"])

    message = str(exit_info.value.code)
    assert "Запрос 1" in message
    assert "404" in message
    assert f"{server.base}/missing" in message


def test_main_reports_number_of_later_failed_request(server):
    with pytest.raises(SystemExit) as exit_info:
        speedmeter.main([f"{server.base}/flaky"])

    assert "Запрос 2" in str(exit_info.value.code)


def test_main_gives_up_on_stalled_server(server, monkeypatch):
    monkeypatch.setattr(speedmeter, "TIMEOUT_SECONDS", STALL_SECONDS / 5)

    with pytest.raises(SystemExit) as exit_info:
        speedmeter.main([f"{server.base}/stall"])

    assert "Запрос 1" in str(exit_info.value.code)


def test_main_reports_url_without_scheme():
    with pytest.raises(SystemExit) as exit_info:
        speedmeter.main(["example.com/image.jpg"])

    assert "Запрос 1" in str(exit_info.value.code)
