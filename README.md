# Collecte crypto

Lancement PowerShell :

    cd C:\Users\Administrateur\zeeschuimer-agent
    $env:OPENROUTER_API_KEY = "votre-cle"
    python agent.py --minutes 5

Vérifier la connexion et Zeeschuimer, puis Entrée. Exporter Zeeschuimer avant de fermer Firefox.

- relevant_24h.jsonl : sélection encore âgée de moins de 24 h au dernier export.
- collection.sqlite3 : historique persistant, notes, posts rejetés et lots en attente.
- last_summary.json : bilan de la dernière session.
- audit.jsonl : décisions et erreurs horodatées en UTC.

Les posts retenus doivent avoir crypto >= 3 et quality >= 3 et appartenir à news, analysis ou education. Promotions et humour restent dans la base avec leurs notes.

Options : --batch-size 12 (1 à 20), --decision-every 4, --max-llm-calls 60, --max-navigations 25.
Le budget LLM compte les opérations de notation/décision ; les tentatives internes de réparation peuvent multiplier les requêtes HTTP. La durée est vérifiée entre opérations et peut être dépassée par une requête en cours.

Chaque pas de défilement est suivi d'une observation. Les changements de cible sont normalement décidés après quatre observations, ou trois sans nouveau post admissible. Le navigateur défile d'une fraction de fenêtre ; aucune technique de dissimulation n'est ajoutée.

Le texte lu est le texte disponible dans le DOM, jusqu'à 6000 caractères. Les publications masquées derrière « voir plus » et les médias ne sont pas intégralement analysés. Les recherches sont récentes et chaque date est contrôlée localement. Une date absente/invalide/future est rejetée.

Au premier lancement du nouveau moteur, l'ancien export est sauvegardé avant reconstruction depuis SQLite. Les anciens exports ne sont pas importés dans la base : le dédoublonnage entre sessions commence avec ce moteur. Ne supprimez pas SQLite si vous souhaitez conserver cette mémoire.

Le fichier de sélection est reconstruit sans doublons depuis SQLite ; les publications devenues anciennes disparaissent lors du prochain export, mais restent dans la base. Zeeschuimer continue de collecter indépendamment et n'est pas filtré.

Vérifications hors ligne : python -m unittest discover -p test_collection.py -v

## Sources prioritaires et Discord

sources.json contient les 50 pseudos transcrits des captures, sans vérification externe de leur identité. Les deux comptes protégés sont désactivés. Les 48 autres sont recherchés par groupes de cinq avant exploration générale. Un compte prioritaire n'est pas une source officielle par défaut.

Les publications de qualité >=4, classées news ou analysis, alimentent une file persistante. alerts_preview.jsonl affiche les alertes en attente sans envoyer de message. Le dédoublonnage couvre les identifiants et le texte identique normalisé ; il ne regroupe pas encore toutes les reformulations du même événement. Les alertes peuvent contenir des opinions et informations non vérifiées : aucun lien de causalité avec les prix n'est confirmé.

Pour autoriser les envois dans votre canal Discord, définir localement :

    $env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/.../..."
    python agent.py --minutes 5 --discord

Ne jamais partager l'URL du webhook. En son absence, lancer sans --discord pour consulter seulement l'aperçu. Un timeout après acceptation par Discord peut exceptionnellement entraîner un doublon lors de la reprise.

Cette version surveille les publications X ; la surveillance indépendante des cours, les flux officiels des banques centrales, le fonctionnement continu et le bot de questions/réponses restent à intégrer. Les quotas et les interventions humaines restent actifs.

## Prototype de classification humaine

Aucune dépendance supplémentaire. Le modèle expérimental est un Naive Bayes multinomial sur le texte, sans pseudo, followers ou score LLM. Il prédit une catégorie de contenu ; il ne vérifie pas les faits et ne note pas la crédibilité d'une personne. Les performances multilingues et le style promotionnel doivent être contrôlés.

    python classifier.py prepare
    python classifier.py annotate
    python classifier.py train

prepare extrait tous les textes de SQLite, y compris les rejets, en préservant les annotations existantes. annotate masque les suggestions LLM et enregistre chaque correction. Choix :
1 sourced_information : information avec source identifiable (lien justificatif requis ; pas nécessairement vérifiée).
2 reasoned_analysis : raisonnement explicite, éléments à l'appui.
3 opinion_prediction : opinion/prédiction peu étayée.
4 promotion : vente, affiliation, mise en avant commerciale.
5 unsubstantiated_claim : affirmation factuelle sans preuve accessible (pas synonyme de mensonge).
Passer avec s en cas de texte incomplet, ambigu ou langue inconnue ; quitter avec q.

Minimum expérimental : 100 textes uniques, 10 de chaque catégorie, et plusieurs auteurs par catégorie. Viser 300–500 annotations diversifiées. Le découpage garde des auteurs distincts pour le test et retire les doublons textuels exacts. Chaque classe doit être présente en entraînement et en test. Le rapport affiche macro-F1, matrice de confusion et comparaison à la classe majoritaire. Le modèle sauvegardé est celui évalué, sans réentraînement caché sur les exemples de test. Un test temporel et une revue des erreurs sont encore nécessaires avant déploiement.

    python classifier.py predict "Texte d'une publication"

Fichiers locaux : annotations.jsonl, content_model.json (après entraînement), training_report.json. Les annotations et données privées sont exclues du ZIP. Le modèle n'est pas connecté aux alertes et aucun score de profil automatique n'est activé.

## Capture volumique puis analyse

    python capture.py --minutes 5 --target-posts 500

Pas de clé OpenRouter ni appel LLM pendant cette collecte. L'objectif compte les nouveaux textes uniques de moins de 24 h insérés dans SQLite, pas les posts déjà stockés ni les posts retenus. Le nombre n'est pas garanti : le débit dépend de X, du chargement et du volume disponible. Le cache DOM conserve les articles déjà chargés même s'ils disparaissent ensuite de l'écran ; il ne voit pas les posts jamais chargés. La sauvegarde utilise une transaction par observation. Les recherches alternent comptes prioritaires et découverte. Les dates et arrêts sur blocage restent actifs. Aucun contournement n'est ajouté.

collected_24h.jsonl contient les textes récents de la base avant sélection, y compris les sessions précédentes et les publications éventuellement peu pertinentes. last_capture_summary.json donne le nombre nouvellement enregistré, le débit réel, la durée et si l'objectif est atteint. Le délai peut être dépassé par une navigation déjà en cours ; l'attente manuelle de fermeture est hors mesure.

Puis analyser sans Firefox :

    python analyze_queue.py --max-llm-calls 20

Avec clé OpenRouter définie. Ajouter --discord uniquement si vous souhaitez envoyer les alertes et avez défini DISCORD_WEBHOOK_URL. Le budget compte les opérations logiques, pas les réparations HTTP internes. La file non traitée reste persistante et les posts devenus anciens sont exclus. Le mode agent.py avec analyse immédiate reste disponible.

## Spécialisation Bitcoin (mode de capture par défaut)

    python capture.py --minutes 5 --target-posts 500

Requête large Bitcoin/BTC/#bitcoin/#BTC/$BTC/₿, puis variantes si épuisement. Sans filtre de langue, popularité ou compte ; réponses incluses. Chaque texte est contrôlé localement après normalisation Unicode, et chaque date doit avoir moins de 24 h. Aucun appel LLM. bitcoin_24h.jsonl contient les publications correspondantes encore récentes dans la base. Les mentions sont des indices de pertinence, pas une garantie ; un contenu implicite sans Bitcoin/BTC/₿ ou uniquement dans une image ne sera pas reconnu. Aucun objectif de débit n'est garanti.

Pour collecter pendant toute la durée sans s'arrêter à 500 :

    python capture.py --minutes 5 --target-posts 1000000

L'épuisement des recherches ou un blocage peut toujours arrêter la collecte avant cinq minutes. Les doublons déjà en base ne comptent pas comme nouveaux posts. Pour l'ancien périmètre crypto : --topic all.

## Descendre dans l’historique

La collecte reste dans les résultats récents et descend pour charger les publications plus anciennes, jusqu’à la limite de 24 h. Les doublons déjà stockés ne provoquent plus un changement de recherche. Le passage à une variante attend six observations bloquées en bas de page, sans nouveaux identifiants ni progression, hors chargement visible. Une pause réseau n’est pas une preuve de complétude ; X peut ne pas fournir tout l’historique. L’export bitcoin_24h.jsonl est trié par date de publication décroissante. La durée, l’objectif de nouveaux posts et les arrêts sur restriction restent actifs.

## Comptes prioritaires + Bitcoin

Le mode Bitcoin recherche d'abord les 48 comptes actifs par groupes de cinq, combinés à Bitcoin/BTC et leurs variantes. Les réponses sont incluses. La moitié de la durée est répartie entre les groupes prioritaires ; l'autre est réservée à la découverte générale. Un chargement de navigation lent peut dépasser une tranche ; les groupes manqués sont comptés. La limite globale de temps et les quotas de navigation restent prioritaires : toutes les sources ne sont pas garanties visitées à chaque session.
last_capture_summary.json sépare priority_searches/general_searches et new_from_priority_searches/new_from_general_searches. Ces nombres décrivent l'origine de la recherche, pas une certification de la source ni une mesure exhaustive de ses publications.
