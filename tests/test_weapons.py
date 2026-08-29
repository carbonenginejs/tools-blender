"""The weapons library, and what a turret's model path is.

Nothing here derives a weapon's identity. The service names each one's
`resPath` and its `slot`, and both are read rather than recomputed -- another
consumer worked the slot out from the path and got the extra-large turrets
wrong, 147 against the library's 72.
"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))

from carbon_eve_resources.core import weapons  # noqa: E402


class FakeClient:
    def __init__(self, answer, turret=None):
        self.answer = answer
        self.turret = turret or {}
        self.asked = []

    def request_json(self, method, route):
        self.asked.append(route)
        if "/weapons" in route:
            return self.answer
        return self.turret


def library(*rows):
    return {"types": {str(row["typeID"]): row for row in rows}}


def weapon(type_id, name, path, slot="turrets", tech=1):
    return {"typeID": type_id, "name": {"en": name, "de": name + " DE"},
            "resPath": path, "slot": slot, "techLevel": tech,
            "metaLevel": 0, "published": True}


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        weapons.forget()
        weapons.CACHE_ROOT["path"] = None

    def test_it_reads_the_english_name_of_eight(self):
        client = FakeClient(library(
            weapon(1, "Gatling Pulse Laser I",
                   "res:/dx9/model/turret/energy/pulse/s/a.black")))
        self.assertEqual(weapons.catalogue(client)[0]["name"],
                         "Gatling Pulse Laser I")

    def test_the_slot_comes_from_the_library_and_is_not_recomputed(self):
        """An XL turret whose path says nothing about being XL."""

        client = FakeClient(library(
            weapon(1, "Big", "res:/dx9/model/turret/energy/pulse/l/a.black",
                   slot="xlTurrets")))
        self.assertEqual(weapons.catalogue(client)[0]["slot"], "xlTurrets")

    def test_only_turret_slots_come_back_by_default(self):
        client = FakeClient(library(
            weapon(1, "Gun", "res:/dx9/model/turret/energy/a.black"),
            weapon(2, "Launcher", "res:/dx9/model/turret/launcher/a.black",
                   slot="launchers"),
            weapon(3, "Big", "res:/dx9/model/turret/energy/b.black",
                   slot="xlTurrets")))
        self.assertEqual([row["name"] for row in weapons.catalogue(client)],
                         ["Big", "Gun"])

    def test_a_weapon_with_no_model_is_dropped(self):
        """There is nothing to fit, so it cannot be offered."""

        rows = library(weapon(1, "Gun", "res:/dx9/model/turret/energy/a.black"))
        rows["types"]["2"] = weapon(2, "Ghost", "")
        self.assertEqual(len(weapons.catalogue(FakeClient(rows))), 1)

    def test_the_family_is_read_off_the_path(self):
        client = FakeClient(library(
            weapon(1, "A", "res:/dx9/model/turret/energy/pulse/s/a.black"),
            weapon(2, "B", "res:/dx9/model/turret/hybrid/blaster/s/b.black")))
        self.assertEqual(weapons.families(weapons.catalogue(client)),
                         ["energy", "hybrid"])

    def test_it_is_sorted_by_name(self):
        client = FakeClient(library(
            weapon(1, "Zeta", "res:/dx9/model/turret/energy/z.black"),
            weapon(2, "Alpha", "res:/dx9/model/turret/energy/a.black")))
        self.assertEqual([row["name"] for row in weapons.catalogue(client)],
                         ["Alpha", "Zeta"])

    def test_the_library_is_asked_for_once(self):
        """It is 1.5MB and changes only when EVE does."""

        client = FakeClient(library(
            weapon(1, "Gun", "res:/dx9/model/turret/energy/a.black")))
        weapons.catalogue(client)
        weapons.catalogue(client)
        self.assertEqual(len([r for r in client.asked if "/weapons" in r]), 1)

    def test_many_weapons_may_share_one_model(self):
        """602 turrets share 57 models, so the path is NOT an identity.

        Keying a picker by the model collapses sixteen weapons onto one entry;
        the type id is what distinguishes them.
        """

        shared = "res:/dx9/model/turret/energy/pulse/l/pulse_mega_t1.black"
        client = FakeClient(library(weapon(1, "Mega I", shared),
                                    weapon(2, "Mega II", shared)))
        rows = weapons.catalogue(client)
        self.assertEqual(len({row["resPath"] for row in rows}), 1)
        self.assertEqual(len({row["typeID"] for row in rows}), 2)


class TurretDocumentTests(unittest.TestCase):
    def setUp(self):
        weapons.forget()
        weapons.CACHE_ROOT["path"] = None

    def test_it_unwraps_the_object(self):
        client = FakeClient({}, turret={"object": {
            "_type": "EveTurretSet",
            "geometryResPath": "res:/dx9/model/turret/energy/a.gr2"}})
        found = weapons.turret_document(client, "res:/a.black")
        self.assertEqual(found["geometryResPath"],
                         "res:/dx9/model/turret/energy/a.gr2")

    def test_it_asks_for_json_at_the_resource_route(self):
        client = FakeClient({}, turret={"object": {"_type": "EveTurretSet"}})
        weapons.turret_document(client, "res:/dx9/model/turret/energy/a.black")
        self.assertIn("resources/dx9/model/turret/energy/a.black?format=json",
                      client.asked[-1])

    def test_an_empty_path_asks_nothing(self):
        client = FakeClient({})
        self.assertEqual(weapons.turret_document(client, ""), {})
        self.assertEqual(client.asked, [])


if __name__ == "__main__":
    unittest.main()
