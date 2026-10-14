"""Migrations du schéma SQLite, versionnées par `PRAGMA user_version`.

Toute évolution du schéma passe par une nouvelle migration ajoutée en fin de `MIGRATIONS`.
Une migration publiée n'est jamais modifiée.
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
        description="imports et transactions",
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
""",
    ),
    Migration(
        numero=2,
        description="classements, alertes et charges fixes",
        sql="""
CREATE TABLE classements (
    transaction_id     TEXT PRIMARY KEY REFERENCES transactions (id),
    mois_budgetaire    TEXT NOT NULL,
    nature             TEXT,                 -- NULL = non classée
    sous_categorie     TEXT,
    mois_rattachement  TEXT,                 -- NULL : mois de paie à déterminer (RG-04)
    origine            TEXT NOT NULL,
    detail             TEXT,
    contrepartie_id    TEXT REFERENCES transactions (id),
    lien_id            TEXT REFERENCES transactions (id),
    exceptionnel       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX classements_mois ON classements (mois_budgetaire);

CREATE TABLE alertes (
    id              INTEGER PRIMARY KEY,
    mois            TEXT NOT NULL,
    code            TEXT NOT NULL,
    transaction_id  TEXT REFERENCES transactions (id),
    message         TEXT NOT NULL
);

CREATE INDEX alertes_mois ON alertes (mois);

CREATE TABLE charges_fixes_mois (
    mois             TEXT NOT NULL,
    nom              TEXT NOT NULL,
    attendu_centimes INTEGER NOT NULL,
    reel_centimes    INTEGER,
    seuil_pct        TEXT NOT NULL,          -- Decimal en texte, jamais REAL
    hors_seuil       INTEGER NOT NULL,
    PRIMARY KEY (mois, nom)
);

CREATE TABLE charges_fixes_transactions (
    mois            TEXT NOT NULL,
    nom             TEXT NOT NULL,
    transaction_id  TEXT NOT NULL REFERENCES transactions (id),
    PRIMARY KEY (mois, nom, transaction_id),
    FOREIGN KEY (mois, nom) REFERENCES charges_fixes_mois (mois, nom)
);
""",
    ),
    Migration(
        numero=3,
        description="questions, règles apprises et décisions",
        sql="""
CREATE TABLE questions (
    id               INTEGER PRIMARY KEY,
    transaction_id   TEXT NOT NULL UNIQUE REFERENCES transactions (id),
    contrepartie_id  TEXT REFERENCES transactions (id),
    type             TEXT NOT NULL CHECK (type IN ('virement', 'salaire', 'operation')),
    motif            TEXT NOT NULL,                  -- 'RG-06', 'RG-14', 'RG-04', 'non_classe'
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
    nature                TEXT,
    sous_categorie        TEXT,
    detail                TEXT,
    active                INTEGER NOT NULL DEFAULT 1,
    creee_le              TEXT NOT NULL,
    question_id           INTEGER REFERENCES questions (id),
    CHECK ((montant_min_centimes IS NULL) = (montant_max_centimes IS NULL))
);

CREATE TABLE decisions (
    transaction_id  TEXT PRIMARY KEY REFERENCES transactions (id),
    question_id     INTEGER NOT NULL REFERENCES questions (id),
    nature          TEXT NOT NULL,
    sous_categorie  TEXT,
    detail          TEXT,
    lien_id         TEXT REFERENCES transactions (id),
    mois_paie       TEXT,                            -- question salaire (RG-04)
    regle_id        INTEGER REFERENCES regles (id),  -- règle créée à partir de cette réponse
    decidee_le      TEXT NOT NULL
);
""",
    ),
    Migration(
        numero=4,
        description="propositions du catégoriseur, tags exceptionnels, règle appliquée",
        sql="""
ALTER TABLE regles ADD COLUMN desactivee_le TEXT;

ALTER TABLE classements ADD COLUMN regle_id INTEGER REFERENCES regles (id);

CREATE TABLE propositions (
    id                       INTEGER PRIMARY KEY,
    mois                     TEXT NOT NULL,
    source                   TEXT NOT NULL CHECK (source IN ('ca_cb', 'tr')),
    cle_marchand             TEXT NOT NULL,
    sous_categorie_proposee  TEXT,           -- NULL : aucune proposition (mode dégradé)
    modele                   TEXT,           -- ex. anthropic:claude-haiku-4-5
    sous_categorie_corrigee  TEXT,           -- saisie de l'utilisateur, prime sur la proposition
    regle_id                 INTEGER REFERENCES regles (id),
    proposee_le              TEXT NOT NULL,
    validee_le               TEXT
);

CREATE INDEX propositions_cle ON propositions (source, cle_marchand);

CREATE TABLE exceptionnels (
    transaction_id  TEXT PRIMARY KEY REFERENCES transactions (id),
    exceptionnel    INTEGER NOT NULL CHECK (exceptionnel IN (0, 1)),
    decide_le       TEXT NOT NULL
);
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
            f"jusqu'à {VERSION_COURANTE} (base créée par une version plus récente du code)"
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
