# Utilise Python 3.11 pour garder imghdr
FROM python:3.11-slim

# Crée un dossier pour l'app
WORKDIR /app

# Copie les fichiers du projet
COPY . .

# Installe les dépendances
RUN pip install --no-cache-dir -r requirements.txt

# Expose le port (si besoin)
EXPOSE 10000

# Commande de démarrage
CMD ["gunicorn", "pers:app", "--bind", "0.0.0.0:10000"]
