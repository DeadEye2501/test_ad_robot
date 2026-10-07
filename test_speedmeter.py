"""Тесты замерятеля скорости на локальном HTTP-сервере."""
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import speedmeter

PAYLOAD_SIZE = 256 * 1024
STALL_SECONDS = 1.0


class _PayloadHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.hits += 1
        if self.path == "/missing":
            self.send_error(404)
            return
        if self.path == "/stall":
            time.sleep(STALL_SECONDS)
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(PAYLOAD_SIZE))
        self.end_headers()
        self.wfile.write(b"x" * PAYLOAD_SIZE)

    def log_message(self, format, *args):
        pass


@pytest.fixture
def server():
    server = HTTPServer(("127.0.0.1", 0), _PayloadHandler)
    server.hits = 0
    server.base = f"http://127.0.0.1:{server.server_port}"
    server.url = f"{server.base}/image.jpg"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def test_download_reads_whole_body(server):
    seconds, size = speedmeter.download(server.url)

    assert size == PAYLOAD_SIZE
    assert seconds > 0


def test_measure_downloads_ten_times(server):
    samples = list(speedmeter.measure(server.url))

    assert server.hits == 10
    assert [size for _, size in samples] == [PAYLOAD_SIZE] * 10


def test_summarize_averages_time_and_divides_bytes_by_total_time():
    summary = speedmeter.summarize([(1.0, 1_000_000), (3.0, 1_000_000)])

    assert summary.avg_seconds == 2.0
    assert summary.total_bytes == 2_000_000
    assert summary.mbit_per_s == 4.0
    assert summary.mbyte_per_s == 0.5


def test_main_makes_ten_requests_and_prints_report(server, capsys):
    speedmeter.main([server.url])

    out = capsys.readouterr().out
    request_lines = [line for line in out.splitlines() if line.startswith("Запрос")]
    avg = re.search(r"Среднее время запроса: ([\d.]+) с", out)
    speed = re.search(r"Скорость: ([\d.]+) Мбит/с \(([\d.]+) МБ/с\)", out)
    assert server.hits == 10
    assert len(request_lines) == 10
    assert f"{10 * PAYLOAD_SIZE} байт" in out
    assert avg and float(avg.group(1)) > 0
    assert speed and float(speed.group(2)) > 0
    assert float(speed.group(1)) == pytest.approx(8 * float(speed.group(2)), abs=0.1)


def test_main_reports_http_error_with_request_number(server):
    with pytest.raises(SystemExit) as exit_info:
        speedmeter.main([f"{server.base}/missing"])

    assert "Запрос 1" in str(exit_info.value.code)
    assert "404" in str(exit_info.value.code)


def test_main_gives_up_on_stalled_server(server, monkeypatch):
    monkeypatch.setattr(speedmeter, "TIMEOUT_SECONDS", STALL_SECONDS / 5)

    with pytest.raises(SystemExit) as exit_info:
        speedmeter.main([f"{server.base}/stall"])

    assert "Запрос 1" in str(exit_info.value.code)


def test_main_reports_url_without_scheme():
    with pytest.raises(SystemExit) as exit_info:
        speedmeter.main(["example.com/image.jpg"])

    assert "Запрос 1" in str(exit_info.value.code)
