"""药水效果兼容层：对齐最新 endstone.potion.Effect API，并兼容 0.11.x。

回退路径必须用控制台 dispatch_command，禁止 player.perform_command('/effect …')，
否则普通玩家会刷「命令权限级别错误：effect」。
"""
from __future__ import annotations

from typing import Any, Optional

Effect = None
EffectType = None

try:
    from endstone.potion import Effect as _Effect, EffectType as _EffectType

    Effect = _Effect
    EffectType = _EffectType
except ImportError:
    try:
        from endstone.effect import EffectType as _EffectType

        EffectType = _EffectType
    except ImportError:
        pass


def resolve_effect_type(name: Any) -> Any:
    """把名字解析成 EffectType / Identifier；失败返回 None。"""
    if name is None or EffectType is None:
        return None
    if not isinstance(name, str):
        return name
    raw = name.strip()
    if not raw:
        return None
    key = raw.lower()
    if hasattr(EffectType, "get"):
        got = EffectType.get(key)
        if got is not None:
            return got
        if not key.startswith("minecraft:"):
            got = EffectType.get(f"minecraft:{key}")
            if got is not None:
                return got
    attr = key.split(":")[-1].upper()
    return getattr(EffectType, attr, None)


def _effect_cmd_name(effect_type: Any) -> str:
    s = str(effect_type).strip()
    if ":" in s:
        s = s.split(":", 1)[1]
    # EffectType.FOO / Enum 形式
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return s.lower()


def _quote_player_name(name: str) -> str:
    name = str(name).replace('"', "")
    if " " in name or name != name.strip():
        return f'"{name}"'
    return name


def _dispatch_effect_command(player, cmd: str) -> bool:
    """以控制台权限执行 effect，避免玩家权限级别错误。"""
    try:
        server = getattr(player, "server", None)
        if server is not None and hasattr(server, "dispatch_command"):
            sender = getattr(server, "command_sender", None)
            if sender is not None:
                return bool(server.dispatch_command(sender, cmd))
    except Exception:
        pass
    return False


def apply_mob_effect(
    player,
    effect_type: Any,
    duration_ticks: int,
    amplifier: int = 0,
    ambient: bool = False,
    particles: bool = True,
    icon: bool = True,
) -> bool:
    """给玩家加效果。优先原生 Effect API，否则用控制台 /effect。"""
    if effect_type is None:
        return False
    resolved = resolve_effect_type(effect_type) if isinstance(effect_type, str) else effect_type
    if resolved is None:
        resolved = effect_type

    if Effect is not None and hasattr(player, "add_effect"):
        try:
            player.add_effect(
                Effect(
                    resolved,
                    int(duration_ticks) if duration_ticks is not None else None,
                    int(amplifier),
                    ambient=bool(ambient),
                    particles=bool(particles),
                    icon=bool(icon),
                )
            )
            return True
        except Exception:
            pass

    # Bedrock: /effect <player> <effect> [seconds] [amplifier] [hideParticles]
    seconds = max(1, int(duration_ticks) // 20) if duration_ticks else 999999
    hide = "true" if not particles else "false"
    cmd_name = _effect_cmd_name(resolved)
    name = getattr(player, "name", None)
    if not name:
        return False
    cmd = f"effect {_quote_player_name(name)} {cmd_name} {seconds} {int(amplifier)} {hide}"
    return _dispatch_effect_command(player, cmd)


def remove_mob_effect(player, effect_type: Any) -> bool:
    """移除玩家身上某效果。"""
    if effect_type is None:
        return False
    resolved = resolve_effect_type(effect_type) if isinstance(effect_type, str) else effect_type
    if resolved is None:
        resolved = effect_type

    if hasattr(player, "remove_effect"):
        try:
            player.remove_effect(resolved)
            return True
        except Exception:
            pass

    cmd_name = _effect_cmd_name(resolved)
    name = getattr(player, "name", None)
    if not name:
        return False
    # 时长 0 = 清除该效果
    cmd = f"effect {_quote_player_name(name)} {cmd_name} 0"
    return _dispatch_effect_command(player, cmd)
