"""Read-only public-model and computed-result API. No AGH/live write endpoints."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .adapter import EpanetAdapter, network_assets
from .datasets import NETWORKS, ROOT, manifest
from waterguard_contracts.core import load, require, resolve


def make_server(output_root=ROOT/'artifacts/hydraulics', port=8001):
    index=load(Path(output_root)/'index.json')
    require(index['mode']=='benchmark','Expected benchmark index')
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.output_root=Path(output_root)
    server.index=index
    if index['schema_version']=='0.3.0':
        from .compiler import assets
        server.assets={key:assets(key) for key in NETWORKS}
    else:
        server.assets={key:network_assets(key) for key in NETWORKS}
    return server


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def respond(self,status,payload):
        body=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-WaterGuard-Mode','benchmark')
        origin=self.headers.get('Origin')
        if origin in ('http://localhost:5173','http://127.0.0.1:5173'):
            self.send_header('Access-Control-Allow-Origin',origin)
            self.send_header('Vary','Origin')
            self.send_header('Access-Control-Expose-Headers','X-WaterGuard-Mode')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.respond(405,dict(mode='benchmark',code='READ_ONLY_BENCHMARK'))

    do_DELETE=do_PUT=do_PATCH=do_POST

    def do_GET(self):
        try:
            self.get()
        except (ValueError,KeyError) as exc:
            self.respond(400,dict(mode='benchmark',code='INPUT_INVALID',message=str(exc)))
        except FileNotFoundError:
            self.respond(404,dict(mode='benchmark',code='ARTIFACT_NOT_FOUND'))

    def get(self):
        url=urlsplit(self.path)
        parts=url.path.strip('/').split('/')
        query=parse_qs(url.query,keep_blank_values=True)
        if len(parts)==4 and parts[:2]==['api','simulations'] and parts[3]=='series':
            require(set(query)<={'node_id','start_s','end_s'} and all(len(v)==1 for v in query.values()),'Invalid query')
        else:
            require(not query,'Unexpected query')
        if parts==['api','networks']:
            return self.respond(200,[assets[1] for assets in self.server.assets.values()])
        if parts==['api','benchmarks','current']:
            return self.respond(200,self.server.index)
        if len(parts)==4 and parts[:2]==['api','networks'] and parts[2] in self.server.assets:
            if parts[3] in ('scene','actions'):
                return self.respond(200,self.server.assets[parts[2]][2 if parts[3]=='scene' else 3])
        ids={case['simulation_id'] for case in self.server.index['cases']}
        if len(parts) in (3,4) and parts[:2]==['api','simulations'] and parts[2] in ids:
            sid=parts[2]
            endpoint='result' if len(parts)==3 else parts[3]
            if endpoint=='series':
                kwargs={k:v[0] if k=='node_id' else int(v[0]) for k,v in query.items()}
                from .runtime import PhysicalAdapter
                adapter=PhysicalAdapter if self.server.index['schema_version']=='0.3.0' else EpanetAdapter
                return self.respond(200,adapter(self.server.output_root).get_series(sid,**kwargs))
            if endpoint in ('result','verification','evidence','task','plan','physics','mapping'):
                file={'evidence':'evidence-manifest.json','physics':'physical-checks.json','mapping':'entity-map.json'}.get(endpoint,endpoint+'.json')
                return self.respond(200,load(resolve(self.server.output_root,sid+'/'+file)))
        self.respond(404,dict(mode='benchmark',code='NOT_FOUND'))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/hydraulics')
    parser.add_argument('--port',type=int,default=8001)
    args=parser.parse_args()
    with make_server(args.output,args.port) as server:
        print(f'Computed BENCHMARK data: http://127.0.0.1:{server.server_port}/api/networks',flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
