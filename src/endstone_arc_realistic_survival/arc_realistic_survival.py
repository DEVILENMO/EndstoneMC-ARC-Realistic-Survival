import datetime
import os
import json
import math

from endstone import GameMode
from endstone.command import Command, CommandSender
from endstone.event import event_handler, PlayerItemConsumeEvent, PlayerMoveEvent, PlayerJoinEvent, PlayerQuitEvent
from endstone.plugin import Plugin
from endstone.form import ActionForm, Button, ModalForm, Label, TextInput
from endstone.potion import Effect, EffectType

from .DatabaseManager import DatabaseManager
from .LanguageManager import LanguageManager
from .NutritionManager import NUTRIENT_KEYS, NutritionManager
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
        self.thirst_consume_debug = False
        self.thirst_task = None
        self.nutrition_manager = None
    
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
        # 保存所有玩家口渴值与营养值
        try:
            for player in self.server.online_players:
                self._persist_player_thirst(player)
                if self.nutrition_manager is not None:
                    self.nutrition_manager.clear_symptoms(player)
                    self.nutrition_manager.persist_player(player)
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
        # 重新加载配置与物品效果，并重启定时器
        try:
            self._load_thirst_settings()
            self._load_thirst_items_config()
            if self.nutrition_manager is not None:
                self.nutrition_manager.load_settings()
                self.nutrition_manager.load_items_config()
            if self.thirst_task is not None:
                try:
                    self.thirst_task.cancel()
                except Exception:
                    pass
                self.thirst_task = None
            self._start_thirst_timer()
            if self.nutrition_manager is not None:
                self.nutrition_manager.start_timer()
        except Exception as e:
            self._safe_log('error', f"[ARS] reload settings error: {e}")

    def _show_survival_config_panel(self, player) -> None:
        try:
            nm = self.nutrition_manager
            title = "ARC Realistic Survival 配置"
            content_lines = [
                "修改后提交即写入配置并热重载",
                "口渴: tick秒/倍数/整数",
                "营养: 衰减秒数/每次衰减/初始值/提示冷却",
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

                    self.setting_manager.SetSetting("thirst_tick_seconds", str(new_tick))
                    self.setting_manager.SetSetting("thirst_decay_per_tick", str(new_decay))
                    self.setting_manager.SetSetting("thirst_moving_multiplier", str(new_move))
                    self.setting_manager.SetSetting("thirst_initial", str(new_initial))
                    self.setting_manager.SetSetting("nutrition_tick_seconds", str(new_n_tick))
                    self.setting_manager.SetSetting("nutrition_decay_per_tick", str(new_n_decay))
                    self.setting_manager.SetSetting("nutrition_initial", str(new_n_initial))
                    self.setting_manager.SetSetting("nutrition_warn_cooldown_seconds", str(new_n_cooldown))

                    self._reload_survival_settings()
                    sender.send_message("[ARS] 配置已保存并重载")
                except Exception as e:
                    sender.send_message(f"[ARS] 配置提交失败: {e}")

            panel = ModalForm(
                title=title,
                controls=[
                    header, input_tick, input_decay, input_move, input_initial,
                    input_nutrition_tick, input_nutrition_decay, input_nutrition_initial, input_nutrition_cooldown,
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
        except Exception as e:
            self._safe_log('error', f"[ARCRealisticSurvival] load thirst settings error: {e}")

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
        if self.nutrition_manager is not None:
            self.nutrition_manager.on_player_join(player)

    @event_handler()
    def on_player_quit(self, event: PlayerQuitEvent):
        player = event.player
        self._persist_player_thirst(player)
        if self.nutrition_manager is not None:
            self.nutrition_manager.on_player_quit(player)

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
                effect_type = EffectType.get(str(eff_name).lower())
                if effect_type is None:
                    continue
                player.add_effect(Effect(effect_type, duration_sec * 20, amplifier, ambient=True))
            except Exception as e:
                self._safe_log('error', f"[ARS] apply item buff error: {e}")
