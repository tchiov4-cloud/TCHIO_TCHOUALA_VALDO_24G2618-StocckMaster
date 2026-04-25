from flask import Flask, render_template, request, redirect, url_for
import mysql.connector
import matplotlib
matplotlib.use('Agg') # Pour éviter les erreurs de threads avec Flask
import matplotlib.pyplot as plt
import io, base64
import os 
from collections import defaultdict

# ==========================================
# 1. INITIALISATION (OBLIGATOIRE AU DÉBUT)
# ==========================================
app = Flask(__name__)



def get_db():
    return mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASS'),
        port=int(os.getenv('DB_PORT', 4000)),
        database=os.getenv('DB_NAME', 'test'),
        # TRÈS IMPORTANT pour TiDB sur Render :
        ssl_disabled=False,
        ssl_verify_cert=False
    )

def fig_to_b64(fig):
    """Convertit un graphique Matplotlib en chaîne Base64 pour HTML"""
    img = io.BytesIO()
    fig.savefig(img, format='png', bbox_inches='tight', dpi=110, facecolor=fig.get_facecolor())
    plt.close(fig)
    return base64.b64encode(img.getvalue()).decode()

# ==========================================
# 2. ROUTES DE TRAITEMENT (LOGIQUE)
# ==========================================

@app.route('/ajouter', methods=['POST'])
def ajouter():
    conn = None
    try:
        # On essaie de récupérer par 'produit' OU par 'type_categorie' 
        # car ton HTML utilise 'type_categorie' pour le nom de la boisson
        nom_produit = request.form.get('produit') or request.form.get('type_categorie')
        
        # On nettoie le nom s'il existe
        if nom_produit:
            nom_produit = nom_produit.strip()

        client = request.form.get('client', 'Anonyme').strip()
        telephone = request.form.get('telephone', 'N/A').strip()
        
        # Récupération de la quantité et du prix
        try:
            qte = int(request.form.get('qte', 0))
            prix = float(request.form.get('pu', 0))
        except:
            qte, prix = 0, 0
            
        mouv = request.form.get('type_mouvement', '').lower()
        # On récupère la catégorie (alcoolisé ou non)
        cat_fixe = request.form.get('type_categorie') 
        date_vente = request.form.get('date_vente')

        # --- LE FIX CRITIQUE ---
        # Si nom_produit est toujours vide ici, on prend la valeur brute du formulaire
        if not nom_produit:
             # On affiche tout dans le terminal pour debugger si ça échoue encore
            print("DONNÉES REÇUES :", request.form)
            return "Erreur : Le nom du produit est vide. Vérifiez la sélection.", 400

        if qte <= 0:
            return f"Erreur : La quantité ({qte}) doit être supérieure à 0.", 400

        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # 1. On s'assure que le produit existe en stock
        cursor.execute("INSERT IGNORE INTO stocks_produits (nom_produit, quantite_casiers) VALUES (%s, 100)", (nom_produit,))
        
        # 2. Mise à jour du stock
        if "entree" in mouv:
            cursor.execute("UPDATE stocks_produits SET quantite_casiers = quantite_casiers + %s WHERE nom_produit = %s", (qte, nom_produit))
        elif "sortie" in mouv:
            cursor.execute("UPDATE stocks_produits SET quantite_casiers = GREATEST(0, quantite_casiers - %s) WHERE nom_produit = %s", (qte, nom_produit))

        # 3. Enregistrement historique
        cursor.execute("""
            INSERT INTO ventes (produit, client, telephone, quantite, prix_unitaire, type_mouvement, type_categorie, date_vente)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (nom_produit, client, telephone, qte, prix, mouv, cat_fixe, date_vente))
        
        conn.commit()
        return redirect(url_for('affichage'))

    except Exception as e:
        if conn: conn.rollback()
        print(f"ERREUR : {str(e)}")
        return f"Erreur système : {str(e)}", 500
    finally:
        if conn: conn.close()


# ==========================================
# 3. ROUTES D'AFFICHAGE (PAGES)
# ==========================================

@app.route('/')
@app.route('/form', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        return ajouter()
    return render_template("form.html")

@app.route('/affichage')
def affichage():
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM ventes ORDER BY id DESC")
        ventes = cursor.fetchall()
        
        # On calcule le CA ici pour le passer au template
        ca_total = sum(float(v['quantite'] or 0) * float(v['prix_unitaire'] or 0) for v in ventes)
        
        # On génère les lignes HTML pour stock.html
        lignes_html = ""
        for v in ventes:
            q = float(v['quantite'] or 0)
            p = float(v['prix_unitaire'] or 0)
            col = "#22c55e" if "entree" in str(v['type_mouvement']).lower() else "#f43f5e"
            lignes_html += f"""
                <tr>
                    <td>{v['date_vente']}</td>
                    <td><b>{v['produit']}</b></td>
                    <td>{v['client']}</td>
                    <td>{v['telephone']}</td>
                    <td>{v['type_categorie']}</td>
                    <td>{q}</td>
                    <td>{p:,.0f}</td>
                    <td style="color:{col}; font-weight:bold;">{v['type_mouvement']}</td>
                    <td style="font-weight:bold;">{q*p:,.0f} FCFA</td>
                </tr>
            """
        
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
        cursor.execute("SELECT nom_produit, quantite_casiers FROM stocks_produits")
        stocks_db = cursor.fetchall()

        analyse_produits = []
        conseils_list = []

        for row in stocks_db:
            qte = int(row['quantite_casiers'])
            nom = row['nom_produit']
            statut = "SAIN"
            if qte <= 30: statut = "CRITIQUE"; conseils_list.append(f"Achat urgent : {nom}")
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
        
        # --- CONFIGURATION STYLE SOMBRE ---
        plt.rcParams.update({
            "figure.facecolor": "#1e293b", "axes.facecolor": "#1e293b",
            "text.color": "#f8fafc", "axes.labelcolor": "#f8fafc",
            "xtick.color": "#94a3b8", "ytick.color": "#94a3b8",
            "font.family": "sans-serif"
        })

        # 1. DONNÉES POUR LES CAMEMBERTS
        cursor.execute("SELECT nom_produit, quantite_casiers FROM stocks_produits")
        cat_stock = {r['nom_produit']: float(r['quantite_casiers']) for r in cursor.fetchall()}

        cursor.execute("SELECT produit, SUM(quantite) as total FROM ventes WHERE LOWER(type_mouvement)='sortie' GROUP BY produit")
        cat_vente = {r['produit']: float(r['total']) for r in cursor.fetchall()}

        # 2. DONNÉES POUR LES BARRES : VENTES PAR MOIS
        cursor.execute("""
            SELECT DATE_FORMAT(date_vente, '%Y-%m') as mois, SUM(quantite) as total 
            FROM ventes WHERE LOWER(type_mouvement)='sortie' 
            GROUP BY mois ORDER BY mois ASC
        """)
        v_mois_data = cursor.fetchall()
        mois_labels = [r['mois'] for r in v_mois_data]
        mois_valeurs = [float(r['total']) for r in v_mois_data]

        # 3. DONNÉES POUR LE PRODUIT LE PLUS VENDU PAR MOIS (Requête corrigée)
        cursor.execute("""
            SELECT mois, produit, total_qte FROM (
                SELECT DATE_FORMAT(date_vente, '%Y-%m') as mois, produit, SUM(quantite) as total_qte,
                RANK() OVER (PARTITION BY DATE_FORMAT(date_vente, '%Y-%m') ORDER BY SUM(quantite) DESC) as rang
                FROM ventes WHERE LOWER(type_mouvement)='sortie'
                GROUP BY mois, produit
            ) t WHERE rang = 1
        """)
        top_prod_data = cursor.fetchall()
        top_labels = [f"{r['mois']}\n{r['produit']}" for r in top_prod_data]
        top_valeurs = [float(r['total_qte']) for r in top_prod_data]

        # --- GÉNÉRATION DES GRAPHIQUES ---
        pies = []
        for data, title in [(cat_vente, "VENTES PAR PRODUIT"), (cat_stock, "STOCK ACTUEL")]:
            fig, ax = plt.subplots(figsize=(8, 8))
            clean = {k: v for k, v in data.items() if v > 0}
            if clean:
                ax.pie(clean.values(), labels=clean.keys(), autopct='%1.1f%%', startangle=140, 
                       colors=['#38bdf8','#10b981','#f43f5e','#fbbf24', '#8b5cf6'])
            ax.set_title(title, color="#38bdf8", fontweight='bold', pad=20)
            pies.append(fig_to_b64(fig))

        bars = []
        # Barres 1 : Ventes par mois
        if mois_labels:
            fig1, ax1 = plt.subplots(figsize=(10, 6))
            ax1.bar(mois_labels, mois_valeurs, color='#38bdf8', alpha=0.8)
            ax1.set_title("VOLUME TOTAL DES VENTES PAR MOIS", color="#38bdf8", fontweight='bold', pad=20)
            plt.xticks(rotation=0)
            bars.append(fig_to_b64(fig1))

        # Barres 2 : Top produit par mois
        if top_labels:
            fig2, ax2 = plt.subplots(figsize=(10, 6))
            ax2.bar(top_labels, top_valeurs, color='#10b981', alpha=0.8)
            ax2.set_title("PRODUIT LE PLUS VENDU PAR MOIS", color="#10b981", fontweight='bold', pad=20)
            plt.xticks(rotation=0)
            bars.append(fig_to_b64(fig2))

        return render_template('stats.html', pies=pies, bars=bars)

    except Exception as e:
        # On affiche l'erreur SQL précise si ça échoue encore
        import traceback
        print(traceback.format_exc())
        return f"Erreur stats : {str(e)}"
    finally:
        if conn: conn.close()


@app.route('/dashboard')
def dashboard():
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        
        # 1. Nombre de clients totaux
       # On compte les noms de clients différents (DISTINCT) dans la table ventes
        cursor.execute("SELECT COUNT(DISTINCT client) as nb FROM ventes WHERE client != 'Anonyme'")
        res_clients = cursor.fetchone()
        total_clients = res_clients['nb'] if res_clients else 0

        # 2. Récupération du STOCK TOTAL (Tous les produits cumulés)
        cursor.execute("SELECT SUM(quantite_casiers) as total FROM stocks_produits")
        res_stock = cursor.fetchone()
        stock_total = res_stock['total'] if res_stock['total'] else 0

        # 3. FILTRAGE DES ALERTES (Seulement <= 35 casiers)
        # On les trie du plus petit au plus grand pour voir l'urgence en premier
        cursor.execute("""
            SELECT nom_produit as produit, quantite_casiers as reste 
            FROM stocks_produits 
            WHERE quantite_casiers <= 35
            ORDER BY quantite_casiers ASC
        """)
        alertes_list = cursor.fetchall()

        # 4. Calcul des flux (Entrées / Sorties) depuis la table ventes
        cursor.execute("SELECT type_mouvement, quantite, prix_unitaire FROM ventes")
        mouvements = cursor.fetchall()

        t_entrees = 0
        v_entrees = 0
        t_sorties = 0
        v_sorties = 0

        for m in mouvements:
            qte = float(m['quantite'] or 0)
            pu = float(m['prix_unitaire'] or 0)
            mouv = str(m['type_mouvement']).lower()

            if "entree" in mouv:
                t_entrees += qte
                v_entrees += (qte * pu)
            elif "sortie" in mouv:
                t_sorties += qte
                v_sorties += (qte * pu)

        return render_template('dashboard.html', 
                               clients=total_clients,
                               stock_total=stock_total,
                               alertes=alertes_list, # Contient uniquement les produits en alerte
                               t_entrees=t_entrees,
                               v_entrees=v_entrees,
                               t_sorties=t_sorties,
                               v_sorties=v_sorties)

    except Exception as e:
        return f"Erreur Dashboard : {str(e)}"
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    app.run()
