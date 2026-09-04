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
make check-stages  # construit les cinq étapes du pilote
make clean         # supprime uniquement les fichiers générés dans .build/
```

`make check-stages` peut être long car chaque étape possède sa propre sortie
Buildroot.
