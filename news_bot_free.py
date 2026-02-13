import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import feedparser
import requests
from bs4 import BeautifulSoup
from telegram import Bot
from telegram.constants import ParseMode
import hashlib
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from deep_translator import GoogleTranslator
import re

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class NewsConfig:
    """Конфигурация для новостных каналов"""
    
    CITIES = {
        'alicante': {
            'name': 'Аликанте',
            'channel_id': '@ALCTODAY',  # Замени на свой канал
            'sources': [
                'https://www.informacion.es/rss/alicante.xml',
                'https://alicanteplaza.es/feed',
                'https://www.alicantehoy.es/feed',
            ],
            'keywords': ['alicante', 'alacant', 'costa blanca']
        },
        'valencia': {
            'name': 'Валенсия',
            'channel_id': '@your_valencia_channel',  # Замени на свой канал
            'sources': [
                'https://www.lasprovincias.es/rss/valencia.xml',
                'https://valenciaplaza.com/feed',
            ],
            'keywords': ['valencia', 'valència', 'comunitat valenciana']
        },
        'barcelona': {
            'name': 'Барселона',
            'channel_id': '@your_barcelona_channel',  # Замени на свой канал
            'sources': [
                'https://www.lavanguardia.com/rss/barcelona.xml',
                'https://beteve.cat/feed/',
            ],
            'keywords': ['barcelona', 'cataluña', 'catalunya']
        }
    }


class NewsParser:
    """Парсер новостей из различных источников"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def parse_rss(self, url: str) -> List[Dict]:
        """Парсинг RSS ленты"""
        try:
            feed = feedparser.parse(url)
            news_items = []
            
            for entry in feed.entries[:10]:  # Берём последние 10 новостей
                news_item = {
                    'title': entry.get('title', ''),
                    'link': entry.get('link', ''),
                    'description': entry.get('summary', entry.get('description', '')),
                    'published': entry.get('published', ''),
                    'image_url': self._extract_image(entry, url),
                    'source': feed.feed.get('title', 'Неизвестный источник')
                }
                news_items.append(news_item)
            
            return news_items
        except Exception as e:
            logger.error(f"Ошибка парсинга RSS {url}: {e}")
            return []
    
    def _extract_image(self, entry, feed_url: str) -> Optional[str]:
        """УЛУЧШЕННОЕ извлечение изображения из записи RSS"""
        
        # Метод 1: media:content (стандарт RSS)
        if hasattr(entry, 'media_content'):
            for media in entry.media_content:
                if 'image' in media.get('type', '') or media.get('url', '').lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    url = media.get('url')
                    if url:
                        logger.info(f"Найдено изображение (media_content): {url}")
                        return url
        
        # Метод 2: media:thumbnail
        if hasattr(entry, 'media_thumbnail'):
            url = entry.media_thumbnail[0].get('url')
            if url:
                logger.info(f"Найдено изображение (media_thumbnail): {url}")
                return url
        
        # Метод 3: enclosure (для подкастов и медиа)
        if hasattr(entry, 'enclosures'):
            for enclosure in entry.enclosures:
                if 'image' in enclosure.get('type', '') or enclosure.get('href', '').lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    url = enclosure.get('href')
                    if url:
                        logger.info(f"Найдено изображение (enclosure): {url}")
                        return url
        
        # Метод 4: Парсинг HTML в описании
        if hasattr(entry, 'summary') or hasattr(entry, 'description'):
            html_content = entry.get('summary', entry.get('description', ''))
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Ищем тег img
            img = soup.find('img')
            if img:
                # Проверяем разные атрибуты
                for attr in ['src', 'data-src', 'data-lazy-src']:
                    url = img.get(attr)
                    if url:
                        # Делаем абсолютный URL если относительный
                        if url.startswith('//'):
                            url = 'https:' + url
                        elif url.startswith('/'):
                            from urllib.parse import urlparse
                            parsed = urlparse(feed_url)
                            url = f"{parsed.scheme}://{parsed.netloc}{url}"
                        
                        logger.info(f"Найдено изображение (HTML img): {url}")
                        return url
        
        # Метод 5: Парсинг content:encoded (WordPress RSS)
        if hasattr(entry, 'content'):
            for content_item in entry.content:
                soup = BeautifulSoup(content_item.get('value', ''), 'html.parser')
                img = soup.find('img')
                if img and img.get('src'):
                    url = img.get('src')
                    if url.startswith('//'):
                        url = 'https:' + url
                    logger.info(f"Найдено изображение (content:encoded): {url}")
                    return url
        
        # Метод 6: Попытка спарсить страницу новости (агрессивный метод)
        # ВНИМАНИЕ: Может замедлить работу, включай при необходимости
         return self._fetch_image_from_article(entry.get('link'))
        logger.debug(f"Изображение не найдено для: {entry.get('title', 'Без названия')}")
        return None
    
    def _fetch_image_from_article(self, article_url: str) -> Optional[str]:
        """Парсинг изображения со страницы статьи (опционально)"""
        try:
            response = self.session.get(article_url, timeout=5)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Ищем Open Graph image
            og_image = soup.find('meta', property='og:image')
            if og_image and og_image.get('content'):
                logger.info(f"Найдено OG изображение: {og_image['content']}")
                return og_image['content']
            
            # Ищем Twitter Card image
            twitter_image = soup.find('meta', attrs={'name': 'twitter:image'})
            if twitter_image and twitter_image.get('content'):
                logger.info(f"Найдено Twitter изображение: {twitter_image['content']}")
                return twitter_image['content']
            
            # Ищем первое большое изображение в статье
            for img in soup.find_all('img'):
                src = img.get('src', '')
                if src and not any(x in src.lower() for x in ['logo', 'icon', 'avatar', 'sprite']):
                    # Проверяем размер (если указан)
                    width = img.get('width', '0')
                    if width.isdigit() and int(width) >= 300:
                        logger.info(f"Найдено изображение со страницы: {src}")
                        return src
            
        except Exception as e:
            logger.debug(f"Не удалось спарсить страницу {article_url}: {e}")
        
        return None
    
    def fetch_all_news(self, sources: List[str]) -> List[Dict]:
        """Получение новостей из всех источников"""
        all_news = []
        for source in sources:
            news = self.parse_rss(source)
            all_news.extend(news)
        return all_news


class NewsFilter:
    """Фильтрация и дедупликация новостей"""
    
    # Список нежелательных слов (можно расширить)
    SPAM_KEYWORDS = [
        'clasificados', 'anuncio', 'publicidad', 'sorteo',
        'oferta laboral', 'se busca', 'se alquila', 'se vende'
    ]
    
    def __init__(self, storage_file='published_news.json'):
        self.storage_file = storage_file
        self.published_hashes = self._load_published()
    
    def _load_published(self) -> set:
        """Загрузка уже опубликованных новостей"""
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return set(data.get('hashes', []))
            except Exception as e:
                logger.error(f"Ошибка загрузки published_news: {e}")
        return set()
    
    def _save_published(self):
        """Сохранение опубликованных новостей"""
        try:
            with open(self.storage_file, 'w', encoding='utf-8') as f:
                json.dump({'hashes': list(self.published_hashes)}, f)
        except Exception as e:
            logger.error(f"Ошибка сохранения published_news: {e}")
    
    def get_news_hash(self, news: Dict) -> str:
        """Получение хеша новости для дедупликации"""
        content = f"{news['title']}{news['link']}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def is_duplicate(self, news: Dict) -> bool:
        """Проверка на дубликат"""
        news_hash = self.get_news_hash(news)
        return news_hash in self.published_hashes
    
    def is_spam(self, news: Dict) -> bool:
        """Простая проверка на спам"""
        text = f"{news['title']} {news['description']}".lower()
        return any(spam_word in text for spam_word in self.SPAM_KEYWORDS)
    
    def mark_as_published(self, news: Dict):
        """Отметить новость как опубликованную"""
        news_hash = self.get_news_hash(news)
        self.published_hashes.add(news_hash)
        self._save_published()
    
    def filter_news(self, news_list: List[Dict], keywords: List[str]) -> List[Dict]:
        """Фильтрация новостей по ключевым словам и дубликатам"""
        filtered = []
        for news in news_list:
            # Проверяем дубликаты
            if self.is_duplicate(news):
                continue
            
            # Проверяем на спам
            if self.is_spam(news):
                logger.info(f"Отфильтрован спам: {news['title']}")
                continue
            
            # Проверяем релевантность по ключевым словам
            text = f"{news['title']} {news['description']}".lower()
            if any(keyword.lower() in text for keyword in keywords):
                filtered.append(news)
        
        return filtered


class FreeNewsProcessor:
    """БЕСПЛАТНАЯ обработка новостей без платных API"""
    
    def __init__(self):
        self.translator = GoogleTranslator(source='auto', target='ru')
    
    def translate_text(self, text: str) -> str:
        """Перевод текста через Google Translate (бесплатно)"""
        try:
            # Google Translate имеет лимит в 5000 символов за раз
            if len(text) > 5000:
                text = text[:5000]
            
            translated = self.translator.translate(text)
            return translated
        except Exception as e:
            logger.error(f"Ошибка перевода: {e}")
            return text  # Возвращаем оригинал если перевод не удался
    
    def clean_html(self, text: str) -> str:
        """Удаление HTML тегов"""
        soup = BeautifulSoup(text, 'html.parser')
        return soup.get_text(separator=' ', strip=True)
    
    def shorten_text(self, text: str, max_sentences: int = 3) -> str:
        """Сокращение текста до N предложений"""
        # Разбиваем на предложения
        sentences = text.replace('!', '.').replace('?', '.').split('.')
        sentences = [s.strip() for s in sentences if s.strip()]
        
        # Берём первые max_sentences предложений
        short_text = '. '.join(sentences[:max_sentences])
        if short_text and not short_text.endswith('.'):
            short_text += '.'
        
        return short_text
    
    def generate_hashtags(self, city_name: str) -> List[str]:
        """Генерация хештегов для города"""
        hashtags = [f"#{city_name}", "#Испания"]
        
        city_tags = {
            'Аликанте': ['#КостаБланка', '#Аликанте'],
            'Валенсия': ['#Валенсия', '#КомунидадВаленсиана'],
            'Барселона': ['#Барселона', '#Каталония']
        }
        
        if city_name in city_tags:
            hashtags = city_tags[city_name]
        
        return hashtags
    
    async def process_news(self, news: Dict, city_name: str) -> Optional[Dict]:
        """Обработка новости БЕЗ платного AI"""
        try:
            # Очищаем HTML из описания
            clean_description = self.clean_html(news['description'])
            
            # Переводим заголовок
            translated_title = self.translate_text(news['title'])
            
            # Переводим и сокращаем описание
            translated_desc = self.translate_text(clean_description)
            short_desc = self.shorten_text(translated_desc, max_sentences=3)
            
            # Генерируем хештеги
            hashtags = self.generate_hashtags(city_name)
            
            # Проверка: если текст слишком короткий или перевод не удался
            if len(short_desc) < 20:
                logger.info(f"Пропуск новости (слишком короткая): {translated_title}")
                return None
            
            return {
                'title': translated_title,
                'text': short_desc,
                'link': news['link'],
                'image_url': news.get('image_url'),
                'hashtags': hashtags,
                'source': news.get('source', '')
            }
            
        except Exception as e:
            logger.error(f"Ошибка обработки новости: {e}")
            return None


class TelegramPublisher:
    """Публикация новостей в Telegram"""
    
    def __init__(self, bot_token: str):
        self.bot = Bot(token=bot_token)
    
    async def publish_news(self, channel_id: str, news: Dict):
        """Публикация одной новости"""
        try:
            # Формируем текст поста
            text = f"<b>{news['title']}</b>\n\n"
            text += f"{news['text']}\n\n"
            
            # Добавляем хештеги
            if news.get('hashtags'):
                text += ' '.join(news['hashtags']) + '\n\n'
            
            # Добавляем ссылку на источник
            text += f"📰 <a href='{news['link']}'>Читать полностью</a>"
            
            # Публикуем с фото или без
            if news.get('image_url'):
                try:
                    logger.info(f"Попытка загрузить фото: {news['image_url']}")
                    await self.bot.send_photo(
                        chat_id=channel_id,
                        photo=news['image_url'],
                        caption=text,
                        parse_mode=ParseMode.HTML
                    )
                    logger.info(f"✅ Опубликовано С ФОТО в {channel_id}")
                except Exception as e:
                    logger.warning(f"❌ Не удалось загрузить фото: {e}")
                    logger.info(f"Публикуем БЕЗ фото...")
                    await self.bot.send_message(
                        chat_id=channel_id,
                        text=text,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=False
                    )
            else:
                logger.info(f"Фото не найдено, публикуем текстом")
                await self.bot.send_message(
                    chat_id=channel_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False
                )
            
            logger.info(f"Успешно опубликовано в {channel_id}: {news['title']}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка публикации в Telegram: {e}")
            return False


class NewsBot:
    """Главный класс бота"""
    
    def __init__(self, telegram_token: str):
        self.parser = NewsParser()
        self.filter = NewsFilter()
        self.processor = FreeNewsProcessor()  # БЕЗ платного API!
        self.publisher = TelegramPublisher(telegram_token)
        self.scheduler = AsyncIOScheduler()
    
    async def process_city_news(self, city_key: str):
        """Обработка новостей для одного города"""
        city_config = NewsConfig.CITIES[city_key]
        logger.info(f"Обработка новостей для {city_config['name']}")
        
        # 1. Получаем новости
        raw_news = self.parser.fetch_all_news(city_config['sources'])
        logger.info(f"Получено {len(raw_news)} новостей для {city_config['name']}")
        
        # Подсчитываем сколько новостей с фото
        with_images = sum(1 for n in raw_news if n.get('image_url'))
        logger.info(f"Из них {with_images} новостей С ФОТО")
        
        # 2. Фильтруем
        filtered_news = self.filter.filter_news(raw_news, city_config['keywords'])
        logger.info(f"После фильтрации осталось {len(filtered_news)} новостей")
        
        # 3. Обрабатываем и публикуем
        published_count = 0
        for news in filtered_news[:5]:  # Берём максимум 5 новостей за раз
            # Обрабатываем (переводим и сокращаем)
            processed_news = await self.processor.process_news(news, city_config['name'])
            
            if processed_news:
                # Публикуем
                success = await self.publisher.publish_news(
                    city_config['channel_id'],
                    processed_news
                )
                
                if success:
                    self.filter.mark_as_published(news)
                    published_count += 1
                    await asyncio.sleep(5)  # Пауза между публикациями
        
        logger.info(f"Опубликовано {published_count} новостей для {city_config['name']}")
    
    async def run_once(self):
        """Однократный запуск проверки всех городов"""
        for city_key in NewsConfig.CITIES.keys():
            await self.process_city_news(city_key)
            await asyncio.sleep(10)  # Пауза между городами
    
    def start_scheduler(self):
        """Запуск планировщика"""
        # Проверка новостей каждые 2 часа
        self.scheduler.add_job(
            self.run_once,
            'interval',
            hours=2,
            id='news_check'
        )
        
        # Также можно настроить конкретное время
        # Например, в 9:00, 13:00, 17:00, 21:00
        # self.scheduler.add_job(self.run_once, 'cron', hour='9,13,17,21')
        
        self.scheduler.start()
        logger.info("Планировщик запущен")
    
    async def run(self):
        """Главный метод запуска бота"""
        logger.info("Бот запущен (БЕСПЛАТНАЯ версия без AI API)")
        logger.info("УЛУЧШЕННЫЙ ПАРСИНГ ИЗОБРАЖЕНИЙ ВКЛЮЧЁН")
        
        # Первый запуск сразу
        await self.run_once()
        
        # Запускаем планировщик
        self.start_scheduler()
        
        # Держим бота активным
        try:
            while True:
                await asyncio.sleep(3600)
        except KeyboardInterrupt:
            logger.info("Бот остановлен")


async def main():
    """Точка входа"""
    import os
    # Читаем токен из переменной окружения Railway
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', 'YOUR_TELEGRAM_BOT_TOKEN')
    
    if TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        print("⚠️  ВНИМАНИЕ! Не забудь заменить TELEGRAM_BOT_TOKEN!")
        print("📖 Получи токен у @BotFather в Telegram")
        return
    
    bot = NewsBot(TELEGRAM_BOT_TOKEN)
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
