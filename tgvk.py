import os
import requests
from telegram import Update
from telegram.ext import Application
import asyncio
from datetime import datetime
import logging

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
    """Получает все посты из Telegram-канала."""
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    offset = 0
    limit = 100
    all_posts = []
    
    while True:
        try:
            updates = await app.bot.get_updates(
                offset=offset,
                limit=limit,
                allowed_updates=["channel_post"]
            )
            
            if not updates:
                break
            
            for update in updates:
                if update.channel_post:
                    all_posts.append(update.channel_post)
                    offset = update.update_id + 1
            
            await asyncio.sleep(1)  # Пауза для API Telegram
            
        except Exception as e:
            logger.error(f"Ошибка при получении постов: {e}")
            break
    
    return all_posts

def upload_photo_to_vk(photo_url: str) -> dict:
    """Загружает фото во ВКонтакте и возвращает данные."""
    try:
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
        
        upload_url = response.json()["response"]["upload_url"]
        
        photo_response = requests.get(photo_url, timeout=30)
        photo_response.raise_for_status()
        files = {"photo": ("photo.jpg", photo_response.content, "image/jpeg")}
        
        upload_response = requests.post(upload_url, files=files, timeout=60)
        upload_response.raise_for_status()
        data = upload_response.json()
        
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
        
        return save_response.json()["response"][0]
    except Exception as e:
        logger.error(f"Ошибка загрузки фото {photo_url}: {e}")
        return None

def upload_video_to_vk(video_url: str, title: str = "Video") -> dict:
    """Загружает видео во ВКонтакте и возвращает данные."""
    try:
        # Получаем URL для загрузки видео
        params = {
            "access_token": VK_GROUP_TOKEN,
            "v": "5.131",
            "name": title,
            "group_id": VK_GROUP_ID
        }
        response = requests.get(
            "https://api.vk.com/method/video.save",
            params=params,
            timeout=30
        )
        response.raise_for_status()
        
        if "error" in response.json():
            error_msg = response.json()["error"]["error_msg"]
            logger.error(f"Ошибка получения URL для видео: {error_msg}")
            return None
        
        upload_data = response.json()["response"]
        upload_url = upload_data["upload_url"]
        
        # Скачиваем видео потоково
        video_response = requests.get(video_url, stream=True, timeout=300)
        video_response.raise_for_status()
        
        files = {"video_file": ("video.mp4", video_response.iter_content(1024*1024), "video/mp4")}
        
        # Загружаем видео
        upload_response = requests.post(upload_url, files=files, timeout=600)
        upload_response.raise_for_status()
        result = upload_response.json()
        
        if "error" in result:
            error_msg = result["error"]["error_msg"]
            logger.error(f"Ошибка загрузки видео: {error_msg}")
            return None
        
        return upload_data
    except Exception as e:
        logger.error(f"Критическая ошибка загрузки видео {video_url}: {e}")
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
            except Exception as e:
                logger.error(f"Ошибка обработки фото {url}: {e}")
    
    # Обрабатываем видео
    if video_url is not None:
        try:
            video_data = upload_video_to_vk(video_url, text[:50] if text else "Video")
            if video_data:
                video_attachment = f"video{video_data['owner_id']}_{video_data['video_id']}"
                attachments.append(video_attachment)
        except Exception as e:
            logger.error(f"Ошибка обработки видео {video_url}: {e}")

    params = {
        "access_token": VK_GROUP_TOKEN,
        "v": "5.131",
        "owner_id": -VK_GROUP_ID,  # Отрицательное для групп
        "message": text,
    }

    if attachments:
        params["attachments"] = ",".join(attachments)

    try:
        response = requests.post(
            "https://api.vk.com/method/wall.post",
            data=params,
            timeout=(30, 60)  # Таймауты: соединение 30 с, чтение 60 с
        )
        response.raise_for_status()
        result = response.json()

        if "error" in result:
            error_msg = result["error"]["error_msg"]
            logger.error(f"Ошибка публикации поста: {error_msg}")
            return False
        

        logger.info("Пост успешно опубликован во ВКонтакте!")
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"Сетевая ошибка при публикации поста: {e}")
        return False
    except Exception as e:
        logger.error(f"Неожиданная ошибка при публикации поста: {e}")
        return False

async def main():
    logger.info("Начинаем перенос старых постов из Telegram во ВКонтакте...")
    posts = await fetch_all_telegram_posts()
    logger.info(f"Найдено {len(posts)} постов для переноса")

    success_count = 0
    error_count = 0

    for i, post in enumerate(posts, 1):
        logger.info(f"Обрабатываем пост {i}/{len(posts)}")
        
        text = post.text or post.caption or ""
        media_urls = []
        video_url = None
        telegram_date = post.date  # Получаем дату публикации из Telegram

        # Извлекаем фото
        if post.photo:
            try:
                photo = post.photo[-1]
                file = await photo.get_file()
                media_urls.append(file.file_path)
            except Exception as e:
                logger.warning(f"Не удалось получить фото для поста {i}: {e}")

        # Извлекаем видео
        if post.video:
            try:
                video = post.video
                file = await video.get_file()
                video_url = file.file_path
            except Exception as e:
                logger.warning(f"Не удалось получить видео для поста {i}: {e}")

        success = publish_to_vk(text, media_urls, video_url, telegram_date)
        if success:
            success_count += 1
        else:
            error_count += 1

        await asyncio.sleep(3)  # Увеличенная пауза для видео

    logger.info(f"Перенос завершён! Успешно: {success_count}, с ошибками: {error_count}")

if __name__ == "__main__":
    asyncio.run(main())
