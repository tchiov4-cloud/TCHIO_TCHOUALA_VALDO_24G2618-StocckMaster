from flask import Flask, render_template, request, redirect, url_for
import mysql.connector
import matplotlib
matplotlib.use('Agg') 
import matplotlib.pyplot as plt
import io, base64
import os 
from collections import defaultdict

# ==========================================
# 1. INITIALISATION
# ==========================================
app = Flask(__name__)

def get_db():
    # Correction : Utilisation systématique de ssl_verify_cert=False pour Render/TiDB
    # et vérification de la variable DB_NAME
    return mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASS'),
        port=int(os.getenv('DB_PORT', 4000)),
        database=os.getenv('DB_NAME', 'valdo_stock'),
        ssl_disabled=False,
        ssl_verify_cert=False
    )

def fig_to_b64(fig):
    img = io.BytesIO()
    fig.savefig(img, format='png', bbox_inches='tight', dpi=110, facecolor=fig.get_facecolor())
    plt.close(fig)
    return base64.b64encode(img.getvalue()).decode()

# ==========================================
# 2. ROUTES DE TRAITEMENT
# ==========================================

@app.route('/ajouter', methods=['POST'])
def ajouter():
    conn = None
    try:
        # On harmonise la récupération du nom du produit
        nom_produit = request.form.get('produit') or request.form.get('type_categorie')
        if nom_produit:
            nom_produit = nom_produit.strip()

        client = request.form.get('client', 'Anonyme').strip()
        telephone = request.form.get('telephone', 'N/A').strip()
        
        try:
            qte = int(request.form.get('qte', 0))
            prix = float(request.form.get('pu', 0))
        except:
            qte, prix = 0, 0
            
        mouv = request.form.get('type_mouvement', '').lower()
        cat_fixe = request.form.get('type_categorie') 
        date_vente = request.form.get('date_vente')

        if not nom_produit:
            return "Erreur : Le nom du produit est vide.", 400

        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # IMPORTANT : On utilise 'produits' comme nom de table ici
        cursor.execute("INSERT IGNORE INTO produits (nom_produit, quantite_casiers) VALUES (%s, 100)", (nom_produit,))
        
        if "entree" in mouv:
            cursor.execute("UPDATE produits SET quantite_casiers = quantite_casiers + %s WHERE nom_produit = %s", (qte, nom_produit))
        elif "sortie" in mouv:
            cursor.execute("UPDATE produits SET quantite_casiers = GREATEST(0, quantite_casiers - %s) WHERE nom_produit = %s", (qte, nom_produit))

        cursor.execute("""
            INSERT INTO ventes (produit, client, telephone, quantite, prix_unitaire, type_mouvement, type_categorie, date_vente)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (nom_produit, client, telephone, qte, prix, mouv, cat_fixe, date_vente))
        
        conn.commit()
        return redirect(url_for('affichage'))

    except Exception as e:
        if conn: conn.rollback()
        return f"Erreur système : {str(e)}", 500
    finally:
        if conn: conn.close()

# ==========================================
# 3. ROUTES D'AFFICHAGE
# ==========================================

@app.route('/')
def index():
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 1. Clients
        cursor.execute("SELECT COUNT(DISTINCT client) as nb FROM ventes WHERE client != 'Anonyme'")
        total_clients = cursor.fetchone()['nb'] or 0

        # 2. Stock Total
        cursor.execute("SELECT SUM(quantite_casiers) as total FROM produits")
        res_stock = cursor.fetchone()
        stock_total = res_stock['total'] if res_stock and res_stock['total'] else 0

        # 3. Alertes (Table 'produits')
        cursor.execute("SELECT nom_produit as produit, quantite_casiers as reste FROM produits WHERE quantite_casiers <= 35 ORDER BY quantite_casiers ASC")
        alertes_list = cursor.fetchall()

        # 4. Flux
        cursor.execute("SELECT type_mouvement, quantite, prix_unitaire FROM ventes")
        mouvements = cursor.fetchall()

        t_entrees, v_entrees, t_sorties, v_sorties = 0, 0, 0, 0
        for m in mouvements:
            q = float(m['quantite'] or 0)
            p = float(m['prix_unitaire'] or 0)
            mouv = str(m['type_mouvement']).lower()
            if "entree" in mouv:
                t_entrees += q
                v_entrees += (q * p)
            elif "sortie" in mouv:
                t_sorties += q
                v_sorties += (q * p)

        return render_template('dashboard.html', clients=total_clients, stock_total=stock_total, 
                               alertes=alertes_list, t_entrees=t_entrees, v_entrees=v_entrees, 
                               t_sorties=t_sorties, v_sorties=v_sorties)
    except Exception as e:
        return f"Erreur Dashboard : {str(e)}"
    finally:
        if conn: conn.close()

@app.route('/affichage')
def affichage():
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM ventes ORDER BY id DESC")
        ventes = cursor.fetchall()
        
        ca_total = sum(float(v['quantite'] or 0) * float(v['prix_unitaire'] or 0) for v in ventes if "sortie" in str(v['type_mouvement']).lower())
        
        lignes_html = ""
        for v in ventes:
            q, p = float(v['quantite'] or 0), float(v['prix_unitaire'] or 0)
            col = "#22c55e" if "entree" in str(v['type_mouvement']).lower() else "#f43f5e"
            lignes_html += f"<tr><td>{v['date_vente']}</td><td><b>{v['produit']}</b></td><td>{v['client']}</td><td>{v['telephone']}</td><td>{v['type_categorie']}</td><td>{q}</td><td>{p:,.0f}</td><td style='color:{col}; font-weight:bold;'>{v['type_mouvement']}</td><td style='font-weight:bold;'>{q*p:,.0f} FCFA</td></tr>"
        
        with open("templates/stock.html", "r", encoding="utf-8") as f:
            html = f.read()
        return html.replace("__LIGNES__", lignes_html).replace("__CA__", f"{ca_total:,.0f}")
    except Exception as e:
        return f"Erreur affichage : {str(e)}"
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
        conseils_list = []
        for row in stocks_db:
            qte, nom = int(row['quantite_casiers']), row['nom_produit']
            statut = "SAIN"
            if qte <= 30: 
                statut = "CRITIQUE"
                conseils_list.append(f"Achat urgent : {nom}")
            elif qte <= 60: statut = "ALERTE"
            analyse_produits.append({'nom': nom, 'qte': qte, 'statut': statut, 'pct': min(qte, 100)})

        return render_template('analyse.html', produits=analyse_produits, conseils=conseils_list)
    except Exception as e:
        return f"Erreur analyse : {str(e)}"
    finally:
        if conn: conn.close()

@app.route('/stats')
def stats():
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        plt.rcParams.update({"figure.facecolor": "#1e293b", "axes.facecolor": "#1e293b", "text.color": "#f8fafc", "axes.labelcolor": "#f8fafc", "xtick.color": "#94a3b8", "ytick.color": "#94a3b8"})

        cursor.execute("SELECT nom_produit, quantite_casiers FROM produits")
        cat_stock = {r['nom_produit']: float(r['quantite_casiers']) for r in cursor.fetchall()}

        cursor.execute("SELECT produit, SUM(quantite) as total FROM ventes WHERE LOWER(type_mouvement)='sortie' GROUP BY produit")
        cat_vente = {r['produit']: float(r['total']) for r in cursor.fetchall()}

        pies = []
        for data, title in [(cat_vente, "VENTES PAR PRODUIT"), (cat_stock, "STOCK ACTUEL")]:
            fig, ax = plt.subplots(figsize=(8, 8))
            clean = {k: v for k, v in data.items() if v > 0}
            if clean:
                ax.pie(clean.values(), labels=clean.keys(), autopct='%1.1f%%', startangle=140, colors=['#38bdf8','#10b981','#f43f5e','#fbbf24'])
            ax.set_title(title, color="#38bdf8", fontweight='bold', pad=20)
            pies.append(fig_to_b64(fig))

        return render_template('stats.html', pies=pies, bars=[])
    except Exception as e:
        return f"Erreur stats : {str(e)}"
    finally:
        if conn: conn.close()

@app.route('/form')
def form():
    return render_template('form.html')

if __name__ == '__main__':
    app.run(debug=False)
