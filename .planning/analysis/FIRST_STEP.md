# First Implementation Step Recommendation

## Analysis Context

Based on reviewing:
- `.planning/PROJECT.md` - Project goals and constraints
- `.planning/ROADMAP.md` - Existing phase structure
- Codebase architecture scan

---

## Current Roadmap Status

| Phase | Status |
|-------|--------|
| Phase 1: FSM Core | ❌ Not started |
| Phase 1: MuMu Control | 🟡 2/3 complete |
| Phase 2: OCR Ops | ❌ Not started |
| Phase 3: Stability | ❌ Not started |
| Phase 4: Scheduler | ❌ Not started |
| Phase 5: Web UI | ❌ Not started |
| Phase 6-7: Strategy/Host | ❌ Not started |
| Phase 4: Bi-weekly Dungeon | ✅ Complete |

---

## Recommendation: **Phase 1 - FSM Core Implementation**

### Why This First?

```
Dependency Graph:

Phase 1 (FSM)
    │
    ├─► Phase 3 (Stability) needs state tracking
    ├─► Phase 5 (Web UI) needs status reporting
    ├─► Phase 6 (Strategy) needs state transitions
    └─► All recovery logic needs valid states
```

### Business Value

1. **Foundation for Stability**: Cannot build auto-recovery without knowing "what broken looks like"
2. **Observability**: Enables debugging device issues remotely
3. **Low Risk**: Additive change, doesn't break existing flow
4. **Enables Downstream Work**: Unblocks 4+ phases

---

## Proposed Implementation Plan

### Step 1: Define State Enum (bot_state.py)

```python
class DeviceState(Enum):
    IDLE = "idle"              # Waiting for wake-up
    RUNNING = "running"        # Normal operation
    RECOVERING = "recovering"  # Auto-recovery in progress
    PAUSED = "paused"          # Manual pause (web UI)
    ERROR = "error"           # Fatal error, needs intervention
```

### Step 2: State Machine Class

```python
class DeviceStateMachine:
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.current_state = DeviceState.IDLE
        self.transition_history = []
    
    def transition(self, new_state: DeviceState, trigger: str) -> bool:
        # Validate transition
        if not self._is_valid_transition(new_state):
            logger.error(f"Invalid transition")
            return False
        
        # Record with timestamp
        self.transition_history.append({
            "from": self.current_state,
            "to": new_state,
            "trigger": trigger,
            "timestamp": datetime.now()
        })
        
        self.current_state = new_state
        return True
```

### Step 3: Integration Points

| Location | Change |
|----------|--------|
| `new_main_v2.py` device loop | Add state transitions at key points |
| `bot_state.py` | Embed FSM instance per device |
| `control_panel_app.py` | Expose current state via API |

### Step 4: Timeout Detection

```python
def check_state_timeout(self, max_duration_by_state: dict):
    if self.transition_history:
        last_transition = self.transition_history[-1]
        duration = datetime.now() - last_transition["timestamp"]
        
        if duration > max_duration_by_state[self.current_state]:
            # Auto-transition to RECOVERING
            self.transition(DeviceState.RECOVERING, "timeout")
```

---

## Files to Create/Modify

### New Files
- `core/state_machine.py` - FSM implementation

### Modified Files
- `bot_state.py` - Add FSM instance
- `new_main_v2.py` - Add transition calls
- `control_panel_app.py` - Expose state via API

---

## Success Criteria

| Criterion | Verification |
|-----------|-------------|
| ✅ Standard states defined | Check enum in code |
| ✅ Transitions logged | Inspect logs for transition entries |
| ✅ Invalid transitions blocked | Attempt illegal transition, verify rejection |
| ✅ Timeout auto-recovery works | Force device into state, wait for timeout |

---

## Estimated Effort

| Task | Time |
|------|------|
| FSM core implementation | 2-3 hours |
| Integration with bot_state | 1 hour |
| Add transitions to main loop | 2 hours |
| Testing & verification | 1 hour |
| **Total** | **~6 hours** |

---

## Alternative: Quick Win Option

If you want something faster, consider:

### Option B: Review Phase 1 Deliverables
- Already 2/3 done
- Immediate value for emulator stability
- Lower risk (localized change)

---

## My Recommendation

**Go with Phase 1 (FSM)** because:
1. It's the foundation your roadmap was built on
2. Enables multiple downstream phases
3. ~6 hours of work for long-term value
4. Low risk, additive change

---

*Ready to proceed? I can create detailed PLAN.md for this phase.*
