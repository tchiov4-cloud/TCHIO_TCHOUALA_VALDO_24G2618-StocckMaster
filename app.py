from flask import Flask, render_template, request, redirect, url_for
import mysql.connector
import matplotlib
matplotlib.use('Agg') 
import matplotlib.pyplot as plt
import io, base64
import os 
from datetime import datetime
from collections import defaultdict

# ==========================================
# 1. INITIALISATION
# ==========================================
app = Flask(__name__)

def get_db():
    return mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASS'),
        port=int(os.getenv('DB_PORT', 4000)),
        database=os.getenv('DB_NAME', 'valdo_stock'),
        ssl_disabled=False,
        ssl_verify_cert=False # Obligatoire pour Render + TiDB Cloud
    )

def fig_to_b64(fig):
    img = io.BytesIO()
    fig.savefig(img, format='png', bbox_inches='tight', dpi=110, facecolor=fig.get_facecolor())
    plt.close(fig)
    return base64.b64encode(img.getvalue()).decode()
    


# Route pour initialiser la base de données
@app.route('/init_db')
def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # Table produits
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS produits (
                id INT AUTO_INCREMENT PRIMARY KEY,
                nom_produit VARCHAR(255) UNIQUE NOT NULL,
                quantite_casiers INT DEFAULT 0,
                capacite_max INT DEFAULT 100
            )
        """)
        
        # Table ventes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ventes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                produit VARCHAR(255) NOT NULL,
                quantite INT NOT NULL,
                type_mouvement VARCHAR(50) NOT NULL,
                date_vente DATE NOT NULL,
                prix_unitaire DECIMAL(10,2),
                client VARCHAR(255) DEFAULT 'Anonyme',
                telephone VARCHAR(50) DEFAULT '',
                type_categorie VARCHAR(100) DEFAULT 'Standard'
            )
        """)
        
        # Insérer les produits existants (liste intacte)
        cursor.execute("SELECT COUNT(*) as count FROM produits")
        count = cursor.fetchone()[0]
        
        if count == 0:
            produits_liste = [
                ('Malta', 85), ('Isembeck', 65), ('Castel', 45),
                ('Djino', 30), ('Mutzig', 55), ('Top', 70),
                ('Vimto', 25), ('Beaufort', 90), ('Guinness', 40), ('Kadji', 60)
            ]
            cursor.executemany(
                "INSERT INTO produits (nom_produit, quantite_casiers, capacite_max) VALUES (%s, %s, 100)",
                produits_liste
            )
        
        conn.commit()
        flash("Base de données initialisée avec succès !", "success")
        return redirect(url_for('dashboard'))
    except Exception as e:
        return f"Erreur d'initialisation : {str(e)}"
    finally:
        conn.close()

@app.route('/ajouter', methods=['POST'])
def ajouter():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    try:
        nom = request.form.get('produit')
        qte = int(request.form.get('qte', 0))
        mouv = request.form.get('type_mouvement').lower()
        date_vente = request.form.get('date_vente')
        prix_unitaire = float(request.form.get('pu', 0))
        client = request.form.get('client', 'Anonyme')
        telephone = request.form.get('tel', '')
        type_categorie = request.form.get('type_categorie', 'boisson')
        
        # Vérifier le produit
        cursor.execute("SELECT quantite_casiers FROM produits WHERE nom_produit = %s", (nom,))
        produit = cursor.fetchone()
        
        if not produit:
            flash(f"Produit '{nom}' non trouvé !", "error")
            return redirect(url_for('form'))
        
        stock_actuel = produit['quantite_casiers']
        
        # Mise à jour du stock
        if "sortie" in mouv or "vente" in mouv:
            if stock_actuel >= qte:
                nouveau_stock = stock_actuel - qte
                cursor.execute("""
                    UPDATE produits SET quantite_casiers = %s WHERE nom_produit = %s
                """, (nouveau_stock, nom))
                message = f"Vente effectuée ! Stock restant : {nouveau_stock} casiers"
            else:
                flash(f"Stock insuffisant ! Stock actuel : {stock_actuel}", "error")
                return redirect(url_for('form'))
        else:
            nouveau_stock = stock_actuel + qte
            cursor.execute("""
                UPDATE produits SET quantite_casiers = %s WHERE nom_produit = %s
            """, (nouveau_stock, nom))
            message = f"Entrée en stock effectuée ! Nouveau stock : {nouveau_stock} casiers"
        
        # Enregistrement
        cursor.execute("""
            INSERT INTO ventes (produit, quantite, type_mouvement, date_vente, prix_unitaire, client, telephone, type_categorie)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (nom, qte, mouv, date_vente, prix_unitaire, client, telephone, type_categorie))
        
        conn.commit()
        flash(message, "success")
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        conn.rollback()
        flash(f"Erreur : {str(e)}", "error")
        return redirect(url_for('form'))
    finally:
        conn.close()

@app.route('/')
@app.route('/dashboard')
def dashboard():
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # Récupérer tous les produits pour l'analyse
        cursor.execute("SELECT nom_produit, quantite_casiers FROM produits ORDER BY quantite_casiers ASC")
        produits = cursor.fetchall()
        
        # Stock total
        stock_total = sum(p['quantite_casiers'] for p in produits)
        
        # Alertes (produits avec stock <= 30)
        alertes = [p for p in produits if p['quantite_casiers'] <= 30]
        
        # Flux financiers
        cursor.execute("SELECT type_mouvement, quantite, prix_unitaire FROM ventes")
        mouvements = cursor.fetchall()
        
        t_entrees, v_entrees, t_sorties, v_sorties = 0, 0, 0, 0
        for m in mouvements:
            q = float(m['quantite'] or 0)
            p = float(m['prix_unitaire'] or 0)
            if "entree" in str(m['type_mouvement']).lower():
                t_entrees += q
                v_entrees += (q * p)
            else:
                t_sorties += q
                v_sorties += (q * p)
        
        # Nombre de clients
        cursor.execute("SELECT COUNT(DISTINCT client) as nb FROM ventes WHERE client != 'Anonyme'")
        total_clients = cursor.fetchone()
        nb_clients = total_clients['nb'] if total_clients else 0
        
        return render_template('dashboard.html', 
                             produits=produits,
                             clients=nb_clients, 
                             stock_total=stock_total, 
                             alertes=alertes, 
                             t_entrees=t_entrees, 
                             v_entrees=v_entrees, 
                             t_sorties=t_sorties, 
                             v_sorties=v_sorties)
    except Exception as e:
        return f"Erreur Dashboard : {str(e)}"
    finally:
        if conn: conn.close()

@app.route('/form')
def form():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT nom_produit FROM produits ORDER BY nom_produit")
    produits = cursor.fetchall()
    conn.close()
    return render_template('form.html', produits=produits)

@app.route('/stats')
def stats():
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        plt.rcParams.update({
            "figure.facecolor": "#1e293b", "axes.facecolor": "#1e293b",
            "text.color": "#f8fafc", "axes.labelcolor": "#f8fafc",
            "xtick.color": "#94a3b8", "ytick.color": "#94a3b8"
        })
        
        # Graphique camembert
        cursor.execute("SELECT nom_produit, quantite_casiers FROM produits WHERE quantite_casiers > 0")
        stocks = cursor.fetchall()
        
        pies = []
        if stocks:
            noms = [s['nom_produit'] for s in stocks]
            qtes = [float(s['quantite_casiers']) for s in stocks]
            
            fig1, ax1 = plt.subplots(figsize=(7, 7))
            ax1.pie(qtes, labels=noms, autopct='%1.1f%%', startangle=140)
            ax1.set_title("RÉPARTITION ACTUELLE DU STOCK", color="#38bdf8", fontweight='bold')
            pies.append(fig_to_b64(fig1))
        
        # Graphique ventes par mois
        cursor.execute("""
            SELECT MONTH(date_vente) as mois, SUM(quantite) as total 
            FROM ventes 
            WHERE LOWER(type_mouvement) = 'sortie'
            GROUP BY MONTH(date_vente)
            ORDER BY mois
        """)
        ventes_mois = cursor.fetchall()
        
        bars = []
        if ventes_mois:
            mois_noms = ["Jan", "Fév", "Mar", "Avr", "Mai", "Juin", "Juil", "Août", "Sept", "Oct", "Nov", "Déc"]
            labels = [mois_noms[int(v['mois'])-1] for v in ventes_mois]
            volumes = [float(v['total']) for v in ventes_mois]
            
            fig2, ax2 = plt.subplots(figsize=(10, 5))
            ax2.bar(labels, volumes, color='#10b981', alpha=0.8)
            ax2.set_title("ÉVOLUTION DES VENTES MENSUELLES", color="#10b981", fontweight='bold')
            ax2.set_ylabel("Quantité Vendue (Casiers)")
            bars.append(fig_to_b64(fig2))
        
        return render_template('stats.html', pies=pies, bars=bars)
    except Exception as e:
        return f"Erreur stats : {str(e)}"
    finally:
        if conn: conn.close()

@app.route('/analyse')
def analyse():
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT nom_produit, quantite_casiers FROM produits")
        stocks_db = cursor.fetchall()

        analyse_produits = []
        CAPACITE_MAX = 100

        for row in stocks_db:
            qte = int(row['quantite_casiers'])
            nom = row['nom_produit']
            
            pourcentage = max(0, min((qte / CAPACITE_MAX) * 100, 100))
            
            if qte <= 30: 
                statut = "CRITIQUE"
                conseil = "RÉAPPROVISIONNEMENT URGENT !"
            elif qte <= 60: 
                statut = "ALERTE"
                conseil = f"Surveiller les ventes. Encore {abs(qte - 30)} ventes avant critique."
            else:
                statut = "SAIN"
                conseil = f"Stock optimal. Encore {qte - 60} ventes possibles avant l'alerte."
            
            analyse_produits.append({
                'nom': nom, 
                'qte': qte, 
                'statut': statut, 
                'pct': pourcentage, 
                'conseil': conseil
            })

        return render_template('analyse.html', produits=analyse_produits)
    except Exception as e:
        return f"Erreur analyse : {str(e)}"
    finally:
        if conn: conn.close()

@app.route('/affichage')
def affichage():
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM ventes ORDER BY date_vente DESC, id DESC LIMIT 100")
        ventes = cursor.fetchall()
        
        ca_total = sum(float(v['quantite'] or 0) * float(v['prix_unitaire'] or 0) 
                      for v in ventes if "sortie" in str(v['type_mouvement']).lower())
        
        return render_template('affichage.html', ventes=ventes, ca_total=ca_total)
    except Exception as e:
        return f"Erreur affichage : {str(e)}"
    finally:
        if conn: conn.close()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)== '__main__':
    app.run(debug=False)
