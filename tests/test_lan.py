# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 J. Lynch. See LICENSE in the program folder.
"""SDS200 over the network (v1.1.89): the UDP link against a simulated SDS200 on this PC.

The simulated scanner listens on 127.0.0.1 with real UDP, answers MDL/VER/GSI and splits its
GSI reply over two datagrams, the way long replies come over the network. Nothing leaves this PC.
Also covers addresses, the RTSP audio command (checked against the real ffmpeg when installed),
whole samples across pipe reads, and switching the connection row between USB and network.
"""
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path

from support import Settings, mon, monitor

XML = (b'<?xml version="1.0" encoding="utf-8"?>\r<ScannerInfo Mode="Scan Mode">'
       b'<Property Mute="Mute"/><System Name="Palmetto 800"/></ScannerInfo>\r')


class FakeSDS200:
    """A UDP scanner on 127.0.0.1. quiet=True: never answers (scanner off)."""

    def __init__(self, quiet=False):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.05)
        self.port = self.sock.getsockname()[1]
        self.quiet, self.received, self.running = quiet, [], True
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while self.running:
            try:
                data, peer = self.sock.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                return
            self.received.append(data)
            if self.quiet:
                continue
            cmd = data.decode().rstrip("\r")
            if cmd == "MDL":
                self.sock.sendto(b"MDL,SDS200\r", peer)
            elif cmd == "VER":
                self.sock.sendto(b"VER,Version 1.23.04\r", peer)
            elif cmd == "GSI":
                reply = b"GSI,<XML>,\r" + XML
                self.sock.sendto(reply[:40], peer)          # one reply, two datagrams
                self.sock.sendto(reply[40:], peer)
            elif cmd.startswith("KEY,"):
                self.sock.sendto(b"KEY,OK\r", peer)

    def close(self):
        self.running = False
        self.sock.close()
        self.thread.join(1)


class Recorder:
    def __init__(self):
        self.items = []

    def emit(self, value):
        self.items.append(value)


class AddressTests(unittest.TestCase):
    def test_addresses(self):
        self.assertEqual(mon.parse_lan_address("192.168.1.50"), ("192.168.1.50", 50536))
        self.assertEqual(mon.parse_lan_address(" 10.0.0.7:6000 "), ("10.0.0.7", 6000))
        self.assertEqual(mon.parse_lan_address("sds200.lan"), ("sds200.lan", 50536))
        for bad in ("", "  ", "192.168.1.50:abc", "192.168.1.50:70000", "a b", "/dev/ttyACM0"):
            with self.assertRaises(ValueError, msg=bad):
                mon.parse_lan_address(bad)

    def test_rtsp_url(self):
        self.assertEqual(mon.rtsp_url("192.168.1.50"), "rtsp://192.168.1.50:554/au:scanner.au")
        self.assertEqual(mon.rtsp_url("192.168.1.50:50536", "/other"), "rtsp://192.168.1.50:554/other")


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.scanner = FakeSDS200()

    def tearDown(self):
        self.scanner.close()

    def test_opening_checks_the_scanner_answers_and_keeps_the_answer(self):
        with mon.UdpScannerLink("127.0.0.1", self.scanner.port) as link:
            data = link.read()
            self.assertIn(b"MDL,SDS200", data)

    def test_commands_go_out_one_per_datagram(self):
        with mon.UdpScannerLink("127.0.0.1", self.scanner.port) as link:
            link.write(b"KAL\rPSI,500\r")
            time.sleep(0.2)
        self.assertIn(b"KAL\r", self.scanner.received)
        self.assertIn(b"PSI,500\r", self.scanner.received)

    def test_reply_in_two_datagrams_is_joined(self):
        with mon.UdpScannerLink("127.0.0.1", self.scanner.port) as link:
            link.reset_input_buffer()
            link.write(b"GSI\r")
            data, deadline = b"", time.monotonic() + 2
            while b"</ScannerInfo>" not in data and time.monotonic() < deadline:
                data += link.read()
        self.assertTrue(data.startswith(b"GSI,<XML>,"))
        self.assertIn(b"</ScannerInfo>", data)

    def test_no_answer_is_an_error(self):
        quiet = FakeSDS200(quiet=True)
        try:
            start = time.monotonic()
            with self.assertRaises(OSError) as ctx:
                mon.UdpScannerLink("127.0.0.1", quiet.port, answer_wait=0.6)
            self.assertLess(time.monotonic() - start, 2)
            self.assertIn("No answer", str(ctx.exception))
        finally:
            quiet.close()


class ThreadOverNetworkTests(unittest.TestCase):
    """ScannerThread.run() over the UDP link: the same signals as over USB."""

    def run_thread(self, address, seconds):
        signals = {n: Recorder() for n in ("message", "xml_received", "command_result", "list_received",
                                          "analyze_received", "menu_received", "graph_received")}

        class Thread(mon.ScannerThread):
            pass

        class Bound:
            def __init__(self, rec):
                self.emit = rec.emit

        for name, rec in signals.items():
            setattr(Thread, name, property(lambda self, rec=rec: Bound(rec)))
        thread = Thread(address, 115200, 250, link="lan")
        stopper = threading.Timer(seconds, lambda: setattr(thread, "running", False))
        stopper.start()
        saved = mon.port_users
        mon.port_users = lambda port: self.fail("a network link must not look for port users")
        try:
            thread.run()
        finally:
            mon.port_users = saved
            stopper.cancel()
        return signals

    def test_status_and_replies_arrive(self):
        scanner = FakeSDS200()
        try:
            sig = self.run_thread(f"127.0.0.1:{scanner.port}", 1.2)
        finally:
            scanner.close()
        messages = sig["message"].items
        self.assertIn("CONNECTED", messages)
        self.assertEqual(messages[-1], "DISCONNECTED")
        self.assertGreaterEqual(len(sig["xml_received"].items), 2)
        self.assertIn("Palmetto 800", sig["xml_received"].items[0])
        self.assertIn("REPLY: MDL,SDS200", sig["command_result"].items)

    def test_scanner_off_gives_error_then_disconnected(self):
        scanner = FakeSDS200(quiet=True)
        saved = mon.UdpScannerLink.__init__.__defaults__
        mon.UdpScannerLink.__init__.__defaults__ = (mon.SCANNER_UDP_PORT, 0.1, 0.5, None)
        try:
            sig = self.run_thread(f"127.0.0.1:{scanner.port}", 3)
        finally:
            mon.UdpScannerLink.__init__.__defaults__ = saved
            scanner.close()
        messages = sig["message"].items
        self.assertNotIn("CONNECTED", messages)
        self.assertTrue(any(m.startswith("ERROR: No answer") for m in messages), messages)
        self.assertEqual(messages[-1], "DISCONNECTED")

    def test_bad_address_is_an_error(self):
        sig = self.run_thread("", 1)
        self.assertTrue(any(m.startswith("ERROR: Enter the SDS200") for m in sig["message"].items))


class NetworkAudioTests(unittest.TestCase):
    def test_stream_command(self):
        cmd = mon.AudioCapture.stream_command("rtsp://10.0.0.7:554/au:scanner.au")
        self.assertEqual(cmd[cmd.index("-i") + 1], "rtsp://10.0.0.7:554/au:scanner.au")
        self.assertEqual(cmd[-5:], ["-ar", "8000", "-f", "s16le", "pipe:1"])
        self.assertNotIn("-rtsp_transport", cmd)
        tcp = mon.AudioCapture.stream_command("rtsp://x/y", "tcp")
        self.assertEqual(tcp[tcp.index("-rtsp_transport") + 1], "tcp")

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_ffmpeg_accepts_the_options(self):
        """The same options on a test file give raw 8 kHz mono 16-bit audio."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.wav"
            with wave.open(str(src), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(22050)
                w.writeframes(b"\x01\x00\x02\x00" * 22050)        # one second
            cmd = mon.AudioCapture.stream_command(str(src))
            out = subprocess.run(["ffmpeg"] + cmd, capture_output=True, timeout=30)
            self.assertEqual(out.returncode, 0, out.stderr.decode())
            self.assertAlmostEqual(len(out.stdout), 16000, delta=200)

    def test_whole_samples_across_reads(self):
        capture = mon.AudioCapture()
        capture.stream_url = "rtsp://x/y"
        capture.fmt_name, capture.rate, capture.channels, capture.frame = "s16le", 8000, 1, 2
        capture.bytes_per_sec = 16000
        chunks = [b"\x01", b"\x02\x03\x04\x05", b"\x06\x07"]

        class Pipe:
            def readAll(self):
                return type("B", (), {"data": lambda s, c=chunks.pop(0): c})()

        capture.io = Pipe()
        for _ in range(3):
            capture.read()
        self.assertEqual(capture.total, 6)
        self.assertEqual(bytes(capture.ring), b"\x01\x02\x03\x04\x05\x06")
        self.assertEqual(capture.stream_carry, b"\x07")

    def test_stream_delay_lengthens_the_tail(self):
        capture = mon.AudioCapture()
        capture.frame, capture.bytes_per_sec = 2, 16000
        capture.postroll, capture.stream_delay = 1.0, 1.5
        capture.stream_url = "rtsp://x/y"
        capture.current = {"data": bytearray(), "meta": None, "stop_at": None}
        capture.end({"row": []})
        self.assertEqual(capture.current["stop_at"], 40000)        # 2.5 s
        capture.stream_url = None
        capture.end({"row": []})
        self.assertEqual(capture.current["stop_at"], 16000)        # sound input: post-roll only


class ConnectionRowTests(unittest.TestCase):
    def test_switching_keeps_each_address(self):
        w = monitor(fresh=True)
        w.port.setCurrentText("/dev/ttyACM0")
        w.link.setCurrentIndex(w.link.findData("lan"))
        self.assertEqual(w.current_link, "lan")
        self.assertEqual(w.settings.value("port"), "/dev/ttyACM0")
        w.port.addItem("192.168.1.50")
        w.port.setCurrentText("192.168.1.50")
        self.assertEqual(w.lan_address(), "192.168.1.50")
        w.link.setCurrentIndex(w.link.findData("usb"))
        self.assertEqual(w.settings.value("lan_address"), "192.168.1.50")
        self.assertEqual(w.port.currentText(), "/dev/ttyACM0")
        self.assertEqual(w.lan_address(), "192.168.1.50")         # network audio still knows it
        w.link.setCurrentIndex(w.link.findData("lan"))
        self.assertEqual(w.port.currentText(), "192.168.1.50")
        self.assertEqual(w.settings.value("link"), "lan")

    def test_link_is_remembered(self):
        Settings.preset = {"link": "lan", "lan_address": "10.0.0.7"}
        try:
            saved_value = Settings.value

            def value(self, key, default=None, **kw):
                return Settings.preset.get(key, saved_value(self, key, default))
            Settings.value = value
            w = monitor(fresh=True)
        finally:
            Settings.value = saved_value
            del Settings.preset
        self.assertEqual(w.current_link, "lan")
        self.assertEqual(w.port.currentText(), "10.0.0.7")

    def test_network_audio_is_offered(self):
        w = monitor(fresh=True)
        if w.audio is None:
            self.skipTest("Qt Multimedia stand-in not available")
        texts = [w.pc_audio_device.itemText(i) for i in range(w.pc_audio_device.count())]
        self.assertEqual(texts[-1], mon.NETWORK_AUDIO_LABEL)


if __name__ == "__main__":
    unittest.main()
