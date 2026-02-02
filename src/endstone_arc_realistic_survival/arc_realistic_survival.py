import datetime
import os
import json
import math

from endstone import GameMode
from endstone.command import Command, CommandSender
from endstone.event import event_handler, PlayerItemConsumeEvent, PlayerMoveEvent, PlayerJoinEvent, PlayerQuitEvent
from endstone.plugin import Plugin
from endstone.form import ModalForm, Label, TextInput

from .DatabaseManager import DatabaseManager
from .LanguageManager import LanguageManager
from .SettingManager import SettingManager


class ARCRealisticSurvivalPlugin(Plugin):
    prefix = "ARCRealisticSurvivalPlugin"
    api_version = "0.10"
    load = "POSTWORLD"

    commands = {
        "ars": {
            "description": "ARC Realistic Survival config panel (op)",
            "usages": ["/ars"],
            "permissions": ["arc_realistic_survival.command.config"],
        }
    }

    permissions = {
        "arc_realistic_survival.command.config": {
            "description": "Allow opening survival config panel",
            "default": False
        }
    }

    def __init__(self):
        super().__init__()
        self.CHUNK_SIZE = 16  # 仍用于内部区块计算（如后续扩展）
        # 生存-口渴系统相关
        self.player_xuid_to_thirst = {}
        self.thirst_tick_seconds = 10
        self.thirst_decay_per_tick = 1
        self.thirst_moving_multiplier = 2.0
        self.thirst_initial = 100
        self.thirst_min = 0
        self.thirst_max = 100
        self.thirst_items_map = {}
        self.thirst_task = None
    
    def _safe_log(self, level: str, message: str):
        """
        安全的日志记录方法，在logger未初始化时使用print
        :param level: 日志级别 (info, warning, error)
        :param message: 日志消息
        """
        if hasattr(self, 'logger') and self.logger is not None:
            if level.lower() == 'info':
                self.logger.info(message)
            elif level.lower() == 'warning':
                self.logger.warning(message)
            elif level.lower() == 'error':
                self.logger.error(message)
            else:
                self.logger.info(message)
        else:
            # 如果logger未初始化，使用print
            print(f"[{level.upper()}] {message}")

    def on_load(self) -> None:
        self._safe_log('info', "[ARCRealisticSurvival] on_load is called!")
        
        # 初始化语言管理器
        self.language_manager = LanguageManager("CN")
        
        # 初始化设置管理器
        self.setting_manager = SettingManager()
        
        # 初始化默认配置
        self._init_default_settings()
        
        # 初始化数据库管理器（仅生存相关）
        db_path = os.path.join("ARCRealisticSurvival", "ars_survival.db")
        self.db_manager = DatabaseManager(db_path)
        
        # 创建表（仅生存相关）
        self._create_survival_tables()
        # 加载生存-口渴系统配置
        self._load_thirst_settings()
        self._load_thirst_items_config()

    def on_enable(self) -> None:
        self._safe_log('info', "[ARCRealisticSurvival] on_enable is called!")
        self.register_events(self)

        # 初始化经济插件 - 检查 arc_core 优先，然后 umoney
        self._init_economy_plugin()
        # 启动口渴值定时任务
        self._start_thirst_timer()

    def on_disable(self) -> None:
        self._safe_log('info', "[ARCRealisticSurvival] on_disable is called!")
        
        # 关闭数据库连接
        if hasattr(self, 'db_manager'):
            self.db_manager.close()
        # 停止口渴定时任务
        if self.thirst_task is not None:
            try:
                self.thirst_task.cancel()
            except Exception:
                pass
            self.thirst_task = None
        # 保存所有玩家口渴值
        try:
            for player in self.server.online_players:
                self._persist_player_thirst(player)
        except Exception:
            pass
    
    def _init_default_settings(self) -> None:
        """初始化默认配置"""
        # 交易税率 (默认5%)
        tax_rate = self.setting_manager.GetSetting("trade_tax_rate")
        if tax_rate is None:
            self.setting_manager.SetSetting("trade_tax_rate", "0.05")
            self._safe_log('info', "[ARCRealisticSurvival] Set default trade tax rate: 5%")
        
        # 最大商店数量限制 (默认50)
        max_shops = self.setting_manager.GetSetting("max_shops_per_player")
        if max_shops is None:
            self.setting_manager.SetSetting("max_shops_per_player", "50")
            self._safe_log('info', "[ARCRealisticSurvival] Set default max shops per player: 50")
        
        # 是否启用交易税 (默认启用)
        tax_enabled = self.setting_manager.GetSetting("trade_tax_enabled")
        if tax_enabled is None:
            self.setting_manager.SetSetting("trade_tax_enabled", "true")
            self._safe_log('info', "[ARCRealisticSurvival] Trade tax enabled by default")

    def _init_economy_plugin(self) -> None:
        """初始化经济插件 - 检查 arc_core 优先，然后 umoney"""
        try:
            self.economy_plugin = self.server.plugin_manager.get_plugin('arc_core')
            if self.economy_plugin is not None:
                self._safe_log('info', "[ARCRealisticSurvival] Using ARC Core economy system for money rewards.")
            else:
                self.economy_plugin = self.server.plugin_manager.get_plugin('umoney')
                if self.economy_plugin is not None:
                    self._safe_log('info', "[ARCRealisticSurvival] Using UMoney economy system for money rewards.")
                else:
                    self._safe_log('warning', "[ARCRealisticSurvival] No supported economy plugin found (arc_core or umoney). Money rewards will not be available.")
        except Exception as e:
            self._safe_log('error', f"[ARCRealisticSurvival] Failed to load economy plugin: {e}. Money rewards will not be available.")

    def _get_player_money(self, player_name: str) -> int:
        return 0

    def _change_player_money(self, player_name: str, amount: int) -> bool:
        return False

    def on_command(self, sender: CommandSender, command: Command, args: list[str]) -> bool:
        match command.name:
            case "ars":
                # /ars reload 支持热重载
                if args and len(args) >= 1 and str(args[0]).lower() == "reload":
                    if hasattr(sender, 'is_op') and not sender.is_op:
                        sender.send_message(self.language_manager.GetText("NO_PERMISSION") or "No permission")
                        return True
                    self._reload_survival_settings()
                    sender.send_message("[ARS] 配置与物品效果已重载")
                    return True
                # 打开配置面板（仅玩家、且需要权限/OP）
                if not hasattr(sender, 'send_form'):
                    sender.send_message(self.language_manager.GetText("PLAYER_ONLY_COMMAND") or "Players only")
                    return True
                if (hasattr(sender, 'has_permission') and sender.has_permission('arc_realistic_survival.command.config')) or getattr(sender, 'is_op', False):
                    self._show_survival_config_panel(sender)
                else:
                    sender.send_message(self.language_manager.GetText("NO_PERMISSION") or "No permission")
                return True
        return True

    def _reload_survival_settings(self) -> None:
        # 重新加载配置与物品效果，并重启口渴定时器
        try:
            self._load_thirst_settings()
            self._load_thirst_items_config()
            # 重启定时任务应用新的节奏
            if self.thirst_task is not None:
                try:
                    self.thirst_task.cancel()
                except Exception:
                    pass
                self.thirst_task = None
            self._start_thirst_timer()
        except Exception as e:
            self._safe_log('error', f"[ARS] reload settings error: {e}")

    def _show_survival_config_panel(self, player) -> None:
        try:
            title = "ARC Realistic Survival 配置"
            content_lines = [
                "修改后提交即写入配置并热重载",
                "单位: tick秒/倍数/整数",
            ]
            header = Label(text="\n".join(content_lines))
            input_tick = TextInput(
                label="thirst_tick_seconds",
                placeholder="每次衰减的秒数",
                default_value=str(self.thirst_tick_seconds)
            )
            input_decay = TextInput(
                label="thirst_decay_per_tick",
                placeholder="每次衰减口渴值(>=0)",
                default_value=str(self.thirst_decay_per_tick)
            )
            input_move = TextInput(
                label="thirst_moving_multiplier",
                placeholder="移动时衰减倍数(>=1.0)",
                default_value=str(self.thirst_moving_multiplier)
            )
            input_initial = TextInput(
                label="thirst_initial",
                placeholder="初始口渴值(0-100)",
                default_value=str(self.thirst_initial)
            )

            def on_submit(sender, json_str: str):
                try:
                    data = json.loads(json_str)
                    # data[1..4] 依次是四个输入框的值
                    new_tick = int(float(data[1]))
                    new_decay = int(float(data[2]))
                    new_move = float(data[3])
                    new_initial = int(float(data[4]))

                    if new_tick < 1:
                        raise ValueError("tick seconds < 1")
                    if new_decay < 0:
                        raise ValueError("decay < 0")
                    if new_move < 1.0:
                        raise ValueError("moving multiplier < 1.0")
                    if new_initial < 0 or new_initial > 100:
                        raise ValueError("initial out of [0,100]")

                    # 写回配置文件
                    self.setting_manager.SetSetting("thirst_tick_seconds", str(new_tick))
                    self.setting_manager.SetSetting("thirst_decay_per_tick", str(new_decay))
                    self.setting_manager.SetSetting("thirst_moving_multiplier", str(new_move))
                    self.setting_manager.SetSetting("thirst_initial", str(new_initial))

                    # 同步到内存并热重载
                    self._reload_survival_settings()
                    sender.send_message("[ARS] 配置已保存并重载")
                except Exception as e:
                    sender.send_message(f"[ARS] 配置提交失败: {e}")

            panel = ModalForm(
                title=title,
                controls=[header, input_tick, input_decay, input_move, input_initial],
                on_close=lambda s: None,
                on_submit=on_submit
            )
            player.send_form(panel)
        except Exception as e:
            self._safe_log('error', f"[ARS] show config panel error: {e}")
    
    # 数据库（仅生存）

    # 生存-口渴系统：数据库与配置
    def _create_survival_tables(self) -> None:
        thirst_fields = {
            "xuid": "TEXT PRIMARY KEY",
            "player_name": "TEXT NOT NULL",
            "thirst": "INTEGER NOT NULL",
            "updated_at": "TEXT NOT NULL"
        }
        if self.db_manager.create_table("player_thirst", thirst_fields):
            self._safe_log('info', "[ARCRealisticSurvival] player_thirst table ready")
        else:
            self._safe_log('error', "[ARCRealisticSurvival] Failed to create player_thirst table")

    def _load_thirst_settings(self) -> None:
        try:
            val = self.setting_manager.GetSetting("thirst_tick_seconds")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_tick_seconds", "10")
                self.thirst_tick_seconds = 10
            else:
                self.thirst_tick_seconds = max(1, int(val))

            val = self.setting_manager.GetSetting("thirst_decay_per_tick")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_decay_per_tick", "1")
                self.thirst_decay_per_tick = 1
            else:
                self.thirst_decay_per_tick = max(0, int(val))

            val = self.setting_manager.GetSetting("thirst_moving_multiplier")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_moving_multiplier", "2.0")
                self.thirst_moving_multiplier = 2.0
            else:
                self.thirst_moving_multiplier = max(1.0, float(val))

            val = self.setting_manager.GetSetting("thirst_initial")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_initial", "100")
                self.thirst_initial = 100
            else:
                self.thirst_initial = int(val)
        except Exception as e:
            self._safe_log('error', f"[ARCRealisticSurvival] load thirst settings error: {e}")

    def _load_thirst_items_config(self) -> None:
        # 文件格式： 物品-口渴变动int-效果string-持续时间int，可缺省后两者
        config_path = os.path.join("ARCRealisticSurvival", "thirst_items.txt")
        self.thirst_items_map = {}
        try:
            if not os.path.exists(os.path.dirname(config_path)):
                os.makedirs(os.path.dirname(config_path), exist_ok=True)
            if not os.path.exists(config_path):
                with open(config_path, "w", encoding="utf-8") as f:
                    f.write("# 示例\n")
                    f.write("COOKED_BEEF|-10\n")
                    f.write("COLA|50|SPEED|30\n")
            with open(config_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = [p.strip() for p in line.replace('-', '|').split('|') if p.strip() != ""]
                    if len(parts) >= 2:
                        item_key = parts[0].upper()
                        try:
                            delta = int(parts[1])
                        except Exception:
                            continue
                        effect = parts[2].upper() if len(parts) >= 3 else None
                        duration = int(parts[3]) if len(parts) >= 4 else None
                        self.thirst_items_map[item_key] = {
                            "delta": delta,
                            "effect": effect,
                            "duration": duration
                        }
        except Exception as e:
            self._safe_log('error', f"[ARCRealisticSurvival] load thirst items error: {e}")

    # 生存-口渴系统：内部工具
    def _clamp_thirst(self, value: int) -> int:
        return max(self.thirst_min, min(self.thirst_max, value))

    def _get_player_xuid(self, player) -> str:
        try:
            return getattr(player, 'xuid', None) or getattr(player, 'uuid', None) or player.name
        except Exception:
            return player.name

    def _load_player_thirst(self, player) -> int:
        xuid = self._get_player_xuid(player)
        row = self.db_manager.query_one("SELECT thirst FROM player_thirst WHERE xuid=?", (xuid,))
        if row is None:
            self.player_xuid_to_thirst[xuid] = self.thirst_initial
            # 插入一条
            self.db_manager.insert("player_thirst", {
                "xuid": xuid,
                "player_name": player.name,
                "thirst": self.thirst_initial,
                "updated_at": datetime.datetime.utcnow().isoformat()
            })
        else:
            self.player_xuid_to_thirst[xuid] = int(row["thirst"])
        return self.player_xuid_to_thirst[xuid]

    def _persist_player_thirst(self, player) -> None:
        try:
            xuid = self._get_player_xuid(player)
            thirst = int(self.player_xuid_to_thirst.get(xuid, self.thirst_initial))
            exists = self.db_manager.query_one("SELECT xuid FROM player_thirst WHERE xuid=?", (xuid,))
            data = {
                "player_name": player.name,
                "thirst": thirst,
                "updated_at": datetime.datetime.utcnow().isoformat()
            }
            if exists is None:
                data_with_key = {"xuid": xuid}
                data_with_key.update(data)
                self.db_manager.insert("player_thirst", data_with_key)
            else:
                self.db_manager.update("player_thirst", data, "xuid=?", (xuid,))
        except Exception as e:
            self._safe_log('error', f"[ARCRealisticSurvival] persist thirst error: {e}")

    def _apply_thirst_delta(self, player, delta: int, reason: str = "") -> int:
        xuid = self._get_player_xuid(player)
        current = int(self.player_xuid_to_thirst.get(xuid, self.thirst_initial))
        new_val = self._clamp_thirst(current + delta)
        self.player_xuid_to_thirst[xuid] = new_val
        # 仅当口渴度整数值确实变化时才提示
        if new_val != current:
            msg = self.language_manager.GetText("THIRST_VALUE") or "当前口渴值: {value}"
            player.send_popup(msg.replace("{value}", str(new_val)))
        return new_val

    def _start_thirst_timer(self) -> None:
        try:
            if self.thirst_task is not None:
                try:
                    self.thirst_task.cancel()
                except Exception:
                    pass
                self.thirst_task = None
            period_seconds = max(1, int(self.thirst_tick_seconds))

            def tick():
                try:
                    for player in self.server.online_players:
                        if player.game_mode != GameMode.SURVIVAL and player.game_mode != GameMode.ADVENTURE:
                            continue
                        base_decay = self.thirst_decay_per_tick
                        # 移动状态由最近移动事件标记
                        moving_flag = getattr(player, '_arc_moving_flag', False)
                        decay = base_decay if not moving_flag else int(math.ceil(base_decay * self.thirst_moving_multiplier))
                        if decay > 0:
                            self._apply_thirst_delta(player, -decay, reason="timer")
                        # 每次循环后重置移动标记
                        if hasattr(player, '_arc_moving_flag'):
                            try:
                                delattr(player, '_arc_moving_flag')
                            except Exception:
                                pass
                        # 定期保存
                        self._persist_player_thirst(player)
                except Exception as e:
                    self._safe_log('error', f"[ARCRealisticSurvival] thirst timer error: {e}")

            scheduler = self.server.scheduler
            # delay 与 period 单位均为 tick（20 tick = 1 秒）
            self.thirst_task = scheduler.run_task(self, tick, 20, period_seconds * 20)
        except Exception as e:
            self._safe_log('error', f"[ARCRealisticSurvival] start thirst timer error: {e}")

    # 生存-口渴系统：事件
    @event_handler()
    def on_player_join(self, event: PlayerJoinEvent):
        player = event.player
        self._load_player_thirst(player)

    @event_handler()
    def on_player_quit(self, event: PlayerQuitEvent):
        player = event.player
        self._persist_player_thirst(player)

    @event_handler()
    def on_player_move(self, event: PlayerMoveEvent):
        try:
            player = event.player
            setattr(player, '_arc_moving_flag', True)
        except Exception:
            pass

    @event_handler()
    def on_player_item_consume(self, event: PlayerItemConsumeEvent):
        try:
            player = event.player
            item = event.item
            item_key = None
            try:
                item_key = getattr(item, 'type', None) or getattr(item, 'name', None)
            except Exception:
                item_key = None
            if item_key is None:
                return
            key = str(item_key).upper()
            cfg = self.thirst_items_map.get(key)
            if cfg is None:
                return
            delta = int(cfg.get("delta", 0))
            self._apply_thirst_delta(player, delta, reason="consume")
            effect = cfg.get("effect")
            duration = cfg.get("duration")
            if effect and duration:
                try:
                    cmd = f"effect give {player.name} {effect.lower()} {int(duration)} 1 true"
                    if hasattr(self.server, 'dispatch_command'):
                        self.server.dispatch_command(cmd)
                except Exception:
                    pass
            self._persist_player_thirst(player)
        except Exception as e:
            self._safe_log('error', f"[ARS] consume event error: {e}")
