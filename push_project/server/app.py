# Python Push Server for 菇勇者
from flask import Flask, request, jsonify, send_from_directory
from pywebpush import webpush, WebPushException
import os, json, time
from pathlib import Path
import base64

app = Flask(__name__)

# Load .env file if exists (support multiline PEM for VAPID_PRI)
env_file = Path(__file__).parent / '.env'
if env_file.exists():
    try:
        raw = env_file.read_text(encoding='utf-8')
        lines = raw.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].rstrip('\n')
            s = line.strip()
            if not s or s.startswith('#'):
                i += 1
                continue

            # Special-case: multiline PEM for VAPID_PRI
            if s.startswith('VAPID_PRI=') and '-----BEGIN' in s:
                # collect PEM lines until END marker
                key, rest = s.split('=', 1)
                pem_lines = [rest]
                i += 1
                while i < len(lines):
                    l = lines[i].rstrip('\n')
                    pem_lines.append(l)
                    if '-----END PRIVATE KEY-----' in l:
                        break
                    i += 1
                full = '\n'.join(pem_lines)
                os.environ.setdefault('VAPID_PRI', full)
                print('ℹ️ Loaded multiline VAPID_PRI from .env')
                i += 1
                continue

            if '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key, value)
            i += 1
    except Exception as e:
        print(f'⚠️ Failed to parse .env: {e}')

# VAPID keys: set from env or generate on first run
VAPID_PUBLIC = os.environ.get('VAPID_PUB', '')
VAPID_PRIVATE = os.environ.get('VAPID_PRI', '')

# Ensure VAPID_PRIVATE is in base64url (raw 32-byte) form expected by pywebpush.
# If user provided a PEM, convert it to raw private scalar and base64url-encode it.
VAPID_PRIVATE_B64 = VAPID_PRIVATE
if VAPID_PRIVATE_B64 and VAPID_PRIVATE_B64.strip().startswith('-----BEGIN'):
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend

        priv = serialization.load_pem_private_key(VAPID_PRIVATE_B64.encode('utf-8'), password=None, backend=default_backend())
        pv = priv.private_numbers().private_value
        # P-256 should be 32 bytes
        raw = pv.to_bytes(32, byteorder='big')
        VAPID_PRIVATE_B64 = base64.urlsafe_b64encode(raw).decode('utf-8').rstrip('=')
        print('ℹ️ Converted PEM VAPID private key to base64url (raw) format for pywebpush')
    except Exception as e:
        print(f'⚠️ Failed to convert PEM VAPID private key to base64url: {e}')
        # keep original VAPID_PRIVATE_B64 (may fail later)

# Diagnostic: print info about the private key content/length
try:
    import re
    print(f'ℹ️ VAPID_PRIVATE_B64 length={len(VAPID_PRIVATE_B64)}')
    print(f'ℹ️ VAPID_PUBLIC length={len(VAPID_PUBLIC)}')
    snippet = (VAPID_PRIVATE_B64[:60] + '...') if len(VAPID_PRIVATE_B64) > 60 else VAPID_PRIVATE_B64
    print(f'ℹ️ VAPID_PRIVATE_B64 snippet: {repr(snippet)}')
    if not re.match(r'^[A-Za-z0-9\-_]+$', VAPID_PRIVATE_B64):
        print('⚠️ VAPID_PRIVATE_B64 contains non-base64url characters')
except Exception:
    pass

if not VAPID_PUBLIC or not VAPID_PRIVATE:
    print('⚠️  VAPID keys not found in environment. Generating temporary keys...')
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization
        import base64
        
        # Generate EC key pair
        private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
        public_key = private_key.public_key()
        
        # Serialize keys
        private_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        public_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint
        )
        
        VAPID_PUBLIC = base64.urlsafe_b64encode(public_bytes).decode('utf-8').rstrip('=')
        VAPID_PRIVATE = private_bytes.decode('utf-8')
        
        print('✅ Generated VAPID keys (save these for production):')
        print(f'   VAPID_PUB={VAPID_PUBLIC}')
        print(f'   VAPID_PRI={VAPID_PRIVATE[:50]}...')
        print('   Set as environment variables to reuse:')
        print(f'   $env:VAPID_PUB = "{VAPID_PUBLIC}"')
        print(f'   $env:VAPID_PRI = "{VAPID_PRIVATE[:50]}..."')
    except Exception as e:
        print(f'❌ Failed to generate VAPID keys: {e}')
        print('   Using placeholder keys (PUSH WILL NOT WORK)')
        VAPID_PUBLIC = 'PLACEHOLDER_PUBLIC_KEY'
        VAPID_PRIVATE = 'PLACEHOLDER_PRIVATE_KEY'

# In-memory subscription storage (replace with DB in production)
SUBSCRIPTIONS = []
SUBS_FILE = Path(__file__).parent / 'subscriptions.json'

# Load persisted subscriptions if present (so restarts keep subscriptions)
if SUBS_FILE.exists():
    try:
        with open(SUBS_FILE, 'r', encoding='utf-8') as f:
            SUBSCRIPTIONS = json.load(f)
            print(f'ℹ️ Loaded {len(SUBSCRIPTIONS)} subscriptions from {SUBS_FILE}')
    except Exception as e:
        print(f'⚠️ Failed to load subscriptions file: {e}')

# Serve static files from ../web
WEB_DIR = Path(__file__).parent.parent / 'web'

@app.route('/')
def index():
    return send_from_directory(WEB_DIR, '菇勇者.html')

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory(WEB_DIR, path)

@app.route('/vapidPublicKey')
def vapid_pub():
    return VAPID_PUBLIC

@app.route('/save-subscription', methods=['POST'])
def save_sub():
    try:
        sub = request.get_json()
        if not sub or 'endpoint' not in sub:
            return ('Invalid subscription', 400)
        SUBSCRIPTIONS.append(sub)
        # persist to disk
        try:
            with open(SUBS_FILE, 'w', encoding='utf-8') as f:
                json.dump(SUBSCRIPTIONS, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f'⚠️ Failed to persist subscription: {e}')
        # 詳細日誌方便除錯
        try:
            print(f'✅ Subscription saved. Total: {len(SUBSCRIPTIONS)}')
            print('   ->', json.dumps(sub)[:1000])
        except Exception:
            print('✅ Subscription saved. (could not stringify payload)')
        return ('', 201)
    except Exception as e:
        print(f'❌ Error saving subscription: {e}')
        return ('error', 500)


@app.route('/subscriptions', methods=['GET'])
def list_subscriptions():
    """Debug endpoint: 回傳目前儲存的 subscription 列表與數量（僅供本機測試）"""
    try:
        return jsonify({'count': len(SUBSCRIPTIONS), 'subscriptions': SUBSCRIPTIONS})
    except Exception as e:
        return jsonify({'count': len(SUBSCRIPTIONS), 'error': str(e)})

@app.route('/send-push', methods=['POST'])
def send_push():
    """Send push notification to all subscribed clients"""
    data = request.get_json() or {}
    payload = json.dumps(data if data else {'title':'菇勇者提醒','body':'車位快到時間'})

    results = []
    for sub in SUBSCRIPTIONS[:]:
        try:
            # Normalize subscription keys (some browsers send base64url without padding)
            def _norm_b64url(s):
                if not s:
                    return s
                s = s.replace('-', '+').replace('_', '/')
                pad = (-len(s)) % 4
                if pad:
                    s = s + ('=' * pad)
                return s

            sub_norm = dict(sub)
            keys = sub_norm.get('keys', {})
            if isinstance(keys, dict):
                if 'p256dh' in keys:
                    keys['p256dh'] = _norm_b64url(keys['p256dh'])
                if 'auth' in keys:
                    keys['auth'] = _norm_b64url(keys['auth'])
                sub_norm['keys'] = keys
            else:
                sub_norm['keys'] = keys

            # debug log lengths
            try:
                ln = lambda v: (len(v) if v else 0)
                print(f"ℹ️ Pushing to endpoint (len p256dh)={ln(keys.get('p256dh'))} (len auth)={ln(keys.get('auth'))}")
            except Exception:
                pass

            # Diagnostic: try decoding vapid private key before calling pywebpush
            try:
                pad = (-len(VAPID_PRIVATE_B64)) % 4
                test = VAPID_PRIVATE_B64 + ('=' * pad)
                decoded = base64.urlsafe_b64decode(test)
                print(f'ℹ️ vapid_private_key base64 decoded ok (bytes len={len(decoded)})')
            except Exception as _e:
                print('❌ vapid_private_key base64 decode failed:', _e)
                # include the string length and sample
                print(f"   len={len(VAPID_PRIVATE_B64)} sample={repr(VAPID_PRIVATE_B64[:40])}")
                raise

            # Provider-specific TTL handling: WNS may reject conflicting TTL/cache headers.
            # Observed: ttl=0 still caused X-WNS-Cache-Policy conflict; omit ttl param for WNS endpoints.
            ttl = 60
            endpoint_l = (sub_norm.get('endpoint') or '').lower()
            is_wns = ('wns' in endpoint_l) or ('notify.windows.com' in endpoint_l)
            if is_wns:
                print('ℹ️ Detected WNS endpoint, omitting ttl parameter to avoid cache-policy conflicts')

            # Build args and call webpush; omit ttl for WNS
            webpush_kwargs = {
                'subscription_info': sub_norm,
                'data': payload,
                'vapid_private_key': VAPID_PRIVATE_B64,
                'vapid_claims': {"sub": "mailto:you@example.com"}
            }
            if not is_wns:
                webpush_kwargs['ttl'] = ttl

            print(f"ℹ️ webpush kwargs: {{'ttl': webpush_kwargs.get('ttl', None)}}")
            webpush(**webpush_kwargs)

            results.append({'ok': True})
        except WebPushException as ex:
            # 詳細錯誤輸出，方便除錯 key/encoding 問題
            try:
                resp = ex.response
                status = resp.status_code if resp is not None else 'N/A'
                body = resp.text if resp is not None else ''
            except Exception:
                status = 'N/A'
                body = ''
            print(f'❌ Push failed (WebPushException): status={status} err={ex} body={body}')
            results.append({'ok': False, 'err': str(ex), 'status': status, 'body': body})
            # Remove invalid subscription
            try:
                if resp is not None and status in [404, 410]:
                    SUBSCRIPTIONS.remove(sub)
                    try:
                        with open(SUBS_FILE, 'w', encoding='utf-8') as f:
                            json.dump(SUBSCRIPTIONS, f, ensure_ascii=False, indent=2)
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception as ex:
            import traceback
            traceback.print_exc()
            print(f'❌ Unexpected error during push: {ex}')
            results.append({'ok': False, 'err': str(ex)})

    return jsonify({'results': results, 'count': len(SUBSCRIPTIONS)})


@app.route('/send-push-single', methods=['POST'])
def send_push_single():
    """Send push to a single subscription for diagnostics.
    Accepts JSON: {"index": <int>} or {"endpoint": "..."} and optional {"payload": {...}}
    Returns detailed diagnostics and provider response.
    """
    body = request.get_json() or {}
    idx = body.get('index')
    endpoint = body.get('endpoint')
    payload = json.dumps(body.get('payload') or {'title':'菇勇者 單一測試','body':'診斷推播'})
    # optional ttl override for testing (integer seconds) - if provided, will be used even for WNS
    ttl_override = body.get('ttl')

    if idx is None and not endpoint:
        return jsonify({'error': 'provide index or endpoint'}), 400

    sub = None
    try:
        if idx is not None:
            sub = SUBSCRIPTIONS[int(idx)]
        else:
            sub = next((s for s in SUBSCRIPTIONS if s.get('endpoint') == endpoint), None)
    except Exception as e:
        return jsonify({'error': 'invalid index', 'detail': str(e)}), 400

    if not sub:
        return jsonify({'error': 'subscription not found'}), 404

    # Normalize keys as in bulk send
    def _norm_b64url(s):
        if not s: return s
        s = s.replace('-', '+').replace('_', '/')
        pad = (-len(s)) % 4
        if pad:
            s = s + ('=' * pad)
        return s

    sub_norm = dict(sub)
    keys = sub_norm.get('keys', {}) or {}
    if 'p256dh' in keys:
        keys['p256dh'] = _norm_b64url(keys['p256dh'])
    if 'auth' in keys:
        keys['auth'] = _norm_b64url(keys['auth'])
    sub_norm['keys'] = keys

    diag = {
        'endpoint': sub.get('endpoint'),
        'p256dh_len': len(keys.get('p256dh') or ''),
        'auth_len': len(keys.get('auth') or ''),
        'vapid_private_len': len(VAPID_PRIVATE_B64 or ''),
        'vapid_public_len': len(VAPID_PUBLIC or ''),
    }

    # Try decode vapid key
    try:
        pad = (-len(VAPID_PRIVATE_B64)) % 4
        test = (VAPID_PRIVATE_B64 or '') + ('=' * pad)
        decoded = base64.urlsafe_b64decode(test)
        diag['vapid_private_decoded_len'] = len(decoded)
        diag['vapid_private_decode_ok'] = True
    except Exception as e:
        diag['vapid_private_decode_ok'] = False
        diag['vapid_private_decode_err'] = str(e)

    # Attempt to send and capture detailed response
    result = {'diag': diag}
    try:
        webpush_kwargs = {
            'subscription_info': sub_norm,
            'data': payload,
            'vapid_private_key': VAPID_PRIVATE_B64,
            'vapid_claims': {"sub": "mailto:you@example.com"}
        }
        if ttl_override is not None:
            try:
                tval = int(ttl_override)
                webpush_kwargs['ttl'] = tval
                result.setdefault('diag', {})['ttl_used'] = tval
                print(f"ℹ️ TTL override provided: {tval}")
            except Exception:
                pass

        print(f"ℹ️ Sending single webpush kwargs: {{'ttl': webpush_kwargs.get('ttl', None)}}")
        webpush(**webpush_kwargs)
        result['ok'] = True
    except WebPushException as ex:
        try:
            resp = ex.response
            status = resp.status_code if resp is not None else 'N/A'
            headers = dict(resp.headers) if resp is not None else {}
            body_text = resp.text if resp is not None else ''
        except Exception:
            status = 'N/A'
            headers = {}
            body_text = ''
        result['ok'] = False
        result['error'] = str(ex)
        result['status'] = status
        result['response_headers'] = headers
        result['response_body'] = body_text
    except Exception as ex:
        import traceback; tb = traceback.format_exc()
        result['ok'] = False
        result['error'] = str(ex)
        result['traceback'] = tb

    return jsonify(result)


@app.route('/clear-subscriptions', methods=['POST'])
def clear_subscriptions():
    """Clear all saved subscriptions (useful after rotating VAPID keys).
    WARNING: clients will need to re-subscribe after this.
    """
    SUBSCRIPTIONS.clear()
    try:
        with open(SUBS_FILE, 'w', encoding='utf-8') as f:
            json.dump(SUBSCRIPTIONS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'⚠️ Failed to persist cleared subscriptions: {e}')
    return jsonify({'ok': True, 'count': 0})

@app.route('/api/time')
def api_time():
    """Return server time in ms for client sync"""
    return jsonify({'server_ms': int(time.time() * 1000)})

if __name__ == '__main__':
    PORT = int(os.environ.get('PORT', 5000))
    print(f'🚀 Push server starting on http://127.0.0.1:{PORT}')
    print(f'📂 Serving web files from: {WEB_DIR}')
    if VAPID_PUBLIC == 'PLACEHOLDER_PUBLIC_KEY':
        print('⚠️  WARNING: Using placeholder VAPID keys. Generate real keys for production!')
    app.run(host='0.0.0.0', port=PORT, debug=True)
