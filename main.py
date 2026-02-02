import logging
import os
import re
import time
import yt_dlp

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)
from telegram.constants import ParseMode

# ==================== CONFIGURACIÓN ====================
# ✅ Usa variable de entorno o fallback (RECOMENDADO: solo variable de entorno)
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "pegatu_token_aqui")

# ✅ Verificación estricta de versión para evitar errores por incompatibilidad
import telegram
if telegram.__version__ != "20.6":
    raise RuntimeError(
        f"❌ ERROR: Se requiere python-telegram-bot 20.6 (tienes {telegram.__version__}). "
        "Ejecuta: pip uninstall -y telegram python-telegram-bot && pip install python-telegram-bot==20.6"
    )

# ==================== CONFIGURACIÓN ADICIONAL ====================
TEMP_DIR = "temp_downloads"
MAX_FILE_SIZE = 49 * 1024 * 1024  # 49MB (margen de seguridad)
MAX_DOWNLOADS_PER_HOUR = 10

# Rate limiting
USER_DOWNLOADS = {}

# Configuración de logging
logging.basicConfig(
    format='%(asctime)s | %(levelname)-8s | %(name)-15s | %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("YouTubeBot")

# ==================== UTILIDADES ====================
def sanitize_filename(filename: str) -> str:
    """Limpia el nombre de archivo de caracteres problemáticos"""
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    filename = re.sub(r'\s+', '_', filename.strip())
    return filename[:50] or "video_sin_titulo"

def format_size(bytes_size: int) -> str:
    """Convierte bytes a formato legible"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024
    return f"{bytes_size:.1f} TB"

def format_duration(seconds: int) -> str:
    """Convierte segundos a formato legible"""
    if seconds < 0:
        return "0:00"
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours}h {mins}m {secs}s"
    return f"{mins}m {secs}s"

def is_valid_youtube_url(url: str) -> bool:
    """Valida URL de YouTube (permisivo para aceptar parámetros)"""
    return 'youtube.com' in url or 'youtu.be' in url or 'youtube-nocookie.com' in url

def extract_video_info(url: str):
    """Extrae información del video (título, duración, vistas, miniatura)"""
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
            'socket_timeout': 10,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                'title': info.get('title', 'Sin título'),
                'duration': info.get('duration', 0),
                'views': info.get('view_count', 0),
                'uploader': info.get('uploader', 'Desconocido'),
                'thumbnail': info.get('thumbnail', ''),
            }
    except Exception as e:
        logger.error(f"Error extrayendo info del video: {e}")
        return None

def check_rate_limit(user_id: int) -> tuple[bool, int]:
    """Verifica límite de descargas por hora"""
    now = time.time()
    if user_id not in USER_DOWNLOADS:
        USER_DOWNLOADS[user_id] = []

    USER_DOWNLOADS[user_id] = [t for t in USER_DOWNLOADS[user_id] if now - t < 3600]

    if len(USER_DOWNLOADS[user_id]) >= MAX_DOWNLOADS_PER_HOUR:
        wait_time = int(3600 - (now - USER_DOWNLOADS[user_id][0]))
        return False, wait_time

    USER_DOWNLOADS[user_id].append(now)
    remaining = MAX_DOWNLOADS_PER_HOUR - len(USER_DOWNLOADS[user_id])
    return True, remaining

# ==================== FUNCIONES DE DESCARGA (SÍNCRONAS) ====================
def download_video(url: str, output_dir: str):
    """Descarga video en mejor calidad (720p) - FUNCIÓN SÍNCRONA"""
    os.makedirs(output_dir, exist_ok=True)

    ydl_opts = {
        'format': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best',
        'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'restrictfilenames': True,
        'socket_timeout': 15,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)

        title = sanitize_filename(info.get('title', 'Sin título'))
        safe_path = os.path.join(output_dir, f"{title}.mp4")

        if filepath != safe_path:
            if os.path.exists(safe_path):
                os.remove(safe_path)
            try:
                os.rename(filepath, safe_path)
                filepath = safe_path
            except Exception as e:
                logger.warning(f"Error renombrando archivo: {e}. Usando ruta original.")

        duration = info.get('duration', 0)
        return filepath, title, duration

def download_audio(url: str, output_dir: str):
    """Descarga solo el audio en formato MP3 - FUNCIÓN SÍNCRONA"""
    os.makedirs(output_dir, exist_ok=True)

    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio/best',
        'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'restrictfilenames': True,
        'socket_timeout': 15,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)

        filepath = str(filepath).replace('.m4a', '.mp3').replace('.webm', '.mp3')

        title = sanitize_filename(info.get('title', 'Sin título'))
        safe_path = os.path.join(output_dir, f"{title}.mp3")

        if filepath != safe_path:
            if os.path.exists(safe_path):
                os.remove(safe_path)
            try:
                os.rename(filepath, safe_path)
                filepath = safe_path
            except Exception as e:
                logger.warning(f"Error renombrando archivo: {e}. Usando ruta original.")

        duration = info.get('duration', 0)
        return filepath, title, duration

# ==================== MANEJADORES ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mensaje de bienvenida profesional con instrucciones claras"""
    welcome_msg = (
        "🎬 <b>YouTube Downloader Bot</b>\n\n"
        "¡Hola! 👋 Soy tu asistente para descargar contenido de YouTube de forma rápida y sencilla.\n\n"
        "✅ <b>¿Qué puedo hacer por ti?</b>\n"
        "   • Descargar videos en formato MP4 (hasta 720p)\n"
        "   • Extraer audio en formato MP3 de alta calidad\n"
        "   • Procesar enlaces de YouTube, Shorts y enlaces cortos\n\n"
        "📌 <b>Instrucciones de uso:</b>\n"
        "   1️⃣ Envía cualquier enlace de YouTube\n"
        "   2️⃣ Selecciona el formato deseado (MP4 o MP3)\n"
        "   3️⃣ ¡Recibe tu archivo en segundos!\n\n"
        "⚠️ <b>Importante:</b>\n"
        "   • Límite: 10 descargas por hora\n"
        "   • Tamaño máximo: 49MB (~8-10 min en 720p)\n"
        "   • Solo para uso personal y legal\n"
        "   • Respeta los derechos de autor\n\n"
        "✨ <i>¡Listo para empezar? ¡Envía tu primer enlace!</i>"
    )

    keyboard = [
        [
            InlineKeyboardButton("ℹ️ Acerca de", callback_data="about"),
            InlineKeyboardButton("⚖️ Términos", callback_data="terms")
        ],
        [
            InlineKeyboardButton("✅ Empezar ahora", callback_data="help_start")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(
            welcome_msg,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    else:
        query = update.callback_query
        await query.edit_message_text(
            welcome_msg,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )

async def about_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Información sobre el bot (para callback)"""
    query = update.callback_query
    await query.answer()

    about_msg = (
        "ℹ️ <b>Acerca de YouTube Downloader Bot</b>\n\n"
        "🤖 <b>Versión:</b> 2.0\n"
        "⚡ <b>Características:</b>\n"
        "   • Descarga rápida de videos y audio\n"
        "   • Soporte para todos los formatos de YouTube\n"
        "   • Límite de tamaño inteligente (49MB)\n"
        "   • Sistema de rate limiting integrado\n"
        "   • Limpieza automática de archivos temporales\n\n"
        "🔒 <b>Seguridad:</b>\n"
        "   • Archivos eliminados inmediatamente después de enviar\n"
        "   • Sin almacenamiento permanente de contenido\n"
        "   • Cumple con políticas de Telegram\n\n"
        "👨‍💻 <b>Desarrollado con:</b>\n"
        "   • Python 3.10+\n"
        "   • python-telegram-bot 20.6\n"
        "   • yt-dlp\n"
        "   • FFmpeg (para conversión de audio)\n\n"
        "💡 <i>Este bot es de código abierto y para uso educativo/personal.</i>"
    )

    keyboard = [[InlineKeyboardButton("⬅️ Volver al inicio", callback_data="start")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        about_msg,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def terms_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Términos de uso (para callback)"""
    query = update.callback_query
    await query.answer()

    terms_msg = (
        "⚖️ <b>Términos de Uso</b>\n\n"
        "Al utilizar este bot, aceptas los siguientes términos:\n\n"
        "✅ <b>Uso Permitido:</b>\n"
        "   • Descargar tus propios videos\n"
        "   • Contenido con licencia Creative Commons\n"
        "   • Material de dominio público\n"
        "   • Contenido con permiso explícito del creador\n\n"
        "❌ <b>Uso Prohibido:</b>\n"
        "   • Descargar contenido con copyright sin permiso\n"
        "   • Distribuir material protegido ilegalmente\n"
        "   • Usar el bot para actividades comerciales masivas\n"
        "   • Evadir sistemas de protección de derechos\n\n"
        "⚠️ <b>Responsabilidad:</b>\n"
        "   • Eres responsable legal del contenido que descargas\n"
        "   • El desarrollador no se hace responsable del mal uso\n"
        "   • YouTube y Telegram son marcas registradas\n"
        "   • Este bot no está afiliado a Google/YouTube/Telegram\n\n"
        "💡 <i>Al continuar usando el bot, aceptas estos términos.</i>"
    )

    keyboard = [[InlineKeyboardButton("⬅️ Volver al inicio", callback_data="start")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        terms_msg,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def help_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guía rápida (para callback)"""
    query = update.callback_query
    await query.answer()

    help_msg = (
        "🚀 <b>Guía Rápida de Inicio</b>\n\n"
        "Sigue estos 3 simples pasos:\n\n"
        "❶ <b>Envía un enlace de YouTube</b>\n"
        "   Ejemplos válidos:\n"
        "   • <code>https://youtu.be/dQw4w9WgXcQ</code>\n"
        "   • <code>https://www.youtube.com/watch?v=XUoXE3bmDJY</code>\n"
        "   • <code>https://youtube.com/shorts/abc123</code>\n\n"
        "❷ <b>Selecciona el formato</b>\n"
        "   • 🎥 <b>MP4</b> - Video con audio (hasta 720p)\n"
        "   • 🎵 <b>MP3</b> - Solo audio (192kbps)\n\n"
        "❸ <b>Recibe tu archivo</b>\n"
        "   • El archivo se enviará en segundos\n"
        "   • Se elimina automáticamente del servidor\n\n"
        "⚠️ <b>Límites:</b>\n"
        "   • Máximo 10 descargas por hora\n"
        "   • Tamaño máximo: 49MB\n\n"
        "💡 <i>¡Listo! Envía tu primer enlace para comenzar.</i>"
    )

    keyboard = [[InlineKeyboardButton("⬅️ Volver al inicio", callback_data="start")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        help_msg,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

# ✅ WRAPPERS PARA COMANDOS (cambios mínimos)
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_msg = (
        "🚀 <b>Guía Rápida de Inicio</b>\n\n"
        "Envía un enlace de YouTube y elige MP4 o MP3.\n\n"
        "✅ Ejemplos:\n"
        "• <code>https://youtu.be/VIDEO_ID</code>\n"
        "• <code>https://www.youtube.com/watch?v=VIDEO_ID</code>\n"
        "• <code>https://youtube.com/shorts/VIDEO_ID</code>\n\n"
        "⚠️ Límite: 10 descargas/hora | Tamaño máx: 49MB\n"
    )
    await update.message.reply_text(help_msg, parse_mode=ParseMode.HTML)

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    about_msg = (
        "ℹ️ <b>Acerca de</b>\n\n"
        "🤖 YouTube Downloader Bot v2.0\n"
        "✅ MP4 (hasta 720p)\n"
        "✅ MP3 (192kbps)\n"
        "🔧 python-telegram-bot 20.6 + yt-dlp\n"
    )
    await update.message.reply_text(about_msg, parse_mode=ParseMode.HTML)

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa URLs de YouTube y muestra opciones de descarga"""
    url = update.message.text.strip()

    if not is_valid_youtube_url(url):
        error_msg = (
            "❌ <b>URL no reconocida</b>\n\n"
            "Por favor, envía un enlace válido de YouTube:\n\n"
            "✅ <b>Ejemplos válidos:</b>\n"
            "   • <code>https://youtu.be/VIDEO_ID</code>\n"
            "   • <code>https://www.youtube.com/watch?v=VIDEO_ID</code>\n"
            "   • <code>https://youtube.com/shorts/VIDEO_ID</code>\n"
        )
        await update.message.reply_text(error_msg, parse_mode=ParseMode.HTML)
        return

    allowed, info = check_rate_limit(update.effective_user.id)
    if not allowed:
        hours = info // 3600
        minutes = (info % 3600) // 60
        wait_msg = (
            f"⏳ <b>Límite de descargas alcanzado</b>\n\n"
            f"Has alcanzado el máximo de {MAX_DOWNLOADS_PER_HOUR} descargas por hora.\n\n"
            f"⏱ <b>Tiempo de espera:</b> {hours}h {minutes}m"
        )
        await update.message.reply_text(wait_msg, parse_mode=ParseMode.HTML)
        return

    processing_msg = await update.message.reply_text(
        "🔍 <b>Analizando enlace...</b>\n\nExtrayendo información del video...",
        parse_mode=ParseMode.HTML
    )

    video_info = extract_video_info(url)

    keyboard = [
        [InlineKeyboardButton("🎬 Descargar MP4 (720p)", callback_data=f"video|{url}")],
        [InlineKeyboardButton("🎵 Extraer MP3 (192kbps)", callback_data=f"audio|{url}")],
        [InlineKeyboardButton("⬅️ Cancelar", callback_data="cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if video_info:
        title = sanitize_filename(video_info['title'])
        duration_str = format_duration(video_info['duration'])
        views_str = f"{video_info['views']:,}" if video_info['views'] else "N/A"
        uploader = video_info['uploader']

        info_msg = (
            "✅ <b>Video encontrado</b>\n\n"
            f"📹 <b>Título:</b> {title}\n"
            f"👤 <b>Canal:</b> {uploader}\n"
            f"⏱ <b>Duración:</b> {duration_str}\n"
            f"👁 <b>Visitas:</b> {views_str}\n\n"
            "👇 <b>Selecciona el formato de descarga:</b>"
        )

        await processing_msg.edit_text(
            info_msg, parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )
    else:
        await processing_msg.edit_text(
            "⚠️ <b>Video detectado</b>\n\n"
            "No pudimos obtener información detallada, pero podemos intentar la descarga.\n\n"
            "👇 <b>Selecciona el formato:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja los callbacks de los botones"""
    query = update.callback_query
    await query.answer()

    if query.data == "start":
        await start(update, context)
        return
    elif query.data == "about":
        await about_handler(update, context)
        return
    elif query.data == "terms":
        await terms_handler(update, context)
        return
    elif query.data == "help_start":
        await help_start_handler(update, context)
        return
    elif query.data == "cancel":
        await query.edit_message_text(
            "❌ <b>Operación cancelada</b>\n\nPuedes enviar otro enlace cuando quieras.",
            parse_mode=ParseMode.HTML
        )
        return

    data = query.data.split("|", 1)
    if len(data) != 2:
        await query.edit_message_text(
            "❌ <b>Error en la solicitud</b>\n\nDatos inválidos. Envía el enlace nuevamente.",
            parse_mode=ParseMode.HTML
        )
        return

    action, url = data

    status_msg = (
        "⏬ <b>Descargando video...</b>\n\n🎥 Formato: MP4 (720p)\n⏱ Por favor espera..."
        if action == "video"
        else "⏬ <b>Extrayendo audio...</b>\n\n🎵 Formato: MP3 (192kbps)\n⏱ Por favor espera..."
    )
    await query.edit_message_text(status_msg, parse_mode=ParseMode.HTML)

    filepath = None
    try:
        if action == "video":
            filepath, title, duration = download_video(url, TEMP_DIR)
            file_type = "video"
        else:
            filepath, title, duration = download_audio(url, TEMP_DIR)
            file_type = "audio"

        file_size = os.path.getsize(filepath)
        if file_size > MAX_FILE_SIZE:
            size_mb = file_size / 1024 / 1024
            max_mb = MAX_FILE_SIZE / 1024 / 1024
            raise Exception(
                f"El archivo ({size_mb:.1f}MB) excede el límite de {max_mb:.0f}MB.\n"
                f"Videos mayores a ~10 minutos en 720p suelen superar este límite."
            )

        caption = (
            f"✅ <b>{title[:45]}</b>\n\n"
            f"⏱ Duración: {format_duration(duration)}\n"
            f"📦 Tamaño: {format_size(file_size)}\n"
            f"{'🎬 Formato: MP4 (720p)' if file_type == 'video' else '🎵 Formato: MP3 (192kbps)'}\n\n"
            f"⚠️ <i>Uso personal y legal únicamente</i>"
        )

        if file_type == "video":
            with open(filepath, 'rb') as video:
                await query.message.reply_video(
                    video=video,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    supports_streaming=True
                )
        else:
            with open(filepath, 'rb') as audio:
                await query.message.reply_audio(
                    audio=audio,
                    caption=caption,
                    parse_mode=ParseMode.HTML
                )

        keyboard = [
            [InlineKeyboardButton("🔄 Descargar otro", callback_data="start")],
            [InlineKeyboardButton("ℹ️ Ayuda", callback_data="help_start")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.reply_text(
            "🎉 <b>¡Descarga completada con éxito!</b>\n\n"
            "✅ Tu archivo ha sido enviado.\n"
            "🧹 El archivo se eliminó automáticamente del servidor.\n\n"
            "¿Quieres descargar otro video?",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )

    except Exception as e:
        error_msg = str(e).lower()
        user_msg = "❌ <b>Error durante la descarga</b>\n\nOcurrió un problema inesperado."

        if any(x in error_msg for x in ["private", "sign in", "age", "confirm your age"]):
            user_msg = (
                "🔒 <b>Video privado o restringido</b>\n\n"
                "YouTube no permite descargar este contenido (privado/edad/login).\n"
                "💡 <i>Usa un video público sin restricciones.</i>"
            )
        elif any(x in error_msg for x in ["copyright", "blocked", "unavailable"]):
            user_msg = (
                "©️ <b>Restricciones de copyright</b>\n\n"
                "El video tiene protección o restricción.\n"
                "💡 <i>Intenta con otro video.</i>"
            )
        elif "ffmpeg" in error_msg or "ffprobe" in error_msg:
            user_msg = (
                "🔧 <b>Error de conversión</b>\n\n"
                "FFmpeg no está instalado o configurado correctamente.\n"
                "💡 <i>Instala FFmpeg en el servidor/PC.</i>"
            )
        elif "timed out" in error_msg or "timeout" in error_msg or "socket" in error_msg:
            user_msg = (
                "⏱ <b>Tiempo de espera agotado</b>\n\n"
                "YouTube no respondió a tiempo.\n"
                "💡 <i>Intenta nuevamente en unos minutos.</i>"
            )
        elif "file too large" in error_msg or "49mb" in error_msg or "50mb" in error_msg:
            user_msg = (
                f"📦 <b>Archivo demasiado grande</b>\n\n"
                f"El archivo excede el límite de {format_size(MAX_FILE_SIZE)}.\n"
                "💡 <i>Usa un video más corto o descarga MP3.</i>"
            )
        else:
            user_msg = (
                "❌ <b>Error durante la descarga</b>\n\n"
                "Ocurrió un problema inesperado.\n\n"
                f"<code>Error: {str(e)[:120]}</code>"
            )

        keyboard = [
            [InlineKeyboardButton("🔄 Intentar nuevamente", callback_data=f"{action}|{url}")],
            [InlineKeyboardButton("⬅️ Volver al inicio", callback_data="start")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.reply_text(
            user_msg,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )

        logger.error(f"Error descargando {url} para usuario {update.effective_user.id}: {e}")

    finally:
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
                logger.info(f"🧹 Archivo temporal eliminado: {os.path.basename(filepath)}")
            except Exception as e:
                logger.warning(f"No se pudo eliminar {filepath}: {e}")

# ==================== INICIALIZACIÓN ====================
async def post_init(app: Application):
    # ✅ v20.6: set_my_commands es async → debe llevar await
    commands = [
        BotCommand("start", "✨ Iniciar el bot y ver instrucciones"),
        BotCommand("help", "📚 Ver guía de uso"),
        BotCommand("about", "ℹ️ Información sobre el bot"),
    ]
    await app.bot.set_my_commands(commands)

def main():
    os.makedirs(TEMP_DIR, exist_ok=True)

    if not BOT_TOKEN or BOT_TOKEN == "TU_TOKEN_AQUI":
        print("\n" + "=" * 70)
        print("❌ ERROR CRÍTICO: TOKEN NO CONFIGURADO")
        print("=" * 70)
        print("\n💡 Configura la variable de entorno TELEGRAM_BOT_TOKEN o pega tu token en BOT_TOKEN.")
        print("=" * 70 + "\n")
        return

    try:
        application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    except Exception as e:
        print("\n" + "=" * 70)
        print("❌ ERROR AL INICIAR EL BOT")
        print("=" * 70)
        print(f"\nError: {e}\n")
        print("Causas comunes:")
        print("  • Token inválido")
        print("  • Conflicto por paquete 'telegram' instalado")
        print("\n💡 Solución:")
        print("  pip uninstall -y telegram python-telegram-bot")
        print("  pip install python-telegram-bot==20.6")
        print("=" * 70 + "\n")
        return

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))   # ✅ comando real
    application.add_handler(CommandHandler("about", about_command)) # ✅ comando real
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    application.add_handler(CallbackQueryHandler(button_handler))

    print("\n" + "=" * 70)
    print("🤖 YOUTUBE DOWNLOADER BOT - v2.0 (python-telegram-bot 20.6)")
    print("=" * 70)
    print(f"\n✅ Token configurado")
    print(f"📁 Temp: {os.path.abspath(TEMP_DIR)}")
    print(f"📦 Límite: {format_size(MAX_FILE_SIZE)} (49MB)")
    print(f"⏱  Rate limit: {MAX_DOWNLOADS_PER_HOUR}/hora/usuario")
    print(f"📄 Logs: bot.log")
    print("\n⚠️ Presiona Ctrl+C para detener\n")
    print("=" * 70 + "\n")

    application.run_polling()

if __name__ == "__main__":
    main()
