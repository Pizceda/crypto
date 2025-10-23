import logging
import sqlite3
import asyncio
import aiohttp
import os
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Получаем токен из переменных окружения
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не найден в переменных окружения!")
    exit(1)

CHANNEL_USERNAME = "@wexxi_code"
MAIN_PHOTO_URL = "https://postimg.cc/5jp2NNDX"

# Данные
CRYPTO_CURRENCIES = {
    "TON": "toncoin", "BTC": "bitcoin", "ETH": "ethereum",
    "BNB": "binancecoin", "SOL": "solana", "ADA": "cardano", "DOGE": "dogecoin"
}

TARGET_CURRENCIES = {
    "RUB": "rub", "USD": "usd", "EUR": "eur", 
    "KZT": "kzt", "UAH": "uah", "BYN": "byn"
}

BINANCE_SYMBOLS = {
    "TON": "TONUSDT", "BTC": "BTCUSDT", "ETH": "ETHUSDT",
    "BNB": "BNBUSDT", "SOL": "SOLUSDT", "ADA": "ADAUSDT", "DOGE": "DOGEUSDT"
}

# Кэширование
price_cache = {}
CACHE_DURATION = timedelta(seconds=30)

class Database:
    def __init__(self):
        self.init_db()
    
    def init_db(self):
        with sqlite3.connect('crypto_bot.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS subscriptions (
                    user_id INTEGER,
                    crypto TEXT,
                    currency TEXT,
                    target_price REAL,
                    is_active INTEGER DEFAULT 1,
                    PRIMARY KEY (user_id, crypto, currency)
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    language TEXT DEFAULT 'ru'
                )
            ''')
            conn.commit()
        logger.info("✅ База данных инициализирована")
    
    def get_user_language(self, user_id):
        try:
            with sqlite3.connect('crypto_bot.db', check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT language FROM user_settings WHERE user_id = ?', (user_id,))
                result = cursor.fetchone()
                return result[0] if result else 'ru'
        except Exception as e:
            logger.error(f"❌ Ошибка получения языка: {e}")
            return 'ru'
    
    def set_user_language(self, user_id, language):
        try:
            with sqlite3.connect('crypto_bot.db', check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute('INSERT OR REPLACE INTO user_settings (user_id, language) VALUES (?, ?)', 
                             (user_id, language))
                conn.commit()
            logger.info(f"✅ Язык сохранен: {user_id} -> {language}")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения языка: {e}")
            return False
    
    def save_subscription(self, user_id, crypto, currency, target_price):
        try:
            with sqlite3.connect('crypto_bot.db', check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO subscriptions 
                    (user_id, crypto, currency, target_price, is_active) 
                    VALUES (?, ?, ?, ?, 1)
                ''', (user_id, crypto, currency, target_price))
                conn.commit()
            logger.info(f"✅ Подписка сохранена: {user_id}, {crypto}, {currency}, {target_price}")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения подписки: {e}")
            return False
    
    def get_user_subscriptions(self, user_id):
        try:
            with sqlite3.connect('crypto_bot.db', check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT crypto, currency, target_price 
                    FROM subscriptions 
                    WHERE user_id = ? AND is_active = 1
                ''', (user_id,))
                result = cursor.fetchall()
                logger.info(f"✅ Найдено подписок для {user_id}: {len(result)}")
                return result
        except Exception as e:
            logger.error(f"❌ Ошибка получения подписок: {e}")
            return []
    
    def stop_all_subscriptions(self, user_id):
        with sqlite3.connect('crypto_bot.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE subscriptions SET is_active = 0 WHERE user_id = ?', (user_id,))
            conn.commit()
    
    def deactivate_subscription(self, user_id, crypto, currency):
        with sqlite3.connect('crypto_bot.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE subscriptions SET is_active = 0 
                WHERE user_id = ? AND crypto = ? AND currency = ?
            ''', (user_id, crypto, currency))
            conn.commit()

class PriceService:
    def __init__(self):
        self.db = Database()
    
    async def get_usd_to_rub_rate(self):
        cache_key = "usd_rub"
        if self._is_cache_valid(cache_key):
            return price_cache[cache_key]['price']
        
        try:
            async with aiohttp.ClientSession() as session:
                sources = [
                    "https://api.exchangerate-api.com/v4/latest/USD",
                    "https://api.coingecko.com/api/v3/simple/price?ids=usd&vs_currencies=rub",
                ]
                
                for url in sources:
                    try:
                        async with session.get(url, timeout=5) as response:
                            if response.status == 200:
                                data = await response.json()
                                rate = self._parse_exchange_rate(data)
                                if rate:
                                    self._set_cache(cache_key, rate)
                                    logger.info(f"💰 Курс USD/RUB: {rate}")
                                    return rate
                    except:
                        continue
                
                rate = 95.0
                self._set_cache(cache_key, rate)
                return rate
                
        except Exception as e:
            logger.error(f"❌ Ошибка получения курса USD/RUB: {e}")
            return 95.0
    
    def _parse_exchange_rate(self, data):
        if 'rates' in data and 'RUB' in data['rates']:
            return float(data['rates']['RUB'])
        elif 'usd' in data and 'rub' in data['usd']:
            return float(data['usd']['rub'])
        elif 'rub' in data:
            return float(data['rub'])
        return None
    
    async def get_crypto_price_coingecko(self, currency_id, target_currency):
        cache_key = f"coingecko_{currency_id}_{target_currency}"
        if self._is_cache_valid(cache_key):
            return price_cache[cache_key]['price']
        
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://api.coingecko.com/api/v3/simple/price?ids={currency_id}&vs_currencies={target_currency}"
                
                async with session.get(url, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        if currency_id in data and target_currency in data[currency_id]:
                            price = data[currency_id][target_currency]
                            self._set_cache(cache_key, price)
                            logger.info(f"✅ CoinGecko: {currency_id} = {price} {target_currency}")
                            return price
        except Exception as e:
            logger.error(f"❌ CoinGecko ошибка: {e}")
        
        return None
    
    async def get_crypto_price_binance(self, currency_symbol, target_currency):
        cache_key = f"binance_{currency_symbol}_{target_currency}"
        if self._is_cache_valid(cache_key):
            return price_cache[cache_key]['price']
        
        try:
            symbol = BINANCE_SYMBOLS.get(currency_symbol)
            if not symbol:
                return None
            
            async with aiohttp.ClientSession() as session:
                url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
                
                async with session.get(url, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        usd_price = float(data['price'])
                        
                        if target_currency == "usd":
                            self._set_cache(cache_key, usd_price)
                            return usd_price
                        
                        # Конвертация в другие валюты
                        if target_currency == "rub":
                            usd_to_rub = await self.get_usd_to_rub_rate()
                            rub_price = usd_price * usd_to_rub
                            self._set_cache(cache_key, rub_price)
                            logger.info(f"✅ Binance: {currency_symbol} = {rub_price:.2f} RUB")
                            return rub_price
                        else:
                            rates = {"eur": 0.92, "kzt": 450.0, "uah": 38.0, "byn": 2.5}
                            if target_currency in rates:
                                converted_price = usd_price * rates[target_currency]
                                self._set_cache(cache_key, converted_price)
                                return converted_price
                            return usd_price
        except Exception as e:
            logger.error(f"❌ Binance ошибка: {e}")
        
        return None
    
    async def get_crypto_price(self, crypto, target_currency):
        currency_id = CRYPTO_CURRENCIES[crypto]
        target_currency_lower = target_currency.lower()
        
        # Пробуем CoinGecko
        price = await self.get_crypto_price_coingecko(currency_id, target_currency_lower)
        
        # Если не сработало, пробуем Binance
        if price is None:
            price = await self.get_crypto_price_binance(crypto, target_currency_lower)
        
        return price
    
    def _is_cache_valid(self, cache_key):
        if cache_key in price_cache:
            cache_time = price_cache[cache_key]['timestamp']
            return datetime.now() - cache_time < CACHE_DURATION
        return False
    
    def _set_cache(self, cache_key, price):
        price_cache[cache_key] = {
            'price': price,
            'timestamp': datetime.now()
        }

class BotService:
    def __init__(self):
        self.db = Database()
        self.price_service = PriceService()
        self.texts = {
            'ru': self._get_russian_texts(),
            'en': self._get_english_texts()
        }
    
    def _get_russian_texts(self):
        return {
            'welcome': "🌍 <b>Выберите язык</b>",
            'language_selected': "✅ <b>Язык установлен: Русский</b>",
            'language_changed': "✅ <b>Язык успешно изменен на Русский</b>",
            'check_subscription': """
📢 <b>ПОДПИШИТЕСЬ НА НАШ КАНАЛ</b>

Чтобы использовать бота, вам необходимо подписаться на наш канал.

Канал: {channel}
""",
            'subscribe': "📢 Подписаться",
            'check': "✅ Проверить подписку",
            'not_subscribed': "❌ Вы еще не подписаны на канал. Пожалуйста, подпишитесь и нажмите 'Проверить подписку'.",
            'main_menu': """
🎯 <b>CryptoPrice Monitor PRO</b>

<b>⚡ ВАШ ЛИЧНЫЙ КРИПТО-ТРЕЙДЕР!</b>

📊 <b>Мониторинг в реальном времени</b>
• Курсы 50+ криптовалют
• 6 валют (RUB, USD, EUR, KZT, UAH, BYN)
• Автообновление каждые 30 сек

🎯 <b>УМНЫЕ УВЕДОМЛЕНИЯ</b>
• Настройка ценовых целей
• Мгновенные оповещения
• Спам при достижении цели!

💰 <b>ВЫГОДНЫЕ ПОКУПКИ</b>
• Не пропустите падение цены
• Авто-стоп при достижении цели
• История ваших подписок

🔧 <b>ПРОСТОЙ ИНТЕРФЕЙС</b>
• Русский/Английский языки
• Интуитивное управление
• Поддержка 24/7

📈 <b>НАЧНИТЕ ЗАРАБАТЫВАТЬ УЖЕ СЕЙЧАС!</b>
""",
            'setup_monitoring': "📊 Настроить мониторинг",
            'my_subscriptions': "📈 Мои подписки", 
            'settings': "⚙️ Настройки",
            'no_subscriptions': "📭 <b>Нет активных подписок</b>",
            'all_stopped': "🛑 <b>Все подписки остановлены!</b>",
            'choose_crypto': "💎 <b>Выберите криптовалюту для мониторинга:</b>",
            'loading': "🔄 Загрузка...",
            'back_menu': "🔙 Назад в меню",
            'back_crypto': "🔙 Назад к выбору крипты",
            'stop_all': "🛑 Остановить все подписки",
            'change_lang': "🌍 Сменить язык",
            'settings_text': "⚙️ <b>Настройки</b>\n\nЗдесь вы можете изменить язык бота.",
            'language_changed_settings': "✅ <b>Язык успешно изменен!</b>\n\nТеперь бот будет использовать выбранный язык."
        }
    
    def _get_english_texts(self):
        return {
            'welcome': "🌍 <b>Choose language</b>",
            'language_selected': "✅ <b>Language set: English</b>",
            'language_changed': "✅ <b>Language successfully changed to English</b>",
            'check_subscription': """
📢 <b>SUBSCRIBE TO OUR CHANNEL</b>

To use the bot, you need to subscribe to our channel.

Channel: {channel}
""",
            'subscribe': "📢 Subscribe",
            'check': "✅ Check subscription",
            'not_subscribed': "❌ You are not subscribed to the channel yet. Please subscribe and click 'Check subscription'.",
            'main_menu': """
🎯 <b>CryptoPrice Monitor PRO</b>

<b>⚡ YOUR PERSONAL CRYPTO TRADER!</b>

📊 <b>Real-time Monitoring</b>
• 50+ cryptocurrency rates
• 6 currencies (RUB, USD, EUR, KZT, UAH, BYN)
• Auto-update every 30 sec

🎯 <b>SMART NOTIFICATIONS</b>
• Price target settings
• Instant alerts
• Spam when target reached!

💰 <b>PROFITABLE PURCHASES</b>
• Don't miss price drops
• Auto-stop when target reached
• Your subscription history

🔧 <b>SIMPLE INTERFACE</b>
• Russian/English languages
• Intuitive control
• 24/7 support

📈 <b>START EARNING RIGHT NOW!</b>
""",
            'setup_monitoring': "📊 Setup Monitoring",
            'my_subscriptions': "📈 My Subscriptions",
            'settings': "⚙️ Settings",
            'no_subscriptions': "📭 <b>No active subscriptions</b>",
            'all_stopped': "🛑 <b>All subscriptions stopped!</b>",
            'choose_crypto': "💎 <b>Choose cryptocurrency to monitor:</b>",
            'loading': "🔄 Loading...",
            'back_menu': "🔙 Back to menu",
            'back_crypto': "🔙 Back to crypto",
            'stop_all': "🛑 Stop all subscriptions",
            'change_lang': "🌍 Change language",
            'settings_text': "⚙️ <b>Settings</b>\n\nHere you can change the bot language.",
            'language_changed_settings': "✅ <b>Language successfully changed!</b>\n\nNow the bot will use the selected language."
        }
    
    def get_text(self, lang, key, **kwargs):
        text = self.texts[lang].get(key, key)
        if kwargs:
            text = text.format(**kwargs)
        return text
    
    async def check_subscription(self, user_id, bot):
        try:
            member = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
            return member.status in ['member', 'administrator', 'creator']
        except Exception as e:
            logger.error(f"❌ Ошибка проверки подписки: {e}")
            return False
    
    async def send_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, keyboard=None):
        """Универсальная функция отправки сообщения"""
        user_id = update.effective_user.id
        
        try:
            if update.callback_query:
                # Если это callback query, редактируем сообщение
                await update.callback_query.message.edit_text(
                    text, 
                    reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
                    parse_mode='HTML'
                )
            else:
                # Если это обычное сообщение, отправляем новое
                await context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
                    parse_mode='HTML'
                )
            logger.info("✅ Сообщение отправлено")
            
        except Exception as e:
            logger.error(f"❌ Ошибка отправки сообщения: {e}")
    
    async def send_photo_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, keyboard):
        """Универсальная функция отправки фото с текстом"""
        user_id = update.effective_user.id
        
        try:
            if update.callback_query:
                try:
                    await update.callback_query.message.delete()
                except:
                    pass
            
            await context.bot.send_photo(
                chat_id=user_id,
                photo=MAIN_PHOTO_URL,
                caption=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
            logger.info("✅ Сообщение с фото отправлено")
            
        except Exception as e:
            logger.error(f"❌ Ошибка отправки фото: {e}")
            # Фолбэк - отправляем только текст
            await self.send_message(update, context, text, keyboard)
    
    async def show_language_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE, source="start"):
        """Показывает выбор языка
        source: 'start' - при запуске, 'settings' - из настроек
        """
        user_id = update.effective_user.id
        current_lang = self.db.get_user_language(user_id)
        
        text = "🌍 <b>Choose your language / Выберите язык</b>"
        
        if source == "settings":
            keyboard = [
                [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru_settings")],
                [InlineKeyboardButton("🇺🇸 English", callback_data="lang_en_settings")],
                [InlineKeyboardButton("🔙 Назад" if current_lang == 'ru' else "🔙 Back", callback_data="settings")]
            ]
        else:
            keyboard = [
                [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
                [InlineKeyboardButton("🇺🇸 English", callback_data="lang_en")]
            ]
        
        await self.send_message(update, context, text, keyboard)
    
    async def show_subscription_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает проверку подписки"""
        user_id = update.effective_user.id
        lang = self.db.get_user_language(user_id)
        
        text = self.get_text(lang, 'check_subscription', channel=CHANNEL_USERNAME)
        keyboard = [
            [InlineKeyboardButton(self.get_text(lang, 'subscribe'), url=f"https://t.me/{CHANNEL_USERNAME[1:]}")],
            [InlineKeyboardButton(self.get_text(lang, 'check'), callback_data="check_subscription")]
        ]
        
        await self.send_message(update, context, text, keyboard)
    
    async def show_main_menu_with_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        lang = self.db.get_user_language(user_id)
        
        keyboard = [
            [InlineKeyboardButton(self.get_text(lang, 'setup_monitoring'), callback_data="setup_monitor")],
            [InlineKeyboardButton(self.get_text(lang, 'my_subscriptions'), callback_data="mystats")],
            [InlineKeyboardButton(self.get_text(lang, 'settings'), callback_data="settings")]
        ]
        
        text = self.get_text(lang, 'main_menu')
        await self.send_photo_message(update, context, text, keyboard)
    
    async def show_crypto_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        lang = self.db.get_user_language(user_id)
        
        keyboard = []
        crypto_list = list(CRYPTO_CURRENCIES.keys())
        
        for i in range(0, len(crypto_list), 2):
            row = []
            for crypto in crypto_list[i:i+2]:
                row.append(InlineKeyboardButton(f"💎 {crypto}", callback_data=f"select_crypto_{crypto}"))
            keyboard.append(row)
        
        keyboard.append([InlineKeyboardButton(self.get_text(lang, 'back_menu'), callback_data="main_menu")])
        
        text = self.get_text(lang, 'choose_crypto')
        await self.send_photo_message(update, context, text, keyboard)
    
    async def show_currency_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE, crypto: str):
        user_id = update.effective_user.id
        lang = self.db.get_user_language(user_id)
        
        # Получаем цены для всех валют
        price_tasks = [
            self.price_service.get_crypto_price(crypto, currency)
            for currency in TARGET_CURRENCIES.keys()
        ]
        prices = await asyncio.gather(*price_tasks)
        
        price_info = "\n".join([
            f"💵 {currency}: {price:,.2f}" if price 
            else f"💵 {currency}: {self.get_text(lang, 'loading')}"
            for currency, price in zip(TARGET_CURRENCIES.keys(), prices)
        ])
        
        # Создаем кнопки валют
        keyboard = []
        currency_list = list(TARGET_CURRENCIES.keys())
        
        for i in range(0, len(currency_list), 3):
            row = []
            for currency in currency_list[i:i+3]:
                row.append(InlineKeyboardButton(f"💵 {currency}", callback_data=f"select_currency_{crypto}_{currency}"))
            keyboard.append(row)
        
        keyboard.append([InlineKeyboardButton(self.get_text(lang, 'back_crypto'), callback_data="setup_monitor")])
        
        text = f"""
💎 <b>Криптовалюта:</b> {crypto}

📊 <b>Текущие цены:</b>
{price_info}

💵 <b>Выберите валюту для покупки:</b>
""" if lang == 'ru' else f"""
💎 <b>Cryptocurrency:</b> {crypto}

📊 <b>Current prices:</b>
{price_info}

💵 <b>Choose purchase currency:</b>
"""
        
        await self.send_photo_message(update, context, text, keyboard)
    
    async def ask_for_target_price(self, update: Update, context: ContextTypes.DEFAULT_TYPE, crypto: str, currency: str):
        user_id = update.effective_user.id
        lang = self.db.get_user_language(user_id)
        
        context.user_data.update({
            'selected_crypto': crypto,
            'selected_currency': currency,
            'waiting_for_price': True
        })
        
        current_price = await self.price_service.get_crypto_price(crypto, currency)
        price_display = f"{current_price:,.2f} {currency}" if current_price else self.get_text(lang, 'loading')
        
        text = f"""
🎯 <b>Настройка мониторинга</b>

💎 <b>Криптовалюта:</b> {crypto}
💵 <b>Валюта:</b> {currency}

💰 <b>Текущая цена:</b> {price_display}

📝 <b>Введите целевую цену в {currency}:</b>
<i>Например: 180.50</i>
""" if lang == 'ru' else f"""
🎯 <b>Setup Monitoring</b>

💎 <b>Cryptocurrency:</b> {crypto}
💵 <b>Currency:</b> {currency}

💰 <b>Current price:</b> {price_display}

📝 <b>Enter target price in {currency}:</b>
<i>Example: 180.50</i>
"""
        
        keyboard = [[InlineKeyboardButton(self.get_text(lang, 'back_crypto'), callback_data=f"select_crypto_{crypto}")]]
        await self.send_photo_message(update, context, text, keyboard)
    
    async def handle_price_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not context.user_data.get('waiting_for_price'):
            return
        
        user_id = update.effective_user.id
        lang = self.db.get_user_language(user_id)
        
        try:
            price = float(update.message.text.replace(',', '.'))
            crypto = context.user_data.get('selected_crypto')
            currency = context.user_data.get('selected_currency')
            
            if not crypto or not currency or price <= 0:
                await update.message.reply_text("❌ Ошибка ввода данных")
                context.user_data.clear()
                return
            
            # Сохраняем подписку
            success = self.db.save_subscription(user_id, crypto, currency, price)
            
            if not success:
                await update.message.reply_text("❌ Ошибка сохранения подписки")
                context.user_data.clear()
                return
            
            # Получаем текущую цену для сравнения
            current_price = await self.price_service.get_crypto_price(crypto, currency)
            
            # Формируем ответ
            if lang == 'ru':
                status = "✅ <b>ЦЕЛЬ УЖЕ ДОСТИГНУТА!</b>" if current_price and current_price <= price else "⏳ <b>Ожидаем падения цены</b>"
                text = f"""
✅ <b>Мониторинг настроен!</b>

💎 <b>Криптовалюта:</b> {crypto}
💵 <b>Валюта:</b> {currency}

{"💰 <b>Текущая цена:</b> " + f"{current_price:,.2f} {currency}" if current_price else ""}
🎯 <b>Целевая цена:</b> {price:,.2f} {currency}

{status}
"""
            else:
                status = "✅ <b>GOAL ALREADY REACHED!</b>" if current_price and current_price <= price else "⏳ <b>Waiting for price drop</b>"
                text = f"""
✅ <b>Monitoring set up!</b>

💎 <b>Cryptocurrency:</b> {crypto}
💵 <b>Currency:</b> {currency}

{"💰 <b>Current price:</b> " + f"{current_price:,.2f} {currency}" if current_price else ""}
🎯 <b>Target price:</b> {price:,.2f} {currency}

{status}
"""
            
            keyboard = [[InlineKeyboardButton(self.get_text(lang, 'back_menu'), callback_data="main_menu")]]
            await self.send_photo_message(update, context, text, keyboard)
            context.user_data.clear()
            
        except ValueError:
            error_msg = "❌ Введите корректное число" if lang == 'ru' else "❌ Enter a valid number"
            await update.message.reply_text(error_msg)

# Глобальные сервисы
bot_service = BotService()

# Обработчики
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"🔄 Пользователь {user_id} запустил бота")
    
    # Всегда показываем выбор языка при старте
    logger.info(f"🌍 Показываем выбор языка для {user_id}")
    await bot_service.show_language_selection(update, context, source="start")

async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    logger.info(f"🔄 Обработка кнопки: {data} от пользователя {user_id}")
    
    # Обработка выбора языка
    if data.startswith("lang_"):
        # Определяем источник и язык
        if data.endswith("_settings"):
            # Выбор языка из настроек
            language = data.replace("_settings", "").split("_")[1]
            source = "settings"
        else:
            # Выбор языка при старте
            language = data.split("_")[1]
            source = "start"
        
        logger.info(f"🌍 Пользователь {user_id} выбрал язык: {language}, источник: {source}")
        
        # Сохраняем язык в базу данных
        success = bot_service.db.set_user_language(user_id, language)
        
        if success:
            lang = bot_service.db.get_user_language(user_id)
            
            if source == "settings":
                # Если из настроек - показываем сообщение об успехе и возвращаем в настройки
                text = bot_service.get_text(lang, 'language_changed_settings')
                await query.message.edit_text(text, parse_mode='HTML')
                await asyncio.sleep(1)
                await show_settings(update, context)
            else:
                # Если при старте - переходим к проверке подписки
                text = bot_service.get_text(lang, 'language_selected')
                await query.message.edit_text(text, parse_mode='HTML')
                await asyncio.sleep(1)
                await bot_service.show_subscription_check(update, context)
        else:
            # Ошибка сохранения языка
            error_text = "❌ Ошибка сохранения языка. Попробуйте еще раз."
            await query.message.edit_text(error_text, parse_mode='HTML')
        
    elif data == "check_subscription":
        # Проверка подписки
        logger.info(f"🔍 Пользователь {user_id} проверяет подписку")
        if await bot_service.check_subscription(user_id, context.bot):
            # Подписан - показываем главное меню
            logger.info(f"✅ Пользователь {user_id} подписан, показываем главное меню")
            await bot_service.show_main_menu_with_photo(update, context)
        else:
            # Не подписан - показываем сообщение "вы не подписались!"
            lang = bot_service.db.get_user_language(user_id)
            text = bot_service.get_text(lang, 'not_subscribed')
            await query.message.edit_text(text, parse_mode='HTML')
            
            # И снова показываем тот же текст что надо подписаться
            await asyncio.sleep(1)
            await bot_service.show_subscription_check(update, context)
            
    elif data == "main_menu":
        await bot_service.show_main_menu_with_photo(update, context)
    elif data == "setup_monitor":
        await bot_service.show_crypto_selection(update, context)
    elif data == "mystats":
        await show_subscriptions(update, context)
    elif data == "settings":
        await show_settings(update, context)
    elif data == "stop_all":
        await stop_all_subscriptions(update, context)
    elif data == "change_lang":
        # Показываем выбор языка из настроек
        await bot_service.show_language_selection(update, context, source="settings")
    elif data.startswith("select_crypto_"):
        crypto = data.replace("select_crypto_", "")
        await bot_service.show_currency_selection(update, context, crypto)
    elif data.startswith("select_currency_"):
        parts = data.split("_")
        crypto = parts[2]
        currency = parts[3]
        await bot_service.ask_for_target_price(update, context, crypto, currency)

async def show_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    lang = bot_service.db.get_user_language(user_id)
    
    subscriptions = bot_service.db.get_user_subscriptions(user_id)
    keyboard = [[InlineKeyboardButton(bot_service.get_text(lang, 'back_menu'), callback_data="main_menu")]]
    
    if not subscriptions:
        text = bot_service.get_text(lang, 'no_subscriptions')
        await bot_service.send_photo_message(update, context, text, keyboard)
        return
    
    keyboard.insert(0, [InlineKeyboardButton(bot_service.get_text(lang, 'stop_all'), callback_data="stop_all")])
    
    text = "📊 <b>Ваши активные подписки:</b>\n\n" if lang == 'ru' else "📊 <b>Your active subscriptions:</b>\n\n"
    
    for crypto, currency, target_price in subscriptions:
        current_price = await bot_service.price_service.get_crypto_price(crypto, currency)
        
        if current_price:
            difference = current_price - target_price
            percentage = (difference / target_price) * 100
            
            if current_price <= target_price:
                status = "🟢 <b>ЦЕЛЬ ДОСТИГНУТА!</b>" if lang == 'ru' else "🟢 <b>TARGET REACHED!</b>"
            else:
                status = (f"🟡 Осталось: {difference:+,.2f} ({percentage:+.1f}%)" if lang == 'ru' 
                         else f"🟡 Remaining: {difference:+,.2f} ({percentage:+.1f}%)")
            
            text += f"""💎 {crypto} → 💵 {currency}
🎯 Цель: {target_price:,.2f} {currency}
💰 Сейчас: {current_price:,.2f} {currency}
{status}\n\n"""
        else:
            status = "⚪ Обновление данных..." if lang == 'ru' else "⚪ Updating data..."
            text += f"""💎 {crypto} → 💵 {currency}
🎯 Цель: {target_price:,.2f} {currency}
{status}\n\n"""
    
    await bot_service.send_photo_message(update, context, text, keyboard)

async def stop_all_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    lang = bot_service.db.get_user_language(user_id)
    
    bot_service.db.stop_all_subscriptions(user_id)
    
    keyboard = [[InlineKeyboardButton(bot_service.get_text(lang, 'back_menu'), callback_data="main_menu")]]
    text = bot_service.get_text(lang, 'all_stopped')
    await bot_service.send_photo_message(update, context, text, keyboard)

async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    lang = bot_service.db.get_user_language(user_id)
    
    keyboard = [
        [InlineKeyboardButton(bot_service.get_text(lang, 'change_lang'), callback_data="change_lang")],
        [InlineKeyboardButton(bot_service.get_text(lang, 'back_menu'), callback_data="main_menu")]
    ]
    
    text = bot_service.get_text(lang, 'settings_text')
    await bot_service.send_photo_message(update, context, text, keyboard)

async def send_spam(context, user_id, crypto, currency, current_price, target_price):
    try:
        # Получаем информацию о пользователе
        user = await context.bot.get_chat(user_id)
        username = f"@{user.username}" if user.username else f"user_{user_id}"
        
        lang = bot_service.db.get_user_language(user_id)
        
        if lang == 'ru':
            main_text = f"""
🚨🚨🚨 <b>ЦЕНА УПАЛА!</b> 🚨🚨🚨

💎 <b>{crypto}</b>
💰 <b>Текущая цена:</b> {current_price:,.2f} {currency}
🎯 <b>Ваша цель:</b> {target_price:,.2f} {currency}

📉 <b>Цена достигла целевого уровня! ПОРА ПОКУПАТЬ!</b> 💰

👤 <b>Пользователь:</b> {username}
"""
            spam_messages = [
                f"🎯 ЦЕЛЬ ДОСТИГНУТА! {username}",
                f"💰 ПОРА ПОКУПАТЬ! {username}",
                f"📉 ЦЕНА УПАЛА ДО НУЖНОГО УРОВНЯ! {username}",
                f"🚨 НЕ ПРОСПИ СВОЙ ШАНС! {username}",
                f"💎 ИДЕАЛЬНЫЙ МОМЕНТ ДЛЯ ПОКУПКИ! {username}",
                f"🔥 ЦЕНА НИЖЕ ТВОЕГО ТАРГЕТА! {username}",
                f"🎊 ПОЗДРАВЛЯЮ С ВЫГОДНОЙ ПОКУПКОЙ! {username}",
                f"⚡ УСПЕЙ КУПИТЬ ПО ВЫГОДНОЙ ЦЕНЕ! {username}",
                f"💸 НЕ УПУСТИ СВОЙ ШАНС! {username}",
                f"🚀 ВРЕМЯ ДЕЙСТВОВАТЬ! {username}",
                f"📊 ЦЕНА ДОСТИГЛА ЦЕЛИ! {username}",
                f"🎯 ТВОЙ МОМЕНТ НАСТАЛ! {username}",
                f"💰 ВЫГОДНАЯ ПОКУПКА ЖДЕТ! {username}",
                f"🔥 НЕ ПРОПУСТИ ЗОЛОТУЮ ВОЗМОЖНОСТЬ! {username}",
                f"🎉 ПОРА ВХОДИТЬ В СДЕЛКУ! {username}"
            ]
        else:
            main_text = f"""
🚨🚨🚨 <b>PRICE DROPPED!</b> 🚨🚨🚨

💎 <b>{crypto}</b>
💰 <b>Current price:</b> {current_price:,.2f} {currency}
🎯 <b>Your target:</b> {target_price:,.2f} {currency}

📉 <b>Price reached target level! TIME TO BUY!</b> 💰

👤 <b>User:</b> {username}
"""
            spam_messages = [
                f"🎯 TARGET REACHED! {username}",
                f"💰 TIME TO BUY! {username}",
                f"📉 PRICE DROPPED TO TARGET LEVEL! {username}",
                f"🚨 DON'T MISS YOUR CHANCE! {username}",
                f"💎 PERFECT TIME TO BUY! {username}",
                f"🔥 PRICE BELOW YOUR TARGET! {username}",
                f"🎊 CONGRATS ON PROFITABLE PURCHASE! {username}",
                f"⚡ BUY AT A GOOD PRICE NOW! {username}",
                f"💸 DON'T MISS YOUR OPPORTUNITY! {username}",
                f"🚀 TIME TO ACT! {username}",
                f"📊 PRICE REACHED TARGET! {username}",
                f"🎯 YOUR MOMENT HAS COME! {username}",
                f"💰 PROFITABLE PURCHASE AWAITS! {username}",
                f"🔥 DON'T MISS THE GOLDEN OPPORTUNITY! {username}",
                f"🎉 TIME TO ENTER THE DEAL! {username}"
            ]
        
        # Отправляем основное сообщение
        await context.bot.send_message(user_id, main_text, parse_mode='HTML')
        
        # Отправляем 15 спам-сообщений
        for i, msg in enumerate(spam_messages[:15], 1):
            try:
                await context.bot.send_message(user_id, f"{msg} [{i}/15]")
                await asyncio.sleep(0.3)  # Небольшая задержка между сообщениями
            except Exception as e:
                logger.error(f"❌ Ошибка отправки спам-сообщения {i}: {e}")
                continue
        
        # Деактивируем подписку после отправки спама
        bot_service.db.deactivate_subscription(user_id, crypto, currency)
        logger.info(f"✅ Спам отправлен пользователю {username} ({user_id})")
        
    except Exception as e:
        logger.error(f"❌ Ошибка в send_spam: {e}")

async def check_prices(context: ContextTypes.DEFAULT_TYPE):
    try:
        db = Database()
        subscriptions = []
        
        with sqlite3.connect('crypto_bot.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT user_id, crypto, currency, target_price FROM subscriptions WHERE is_active = 1')
            subscriptions = cursor.fetchall()
        
        logger.info(f"🔍 Проверка {len(subscriptions)} подписок")
        
        for user_id, crypto, currency, target_price in subscriptions:
            current_price = await bot_service.price_service.get_crypto_price(crypto, currency)
            
            if current_price and current_price <= target_price:
                logger.info(f"🎯 ЦЕЛЬ ДОСТИГНУТА! {crypto}: {current_price} <= {target_price}")
                await send_spam(context, user_id, crypto, currency, current_price, target_price)
                
    except Exception as e:
        logger.error(f"❌ Ошибка в check_prices: {e}")

def main():
    # Токен уже проверен в начале кода
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_button_click))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_service.handle_price_input))
    
    app.job_queue.run_repeating(check_prices, interval=30, first=10)
    
    logger.info("🤖 Бот запущен на Railway!")
    app.run_polling()

if __name__ == '__main__':
    main()
