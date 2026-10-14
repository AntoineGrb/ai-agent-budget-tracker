"""Journal `decisions.md`, régénéré à chaque `classify` depuis la base (cadrage §7, spec 07).

Fichier local de lecture humaine : il contient les libellés, comme le terminal, et n'est envoyé
nulle part. La base reste la source de vérité ; le contenu est déterministe à horloge figée.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from budget.categories import SousCategorie, libelle_complet
from budget.horloge import maintenant
from budget.ledger import Ledger
from budget.models import Nature, Source, Transaction
from budget.montants import formater_montant
from budget.questions.modeles import TypeQuestion
from budget.regles import Regle
from budget.restitution.operations import LIBELLES_NATURE, libelle_compte

_TITRES_QUESTION = {
    TypeQuestion.VIREMENT: "Virement",
    TypeQuestion.SALAIRE: "Salaire",
    TypeQuestion.OPERATION: "Opération",
}


def _cellule(texte: str) -> str:
    return " ".join(texte.split()).replace("|", "\\|")


def _table(entetes: Sequence[str], lignes: Iterable[Sequence[str]]) -> list[str]:
    sortie = [
        "| " + " | ".join(entetes) + " |",
        "| " + " | ".join("---" for _ in entetes) + " |",
    ]
    sortie += ["| " + " | ".join(_cellule(c) for c in ligne) + " |" for ligne in lignes]
    return sortie


def _date(valeur: object, *, heure: bool = True) -> str:
    instant = datetime.fromisoformat(str(valeur))
    return f"{instant:%d/%m/%Y %H:%M}" if heure else f"{instant:%d/%m/%Y}"


def _operation(t: Transaction) -> str:
    return (
        f"{libelle_compte(t)} · {t.date_operation:%d/%m/%Y} · {formater_montant(t.montant)} · "
        f"{t.type_operation or 'Carte'} · {t.libelle}"
    )


def condition_regle(regle: Regle) -> str:
    if regle.montant_min is None or regle.montant_max is None:
        return "sans condition"
    if regle.montant_min == regle.montant_max:
        return f"montant {formater_montant(regle.montant_min)}"
    return f"de {formater_montant(regle.montant_min)} à {formater_montant(regle.montant_max)}"


def resultat_regle(regle: Regle) -> str:
    cible = regle.sous_categorie or regle.detail or regle.nature
    return str(cible) if cible is not None else "toujours demander"


@dataclass
class _Mois:
    questions: list[tuple[str, int, list[str]]] = field(default_factory=list)
    marchands: list[tuple[str, int, list[str]]] = field(default_factory=list)
    exceptionnels: list[tuple[str, str, list[str]]] = field(default_factory=list)


def generer_journal(ledger: Ledger, origine: str | None = None) -> str:
    """Contenu Markdown du journal ; `origine` : chemin de la base affiché en en-tête."""
    transactions = {t.id: t for t in ledger.transactions()}
    regles = ledger.regles()
    par_mois: dict[str, _Mois] = {}

    # Questions répondues : la décision portée par la transaction de la question
    for ligne in ledger.lire_sql(
        "SELECT q.id, q.type, q.motif, d.decidee_le, d.nature, d.sous_categorie, d.detail, "
        "d.lien_id, d.mois_paie, d.regle_id, q.transaction_id FROM questions q "
        "JOIN decisions d ON d.transaction_id = q.transaction_id "
        "WHERE q.repondue_le IS NOT NULL"
    ):
        numero, type_q, motif, decidee, nature, sc, detail, lien, mois_paie, regle_id, tid = ligne
        t = transactions[str(tid)]
        titre = f"{_TITRES_QUESTION[TypeQuestion(str(type_q))]} ({motif})"
        reponse = _reponse(
            Nature(str(nature)),
            SousCategorie(str(sc)) if sc else None,
            str(detail) if detail else None,
            transactions.get(str(lien)) if lien else None,
            str(mois_paie) if mois_paie else None,
        )
        regle = f"n° {regle_id}" if regle_id is not None else ""
        par_mois.setdefault(t.mois_budgetaire, _Mois()).questions.append(
            (str(decidee), int(str(numero)), [_date(decidee), _operation(t), titre, reponse, regle])
        )

    # Marchands validés : opérations du mois couvertes par la règle créée
    couvertes = {
        (int(str(r)), str(m)): int(str(n))
        for r, m, n in ledger.lire_sql(
            "SELECT regle_id, mois_budgetaire, COUNT(*) FROM classements "
            "WHERE regle_id IS NOT NULL GROUP BY regle_id, mois_budgetaire"
        )
    }
    for p in ledger.propositions():
        if p.validee_le is None or p.retenue is None:
            continue
        compte = "Perso" if p.source is Source.TR else "Joint"
        proposee = libelle_complet(p.proposee) if p.proposee else "—"
        operations = couvertes.get((p.regle_id or 0, p.mois), 0)
        par_mois.setdefault(p.mois, _Mois()).marchands.append(
            (
                p.validee_le.isoformat(),
                p.id,
                [
                    _date(p.validee_le.isoformat()),
                    compte,
                    p.cle_marchand,
                    str(operations),
                    proposee,
                    libelle_complet(p.retenue),
                    f"n° {p.regle_id}" if p.regle_id is not None else "",
                ],
            )
        )

    for tid, exceptionnel, decide in ledger.lire_sql(
        "SELECT transaction_id, exceptionnel, decide_le FROM exceptionnels"
    ):
        t = transactions[str(tid)]
        par_mois.setdefault(t.mois_budgetaire, _Mois()).exceptionnels.append(
            (str(decide), t.id, [_date(decide), _operation(t), "oui" if exceptionnel else "non"])
        )

    actives = sum(1 for r in regles if r.active)
    instant = maintenant()
    lignes = [
        "# Journal des décisions",
        "",
        f"Régénéré le {instant:%d/%m/%Y à %H:%M} depuis "
        f"{origine or ledger.chemin.as_posix()}. Ne pas modifier : la base fait foi.",
        "",
        f"## Règles ({actives} actives, {len(regles) - actives} désactivée(s))",
        "",
        *_table(
            (
                "N°",
                "Type",
                "Source",
                "Critère",
                "Valeur",
                "Condition",
                "Résultat",
                "Créée le",
                "Désactivée le",
            ),
            (
                (
                    str(r.id),
                    r.type.value,
                    r.source.value,
                    r.critere.value,
                    r.valeur,
                    condition_regle(r),
                    resultat_regle(r),
                    f"{r.creee_le:%d/%m/%Y}" if r.creee_le else "",
                    f"{r.desactivee_le:%d/%m/%Y}" if r.desactivee_le else "",
                )
                for r in regles
            ),
        ),
    ]
    for mois in sorted(par_mois, reverse=True):
        contenu = par_mois[mois]
        lignes += ["", f"## {mois}"]
        sections = (
            (
                "Questions",
                ("Décidée le", "Opération", "Question", "Réponse", "Règle créée"),
                contenu.questions,
            ),
            (
                "Marchands validés",
                ("Validé le", "Compte", "Marchand", "Op.", "Proposition", "Retenue", "Règle"),
                contenu.marchands,
            ),
            (
                "Tags exceptionnels",
                ("Décidé le", "Opération", "Exceptionnel"),
                contenu.exceptionnels,
            ),
        )
        for titre, entetes, entrees in sections:
            ordonnees = [cellules for *_, cellules in sorted(entrees, key=lambda e: (e[0], e[1]))]
            lignes += ["", f"### {titre} ({len(ordonnees)})", "", *_table(entetes, ordonnees)]
    return "\n".join(lignes) + "\n"


def _reponse(
    nature: Nature,
    sous_categorie: SousCategorie | None,
    detail: str | None,
    liee: Transaction | None,
    mois_paie: str | None,
) -> str:
    if mois_paie is not None:
        return f"Mois de paie {mois_paie}"
    texte = LIBELLES_NATURE[nature]
    if sous_categorie is not None:
        return f"{texte} › {libelle_complet(sous_categorie)}"
    if liee is not None:
        return (
            f"{texte} : {libelle_compte(liee)} · {liee.date_operation:%d/%m/%Y} · "
            f"{formater_montant(liee.montant)}"
        )
    return f"{texte} ({detail})" if detail else texte


def ecrire_journal(ledger: Ledger, chemin: Path, origine: str | None = None) -> Path:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(generer_journal(ledger, origine), encoding="utf-8")
    return chemin
