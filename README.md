# Drivers Linux en Rust : espace utilisateur, noyau et matériel

Ce projet accompagne une présentation sur le développement de pilotes Linux
en Rust. Un périphérique RGB émulé dans QEMU sert de fil conducteur pour
explorer les échanges entre une application, le noyau Linux et le matériel.

Le pilote se construit progressivement : périphérique caractère, accès aux
registres MMIO, découverte par Device Tree, commandes ioctl et travail différé.
La dernière étape confie un fondu au périphérique virtuel, qui signale sa fin
par une IRQ et permet au pilote de réveiller l'application en attente.

Buildroot fournit le système Linux invité et les outils de compilation.
Les versions de QEMU et Buildroot sont épinglées dans des sous-modules ; les
sources du périphérique, les pilotes Rust et les patches d'intégration sont
conservés dans ce dépôt pour rendre les démonstrations reproductibles.

## Versions et environnement testé

Le projet a été testé sur **Fedora 44**, avec **GCC 16.2.1** sur l'hôte
(`20260819`, paquet Red Hat `16.2.1-2`). Ce compilateur construit notamment
QEMU et les outils hôtes. Buildroot fournit séparément le compilateur croisé
ARM64 **GCC 15.2.0** pour le système invité ; le pilote utilise Rust.

| Composant | Version utilisée | Référence |
| --- | --- | --- |
| QEMU | `11.0.50` (développement, `v11.0.0-2352-g8f1d3b586f`) | Sous-module `vendor/qemu/`, commit `8f1d3b586f1265023f75ea9c227c35d463321aef` |
| Buildroot | `2026.05-334-g6b1f6f7a48` | Sous-module `vendor/buildroot/`, commit `6b1f6f7a480765b3d6ca07691095035a8b899eaf` |
| Noyau Linux | `6.18.7` | Téléchargé par Buildroot, version fixée dans `buildroot-external/configs/rgb_defconfig` |
| Reveal.js | `5.2.1-20-gbecc9bd1` | Sous-module `reveal.js/`, commit `becc9bd19e418b75027b541c41952105a1425c96` |

Les compilateurs utilisés se répartissent ainsi :

| Compilateur | Fourni par | Utilisation |
| --- | --- | --- |
| GCC 16.2.1 | Fedora 44 | QEMU et outils exécutés sur l'hôte |
| GCC 15.2.0 | Buildroot | C du noyau Linux et des programmes ARM64 invités |
| Rust 1.96.0 | Buildroot | Code Rust, dont le pilote |

Les outils fournis par Buildroot se trouvent dans
`.build/buildroot-output/<stage>/host/bin/`. Buildroot compile GCC, LLVM et
Clang depuis leurs sources ; Rust est installé depuis une distribution
binaire précompilée. Bindgen, également construit par Buildroot, utilise
`libclang` pour générer les bindings C nécessaires au code Rust du noyau.

Le noyau Linux n'est pas un sous-module du dépôt. Les références QEMU,
Buildroot et Reveal.js correspondent aux commits épinglés, pas nécessairement
à des versions publiées sans modifications ultérieures.

## Sous-modules

Le dépôt référence trois projets externes comme sous-modules Git. Chaque
référence désigne un commit précis pour retrouver les mêmes sources lors
d'une nouvelle construction.

### QEMU — `vendor/qemu/`

[QEMU](https://gitlab.com/qemu-project/qemu) émule la machine ARM64 qui exécute
Linux et le matériel auquel le pilote accède. Le projet lui ajoute un
périphérique RGB : registres MMIO, timer de fondu et ligne IRQ reliée au
contrôleur d'interruptions. La machine émulée décrit aussi ce périphérique
dans le Device Tree transmis à Linux.

La construction utilise un clone sous `.build/src/qemu/`. Les scripts y
appliquent les patches de `integrations/qemu/` et y copient le périphérique
de `device/qemu/`, en conservant le sous-module d'origine intact.

### Buildroot — `vendor/buildroot/`

[Buildroot](https://github.com/buildroot/buildroot) construit les outils de
compilation et le système Linux invité : noyau avec support Rust, module du
pilote et système de fichiers contenant les outils de démonstration,
notamment Python et le serveur SSH Dropbear.

Son clone de construction se trouve sous `.build/src/buildroot/`. Le
répertoire `buildroot-external/` fournit les configurations, les patches Linux
et le mécanisme qui injecte la version sélectionnée du pilote. Chaque étape
possède sa sortie sous `.build/buildroot-output/`.

### Reveal.js — `reveal.js/`

[Reveal.js](https://github.com/hakimel/reveal.js) fournit le moteur de la
présentation HTML : navigation entre diapositives, thèmes, notes du
présentateur et coloration du code. La présentation dans `slides/` charge
directement ses styles, scripts et plugins depuis ce sous-module.

Les trois sous-modules s'initialisent depuis la racine du dépôt avec
`git submodule update --init`. Les modifications propres au projet restent
dans les sources, templates et patches du dépôt principal.

## Construire la présentation

Toutes les commandes suivantes s'exécutent depuis la **racine du dépôt**.
La présentation est en anglais, avec des notes de démonstration, et utilise
Reveal.js. Installer `uv`, puis initialiser les sous-modules :

```sh
git submodule update --init
make slides
```

`make slides` génère `slides/index.html` à partir de
`slides/templates/index.html`. Pour servir la présentation et la reconstruire
à chaque modification :

```sh
make slides-serve
```

Ouvrir ensuite [http://localhost:8000/slides/](http://localhost:8000/slides/).

Pour exporter en PDF, installer également Podman et `curl`. Garder le serveur
actif et lancer dans un second terminal :

```sh
make slides-pdf-image
make slides-pdf
```

La première commande construit l'image de conteneur utilisée pour l'export ;
la seconde produit `slides/linux_rust_drivers.pdf`.

## Organisation du dépôt

| Répertoire | Contenu et rôle |
| --- | --- |
| `docs/` | Guide de construction, validation et démonstration des six étapes du pilote. |
| `slides/` | Template HTML de la présentation, HTML généré, styles, images et exports. Modifier la présentation dans `slides/templates/index.html`. |
| `reveal.js/` | Sous-module du moteur de présentation et de ses plugins. |
| `build_pdf/` | Conteneur et scripts d'export de la présentation en PDF. |
| `device/` | Sources du périphérique RGB virtuel QEMU : registres, timer de fondu et IRQ. |
| `driver/` | Versions pédagogiques autonomes du pilote dans `stages/` et clients Python des commandes ioctl. |
| `buildroot-external/` | Configuration Buildroot et noyau, patches Linux et injection du pilote sélectionné dans la construction. |
| `integrations/` | Patches d'intégration du périphérique dans QEMU et du support Linux/Rust dans Buildroot. |
| `scripts/` | Vérification de la structure et préparation des clones de construction QEMU et Buildroot. |
| `tools/` | Visualiseur Pygame de la LED, lancé avec `uv`, via socket Unix ou relais TCP. |
| `tests/` | Tests du périphérique QEMU et vérifications des pilotes dans la VM. |
| `vendor/` | Sous-modules QEMU et Buildroot épinglés à des versions précises. |
| `.build/` | Répertoire généré : clones de construction, téléchargements, caches, outils, modules et images de VM. |

Les scripts appliquent les patches dans les clones sous `.build/src/`, sans
modifier les sous-modules d'origine. À la racine, le `Makefile` expose les
commandes communes et `server.py` construit et sert les slides.

## Construction, validation et démonstration

Consulter le répertoire [docs/](docs/) :

- [Guide d'utilisation](docs/utilisation.md) : préparer l'environnement,
  construire et valider le projet, puis dérouler la démonstration progressive
  dans une même VM et comprendre l’apport de chaque étape du pilote.
