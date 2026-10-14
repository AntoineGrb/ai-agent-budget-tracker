"""Classement par le fichier de contexte : salaires, dotations, provision, charges fixes (spec 05).

Les règles ne portent que sur le compte joint. Les alertes sont informatives ; seul un contexte
ambigu (une opération qui correspond à plusieurs éléments de même type) lève `ControleError`.
"""

from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_UP, Decimal

from budget.classement.criteres import contient, correspond, reperer
from budget.classement.modeles import Alerte, Classement, OrigineClassement, RapprochementCharge
from budget.config import ChargeFixe, Contexte
from budget.errors import ControleError
from budget.libelles import normaliser_libelle
from budget.models import Nature, Source, Transaction
from budget.montants import CENTIME, formater_montant, formater_pourcentage
from budget.salaires import rattacher_salaire

_CONTEXTE = OrigineClassement.CONTEXTE
DETAIL_REPRISE = "provision.reprise"


def _debit_joint(transaction: Transaction) -> bool:
    return transaction.source is Source.CA_JOINT and transaction.montant < 0


def _credit_joint(transaction: Transaction) -> bool:
    return transaction.source is Source.CA_JOINT and transaction.montant > 0


def _refuser_ambiguite(regle: str, transaction: Transaction, element: str, noms: list[str]) -> None:
    if len(noms) > 1:
        raise ControleError(
            f"{regle} : {reperer(transaction)} : correspond à plusieurs {element} du contexte "
            f"({', '.join(noms)}) ; rendre leurs critères exclusifs"
        )


# Règles 2 à 5 : une transaction à la fois ---------------------------------------------------


def classer_salaire(transaction: Transaction, contexte: Contexte) -> Classement | None:
    """RG-04 : salaire rattaché à son mois de paie (`None` s'il est à déterminer)."""
    rattachement = rattacher_salaire(transaction, contexte)
    if rattachement is None:
        return None
    return Classement.pour(
        transaction, Nature.REVENU, _CONTEXTE, f"salaires.{rattachement.nom}"
    ).model_copy(update={"mois_rattachement": rattachement.mois_paie})


def classer_dotation(transaction: Transaction, contexte: Contexte) -> Classement | None:
    """RG-05 : virement au libellé **et** au montant d'une dotation déclarée."""
    if not _debit_joint(transaction):
        return None
    dotations = [d for d in contexte.dotations if contient(transaction, d.libelle_contient)]
    _refuser_ambiguite("RG-05", transaction, "dotations", [d.nom for d in dotations])
    if not dotations or -transaction.montant != dotations[0].montant:
        return None
    return Classement.pour(transaction, Nature.DOTATION, _CONTEXTE, f"dotations.{dotations[0].nom}")


def classer_reprise_provision(transaction: Transaction, contexte: Contexte) -> Classement | None:
    """RG-08 : reprise depuis la provision, neutralisée avec le paiement qu'elle finance."""
    if _credit_joint(transaction) and contient(
        transaction, contexte.provision.reprises_libelle_contient
    ):
        return Classement.pour(transaction, Nature.PROVISION, _CONTEXTE, DETAIL_REPRISE)
    return None


def classer_paiement_finance(transaction: Transaction, contexte: Contexte) -> Classement | None:
    """RG-08 : paiement financé par la provision."""
    if not _debit_joint(transaction):
        return None
    paiements = [p for p in contexte.provision.paiements_finances if correspond(transaction, p)]
    _refuser_ambiguite("RG-08", transaction, "paiements financés", [p.nom for p in paiements])
    if not paiements:
        return None
    return Classement.pour(
        transaction, Nature.PROVISION, _CONTEXTE, f"provision.{paiements[0].nom}"
    )


# Règle 6 : charges fixes (RG-18) --------------------------------------------------------------

_CleCritere = tuple[str | None, str | None, str | None]


def _cle(charge: ChargeFixe) -> _CleCritere:
    def norme(valeur: str | None) -> str | None:
        return normaliser_libelle(valeur) if valeur is not None else None

    return (charge.ics, norme(charge.libelle_contient), norme(charge.type_operation))


def _apparier_par_montant(
    charges: Sequence[ChargeFixe], candidates: Sequence[Transaction], alertes: list[Alerte]
) -> dict[str, list[Transaction]]:
    """Charges à critères identiques : appariement par montant absolu croissant (RG-18)."""
    charges = sorted(charges, key=lambda c: c.montant)
    candidates = sorted(candidates, key=lambda t: (-t.montant, t.position))
    if len(charges) == len(candidates):
        return {c.nom: [t] for c, t in zip(charges, candidates, strict=True)}

    if candidates:
        alertes.append(
            Alerte(
                code="RG-18",
                message=(
                    f"charges {', '.join(c.nom for c in charges)} : {len(charges)} opérations "
                    f"attendues, {len(candidates)} trouvées ; appariement au plus proche montant "
                    "attendu, les opérations en trop restent non classées"
                ),
            )
        )
    # Au plus proche montant attendu : paires prises par écart croissant, chacune une seule fois.
    paires = sorted(
        (abs(-t.montant - c.montant), i, j)
        for i, c in enumerate(charges)
        for j, t in enumerate(candidates)
    )
    affectees: dict[str, list[Transaction]] = {}
    prises: set[int] = set()
    for _, i, j in paires:
        if charges[i].nom not in affectees and j not in prises:
            affectees[charges[i].nom] = [candidates[j]]
            prises.add(j)
    return affectees


def _rapprocher(
    charge: ChargeFixe, transactions: Sequence[Transaction], contexte: Contexte
) -> RapprochementCharge:
    seuil = (
        charge.tolerance_pct
        if charge.tolerance_pct is not None
        else contexte.seuils.ecart_charge_fixe_pct
    )
    reel: Decimal | None = None
    ecart: Decimal | None = None
    ecart_pct: Decimal | None = None
    if transactions:
        reel = sum((-t.montant for t in transactions), start=Decimal(0))
        ecart = reel - charge.montant
        ecart_pct = (ecart / charge.montant * 100).quantize(CENTIME, rounding=ROUND_HALF_UP)
    return RapprochementCharge(
        nom=charge.nom,
        attendu=charge.montant,
        reel=reel,
        transaction_ids=tuple(t.id for t in transactions),
        ecart=ecart,
        ecart_pct=ecart_pct,
        seuil_pct=seuil,
        hors_seuil=ecart_pct is not None and abs(ecart_pct) > seuil,
    )


def _alerte_charge(rapprochement: RapprochementCharge) -> Alerte | None:
    nom, attendu = rapprochement.nom, formater_montant(rapprochement.attendu)
    if rapprochement.reel is None:
        message = f"charge {nom} attendue absente : {attendu} attendus, aucune opération du mois"
    elif rapprochement.hors_seuil and rapprochement.ecart_pct is not None:
        message = (
            f"charge {nom} : {formater_montant(rapprochement.reel)} au lieu de {attendu} "
            f"({formater_pourcentage(rapprochement.ecart_pct, signe=True)}, "
            f"seuil {formater_pourcentage(rapprochement.seuil_pct)})"
        )
    else:
        return None
    ids = rapprochement.transaction_ids
    return Alerte(code="RG-18", transaction_id=ids[0] if len(ids) == 1 else None, message=message)


def affecter_charges_fixes(
    restantes: Iterable[Transaction], contexte: Contexte, alertes: list[Alerte]
) -> tuple[list[Classement], list[RapprochementCharge]]:
    """RG-18 : affecte les débits du joint non classés aux charges fixes et les rapproche."""
    groupes: dict[_CleCritere, list[ChargeFixe]] = {}
    for charge in contexte.charges_fixes:
        groupes.setdefault(_cle(charge), []).append(charge)

    candidates: dict[_CleCritere, list[Transaction]] = {cle: [] for cle in groupes}
    for transaction in restantes:
        if not _debit_joint(transaction):
            continue
        cles = [cle for cle, charges in groupes.items() if correspond(transaction, charges[0])]
        noms = [charge.nom for cle in cles for charge in groupes[cle]]
        if len(cles) > 1:
            _refuser_ambiguite("RG-18", transaction, "charges fixes", noms)
        if cles:
            candidates[cles[0]].append(transaction)

    affectations: dict[str, list[Transaction]] = {}
    for cle, charges in groupes.items():
        if len(charges) == 1:
            affectations[charges[0].nom] = candidates[cle]
        else:
            affectations.update(_apparier_par_montant(charges, candidates[cle], alertes))

    classements: list[Classement] = []
    rapprochements: list[RapprochementCharge] = []
    for charge in contexte.charges_fixes:
        transactions = affectations.get(charge.nom, [])
        classements.extend(
            Classement.pour(
                t,
                Nature.DEPENSE,
                _CONTEXTE,
                f"charges_fixes.{charge.nom}",
                sous_categorie=charge.sous_categorie,
            )
            for t in transactions
        )
        rapprochement = _rapprocher(charge, transactions, contexte)
        rapprochements.append(rapprochement)
        if (alerte := _alerte_charge(rapprochement)) is not None:
            alertes.append(alerte)
    return classements, rapprochements


# Alertes de fin de classement ----------------------------------------------------------------


def alertes_cartes(transactions: Iterable[Transaction], contexte: Contexte) -> list[Alerte]:
    """RG-17 : opération d'une carte inactive ou absente du contexte."""
    joint = contexte.comptes.joint
    alertes = []
    for t in transactions:
        if t.carte is None or t.carte in joint.cartes_actives:
            continue
        etat = "inactive" if t.carte in joint.cartes_inactives else "inconnue du contexte"
        alertes.append(
            Alerte(
                code="RG-17", transaction_id=t.id, message=f"carte {t.carte} {etat} : {reperer(t)}"
            )
        )
    return alertes


def alertes_salaires(
    transactions: Iterable[Transaction], classements: dict[str, Classement], contexte: Contexte
) -> list[Alerte]:
    """RG-04 : salaire dont le mois de paie reste à déterminer (ni période, ni réponse)."""
    alertes = []
    for t in transactions:
        classement = classements.get(t.id)
        if (
            classement is None
            or not (classement.detail or "").startswith("salaires.")
            or classement.mois_rattachement is not None
        ):
            continue
        rattachement = rattacher_salaire(t, contexte)
        assert rattachement is not None
        alertes.append(
            Alerte(
                code="RG-04",
                transaction_id=t.id,
                message=(
                    f"salaire {rattachement.nom} reçu le {t.date_operation:%d/%m/%Y}, "
                    f"{formater_montant(t.montant)} : mois de paie à confirmer "
                    f"({' ou '.join(rattachement.mois_possibles)}), question à l'étape 6"
                ),
            )
        )
    return alertes


def alertes_dotations(
    transactions: Sequence[Transaction], classements: dict[str, Classement], contexte: Contexte
) -> list[Alerte]:
    """RG-05 : dotation déclarée sans opération correspondante dans le mois."""
    trouvees = {c.detail for c in classements.values() if c.nature is Nature.DOTATION}
    alertes = []
    for dotation in contexte.dotations:
        if f"dotations.{dotation.nom}" in trouvees:
            continue
        attendu = formater_montant(dotation.montant)
        meme_libelle = [
            t for t in transactions if _debit_joint(t) and contient(t, dotation.libelle_contient)
        ]
        if meme_libelle:
            presences = ", ".join(
                f"le {t.date_operation:%d/%m/%Y} avec {formater_montant(-t.montant)}"
                for t in meme_libelle
            )
            message = (
                f"dotation {dotation.nom} non trouvée : libellé présent {presences} "
                f"au lieu de {attendu}"
            )
        else:
            message = (
                f"dotation {dotation.nom} non trouvée : aucun virement du joint ne porte son "
                f"libellé ({attendu} attendus)"
            )
        alertes.append(
            Alerte(
                code="RG-05",
                transaction_id=meme_libelle[0].id if len(meme_libelle) == 1 else None,
                message=message,
            )
        )
    return alertes


def alertes_provision(
    transactions: Sequence[Transaction], classements: dict[str, Classement]
) -> list[Alerte]:
    """RG-08 : reprise de provision sans paiement financé dans le mois, ou l'inverse."""
    reprises: list[Transaction] = []
    paiements: list[Transaction] = []
    for t in transactions:
        classement = classements.get(t.id)
        if classement is None or classement.nature is not Nature.PROVISION:
            continue
        (reprises if classement.detail == DETAIL_REPRISE else paiements).append(t)
    if reprises and not paiements:
        return [
            Alerte(
                code="RG-08",
                transaction_id=t.id,
                message=f"reprise de provision sans paiement financé dans le mois : {reperer(t)}",
            )
            for t in reprises
        ]
    if paiements and not reprises:
        return [
            Alerte(
                code="RG-08",
                transaction_id=t.id,
                message=(
                    f"paiement financé par la provision ({classements[t.id].detail}) sans "
                    f"reprise dans le mois : {reperer(t)}"
                ),
            )
            for t in paiements
        ]
    return []
