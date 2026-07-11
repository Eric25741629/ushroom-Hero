"""final_v1 mining planner — public entry: plan_final_v1."""


def __getattr__(name):
    # lazy: scoring/types 可獨立 import，planner 到用時才載入
    if name == "plan_final_v1":
        from .planner import plan_final_v1
        return plan_final_v1
    raise AttributeError(name)


__all__ = ["plan_final_v1"]
