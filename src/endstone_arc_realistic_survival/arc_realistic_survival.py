import datetime
import os
import json
import math
import time

from endstone import GameMode
from endstone.attribute import Attribute, AttributeModifier
from endstone.command import Command, CommandSender
from endstone.event import event_handler, PlayerItemConsumeEvent, PlayerMoveEvent, PlayerJoinEvent, PlayerQuitEvent, ActorDamageEvent, PlayerDeathEvent, PlayerRespawnEvent, PlayerGameModeChangeEvent
from endstone.plugin import Plugin
from endstone.form import ActionForm, Button, ModalForm, Label, TextInput

from .DatabaseManager import DatabaseManager
from .LanguageManager import LanguageManager
from .NutritionManager import NUTRIENT_KEYS, NutritionManager
from .SettingManager import SettingManager
from .ZombieVirusManager import ZombieVirusManager
from .effect_compat import apply_mob_effect, resolve_effect_type


class ARCRealisticSurvivalPlugin(Plugin):
    prefix = "ARCRealisticSurvivalPlugin"
    api_version = "0.10"
    load = "POSTWORLD"

    commands = {
        "ars": {
            "description": "ARC Realistic Survival：营养/感染面板；OP 可开配置与调试。",
            "usages": [
                "/ars",
                "/ars nutrition",
                "/ars infection",
                "/ars reload",
                "/ars infectset <player: player> <value: float>",
                "/ars nutriset <player: player> <nutrient: str> <value: int>",
            ],
            "permissions": ["arc_realistic_survival.command.common"],
        }
    }

    permissions = {
        "arc_realistic_survival.command.common": {
            "description": "允许使用 /ars nutrition、/ars infection 等基础指令",
            "default": True,
        },
        "arc_realistic_survival.command.config": {
            "description": "允许打开 /ars 配置面板及 reload / nutriset / infectset（OP）",
            "default": "op",
        },
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
        self.thirst_consume_debug = False
        self.thirst_task = None
        # 口渴 0→-75% 移速，100→+125% 移速（MULTIPLY_BASE 线性映射）
        self.thirst_speed_at_zero = -0.75
        self.thirst_speed_at_full = 1.25
        self.thirst_fatal_seconds = 3600
        self.player_xuid_to_dehydrated_since = {}
        self.THIRST_SPEED_MODIFIER = "ars:thirst_speed"
        # 创造/旁观时冻结真实生存数值；切回生存时恢复
        self._creative_snapshots = {}
        self.nutrition_manager = None
        self.zombie_virus_manager = None
    
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

    def _log_consume_always(self, message: str) -> None:
        """进食/饮水诊断：同时写入插件 logger 与 stdout，避免后台级别过滤导致看不见。"""
        self._safe_log('info', message)
        try:
            print(f"[ARCRealisticSurvival][consume] {message}", flush=True)
        except Exception:
            pass

    def _resolve_survival_db_path(self) -> str:
        """与 settings.yml 同目录优先：plugins/ARCRealisticSurvival/ars_survival.db；兼容旧路径 ARCRealisticSurvival/。"""
        preferred = os.path.join("plugins", "ARCRealisticSurvival", "ars_survival.db")
        legacy = os.path.join("ARCRealisticSurvival", "ars_survival.db")
        if os.path.isfile(preferred):
            return preferred
        if os.path.isfile(legacy):
            return legacy
        return preferred

    def on_load(self) -> None:
        self._safe_log('info', "[ARCRealisticSurvival] on_load is called!")
        
        # 初始化语言管理器
        self.language_manager = LanguageManager("CN")
        
        # 初始化设置管理器
        self.setting_manager = SettingManager()
        
        # 初始化默认配置
        self._init_default_settings()
        
        # 初始化数据库管理器（仅生存相关）
        db_path = self._resolve_survival_db_path()
        self.db_manager = DatabaseManager(db_path)
        self._safe_log(
            'info',
            f"[ARCRealisticSurvival] ars_survival.db -> {os.path.abspath(db_path)}",
        )
        
        # 创建表（仅生存相关）
        self._create_survival_tables()
        # 初始化营养学管理器
        self.nutrition_manager = NutritionManager(
            self,
            self.db_manager,
            self.setting_manager,
            self._safe_log,
            self._get_player_xuid,
            self._collect_item_identity_strings,
        )
        self.nutrition_manager.ensure_tables()
        self.nutrition_manager.load_settings()
        self.nutrition_manager.load_items_config()
        # 初始化丧尸病毒管理器
        self.zombie_virus_manager = ZombieVirusManager(
            self,
            self.db_manager,
            self.setting_manager,
            self._safe_log,
            self._get_player_xuid,
        )
        self.zombie_virus_manager.ensure_tables()
        self.zombie_virus_manager.load_settings()
        self.zombie_virus_manager.load_sources_config()
        # 加载生存-口渴系统配置
        self._load_thirst_settings()
        self._load_thirst_items_config()

    def on_enable(self) -> None:
        self._safe_log('info', "[ARCRealisticSurvival] on_enable is called!")
        self.register_events(self)
        self._safe_log(
            'info',
            "[ARCRealisticSurvival] 事件已注册（含 PlayerItemConsumeEvent），进食/饮水时控制台会输出 [consume] 行",
        )

        # 初始化经济插件 - 检查 arc_core 优先，然后 umoney
        self._init_economy_plugin()
        # 启动口渴值定时任务
        self._start_thirst_timer()
        # 启动营养学定时任务
        if self.nutrition_manager is not None:
            self.nutrition_manager.start_timer()
        if self.zombie_virus_manager is not None:
            self.zombie_virus_manager.start_timer()

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
        # 停止营养定时任务并保存
        if self.nutrition_manager is not None:
            self.nutrition_manager.stop_timer()
        if self.zombie_virus_manager is not None:
            self.zombie_virus_manager.stop_timer()
        # 保存所有玩家口渴值、营养值与感染值
        try:
            for player in self.server.online_players:
                self._persist_player_thirst(player)
                if self.nutrition_manager is not None:
                    self.nutrition_manager.clear_symptoms(player)
                    self.nutrition_manager.persist_player(player)
                if self.zombie_virus_manager is not None:
                    self.zombie_virus_manager.persist_player(player)
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
                if args and len(args) >= 1:
                    sub = str(args[0]).lower()
                    if sub == "reload":
                        if hasattr(sender, 'is_op') and not sender.is_op:
                            sender.send_message(self.language_manager.GetText("NO_PERMISSION") or "No permission")
                            return True
                        self._reload_survival_settings()
                        sender.send_message("[ARS] 配置与物品效果已重载")
                        return True
                    if sub == "nutrition":
                        if not hasattr(sender, 'send_form'):
                            sender.send_message(self.language_manager.GetText("PLAYER_ONLY_COMMAND") or "Players only")
                            return True
                        self._show_nutrition_panel(sender)
                        return True
                    if sub == "infection":
                        if not hasattr(sender, 'send_form'):
                            sender.send_message(self.language_manager.GetText("PLAYER_ONLY_COMMAND") or "Players only")
                            return True
                        self._show_infection_panel(sender)
                        return True
                    if sub == "infectset":
                        if not getattr(sender, 'is_op', False):
                            sender.send_message(self.language_manager.GetText("NO_PERMISSION") or "No permission")
                            return True
                        if len(args) < 3:
                            sender.send_message("[ARS] 用法: /ars infectset <玩家> <0-100>")
                            return True
                        target = self.server.get_player(args[1])
                        if target is None:
                            sender.send_message(f"[ARS] 找不到玩家: {args[1]}")
                            return True
                        try:
                            value = float(args[2])
                        except ValueError:
                            sender.send_message("[ARS] 数值必须是 0-100")
                            return True
                        if self.zombie_virus_manager is None:
                            sender.send_message("[ARS] 感染系统未初始化")
                            return True
                        try:
                            val = self.zombie_virus_manager.set_infection(target, value)
                            sender.send_message(f"[ARS] 已设置 {target.name} 感染值={int(val)}")
                        except Exception as e:
                            sender.send_message(f"[ARS] 设置失败: {e}")
                        return True
                    if sub == "nutriset":
                        if not getattr(sender, 'is_op', False):
                            sender.send_message(self.language_manager.GetText("NO_PERMISSION") or "No permission")
                            return True
                        if len(args) < 4:
                            sender.send_message("[ARS] 用法: /ars nutriset <玩家> <vitamin_a|vitamin_c|iron|protein> <0-100>")
                            return True
                        target = self.server.get_player(args[1])
                        if target is None:
                            sender.send_message(f"[ARS] 找不到玩家: {args[1]}")
                            return True
                        nutrient = args[2].lower()
                        if nutrient not in NUTRIENT_KEYS:
                            sender.send_message("[ARS] 营养素必须是: vitamin_a, vitamin_c, iron, protein")
                            return True
                        try:
                            value = int(args[3])
                        except ValueError:
                            sender.send_message("[ARS] 数值必须是 0-100 的整数")
                            return True
                        if self.nutrition_manager is None:
                            sender.send_message("[ARS] 营养系统未初始化")
                            return True
                        try:
                            data = self.nutrition_manager.set_nutrient(target, nutrient, value)
                            sender.send_message(
                                f"[ARS] 已设置 {target.name} 的 {nutrient}={data[nutrient]}"
                            )
                        except Exception as e:
                            sender.send_message(f"[ARS] 设置失败: {e}")
                        return True
                # 打开配置面板（仅玩家、且需要 config 权限 / OP）
                if not hasattr(sender, 'send_form'):
                    sender.send_message(self.language_manager.GetText("PLAYER_ONLY_COMMAND") or "Players only")
                    return True
                has_cfg = False
                try:
                    has_cfg = sender.has_permission("arc_realistic_survival.command.config")
                except Exception:
                    has_cfg = False
                if has_cfg or getattr(sender, "is_op", False):
                    self._show_survival_config_panel(sender)
                else:
                    sender.send_message(
                        "[ARS] 用法: /ars nutrition | /ars infection\n"
                        "配置面板需要 OP 权限（/ars）"
                    )
                return True
        return True

    def _reload_survival_settings(self) -> None:
        # 重新加载配置与物品效果，并重启定时器
        try:
            self._load_thirst_settings()
            self._load_thirst_items_config()
            if self.nutrition_manager is not None:
                self.nutrition_manager.load_settings()
                self.nutrition_manager.load_items_config()
            if self.zombie_virus_manager is not None:
                self.zombie_virus_manager.load_settings()
                self.zombie_virus_manager.load_sources_config()
            if self.thirst_task is not None:
                try:
                    self.thirst_task.cancel()
                except Exception:
                    pass
                self.thirst_task = None
            self._start_thirst_timer()
            if self.nutrition_manager is not None:
                self.nutrition_manager.start_timer()
            if self.zombie_virus_manager is not None:
                self.zombie_virus_manager.start_timer()
        except Exception as e:
            self._safe_log('error', f"[ARS] reload settings error: {e}")

    def _show_survival_config_panel(self, player) -> None:
        try:
            nm = self.nutrition_manager
            zvm = self.zombie_virus_manager
            title = "ARC Realistic Survival 配置"
            content_lines = [
                "修改后提交即写入配置并热重载",
                "口渴/营养/感染: 见各字段说明",
            ]
            header = Label(text="\n".join(content_lines))
            input_tick = TextInput(
                label="thirst_tick_seconds",
                placeholder="口渴衰减间隔（秒）",
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
            input_nutrition_tick = TextInput(
                label="nutrition_tick_seconds",
                placeholder="营养衰减间隔（秒，默认300）",
                default_value=str(nm.nutrition_tick_seconds if nm else 300)
            )
            input_nutrition_decay = TextInput(
                label="nutrition_decay_per_tick",
                placeholder="每次衰减营养值(>=0)",
                default_value=str(nm.nutrition_decay_per_tick if nm else 1)
            )
            input_nutrition_initial = TextInput(
                label="nutrition_initial",
                placeholder="初始营养值(0-100)",
                default_value=str(nm.nutrition_initial if nm else 100)
            )
            input_nutrition_cooldown = TextInput(
                label="nutrition_warn_cooldown_seconds",
                placeholder="症状提示冷却（秒）",
                default_value=str(nm.nutrition_warn_cooldown_seconds if nm else 300)
            )
            input_infection_tick = TextInput(
                label="infection_tick_seconds",
                placeholder="感染 tick 间隔（秒）",
                default_value=str(zvm.infection_tick_seconds if zvm else 12)
            )
            input_infection_threshold = TextInput(
                label="infection_threshold",
                placeholder="恶化临界值（默认50）",
                default_value=str(int(zvm.infection_threshold if zvm else 50))
            )
            input_infection_growth = TextInput(
                label="infection_growth_per_minute",
                placeholder="超临界每分钟增长",
                default_value=str(int(zvm.infection_growth_per_minute if zvm else 5))
            )
            input_infection_decay = TextInput(
                label="infection_decay_per_minute",
                placeholder="低于临界每分钟下降",
                default_value=str(int(zvm.infection_decay_per_minute if zvm else 2))
            )

            def on_submit(sender, json_str: str):
                try:
                    data = json.loads(json_str)
                    new_tick = int(float(data[1]))
                    new_decay = int(float(data[2]))
                    new_move = float(data[3])
                    new_initial = int(float(data[4]))
                    new_n_tick = int(float(data[5]))
                    new_n_decay = int(float(data[6]))
                    new_n_initial = int(float(data[7]))
                    new_n_cooldown = int(float(data[8]))
                    new_i_tick = int(float(data[9]))
                    new_i_threshold = float(data[10])
                    new_i_growth = float(data[11])
                    new_i_decay = float(data[12])

                    if new_tick < 1:
                        raise ValueError("thirst tick seconds < 1")
                    if new_decay < 0:
                        raise ValueError("thirst decay < 0")
                    if new_move < 1.0:
                        raise ValueError("moving multiplier < 1.0")
                    if new_initial < 0 or new_initial > 100:
                        raise ValueError("thirst initial out of [0,100]")
                    if new_n_tick < 30:
                        raise ValueError("nutrition tick seconds < 30")
                    if new_n_decay < 0:
                        raise ValueError("nutrition decay < 0")
                    if new_n_initial < 0 or new_n_initial > 100:
                        raise ValueError("nutrition initial out of [0,100]")
                    if new_n_cooldown < 30:
                        raise ValueError("nutrition cooldown < 30")
                    if new_i_tick < 6:
                        raise ValueError("infection tick seconds < 6")
                    if new_i_threshold < 1 or new_i_threshold > 99:
                        raise ValueError("infection threshold out of (0,100)")
                    if new_i_growth < 0 or new_i_decay < 0:
                        raise ValueError("infection growth/decay < 0")

                    self.setting_manager.SetSetting("thirst_tick_seconds", str(new_tick))
                    self.setting_manager.SetSetting("thirst_decay_per_tick", str(new_decay))
                    self.setting_manager.SetSetting("thirst_moving_multiplier", str(new_move))
                    self.setting_manager.SetSetting("thirst_initial", str(new_initial))
                    self.setting_manager.SetSetting("nutrition_tick_seconds", str(new_n_tick))
                    self.setting_manager.SetSetting("nutrition_decay_per_tick", str(new_n_decay))
                    self.setting_manager.SetSetting("nutrition_initial", str(new_n_initial))
                    self.setting_manager.SetSetting("nutrition_warn_cooldown_seconds", str(new_n_cooldown))
                    self.setting_manager.SetSetting("infection_tick_seconds", str(new_i_tick))
                    self.setting_manager.SetSetting("infection_threshold", str(new_i_threshold))
                    self.setting_manager.SetSetting("infection_growth_per_minute", str(new_i_growth))
                    self.setting_manager.SetSetting("infection_decay_per_minute", str(new_i_decay))

                    self._reload_survival_settings()
                    sender.send_message("[ARS] 配置已保存并重载")
                except Exception as e:
                    sender.send_message(f"[ARS] 配置提交失败: {e}")

            panel = ModalForm(
                title=title,
                controls=[
                    header, input_tick, input_decay, input_move, input_initial,
                    input_nutrition_tick, input_nutrition_decay, input_nutrition_initial, input_nutrition_cooldown,
                    input_infection_tick, input_infection_threshold, input_infection_growth, input_infection_decay,
                ],
                on_close=lambda s: None,
                on_submit=on_submit
            )
            player.send_form(panel)
        except Exception as e:
            self._safe_log('error', f"[ARS] show config panel error: {e}")

    def _show_nutrition_panel(self, player) -> None:
        try:
            if self.nutrition_manager is None:
                player.send_message("[ARS] 营养系统未初始化")
                return
            status_lines = self.nutrition_manager.get_status_lines(player)
            catalog_lines = self.nutrition_manager.get_food_catalog_lines(limit=15)
            body = "\n".join(status_lines + [""] + catalog_lines)
            form = ActionForm(
                title="营养学",
                content=body,
                buttons=[Button("刷新"), Button("关闭")],
                on_submit=lambda s, idx: self._on_nutrition_panel_submit(s, idx),
                on_close=lambda s: None,
            )
            player.send_form(form)
        except Exception as e:
            self._safe_log('error', f"[ARS] show nutrition panel error: {e}")
            player.send_message(f"[ARS] 无法打开营养面板: {e}")

    def _on_nutrition_panel_submit(self, player, index: int) -> None:
        if index == 0:
            self._show_nutrition_panel(player)

    def _show_infection_panel(self, player) -> None:
        try:
            if self.zombie_virus_manager is None:
                player.send_message("[ARS] 感染系统未初始化")
                return
            status_lines = self.zombie_virus_manager.get_status_lines(player)
            catalog_lines = self.zombie_virus_manager.get_source_catalog_lines(limit=15)
            body = "\n".join(status_lines + [""] + catalog_lines)
            form = ActionForm(
                title="丧尸病毒感染",
                content=body,
                buttons=[Button("刷新"), Button("关闭")],
                on_submit=lambda s, idx: self._on_infection_panel_submit(s, idx),
                on_close=lambda s: None,
            )
            player.send_form(form)
        except Exception as e:
            self._safe_log('error', f"[ARS] show infection panel error: {e}")
            player.send_message(f"[ARS] 无法打开感染面板: {e}")

    def _on_infection_panel_submit(self, player, index: int) -> None:
        if index == 0:
            self._show_infection_panel(player)
    
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
        if not self.db_manager.ensure_column("player_thirst", "dehydrated_since", "REAL"):
            self._safe_log('warning', "[ARCRealisticSurvival] failed to add player_thirst.dehydrated_since")

        thirst_items_fields = {
            "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
            "item_id": "TEXT NOT NULL UNIQUE",
            "item_name": "TEXT",
            "thirst_delta": "INTEGER NOT NULL DEFAULT 0",
            "buffs": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        }
        if self.db_manager.create_table("thirst_items", thirst_items_fields):
            self._safe_log('info', "[ARCRealisticSurvival] thirst_items table ready")

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

            val = self.setting_manager.GetSetting("thirst_consume_debug")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_consume_debug", "false")
                self.thirst_consume_debug = False
            else:
                self.thirst_consume_debug = str(val).strip().lower() in ("1", "true", "yes", "on")

            val = self.setting_manager.GetSetting("thirst_speed_at_zero")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_speed_at_zero", "-0.75")
                self.thirst_speed_at_zero = -0.75
            else:
                self.thirst_speed_at_zero = float(val)

            val = self.setting_manager.GetSetting("thirst_speed_at_full")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_speed_at_full", "1.25")
                self.thirst_speed_at_full = 1.25
            else:
                self.thirst_speed_at_full = float(val)

            val = self.setting_manager.GetSetting("thirst_fatal_seconds")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_fatal_seconds", "3600")
                self.thirst_fatal_seconds = 3600
            else:
                self.thirst_fatal_seconds = max(1, int(float(val)))
        except Exception as e:
            self._safe_log('error', f"[ARCRealisticSurvival] load thirst settings error: {e}")

    def _thirst_speed_amount(self, thirst: int) -> float:
        """口渴 0→thirst_speed_at_zero，100→thirst_speed_at_full，线性映射；低于 0 按 0 算。"""
        span = float(self.thirst_max - self.thirst_min) or 100.0
        t = max(self.thirst_min, min(self.thirst_max, int(thirst)))
        ratio = (t - self.thirst_min) / span
        return float(self.thirst_speed_at_zero) + ratio * (
            float(self.thirst_speed_at_full) - float(self.thirst_speed_at_zero)
        )

    def _apply_thirst_movement_modifier(self, player) -> None:
        try:
            get_attr = getattr(player, "get_attribute", None)
            if get_attr is None:
                return
            xuid = self._get_player_xuid(player)
            thirst = int(self.player_xuid_to_thirst.get(xuid, self.thirst_initial))
            inst = get_attr(Attribute.MOVEMENT_SPEED)
            if inst is None:
                return
            self._clear_thirst_movement_modifier(player)
            amount = self._thirst_speed_amount(thirst)
            mod = AttributeModifier(
                self.THIRST_SPEED_MODIFIER,
                amount,
                AttributeModifier.MULTIPLY_BASE,
            )
            if hasattr(inst, "add_transient_modifier"):
                inst.add_transient_modifier(mod)
            else:
                inst.add_modifier(mod)
        except Exception as e:
            self._safe_log('error', f"[ARS] thirst movement modifier error: {e}")

    def _clear_thirst_movement_modifier(self, player) -> None:
        try:
            get_attr = getattr(player, "get_attribute", None)
            if get_attr is None:
                return
            inst = get_attr(Attribute.MOVEMENT_SPEED)
            if inst is None:
                return
            try:
                inst.remove_modifier(self.THIRST_SPEED_MODIFIER)
            except Exception:
                for existing in list(getattr(inst, "modifiers", []) or []):
                    if getattr(existing, "name", None) == self.THIRST_SPEED_MODIFIER:
                        inst.remove_modifier(existing)
                        break
        except Exception:
            pass

    def _register_thirst_item_cfg(self, item_id: str, cfg: dict) -> None:
        """同一物品写入完整命名空间键与短 id（: 后一段），便于匹配 item.type 的多种格式。"""
        raw = str(item_id).strip()
        if not raw:
            return
        upper_full = raw.upper()
        self.thirst_items_map[upper_full] = cfg
        if ":" in upper_full:
            short_key = upper_full.split(":", 1)[1]
            self.thirst_items_map[short_key] = cfg

    def _collect_item_identity_strings(self, item) -> list[str]:
        """从 ItemStack 收集可能用于匹配的字符串（去重、保序）。"""
        candidates = []
        seen = set()

        def add_one(val) -> None:
            if val is None:
                return
            s = str(val).strip()
            if not s or s in seen:
                return
            seen.add(s)
            candidates.append(s)

        add_one(getattr(item, "type", None))
        add_one(getattr(item, "name", None))
        try:
            t = getattr(item, "type", None)
            if t is not None and hasattr(t, "name"):
                add_one(getattr(t, "name", None))
            if t is not None:
                add_one(t)
        except Exception:
            pass
        add_one(item)
        return candidates

    def _find_thirst_cfg_for_item(self, item):
        for cand in self._collect_item_identity_strings(item):
            upper_full = cand.upper()
            if upper_full in self.thirst_items_map:
                return self.thirst_items_map[upper_full], cand, upper_full
            if ":" in upper_full:
                short_key = upper_full.split(":", 1)[1]
                if short_key in self.thirst_items_map:
                    return self.thirst_items_map[short_key], cand, short_key
        return None, None, None

    def _load_thirst_items_config(self) -> None:
        """仅从 SQLite 表 thirst_items 加载口渴物品（item_id、thirst_delta、buffs）。"""
        self.thirst_items_map = {}
        db_count = 0
        try:
            if not self.db_manager.table_exists("thirst_items"):
                self._safe_log(
                    'warning',
                    "[ARCRealisticSurvival] thirst_items 表不存在，口渴物品配置为空（启动时应已自动建表）",
                )
                return
            rows = self.db_manager.query_all(
                "SELECT item_id, thirst_delta, buffs FROM thirst_items WHERE item_id IS NOT NULL AND item_id != ''"
            )
            for row in rows:
                item_id = row.get("item_id")
                if not item_id:
                    continue
                try:
                    delta = int(row.get("thirst_delta", 0))
                except Exception:
                    continue
                buffs_raw = row.get("buffs")
                buffs_list = None
                if buffs_raw:
                    try:
                        parsed = json.loads(buffs_raw)
                        if isinstance(parsed, list):
                            buffs_list = parsed
                    except Exception:
                        buffs_list = None
                cfg = {
                    "delta": delta,
                    "buffs": buffs_list,
                }
                self._register_thirst_item_cfg(item_id, cfg)
                db_count += 1
        except Exception as e:
            self._safe_log('error', f"[ARCRealisticSurvival] load thirst_items from DB error: {e}")

        self._safe_log(
            'info',
            f"[ARCRealisticSurvival] thirst items: database={db_count} rows, "
            f"lookup keys={len(self.thirst_items_map)} (含命名空间/短名展开)",
        )

    # 生存-口渴系统：内部工具
    def _is_survival_like(self, player) -> bool:
        try:
            return player.game_mode == GameMode.SURVIVAL or player.game_mode == GameMode.ADVENTURE
        except Exception:
            return True

    def _enter_non_survival_mode(self, player) -> None:
        """创造/旁观：快照真实数值 → 运行时设为正常值 → 停止后续变动处理。"""
        xuid = self._get_player_xuid(player)
        if xuid not in self._creative_snapshots:
            snap = {
                "thirst": int(self.player_xuid_to_thirst.get(xuid, self.thirst_initial)),
                "dehydrated_since": self.player_xuid_to_dehydrated_since.get(xuid),
                "nutrition": None,
                "infection": None,
            }
            if self.nutrition_manager is not None:
                nm = self.nutrition_manager
                snap["nutrition"] = dict(
                    nm.player_nutrition.get(xuid) or nm._default_nutrition()
                )
            if self.zombie_virus_manager is not None:
                snap["infection"] = float(
                    self.zombie_virus_manager.player_infection.get(xuid, 0.0)
                )
            self._creative_snapshots[xuid] = snap

        self.player_xuid_to_thirst[xuid] = self.thirst_initial
        self.player_xuid_to_dehydrated_since[xuid] = None
        self._clear_thirst_movement_modifier(player)
        if self.nutrition_manager is not None:
            self.nutrition_manager.apply_healthy_bypass(player)
        if self.zombie_virus_manager is not None:
            self.zombie_virus_manager.apply_healthy_bypass(player)

    def _leave_non_survival_mode(self, player) -> None:
        """切回生存/冒险：从快照恢复真实数值并重新挂效果。"""
        xuid = self._get_player_xuid(player)
        snap = self._creative_snapshots.pop(xuid, None)
        if snap is not None:
            self.player_xuid_to_thirst[xuid] = int(snap.get("thirst", self.thirst_initial))
            self.player_xuid_to_dehydrated_since[xuid] = snap.get("dehydrated_since")
            if self.nutrition_manager is not None and snap.get("nutrition") is not None:
                self.nutrition_manager.restore_nutrition(player, snap["nutrition"])
            elif self.nutrition_manager is not None:
                self.nutrition_manager._apply_persistent_symptoms(player)
            if self.zombie_virus_manager is not None and snap.get("infection") is not None:
                self.zombie_virus_manager.restore_infection(player, snap["infection"])
        else:
            if self.nutrition_manager is not None:
                self.nutrition_manager._apply_persistent_symptoms(player)
        self._apply_thirst_movement_modifier(player)
        self._sync_dehydration_state(player)
        self._persist_player_thirst(player)
        if self.nutrition_manager is not None:
            self.nutrition_manager.persist_player(player)
        if self.zombie_virus_manager is not None:
            self.zombie_virus_manager.persist_player(player)

    def _restore_snapshot_before_persist(self, player) -> None:
        """退出时若仍在创造旁观，把快照写回内存再落库，避免把「正常值」存进数据库。"""
        xuid = self._get_player_xuid(player)
        snap = self._creative_snapshots.pop(xuid, None)
        if snap is None:
            return
        self.player_xuid_to_thirst[xuid] = int(snap.get("thirst", self.thirst_initial))
        self.player_xuid_to_dehydrated_since[xuid] = snap.get("dehydrated_since")
        if self.nutrition_manager is not None and snap.get("nutrition") is not None:
            x = self._get_player_xuid(player)
            data = {k: int(snap["nutrition"].get(k, self.nutrition_manager.nutrition_initial)) for k in NUTRIENT_KEYS}
            self.nutrition_manager.player_nutrition[x] = data
        if self.zombie_virus_manager is not None and snap.get("infection") is not None:
            self.zombie_virus_manager.player_infection[xuid] = float(snap["infection"])

    def _clamp_thirst(self, value: int) -> int:
        """上限 100；允许低于 0，以便记录严重脱水持续时间。"""
        return min(self.thirst_max, int(value))

    def _get_player_xuid(self, player) -> str:
        try:
            return getattr(player, 'xuid', None) or getattr(player, 'uuid', None) or player.name
        except Exception:
            return player.name

    def _parse_dehydrated_since(self, raw) -> float | None:
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except Exception:
            return None

    def _load_player_thirst(self, player) -> int:
        xuid = self._get_player_xuid(player)
        row = self.db_manager.query_one(
            "SELECT thirst, dehydrated_since FROM player_thirst WHERE xuid=?",
            (xuid,),
        )
        if row is None:
            row = self.db_manager.query_one("SELECT thirst FROM player_thirst WHERE xuid=?", (xuid,))
        if row is None:
            self.player_xuid_to_thirst[xuid] = self.thirst_initial
            self.player_xuid_to_dehydrated_since[xuid] = None
            self.db_manager.insert("player_thirst", {
                "xuid": xuid,
                "player_name": player.name,
                "thirst": self.thirst_initial,
                "dehydrated_since": None,
                "updated_at": datetime.datetime.utcnow().isoformat()
            })
        else:
            self.player_xuid_to_thirst[xuid] = int(row["thirst"])
            self.player_xuid_to_dehydrated_since[xuid] = self._parse_dehydrated_since(
                row.get("dehydrated_since")
            )
        return self.player_xuid_to_thirst[xuid]

    def _persist_player_thirst(self, player) -> None:
        try:
            xuid = self._get_player_xuid(player)
            thirst = int(self.player_xuid_to_thirst.get(xuid, self.thirst_initial))
            since = self.player_xuid_to_dehydrated_since.get(xuid)
            exists = self.db_manager.query_one("SELECT xuid FROM player_thirst WHERE xuid=?", (xuid,))
            data = {
                "player_name": player.name,
                "thirst": thirst,
                "dehydrated_since": float(since) if since is not None else None,
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
        if not self._is_survival_like(player):
            return int(self.player_xuid_to_thirst.get(self._get_player_xuid(player), self.thirst_initial))
        xuid = self._get_player_xuid(player)
        current = int(self.player_xuid_to_thirst.get(xuid, self.thirst_initial))
        new_val = self._clamp_thirst(current + delta)
        self.player_xuid_to_thirst[xuid] = new_val
        # 仅当口渴度整数值确实变化时才提示
        if new_val != current:
            msg = self.language_manager.GetText("THIRST_VALUE") or "当前口渴值: {value}"
            player.send_popup(msg.replace("{value}", str(new_val)))
        self._apply_thirst_movement_modifier(player)
        self._sync_dehydration_state(player)
        return new_val

    def _reset_player_thirst(self, player) -> None:
        if not self._is_survival_like(player):
            return
        xuid = self._get_player_xuid(player)
        self.player_xuid_to_thirst[xuid] = self.thirst_initial
        self.player_xuid_to_dehydrated_since[xuid] = None
        self._persist_player_thirst(player)
        self._apply_thirst_movement_modifier(player)

    def _notify_dehydrated(self, player) -> None:
        title = self.language_manager.GetText("THIRST_DEHYDRATED_TITLE") or "严重脱水"
        content = (
            self.language_manager.GetText("THIRST_DEHYDRATED_WARNING")
            or "口渴值已耗尽，超过一小时将致命！"
        )
        try:
            player.send_toast(title, content)
        except Exception:
            try:
                player.send_message(f"[{title}] {content}")
            except Exception:
                pass

    def _kill_from_dehydration(self, player) -> None:
        title = self.language_manager.GetText("THIRST_DEHYDRATED_DEATH_TITLE") or "严重脱水"
        content = self.language_manager.GetText("THIRST_DEHYDRATED_DEATH") or "你因严重脱水而死。"
        try:
            player.send_toast(title, content)
        except Exception:
            try:
                player.send_message(f"[{title}] {content}")
            except Exception:
                pass
        effect = resolve_effect_type("instant_damage")
        applied = apply_mob_effect(player, effect, 1, 255, particles=True, icon=True)
        if applied:
            return
        try:
            if hasattr(player, "perform_command"):
                player.perform_command("effect @s instant_damage 1 255")
        except Exception as e:
            self._safe_log('error', f"[ARS] dehydration instant_damage error: {e}")

    def _sync_dehydration_state(self, player) -> None:
        """口渴 < 0 时记录起始时间；持续超过 thirst_fatal_seconds 则瞬间伤害 255。"""
        if not self._is_survival_like(player):
            return
        xuid = self._get_player_xuid(player)
        thirst = int(self.player_xuid_to_thirst.get(xuid, self.thirst_initial))
        if thirst < 0:
            started = self.player_xuid_to_dehydrated_since.get(xuid)
            if started is None:
                self.player_xuid_to_dehydrated_since[xuid] = time.time()
                self._notify_dehydrated(player)
                return
            try:
                elapsed = time.time() - float(started)
            except Exception:
                self.player_xuid_to_dehydrated_since[xuid] = time.time()
                return
            if elapsed >= float(self.thirst_fatal_seconds):
                self._kill_from_dehydration(player)
        elif self.player_xuid_to_dehydrated_since.get(xuid) is not None:
            self.player_xuid_to_dehydrated_since[xuid] = None

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
                        else:
                            self._sync_dehydration_state(player)
                        # 口渴未变时也重挂移速，避免 transient modifier 丢失后「0 口渴仍健步如飞」
                        self._apply_thirst_movement_modifier(player)
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
        if self.nutrition_manager is not None:
            self.nutrition_manager.on_player_join(player)
        if self.zombie_virus_manager is not None:
            self.zombie_virus_manager.on_player_join(player)
        if self._is_survival_like(player):
            self._apply_thirst_movement_modifier(player)
            self._sync_dehydration_state(player)
        else:
            self._enter_non_survival_mode(player)

    @event_handler()
    def on_player_quit(self, event: PlayerQuitEvent):
        player = event.player
        self._restore_snapshot_before_persist(player)
        self._persist_player_thirst(player)
        self._clear_thirst_movement_modifier(player)
        if self.nutrition_manager is not None:
            self.nutrition_manager.on_player_quit(player)
        if self.zombie_virus_manager is not None:
            self.zombie_virus_manager.on_player_quit(player)

    @event_handler()
    def on_player_game_mode_change(self, event: PlayerGameModeChangeEvent):
        player = event.player
        new_mode = event.new_game_mode
        now_survival = new_mode == GameMode.SURVIVAL or new_mode == GameMode.ADVENTURE
        xuid = self._get_player_xuid(player)
        if now_survival:
            if xuid in self._creative_snapshots:
                self._leave_non_survival_mode(player)
            else:
                self._apply_thirst_movement_modifier(player)
                self._sync_dehydration_state(player)
                if self.nutrition_manager is not None:
                    self.nutrition_manager._apply_persistent_symptoms(player)
        else:
            self._enter_non_survival_mode(player)

    @event_handler()
    def on_actor_damage(self, event: ActorDamageEvent):
        if self.zombie_virus_manager is not None:
            self.zombie_virus_manager.on_actor_damage(event)

    @event_handler()
    def on_player_death(self, event: PlayerDeathEvent):
        player = event.player
        if not self._is_survival_like(player):
            return
        self._reset_player_thirst(player)
        if self.zombie_virus_manager is not None:
            self.zombie_virus_manager.reset_on_death(player)

    @event_handler()
    def on_player_respawn(self, event: PlayerRespawnEvent):
        player = event.player
        if self._is_survival_like(player):
            self._apply_thirst_movement_modifier(player)
            if self.nutrition_manager is not None:
                self.nutrition_manager.on_player_respawn(player)
        else:
            self._enter_non_survival_mode(player)

    @event_handler()
    def on_player_move(self, event: PlayerMoveEvent):
        try:
            player = event.player
            if not self._is_survival_like(player):
                return
            setattr(player, '_arc_moving_flag', True)
        except Exception:
            pass

    @event_handler()
    def on_player_item_consume(self, event: PlayerItemConsumeEvent):
        try:
            player = event.player
            if not self._is_survival_like(player):
                return
            item = event.item
            if item is None:
                self._log_consume_always(
                    f"player={player.name} item=None，跳过（事件未携带物品栈）",
                )
                return

            identity_list = self._collect_item_identity_strings(item)
            cfg, matched_src, lookup_key = self._find_thirst_cfg_for_item(item)
            hand = getattr(event, "hand", None)

            if self.thirst_consume_debug:
                self._safe_log(
                    'info',
                    f"[ARS][consume][verbose] player={player.name} hand={hand!r} "
                    f"identities={identity_list!r} matched_key={lookup_key!r} from={matched_src!r} "
                    f"has_cfg={cfg is not None}",
                )

            thirst_handled = False
            if cfg is not None:
                delta = int(cfg.get("delta", 0))
                self._log_consume_always(
                    f"player={player.name} hand={hand!r} identities={identity_list!r} "
                    f"→ 命中 key={lookup_key!r} thirst_delta={delta}",
                )
                self._apply_thirst_delta(player, delta, reason="consume")
                self._apply_item_buffs(player, cfg.get("buffs") or [])
                thirst_handled = True

            nutrition_handled = False
            if self.nutrition_manager is not None:
                nutrition_handled = self.nutrition_manager.on_player_consume(player, item)

            if not thirst_handled and not nutrition_handled:
                self._log_consume_always(
                    f"player={player.name} hand={hand!r} identities={identity_list!r} "
                    f"→ 未匹配 thirst_items / nutrition_items",
                )
                return

            self._persist_player_thirst(player)
        except Exception as e:
            self._safe_log('error', f"[ARS] consume event error: {e}")

    def _apply_item_buffs(self, player, buffs: list) -> None:
        for buff in buffs:
            if not isinstance(buff, dict):
                continue
            eff_name = buff.get("name")
            if not eff_name:
                continue
            try:
                duration_sec = int(buff.get("duration", 30))
            except Exception:
                duration_sec = 30
            try:
                amplifier = int(buff.get("amplifier", 0))
            except Exception:
                amplifier = 0
            try:
                effect_type = resolve_effect_type(str(eff_name))
                if effect_type is None:
                    continue
                apply_mob_effect(player, effect_type, duration_sec * 20, amplifier, ambient=True)
            except Exception as e:
                self._safe_log('error', f"[ARS] apply item buff error: {e}")
