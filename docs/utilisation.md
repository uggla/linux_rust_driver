# Guide d'utilisation

Toutes les commandes sont à lancer depuis le répertoire `phase_01`.

## Première construction

Les outils principaux requis sont Git, Make, Ninja et Python 3. La première
construction nécessite un accès réseau pour initialiser les sous-modules et
télécharger les dépendances de QEMU et Buildroot.

```sh
make setup
make
```

`make` construit QEMU, le noyau Linux, le module Rust et l'image Buildroot.
Les fichiers générés sont placés dans `.build/`.

## Lancer la démonstration

```sh
make run
```

Une fois l'invite Buildroot affichée, se connecter avec l'utilisateur `root`,
sans mot de passe, puis exécuter :

```sh
modprobe rgb
major=$(awk '$2 == "rgb" { print $1 }' /proc/devices)
mknod /dev/rgb c "$major" 0
echo FF0000 > /dev/rgb
```

La dernière commande envoie la couleur rouge au périphérique RGB émulé par
QEMU. Pour quitter QEMU depuis la console série, utiliser `Ctrl-a`, puis `x`.

## Choisir une étape du pilote

L'étape finale `05-ioctl-async` est utilisée par défaut. Une autre étape peut
être sélectionnée avec `RGB_DRIVER_STAGE` :

```sh
make image RGB_DRIVER_STAGE=03-device-tree
make run RGB_DRIVER_STAGE=03-device-tree
```

Les étapes disponibles sont décrites dans [evolution.md](evolution.md).

## Vérifier ou nettoyer

```sh
make smoke         # démarre la VM et vérifie une écriture RGB
make check         # contrôle la structure puis lance le smoke test
make check-stages  # construit les six étapes du pilote
make clean         # supprime uniquement les fichiers générés dans .build/
```

`make check-stages` peut être long car chaque étape possède sa propre sortie
Buildroot.

## Fondu matériel et IRQ — stage 06

```sh
make run RGB_DRIVER_STAGE=06-hw-offloading
```

Charger `rgb` et créer `/dev/rgb` comme ci-dessus. Copier le client depuis
un terminal de l'hôte :

```sh
scp -P 2222 driver/rgb_ioctl_fade.py root@localhost:/tmp/
```

Dans la VM :

```sh
python3 /tmp/rgb_ioctl_fade.py
python3 /tmp/rgb_ioctl_fade.py --color FF0000 --duration-ms 10000
```

Par défaut, le fondu va de la couleur courante vers le rouge en 3 000 ms.
Le client affiche son PID et le retour immédiat de START, puis dort dans
WAIT jusqu'à la notification de fin. Pour le voir, ouvrir une seconde
connexion SSH dans la même VM :

```sh
ssh -p 2222 root@localhost
# Remplacer 123 par le PID affiché par Python.
grep '^State:' /proc/123/status
grep rgb /proc/interrupts
```

L'état `S (sleeping)` montre l'attente interruptible ; il ne prouve pas à lui
seul la réception d'une IRQ. Comparer le compteur `rgb` avant et après le
fondu : il augmente d'une unité. Les traces montrent successivement la fin
QEMU, `completion IRQ received, acknowledging generation=...` dans le
handler Rust threadé, puis le retour de WAIT dans Python.

Au stage 04, le thread Python dort aussi, mais pendant les pauses d'un cycle
logiciel synchrone. Au stage 05, Python soumet une workqueue et se termine ;
le worker noyau exécute les changements. Au stage 06, QEMU réalise le fondu
et l'IRQ réveille le thread Python bloqué dans WAIT.

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

### Vérifications

`make check-offloading` construit le stage 06 et lance le smoke test, puis
les scénarios de `tests/guest_fade.py` : erreurs d'ABI, concurrence, IRQ unique,
attente tardive, répétition, signal, IRQ masquée/expiration (65 secondes),
retrait avec fichier ouvert, rebinding et rechargement du module.

`tests/qemu_fade.py` vérifie directement le périphérique avec QTest/QMP :
interpolation, niveau et acquittement IRQ, annulation, reset, sauvegarde et
restauration d'un fondu actif (VMState via snapshot). Il nécessite `qemu-img`
et est également exécuté par `make check-offloading`.
