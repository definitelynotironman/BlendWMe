"""Local integration-test fixture. Not an AI model and not shipped in the extension."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import sys
import time
from pathlib import Path

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def do_POST(self):
        payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if 'SLOW' in payload['messages'][-1]['content']:time.sleep(1)
        answer={'message':'Fixture step','done':False,'action':{'tool':'create_primitive','args':{'name':'FixtureCube','primitive':'cube'}},'guide':{'title':'Fixture','instruction':'Add a cube','why':'Test only','target':'ADD'}}
        if 'BAD' in payload['messages'][-1]['content']:answer['action']['tool']='exec'
        raw=json.dumps({'message':{'content':json.dumps(answer)}}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers()
        try:self.wfile.write(raw)
        except BrokenPipeError:pass

if __name__=='__main__':
    s=HTTPServer(('127.0.0.1',0),Handler)
    Path(sys.argv[1]).write_text(str(s.server_address[1]))
    s.serve_forever()
