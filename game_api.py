"""
Flask 網站 API - 透過網頁介面控制遊戲自動化
"""
from flask import Flask, render_template, request, jsonify, send_from_directory
import json
import logging
from event_manager import (
    get_event_manager, EventType, EventPriority, 
    emit, GameEvent
)
from datetime import datetime

app = Flask(__name__)

# ============ 配置 ============
app.config['JSON_SORT_KEYS'] = False
logger = logging.getLogger(__name__)

# 獲取事件管理器
event_manager = get_event_manager(logger)

# ============ API 路由 ============

@app.route('/', methods=['GET'])
def dashboard():
    """主控制面板"""
    return render_template('dashboard.html')

@app.route('/api/emit', methods=['POST'])
def api_emit_event():
    """API: 發出事件"""
    try:
        data = request.get_json()
        
        # 驗證必須字段
        if not data.get('event_type') or not data.get('device_ip'):
            return jsonify({
                "status": "error",
                "message": "缺少必要欄位: event_type 或 device_ip"
            }), 400
        
        # 取得事件類型
        try:
            event_type = EventType[data['event_type'].upper()]
        except KeyError:
            return jsonify({
                "status": "error",
                "message": f"未知的事件類型: {data['event_type']}"
            }), 400
        
        # 取得優先級
        priority = EventPriority[data.get('priority', 'NORMAL').upper()]
        
        # 建立並發出事件
        event = GameEvent(
            event_type=event_type,
            device_ip=data['device_ip'],
            priority=priority,
            data=data.get('data', {})
        )
        event_manager.emit_event(event)
        
        return jsonify({
            "status": "success",
            "event_id": event.event_id,
            "message": f"事件已發出: {event_type.value}"
        }), 200
    
    except Exception as e:
        logger.error(f"發出事件時出錯: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/events/next', methods=['GET'])
def api_get_next_event():
    """API: 獲取下一個事件（主程式輪詢用）"""
    try:
        timeout = int(request.args.get('timeout', 1))
        event = event_manager.get_next_event(timeout=timeout)
        
        if event:
            return jsonify({
                "status": "success",
                "event": event.to_dict()
            }), 200
        else:
            return jsonify({
                "status": "empty",
                "message": "隊列為空"
            }), 204
    
    except Exception as e:
        logger.error(f"獲取事件時出錯: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/stats', methods=['GET'])
def api_get_stats():
    """API: 獲取統計信息"""
    try:
        stats = event_manager.get_stats()
        return jsonify({
            "status": "success",
            "data": stats
        }), 200
    except Exception as e:
        logger.error(f"獲取統計信息時出錯: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/history', methods=['GET'])
def api_get_history():
    """API: 獲取事件歷史"""
    try:
        device_ip = request.args.get('device_ip')
        limit = int(request.args.get('limit', 100))
        
        history = event_manager.get_history(device_ip, limit)
        return jsonify({
            "status": "success",
            "count": len(history),
            "events": history
        }), 200
    except Exception as e:
        logger.error(f"獲取歷史時出錯: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/devices/state', methods=['GET', 'POST'])
def api_device_state():
    """API: 獲取/設定設備狀態"""
    try:
        if request.method == 'GET':
            device_ip = request.args.get('device_ip')
            if not device_ip:
                return jsonify({
                    "status": "error",
                    "message": "缺少 device_ip 參數"
                }), 400
            
            state = event_manager.get_device_state(device_ip)
            return jsonify({
                "status": "success",
                "device_ip": device_ip,
                "state": state
            }), 200
        
        else:  # POST
            data = request.get_json()
            device_ip = data.get('device_ip')
            state = data.get('state')
            
            if not device_ip or not state:
                return jsonify({
                    "status": "error",
                    "message": "缺少 device_ip 或 state"
                }), 400
            
            event_manager.set_device_state(device_ip, state)
            return jsonify({
                "status": "success",
                "message": f"設備狀態已設定為: {state}"
            }), 200
    
    except Exception as e:
        logger.error(f"操作設備狀態時出錯: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/event-types', methods=['GET'])
def api_get_event_types():
    """API: 獲取所有事件類型"""
    try:
        event_types = [
            {
                "name": et.name,
                "value": et.value,
                "description": {
                    "START_GAME": "啟動遊戲",
                    "STOP_GAME": "停止遊戲",
                    "TRIGGER_FARMING": "觸發農業",
                    "TRIGGER_PARKING": "觸發停車",
                    "TRIGGER_BATTLE": "觸發戰鬥",
                    "TRIGGER_MINING": "觸發挖礦",
                    "TRIGGER_MISSION": "觸發任務",
                    "PAUSE_DEVICE": "暫停設備",
                    "RESUME_DEVICE": "恢復設備",
                    "FORCE_WAKE": "強制喚醒",
                    "SHUTDOWN": "關閉程式",
                }.get(et.name, "")
            }
            for et in EventType
        ]
        
        priorities = [
            {"name": p.name, "value": p.value}
            for p in EventPriority
        ]
        
        return jsonify({
            "status": "success",
            "event_types": event_types,
            "priorities": priorities
        }), 200
    except Exception as e:
        logger.error(f"獲取事件類型時出錯: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# ============ 快捷命令 ============

@app.route('/api/quick/farm/<device_ip>', methods=['POST'])
def quick_farm(device_ip):
    """快捷命令: 觸發農業"""
    try:
        event_id = emit(EventType.TRIGGER_FARMING, device_ip, 
                       priority=EventPriority.NORMAL)
        return jsonify({
            "status": "success",
            "event_id": event_id,
            "message": f"已觸發 {device_ip} 的農業"
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/quick/park/<device_ip>', methods=['POST'])
def quick_park(device_ip):
    """快捷命令: 觸發停車"""
    try:
        event_id = emit(EventType.TRIGGER_PARKING, device_ip,
                       priority=EventPriority.HIGH)
        return jsonify({
            "status": "success",
            "event_id": event_id,
            "message": f"已觸發 {device_ip} 的停車"
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/quick/wake/<device_ip>', methods=['POST'])
def quick_wake(device_ip):
    """快捷命令: 強制喚醒"""
    try:
        event_id = emit(EventType.FORCE_WAKE, device_ip,
                       priority=EventPriority.HIGH)
        return jsonify({
            "status": "success",
            "event_id": event_id,
            "message": f"已強制喚醒 {device_ip}"
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/quick/pause/<device_ip>', methods=['POST'])
def quick_pause(device_ip):
    """快捷命令: 暫停設備"""
    try:
        event_id = emit(EventType.PAUSE_DEVICE, device_ip,
                       priority=EventPriority.HIGH)
        return jsonify({
            "status": "success",
            "event_id": event_id,
            "message": f"已暫停 {device_ip}"
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/quick/resume/<device_ip>', methods=['POST'])
def quick_resume(device_ip):
    """快捷命令: 恢復設備"""
    try:
        event_id = emit(EventType.RESUME_DEVICE, device_ip,
                       priority=EventPriority.HIGH)
        return jsonify({
            "status": "success",
            "event_id": event_id,
            "message": f"已恢復 {device_ip}"
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ============ 健康檢查 ============

@app.route('/health', methods=['GET'])
def health():
    """健康檢查"""
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat()
    }), 200

# ============ 錯誤處理 ============

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "status": "error",
        "message": "找不到資源"
    }), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        "status": "error",
        "message": "伺服器內部錯誤"
    }), 500

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    app.run(host='0.0.0.0', port=5000, debug=True)
