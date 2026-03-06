import os
import sys
import sqlite3
import functools
import tempfile
import subprocess
import uuid
import json
import requests
import time
from io import BytesIO

from werkzeug.utils import secure_filename
from dotenv import load_dotenv

from flask import (
    Flask, render_template, g, request, redirect,
    url_for, session, make_response, flash, jsonify
)
from flask_mail import Mail, Message
from twilio.rest import Client
import pandas as pd

# Chargement des variables d'environnement
load_dotenv()

app = Flask(__name__)
app.config['DATABASE'] = 'restaurant.db'
app.secret_key = 'une_cle_secrete_tres_longue_et_difficile_a_deviner'

# Configuration de l'upload d'images
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 Mo max
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Configuration Flask-Mail
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'votre.email@gmail.com'
app.config['MAIL_PASSWORD'] = 'motdepasse'
mail = Mail(app)

# Configuration Twilio (à remplacer par vos identifiants)
twilio_account_sid = os.environ.get('TWILIO_ACCOUNT_SID', 'ton_account_sid')
twilio_auth_token = os.environ.get('TWILIO_AUTH_TOKEN', 'ton_auth_token')
twilio_phone_number = os.environ.get('TWILIO_PHONE_NUMBER', '+1234567890')
twilio_client = Client(twilio_account_sid, twilio_auth_token)

# -------------------- Configuration PAYTECH --------------------
PAYTECH_API_KEY = os.environ.get('PAYTECH_API_KEY')
PAYTECH_SECRET_KEY = os.environ.get('PAYTECH_SECRET_KEY')
PAYTECH_BASE_URL = os.environ.get('PAYTECH_BASE_URL', 'https://paytech.sn/api')

def initier_paiement_paytech(montant, telephone, commande_id, description="Commande Néo Pizza"):
    """Initie un paiement via PAYTECH et retourne l'URL de redirection."""
    headers = {
        'API_KEY': PAYTECH_API_KEY,
        'API_SECRET': PAYTECH_SECRET_KEY,
        'Content-Type': 'application/json'
    }

    # Génération d'une référence unique (timestamp + commande_id)
    ref_unique = f'CMD{commande_id}_{int(time.time())}'

    payload = {
        'item_name': description[:50],
        'item_price': int(montant),
        'currency': 'XOF',
        'ref_command': ref_unique,               # Référence unique
        'command_name': description[:100],
        'env': 'test',  # <-- MODIFICATION : forcé en mode test (en attendant activation)
        'ipn_url': url_for('paytech_webhook', _external=True),
        'success_url': url_for('confirmation', commande_id=commande_id, _external=True),
        'cancel_url': url_for('paiement_erreur', commande_id=commande_id, _external=True)
    }

    if telephone:
        payload['target_payment'] = 'Orange Money, Wave, Free Money'

    try:
        full_url = f"{PAYTECH_BASE_URL}/payment/request-payment"
        print(f"Envoi requête PAYTECH vers {full_url}")
        print(f"Payload: {payload}")

        response = requests.post(
            full_url,
            json=payload,
            headers=headers,
            timeout=30
        )

        print(f"Réponse HTTP {response.status_code}")
        print(f"Contenu: {response.text}")

        if response.status_code == 200:
            data = response.json()
            if data.get('success') == 1:
                token = data.get('token')
                return f"https://paytech.sn/payment/checkout/{token}"
            else:
                print("La requête n'a pas réussi (success != 1)")
                return None
        else:
            print(f"Erreur HTTP {response.status_code}")
            return None

    except requests.exceptions.RequestException as e:
        print(f"Erreur PAYTECH : {e}")
        return None

@app.route('/webhook/paytech', methods=['POST'])
def paytech_webhook():
    """Reçoit la confirmation de paiement de PAYTECH."""
    data = request.json
    print("Webhook reçu :", data)

    ref_command = data.get('ref_command')
    if ref_command and ref_command.startswith('CMD'):
        # Extraire l'ID de commande (format: CMD123_1234567890)
        commande_id = ref_command.split('_')[0][3:]  # prend la partie avant le _
        if data.get('status') == 'completed':
            db = get_db()
            db.execute('UPDATE commandes SET statut = ?, mode_paiement = ? WHERE id = ?',
                       ('payée', 'paytech', commande_id))
            db.commit()
            return jsonify({'message': 'OK'}), 200

    return jsonify({'error': 'Invalid data'}), 400

@app.route('/paiement_erreur/<int:commande_id>')
def paiement_erreur(commande_id):
    flash("Le paiement a échoué ou a été annulé. Veuillez réessayer.", "warning")
    return redirect(url_for('choix_paiement', commande_id=commande_id))

# -------------------- Fonctions utilitaires --------------------
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(app.config['DATABASE'])
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def login_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return view(**kwargs)
    return wrapped_view

def init_db():
    """Crée les tables et insère les données de base si elles n'existent pas."""
    db = get_db()
    db.executescript('''
        CREATE TABLE IF NOT EXISTS restaurants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL,
            adresse TEXT,
            telephone TEXT
        );
        CREATE TABLE IF NOT EXISTS tables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero INTEGER NOT NULL,
            restaurant_id INTEGER NOT NULL,
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
        );
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL,
            restaurant_id INTEGER NOT NULL,
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
        );
        CREATE TABLE IF NOT EXISTS plats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL,
            description TEXT,
            prix REAL NOT NULL,
            categorie_id INTEGER NOT NULL,
            restaurant_id INTEGER NOT NULL,
            image_url TEXT,
            FOREIGN KEY (categorie_id) REFERENCES categories(id),
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
        );
        CREATE TABLE IF NOT EXISTS commandes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_id INTEGER NOT NULL,
            statut TEXT NOT NULL,
            total REAL NOT NULL,
            mode_paiement TEXT,
            date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (table_id) REFERENCES tables(id)
        );
        CREATE TABLE IF NOT EXISTS commande_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            commande_id INTEGER NOT NULL,
            plat_id INTEGER NOT NULL,
            quantite INTEGER NOT NULL,
            FOREIGN KEY (commande_id) REFERENCES commandes(id),
            FOREIGN KEY (plat_id) REFERENCES plats(id)
        );
    ''')

    # Vérifier si le restaurant 1 existe, sinon l'initialiser
    resto = db.execute('SELECT * FROM restaurants WHERE id = 1').fetchone()
    if not resto:
        db.execute("INSERT INTO restaurants (id, nom, adresse, telephone) VALUES (1, 'Néo Pizza', 'Guédiawaye', '78 730 19 19')")
        # Créer les tables 1 à 10
        for i in range(1, 11):
            db.execute("INSERT INTO tables (numero, restaurant_id) VALUES (?, ?)", (i, 1))
        # Ajouter les catégories
        categories = ['PIZZA', 'TACOS', 'POUL PANÉ', 'BOX SALÉ', 'DESSERTS & BOISSONS']
        for cat in categories:
            db.execute("INSERT INTO categories (nom, restaurant_id) VALUES (?, ?)", (cat, 1))
        # Ajouter quelques plats (vous pouvez compléter avec vos données)
        pizza_id = db.execute("SELECT id FROM categories WHERE nom = 'PIZZA' AND restaurant_id = 1").fetchone()['id']
        plats_pizza = [
            ("REINE DE MON CŒUR", "Mergeuz ou jambon, piment vert, fromage, sauce tomates", 4500, pizza_id),
            ("MOUSSAKA", "Viande hachée, poivron, fromage, sauce tomates", 3500, pizza_id),
        ]
        for nom, desc, prix, cat_id in plats_pizza:
            db.execute("INSERT INTO plats (nom, description, prix, categorie_id, restaurant_id) VALUES (?, ?, ?, ?, 1)",
                       (nom, desc, prix, cat_id))
        db.commit()

# Appeler l'initialisation au démarrage
with app.app_context():
    init_db()

def envoyer_sms(telephone, message):
    try:
        twilio_client.messages.create(
            body=message,
            from_=twilio_phone_number,
            to=telephone
        )
        return True
    except Exception as e:
        print(f"Erreur SMS : {e}")
        return False

def save_uploaded_image(file):
    """Sauvegarde un fichier image uploadé et retourne le chemin relatif."""
    if file and file.filename != '':
        filename = secure_filename(file.filename)
        unique_name = str(uuid.uuid4()) + '_' + filename
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
        file.save(file_path)
        return os.path.join('uploads', unique_name).replace('\\', '/')
    return None

# -------------------- Routes publiques (client) --------------------
@app.route('/')
def accueil():
    restaurant_id = 1
    table_id = 1

    db = get_db()
    restaurant = db.execute('SELECT * FROM restaurants WHERE id = ?', (restaurant_id,)).fetchone()
    if not restaurant:
        return "Restaurant non trouvé", 404

    table = db.execute('SELECT * FROM tables WHERE restaurant_id = ? AND numero = ?', (restaurant_id, table_id)).fetchone()
    if not table:
        return "Table non trouvée", 404

    categories = db.execute('SELECT * FROM categories WHERE restaurant_id = ? ORDER BY id', (restaurant_id,)).fetchall()
    plats_par_categorie = {}
    for cat in categories:
        plats = db.execute('SELECT * FROM plats WHERE categorie_id = ? ORDER BY nom', (cat['id'],)).fetchall()
        plats_par_categorie[cat['nom']] = plats

    return render_template('menu.html', restaurant=restaurant, table_id=table_id, plats_par_categorie=plats_par_categorie)

@app.route('/restaurant/<int:restaurant_id>/table/<int:table_id>')
def menu(restaurant_id, table_id):
    db = get_db()
    restaurant = db.execute('SELECT * FROM restaurants WHERE id = ?', (restaurant_id,)).fetchone()
    if not restaurant:
        return "Restaurant non trouvé", 404
    table = db.execute('SELECT * FROM tables WHERE restaurant_id = ? AND numero = ?', (restaurant_id, table_id)).fetchone()
    if not table:
        return "Table non trouvée", 404
    categories = db.execute('SELECT * FROM categories WHERE restaurant_id = ? ORDER BY id', (restaurant_id,)).fetchall()
    plats_par_categorie = {}
    for cat in categories:
        plats = db.execute('SELECT * FROM plats WHERE categorie_id = ? ORDER BY nom', (cat['id'],)).fetchall()
        plats_par_categorie[cat['nom']] = plats
    return render_template('menu.html', restaurant=restaurant, table_id=table_id, plats_par_categorie=plats_par_categorie)

@app.route('/commander', methods=['POST'])
def commander():
    db = get_db()
    restaurant_id = request.form['restaurant_id']
    table_id = request.form['table_id']
    plats_ids = request.form.getlist('plats')

    if not plats_ids:
        return "Aucun plat sélectionné. <a href='javascript:history.back()'>Retour</a>", 400

    total = 0
    details = []
    for plat_id in plats_ids:
        quantite = int(request.form.get(f'quantite_{plat_id}', 1))
        plat = db.execute('SELECT prix FROM plats WHERE id = ?', (plat_id,)).fetchone()
        if plat:
            total += plat['prix'] * quantite
            details.append((plat_id, quantite))
        else:
            return f"Plat {plat_id} introuvable. <a href='javascript:history.back()'>Retour</a>", 400

    cur = db.execute('''
        INSERT INTO commandes (table_id, statut, total, mode_paiement)
        VALUES (?, ?, ?, ?)
    ''', (table_id, 'en attente', total, None))
    commande_id = cur.lastrowid

    for plat_id, quantite in details:
        db.execute('''
            INSERT INTO commande_details (commande_id, plat_id, quantite)
            VALUES (?, ?, ?)
        ''', (commande_id, plat_id, quantite))

    db.commit()
    return redirect(url_for('choix_paiement', commande_id=commande_id))

@app.route('/paiement/<int:commande_id>')
def choix_paiement(commande_id):
    db = get_db()
    commande = db.execute('SELECT * FROM commandes WHERE id = ?', (commande_id,)).fetchone()
    if not commande:
        return "Commande introuvable", 404
    return render_template('paiement.html', commande=commande)

@app.route('/payer', methods=['POST'])
def payer():
    db = get_db()
    commande_id = request.form['commande_id']
    mode = request.form['mode']

    if mode == 'especes':
        db.execute('UPDATE commandes SET mode_paiement = ?, statut = ? WHERE id = ?',
                   (mode, 'payée', commande_id))
        db.commit()
        return redirect(url_for('confirmation', commande_id=commande_id))
    else:
        commande = db.execute('SELECT * FROM commandes WHERE id = ?', (commande_id,)).fetchone()
        if not commande:
            return "Commande introuvable", 404
        return render_template('paiement_mobile.html', commande_id=commande_id, mode=mode, commande=commande)

@app.route('/payer_mobile', methods=['POST'])
def payer_mobile():
    db = get_db()
    commande_id = request.form['commande_id']
    mode = request.form['mode']
    telephone = request.form['telephone']
    print(f"Mode reçu : {mode}")

    # Vérifier que la commande existe et n'est pas déjà payée
    commande = db.execute('SELECT total, statut FROM commandes WHERE id = ?', (commande_id,)).fetchone()
    if not commande:
        flash("Commande introuvable.", "danger")
        return redirect(url_for('accueil'))
    if commande['statut'] == 'payée':
        flash("Cette commande a déjà été payée.", "info")
        return redirect(url_for('confirmation', commande_id=commande_id))

    if mode == 'paytech':
        payment_url = initier_paiement_paytech(commande['total'], telephone, commande_id)
        if payment_url:
            return redirect(payment_url)
        else:
            flash("Erreur avec PAYTECH. Paiement simulé (mode test).", "warning")
            db.execute('UPDATE commandes SET mode_paiement = ?, statut = ? WHERE id = ?',
                       ('paytech_simulé', 'payée', commande_id))
            db.commit()
            return redirect(url_for('confirmation', commande_id=commande_id))
    else:
        db.execute('UPDATE commandes SET mode_paiement = ?, statut = ? WHERE id = ?',
                   (mode, 'payée', commande_id))
        db.commit()
        return redirect(url_for('confirmation', commande_id=commande_id))

@app.route('/confirmation/<int:commande_id>')
def confirmation(commande_id):
    db = get_db()
    commande = db.execute('''
        SELECT c.*, t.numero as table_num, t.restaurant_id
        FROM commandes c
        JOIN tables t ON c.table_id = t.id
        WHERE c.id = ?
    ''', (commande_id,)).fetchone()
    if not commande:
        return "Commande introuvable", 404
    return render_template('confirmation.html', commande=commande, restaurant_id=commande['restaurant_id'], table_id=commande['table_num'])

# -------------------- Routes pour le gérant (authentification) --------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form['password'] == 'admin123':
            session['logged_in'] = True
            session['restaurant_id'] = int(request.form.get('restaurant_id', 1))
            return redirect(url_for('dashboard'))
        else:
            error = 'Mot de passe incorrect'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('accueil'))

# -------------------- Tableau de bord principal --------------------
@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    restaurant_id = session.get('restaurant_id', 1)
    filtre = request.args.get('statut', 'tous')

    if filtre == 'tous':
        commandes = db.execute('''
            SELECT c.*, t.numero as table_num
            FROM commandes c
            JOIN tables t ON c.table_id = t.id
            WHERE t.restaurant_id = ?
            ORDER BY c.date_creation DESC
        ''', (restaurant_id,)).fetchall()
    else:
        commandes = db.execute('''
            SELECT c.*, t.numero as table_num
            FROM commandes c
            JOIN tables t ON c.table_id = t.id
            WHERE t.restaurant_id = ? AND c.statut = ?
            ORDER BY c.date_creation DESC
        ''', (restaurant_id, filtre)).fetchall()

    return render_template('dashboard.html', commandes=commandes, filtre=filtre)

@app.route('/changer_statut', methods=['POST'])
@login_required
def changer_statut():
    db = get_db()
    commande_id = request.form['commande_id']
    nouveau_statut = request.form['nouveau_statut']
    db.execute('UPDATE commandes SET statut = ? WHERE id = ?', (nouveau_statut, commande_id))
    db.commit()
    return redirect(url_for('dashboard'))

@app.route('/commande/<int:commande_id>')
@login_required
def detail_commande(commande_id):
    db = get_db()
    commande = db.execute('''
        SELECT c.*, t.numero as table_num
        FROM commandes c
        JOIN tables t ON c.table_id = t.id
        WHERE c.id = ?
    ''', (commande_id,)).fetchone()
    if not commande:
        return "Commande introuvable", 404
    details = db.execute('''
        SELECT p.nom, cd.quantite, p.prix
        FROM commande_details cd
        JOIN plats p ON cd.plat_id = p.id
        WHERE cd.commande_id = ?
    ''', (commande_id,)).fetchall()
    return render_template('detail_commande.html', commande=commande, details=details)

# -------------------- Gestion des catégories --------------------
@app.route('/categories')
@login_required
def liste_categories():
    db = get_db()
    restaurant_id = session.get('restaurant_id', 1)
    categories = db.execute('SELECT * FROM categories WHERE restaurant_id = ? ORDER BY id', (restaurant_id,)).fetchall()
    return render_template('categories.html', categories=categories)

@app.route('/ajouter_categorie', methods=['POST'])
@login_required
def ajouter_categorie():
    db = get_db()
    nom = request.form['nom']
    restaurant_id = session.get('restaurant_id', 1)
    db.execute('INSERT INTO categories (nom, restaurant_id) VALUES (?, ?)', (nom, restaurant_id))
    db.commit()
    return redirect(url_for('liste_categories'))

@app.route('/modifier_categorie/<int:categorie_id>', methods=['GET', 'POST'])
@login_required
def modifier_categorie(categorie_id):
    db = get_db()
    if request.method == 'POST':
        nouveau_nom = request.form['nom']
        db.execute('UPDATE categories SET nom = ? WHERE id = ?', (nouveau_nom, categorie_id))
        db.commit()
        return redirect(url_for('liste_categories'))
    else:
        categorie = db.execute('SELECT * FROM categories WHERE id = ?', (categorie_id,)).fetchone()
        if not categorie:
            return "Catégorie introuvable", 404
        return render_template('modifier_categorie.html', categorie=categorie)

@app.route('/supprimer_categorie/<int:categorie_id>')
@login_required
def supprimer_categorie(categorie_id):
    db = get_db()
    plats = db.execute('SELECT COUNT(*) as count FROM plats WHERE categorie_id = ?', (categorie_id,)).fetchone()
    if plats['count'] > 0:
        return "Impossible de supprimer : des plats sont encore liés à cette catégorie. <a href='/categories'>Retour</a>", 400
    db.execute('DELETE FROM categories WHERE id = ?', (categorie_id,))
    db.commit()
    return redirect(url_for('liste_categories'))

# -------------------- Gestion du menu (CRUD) avec images --------------------
@app.route('/dashboard/menu')
@login_required
def gestion_plats():
    db = get_db()
    restaurant_id = session.get('restaurant_id', 1)
    categories = db.execute('SELECT * FROM categories WHERE restaurant_id = ? ORDER BY id', (restaurant_id,)).fetchall()
    plats_par_categorie = {}
    for cat in categories:
        plats = db.execute('SELECT * FROM plats WHERE categorie_id = ? ORDER BY nom', (cat['id'],)).fetchall()
        plats_par_categorie[cat['nom']] = plats
    return render_template('gestion_plats.html', plats_par_categorie=plats_par_categorie)

@app.route('/dashboard/plat/ajouter', methods=['GET', 'POST'])
@login_required
def ajouter_plat():
    db = get_db()
    restaurant_id = session.get('restaurant_id', 1)
    categories = db.execute('SELECT * FROM categories WHERE restaurant_id = ?', (restaurant_id,)).fetchall()

    if request.method == 'POST':
        nom = request.form['nom']
        description = request.form['description']
        prix = float(request.form['prix'])
        categorie_id = request.form['categorie_id']

        file = request.files.get('image_file')
        image_url = request.form.get('image_url', '').strip()

        if file and file.filename != '':
            saved_path = save_uploaded_image(file)
            if saved_path:
                image_url = saved_path

        db.execute('''
            INSERT INTO plats (nom, description, prix, categorie_id, restaurant_id, image_url)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (nom, description, prix, categorie_id, restaurant_id, image_url or None))
        db.commit()
        flash('Plat ajouté avec succès', 'success')
        return redirect(url_for('gestion_plats'))

    return render_template('form_plat.html', categories=categories, plat=None)

@app.route('/dashboard/plat/modifier/<int:plat_id>', methods=['GET', 'POST'])
@login_required
def modifier_plat(plat_id):
    db = get_db()
    restaurant_id = session.get('restaurant_id', 1)
    plat = db.execute('SELECT * FROM plats WHERE id = ? AND restaurant_id = ?', (plat_id, restaurant_id)).fetchone()
    if not plat:
        return "Plat introuvable", 404

    categories = db.execute('SELECT * FROM categories WHERE restaurant_id = ?', (restaurant_id,)).fetchall()

    if request.method == 'POST':
        nom = request.form['nom']
        description = request.form['description']
        prix = float(request.form['prix'])
        categorie_id = request.form['categorie_id']
        remove_image = request.form.get('remove_image') == 'on'

        file = request.files.get('image_file')
        new_image_url = request.form.get('image_url', '').strip()
        image_url = plat['image_url']

        if remove_image:
            if plat['image_url'] and not plat['image_url'].startswith('http'):
                old_path = os.path.join('static', plat['image_url'])
                if os.path.exists(old_path):
                    os.remove(old_path)
            image_url = None
        elif file and file.filename != '':
            saved_path = save_uploaded_image(file)
            if saved_path:
                if plat['image_url'] and not plat['image_url'].startswith('http'):
                    old_path = os.path.join('static', plat['image_url'])
                    if os.path.exists(old_path):
                        os.remove(old_path)
                image_url = saved_path
        elif new_image_url:
            image_url = new_image_url
            if plat['image_url'] and not plat['image_url'].startswith('http'):
                old_path = os.path.join('static', plat['image_url'])
                if os.path.exists(old_path):
                    os.remove(old_path)

        db.execute('''
            UPDATE plats SET nom=?, description=?, prix=?, categorie_id=?, image_url=?
            WHERE id=?
        ''', (nom, description, prix, categorie_id, image_url, plat_id))
        db.commit()
        flash('Plat modifié avec succès', 'success')
        return redirect(url_for('gestion_plats'))

    return render_template('form_plat.html', categories=categories, plat=plat)

@app.route('/dashboard/plat/supprimer/<int:plat_id>')
@login_required
def supprimer_plat(plat_id):
    db = get_db()
    restaurant_id = session.get('restaurant_id', 1)
    plat = db.execute('SELECT * FROM plats WHERE id = ? AND restaurant_id = ?', (plat_id, restaurant_id)).fetchone()
    if plat:
        if plat['image_url'] and not plat['image_url'].startswith('http'):
            image_path = os.path.join('static', plat['image_url'])
            if os.path.exists(image_path):
                os.remove(image_path)
        db.execute('DELETE FROM plats WHERE id = ?', (plat_id,))
        db.commit()
        flash('Plat supprimé', 'success')
    return redirect(url_for('gestion_plats'))

# -------------------- Statistiques --------------------
@app.route('/dashboard/stats')
@login_required
def stats():
    db = get_db()
    restaurant_id = session.get('restaurant_id', 1)

    ca_jour = db.execute('''
        SELECT COALESCE(SUM(c.total), 0) as ca_jour
        FROM commandes c
        JOIN tables t ON c.table_id = t.id
        WHERE t.restaurant_id = ? AND date(c.date_creation) = date('now') AND c.statut = 'payée'
    ''', (restaurant_id,)).fetchone()['ca_jour']

    ca_semaine = db.execute('''
        SELECT COALESCE(SUM(c.total), 0) as ca_semaine
        FROM commandes c
        JOIN tables t ON c.table_id = t.id
        WHERE t.restaurant_id = ? AND date(c.date_creation) >= date('now', '-7 days') AND c.statut = 'payée'
    ''', (restaurant_id,)).fetchone()['ca_semaine']

    ca_mois = db.execute('''
        SELECT COALESCE(SUM(c.total), 0) as ca_mois
        FROM commandes c
        JOIN tables t ON c.table_id = t.id
        WHERE t.restaurant_id = ? AND date(c.date_creation) >= date('now', '-30 days') AND c.statut = 'payée'
    ''', (restaurant_id,)).fetchone()['ca_mois']

    nb_commandes_jour = db.execute('''
        SELECT COUNT(*) as nb
        FROM commandes c
        JOIN tables t ON c.table_id = t.id
        WHERE t.restaurant_id = ? AND date(c.date_creation) = date('now')
    ''', (restaurant_id,)).fetchone()['nb']

    top_plats = db.execute('''
        SELECT p.nom, SUM(cd.quantite) as total_vendu
        FROM commande_details cd
        JOIN plats p ON cd.plat_id = p.id
        JOIN commandes c ON cd.commande_id = c.id
        JOIN tables t ON c.table_id = t.id
        WHERE t.restaurant_id = ? AND c.statut = 'payée'
        GROUP BY p.id
        ORDER BY total_vendu DESC
        LIMIT 5
    ''', (restaurant_id,)).fetchall()

    paiements = db.execute('''
        SELECT c.mode_paiement, COUNT(*) as nb
        FROM commandes c
        JOIN tables t ON c.table_id = t.id
        WHERE t.restaurant_id = ? AND c.statut = 'payée'
        GROUP BY c.mode_paiement
    ''', (restaurant_id,)).fetchall()

    return render_template('stats.html',
                           ca_jour=ca_jour,
                           ca_semaine=ca_semaine,
                           ca_mois=ca_mois,
                           nb_commandes_jour=nb_commandes_jour,
                           top_plats=top_plats,
                           paiements=paiements)

# -------------------- Vue cuisine --------------------
@app.route('/kitchen')
@login_required
def kitchen_view():
    db = get_db()
    restaurant_id = session.get('restaurant_id', 1)
    commandes = db.execute('''
        SELECT c.*, t.numero as table_num
        FROM commandes c
        JOIN tables t ON c.table_id = t.id
        WHERE t.restaurant_id = ? AND c.statut IN ('en attente', 'en préparation')
        ORDER BY c.date_creation ASC
    ''', (restaurant_id,)).fetchall()

    commandes_details = []
    for cmd in commandes:
        details = db.execute('''
            SELECT p.nom, cd.quantite
            FROM commande_details cd
            JOIN plats p ON cd.plat_id = p.id
            WHERE cd.commande_id = ?
        ''', (cmd['id'],)).fetchall()
        commandes_details.append({
            'commande': cmd,
            'details': details
        })

    return render_template('kitchen.html', commandes=commandes_details)

# -------------------- Exports PDF et Excel --------------------
# ROUTE PDF DÉSACTIVÉE POUR LE DÉPLOIEMENT (problème WeasyPrint)
# @app.route('/export/pdf/<int:commande_id>')
# @login_required
# def export_pdf(commande_id):
#     db = get_db()
#     commande = db.execute('''
#         SELECT c.*, t.numero as table_num
#         FROM commandes c
#         JOIN tables t ON c.table_id = t.id
#         WHERE c.id = ?
#     ''', (commande_id,)).fetchone()
#
#     details = db.execute('''
#         SELECT p.nom, cd.quantite, p.prix
#         FROM commande_details cd
#         JOIN plats p ON cd.plat_id = p.id
#         WHERE cd.commande_id = ?
#     ''', (commande_id,)).fetchall()
#
#     html_content = render_template('facture_pdf.html', commande=commande, details=details)
#
#     with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
#         f.write(html_content)
#         html_path = f.name
#
#     pdf_path = tempfile.mktemp(suffix='.pdf')
#
#     try:
#         subprocess.run(
#             [WEASYPRINT_EXE, html_path, pdf_path],
#             check=True,
#             capture_output=True,
#             text=True
#         )
#         with open(pdf_path, 'rb') as f:
#             pdf_data = f.read()
#         response = make_response(pdf_data)
#         response.headers['Content-Type'] = 'application/pdf'
#         response.headers['Content-Disposition'] = f'attachment; filename=facture_{commande_id}.pdf'
#         return response
#     except subprocess.CalledProcessError as e:
#         print(f"Erreur WeasyPrint : {e.stderr}")
#         return f"Erreur lors de la génération du PDF : {e.stderr}", 500
#     finally:
#         os.unlink(html_path)
#         if os.path.exists(pdf_path):
#             os.unlink(pdf_path)

@app.route('/export/excel')
@login_required
def export_excel():
    db = get_db()
    restaurant_id = session.get('restaurant_id', 1)

    commandes = db.execute('''
        SELECT c.id, t.numero as table_num, c.total, c.statut,
               c.mode_paiement, c.date_creation
        FROM commandes c
        JOIN tables t ON c.table_id = t.id
        WHERE t.restaurant_id = ?
        ORDER BY c.date_creation DESC
    ''', (restaurant_id,)).fetchall()

    data = []
    for cmd in commandes:
        data.append({
            'ID': cmd['id'],
            'Table': cmd['table_num'],
            'Total (FCFA)': cmd['total'],
            'Statut': cmd['statut'],
            'Paiement': cmd['mode_paiement'] or 'Non défini',
            'Date': cmd['date_creation']
        })

    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Commandes', index=False)
    output.seek(0)

    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    response.headers['Content-Disposition'] = 'attachment; filename=commandes_neo_pizza.xlsx'
    return response

# -------------------- Notification par email --------------------
@app.route('/notifier_client/<int:commande_id>')
@login_required
def notifier_client(commande_id):
    db = get_db()
    commande = db.execute('''
        SELECT c.*, t.numero as table_num
        FROM commandes c
        JOIN tables t ON c.table_id = t.id
        WHERE c.id = ?
    ''', (commande_id,)).fetchone()

    msg = Message(
        subject=f"Votre commande Néo Pizza #{commande_id} est prête !",
        sender=app.config['MAIL_USERNAME'],
        recipients=['client@example.com']
    )
    msg.body = f"""
    Bonjour,
    
    Votre commande #{commande_id} (table {commande['table_num']}) est prête !
    Vous pouvez venir la chercher à la caisse.
    
    Total : {commande['total']} FCFA
    
    Merci de votre confiance,
    L'équipe Néo Pizza
    """

    try:
        mail.send(msg)
        flash('Email envoyé au client', 'success')
    except Exception as e:
        flash(f"Erreur lors de l'envoi de l'email : {e}", 'danger')

    return redirect(url_for('dashboard'))

# -------------------- Lancement de l'application --------------------
if __name__ == '__main__':
    app.run(debug=True)
