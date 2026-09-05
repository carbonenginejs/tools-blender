"""The weapons library, its mount compatibility, and each model path.

The service names each weapon's `resPath`, natural `slot`, and compatible bays.
Legacy libraries need one fallback for launcher sizes because their types omit
`chargeSize`; that fallback must never rewrite the natural slot.
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


def library(*rows, groups=None):
    return {"types": {str(row["typeID"]): row for row in rows},
            "groups": {str(key): value for key, value in (groups or {}).items()}}


def weapon(type_id, name, path, slot="turrets", tech=1, *, group_id=1,
           charge_size=None, compatible_slots=None, size=None):
    return {"typeID": type_id, "name": {"en": name, "de": name + " DE"},
            "resPath": path, "slot": slot, "techLevel": tech,
            "groupID": group_id, "chargeSize": charge_size,
            **({"compatibleSlots": compatible_slots}
               if compatible_slots is not None else {}),
            **({"size": size} if size is not None else {}),
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

    def test_all_weapon_slots_come_back_by_default(self):
        client = FakeClient(library(
            weapon(1, "Gun", "res:/dx9/model/turret/energy/a.black"),
            weapon(2, "Launcher", "res:/dx9/model/turret/launcher/a.black",
                   slot="launchers"),
            weapon(3, "Bomb", "res:/dx9/model/turret/launcher/bomb/a.black",
                   slot="bombs"),
            weapon(4, "Atomic", "res:/dx9/model/turret/atomic/a.black",
                   slot="atomics"),
            weapon(5, "Chain", "res:/dx9/model/turret/chain/a.black",
                   slot="chains"),
            weapon(6, "Big", "res:/dx9/model/turret/energy/b.black",
                   slot="xlTurrets")))
        self.assertEqual([row["name"] for row in weapons.catalogue(client)],
                         ["Atomic", "Big", "Bomb", "Chain", "Gun", "Launcher"])

    def test_xl_hardpoints_take_every_xl_weapon(self):
        groups = {
            10: {"name": {"en": "Missile Launcher XL Torpedo"}},
            11: {"name": {"en": "Missile Launcher Torpedo"}},
            12: {"name": {"en": "Precursor Weapon"}},
        }
        client = FakeClient(library(
            weapon(1, "XL launcher", "res:/dx9/model/turret/launcher/xl.black",
                   slot="launchers", group_id=10),
            weapon(2, "Large launcher", "res:/dx9/model/turret/launcher/l.black",
                   slot="launchers", group_id=11),
            weapon(3, "XL atomic", "res:/dx9/model/turret/atomic/xl.black",
                   slot="atomics", group_id=12, charge_size=4),
            weapon(4, "XL gun", "res:/dx9/model/turret/energy/xl.black",
                   slot="xlTurrets", group_id=12, charge_size=4),
            groups=groups))

        rows = weapons.catalogue(client, slots=("xlTurrets",))
        self.assertEqual([row["name"] for row in rows],
                         ["XL atomic", "XL gun", "XL launcher"])
        self.assertEqual(rows[-1]["size"], "XL")

    def test_shared_compatibility_is_used_without_rederiving_size(self):
        client = FakeClient(library(
            weapon(1, "Future XL weapon", "res:/weapon/future.black",
                   slot="launchers",
                   compatible_slots=["launchers", "xlTurrets"])))

        rows = weapons.catalogue(client, slots="xlTurrets")
        self.assertEqual([row["name"] for row in rows], ["Future XL weapon"])
        self.assertEqual(rows[0]["compatibleSlots"],
                         ["launchers", "xlTurrets"])

    def test_weapon_kind_mapping_covers_every_natural_slot_once(self):
        self.assertEqual(tuple(row[1] for row in weapons.WEAPON_KINDS),
                         weapons.WEAPON_SLOTS)
        self.assertEqual(len(set(weapons.WEAPON_SLOTS)), 6)
        self.assertEqual(weapons.WEAPON_KINDS[1],
                         ("xl", "xlTurrets", "XL Turrets"))

    def test_natural_slot_filter_still_accepts_ordinary_launchers(self):
        client = FakeClient(library(
            weapon(1, "Launcher", "res:/dx9/model/turret/launcher/a.black",
                   slot="launchers"),
            weapon(2, "Gun", "res:/dx9/model/turret/energy/a.black")))
        self.assertEqual(
            [row["name"] for row in weapons.catalogue(client, slots="launchers")],
            ["Launcher"],
        )

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
