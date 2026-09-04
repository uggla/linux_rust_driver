# Driver Linux RGB en Rust — phase 01

Ce dépôt transforme le POC en construction reproductible. QEMU et Buildroot
sont des sous-modules upstream immuables ; seules les sources du périphérique,
du driver et leurs petits patches d'intégration sont suivis ici.

## Construction

Prérequis principaux : Git, Make, Ninja, Python 3 et les dépendances de
construction habituelles de QEMU/Buildroot. Les téléchargements et résultats
sont placés dans `.build/`.

```sh
make setup
make qemu
make image
make run
```

La version finale `05-ioctl-async` est construite par défaut. Voir
le [guide d'utilisation](docs/utilisation.md) pour le déroulé complet et
[`docs/evolution.md`](docs/evolution.md) pour rejouer les étapes précédentes.

`make run` démarre la VM avec la console série et expose SSH sur le port 2222.
Dans la VM, le comportement reste volontairement manuel :

```sh
modprobe rgb
major=$(awk '$2 == "rgb" { print $1 }' /proc/devices)
mknod /dev/rgb c "$major" 0
echo FF0000 > /dev/rgb
```

Le client ioctl est suivi dans `driver/rgb_ioctl_cycle.py`. Après l'avoir copié
dans la VM, le cycle peut être lancé avec :

```sh
python3 rgb_ioctl_cycle.py /dev/rgb
```

## Validation

```sh
make check          # build final et smoke test write/MMIO dans la VM
make check-stages   # compile les cinq étapes pédagogiques
```

Le smoke test charge le module, crée temporairement `/dev/rgb`, écrit du rouge
et vérifie à la fois les traces Linux et la sortie du périphérique QEMU.

## Organisation

- `vendor/` : sous-modules QEMU et Buildroot épinglés ;
- `device/qemu/` : source canonique du périphérique virtuel ;
- `driver/stages/` : cinq versions autonomes du driver Rust ;
- `integrations/` : patches minimaux appliqués aux clones de build ;
- `buildroot-external/` : defconfig, configuration noyau, patches et hook
  d'injection du stage choisi.

Les scripts préparent des clones locaux sous `.build/src/`. Ils ne patchent
jamais directement les sous-modules.
