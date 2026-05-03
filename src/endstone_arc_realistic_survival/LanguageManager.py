import os
from pathlib import Path

MAIN_PATH = 'plugins/ARCRealisticSurvival'

class LanguageManager:
    language_dict = {}  # Class variable shared across instances

    def __init__(self, default_language_code):
        self.language_code = default_language_code.upper()
        if self.language_code not in LanguageManager.language_dict:
            LanguageManager.language_dict[self.language_code] = {}

        # Use Path for cross-platform compatibility
        self.language_file_path = Path(MAIN_PATH) / f"{self.language_code}.txt"
        self._load_language_file()

    def _load_language_file(self):
        # Create config directory if not exists
        self.language_file_path.parent.mkdir(exist_ok=True)

        # Create language file if not exists
        if not self.language_file_path.exists():
            self.language_file_path.touch()

        # Load language file content
        loaded_keys = []
        with self.language_file_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # 跳过空行和注释行
                if not line or line.startswith("#"):
                    continue
                # 确保行中包含等号
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip()
                    # 只有当键不为空时才添加
                    if key:
                        # 如果键已存在，优先保留非空值（避免空值覆盖有效值）
                        if key in LanguageManager.language_dict[self.language_code]:
                            existing_value = LanguageManager.language_dict[self.language_code][key]
                            # 如果旧值为空但新值非空，使用新值
                            if not existing_value and value:
                                LanguageManager.language_dict[self.language_code][key] = value
                            # 如果旧值非空但新值为空，保留旧值（不更新）
                            elif existing_value and not value:
                                pass  # 保留旧值，忽略空值
                            # 如果都非空，保留第一个（不更新，避免后面的覆盖前面的）
                            elif existing_value and value:
                                pass  # 保留第一个值
                            # 如果都为空，保持为空
                            else:
                                pass
                        else:
                            # 键不存在，直接添加
                            LanguageManager.language_dict[self.language_code][key] = value
                        # 记录所有遇到的键
                        if key not in loaded_keys:
                            loaded_keys.append(key)

    def GetText(self, key, lang_code=None):
        # If no language code provided, use instance's language code
        target_lang = (lang_code or self.language_code).upper()

        # If the target language hasn't been loaded yet, load it
        if target_lang not in LanguageManager.language_dict:
            temp_manager = LanguageManager(target_lang)

        # If key doesn't exist in target language, add it
        if key not in LanguageManager.language_dict[target_lang]:
            target_file_path = Path(MAIN_PATH) / f"{target_lang}.txt"
            with target_file_path.open("a", encoding="utf-8") as f:
                f.write(f"\n{key}=")
            LanguageManager.language_dict[target_lang][key] = ""

        if not LanguageManager.language_dict[target_lang][key]:
            return ''
        else:
            return LanguageManager.language_dict[target_lang][key]