import os
import json
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import logging
from datetime import datetime
import asyncio
import shutil
import zipfile
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import hashlib

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
VK_GROUP_TOKEN = os.getenv("VK_GROUP_TOKEN")
VK_GROUP_ID = os.getenv("VK_GROUP_ID")
ADMIN_ID = os.getenv("ADMIN_ID")

# Создаем директории
TEMP_DIR = "temp_media"
UPLOADS_DIR = "uploads"
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

# Лимиты Telegram API
TELEGRAM_FILE_LIMIT = 50 * 1024 * 1024  # 50 МБ

def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором"""
    if not ADMIN_ID:
        return False
    admin_ids = [int(id.strip()) for id in ADMIN_ID.split(',') if id.strip().isdigit()]
    return user_id in admin_ids

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав для использования этого бота.")
        return
    
    help_text = (
        "🤖 <b>Бот для переноса постов из Telegram в VK</b>\n\n"
        "📤 <b>Как использовать (метод с несколькими архивами):</b>\n\n"
        "1. Экспортируйте чат из Telegram Desktop\n"
        "2. При экспорте ВЫБЕРИТЕ ТОЛЬКО ОДИН тип данных:\n"
        "   • Только текстовые сообщения\n"
        "   • Только фотографии\n"
        "   • Только видео\n"
        "   • Только файлы/документы\n"
        "   • Только стикеры\n\n"
        "3. Сохраните каждый экспорт в отдельную папку\n"
        "4. Заархивируйте каждую папку отдельно\n"
        "5. Отправляйте архивы по одному\n\n"
        "📋 <b>Команды:</b>\n"
        "/start - Показать это сообщение\n"
        "/new - Начать новый сбор архива\n"
        "/stats - Статистика обработки\n"
        "/cancel - Отменить текущую обработку\n"
        "/process - Обработать все полученные архивы"
    )
    await update.message.reply_text(help_text, parse_mode='HTML')

async def new_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начинает новый сбор архивов"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав для использования этого бота.")
        return
    
    # Создаем уникальную сессию для этого пользователя
    session_id = hashlib.md5(f"{user_id}_{datetime.now()}".encode()).hexdigest()[:8]
    context.user_data['session_id'] = session_id
    context.user_data['archives'] = []
    context.user_data['processing'] = False
    
    # Создаем папку для сессии
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    context.user_data['session_dir'] = session_dir
    
    await update.message.reply_text(
        f"✅ Начата новая сессия сбора архивов.\n"
        f"ID сессии: <code>{session_id}</code>\n\n"
        f"Теперь отправляйте архивы по одному. После отправки всех архивов "
        f"используйте команду /process для начала обработки.",
        parse_mode='HTML'
    )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статистику"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав для использования этого бота.")
        return
    
    if 'archives' in context.user_data:
        archives = context.user_data['archives']
        total_size = sum(archives) / (1024 * 1024) if archives else 0
        
        text = (
            f"📊 <b>Статистика сессии:</b>\n\n"
            f"ID сессии: {context.user_data.get('session_id', 'Нет')}\n"
            f"Получено архивов: {len(archives)}\n"
            f"Общий размер: {total_size:.2f} МБ\n"
            f"Статус: {'🔄 Обработка' if context.user_data.get('processing') else '⏸ Ожидание'}\n"
        )
        
        if 'stats' in context.user_data:
            stats_data = context.user_data['stats']
            text += (
                f"\n📈 <b>Прогресс обработки:</b>\n"
                f"Всего постов: {stats_data.get('total', 0)}\n"
                f"Обработано: {stats_data.get('processed', 0)}\n"
                f"Успешно: {stats_data.get('success', 0)}\n"
                f"Ошибок: {stats_data.get('errors', 0)}"
            )
    else:
        text = "Нет активной сессии. Используйте /new для начала."
    
    await update.message.reply_text(text, parse_mode='HTML')

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отменяет текущую обработку"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав для использования этого бота.")
        return
    
    if 'processing' in context.user_data and context.user_data['processing']:
        context.user_data['processing'] = False
        await update.message.reply_text("⏹ Обработка остановлена")
    else:
        # Очищаем сессию
        if 'session_dir' in context.user_data:
            try:
                shutil.rmtree(context.user_data['session_dir'])
            except:
                pass
        
        context.user_data.clear()
        await update.message.reply_text("✅ Сессия очищена")

def identify_archive_type(extract_dir: str) -> str:
    """Определяет тип архива по содержимому"""
    files_dir = os.path.join(extract_dir, 'files')
    
    if not os.path.exists(files_dir):
        return "unknown"
    
    # Считаем количество файлов разных типов
    video_count = 0
    photo_count = 0
    sticker_count = 0
    voice_count = 0
    document_count = 0
    
    for file in os.listdir(files_dir):
        ext = os.path.splitext(file)[1].lower()
        if ext in ['.mp4', '.mov', '.avi', '.mkv', '.webm']:
            video_count += 1
        elif ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
            photo_count += 1
        elif ext in ['.webp', '.tgs'] or 'sticker' in file.lower():
            sticker_count += 1
        elif ext in ['.ogg', '.mp3', '.m4a'] or 'voice' in file.lower():
            voice_count += 1
        elif ext in ['.pdf', '.doc', '.docx', '.xls', '.zip', '.rar']:
            document_count += 1
    
    # Определяем основной тип
    counts = {
        'video': video_count,
        'photo': photo_count,
        'sticker': sticker_count,
        'voice': voice_count,
        'document': document_count
    }
    
    if max(counts.values()) > 0:
        return max(counts, key=counts.get)
    return "unknown"

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает полученный ZIP файл"""
    user_id = update.effective_user.id
    
    # Проверяем права доступа
    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав для использования этого бота.")
        return
    
    # Проверяем наличие активной сессии
    if 'session_id' not in context.user_data:
        await update.message.reply_text(
            "❌ Нет активной сессии. Используйте /new для начала сбора архивов."
        )
        return
    
    document = update.message.document
    
    # Проверяем расширение
    if not document.file_name or not document.file_name.endswith('.zip'):
        await update.message.reply_text("❌ Пожалуйста, отправьте ZIP-архив")
        return
    
    # Проверяем размер (предупреждаем, если близко к лимиту)
    if document.file_size > TELEGRAM_FILE_LIMIT:
        await update.message.reply_text(
            f"❌ Файл слишком большой ({document.file_size / 1024 / 1024:.1f} МБ).\n"
            f"Лимит Telegram: {TELEGRAM_FILE_LIMIT / 1024 / 1024:.0f} МБ.\n"
            f"Разделите экспорт на более мелкие части."
        )
        return
    
    if document.file_size > 40 * 1024 * 1024:  # 40 МБ
        await update.message.reply_text(
            f"⚠️ Файл большой ({document.file_size / 1024 / 1024:.1f} МБ). "
            f"Загрузка может занять время..."
        )
    
    status_msg = await update.message.reply_text(
        f"📥 Получен архив: {document.file_name}\n"
        f"Размер: {document.file_size / 1024 / 1024:.1f} МБ\n"
        f"Начинаю сохранение..."
    )
    
    # Создаем папку для этого архива
    archive_dir = os.path.join(context.user_data['session_dir'], f"archive_{len(context.user_data['archives'])}")
    os.makedirs(archive_dir, exist_ok=True)
    zip_path = os.path.join(archive_dir, document.file_name)
    
    try:
        # Скачиваем архив
        file = await context.bot.get_file(document.file_id)
        await file.download_to_drive(zip_path)
        
        await status_msg.edit_text("📦 Распаковываю архив...")
        
        # Распаковываем
        extract_zip(zip_path, archive_dir)
        
        # Определяем тип архива
        archive_type = identify_archive_type(archive_dir)
        
        # Сохраняем информацию об архиве
        context.user_data['archives'].append({
            'path': archive_dir,
            'type': archive_type,
            'name': document.file_name,
            'size': document.file_size
        })
        
        await status_msg.edit_text(
            f"✅ Архив #{len(context.user_data['archives'])} сохранен!\n"
            f"📁 Тип: <b>{archive_type}</b>\n"
            f"📦 Файлов: {len(os.listdir(os.path.join(archive_dir, 'files'))) if os.path.exists(os.path.join(archive_dir, 'files')) else 0} в папке files\n\n"
            f"Всего архивов в сессии: {len(context.user_data['archives'])}\n"
            f"Используйте /process для обработки всех архивов.",
            parse_mode='HTML'
        )
        
    except Exception as e:
        logger.error(f"Ошибка при сохранении архива: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {str(e)}")

def extract_zip(zip_path: str, extract_to: str) -> str:
    """Распаковывает ZIP архив"""
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_to)
    
    # Ищем result.json
    for root, dirs, files in os.walk(extract_to):
        if 'result.json' in files:
            return os.path.join(root, 'result.json')
    
    raise FileNotFoundError("result.json не найден в архиве")

def merge_json_data(json_files: List[str]) -> List[Dict]:
    """Объединяет данные из нескольких JSON файлов"""
    all_messages = []
    
    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if 'messages' in data:
                for msg in data['messages']:
                    if msg.get('type') == 'message' and msg.get('text'):
                        all_messages.append(msg)
        except Exception as e:
            logger.error(f"Ошибка при чтении {json_file}: {e}")
    
    # Сортируем по дате
    all_messages.sort(key=lambda x: x.get('date', ''))
    
    return all_messages

async def process_archives(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает все собранные архивы"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав для использования этого бота.")
        return
    
    if 'archives' not in context.user_data or not context.user_data['archives']:
        await update.message.reply_text("❌ Нет архивов для обработки. Сначала отправьте их.")
        return
    
    if context.user_data.get('processing'):
        await update.message.reply_text("❌ Обработка уже запущена")
        return
    
    status_msg = await update.message.reply_text(
        f"🔄 Начинаю обработку {len(context.user_data['archives'])} архивов..."
    )
    
    context.user_data['processing'] = True
    
    try:
        # Собираем все JSON файлы
        json_files = []
        for archive in context.user_data['archives']:
            for root, dirs, files in os.walk(archive['path']):
                if 'result.json' in files:
                    json_files.append(os.path.join(root, 'result.json'))
        
        if not json_files:
            await status_msg.edit_text("❌ Не найдено JSON файлов в архивах")
            return
        
        # Объединяем все сообщения
        all_messages = merge_json_data(json_files)
        
        await status_msg.edit_text(
            f"📊 Найдено {len(all_messages)} постов в объединенных архивах.\n"
            f"Начинаю перенос в VK..."
        )
        
        # Инициализируем статистику
        context.user_data['stats'] = {
            'total': len(all_messages),
            'processed': 0,
            'success': 0,
            'errors': 0
        }
        
        # Обрабатываем каждый пост
        for i, msg in enumerate(all_messages, 1):
            if not context.user_data.get('processing', True):
                await update.message.reply_text("⏹ Обработка остановлена пользователем")
                break
            
            try:
                # Ищем медиа во всех архивах
                media_paths = []
                video_path = None
                
                for archive in context.user_data['archives']:
                    paths, video = extract_media_from_message(msg, archive['path'])
                    media_paths.extend(paths)
                    if video and not video_path:
                        video_path = video
                
                # Извлекаем текст
                text = format_message_text(msg)
                date = format_date(msg.get('date', ''))
                
                # Публикуем
                if text or media_paths or video_path:
                    success = publish_to_vk(text, media_paths, video_path, date)
                else:
                    success = False
                
                # Обновляем статистику
                context.user_data['stats']['processed'] += 1
                if success:
                    context.user_data['stats']['success'] += 1
                else:
                    context.user_data['stats']['errors'] += 1
                
                # Обновляем статус
                if i % 5 == 0:
                    stats = context.user_data['stats']
                    await update.message.reply_text(
                        f"🔄 Прогресс: {i}/{len(all_messages)}\n"
                        f"✅ Успешно: {stats['success']}\n"
                        f"❌ Ошибок: {stats['errors']}"
                    )
                
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"Ошибка обработки поста {i}: {e}")
                context.user_data['stats']['errors'] += 1
                context.user_data['stats']['processed'] += 1
        
        # Финальный отчет
        stats = context.user_data['stats']
        await update.message.reply_text(
            f"✅ <b>Перенос завершен!</b>\n\n"
            f"Всего постов: {stats['total']}\n"
            f"Обработано: {stats['processed']}\n"
            f"Успешно: {stats['success']}\n"
            f"Ошибок: {stats['errors']}",
            parse_mode='HTML'
        )
        
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        await update.message.reply_text(f"❌ Ошибка обработки: {str(e)}")
    
    finally:
        context.user_data['processing'] = False

# Остальные функции (format_message_text, format_date, upload_photo_to_vk и т.д.)
# остаются такими же, как в предыдущей версии

def main():
    """Запуск бота"""
    # Проверяем переменные
    if not all([TELEGRAM_BOT_TOKEN, VK_GROUP_TOKEN, VK_GROUP_ID, ADMIN_ID]):
        logger.error("❌ Не все переменные окружения установлены!")
        return
    
    # Создаем приложение
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Добавляем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_collection))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("process", process_archives))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    logger.info("🚀 Бот запущен1...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()