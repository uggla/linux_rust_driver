# Évolution pédagogique du driver

Les sept commits du POC Buildroot se traduisent par cinq états techniques
rejouables. Les commits documentaires sont conservés dans cette chronologie,
mais ne constituent pas des sources supplémentaires.

| Commit d'origine | Contenu | Stage rejouable |
| --- | --- | --- |
| `db9d5f557b` | Notes initiales | Documentation |
| `66c840c7ef` | Périphérique caractère Rust | `01-character-device` |
| `651b9cf29f` | Accès MMIO à adresse fixe | `02-mmio-fixed` |
| `1abe3e31cb` | Découverte Device Tree et platform driver | `03-device-tree` |
| `845bb81a7e` | Mise à jour des notes | Documentation |
| `cd4f3f916d` | Cycle ioctl synchrone | `04-ioctl-sync` |
| `4bc6eabda7` | Cycle ioctl sur workqueue | `05-ioctl-async` |

Construire une étape particulière :

```sh
make image RGB_DRIVER_STAGE=03-device-tree
```

Chaque étape utilise son propre répertoire de sortie Buildroot sous `.build/`,
ce qui évite de mélanger des modules provenant de versions différentes.

## Stage 06 — fondu matériel et IRQ

`06-hw-offloading` ajoute une sixième étape, sélectionnée explicitement :

```sh
make run RGB_DRIVER_STAGE=06-hw-offloading
make check-offloading
```

QEMU réalise le fondu avec un timer virtuel. Le pilote soumet les paramètres,
puis son handler IRQ threadé acquitte la fin et réveille les appels WAIT.
Il n'effectue aucune écriture intermédiaire de couleur.
