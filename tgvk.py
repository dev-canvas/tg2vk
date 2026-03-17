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
ADMIN_ID = os.getenv("ADMIN_ID")  # ID администратора (можно несколько через запятую)

# Создаем директорию для временных файлов
TEMP_DIR = "temp_media"
os.makedirs(TEMP_DIR, exist_ok=True)

def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором"""
    if not ADMIN_ID:
        return False
    
    # Разделяем строку с ID админов (поддерживает несколько ID через запятую)
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
        "📤 <b>Как использовать:</b>\n"
        "1. Экспортируйте чат из Telegram Desktop:\n"
        "   - Откройте канал/чат\n"
        "   - Нажмите ⋮ → 'Экспортировать историю чата'\n"
        "   - Выберите формат JSON\n"
        "   - Скачайте архив\n\n"
        "2. Отправьте мне ZIP-архив с экспортированными данными\n\n"
        "3. Я обработаю файл и перенесу посты в VK\n\n"
        "📋 <b>Команды:</b>\n"
        "/start - Показать это сообщение\n"
        "/stats - Статистика обработки\n"
        "/cancel - Отменить текущую обработку"
    )
    await update.message.reply_text(help_text, parse_mode='HTML')

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статистику текущей обработки"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав для использования этого бота.")
        return
    
    if 'stats' in context.user_data:
        stats_data = context.user_data['stats']
        text = (
            f"📊 <b>Статистика обработки:</b>\n\n"
            f"Всего постов: {stats_data.get('total', 0)}\n"
            f"Обработано: {stats_data.get('processed', 0)}\n"
            f"Успешно: {stats_data.get('success', 0)}\n"
            f"Ошибок: {stats_data.get('errors', 0)}"
        )
    else:
        text = "Нет активной обработки. Отправьте ZIP-архив с экспортом"
    
    await update.message.reply_text(text, parse_mode='HTML')

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отменяет текущую обработку"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав для использования этого бота.")
        return
    
    if 'processing' in context.user_data:
        context.user_data['processing'] = False
        await update.message.reply_text("✅ Обработка отменена")
    else:
        await update.message.reply_text("Нет активной обработки")

def extract_zip(zip_path: str, extract_to: str) -> str:
    """Распаковывает ZIP архив и возвращает путь к result.json"""
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_to)
        
        # Ищем result.json в распакованных файлах
        for root, dirs, files in os.walk(extract_to):
            if 'result.json' in files:
                return os.path.join(root, 'result.json')
        
        raise FileNotFoundError("result.json не найден в архиве")
    except zipfile.BadZipFile:
        raise Exception("Файл поврежден или не является ZIP-архивом")

def parse_telegram_export(json_path: str) -> List[Dict]:
    """Парсит экспортированный JSON из Telegram"""
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError:
        raise Exception("Не удалось прочитать JSON файл. Возможно, он поврежден")
    
    messages = []
    
    # Проверяем структуру JSON
    if isinstance(data, dict):
        if 'messages' in data:
            # Стандартный формат экспорта Telegram
            for msg in data['messages']:
                if msg.get('type') == 'message' and msg.get('text'):
                    messages.append(msg)
        elif 'chats' in data and 'list' in data['chats']:
            # Альтернативный формат
            for chat in data['chats']['list']:
                if 'messages' in chat:
                    for msg in chat['messages']:
                        if msg.get('text'):
                            messages.append(msg)
    elif isinstance(data, list):
        # Если данные - это просто список сообщений
        messages = [msg for msg in data if msg.get('text')]
    
    logger.info(f"Найдено {len(messages)} сообщений в JSON")
    return messages

def extract_media_from_message(msg: Dict, export_dir: str) -> Tuple[List[str], Optional[str]]:
    """Извлекает пути к медиафайлам из сообщения"""
    media_paths = []
    video_path = None
    
    # Путь к папке с файлами
    files_dir = os.path.join(export_dir, 'files')
    
    if not os.path.exists(files_dir):
        logger.warning(f"Папка files не найдена: {files_dir}")
        return media_paths, video_path
    
    # Проверяем наличие фото
    if 'photo' in msg and msg['photo']:
        photo_list = msg['photo']
        if isinstance(photo_list, str):
            photo_list = [photo_list]
        
        for photo in photo_list:
            if isinstance(photo, str):
                photo_path = os.path.join(files_dir, photo)
                if os.path.exists(photo_path):
                    media_paths.append(photo_path)
    
    # Проверяем наличие файлов
    if 'file' in msg and msg['file']:
        file_path = os.path.join(files_dir, msg['file'])
        if os.path.exists(file_path):
            file_ext = os.path.splitext(file_path)[1].lower()
            video_extensions = ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v']
            photo_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']
            
            if file_ext in video_extensions:
                video_path = file_path
            elif file_ext in photo_extensions:
                media_paths.append(file_path)
    
    # Проверяем медиа в разных полях
    if 'media_type' in msg:
        if msg['media_type'] in ['video_file', 'video', 'animation'] and 'file' in msg:
            file_path = os.path.join(files_dir, msg['file'])
            if os.path.exists(file_path):
                video_path = file_path
        elif msg['media_type'] in ['photo', 'sticker', 'animated_webp', 'voice'] and 'file' in msg:
            file_path = os.path.join(files_dir, msg['file'])
            if os.path.exists(file_path):
                media_paths.append(file_path)
    
    return media_paths, video_path

def format_message_text(msg: Dict) -> str:
    """Форматирует текст сообщения"""
    text = ""
    
    if isinstance(msg.get('text'), str):
        text = msg['text']
    elif isinstance(msg.get('text'), list):
        # Обрабатываем форматированный текст
        for part in msg['text']:
            if isinstance(part, str):
                text += part
            elif isinstance(part, dict) and 'text' in part:
                text += part['text']
    
    # Ограничиваем длину текста для VK
    if len(text) > 9000:
        text = text[:9000] + "...\n\n[Текст обрезан из-за ограничений VK]"
    
    return text.strip()

def format_date(date_str: str) -> str:
    """Форматирует дату из Telegram"""
    if not date_str:
        return ""
    
    try:
        # Пробуем разные форматы даты
        if 'T' in date_str:
            # ISO формат: 2024-01-15T14:30:00
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            return dt.strftime("%d.%m.%Y %H:%M")
        else:
            # Простой формат: 2024-01-15 14:30:00
            dt = datetime.strptime(date_str[:19], '%Y-%m-%d %H:%M:%S')
            return dt.strftime("%d.%m.%Y %H:%M")
    except:
        return date_str

def upload_photo_to_vk(photo_path: str) -> Optional[str]:
    """Загружает фото в VK"""
    try:
        # Получаем URL для загрузки
        params = {
            "access_token": VK_GROUP_TOKEN,
            "v": "5.131",
            "group_id": abs(int(VK_GROUP_ID))
        }
        response = requests.get(
            "https://api.vk.com/method/photos.getWallUploadServer",
            params=params,
            timeout=30
        )
        response.raise_for_status()
        
        result = response.json()
        if "error" in result:
            logger.error(f"Ошибка VK API: {result['error']['error_msg']}")
            return None
        
        upload_url = result["response"]["upload_url"]
        
        # Загружаем фото
        with open(photo_path, 'rb') as f:
            files = {"photo": f}
            upload_response = requests.post(upload_url, files=files, timeout=60)
            upload_response.raise_for_status()
            data = upload_response.json()
        
        # Сохраняем фото
        save_params = {
            "access_token": VK_GROUP_TOKEN,
            "v": "5.131",
            "group_id": abs(int(VK_GROUP_ID)),
            "photo": data["photo"],
            "server": data["server"],
            "hash": data["hash"]
        }
        save_response = requests.post(
            "https://api.vk.com/method/photos.saveWallPhoto",
            data=save_params,
            timeout=30
        )
        save_response.raise_for_status()
        
        save_result = save_response.json()
        if "error" in save_result:
            logger.error(f"Ошибка сохранения фото: {save_result['error']['error_msg']}")
            return None
        
        photo_data = save_result["response"][0]
        return f"photo{photo_data['owner_id']}_{photo_data['id']}"
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Сетевая ошибка при загрузке фото: {e}")
        return None
    except Exception as e:
        logger.error(f"Ошибка загрузки фото {photo_path}: {e}")
        return None

def upload_video_to_vk(video_path: str, title: str = "Video") -> Optional[str]:
    """Загружает видео в VK"""
    try:
        # Получаем URL для загрузки
        params = {
            "access_token": VK_GROUP_TOKEN,
            "v": "5.131",
            "name": title[:100],
            "group_id": abs(int(VK_GROUP_ID)),
            "description": f"Видео из Telegram"
        }
        
        response = requests.get(
            "https://api.vk.com/method/video.save",
            params=params,
            timeout=30
        )
        response.raise_for_status()
        
        result = response.json()
        if "error" in result:
            logger.error(f"Ошибка получения URL: {result['error']['error_msg']}")
            return None
        
        upload_data = result["response"]
        
        if "upload_url" not in upload_data:
            logger.error("Нет upload_url в ответе")
            return None
        
        # Загружаем видео
        with open(video_path, 'rb') as f:
            files = {"video_file": f}
            upload_response = requests.post(
                upload_data["upload_url"], 
                files=files, 
                timeout=600
            )
            upload_response.raise_for_status()
        
        return f"video{upload_data['owner_id']}_{upload_data['video_id']}"
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Сетевая ошибка при загрузке видео: {e}")
        return None
    except Exception as e:
        logger.error(f"Ошибка загрузки видео {video_path}: {e}")
        return None

def publish_to_vk(text: str, media_paths: List[str], video_path: Optional[str] = None, 
                  date: Optional[str] = None) -> bool:
    """Публикует пост в VK"""
    attachments = []
    
    # Добавляем дату
    if date:
        text += f"\n\n📅 {date}"
    
    # Загружаем фото (не более 10 фото на пост)
    for photo_path in media_paths[:10]:
        attachment = upload_photo_to_vk(photo_path)
        if attachment:
            attachments.append(attachment)
            logger.info(f"✅ Загружено фото: {os.path.basename(photo_path)}")
    
    # Загружаем видео (если есть)
    if video_path:
        attachment = upload_video_to_vk(video_path, text[:50])
        if attachment:
            attachments.append(attachment)
            logger.info(f"✅ Загружено видео: {os.path.basename(video_path)}")
    
    # Публикуем пост
    params = {
        "access_token": VK_GROUP_TOKEN,
        "v": "5.131",
        "owner_id": -abs(int(VK_GROUP_ID)),
        "message": text[:10000],  # VK ограничение
        "from_group": 1
    }
    
    if attachments:
        params["attachments"] = ",".join(attachments)
    
    try:
        response = requests.post(
            "https://api.vk.com/method/wall.post",
            data=params,
            timeout=30
        )
        response.raise_for_status()
        result = response.json()
        
        if "error" in result:
            logger.error(f"Ошибка публикации: {result['error']['error_msg']}")
            return False
        
        logger.info(f"✅ Пост опубликован в VK")
        return True
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Сетевая ошибка при публикации: {e}")
        return False
    except Exception as e:
        logger.error(f"Ошибка публикации: {e}")
        return False

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает полученный ZIP файл"""
    user_id = update.effective_user.id
    
    # Проверяем права доступа
    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав для использования этого бота.")
        logger.warning(f"Попытка доступа неавторизованного пользователя: {user_id}")
        return
    
    # Проверяем наличие необходимых переменных
    if not all([TELEGRAM_BOT_TOKEN, VK_GROUP_TOKEN, VK_GROUP_ID]):
        await update.message.reply_text("❌ Ошибка конфигурации бота. Проверьте переменные окружения.")
        return
    
    document = update.message.document
    
    # Проверяем расширение
    if not document.file_name or not document.file_name.endswith('.zip'):
        await update.message.reply_text("❌ Пожалуйста, отправьте ZIP-архив")
        return
    
    # Проверяем размер файла (предупреждаем, но не блокируем)
    if document.file_size > 100 * 1024 * 1024:  # 100 МБ
        await update.message.reply_text(
            f"⚠️ Файл очень большой ({document.file_size / 1024 / 1024:.1f} МБ). "
            f"Обработка может занять длительное время."
        )
    
    status_msg = await update.message.reply_text("📥 Получен архив, начинаю распаковку...")
    
    # Создаем уникальную папку для этого пользователя
    user_dir = os.path.join(TEMP_DIR, str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    
    try:
        # Скачиваем архив
        file = await context.bot.get_file(document.file_id)
        zip_path = os.path.join(user_dir, document.file_name)
        await file.download_to_drive(zip_path)
        
        await status_msg.edit_text("📦 Распаковываю архив...")
        
        # Распаковываем архив
        json_path = extract_zip(zip_path, user_dir)
        export_dir = os.path.dirname(json_path)
        
        await status_msg.edit_text("📊 Анализирую файл...")
        
        # Парсим JSON
        try:
            messages = parse_telegram_export(json_path)
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка при анализе JSON: {str(e)}")
            return
        
        if not messages:
            await status_msg.edit_text("❌ Не найдено сообщений в файле")
            return
        
        await status_msg.edit_text(
            f"📊 Найдено {len(messages)} постов. Начинаю перенос в VK...\n"
            f"Это может занять некоторое время."
        )
        
        # Инициализируем статистику
        context.user_data['processing'] = True
        context.user_data['stats'] = {
            'total': len(messages),
            'processed': 0,
            'success': 0,
            'errors': 0
        }
        
        # Обрабатываем каждый пост
        for i, msg in enumerate(messages, 1):
            if not context.user_data.get('processing', True):
                await update.message.reply_text("⏹ Обработка остановлена пользователем")
                break
            
            try:
                # Извлекаем текст и медиа
                text = format_message_text(msg)
                media_paths, video_path = extract_media_from_message(msg, export_dir)
                date = format_date(msg.get('date', ''))
                
                # Публикуем только если есть контент
                if text or media_paths or video_path:
                    success = publish_to_vk(text, media_paths, video_path, date)
                else:
                    success = False
                    logger.info(f"Пост {i} пропущен - нет контента")
                
                # Обновляем статистику
                context.user_data['stats']['processed'] += 1
                if success:
                    context.user_data['stats']['success'] += 1
                else:
                    context.user_data['stats']['errors'] += 1
                
                # Обновляем статус каждые 5 постов
                if i % 5 == 0:
                    stats_data = context.user_data['stats']
                    await update.message.reply_text(
                        f"🔄 Прогресс: {i}/{len(messages)}\n"
                        f"✅ Успешно: {stats_data['success']}\n"
                        f"❌ Ошибок: {stats_data['errors']}"
                    )
                
                # Пауза между постами
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"Ошибка обработки поста {i}: {e}")
                context.user_data['stats']['errors'] += 1
                context.user_data['stats']['processed'] += 1
        
        # Финальный отчет
        stats_data = context.user_data['stats']
        await update.message.reply_text(
            f"✅ <b>Перенос завершен!</b>\n\n"
            f"Всего постов: {stats_data['total']}\n"
            f"Обработано: {stats_data['processed']}\n"
            f"Успешно: {stats_data['success']}\n"
            f"Ошибок: {stats_data['errors']}",
            parse_mode='HTML'
        )
        
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        await update.message.reply_text(f"❌ Ошибка обработки: {str(e)}")
    
    finally:
        # Очищаем временные файлы
        context.user_data['processing'] = False
        try:
            shutil.rmtree(user_dir)
            logger.info(f"Временные файлы удалены: {user_dir}")
        except Exception as e:
            logger.error(f"Ошибка при удалении временных файлов: {e}")

def main():
    """Запуск бота"""
    # Проверяем наличие всех необходимых переменных
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN не установлен!")
        return
    
    if not VK_GROUP_TOKEN:
        logger.error("❌ VK_GROUP_TOKEN не установлен!")
        return
    
    if not VK_GROUP_ID:
        logger.error("❌ VK_GROUP_ID не установлен!")
        return
    
    if not ADMIN_ID:
        logger.error("❌ ADMIN_ID не установлен! Бот будет доступен только администраторам.")
        return
    
    try:
        # Проверяем, что VK_GROUP_ID можно преобразовать в число
        int(VK_GROUP_ID)
    except ValueError:
        logger.error("❌ VK_GROUP_ID должен быть числом!")
        return
    
    # Проверяем ADMIN_ID
    admin_ids = [id.strip() for id in ADMIN_ID.split(',') if id.strip()]
    valid_admins = []
    for admin_id in admin_ids:
        try:
            valid_admins.append(int(admin_id))
        except ValueError:
            logger.warning(f"❌ Неверный формат ADMIN_ID: {admin_id} - должен быть числом")
    
    if not valid_admins:
        logger.error("❌ Нет валидных ID администраторов!")
        return
    
    logger.info(f"✅ Администраторы бота: {valid_admins}")
    
    # Создаем приложение
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Добавляем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    logger.info("🚀 Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()