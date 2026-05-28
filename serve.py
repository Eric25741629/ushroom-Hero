#!/usr/bin/env python3
"""
簡單的本機 HTTP server，會為所有回應加入 CORS headers，方便前端 fetch 本機 JSON
使用：
  python serve.py -p 8000 -b 127.0.0.1
或
  python -m serve.py

按 Ctrl+C 停止。
"""
from http.server import SimpleHTTPRequestHandler, HTTPServer
import argparse

class CORSRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        # 允許跨來源存取（只用於本機測試），生產環境請謹慎使用
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()


def run(port: int = 8000, bind: str = '127.0.0.1'):
    server_address = (bind, port)
    httpd = HTTPServer(server_address, CORSRequestHandler)
    print(f"Serving HTTP on {bind} port {port} (http://{bind}:{port}/) ...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nShutting down server...')
        httpd.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Simple HTTP server with CORS for local testing')
    parser.add_argument('-p', '--port', type=int, default=8000, help='Port to bind to (default: 8000)')
    parser.add_argument('-b', '--bind', default='127.0.0.1', help='Bind address (default: 127.0.0.1)')
    args = parser.parse_args()
    run(args.port, args.bind)
