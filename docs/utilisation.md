# Guide d'utilisation

Ce guide commence par la validation du projet avec le stage final, puis décrit
la démonstration progressive des six stages. Les commandes **hôte** se lancent
à la racine du dépôt ; les commandes **VM** se lancent dans Linux invité.

## 1. Valider le projet — stage 06 par défaut

### Préparer l'environnement et construire

Installer Git, Make, Ninja, Python 3, `qemu-img` et les dépendances de
construction de QEMU/Buildroot (compilateurs, bibliothèques de développement,
modules Perl). La première construction nécessite un accès réseau pour
initialiser les sous-modules et télécharger les sources et outils.

**Hôte :**

```sh
make setup
make
```

`make setup` initialise les sous-modules et vérifie les principaux outils ;
il n'installe pas les paquets système. `make` construit QEMU, le noyau,
le module Rust et le système de fichiers de la VM. On peut séparer ces deux
constructions avec `make qemu` puis `make image`.

Le stage par défaut est **`06-hw-offloading`**, dans le Makefile principal
comme dans l'intégration Buildroot. Les résultats sont placés sous `.build/`,
avec une sortie Buildroot distincte pour chaque stage.

### Vérifier automatiquement

Arrêter la VM de démonstration avant de reconstruire ou tester son image :
les tests ont besoin d'accéder à son système de fichiers. Ils attendent une
image de construction avec le compte `root` sans mot de passe.

**Hôte :**

```sh
make smoke
make check
```

- `make smoke` construit si nécessaire, démarre la VM, charge le module,
  crée temporairement `/dev/rgb`, écrit du rouge et vérifie les traces Linux
  et QEMU. Réussite : `RGB VM smoke test: OK`.
- `make check` vérifie aussi la structure et les versions des sous-modules,
  puis exécute le test QEMU et les scénarios VM du stage sélectionné.
  Avec le stage 06 par défaut, cela couvre le fondu, une IRQ par opération,
  l'acquittement, les attentes concurrentes et tardives, les erreurs, les
  signaux, le retrait du périphérique et le rechargement du module.
- Le test d'expiration masque volontairement l'IRQ et attend **65 secondes**.
  Ce délai fait partie du test ; il ne signifie pas que la construction bloque.

Les messages attendus pour `make check` sont :

```text
QEMU fade/reset/VMState checks: OK
fade/IRQ VM scenarios: OK
RGB VM smoke test: OK
```

Le premier teste directement le périphérique avec QTest/QMP : interpolation,
IRQ de niveau, acquittement, annulation, reset et sauvegarde/restauration d'un
fondu actif. Les scénarios IRQ dans la VM sont exécutés pour le stage 06 ;
les autres stages sont vérifiés selon leurs interfaces disponibles.

```sh
make smoke RGB_DRIVER_STAGE=01-character-device
make check RGB_DRIVER_STAGE=03-device-tree
make check-stages  # compile les six étapes, sans les démontrer
```

`make check-stages` peut être long car chaque étape possède sa propre sortie
Buildroot. `make clean` supprime les fichiers générés dans `.build/`.

### Essayer le stage final manuellement

**Hôte, premier terminal :**

```sh
make run
```

Dans la console Buildroot, se connecter avec `root` (sans mot de passe dans
l'image initiale). **VM :**

```sh
modprobe rgb
lsmod
major=$(awk '$2 == "rgb" { print $1 }' /proc/devices)
mknod /dev/rgb c "$major" 0
```

Pour les copies SSH/SCP, définir un mot de passe `root` dans la console
**VM** (une fois pour cette image), puis le saisir lors des connexions :

```sh
passwd
```

**Hôte, second terminal :**

```sh
scp -O -P 2222 driver/rgb_ioctl_fade.py root@localhost:/tmp/
```

`-O` force le protocole SCP historique : l'image utilise Dropbear sans serveur
SFTP. Si un mot de passe a été configuré dans la VM, SSH/SCP le demandent.

**VM :**

```sh
python3 /tmp/rgb_ioctl_fade.py
python3 /tmp/rgb_ioctl_fade.py --color FF0000 --duration-ms 10000
```

Le client applique le rouge, puis demande un fondu matériel vers le noir en
3 000 ms par défaut. `--color` choisit la couleur de départ ; la cible du
client est toujours le noir. START retourne immédiatement, puis Python dort
dans WAIT jusqu'à la notification de fin.

Pour observer un fondu de 10 secondes, relever le compteur IRQ avant le départ,
puis lire l'état du PID affiché par Python dans un second terminal **VM** :

```sh
grep rgb /proc/interrupts
# Pendant le fondu : remplacer 123 par le PID affiché.
grep '^State:' /proc/123/status
# Après la fin : comparer le compteur.
grep rgb /proc/interrupts
```

Une autre connexion VM peut s'ouvrir depuis l'hôte avec
`ssh -p 2222 root@localhost`. L'état `S (sleeping)` montre l'attente ; il ne
prouve pas à lui seul la réception de l'IRQ. Le compteur augmente d'une unité
et les traces montrent la fin QEMU, `completion IRQ received, acknowledging`
dans le handler Rust threadé, puis le retour de WAIT dans Python.

## 2. Faire la démonstration — du stage 01 au stage 06

Ce parcours utilise une seule VM, démarrée au stage 01. Les étapes suivantes
remplacent uniquement son module Rust. Arrêter une éventuelle VM précédente
avant de démarrer celle-ci, notamment pour libérer le port SSH 2222.

### Préparer et démarrer au stage 01

**Hôte :**

```sh
make setup
make qemu
make image RGB_DRIVER_STAGE=01-character-device
make run RGB_DRIVER_STAGE=01-character-device
```

Ces choix explicites servent au déroulé pédagogique ; le défaut reste le
stage 06. Conserver cette console ouverte et utiliser un second terminal hôte
pour les constructions et les copies. Avant la première copie, définir le
mot de passe SSH avec `passwd` dans la console VM si cela n'a pas déjà été fait.

### Charger le premier module et créer le node

Après connexion en `root`, **VM :**

```sh
modprobe rgb
lsmod
major=$(awk '$2 == "rgb" { print $1 }' /proc/devices)
mknod /dev/rgb c "$major" 0
ls -l /dev/rgb
echo FF0000 > /dev/rgb
```

`modprobe` charge le module du stage 01 installé dans cette image. `lsmod`
affiche une ligne `rgb` quand il est chargé. Le stage 01 reçoit la couleur et
la trace dans Linux ; aucune couleur n'est appliquée dans QEMU à cette étape.

**Créer `/dev/rgb` une seule fois pour cette session de démonstration.**
Le node associe le major dynamique du pilote au minor 0. Il reste présent
après `rmmod` : les changements de stage le réutilisent. Si le major change,
supprimer le node et le recréer avec la nouvelle valeur de `/proc/devices`.
Après redémarrage de la VM, refaire sa création car `/dev` utilise devtmpfs.

### Construire, copier et charger un nouveau stage

Exemple de passage au stage 02. **Hôte :**

```sh
make image RGB_DRIVER_STAGE=02-mmio-fixed
scp -O -P 2222 \
  .build/buildroot-output/02-mmio-fixed/build/linux-6.18.7/samples/rust/rgb.ko \
  root@localhost:/tmp/rgb.ko
```

Puis **VM :**

```sh
rmmod rgb
lsmod
insmod /tmp/rgb.ko
lsmod
echo FF0000 > /dev/rgb
```

La ligne `rgb` disparaît après `rmmod` et revient après `insmod`. Attendre la
fin des opérations précédentes et fermer les clients avant de décharger le
pilote. `insmod` charge exactement le module copié ; un `modprobe rgb`
rechargerait le stage 01 encore installé dans l'image.

**Pour chaque nouveau stage, répéter seulement la construction et la copie
sur l'hôte, puis le déchargement/chargement dans la VM.** Remplacer le nom du
stage dans les deux chemins de l'exemple. Ni redémarrage de la VM, ni nouvelle
création du node ne sont nécessaires dans le déroulé normal. Les six stages
utilisent la même configuration noyau, ce qui permet ce remplacement.

### Ce que montre chaque stage

| Stage | Ajout | Commande dans la VM et résultat attendu |
| --- | --- | --- |
| `01-character-device` | Interface caractère et copie depuis l'espace utilisateur | `echo FF0000 > /dev/rgb` : couleur reçue dans les traces Linux, sans accès matériel |
| `02-mmio-fixed` | Réservation et mapping MMIO à adresse fixe | Même écriture : trace `wrote MMIO color` dans Linux et `rgb-led: apply RGB(255, 0, 0)` dans QEMU |
| `03-device-tree` | Découverte via Device Tree et platform driver | Au chargement : `platform device matched`, puis même écriture MMIO |
| `04-ioctl-sync` | Cycle rouge → vert → bleu dans l'ioctl | Client cycle : Python reste bloqué environ trois secondes pendant le cycle logiciel |
| `05-ioctl-async` | Cycle exécuté par une workqueue noyau | Même client : Python retourne immédiatement, le worker poursuit les changements de couleur |
| `06-hw-offloading` | Fondu autonome QEMU et IRQ de fin | Client fade : rouge → noir, START immédiat, Python endormi dans WAIT puis réveillé par la notification IRQ |

Pour les stages 04–05, copier le client **depuis l'hôte** :

```sh
scp -O -P 2222 driver/rgb_ioctl_cycle.py root@localhost:/tmp/
```

Puis **VM :**

```sh
python3 /tmp/rgb_ioctl_cycle.py /dev/rgb
```

Pour le stage 06, copier `driver/rgb_ioctl_fade.py` et reprendre les commandes
d'essai manuel de la première partie, sans relancer `make run`. Le stage 06
n'accepte pas l'ancien ioctl cycle : utiliser son client fade.

Au stage 04, le thread Python dort pendant les pauses du cycle logiciel ;
au stage 05, c'est le worker noyau qui attend entre les couleurs. Au stage 06,
QEMU effectue les transitions et son IRQ réveille Python dans WAIT.

### Console et moniteur QEMU

Dans le terminal lancé par `make run`, `Ctrl-a`, puis `c` bascule entre console
Linux et moniteur QEMU. Dans le moniteur :

```text
info qtree
xp /5wx 0x090d0000
```

`Ctrl-a`, puis `x` quitte QEMU. Les traces `rgb-led:` viennent du processus
QEMU sur l'hôte ; les traces `rgb:` viennent du pilote dans Linux invité.

## 3. Référence des interfaces du stage 06

### Contrat utilisateur

- START : `_IOW('r', 0x02, 8)`, soit `0x40087202` sur cette machine ARM64.
  Argument de huit octets : deux `u32` en ordre natif, couleur `0x00RRGGBB`
  puis durée entre 1 et 60 000 ms. Python utilise `struct.pack('=II', ...)`.
- WAIT : `_IO('r', 0x03)`, soit `0x7203`, sans argument. Attend la dernière
  génération soumise au moment de l'appel. Plusieurs appels peuvent observer
  la même fin ; un appel après la fin retourne immédiatement.
- L'état est global au périphérique, sans propriétaire par fichier ouvert.
  Aucun START préalable : `EINVAL`. Paramètres invalides : `EINVAL`.
  START ou write pendant un fondu : `EBUSY`. Ancien ioctl cycle : `ENOTTY`.
- WAIT est interruptible et expire après 65 secondes (`ETIMEDOUT`). Ni un
  signal, ni la fermeture du client, ni cette expiration n'annulent le fondu.
  Après expiration, le périphérique reste occupé jusqu'à réception de l'IRQ
  ou retrait. Le retrait réveille les attentes avec `ENODEV`.

### Contrat matériel

Accès MMIO alignés de 32 bits, base `0x090d0000`, fenêtre `0x100` :

| Offset | Registre | Sens |
| --- | --- | --- |
| `0x00/04/08` | RED/GREEN/BLUE | Canaux courants ; écritures immédiates au repos |
| `0x0c` | APPLY | Écrire 1 pour appliquer la couleur immédiate |
| `0x10` | STATUS | BUSY=0, READY=1, ERROR=2 |
| `0x14` | TARGET | Cible `0x00RRGGBB` |
| `0x18` | DURATION_MS | 1 à 60 000 ms |
| `0x1c` | COMMAND | 1=démarrer, 2=annuler ; lecture=0 |
| `0x20` | IRQ_STATUS | Bit 0=fin en attente, écrire 1 pour acquitter |
| `0x24` | IRQ_ENABLE | Bit 0=autoriser la ligne IRQ |

Interpolation linéaire toutes les 20 ms en temps virtuel, cible exacte à
l'échéance, sans notion de correction gamma. Les paramètres sont figés
pendant le fondu. L'annulation conserve la couleur atteinte et efface l'IRQ.
Le reset remet la couleur à noir. L'IRQ utilise la SPI d'indice 10 (INTID 42
sur le GIC), décrite dans le Device Tree ; le numéro Linux reste dynamique.
Le masque contrôle la ligne, pas le bit de fin : réactiver le masque après
une fin non acquittée déclenche l'IRQ.

Le format de migration du périphérique passe à la version 2 ; les anciens
états sauvegardés du périphérique version 1 ne sont pas compatibles.
