import os
import requests
from telegram import Update
from telegram.ext import Application
import asyncio
from datetime import datetime
import logging
from telegram.error import TelegramError

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация (замените на свои значения)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = int(os.getenv("TELEGRAM_CHANNEL_ID"))
VK_GROUP_TOKEN = os.getenv("VK_GROUP_TOKEN")
VK_GROUP_ID = int(os.getenv("VK_GROUP_ID"))

async def fetch_all_telegram_posts():
    """Получает все посты из Telegram-канала с использованием chat_id."""
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    all_posts = []
    last_message_id = None
    
    try:
        while True:
            # Получаем сообщения из канала
            updates = await app.bot.get_chat_history(
                chat_id=TELEGRAM_CHANNEL_ID,
                limit=100,
                offset_id=last_message_id
            )
            
            if not updates:
                break
            
            for message in updates:
                all_posts.append(message)
                last_message_id = message.message_id
            
            await asyncio.sleep(1)  # Пауза для API Telegram
            
    except TelegramError as e:
        logger.error(f"Ошибка при получении постов из Telegram: {e}")
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}")
    
    return all_posts

def upload_photo_to_vk(photo_url: str) -> dict:
    """Загружает фото во ВКонтакте и возвращает данные."""
    try:
        # Получаем URL для загрузки
        params = {
            "access_token": VK_GROUP_TOKEN,
            "v": "5.131",
            "group_id": VK_GROUP_ID
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
        
        # Скачиваем и загружаем фото
        photo_response = requests.get(photo_url, timeout=30)
        photo_response.raise_for_status()
        files = {"photo": ("photo.jpg", photo_response.content, "image/jpeg")}
        
        upload_response = requests.post(upload_url, files=files, timeout=60)
        upload_response.raise_for_status()
        data = upload_response.json()
        
        # Сохраняем фото на стене
        save_params = {
            "access_token": VK_GROUP_TOKEN,
            "v": "5.131",
            "group_id": VK_GROUP_ID,
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
            
        return save_result["response"][0]
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Сетевая ошибка при загрузке фото: {e}")
        return None
    except Exception as e:
        logger.error(f"Неожиданная ошибка при загрузке фото: {e}")
        return None

def upload_video_to_vk(video_url: str, title: str = "Video") -> dict:
    """Загружает видео во ВКонтакте и возвращает данные."""
    try:
        # Получаем URL для загрузки видео
        params = {
            "access_token": VK_GROUP_TOKEN,
            "v": "5.131",
            "name": title[:100],  # VK ограничивает название 100 символами
            "group_id": VK_GROUP_ID,
            "description": f"Видео из Telegram от {datetime.now().strftime('%d.%m.%Y')}"
        }
        
        response = requests.get(
            "https://api.vk.com/method/video.save",
            params=params,
            timeout=30
        )
        response.raise_for_status()
        
        result = response.json()
        if "error" in result:
            logger.error(f"Ошибка получения URL для видео: {result['error']['error_msg']}")
            return None
        
        upload_data = result["response"]
        
        # Проверяем наличие upload_url
        if "upload_url" not in upload_data:
            logger.error("Нет upload_url в ответе VK API")
            return None
            
        upload_url = upload_data["upload_url"]
        
        # Скачиваем видео потоково
        video_response = requests.get(video_url, stream=True, timeout=300)
        video_response.raise_for_status()
        
        # Загружаем видео
        files = {"video_file": ("video.mp4", video_response.raw, "video/mp4")}
        upload_response = requests.post(upload_url, files=files, timeout=600)
        upload_response.raise_for_status()
        
        upload_result = upload_response.json()
        
        if "error" in upload_result:
            logger.error(f"Ошибка загрузки видео: {upload_result['error']['error_msg']}")
            return None
        
        # Возвращаем данные с правильными ключами
        return {
            "owner_id": upload_data["owner_id"],
            "video_id": upload_data["video_id"]
        }
        
    except requests.exceptions.Timeout:
        logger.error(f"Таймаут при загрузке видео {video_url}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Сетевая ошибка при загрузке видео: {e}")
        return None
    except Exception as e:
        logger.error(f"Критическая ошибка загрузки видео: {e}")
        return None

def format_telegram_date(timestamp: int) -> str:
    """Форматирует дату из Telegram в читаемый формат."""
    if timestamp is None:
        return "Неизвестно"
    dt = datetime.fromtimestamp(timestamp)
    return dt.strftime("%d.%m.%Y %H:%M")

def publish_to_vk(text: str, media_urls: list, video_url: str = None, telegram_date: int = None):
    """Публикует пост во ВКонтакте с фото и/или видео и датой публикации."""
    attachments = []
    
    # Добавляем дату публикации в конец текста
    if telegram_date is not None:
        date_str = format_telegram_date(telegram_date)
        text += f"\n\n📅 {date_str}"
    
    # Обрабатываем фото
    for url in media_urls:
        if url is not None:
            try:
                photo_data = upload_photo_to_vk(url)
                if photo_data:
                    attachments.append(f"photo{photo_data['owner_id']}_{photo_data['id']}")
                    logger.info(f"Фото {url} успешно загружено")
            except Exception as e:
                logger.error(f"Ошибка обработки фото {url}: {e}")
    
    # Обрабатываем видео
    if video_url is not None:
        try:
            video_data = upload_video_to_vk(video_url, text[:50] if text else "Video")
            if video_data:
                video_attachment = f"video{video_data['owner_id']}_{video_data['video_id']}"
                attachments.append(video_attachment)
                logger.info(f"Видео успешно загружено")
        except Exception as e:
            logger.error(f"Ошибка обработки видео {video_url}: {e}")

    params = {
        "access_token": VK_GROUP_TOKEN,
        "v": "5.131",
        "owner_id": -abs(VK_GROUP_ID),  # Отрицательное для групп
        "message": text,
        "from_group": 1  # Публикуем от имени группы
    }

    if attachments:
        params["attachments"] = ",".join(attachments)

    try:
        response = requests.post(
            "https://api.vk.com/method/wall.post",
            data=params,
            timeout=(30, 120)
        )
        response.raise_for_status()
        result = response.json()

        if "error" in result:
            error_msg = result["error"]["error_msg"]
            logger.error(f"Ошибка публикации поста: {error_msg}")
            return False
        
        logger.info("✅ Пост успешно опубликован во ВКонтакте!")
        return True

    except requests.exceptions.Timeout:
        logger.error("Таймаут при публикации поста")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Сетевая ошибка при публикации поста: {e}")
        return False
    except Exception as e:
        logger.error(f"Неожиданная ошибка при публикации поста: {e}")
        return False

async def process_media(post):
    """Обрабатывает медиафайлы из поста."""
    media_urls = []
    video_url = None
    
    try:
        # Обрабатываем фото
        if hasattr(post, 'photo') and post.photo:
            try:
                # Берем самую большую версию фото
                photo = post.photo[-1]
                file = await photo.get_file()
                file_path = file.file_path
                # Полный URL для скачивания
                media_urls.append(f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}")
                logger.info(f"Найдено фото: {file_path}")
            except Exception as e:
                logger.warning(f"Не удалось получить фото: {e}")
        
        # Обрабатываем видео
        if hasattr(post, 'video') and post.video:
            try:
                video = post.video
                file = await video.get_file()
                file_path = file.file_path
                video_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
                logger.info(f"Найдено видео: {file_path}")
            except Exception as e:
                logger.warning(f"Не удалось получить видео: {e}")
                
    except Exception as e:
        logger.error(f"Ошибка при обработке медиа: {e}")
    
    return media_urls, video_url

async def main():
    logger.info("🚀 Начинаем перенос старых постов из Telegram во ВКонтакте...")
    
    # Проверка наличия токенов
    if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, VK_GROUP_TOKEN, VK_GROUP_ID]):
        logger.error("❌ Не все переменные окружения установлены!")
        return
    
    posts = await fetch_all_telegram_posts()
    logger.info(f"📊 Найдено {len(posts)} постов для переноса")

    if not posts:
        logger.info("Нет постов для переноса")
        return

    success_count = 0
    error_count = 0

    for i, post in enumerate(posts, 1):
        logger.info(f"📝 Обрабатываем пост {i}/{len(posts)}")
        
        # Получаем текст поста
        text = post.text or post.caption or ""
        telegram_date = post.date
        
        # Обрабатываем медиа
        media_urls, video_url = await process_media(post)
        
        logger.info(f"Текст: {text[:50]}..." if text else "Без текста")
        logger.info(f"Медиа: {len(media_urls)} фото, видео: {'да' if video_url else 'нет'}")

        # Публикуем в VK
        success = publish_to_vk(text, media_urls, video_url, telegram_date)
        
        if success:
            success_count += 1
        else:
            error_count += 1

        # Увеличенная пауза для видео
        await asyncio.sleep(5)

    logger.info(f"✨ Перенос завершён! Успешно: {success_count}, с ошибками: {error_count}")

if __name__ == "__main__":
    asyncio.run(main())