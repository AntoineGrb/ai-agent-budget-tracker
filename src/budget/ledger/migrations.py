"""Migrations du schéma SQLite, versionnées par `PRAGMA user_version`.

Le recadrage d'octobre 2026 a refait le schéma en une seule migration, sur une base neuve. Depuis,
toute évolution du schéma passe par une nouvelle migration ajoutée en fin de `MIGRATIONS`. Une
migration publiée n'est jamais modifiée.
"""

import sqlite3
from dataclasses import dataclass

from budget.errors import LedgerError


@dataclass(frozen=True)
class Migration:
    numero: int
    description: str
    sql: str


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        numero=1,
        description="schéma du recadrage : imports, transactions, classements, mémoire",
        sql="""
CREATE TABLE imports (
    id              INTEGER PRIMARY KEY,
    source          TEXT NOT NULL CHECK (source IN ('ca_cb', 'ca_joint', 'tr')),
    mois            TEXT NOT NULL,            -- AAAA-MM
    fichier         TEXT NOT NULL,
    empreinte       TEXT NOT NULL,            -- sha256 du fichier, informatif
    nb_operations   INTEGER NOT NULL,
    importe_le      TEXT NOT NULL,            -- horodatage ISO 8601
    UNIQUE (source, mois)
);

CREATE TABLE transactions (
    id                    TEXT PRIMARY KEY,   -- RG-16
    import_id             INTEGER NOT NULL REFERENCES imports (id),
    source                TEXT NOT NULL,
    compte                TEXT NOT NULL CHECK (compte IN ('joint', 'perso')),
    fichier               TEXT NOT NULL,
    position              INTEGER NOT NULL,
    rang                  INTEGER NOT NULL CHECK (rang >= 1),
    date_operation        TEXT NOT NULL,
    date_debit            TEXT,
    mois_budgetaire       TEXT NOT NULL,      -- RG-01
    type_operation        TEXT,
    libelle               TEXT NOT NULL,
    libelle_brut          TEXT NOT NULL,
    libelle_normalise     TEXT NOT NULL,
    ics                   TEXT,
    carte                 TEXT,
    montant_centimes      INTEGER NOT NULL CHECK (montant_centimes <> 0),
    solde_apres_centimes  INTEGER
);

CREATE INDEX transactions_mois ON transactions (mois_budgetaire);

CREATE TABLE questions (
    id               INTEGER PRIMARY KEY,
    transaction_id   TEXT NOT NULL UNIQUE REFERENCES transactions (id),
    type             TEXT NOT NULL CHECK (type IN ('virement', 'operation')),
    motif            TEXT NOT NULL,                  -- 'RG-06', 'RG-13', 'RG-14'
    posee_le         TEXT NOT NULL,
    repondue_le      TEXT
);

CREATE TABLE regles (
    id                    INTEGER PRIMARY KEY,
    type                  TEXT NOT NULL CHECK (type IN (
                              'virement', 'prelevement', 'marchand_carte', 'toujours_demander'
                          )),
    source                TEXT NOT NULL,
    critere               TEXT NOT NULL
                          CHECK (critere IN ('ics', 'libelle_exact', 'libelle_prefixe')),
    valeur                TEXT NOT NULL,
    montant_min_centimes  INTEGER,
    montant_max_centimes  INTEGER,
    sous_categorie        TEXT,
    non_comptee           INTEGER NOT NULL DEFAULT 0 CHECK (non_comptee IN (0, 1)),  -- RG-10
    active                INTEGER NOT NULL DEFAULT 1,
    creee_le              TEXT NOT NULL,
    desactivee_le         TEXT,
    question_id           INTEGER REFERENCES questions (id),
    CHECK ((montant_min_centimes IS NULL) = (montant_max_centimes IS NULL)),
    CHECK (NOT (non_comptee = 1 AND sous_categorie IS NOT NULL))
);

CREATE TABLE decisions (
    transaction_id  TEXT PRIMARY KEY REFERENCES transactions (id),
    question_id     INTEGER NOT NULL REFERENCES questions (id),
    sous_categorie  TEXT,
    non_comptee     INTEGER NOT NULL DEFAULT 0,
    regle_id        INTEGER REFERENCES regles (id),  -- règle créée à partir de cette réponse
    decidee_le      TEXT NOT NULL,
    CHECK ((non_comptee = 1) <> (sous_categorie IS NOT NULL))
);

CREATE TABLE classements (
    transaction_id   TEXT PRIMARY KEY REFERENCES transactions (id),
    mois_budgetaire  TEXT NOT NULL,
    sous_categorie   TEXT,                 -- NULL et non_comptee = 0 : en attente
    non_comptee      INTEGER NOT NULL DEFAULT 0,
    origine          TEXT NOT NULL,
    detail           TEXT,
    regle_id         INTEGER REFERENCES regles (id),
    CHECK (NOT (non_comptee = 1 AND sous_categorie IS NOT NULL))
);

CREATE INDEX classements_mois ON classements (mois_budgetaire);

CREATE TABLE propositions (
    id                       INTEGER PRIMARY KEY,
    mois                     TEXT NOT NULL,
    source                   TEXT NOT NULL CHECK (source IN ('ca_cb', 'tr')),
    cle_marchand             TEXT NOT NULL,
    sous_categorie_proposee  TEXT,           -- NULL : aucune proposition (mode dégradé)
    modele                   TEXT,           -- ex. anthropic:claude-haiku-4-5
    sous_categorie_corrigee  TEXT,           -- saisie de l'utilisateur, prime sur la proposition
    corrigee_non_comptee     INTEGER NOT NULL DEFAULT 0,
    regle_id                 INTEGER REFERENCES regles (id),
    proposee_le              TEXT NOT NULL,
    validee_le               TEXT
);

CREATE INDEX propositions_cle ON propositions (source, cle_marchand);
""",
    ),
)

VERSION_COURANTE = MIGRATIONS[-1].numero


def version(connexion: sqlite3.Connection) -> int:
    """Version du schéma (`PRAGMA user_version`, 0 pour une base neuve)."""
    return int(connexion.execute("PRAGMA user_version").fetchone()[0])


def appliquer_migrations(connexion: sqlite3.Connection, nom_base: str) -> list[int]:
    """Applique les migrations manquantes, chacune dans sa transaction ; renvoie leurs numéros.

    La connexion doit être en mode autocommit (`isolation_level=None`).
    """
    actuelle = version(connexion)
    if actuelle > VERSION_COURANTE:
        raise LedgerError(
            f"{nom_base} : schéma en version {actuelle}, ce code ne connaît que les versions "
            f"jusqu'à {VERSION_COURANTE}. Base d'une autre version du code (avant le recadrage "
            "d'octobre 2026 ?) : la mettre de côté et repartir d'une base neuve"
        )
    appliquees = []
    for migration in MIGRATIONS:
        if migration.numero <= actuelle:
            continue
        try:
            connexion.execute("BEGIN")
            for instruction in _instructions(migration.sql):
                connexion.execute(instruction)
            connexion.execute(f"PRAGMA user_version = {migration.numero:d}")
            connexion.execute("COMMIT")
        except sqlite3.Error as erreur:
            if connexion.in_transaction:
                connexion.execute("ROLLBACK")
            raise LedgerError(
                f"{nom_base} : échec de la migration {migration.numero:04d} "
                f"({migration.description}) : {erreur}"
            ) from erreur
        appliquees.append(migration.numero)
    return appliquees


def _instructions(sql: str) -> list[str]:
    """Découpe un script en instructions, commentaires `--` retirés.

    `executescript` validerait implicitement la transaction en cours : on exécute donc les
    instructions une à une. Les migrations ne contiennent ni trigger ni `;` littéral.
    """
    sans_commentaires = "\n".join(ligne.split("--", 1)[0] for ligne in sql.splitlines())
    return [i.strip() for i in sans_commentaires.split(";") if i.strip()]
