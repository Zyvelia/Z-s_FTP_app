"""A local HTTP file receiver with explicit approval before accepting a body."""

from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address, IPv4Network, ip_address
import json
import os
from pathlib import Path, PureWindowsPath
import secrets
import shutil
import socket
from tempfile import NamedTemporaryFile
from threading import BoundedSemaphore, Lock, Thread
from time import monotonic

PRIVATE = tuple(IPv4Network(network) for network in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8'))
MAX_UPLOAD = 32 * 1024**3
MAX_PENDING = 20
EXPIRY = 300

PAGE = b'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Z FTP Phone Drop</title><style>
html{color-scheme:dark}body{font:1rem system-ui;background:#171522;color:#fff;margin:0}
main{max-width:32rem;padding:2rem;margin:auto}input,button{display:block;margin:1rem 0;font:inherit}
button{padding:.7rem 1rem;background:#7449a0;color:white;border:0;border-radius:.4rem}
progress{width:100%}</style></head><body><main><h1>Z FTP Phone Drop</h1>
<label>Choose a file <input type="file" id="file"></label>
<button id="send" type="button">Request upload</button>
<p id="status" role="status"></p><progress id="progress" value="0" max="100" hidden></progress></main>
<script>
const fileInput=document.querySelector('#file'), button=document.querySelector('#send');
const status=document.querySelector('#status'), meter=document.querySelector('#progress');
const base=location.pathname.replace(/\\/$/,'');
button.onclick=async()=>{
 const file=fileInput.files[0]; if(!file){status.textContent='Choose a file first.';return;}
 button.disabled=true;meter.hidden=true;meter.value=0;
 try{
  const req=await fetch(base+'/request',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:file.name,size:file.size})});
  const created=await req.json();if(!req.ok)throw Error(created.error);
  status.textContent='Waiting for someone at the PC to approve this file.';
  const url=base+'/request/'+created.id;
  for(let i=0;i<300;i++){
   await new Promise(resolve=>setTimeout(resolve,1000));
   const result=await fetch(url), info=await result.json();
   if(!result.ok)throw Error(info.error);
   if(info.state==='rejected')throw Error('The PC declined the upload.');
   if(info.state!=='approved')continue;
   meter.hidden=false;status.textContent='Uploading...';
   await new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();xhr.open('POST',url+'/file');xhr.timeout=0;
    xhr.upload.onprogress=e=>{if(e.lengthComputable)meter.value=e.loaded*100/e.total;};
    xhr.onload=()=>{let body={};try{body=JSON.parse(xhr.responseText)}catch{}
      xhr.status===201?resolve():reject(Error(body.error||'Upload failed.'));};
    xhr.onerror=()=>reject(Error('Connection lost.'));xhr.send(file);
   });meter.value=100;status.textContent='Received by the PC.';return;
  } throw Error('The upload request expired.');
 }catch(error){status.textContent=error.message||'Could not reach the PC.';}
 finally{button.disabled=false;}
};
</script></body></html>'''


def private_address(value):
    try:
        address = ip_address(value)
    except ValueError:
        return False
    return isinstance(address, IPv4Address) and any(address in network for network in PRIVATE)


def valid_name(name):
    if (not isinstance(name, str) or not name or len(name) > 180 or name in ('.', '..')
            or name.endswith(('.', ' ')) or PureWindowsPath(name).is_reserved()
            or any(not c.isprintable() or c in '<>:"/\\|?*' for c in name)):
        raise ValueError('Choose a file with a supported filename.')
    return name


@dataclass
class DropRequest:
    id: str
    ip: str
    name: str
    size: int
    deadline: float
    state: str = 'pending'


class PhoneDrop:
    def __init__(self, address, directory, port=0):
        if not private_address(address):
            raise ValueError('Select a private LAN IPv4 address.')
        self.directory = Path(directory).resolve(strict=True)
        if not self.directory.is_dir():
            raise ValueError('Select an existing destination folder.')
        self.code = secrets.token_urlsafe(24)
        self.lock = Lock()
        self.requests = {}
        self.stopped = False
        self.server = DropHTTPServer((address, port), DropHandler)
        self.server.drop = self
        self.host = f'{address}:{self.server.server_port}'
        self.thread = Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.1}, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f'http://{self.host}/{self.code}/'

    def new_request(self, ip, name, size):
        name = valid_name(name)
        if type(size) is not int or not 0 <= size <= MAX_UPLOAD:
            raise ValueError('A file must be at most 32 GiB.')
        with self.lock:
            self.prune()
            if self.stopped or len(self.requests) >= MAX_PENDING:
                raise ValueError('The receiver is busy. Try again shortly.')
            request = DropRequest(secrets.token_urlsafe(24), ip, name, size, monotonic() + EXPIRY)
            self.requests[request.id] = request
            return request.id

    def prune(self):
        now = monotonic()
        for key in tuple(self.requests):
            request = self.requests[key]
            if request.state != 'receiving' and request.deadline < now:
                del self.requests[key]

    def snapshot(self):
        with self.lock:
            self.prune()
            return [(r.id, r.ip, r.name, r.size, r.state) for r in self.requests.values()]

    def decide(self, request_id, accepted):
        with self.lock:
            self.prune()
            request = self.requests.get(request_id)
            if self.stopped or request is None or request.state != 'pending':
                return False
            if accepted and shutil.disk_usage(self.directory).free < request.size:
                raise OSError('The destination drive has insufficient free space.')
            request.state = 'approved' if accepted else 'rejected'
            request.deadline = monotonic() + EXPIRY
            return True

    def request_state(self, request_id, ip):
        with self.lock:
            self.prune()
            request = self.requests.get(request_id)
            return request.state if request is not None and request.ip == ip else None

    def begin_upload(self, request_id, ip, length):
        with self.lock:
            self.prune()
            request = self.requests.get(request_id)
            if (self.stopped or request is None or request.ip != ip or request.state != 'approved'
                    or request.size != length):
                return None
            request.state = 'receiving'
            return request

    def receive(self, request, stream):
        staged = None
        try:
            with NamedTemporaryFile(dir=self.directory, prefix='.zftp-phone-', suffix='.part', delete=False) as output:
                staged = Path(output.name)
                remaining = request.size
                while remaining:
                    data = stream.read(min(remaining, 256 * 1024))
                    if not data:
                        raise OSError('The phone disconnected before sending the whole file.')
                    output.write(data)
                    remaining -= len(data)
            with self.lock:
                if self.stopped:
                    raise OSError('Phone Drop was stopped.')
                original = Path(request.name)
                for number in range(1, 10001):
                    name = request.name if number == 1 else f'{original.stem} ({number}){original.suffix}'
                    destination = self.directory / name
                    try:
                        if os.name == 'nt':
                            staged.rename(destination)
                        else:
                            os.link(staged, destination)
                    except FileExistsError:
                        continue
                    request.state = 'received'
                    request.deadline = monotonic() + 60
                    return
                raise OSError('There are too many files with this name.')
        except OSError:
            with self.lock:
                request.state = 'failed'
                request.deadline = monotonic() + 60
            raise
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)

    def stop(self):
        with self.lock:
            self.stopped = True
        self.server.shutdown()
        self.server.close_clients()
        self.server.server_close()
        self.thread.join(timeout=2)


class DropHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler):
        self.handlers = BoundedSemaphore(24)
        self.sockets = set()
        self.sockets_lock = Lock()
        super().__init__(address, handler)

    def verify_request(self, request, address):
        return private_address(address[0])

    def process_request(self, request, address):
        if not self.handlers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        with self.sockets_lock:
            self.sockets.add(request)
        try:
            super().process_request(request, address)
        except BaseException:
            self.release(request)
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.release(request)

    def release(self, request):
        with self.sockets_lock:
            self.sockets.discard(request)
        self.handlers.release()

    def close_clients(self):
        with self.sockets_lock:
            for request in self.sockets:
                try:
                    request.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


class DropHandler(BaseHTTPRequestHandler):
    timeout = 10

    def log_message(self, *_):
        pass

    def route(self):
        if self.headers.get('Host') != self.server.drop.host:
            return None
        parts = self.path.split('/')
        if len(parts) < 3 or not parts[1].isascii() or not secrets.compare_digest(parts[1], self.server.drop.code):
            return None
        return parts[2:]

    def reply(self, status, *, page=False, **fields):
        data = PAGE if page else json.dumps(fields).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8' if page else 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; connect-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        route = self.route()
        if route == ['']:
            self.reply(HTTPStatus.OK, page=True)
            return
        if route and len(route) == 2 and route[0] == 'request':
            state = self.server.drop.request_state(route[1], self.client_address[0])
            if state is not None:
                self.reply(HTTPStatus.OK, state=state)
                return
        self.reply(HTTPStatus.NOT_FOUND, error='This Phone Drop link or request is no longer available.')

    def do_POST(self):
        route = self.route()
        drop = self.server.drop
        if route is None:
            self.reply(HTTPStatus.NOT_FOUND, error='This Phone Drop link is unavailable.')
            return
        if self.headers.get('Origin') != 'http://' + drop.host:
            self.reply(HTTPStatus.FORBIDDEN, error='Open the Phone Drop page to send a file.')
            return
        try:
            length = int(self.headers.get('Content-Length', '-1'))
            if length < 0 or self.headers.get('Transfer-Encoding'):
                raise ValueError('The request must declare its size.')
            if route == ['request'] and length <= 4096 and self.headers.get_content_type() == 'application/json':
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('Invalid upload request.')
                identifier = drop.new_request(self.client_address[0], body.get('name'), body.get('size'))
                self.reply(HTTPStatus.CREATED, id=identifier)
                return
            if len(route) == 3 and route[0] == 'request' and route[2] == 'file':
                request = drop.begin_upload(route[1], self.client_address[0], length)
                if request is None:
                    self.reply(HTTPStatus.FORBIDDEN, error='The PC has not approved this upload or the size changed.')
                    return
                drop.receive(request, self.rfile)
                self.reply(HTTPStatus.CREATED, state='received')
                return
        except (ValueError, OSError) as error:
            self.reply(HTTPStatus.BAD_REQUEST, error=str(error) if isinstance(error, ValueError) else 'The PC could not receive the whole file.')
            return
        self.reply(HTTPStatus.BAD_REQUEST, error='Invalid upload request.')
