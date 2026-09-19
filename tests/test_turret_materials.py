import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"addons"))
from carbon_eve_resources.core import turret_materials as tm

class TurretMaterialsTests(unittest.TestCase):
    def test_only_second_default_pattern_layer_requires_sof6(self):
        effect = {"constParameters": [{"name": "PMtl2BaseColor", "value": [0]*4}]}
        generic = {"patternMaterialPrefixes": ["PMtl1", "PMtl2"]}
        faction = {"defaultPatternLayer2MaterialName": "paint"}
        material = lambda name: {"parameters": {"BaseColor": [1,2,3,4]}}
        self.assertEqual(tm.resolve(effect, generic, faction, material), {})
        self.assertEqual(tm.resolve(effect, generic, faction, material, sof6=True),
                         {"PMtl2BaseColor": [1,2,3,4]})
        self.assertEqual(tm.resolve(effect, generic, faction, material,
                         dna="hull:faction:race:pattern?pattern;none;paint"),
                         {"PMtl2BaseColor": [1,2,3,4]})

    def test_material_prefix_matching_is_case_insensitive(self):
        effect = {"constParameters": [{"name": "mtL1BaseColor", "value": [0]*4}]}
        faction = {"areaMaterials": {"materialNames": {"0:0": "paint"}}}
        values = tm.resolve(effect, {"materialPrefixes": ["Mtl1"]}, faction,
                            lambda name: {"parameters": {"BaseColor": [1,2,3,4]}})
        self.assertEqual(values["mtL1BaseColor"], [1,2,3,4])

    def test_parameter_names_area_remapping_and_full_vectors(self):
        generic={"materialPrefixes":[{"str":"Mtl1"},{"str":"Mtl2"}],"turretAreaType":10}
        faction={"materialUsageList":[1,0],"colorData":{"colors":[[2,3,4,5]]},
                 "areaMaterials":{"materialNames":{"10:1":"pbr","0:0":"eve"},
                                  "glowColor":{"10:DirtColor1":0}}}
        materials={"pbr":{"parameters":{"BaseColor":[.1,.2,.3,.4],"GeneralData":[.5,.6,.7,.8]}},
                   "eve":{"parameters":{"DiffuseColor":[1,2,3,4]}}}
        effect={"constParameters":[{"name":n,"value":[9]*4} for n in
                ("Mtl1BaseColor","Mtl1GeneralData","Mtl2DiffuseColor","DirtColor1","Unknown")]}
        values=tm.resolve(effect,generic,faction,materials.__getitem__)
        self.assertEqual(values["Mtl1BaseColor"],[.1,.2,.3,.4])
        self.assertEqual(values["Mtl1GeneralData"],[.5,.6,.7,.8])
        self.assertEqual(values["Mtl2DiffuseColor"],[1,2,3,4])
        self.assertEqual(values["DirtColor1"],[2,3,4,5])
        self.assertNotIn("Unknown",values)
        original={"turretEffect":effect}
        result=tm.apply(original,values)
        self.assertEqual(original["turretEffect"]["constParameters"][0]["value"],[9]*4)
        self.assertEqual(result["turretEffect"]["constParameters"][-1]["value"],[9]*4)

    def test_constants_take_precedence_and_vector_fallback(self):
        constant={"name":"Constant","value":[1]*4}
        vector={"_type":"Tr2Vector4Parameter","name":"Vector","value":[2]*4}
        effect={"constParameters":[constant],"parameters":[vector]}
        self.assertEqual(tm.parameters(effect),[constant])
        effect["constParameters"]=[]
        self.assertEqual(tm.parameters(effect),[vector])

    def test_eve_primary_default_race_priority_and_no_glow_dimming(self):
        effect={"constParameters":[{"name":"GeneralGlowColor","value":[0]*4}]}
        faction={"areaMaterials":{"glowColor":{"0:GeneralGlowColor":0}},"colorData":{"colors":[[4,3,2,1],[8,7,6,5]]}}
        race={"areaMaterials":{"glowColor":{"0:GeneralGlowColor":1}}}
        self.assertEqual(tm.resolve(effect,{},faction,lambda n:{},race=race)["GeneralGlowColor"],[8,7,6,5])
        self.assertEqual(tm.resolve(effect,{},faction,lambda n:{})["GeneralGlowColor"],[4,3,2,1])
