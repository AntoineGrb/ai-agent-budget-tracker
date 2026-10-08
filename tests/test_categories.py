import pytest

from budget.categories import (
    Categorie,
    NatureDepense,
    SousCategorie,
    categorie_de,
    libelle_de,
    nature_de,
)


def test_cardinalites() -> None:
    assert len(Categorie) == 11
    assert len(SousCategorie) == 38


@pytest.mark.parametrize("sc", list(SousCategorie))
def test_chaque_sous_categorie_est_complete(sc: SousCategorie) -> None:
    prefixe, _, suffixe = sc.value.partition(".")
    assert suffixe
    assert categorie_de(sc) == Categorie(prefixe)
    assert sc.name.startswith(categorie_de(sc).name + "_")
    assert isinstance(nature_de(sc), NatureDepense)
    assert libelle_de(sc)


def test_chaque_categorie_a_un_libelle_et_des_sous_categories() -> None:
    for categorie in Categorie:
        assert categorie.libelle
        assert any(categorie_de(sc) == categorie for sc in SousCategorie)
    assert Categorie.RESTOS.libelle == "Restos & commandes"
    assert Categorie.CADEAUX.libelle == "Cadeaux & dons"


@pytest.mark.parametrize(
    ("sc", "nature"),
    [
        (SousCategorie.LOGEMENT_PRET, NatureDepense.FIXE),
        (SousCategorie.LOGEMENT_TRAVAUX_BRICOLAGE, NatureDepense.PILOTABLE),
        (SousCategorie.ENFANTS_GARDE, NatureDepense.FIXE),
        (SousCategorie.ENFANTS_ACTIVITES, NatureDepense.PILOTABLE),
        (SousCategorie.TRANSPORT_TRANSPORTS_COMMUN, NatureDepense.FIXE),
        (SousCategorie.TRANSPORT_CARBURANT, NatureDepense.PILOTABLE),
        (SousCategorie.SANTE_OPTIQUE, NatureDepense.PILOTABLE),
        (SousCategorie.ABONNEMENTS_FRAIS_BANCAIRES, NatureDepense.FIXE),
    ],
)
def test_nature_des_categories_mixtes(sc: SousCategorie, nature: NatureDepense) -> None:
    assert nature_de(sc) == nature
