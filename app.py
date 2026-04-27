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

# ==========================================
# 2. ROUTES DE TRAITEMENT
# ==========================================

@app.route('/ajouter', methods=['POST'])
def ajouter():
    conn = None
    try:
        nom_produit = request.form.get('produit').strip()
        client = request.form.get('client', 'Anonyme').strip()
        telephone = request.form.get('telephone', 'N/A').strip()
        date_vente = request.form.get('date_vente')
        mouv_raw = request.form.get('type_mouvement', '').lower()
        cat_fixe = request.form.get('type_categorie')
        
        try:
            qte = int(request.form.get('qte', 0))
            prix = float(request.form.get('pu', 0))
        except:
            qte, prix = 0, 0

        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # 1. Récupérer le niveau actuel du stock
        cursor.execute("SELECT quantite_casiers FROM produits WHERE nom_produit = %s", (nom_produit,))
        result = cursor.fetchone()
        
        if not result:
            cursor.execute("INSERT INTO produits (nom_produit, quantite_casiers) VALUES (%s, 100)", (nom_produit,))
            stock_actuel = 100
        else:
            stock_actuel = result['quantite_casiers']

        # 2. Logique Métier
        # --- LOGIQUE ENTRÉE (On ne touche pas, reste comme tu l'as voulu) ---
        if "entree" in mouv_raw:
            if stock_actuel <= 35:
                cursor.execute("UPDATE produits SET quantite_casiers = quantite_casiers + %s WHERE nom_produit = %s", (qte, nom_produit))
            else:
                return f"Refusé : Le stock de {nom_produit} est suffisant ({stock_actuel} > 35).", 400
        
        # --- LOGIQUE VENTE (Modifiée pour toujours diminuer) ---
        elif "sortie" in mouv_raw or "vente" in mouv_raw:
            # On soustrait directement la quantité vendue
            # GREATEST(0, ...) évite que le stock devienne négatif si on vend plus que prévu
            cursor.execute("""
                UPDATE produits 
                SET quantite_casiers = GREATEST(0, quantite_casiers - %s) 
                WHERE nom_produit = %s
            """, (qte, nom_produit))

        # 3. Journalisation de l'opération
        cursor.execute("""
            INSERT INTO ventes (produit, client, telephone, quantite, prix_unitaire, type_mouvement, type_categorie, date_vente)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (nom_produit, client, telephone, qte, prix, mouv_raw, cat_fixe, date_vente))
        
        conn.commit()
        return redirect(url_for('affichage'))

    except Exception as e:
        if conn: conn.rollback()
        return f"Erreur : {str(e)}", 500
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
        
        # Stock Total Dynamique : Somme des 10 produits (ex: 100x10 = 1000 au début)
        cursor.execute("SELECT SUM(quantite_casiers) as total FROM produits")
        res_stock = cursor.fetchone()
        stock_total = res_stock['total'] if res_stock and res_stock['total'] else 0

        # Alertes : Produits sous le seuil de 35
        cursor.execute("SELECT nom_produit as produit, quantite_casiers as reste FROM produits WHERE quantite_casiers <= 35 ORDER BY quantite_casiers ASC")
        alertes_list = cursor.fetchall()

        # Flux financiers et volumes
        cursor.execute("SELECT type_mouvement, quantite, prix_unitaire FROM ventes")
        mouvements = cursor.fetchall()

        t_entrees, v_entrees, t_sorties, v_sorties = 0, 0, 0, 0
        for m in mouvements:
            q, p = float(m['quantite'] or 0), float(m['prix_unitaire'] or 0)
            mouv = str(m['type_mouvement']).lower()
            if "entree" in mouv:
                t_entrees += q
                v_entrees += (q * p)
            elif "sortie" in mouv:
                t_sorties += q
                v_sorties += (q * p)

        cursor.execute("SELECT COUNT(DISTINCT client) as nb FROM ventes WHERE client != 'Anonyme'")
        total_clients = cursor.fetchone()['nb'] or 0

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

@app.route('/stats')
def stats():
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # Style sombre pour le dashboard
        plt.rcParams.update({
            "figure.facecolor": "#1e293b", "axes.facecolor": "#1e293b", 
            "text.color": "#f8fafc", "axes.labelcolor": "#f8fafc", 
            "xtick.color": "#94a3b8", "ytick.color": "#94a3b8"
        })

        # --- 1. DONNÉES : Stock Actuel (Camembert) ---
        cursor.execute("SELECT nom_produit, quantite_casiers FROM produits")
        res_s = cursor.fetchall()
        noms_s = [r['nom_produit'] for r in res_s]
        qtes_s = [float(r['quantite_casiers']) for r in res_s]

        # --- 2. DONNÉES : Ventes par Mois (Barres) ---
        # On extrait le mois et on somme les quantités de 'sortie'
        query_mois = """
            SELECT MONTH(date_vente) as mois, SUM(quantite) as total 
            FROM ventes 
            WHERE LOWER(type_mouvement) = 'sortie' 
            GROUP BY MONTH(date_vente)
            ORDER BY MONTH(date_vente)
        """
        cursor.execute(query_mois)
        res_m = cursor.fetchall()
        
        # Mapping des noms de mois en français
        mois_noms = ["Jan", "Fév", "Mar", "Avr", "Mai", "Juin", "Juil", "Août", "Sept", "Oct", "Nov", "Déc"]
        labels_m = [mois_noms[int(r['mois'])-1] for r in res_m]
        volumes_m = [float(r['total']) for r in res_m]

        pies = []
        bars = []

        # Génération du Camembert (Stocks)
        if qtes_s:
            fig1, ax1 = plt.subplots(figsize=(7, 7))
            ax1.pie(qtes_s, labels=noms_s, autopct='%1.1f%%', startangle=140, colors=['#38bdf8','#10b981','#f43f5e','#fbbf24'])
            ax1.set_title("RÉPARTITION ACTUELLE DU STOCK", color="#38bdf8", fontweight='bold')
            pies.append(fig_to_b64(fig1))

        # Génération de l'Histogramme (Ventes par mois)
        if volumes_m:
            fig2, ax2 = plt.subplots(figsize=(10, 5))
            ax2.bar(labels_m, volumes_m, color='#10b981', alpha=0.8)
            ax2.set_title("ÉVOLUTION DES VENTES MENSUELLES", color="#10b981", fontweight='bold')
            ax2.set_ylabel("Quantité Vendue (Casiers)")
            bars.append(fig_to_b64(fig2))

        return render_template('stats.html', pies=pies, bars=bars)
    except Exception as e:
        return f"Erreur stats : {str(e)}"
    finally:
        if conn: conn.close()
            
@app.route('/form')
def form():
    return render_template('form.html')

@app.route('/analyse')
def analyse():
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT nom_produit, quantite_casiers FROM produits")
        stocks_db = cursor.fetchall()

        analyse_produits = []
        for row in stocks_db:
            qte, nom = int(row['quantite_casiers']), row['nom_produit']
            statut = "SAIN"
            conseil = "Stock optimal."
            if qte <= 30: 
                statut = "CRITIQUE"
                conseil = "RÉAPPROVISIONNEMENT URGENT !"
            elif qte <= 60: 
                statut = "ALERTE"
                conseil = "Surveiller les ventes."
            
            analyse_produits.append({'nom': nom, 'qte': qte, 'statut': statut, 'pct': min(qte, 100), 'conseil': conseil})

        return render_template('analyse.html', produits=analyse_produits)
    except Exception as e:
        return f"Erreur analyse : {str(e)}"
    finally:
        if conn: conn.close()

if __name__ == '__main__':
    app.run(debug=False)
