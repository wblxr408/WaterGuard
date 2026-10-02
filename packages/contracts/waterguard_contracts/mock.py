"""Read-only, localhost fixture API. Not a task/job server or AGH host."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .core import FIXTURES, FixtureAdapter, check_bundle, events_after, load, require


def make_server(port=8000):
    check_bundle()
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, payload=b'', content_type='application/json; charset=utf-8'):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-WaterGuard-Mode', 'fixture')
        origin = self.headers.get('Origin')
        if origin in ('http://localhost:5173', 'http://127.0.0.1:5173'):
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Expose-Headers', 'X-WaterGuard-Mode')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        origin = self.headers.get('Origin')
        if origin in ('http://localhost:5173', 'http://127.0.0.1:5173'):
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Last-Event-ID')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_POST(self):
        self.respond(405, dict(mode='fixture', code='READ_ONLY_FIXTURE', message='Task creation, live execution and cancellation belong to the future backend.'))

    do_PUT = do_PATCH = do_DELETE = do_POST

    def do_GET(self):
        try:
            self.get()
        except (ValueError, KeyError) as exc:
            self.respond(400, dict(mode='fixture', code='INPUT_INVALID', message=str(exc)))

    def get(self):
        url = urlsplit(self.path)
        query = parse_qs(url.query, keep_blank_values=True)
        path = url.path
        if path.endswith('/series'):
            require(set(query) <= {'node_id','start_s','end_s'}, 'Unknown query parameter')
            require(all(len(v) == 1 for v in query.values()), 'Duplicate query parameter')
        else:
            require(not query, 'This endpoint accepts no query parameters')
        fixed = {
            '/api/fixture': 'task.json',
            '/api/networks/wg-fixture-loop-v1/scene': 'scene.json',
            '/api/tasks/task-day1-closure': 'task.json',
            '/api/runs/run-day1-fixture': 'run.json',
            '/api/runs/run-day1-fixture/evidence': 'evidence-manifest.json',
        }
        if path in fixed:
            return self.respond(200, load(FIXTURES / fixed[path]))
        if path == '/api/networks':
            return self.respond(200, [load(FIXTURES/'network-manifest.json')])
        if path == '/api/runs/run-day1-fixture/candidates':
            return self.respond(200, [dict(plan=load(FIXTURES/f'candidate-{s}.json'), verification=load(FIXTURES/f'verification-{s}.json')) for s in ('a','b')])
        if path == '/api/runs/run-day1-fixture/events':
            events = [json.loads(line) for line in (FIXTURES/'events.jsonl').read_text(encoding='utf-8').splitlines()]
            remaining = events_after(events, int(self.headers.get('Last-Event-ID', '0')))
            if not remaining:
                return self.respond(204)
            data = ''.join(f'id: {e["sequence"]}\nevent: {e["type"]}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n' for e in remaining)
            return self.respond(200, data.encode('utf-8'), 'text/event-stream; charset=utf-8')
        parts = path.strip('/').split('/')
        if len(parts) == 4 and parts[:2] == ['api','simulations'] and parts[3] == 'series':
            if parts[2] not in ('simulation-a','simulation-b'):
                return self.respond(404, dict(mode='fixture', code='NOT_FOUND'))
            kwargs = {key: values[0] if key == 'node_id' else int(values[0]) for key, values in query.items()}
            return self.respond(200, FixtureAdapter().get_series(parts[2], **kwargs))
        # Only indexed fixture artifacts can be read. No arbitrary filesystem path.
        if len(parts) == 3 and parts[:2] == ['api','artifacts']:
            evidence = load(FIXTURES/'evidence-manifest.json')
            allowed = set(evidence['hashes']) | {'evidence-manifest.json'}
            if parts[2] in allowed:
                content_type = 'application/json; charset=utf-8' if parts[2].endswith('.json') else 'text/plain; charset=utf-8'
                return self.respond(200, (FIXTURES/parts[2]).read_bytes(), content_type)
        self.respond(404, dict(mode='fixture', code='NOT_FOUND'))
