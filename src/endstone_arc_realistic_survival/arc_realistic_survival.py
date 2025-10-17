import datetime
import os
import json
import math

from endstone.command import Command, CommandSender
from endstone.event import event_handler, PlayerItemConsumeEvent, PlayerMoveEvent, PlayerJoinEvent, PlayerQuitEvent, PlayerDeathEvent
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
            "description": "ARC Realistic Survival 配置面板",
            "usages": ["/ars"],
            "permissions": ["arc_realistic_survival.command.config"]
        },
        "arsdebug": {
            "description": "ARC Realistic Survival 调试命令",
            "usages": ["/arsdebug"],
            "permissions": ["arc_realistic_survival.command.debug"]
        }
    }

    permissions = {
        "arc_realistic_survival.command.config": {
            "description": "允许打开生存配置面板",
            "default": "op"
        },
        "arc_realistic_survival.command.debug": {
            "description": "允许使用调试命令",
            "default": "op"
        }
    }

    def __init__(self):
        super().__init__()
        # 生存-口渴系统相关
        self.player_xuid_to_thirst = {}
        self.player_moving_flags = {}  # 存储玩家移动状态的字典
        self.thirst_decay_per_second = 0.1  # 每秒减少的口渴值
        self.thirst_moving_multiplier = 2.0  # 移动时的倍数
        self.thirst_initial = 100.0
        self.thirst_task = None
        self.thirst_damage_timer = 0  # 掉血计时器（秒）
    
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
        
        # 初始化设置管理器
        self.setting_manager = SettingManager()
        
        # 先加载语言配置，再初始化语言管理器
        language_code = self.setting_manager.GetSetting("language") or "CN"
        self.language_manager = LanguageManager(language_code.upper())
        
        # 初始化默认配置
        self._init_default_settings()
        
        # 初始化数据库管理器（仅生存相关）
        db_path = os.path.join("Plugins", "ARCRealisticSurvival", "ars_survival.db")
        self._safe_log('info', f"[ARCRealisticSurvival] Database path: {db_path}")
        self.db_manager = DatabaseManager(db_path)
        
        # 创建表（仅生存相关）
        self._create_survival_tables()
        # 加载生存-口渴系统配置
        self._load_thirst_settings()

    def on_enable(self) -> None:
        self._safe_log('info', "[ARCRealisticSurvival] on_enable is called!")
        self.register_events(self)

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

    def on_command(self, sender: CommandSender, command: Command, args: list[str]) -> bool:
        '''
            命令路由器
        '''
        self.execute_command(sender, args, False)
        return True
    
    def execute_command(self, sender: CommandSender, args: list[str], is_from_ui: bool) -> None:
        """执行命令"""
        if len(args) == 0:
            # /ars - 打开配置面板
            self._handle_config_panel(sender)
        elif args[0] == "debug":
            # /ars debug - 调试命令
            self._handle_debug_command(sender, args[1:] if len(args) > 1 else [])
        else:
            sender.send_message("未知命令，使用 /ars 打开配置面板")
    
    def _handle_config_panel(self, sender: CommandSender) -> None:
        """处理配置面板命令"""
        if not hasattr(sender, 'send_form'):
            sender.send_message(self.language_manager.GetText("PLAYER_ONLY_COMMAND") or "仅限玩家使用")
            return
        
        if (hasattr(sender, 'has_permission') and sender.has_permission('arc_realistic_survival.command.config')) or getattr(sender, 'is_op', False):
            self._show_survival_config_panel(sender)
        else:
            sender.send_message(self.language_manager.GetText("NO_PERMISSION") or "没有权限")
    
    def _handle_reload_command(self, sender: CommandSender) -> None:
        """处理重载命令"""
        if hasattr(sender, 'is_op') and not sender.is_op:
            sender.send_message(self.language_manager.GetText("NO_PERMISSION") or "没有权限")
            return
        
        self._reload_survival_settings()
        sender.send_message(self.language_manager.GetText("CONFIG_RELOADED") or "[ARS] 配置与物品效果已重载")
    
    def _handle_debug_command(self, sender: CommandSender, args: list[str]) -> None:
        """处理调试命令"""
        if not hasattr(sender, 'is_op') or not sender.is_op:
            sender.send_message("需要OP权限")
            return
        
        if len(args) == 0:
            sender.send_message("调试命令用法:")
            sender.send_message("/ars debug items - 查看数据库中的物品配置")
            sender.send_message("/ars debug thirst <玩家名> - 查看玩家口渴值")
            return
        
        if args[0] == "items":
            # 显示数据库中的物品配置
            try:
                items = self.db_manager.query_all("SELECT * FROM thirst_items ORDER BY item_name")
                if not items:
                    sender.send_message("数据库中没有配置任何物品")
                else:
                    sender.send_message(f"数据库中共有 {len(items)} 个配置的物品:")
                    for item in items:
                        buffs_text = ""
                        if item['buffs']:
                            try:
                                buffs = json.loads(item['buffs'])
                                if buffs:
                                    buffs_text = f" (Buff: {', '.join([f'{b['name']}({b['duration']}s)' for b in buffs])})"
                            except:
                                pass
                        sender.send_message(f"- {item['item_name']} (ID: {item['item_id']}) - 口渴值: {item['thirst_delta']}{buffs_text}")
            except Exception as e:
                sender.send_message(f"查询数据库失败: {e}")
        
        elif args[0] == "thirst" and len(args) > 1:
            # 查看玩家口渴值
            player_name = args[1]
            try:
                player = None
                for p in self.server.online_players:
                    if p.name.lower() == player_name.lower():
                        player = p
                        break
                
                if not player:
                    sender.send_message(f"玩家 {player_name} 不在线")
                    return
                
                xuid = self._get_player_xuid(player)
                thirst = self.player_xuid_to_thirst.get(xuid, self.thirst_initial)
                sender.send_message(f"玩家 {player.name} 的口渴值: {thirst}")
                
            except Exception as e:
                sender.send_message(f"查询玩家口渴值失败: {e}")
        
        else:
            sender.send_message("未知的调试命令")
    

    def _reload_survival_settings(self) -> None:
        # 重新加载配置与物品效果，并重启口渴定时器
        try:
            self._load_thirst_settings()
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
        """显示生存配置面板"""
        try:
            from endstone.form import ActionForm
            
            title = self.language_manager.GetText("CONFIG_PANEL_TITLE") or "ARC Realistic Survival 配置"
            form = ActionForm(
                title=title,
                content=self.language_manager.GetText("CONFIG_PANEL_DESCRIPTION") or "选择要进行的操作:"
            )
            
            # 添加配置按钮
            form.add_button(
                self.language_manager.GetText("CONFIG_THIRST_SETTINGS") or "口渴系统配置",
                on_click=lambda sender: self._show_thirst_config_form(sender)
            )
            
            # 添加重载按钮
            form.add_button(
                self.language_manager.GetText("RELOAD_CONFIG") or "重载配置",
                on_click=lambda sender: self._handle_reload_command(sender)
            )
            
            # 添加物品管理按钮
            form.add_button(
                self.language_manager.GetText("MANAGE_THIRST_ITEMS") or "管理物品口渴值设定",
                on_click=lambda sender: self._show_items_management_panel(sender)
            )
            
            player.send_form(form)
            
        except Exception as e:
            self._safe_log('error', f"[ARS] show config panel error: {e}")
    
    def _show_thirst_config_form(self, player) -> None:
        """显示口渴配置表单"""
        try:
            title = self.language_manager.GetText("THIRST_CONFIG_TITLE") or "口渴系统配置"
            content_lines = [
                self.language_manager.GetText("CONFIG_DESCRIPTION") or "修改后提交即写入配置并热重载",
                self.language_manager.GetText("CONFIG_UNITS") or "单位: 数值/倍数/整数",
            ]
            header = Label(text="\n".join(content_lines))
            
            input_decay = TextInput(
                label=self.language_manager.GetText("THIRST_DECAY_PER_SECOND") or "thirst_decay_per_second",
                placeholder=self.language_manager.GetText("THIRST_DECAY_PLACEHOLDER") or "每秒衰减的口渴值",
                default_value=str(self.thirst_decay_per_second)
            )
            input_move = TextInput(
                label=self.language_manager.GetText("THIRST_MOVING_MULTIPLIER") or "thirst_moving_multiplier",
                placeholder=self.language_manager.GetText("THIRST_MOVING_PLACEHOLDER") or "移动时衰减倍数(>=1.0)",
                default_value=str(self.thirst_moving_multiplier)
            )
            input_initial = TextInput(
                label=self.language_manager.GetText("THIRST_INITIAL") or "thirst_initial",
                placeholder=self.language_manager.GetText("THIRST_INITIAL_PLACEHOLDER") or "初始口渴值(0-100)",
                default_value=str(self.thirst_initial)
            )

            def on_submit(sender, json_str: str):
                try:
                    data = json.loads(json_str)
                    # data[1..3] 依次是三个输入框的值
                    new_decay = float(data[1])
                    new_move = float(data[2])
                    new_initial = float(data[3])

                    if new_decay < 0:
                        raise ValueError(self.language_manager.GetText("ERROR_DECAY_NEGATIVE") or "decay < 0")
                    if new_move < 1.0:
                        raise ValueError(self.language_manager.GetText("ERROR_MULTIPLIER_TOO_LOW") or "moving multiplier < 1.0")
                    if new_initial < 0 or new_initial > 100:
                        raise ValueError(self.language_manager.GetText("ERROR_INITIAL_OUT_OF_RANGE") or "initial out of [0,100]")

                    # 写回配置文件
                    self.setting_manager.SetSetting("thirst_decay_per_second", str(new_decay))
                    self.setting_manager.SetSetting("thirst_moving_multiplier", str(new_move))
                    self.setting_manager.SetSetting("thirst_initial", str(new_initial))

                    # 同步到内存并热重载
                    self._reload_survival_settings()
                    sender.send_message(self.language_manager.GetText("CONFIG_SAVED") or "[ARS] 配置已保存并重载")
                except Exception as e:
                    sender.send_message(f"{self.language_manager.GetText('CONFIG_SAVE_FAILED') or '[ARS] 配置提交失败'}: {e}")

            panel = ModalForm(
                title=title,
                controls=[header, input_decay, input_move, input_initial],
                on_close=lambda s: None,
                on_submit=on_submit
            )
            player.send_form(panel)
        except Exception as e:
            self._safe_log('error', f"[ARS] show thirst config form error: {e}")
    
    def _show_items_management_panel(self, player) -> None:
        """显示物品管理面板"""
        try:
            from endstone.form import ActionForm
            
            title = self.language_manager.GetText("ITEMS_MANAGEMENT_TITLE") or "物品口渴值管理"
            form = ActionForm(
                title=title,
                content=self.language_manager.GetText("ITEMS_MANAGEMENT_DESCRIPTION") or "选择要进行的操作:"
            )
            
            # 添加查看已配置物品按钮
            form.add_button(
                self.language_manager.GetText("VIEW_CONFIGURED_ITEMS") or "查看已配置物品",
                on_click=lambda sender: self._show_configured_items_panel(sender)
            )
            
            # 添加从背包添加物品按钮
            form.add_button(
                self.language_manager.GetText("ADD_ITEM_FROM_INVENTORY") or "从背包添加物品",
                on_click=lambda sender: self._show_inventory_items_panel(sender)
            )
            
            # 添加返回按钮
            form.add_button(
                self.language_manager.GetText("BACK") or "返回",
                on_click=lambda sender: self._show_survival_config_panel(sender)
            )
            
            player.send_form(form)
            
        except Exception as e:
            self._safe_log('error', f"[ARS] show items management panel error: {e}")
    
    def _show_inventory_items_panel(self, player) -> None:
        """显示背包物品选择面板"""
        try:
            # 获取背包物品
            inventory_items = self._get_player_inventory_items(player)
            
            from endstone.form import ActionForm
            title = self.language_manager.GetText("INVENTORY_ITEMS_TITLE") or "背包物品"
            
            if not inventory_items:
                form = ActionForm(
                    title=title,
                    content=self.language_manager.GetText("NO_ITEMS_IN_INVENTORY") or "背包中没有物品",
                    on_close=lambda sender: self._show_items_management_panel(sender)
                )
                player.send_form(form)
                return
            
            form = ActionForm(
                title=title,
                content=self.language_manager.GetText("SELECT_ITEM_TO_CONFIG") or "选择要配置口渴效果的物品:"
            )
            
            for item in inventory_items:
                display_name = f"{item['name']} (ID: {item['type']}) x{item['count']}"
                form.add_button(
                    display_name,
                    on_click=lambda sender, item_info=item: self._show_item_config_form(sender, item_info)
                )
            
            form.add_button(
                self.language_manager.GetText("BACK") or "返回",
                on_click=lambda sender: self._show_items_management_panel(sender)
            )
            
            player.send_form(form)
            
        except Exception as e:
            self._safe_log('error', f"[ARS] show inventory items panel error: {e}")
    
    def _show_configured_items_panel(self, player) -> None:
        """显示已配置物品面板"""
        try:
            # 从数据库获取已配置的物品
            items = self.db_manager.query_all("SELECT * FROM thirst_items ORDER BY item_name")
            
            from endstone.form import ActionForm
            title = self.language_manager.GetText("CONFIGURED_ITEMS_TITLE") or "已配置物品"
            form = ActionForm(
                title=title,
                content=self.language_manager.GetText("CONFIGURED_ITEMS_DESCRIPTION") or "已配置口渴效果的物品:"
            )
            
            if not items:
                form.add_button(
                    self.language_manager.GetText("NO_CONFIGURED_ITEMS") or "暂无配置物品",
                    on_click=lambda sender: None
                )
            else:
                for item in items:
                    buffs_text = ""
                    if item['buffs']:
                        try:
                            buffs = json.loads(item['buffs'])
                            if buffs:
                                buffs_text = f" (Buff: {', '.join([f'{b['name']}({b['duration']}s)' for b in buffs])})"
                        except:
                            pass
                    
                    display_name = f"{item['item_name']} (ID: {item['item_id']}) - {self.language_manager.GetText('THIRST_VALUE') or '口渴值'}: {item['thirst_delta']}{buffs_text}"
                    form.add_button(
                        display_name,
                        on_click=lambda sender, item_id=item['id']: self._edit_existing_item(sender, item_id)
                    )
            
            form.add_button(
                self.language_manager.GetText("BACK") or "返回",
                on_click=lambda sender: self._show_items_management_panel(sender)
            )
            
            player.send_form(form)
            
        except Exception as e:
            self._safe_log('error', f"[ARS] show configured items panel error: {e}")
    
    def _show_item_config_form(self, player, item) -> None:
        """显示物品配置表单"""
        try:
            from endstone.form import ModalForm, Label, TextInput
            
            title = self.language_manager.GetText("ITEM_CONFIG_TITLE") or "配置物品口渴效果"
            
            # 创建标签显示物品信息
            item_label = Label(text=f"{self.language_manager.GetText('ITEM_NAME') or '物品'}: {item['name']} (ID: {item['type']})")
            
            # 创建输入框
            thirst_input = TextInput(
                label=self.language_manager.GetText("THIRST_DELTA") or "口渴值变化",
                placeholder=self.language_manager.GetText("THIRST_DELTA_PLACEHOLDER") or "输入口渴值变化量 (正数增加，负数减少)",
                default_value="0.0"
            )
            
            buff_names_input = TextInput(
                label=self.language_manager.GetText("BUFF_NAMES") or "Buff名称 (可选)",
                placeholder=self.language_manager.GetText("BUFF_NAMES_PLACEHOLDER") or "输入Buff名称，多个用逗号分隔",
                default_value=""
            )
            
            buff_durations_input = TextInput(
                label=self.language_manager.GetText("BUFF_DURATIONS") or "Buff持续时间 (可选)",
                placeholder=self.language_manager.GetText("BUFF_DURATIONS_PLACEHOLDER") or "输入Buff持续时间(秒)，多个用逗号分隔",
                default_value=""
            )
            
            buff_amplifiers_input = TextInput(
                label=self.language_manager.GetText("BUFF_AMPLIFIERS") or "Buff强度 (可选)",
                placeholder=self.language_manager.GetText("BUFF_AMPLIFIERS_PLACEHOLDER") or "输入Buff强度等级，多个用逗号分隔",
                default_value=""
            )
            
            def on_submit(sender, json_str: str):
                try:
                    data = json.loads(json_str)
                    # data[1..4] 依次是四个输入框的值
                    thirst_delta = float(data[1])
                    buff_names = data[2].strip()
                    buff_durations = data[3].strip()
                    buff_amplifiers = data[4].strip()
                    
                    buffs = []
                    if buff_names and buff_durations:
                        names = [name.strip() for name in buff_names.split(',') if name.strip()]
                        durations = [int(d.strip()) for d in buff_durations.split(',') if d.strip()]
                        amplifiers = [int(a.strip()) for a in buff_amplifiers.split(',') if a.strip()] if buff_amplifiers else []
                        
                        for i, name in enumerate(names):
                            if i < len(durations):
                                buff_data = {
                                    'name': name,
                                    'duration': durations[i]
                                }
                                # 如果有强度配置，使用配置的强度，否则默认为1
                                if i < len(amplifiers):
                                    buff_data['amplifier'] = amplifiers[i]
                                else:
                                    buff_data['amplifier'] = 1
                                buffs.append(buff_data)
                    
                    # 保存到数据库
                    self._save_thirst_item(item['type'], item['name'], thirst_delta, buffs)
                    sender.send_message(f"{self.language_manager.GetText('ITEM_CONFIGURED') or '[ARS] 已配置物品'}: {item['name']} {self.language_manager.GetText('THIRST_EFFECT') or '的口渴效果'}")
                    
                except Exception as e:
                    sender.send_message(f"{self.language_manager.GetText('CONFIG_FAILED') or '[ARS] 配置失败'}: {str(e)}")
            
            form = ModalForm(
                title=title,
                controls=[item_label, thirst_input, buff_names_input, buff_durations_input, buff_amplifiers_input],
                on_close=lambda s: None,
                on_submit=on_submit
            )
            
            player.send_form(form)
            
        except Exception as e:
            self._safe_log('error', f"[ARS] show item config form error: {e}")
    
    def _save_thirst_item(self, item_id: str, item_name: str, thirst_delta: float, buffs: list) -> None:
        """保存口渴物品到数据库"""
        try:
            # 确保物品ID是小写格式
            item_id = str(item_id).lower()
            
            buffs_json = json.dumps(buffs) if buffs else None
            now = datetime.datetime.utcnow().isoformat()
            
            # 检查是否已存在
            existing = self.db_manager.query_one("SELECT id FROM thirst_items WHERE item_id=?", (item_id,))
            
            if existing:
                # 更新
                data = {
                    "item_name": item_name,
                    "thirst_delta": thirst_delta,
                    "buffs": buffs_json,
                    "updated_at": now
                }
                self.db_manager.update("thirst_items", data, "item_id=?", (item_id,))
            else:
                # 插入
                data = {
                    "item_id": item_id,
                    "item_name": item_name,
                    "thirst_delta": thirst_delta,
                    "buffs": buffs_json,
                    "created_at": now,
                    "updated_at": now
                }
                self.db_manager.insert("thirst_items", data)
                
        except Exception as e:
            self._safe_log('error', f"[ARCRealisticSurvival] save thirst item error: {e}")
    
    def _edit_existing_item(self, player, item_id: int) -> None:
        """编辑已存在的物品"""
        try:
            item = self.db_manager.query_one("SELECT * FROM thirst_items WHERE id=?", (item_id,))
            if not item:
                player.send_message(self.language_manager.GetText("ITEM_NOT_EXISTS") or "[ARS] 物品不存在")
                return
            
            from endstone.form import ModalForm, Label, TextInput
            
            title = self.language_manager.GetText("EDIT_ITEM_TITLE") or "编辑物品口渴效果"
            
            # 创建标签显示物品信息
            item_label = Label(text=f"{self.language_manager.GetText('ITEM_NAME') or '物品'}: {item['item_name']} (ID: {item['item_id']})")
            
            # 创建输入框
            thirst_input = TextInput(
                label=self.language_manager.GetText("THIRST_DELTA") or "口渴值变化",
                placeholder=self.language_manager.GetText("THIRST_DELTA_PLACEHOLDER") or "输入口渴值变化量 (正数增加，负数减少)",
                default_value=str(item['thirst_delta'])
            )
            
            buffs_text = ""
            buff_durations_text = ""
            buff_amplifiers_text = ""
            if item['buffs']:
                try:
                    buffs = json.loads(item['buffs'])
                    if buffs:
                        buffs_text = ', '.join([b['name'] for b in buffs])
                        buff_durations_text = ', '.join([str(b['duration']) for b in buffs])
                        # 处理强度，如果没有强度字段则默认为1
                        amplifiers = []
                        for b in buffs:
                            amplifiers.append(str(b.get('amplifier', 1)))
                        buff_amplifiers_text = ', '.join(amplifiers)
                except:
                    pass
            
            buff_names_input = TextInput(
                label=self.language_manager.GetText("BUFF_NAMES") or "Buff名称 (可选)",
                placeholder=self.language_manager.GetText("BUFF_NAMES_PLACEHOLDER") or "输入Buff名称，多个用逗号分隔",
                default_value=buffs_text
            )
            
            buff_durations_input = TextInput(
                label=self.language_manager.GetText("BUFF_DURATIONS") or "Buff持续时间 (可选)",
                placeholder=self.language_manager.GetText("BUFF_DURATIONS_PLACEHOLDER") or "输入Buff持续时间(秒)，多个用逗号分隔",
                default_value=buff_durations_text
            )
            
            buff_amplifiers_input = TextInput(
                label=self.language_manager.GetText("BUFF_AMPLIFIERS") or "Buff强度 (可选)",
                placeholder=self.language_manager.GetText("BUFF_AMPLIFIERS_PLACEHOLDER") or "输入Buff强度等级，多个用逗号分隔",
                default_value=buff_amplifiers_text
            )
            
            def on_submit(sender, json_str: str):
                try:
                    data = json.loads(json_str)
                    # data[1..4] 依次是四个输入框的值
                    thirst_delta = float(data[1])
                    buff_names = data[2].strip()
                    buff_durations = data[3].strip()
                    buff_amplifiers = data[4].strip()
                    
                    buffs = []
                    if buff_names and buff_durations:
                        names = [name.strip() for name in buff_names.split(',') if name.strip()]
                        durations = [int(d.strip()) for d in buff_durations.split(',') if d.strip()]
                        amplifiers = [int(a.strip()) for a in buff_amplifiers.split(',') if a.strip()] if buff_amplifiers else []
                        
                        for i, name in enumerate(names):
                            if i < len(durations):
                                buff_data = {
                                    'name': name,
                                    'duration': durations[i]
                                }
                                # 如果有强度配置，使用配置的强度，否则默认为1
                                if i < len(amplifiers):
                                    buff_data['amplifier'] = amplifiers[i]
                                else:
                                    buff_data['amplifier'] = 1
                                buffs.append(buff_data)
                    
                    # 更新数据库
                    buffs_json = json.dumps(buffs) if buffs else None
                    now = datetime.datetime.utcnow().isoformat()
                    
                    data = {
                        "thirst_delta": thirst_delta,
                        "buffs": buffs_json,
                        "updated_at": now
                    }
                    self.db_manager.update("thirst_items", data, "id=?", (item_id,))
                    
                    sender.send_message(f"{self.language_manager.GetText('ITEM_UPDATED') or '[ARS] 已更新物品'}: {item['item_name']} {self.language_manager.GetText('THIRST_EFFECT') or '的口渴效果'}")
                    
                except Exception as e:
                    sender.send_message(f"{self.language_manager.GetText('UPDATE_FAILED') or '[ARS] 更新失败'}: {str(e)}")
            
            form = ModalForm(
                title=title,
                controls=[item_label, thirst_input, buff_names_input, buff_durations_input, buff_amplifiers_input],
                on_close=lambda s: None,
                on_submit=on_submit
            )
            
            player.send_form(form)
            
        except Exception as e:
            self._safe_log('error', f"[ARS] edit existing item error: {e}")
    
    # 数据库（仅生存）
    # 生存-口渴系统：数据库与配置
    def _create_survival_tables(self) -> None:
        thirst_fields = {
            "xuid": "TEXT PRIMARY KEY",
            "player_name": "TEXT NOT NULL",
            "thirst": "REAL NOT NULL",
            "updated_at": "TEXT NOT NULL"
        }
        if self.db_manager.create_table("player_thirst", thirst_fields):
            self._safe_log('info', "[ARCRealisticSurvival] player_thirst table ready")
        else:
            self._safe_log('error', "[ARCRealisticSurvival] Failed to create player_thirst table")
        
        # 口渴物品表
        thirst_items_fields = {
            "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
            "item_id": "TEXT NOT NULL UNIQUE",
            "item_name": "TEXT NOT NULL",
            "thirst_delta": "REAL NOT NULL",
            "buffs": "TEXT",  # JSON格式存储buff列表
            "created_at": "TEXT NOT NULL",
            "updated_at": "TEXT NOT NULL"
        }
        if self.db_manager.create_table("thirst_items", thirst_items_fields):
            self._safe_log('info', "[ARCRealisticSurvival] thirst_items table ready")
        else:
            self._safe_log('error', "[ARCRealisticSurvival] Failed to create thirst_items table")

    def _load_thirst_settings(self) -> None:
        try:
            # 加载口渴配置
            val = self.setting_manager.GetSetting("thirst_decay_per_second")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_decay_per_second", "0.1")
                self.thirst_decay_per_second = 0.1
            else:
                self.thirst_decay_per_second = max(0.0, float(val))

            val = self.setting_manager.GetSetting("thirst_moving_multiplier")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_moving_multiplier", "2.0")
                self.thirst_moving_multiplier = 2.0
            else:
                self.thirst_moving_multiplier = max(1.0, float(val))

            val = self.setting_manager.GetSetting("thirst_initial")
            if val is None or val == "":
                self.setting_manager.SetSetting("thirst_initial", "100.0")
                self.thirst_initial = 100.0
            else:
                self.thirst_initial = float(val)
        except Exception as e:
            self._safe_log('error', f"[ARCRealisticSurvival] load thirst settings error: {e}")

    # 生存-口渴系统：内部工具
    def _clamp_thirst(self, value: float) -> float:
        return max(0.0, min(100.0, value))

    def _get_player_xuid(self, player) -> str:
        try:
            return getattr(player, 'xuid', None) or getattr(player, 'uuid', None) or player.name
        except Exception:
            return player.name

    def _get_player_inventory_items(self, player):
        """获取玩家背包物品"""
        try:
            items = []
            # 使用EndStone inventory API遍历玩家背包
            inventory = player.inventory
            
            for slot_index in range(inventory.size):
                item_stack = inventory.get_item(slot_index)
                
                if item_stack and item_stack.type and item_stack.amount > 0:
                    # 获取物品类型ID和显示名称
                    item_type_id = item_stack.type.id
                    item_type_translation_key = item_stack.type.translation_key
                    
                    # 尝试获取本地化的物品名称
                    try:
                        display_name = self.server.language.translate(
                            item_type_translation_key,
                            None,
                            player.locale
                        )
                    except:
                        # 如果翻译失败，使用类型ID作为备选
                        display_name = item_type_id
                    
                    # 如果有自定义显示名称，优先使用
                    if item_stack.item_meta and item_stack.item_meta.has_display_name:
                        display_name = item_stack.item_meta.display_name
                    
                    # 获取附魔信息
                    enchants = {}
                    if item_stack.item_meta and item_stack.item_meta.enchants:
                        try:
                            # 附魔信息直接是字典格式 {enchant_key: level}
                            if isinstance(item_stack.item_meta.enchants, dict):
                                enchants = item_stack.item_meta.enchants.copy()
                            else:
                                # 如果是列表格式，转换为字典
                                for enchant in item_stack.item_meta.enchants:
                                    try:
                                        if hasattr(enchant, 'type') and hasattr(enchant.type, 'id'):
                                            enchant_id = enchant.type.id
                                        else:
                                            enchant_id = str(enchant.type)
                                        
                                        if hasattr(enchant, 'level'):
                                            enchant_level = enchant.level
                                        else:
                                            enchant_level = 1  # 默认等级
                                        
                                        enchants[enchant_id] = enchant_level
                                    except Exception as enchant_error:
                                        self._safe_log('warning', f"[ARCRealisticSurvival] Failed to get enchant info: {str(enchant_error)}")
                                        continue
                        except Exception as e:
                            self._safe_log('warning', f"[ARCRealisticSurvival] Failed to process enchants: {str(e)}")
                            enchants = {}
                    
                    # 获取Lore信息
                    lore = []
                    if item_stack.item_meta and item_stack.item_meta.has_lore:
                        try:
                            lore = item_stack.item_meta.lore
                            if not isinstance(lore, list):
                                lore = []
                        except Exception as lore_error:
                            self._safe_log('warning', f"[ARCRealisticSurvival] Failed to get lore info: {str(lore_error)}")
                            lore = []
                    
                    items.append({
                        'type': item_type_id,  # 使用类型ID而不是ItemType对象
                        'type_translation_key': item_type_translation_key,  # 保存翻译键
                        'name': display_name,
                        'count': item_stack.amount,
                        'data': item_stack.data,
                        'enchants': enchants,  # 保存附魔信息
                        'lore': lore,  # 保存Lore信息
                        'slot_index': slot_index  # 记录槽位索引，用于后续操作
                    })
            
            return items
        except Exception as e:
            self._safe_log('error', f"[ARCRealisticSurvival] Get player inventory error: {str(e)}")
            return []

    def _load_player_thirst(self, player) -> float:
        xuid = self._get_player_xuid(player)
        self._safe_log('info', f"[ARCRealisticSurvival] Loading thirst for player {player.name} (XUID: {xuid})")
        row = self.db_manager.query_one("SELECT thirst FROM player_thirst WHERE xuid=?", (xuid,))
        if row is None:
            self.player_xuid_to_thirst[xuid] = self.thirst_initial
            self._safe_log('info', f"[ARCRealisticSurvival] New player {player.name}, setting initial thirst: {self.thirst_initial}")
            # 插入一条
            self.db_manager.insert("player_thirst", {
                "xuid": xuid,
                "player_name": player.name,
                "thirst": self.thirst_initial,
                "updated_at": datetime.datetime.utcnow().isoformat()
            })
        else:
            self.player_xuid_to_thirst[xuid] = float(row["thirst"])
            self._safe_log('info', f"[ARCRealisticSurvival] Loaded existing thirst for {player.name}: {row['thirst']}")
        return self.player_xuid_to_thirst[xuid]

    def _persist_player_thirst(self, player) -> None:
        try:
            xuid = self._get_player_xuid(player)
            thirst = float(self.player_xuid_to_thirst.get(xuid, self.thirst_initial))
            # self._safe_log('info', f"[ARCRealisticSurvival] Saving thirst for player {player.name}: {thirst}")
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
                # self._safe_log('info', f"[ARCRealisticSurvival] Inserted new thirst record for {player.name}")
            else:
                self.db_manager.update("player_thirst", data, "xuid=?", (xuid,))
                # self._safe_log('info', f"[ARCRealisticSurvival] Updated thirst record for {player.name}")
        except Exception as e:
            self._safe_log('error', f"[ARCRealisticSurvival] persist thirst error: {e}")

    def _apply_thirst_delta(self, player, delta: float) -> float:
        xuid = self._get_player_xuid(player)
        current = float(self.player_xuid_to_thirst.get(xuid, self.thirst_initial))
        new_val = self._clamp_thirst(current + delta)
        self.player_xuid_to_thirst[xuid] = new_val
        
        # 每次变动都显示提示
        actual_change = math.floor(new_val) - math.floor(current)
        if abs(actual_change) > 0:
            player.send_tip(f"口渴值: {int(new_val)}")
        
        return new_val

    def _start_thirst_timer(self) -> None:
        try:
            if self.thirst_task is not None:
                try:
                    self.thirst_task.cancel()
                except Exception:
                    pass
                self.thirst_task = None
            
            # 每秒执行一次口渴值计算
            def tick():
                try:
                    # 增加掉血计时器
                    self.thirst_damage_timer += 1
                    
                    for player in self.server.online_players:
                        base_decay = self.thirst_decay_per_second
                        # 移动状态由最近移动事件标记
                        xuid = self._get_player_xuid(player)
                        moving_flag = self.player_moving_flags.get(xuid, False)
                        decay = base_decay if not moving_flag else base_decay * self.thirst_moving_multiplier
                        if decay > 0:
                            self._apply_thirst_delta(player, -decay)
                            # 调试：显示口渴值变化
                            # if moving_flag:
                            #     self._safe_log('info', f"[ARCRealisticSurvival] Player {player.name} moving, decay: {decay} (base: {base_decay} x {self.thirst_moving_multiplier})")
                            # else:
                            #     self._safe_log('info', f"[ARCRealisticSurvival] Player {player.name} stationary, decay: {decay}")
                        
                        # 检查口渴值掉血（每10秒检查一次）
                        if self.thirst_damage_timer >= 10:
                            current_thirst = self.player_xuid_to_thirst.get(xuid, self.thirst_initial)
                            if current_thirst <= 0:
                                # 口渴值为0，造成伤害
                                self._apply_thirst_damage(player)
                            else:
                                # 口渴值大于0，重置计时器（避免重复提示）
                                pass
                        
                        # 每次循环后重置移动标记（在下一秒重新检测）
                        if xuid in self.player_moving_flags:
                            del self.player_moving_flags[xuid]
                        # 定期保存
                        self._persist_player_thirst(player)
                    
                    # 每10秒重置掉血计时器
                    if self.thirst_damage_timer >= 10:
                        self.thirst_damage_timer = 0
                        
                except Exception as e:
                    self._safe_log('error', f"[ARCRealisticSurvival] thirst timer error: {e}")

            # 优先使用服务器调度器（Endstone: scheduler.run_task(plugin, task, delay, period)）
            scheduler = getattr(self.server, 'scheduler', None)
            if scheduler is not None and hasattr(scheduler, 'run_task'):
                # delay 与 period 单位均为 tick（20 tick = 1 秒）
                self.thirst_task = scheduler.run_task(self, tick, 20, 20)
            else:
                # 无调度器时使用 threading.Timer 兜底
                try:
                    import threading
                    class _ArcTimer:
                        def __init__(self, interval, target):
                            self.interval = interval
                            self.target = target
                            self._stop = False
                            self._timer = None
                            self._schedule()

                        def _schedule(self):
                            if self._stop:
                                return
                            self._timer = threading.Timer(self.interval, self._run)
                            self._timer.daemon = True
                            self._timer.start()

                        def _run(self):
                            try:
                                self.target()
                            finally:
                                self._schedule()

                        def cancel(self):
                            self._stop = True
                            try:
                                if self._timer is not None:
                                    self._timer.cancel()
                            except Exception:
                                pass

                    self.thirst_task = _ArcTimer(1.0, tick)
                    self._safe_log('warning', "[ARCRealisticSurvival] No server scheduler; using threading.Timer fallback for thirst.")
                except Exception:
                    self._safe_log('warning', "[ARCRealisticSurvival] No scheduler available; thirst will not tick.")
        except Exception as e:
            self._safe_log('error', f"[ARCRealisticSurvival] start thirst timer error: {e}")

    # 生存-口渴系统：事件
    @event_handler()
    def on_player_join(self, event: PlayerJoinEvent):
        player = event.player
        # self._safe_log('info', f"[ARCRealisticSurvival] Player {player.name} joined, loading thirst")
        self._load_player_thirst(player)

    @event_handler()
    def on_player_quit(self, event: PlayerQuitEvent):
        player = event.player
        # self._safe_log('info', f"[ARCRealisticSurvival] Player {player.name} quit, saving thirst")
        self._persist_player_thirst(player)

    @event_handler()
    def on_player_move(self, event: PlayerMoveEvent):
        player = event.player
        xuid = self._get_player_xuid(player)
        self.player_moving_flags[xuid] = True

    @event_handler()
    def on_player_item_consume(self, event: PlayerItemConsumeEvent):
        try:
            player = event.player
            item = event.item
            
            # 调试：打印物品信息
            # self._safe_log('info', f"[ARS] Player {player.name} consumed item: {item}")
            
            item_key = None
            try:
                # 尝试多种方式获取物品ID
                if hasattr(item, 'type') and hasattr(item.type, 'id'):
                    item_key = item.type.id
                elif hasattr(item, 'type'):
                    item_key = str(item.type)
                elif hasattr(item, 'name'):
                    item_key = item.name
                else:
                    item_key = str(item)
            except Exception as e:
                self._safe_log('error', f"[ARS] Failed to get item key: {e}")
                item_key = None
                
            if item_key is None:
                self._safe_log('warning', f"[ARS] Could not determine item key for consumed item")
                return
            
            item_id = str(item_key).lower()
            # self._safe_log('info', f"[ARS] Item ID: {item_id}")
            
            # 从数据库获取物品配置
            item_config = self.db_manager.query_one("SELECT * FROM thirst_items WHERE item_id=?", (item_id,))
            if item_config is None:
                # self._safe_log('info', f"[ARS] No thirst config found for item: {item_id}")
                return
            
            # self._safe_log('info', f"[ARS] Found thirst config for {item_id}: {item_config}")
            
            # 应用口渴值变化
            thirst_delta = float(item_config['thirst_delta'])
            self._apply_thirst_delta(player, thirst_delta)
            
            # 应用Buff效果
            if item_config['buffs']:
                try:
                    buffs = json.loads(item_config['buffs'])
                    for buff in buffs:
                        amplifier = buff.get('amplifier', 1)  # 默认强度为1
                        self._apply_buff_to_player(player, buff['name'], buff['duration'], amplifier)
                except Exception as e:
                    self._safe_log('error', f"[ARCRealisticSurvival] apply buffs error: {e}")
            
            self._persist_player_thirst(player)
        except Exception as e:
            self._safe_log('error', f"[ARS] consume event error: {e}")
    
    @event_handler
    def on_actor_death(self, event: PlayerDeathEvent):
        # 玩家死亡时，重置口渴值为初始值
        try:
            player = event.player
            xuid = self._get_player_xuid(player)
            
            # 重置口渴值为初始值
            self.player_xuid_to_thirst[xuid] = self.thirst_initial
            
            # 保存到数据库
            self._persist_player_thirst(player)
            
            self._safe_log('info', f"[ARCRealisticSurvival] 玩家 {player.name} 死亡，重置口渴值为 {self.thirst_initial}")
            
        except Exception as e:
            self._safe_log('error', f"[ARCRealisticSurvival] 处理玩家死亡事件时出错: {e}")
    
    def _apply_thirst_damage(self, player) -> None:
        """给口渴值为0的玩家造成伤害"""
        try:
            # 检查玩家是否还活着
            if not hasattr(player, 'health') or player.health <= 0:
                return
            
            # 检查玩家当前口渴值是否仍然为0
            xuid = self._get_player_xuid(player)
            current_thirst = self.player_xuid_to_thirst.get(xuid, self.thirst_initial)
            if current_thirst > 0:
                return  # 口渴值已经恢复，不造成伤害
            
            # 使用 instant_damage 效果造成伤害
            self._apply_buff_to_player(player, "instant_damage", 1, 1)
            
            # 提示玩家需要补充水分
            player.send_message("§c[ARS] 你因缺水而受到伤害！请立即补充水分！")
            
            # 发送屏幕提示
            player.send_tip("§受到口渴伤害！")
                
        except Exception as e:
            self._safe_log('error', f"[ARCRealisticSurvival] apply thirst damage error: {e}")

    def _apply_buff_to_player(self, player, buff_name: str, duration: int, amplifier: int = 1) -> None:
        """给玩家应用Buff效果"""
        try:
            # 使用命令：effect <player> <effect> <time> <amplifier>
            cmd = f"effect {player.name} {buff_name.lower()} {duration} {amplifier}"
            self.server.dispatch_command(self.server.command_sender, cmd)
        except Exception as e:
            self._safe_log('error', f"[ARCRealisticSurvival] apply buff error: {e}")
