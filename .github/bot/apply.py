#!/usr/bin/env python3
"""
Автоматический бот для применения инструкций из комментариев к PR/Issues.
Поддерживает команды вида `/bot apply:` с секциями FILE, MODE, PATTERN, REPLACEMENT, FLAGS, CONTENT.
"""

import os
import re
import sys
import logging
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any


class BotLogger:
    """Логгер для записи всех действий бота."""
    
    def __init__(self, log_file: str = None):
        self.logger = logging.getLogger('SmartSellBot')
        self.logger.setLevel(logging.INFO)
        
        # Форматирование логов
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # Консольный вывод
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
        
        # Файловый вывод
        if log_file:
            file_handler = logging.FileHandler(log_file)
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
    
    def info(self, message: str):
        self.logger.info(message)
    
    def warning(self, message: str):
        self.logger.warning(message)
    
    def error(self, message: str):
        self.logger.error(message)


class InstructionParser:
    """Парсер для извлечения инструкций из комментариев."""
    
    BOT_COMMAND_PATTERN = r'/bot\s+apply:\s*'
    SECTION_PATTERN = r'(\w+):\s*(.*?)(?=\n\w+:|$)'
    
    def __init__(self, logger: BotLogger):
        self.logger = logger
    
    def parse_comment(self, comment_text: str) -> List[Dict[str, Any]]:
        """
        Парсит комментарий и извлекает инструкции.
        
        Args:
            comment_text: Текст комментария
            
        Returns:
            Список инструкций для выполнения
        """
        instructions = []
        
        # Ищем команды бота
        bot_commands = re.finditer(
            self.BOT_COMMAND_PATTERN + r'(.*?)(?=/bot\s+apply:|$)', 
            comment_text, 
            re.DOTALL | re.IGNORECASE
        )
        
        for match in bot_commands:
            instruction_block = match.group(1).strip()
            if instruction_block:
                instruction = self._parse_instruction_block(instruction_block)
                if instruction:
                    instructions.append(instruction)
        
        self.logger.info(f"Найдено {len(instructions)} инструкций для выполнения")
        return instructions
    
    def _parse_instruction_block(self, block: str) -> Optional[Dict[str, Any]]:
        """
        Парсит блок инструкций.
        
        Args:
            block: Блок с инструкциями
            
        Returns:
            Словарь с параметрами инструкции или None
        """
        sections = {}
        
        # Извлекаем секции
        for match in re.finditer(self.SECTION_PATTERN, block, re.DOTALL | re.IGNORECASE):
            section_name = match.group(1).upper()
            section_value = match.group(2).strip()
            sections[section_name] = section_value
        
        # Проверяем обязательные поля
        if 'FILE' not in sections or 'MODE' not in sections:
            self.logger.warning("Пропущены обязательные секции FILE или MODE")
            return None
        
        instruction = {
            'file': sections['FILE'],
            'mode': sections['MODE'].upper(),
            'pattern': sections.get('PATTERN'),
            'replacement': sections.get('REPLACEMENT'),
            'flags': sections.get('FLAGS', ''),
            'content': sections.get('CONTENT', ''),
        }
        
        self.logger.info(f"Парсинг инструкции: {instruction['mode']} для файла {instruction['file']}")
        return instruction


class FileProcessor:
    """Процессор для применения изменений к файлам."""
    
    SUPPORTED_MODES = ['SET', 'APPEND', 'REPLACE', 'PATCH']
    
    def __init__(self, logger: BotLogger, base_path: str = '.'):
        self.logger = logger
        self.base_path = Path(base_path).resolve()
    
    def apply_instruction(self, instruction: Dict[str, Any]) -> bool:
        """
        Применяет инструкцию к файлу.
        
        Args:
            instruction: Словарь с параметрами инструкции
            
        Returns:
            True если операция успешна, False иначе
        """
        mode = instruction['mode']
        file_path = self.base_path / instruction['file']
        
        if mode not in self.SUPPORTED_MODES:
            self.logger.error(f"Неподдерживаемый режим: {mode}")
            return False
        
        try:
            # Создаем директории если нужно
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            if mode == 'SET':
                return self._set_file_content(file_path, instruction['content'])
            elif mode == 'APPEND':
                return self._append_to_file(file_path, instruction['content'])
            elif mode == 'REPLACE':
                return self._replace_in_file(file_path, instruction)
            elif mode == 'PATCH':
                return self._patch_file(file_path, instruction)
                
        except Exception as e:
            self.logger.error(f"Ошибка при обработке файла {file_path}: {str(e)}")
            return False
        
        return False
    
    def _set_file_content(self, file_path: Path, content: str) -> bool:
        """Устанавливает содержимое файла."""
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            self.logger.info(f"Установлено содержимое файла: {file_path}")
            return True
        except Exception as e:
            self.logger.error(f"Ошибка при записи файла {file_path}: {str(e)}")
            return False
    
    def _append_to_file(self, file_path: Path, content: str) -> bool:
        """Добавляет содержимое в конец файла."""
        try:
            with open(file_path, 'a', encoding='utf-8') as f:
                if not content.endswith('\n'):
                    content += '\n'
                f.write(content)
            self.logger.info(f"Добавлено содержимое в файл: {file_path}")
            return True
        except Exception as e:
            self.logger.error(f"Ошибка при добавлении в файл {file_path}: {str(e)}")
            return False
    
    def _replace_in_file(self, file_path: Path, instruction: Dict[str, Any]) -> bool:
        """Заменяет текст в файле по паттерну."""
        pattern = instruction.get('pattern')
        replacement = instruction.get('replacement', '')
        flags_str = instruction.get('flags', '')
        
        if not pattern:
            self.logger.error("Не указан паттерн для замены")
            return False
        
        try:
            # Определяем флаги регулярных выражений
            flags = 0
            if 'i' in flags_str.lower():
                flags |= re.IGNORECASE
            if 'm' in flags_str.lower():
                flags |= re.MULTILINE
            if 's' in flags_str.lower():
                flags |= re.DOTALL
            
            # Читаем файл
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            else:
                content = ''
            
            # Выполняем замену
            new_content = re.sub(pattern, replacement, content, flags=flags)
            
            # Записываем результат
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            self.logger.info(f"Выполнена замена в файле: {file_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка при замене в файле {file_path}: {str(e)}")
            return False
    
    def _patch_file(self, file_path: Path, instruction: Dict[str, Any]) -> bool:
        """Применяет патч к файлу (частичная замена)."""
        pattern = instruction.get('pattern')
        replacement = instruction.get('replacement', '')
        
        if not pattern:
            self.logger.error("Не указан паттерн для патча")
            return False
        
        try:
            # Читаем файл
            if not file_path.exists():
                self.logger.error(f"Файл не существует для патча: {file_path}")
                return False
            
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Ищем строки для замены
            modified = False
            for i, line in enumerate(lines):
                if re.search(pattern, line):
                    lines[i] = re.sub(pattern, replacement, line)
                    modified = True
            
            if modified:
                # Записываем изменения
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.writelines(lines)
                self.logger.info(f"Применен патч к файлу: {file_path}")
                return True
            else:
                self.logger.warning(f"Паттерн не найден в файле: {file_path}")
                return False
                
        except Exception as e:
            self.logger.error(f"Ошибка при применении патча к файлу {file_path}: {str(e)}")
            return False


class SmartSellBot:
    """Основной класс бота для обработки команд."""
    
    def __init__(self, base_path: str = '.', log_file: str = None):
        self.logger = BotLogger(log_file)
        self.parser = InstructionParser(self.logger)
        self.processor = FileProcessor(self.logger, base_path)
    
    def process_comment(self, comment_text: str) -> Dict[str, Any]:
        """
        Обрабатывает комментарий и применяет все инструкции.
        
        Args:
            comment_text: Текст комментария
            
        Returns:
            Результат выполнения операций
        """
        start_time = datetime.now()
        self.logger.info("Начало обработки комментария")
        
        instructions = self.parser.parse_comment(comment_text)
        results = {
            'total_instructions': len(instructions),
            'successful': 0,
            'failed': 0,
            'errors': []
        }
        
        for i, instruction in enumerate(instructions, 1):
            self.logger.info(f"Выполнение инструкции {i}/{len(instructions)}")
            
            if self.processor.apply_instruction(instruction):
                results['successful'] += 1
            else:
                results['failed'] += 1
                results['errors'].append(f"Ошибка в инструкции {i}: {instruction}")
        
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        self.logger.info(f"Обработка завершена за {duration:.2f} сек. "
                        f"Успешно: {results['successful']}, Ошибок: {results['failed']}")
        
        return results


def main():
    """Главная функция для запуска бота."""
    parser = argparse.ArgumentParser(description='SmartSell Bot - Автоматическое применение инструкций')
    parser.add_argument('--comment', type=str, help='Текст комментария для обработки')
    parser.add_argument('--comment-file', type=str, help='Файл с текстом комментария')
    parser.add_argument('--base-path', type=str, default='.', help='Базовый путь для файлов')
    parser.add_argument('--log-file', type=str, help='Файл для логов')
    parser.add_argument('--test', action='store_true', help='Запуск в тестовом режиме')
    
    args = parser.parse_args()
    
    # Инициализация бота
    bot = SmartSellBot(args.base_path, args.log_file)
    
    # Тестовый режим
    if args.test:
        test_comment = """
/bot apply:
FILE: test_example.txt
MODE: SET
CONTENT: Это тестовый файл, созданный ботом.
Вторая строка файла.

/bot apply:
FILE: README.md
MODE: APPEND
CONTENT: 
## Добавлено ботом
Эта секция была добавлена автоматически.
        """
        bot.logger.info("Запуск в тестовом режиме")
        results = bot.process_comment(test_comment)
        print(f"Результаты теста: {results}")
        return
    
    # Получение текста комментария
    comment_text = None
    if args.comment:
        comment_text = args.comment
    elif args.comment_file:
        try:
            with open(args.comment_file, 'r', encoding='utf-8') as f:
                comment_text = f.read()
        except Exception as e:
            print(f"Ошибка чтения файла комментария: {e}")
            sys.exit(1)
    else:
        print("Не указан текст комментария. Используйте --comment или --comment-file")
        sys.exit(1)
    
    # Обработка комментария
    results = bot.process_comment(comment_text)
    
    # Вывод результатов
    if results['failed'] > 0:
        print(f"Выполнено с ошибками: {results['successful']} успешно, {results['failed']} с ошибками")
        sys.exit(1)
    else:
        print(f"Все операции выполнены успешно: {results['successful']} инструкций")


if __name__ == '__main__':
    main()