"""ws_token backend — drive game tasks over a direct WebSocket connection using
an ADB-scraped login ticket, with no App / screen / OCR.

Foundation layer (Step 1): codec (framing+XOR+protobuf), creds, WSGameClient.
See docs/WS_TOKEN_BACKEND_PLAN.md for the roadmap.
"""
