"""RG-04 : rattachement d'un salaire à son mois de paie (fonction pure, spec 04)."""

import re
from datetime import date

from pydantic import BaseModel, ConfigDict

from budget.config import Contexte
from budget.dates import mois_budgetaire
from budget.errors import ControleError
from budget.libelles import normaliser_libelle
from budget.models import Source, Transaction
from budget.montants import formater_montant

_PERIODE = re.compile(r"(\d{2})/(\d{4})")


class RattachementSalaire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    nom: str  # nom du salaire dans le contexte
    mois_paie: str | None  # "AAAA-MM" ; None = à déterminer par question (étape 6)
    mois_possibles: tuple[str, ...] = ()  # si mois_paie est None : mois précédent et de réception


def _mois_precedent(jour: date) -> str:
    return f"{jour.year - 1}-12" if jour.month == 1 else f"{jour.year}-{jour.month - 1:02d}"


def rattacher_salaire(transaction: Transaction, contexte: Contexte) -> RattachementSalaire | None:
    """Mois de paie d'un salaire reçu sur le joint, ou `None` si ce n'est pas un salaire.

    Ordre : période lue dans le libellé (`periode_regex`), sinon fenêtre `fenetre_salaire`.
    Hors fenêtre et sans période, `mois_paie` reste `None` : on ne devine pas.
    """
    if transaction.source is not Source.CA_JOINT or transaction.montant <= 0:
        return None
    position = (
        f"{transaction.fichier}, position {transaction.position}, "
        f"{transaction.date_operation:%d/%m/%Y}, {formater_montant(transaction.montant)}"
    )
    salaires = [
        s
        for s in contexte.salaires
        if normaliser_libelle(s.libelle_contient) in transaction.libelle_normalise
    ]
    if len(salaires) > 1:
        raise ControleError(
            f"RG-04 : {position} : correspond à plusieurs salaires du contexte "
            f"({', '.join(s.nom for s in salaires)}) ; rendre leurs `libelle_contient` exclusifs"
        )
    if not salaires:
        return None
    salaire = salaires[0]

    if salaire.periode_regex is not None and (
        correspondance := re.search(salaire.periode_regex, transaction.libelle)
    ):
        periode = correspondance.group(1)
        lue = _PERIODE.fullmatch(periode)
        if lue is None or not 1 <= int(lue.group(1)) <= 12:
            raise ControleError(
                f"RG-04 : {position} : période {periode!r} capturée par la periode_regex du "
                f"salaire {salaire.nom}, attendu un mois MM/AAAA valide"
            )
        return RattachementSalaire(nom=salaire.nom, mois_paie=f"{lue.group(2)}-{lue.group(1)}")

    reception = transaction.date_operation
    fenetre = contexte.fenetre_salaire
    if reception.day >= fenetre.du_jour:
        return RattachementSalaire(nom=salaire.nom, mois_paie=mois_budgetaire(reception))
    if reception.day <= fenetre.au_jour_mois_suivant:
        return RattachementSalaire(nom=salaire.nom, mois_paie=_mois_precedent(reception))
    return RattachementSalaire(
        nom=salaire.nom,
        mois_paie=None,
        mois_possibles=(_mois_precedent(reception), mois_budgetaire(reception)),
    )
