# Tiny non-blocking web server for the CYD dashboard.
# poll() is called from the main loop; each call handles at most one request.
#   GET  /                 status page (open)
#   GET  /api/status       JSON status (open)
#   POST /api/settings     form: poll, idle, bambuddy  (password)
#   POST /api/screen       form: on=1|0               (password)
#   POST /api/reboot                                    (password)
#   PUT  /upload/<file.py> raw body = new file          (password, syntax-checked)
# Password: header "X-Password: ..." or form field "pw".
import socket
import json
import os
import time
import gc
import machine

MAX_UPLOAD = 40_000
UPLOADABLE = ("main.py", "app.py", "web.py", "ili9341.py", "touch.py", "font.py", "config.py",
              "touch_cal.py", "calibrate.py")
MAX_FONT = 60_000


def allowed(name, length):
    """Code files from UPLOADABLE (as .py or precompiled .mpy), or BFN1 fonts (f_*.bfn)."""
    if name in UPLOADABLE or name == "index.html" or (
            name.endswith(".mpy") and name[:-4] + ".py" in UPLOADABLE):
        return 0 < length <= MAX_UPLOAD
    return name.startswith("f_") and name.endswith(".bfn") and "/" not in name and 0 < length <= MAX_FONT


class Web:
    def __init__(self, app, password, port=80):
        self.app = app            # object with .status() dict, .apply(dict), .screen(bool)
        self.pw = password
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
        s.listen(2)
        s.setblocking(False)
        self.sock = s

    def poll(self):
        try:
            cl, addr = self.sock.accept()
        except OSError:
            return
        try:
            cl.settimeout(3)
            self._handle(cl)
        except Exception as e:
            print("web", e)
        finally:
            cl.close()
            gc.collect()

    # ----- request plumbing -----

    def _handle(self, cl):
        f = cl.makefile("rwb", 0)
        line = f.readline()
        if not line:
            return
        try:
            method, path, _ = line.decode().split(" ", 2)
        except ValueError:
            return
        headers = {}
        while True:
            h = f.readline()
            if not h or h == b"\r\n":
                break
            k, _, v = h.decode().partition(":")
            headers[k.strip().lower()] = v.strip()
        length = int(headers.get("content-length", 0))

        if method == "GET" and path == "/":
            return self._send_file(cl, "index.html", "text/html")
        if method == "GET" and path == "/api/status":
            return self._send(cl, 200, json.dumps(self.app.status()), "application/json")

        if method == "PUT" and path.startswith("/upload/"):
            name = path[8:]
            if not self._authed(headers, {}):
                self._drain(f, length)
                return self._send(cl, 401, "bad password")
            if not allowed(name, length):
                self._drain(f, length)
                return self._send(cl, 400, "file not allowed or bad size: %s (%d bytes)" % (name, length))
            return self._upload(cl, f, name, length)

        if method == "POST" and path.startswith("/api/"):
            if length > 2000:
                return self._send(cl, 413, "too big")
            form = self._form(f.read(length).decode() if length else "")
            if not self._authed(headers, form):
                return self._send(cl, 401, "bad password")
            if path == "/api/settings":
                return self._send(cl, 200, json.dumps(self.app.apply(form)), "application/json")
            if path == "/api/screen":
                self.app.screen(form.get("on") == "1")
                return self._send(cl, 200, "ok")
            if path == "/api/reboot":
                self._send(cl, 200, "rebooting")
                time.sleep_ms(300)
                machine.reset()
        self._send(cl, 404, "not found")

    def _authed(self, headers, form):
        if not self.pw:
            return False          # no password set: all writes refused
        return headers.get("x-password") == self.pw or form.get("pw") == self.pw

    @staticmethod
    def _form(body):
        out = {}
        for part in body.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k] = unquote(v)
        return out

    @staticmethod
    def _send(cl, code, body, ctype="text/plain"):
        if isinstance(body, str):
            body = body.encode()
        cl.send(b"HTTP/1.0 %d OK\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
                b"Cache-Control: no-store\r\nConnection: close\r\n\r\n" % (code, ctype.encode(), len(body)))
        for i in range(0, len(body), 1024):
            cl.send(body[i:i + 1024])

    @staticmethod
    def _send_file(cl, path, ctype):
        """Stream a file from flash. The page is kept out of RAM on purpose (CLAUDE.md: heap budget)."""
        try:
            size = os.stat(path)[6]
        except OSError:
            return Web._send(cl, 404, path + " missing")
        cl.send(b"HTTP/1.0 200 OK\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
                b"Cache-Control: no-store\r\nConnection: close\r\n\r\n" % (ctype.encode(), size))
        buf = bytearray(512)
        with open(path, "rb") as f:
            while True:
                n = f.readinto(buf)
                if not n:
                    break
                cl.send(buf if n == 512 else buf[:n])

    @staticmethod
    def _drain(f, length):
        """Read and discard a request body we are rejecting. Closing a socket with
        unread data makes lwIP send RST, and the client never sees our reply."""
        left = min(length, MAX_FONT)
        while left > 0:
            chunk = f.read(min(1024, left))
            if not chunk:
                break
            left -= len(chunk)

    def _upload(self, cl, f, name, length):
        tmp = name + ".new"
        got = 0
        gc.collect()                      # start from a tidy heap: growth here starves Wi-Fi (CLAUDE.md 5.5)
        buf = bytearray(512)              # one reused buffer instead of a new bytes object per chunk
        mv = memoryview(buf)
        with open(tmp, "wb") as out:
            while got < length:
                n = f.readinto(mv[:min(512, length - got)])
                if not n:
                    break
                out.write(mv[:n])
                got += n
        if got != length:
            os.remove(tmp)
            return self._send(cl, 400, "short upload %d/%d" % (got, length))
        try:                                 # refuse code that would not compile / bad fonts
            if name.endswith(".py"):
                with open(tmp) as src:
                    compile(src.read(), name, "exec")
            elif name == "index.html":
                with open(tmp, "rb") as src:
                    if not src.read(15).lower().startswith(b"<!doctype html"):
                        raise ValueError("not an HTML page")
            elif name.endswith(".mpy"):
                with open(tmp, "rb") as src:
                    if src.read(2) != b"M\x06":
                        raise ValueError("not a v6 .mpy (build with mpy-cross 1.29)")
            else:
                with open(tmp, "rb") as src:
                    if src.read(4) != b"BFN1":
                        raise ValueError("not a BFN1 font")
        except Exception as e:
            os.remove(tmp)
            return self._send(cl, 400, "rejected, not installed: %r" % e)
        gc.collect()
        os.rename(tmp, name)
        if name.endswith(".mpy"):            # MicroPython imports X.py before X.mpy
            try:
                os.remove(name[:-4] + ".py")
            except OSError:
                pass
        self._send(cl, 200, "installed %s (%d bytes); POST /api/reboot to run it" % (name, got))


def unquote(s):
    s = s.replace("+", " ")
    parts = s.split("%")
    out = parts[0]
    for p in parts[1:]:
        try:
            out += chr(int(p[:2], 16)) + p[2:]
        except ValueError:
            out += "%" + p
    return out


