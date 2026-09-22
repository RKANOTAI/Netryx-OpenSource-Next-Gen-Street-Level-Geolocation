# Localiser une photo avec l’imagerie ouverte

## Ce qui fonctionne, et ce qui ne fonctionne pas

Le moteur web utilise **Panoramax par défaut**, sans clé Google : métadonnées
publiques STAC, images de rue ouvertes, CosPlace puis DISK/LightGlue et RANSAC.
Il n’interroge plus obligatoirement Google pour chaque nouvelle recherche.
Le GUI historique `test_super.py` n’est pas migré : utiliser le worker web ou
la commande ci-dessous, pas son ancien téléchargement de tuiles Google.

Il faut une **zone de recherche plausible**. Ce n’est pas une recherche mondiale
à partir d’une photo seule. Une commune publicitaire, un centre de carte flouté
ou le centroïde d’une commune n’est pas l’adresse du bâtiment. Réduire la zone
à partir d’indices indépendants, puis comparer uniquement les extérieurs visibles
depuis la rue. Un jardin arrière peut ne pas être visible dans les panoramas.

## Lancer sur des photos locales

Depuis la racine du dépôt, dans l’environnement disposant des modèles :

```bash
.venv/bin/python -m netryx_web.locate_photo --help

# Coordonnées EXEMPLES : remplacer par le centre de votre propre zone.
.venv/bin/python -m netryx_web.locate_photo \
  /chemin/facade.jpg /chemin/autre-exterieur.jpg \
  --latitude 48.8566 --longitude 2.3522 --radius 300 \
  --reviewed-exterior --work-dir runtime/ma-recherche
```

`--reviewed-exterior` signifie que l’opérateur a **visuellement vérifié toutes
les photos fournies** : ce sont bien des extérieurs. L’approbation est enregistrée
avec le SHA-256 des octets du fichier. Elle ne vaut ni preuve de localisation ni
score de modèle. Ne pas l’utiliser pour les intérieurs. Sans ce drapeau, le
classificateur ImageNet existant reste conservateur et peut rejeter une vraie
façade ; le seuil n’a pas été abaissé arbitrairement.

Utiliser un répertoire distinct par recherche. Le chemin de chaque image doit
être local ; aucune photo n’est envoyée à un fournisseur de modèle. Les requêtes
Panoramax transmettent la zone géographique recherchée, pas la photo requête.

Le formulaire web **Photos** et la commande locale permettent une revue manuelle
explicite des extérieurs. Le flux web **URL d’annonce** reste automatique : si
toutes les photos récupérées sont rejetées, il faut relancer la recherche via le
formulaire Photos avec des fichiers vérifiés, ou remplacer le filtre ImageNet
provisoire par un modèle indoor/outdoor calibré.

## Réglages

- `NETRYX_IMAGERY_PROVIDER=panoramax` : défaut, aucune clé nécessaire.
- `NETRYX_PANORAMAX_IMAGE_SIZE=hd` : panoramas haute résolution, plus lourds mais
  utiles pour les petites façades ; défaut `sd`. Les caches SD et HD sont séparés.
- `NETRYX_PANORAMAX_SEARCH_LIMIT=1000` : plafond de métadonnées par zone ; si
  la réponse atteint ce plafond, réduire la zone plutôt que supposer la couverture complète.
- `NETRYX_PANORAMAX_MAX_IMAGES=1000` : borne des images acceptées par recherche.
  Seuls les hôtes IGN et OSM France sont acceptés par défaut ; les autres hôtes
  sont rejetés et comptés dans l’audit. L’administrateur peut étendre la liste avec
  `NETRYX_PANORAMAX_ALLOWED_HOSTS` après vérification du fournisseur.
- `NETRYX_IMAGERY_PROVIDER=google` : uniquement pour un usage Google autorisé ;
  conserve les contrôles `GOOGLE_STREETVIEW_API_KEY` et
  `NETRYX_GOOGLE_STREETVIEW_AUTHORIZED=true`. Aucune bascule automatique payante.
- `NETRYX_SEARCH_RADIUS_M=300` : rayon web par défaut ; le `--radius` local prime.
- `NETRYX_GLOBAL_TOP_K=20` : candidats distincts par panorama et variante de photo.
- `NETRYX_VERIFY_TOP_PANOS=8` : panoramas vérifiés par photo. Augmenter pour une
  recherche difficile, au prix d’un temps de calcul supérieur.
- `NETRYX_INDEX_HEADING_STEP=45` : pas des vues extraites des panoramas.
- `NETRYX_MIN_INLIERS=8` : seuil minimal, plancher de 8 ; il filtre aussi les
  photos comptées dans le support multi-images. Un faible seuil n’est pas une
  probabilité de bonne localisation.

Pour approfondir un classement peu discriminant :

```bash
NETRYX_GLOBAL_TOP_K=60 NETRYX_VERIFY_TOP_PANOS=40 \
  NETRYX_PANORAMAX_IMAGE_SIZE=hd \
  .venv/bin/python -m netryx_web.locate_photo /chemin/facade.jpg \
  --latitude 48.8566 --longitude 2.3522 --radius 150 \
  --reviewed-exterior --work-dir runtime/recherche-approfondie
```

## Résultats et limites

- `listing-manifest.json` : photos, zone et éventuelle approbation manuelle.
- `exterior-selection.json` : décision pour chaque photo, y compris rejets.
- `pano-crawl.json` : couverture trouvée et sources.
  Les échecs d’acquisition/indexation y sont détaillés, même si aucune image n’est utilisable.
- `search-evidence.json` : candidats CosPlace, résultats géométriques, classement,
  seuil et paramètres de la recherche ; écrit aussi en cas d’absence de match.
- `result.json` : conclusion produite par la commande locale en cas de succès.

Les coordonnées renvoyées sont la **position de la caméra**, pas la parcelle ni
une adresse certifiée. Vérifier visuellement le meilleur candidat et les voisins.
La BAN peut proposer une adresse voisine de la caméra ; cela ne démontre pas
l’adresse du bien. Les angles des panoramas Panoramax sont des angles locaux à
l’image, sauf indication explicite : ne pas projeter une façade à partir de ces
angles comme s’ils étaient automatiquement orientés vers le nord.

La couverture Panoramax varie selon les lieux. L’absence de correspondance peut
signifier absence de couverture, mauvaise zone, façade invisible, différences
d’époque ou échec du modèle. Le programme ne fabrique pas de localisation en
cas d’échec. L’API de recherche bbox n’expose pas de pagination fiable : une
réponse saturant la limite doit être signalée, pas assimilée à toute la couverture.

Conserver les attributions et licences des images et respecter leurs conditions.
Le lien Google Maps final sert à afficher des coordonnées ; il ne signifie pas
que Google a fourni l’image de référence.

Les résultats historiques de l’API peuvent être **rejoués depuis le cache** de
`NETRYX_RESULTS_DIR`. La commande locale ne réutilise pas ce résultat historique
et permet de tester réellement la nouvelle acquisition.

## Tests sans GPU

```bash
uv pip install --python .venv/bin/python -r requirements-test.txt
.venv/bin/python -m pytest tests -q
npm --prefix frontend test
npm --prefix frontend run check
```

`unittest discover` seul ignore les tests fonctionnels pytest : il ne suffit pas.
Ces tests contrôlent les contrats, les erreurs et les régressions ; un vrai essai
réseau avec les modèles reste nécessaire pour mesurer le matching.

## Sources

- Présentation et réutilisation : https://docs.panoramax.fr/
- API STAC : https://docs.panoramax.fr/backend/api/api/
- Recherche fédérée : https://api.panoramax.xyz/api/search
- Carte de couverture : https://explore.panoramax.fr/
